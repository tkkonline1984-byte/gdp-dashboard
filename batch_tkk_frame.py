# -*- coding: utf-8 -*-
"""
TKK ONLINE - Auto Background Remove + Frame
Windows-ready batch processor

Features
- Process many product images from products/
- Automatic background removal using OpenCV GrabCut + guided filtering
- Edge decontamination to reduce white/gray halos
- Automatically crop transparent margins
- Make products large and centered inside the frame
- Soft product shadow
- Preserve the original filename stem
- Remove trailing "(1)" from filename
- 1 product -> 1 PNG
- Create outputs.zip automatically
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import traceback
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageDraw

BASE_DIR = Path(__file__).resolve().parent
FRAME_PATH = BASE_DIR / "frame.png"
INPUT_DIR = BASE_DIR / "products"
OUTPUT_DIR = BASE_DIR / "outputs"
ZIP_BASENAME = str(BASE_DIR / "outputs")

VALID_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# -------------------------
# Background removal
# -------------------------
GRABCUT_WORK_WIDTH = 1600
GRABCUT_ITER = 8

# GrabCut initialization rectangle as image ratios
RECT_LEFT = 0.025
RECT_TOP = 0.025
RECT_RIGHT = 0.975
RECT_BOTTOM = 0.975

EDGE_BAND_RATIO = 0.004
FEATHER_PX = 1.8
INPAINT_MAX_DIM = 1500

ALPHA_LOW_CUTOFF = 8
ALPHA_HIGH_CUTOFF = 248

# -------------------------
# Placement in frame
# Reference frame size: 1254 x 1254
# Coordinates automatically scale if frame size differs.
# -------------------------
REFERENCE_FRAME_W = 1254
REFERENCE_FRAME_H = 1254

REF_ZONE_X0, REF_ZONE_X1 = 20, 1230
REF_ZONE_Y0, REF_ZONE_Y1 = 255, 915

# Product fills most of the available zone
ZONE_FILL_X = 0.985
ZONE_FILL_Y = 0.985

# Optional vertical shift; positive moves product downward
PRODUCT_Y_OFFSET_RATIO = 0.00

# Shadow
SHADOW_OPACITY = 78
SHADOW_BLUR = 16


def imread_unicode(path: Path):
    """Read images with Thai/Unicode filenames safely on Windows."""
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def clean_output_stem(stem: str) -> str:
    """Preserve filename; remove trailing (1), including optional spaces."""
    stem = stem.strip()
    stem = re.sub(r"\s*\(1\)$", "", stem)
    return stem or "output"


def scaled_zone(frame_size):
    fw, fh = frame_size
    sx = fw / REFERENCE_FRAME_W
    sy = fh / REFERENCE_FRAME_H
    return (
        int(round(REF_ZONE_X0 * sx)),
        int(round(REF_ZONE_Y0 * sy)),
        int(round(REF_ZONE_X1 * sx)),
        int(round(REF_ZONE_Y1 * sy)),
    )


def largest_component(mask_u8: np.ndarray) -> np.ndarray:
    """Keep the largest connected foreground component."""
    binary = np.where(mask_u8 > 0, 255, 0).astype(np.uint8)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if num_labels <= 1:
        return binary
    largest = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
    return np.where(labels == largest, 255, 0).astype(np.uint8)


def refine_mask(mask_u8: np.ndarray) -> np.ndarray:
    """Remove small holes/noise without aggressively eating product edges."""
    kernel_close = np.ones((5, 5), np.uint8)
    kernel_open = np.ones((3, 3), np.uint8)
    out = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, kernel_close, iterations=2)
    out = cv2.morphologyEx(out, cv2.MORPH_OPEN, kernel_open, iterations=1)
    return largest_component(out)


def remove_background(input_path: Path) -> Image.Image:
    """Remove background and return tightly-cropped RGBA PIL image."""
    img_bgr = imread_unicode(input_path)
    if img_bgr is None:
        raise ValueError(f"เปิดไฟล์ไม่ได้: {input_path}")

    h, w = img_bgr.shape[:2]
    if h < 2 or w < 2:
        raise ValueError("รูปมีขนาดเล็กเกินไป")

    # If input is a PNG/WebP with meaningful alpha, use that alpha directly.
    try:
        pil_src = Image.open(input_path).convert("RGBA")
        alpha_src = np.array(pil_src.getchannel("A"))
        if alpha_src.min() < 250:
            rgba = np.array(pil_src)
            alpha = rgba[:, :, 3]
            alpha[alpha < ALPHA_LOW_CUTOFF] = 0
            alpha[alpha > ALPHA_HIGH_CUTOFF] = 255
            rgba[:, :, 3] = alpha
            result = Image.fromarray(rgba, "RGBA")
            bbox = result.getbbox()
            return result.crop(bbox) if bbox else result
    except Exception:
        pass

    # Work at a bounded width for speed.
    if w > GRABCUT_WORK_WIDTH:
        scale = GRABCUT_WORK_WIDTH / w
        work_w = GRABCUT_WORK_WIDTH
        work_h = max(2, int(round(h * scale)))
        small = cv2.resize(img_bgr, (work_w, work_h), interpolation=cv2.INTER_AREA)
    else:
        small = img_bgr.copy()
        work_h, work_w = small.shape[:2]

    x0 = int(work_w * RECT_LEFT)
    x1 = int(work_w * RECT_RIGHT)
    y0 = int(work_h * RECT_TOP)
    y1 = int(work_h * RECT_BOTTOM)

    x0 = max(0, min(x0, work_w - 2))
    y0 = max(0, min(y0, work_h - 2))
    x1 = max(x0 + 1, min(x1, work_w - 1))
    y1 = max(y0 + 1, min(y1, work_h - 1))
    rect = (x0, y0, x1 - x0, y1 - y0)

    gc_mask = np.zeros((work_h, work_w), np.uint8)
    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)

    cv2.grabCut(
        small,
        gc_mask,
        rect,
        bgd_model,
        fgd_model,
        GRABCUT_ITER,
        cv2.GC_INIT_WITH_RECT,
    )

    fg = np.where(
        (gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD),
        255,
        0,
    ).astype(np.uint8)

    fg = refine_mask(fg)

    # Resize mask back to original image size.
    mask_full = cv2.resize(fg, (w, h), interpolation=cv2.INTER_LINEAR)
    _, mask_full_bin = cv2.threshold(mask_full, 127, 255, cv2.THRESH_BINARY)

    # Confidence zones.
    band_px = max(3, int(round(max(w, h) * EDGE_BAND_RATIO)))
    if band_px % 2 == 0:
        band_px += 1
    band_kernel = np.ones((band_px, band_px), np.uint8)

    sure_fg = cv2.erode(mask_full_bin, band_kernel, iterations=1)
    sure_bg = cv2.erode(255 - mask_full_bin, band_kernel, iterations=1)

    # Guided filter gives a cleaner edge near labels/cans/bottles.
    if hasattr(cv2, "ximgproc") and hasattr(cv2.ximgproc, "guidedFilter"):
        guide = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        refined = cv2.ximgproc.guidedFilter(
            guide=guide,
            src=mask_full_bin,
            radius=6,
            eps=40,
        )
    else:
        refined = cv2.GaussianBlur(mask_full_bin, (0, 0), sigmaX=1.2)

    alpha = refined.astype(np.float32) / 255.0
    alpha = np.where(
        sure_fg > 0,
        1.0,
        np.where(sure_bg > 0, 0.0, alpha),
    ).astype(np.float32)

    alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=FEATHER_PX)
    alpha = np.clip(alpha, 0.0, 1.0)

    # Estimate background by inpainting the detected foreground.
    inpaint_src = img_bgr
    inpaint_mask = mask_full_bin
    inpaint_scale = 1.0

    if INPAINT_MAX_DIM and max(w, h) > INPAINT_MAX_DIM:
        inpaint_scale = INPAINT_MAX_DIM / max(w, h)
        iw = max(2, int(round(w * inpaint_scale)))
        ih = max(2, int(round(h * inpaint_scale)))
        inpaint_src = cv2.resize(img_bgr, (iw, ih), interpolation=cv2.INTER_AREA)
        inpaint_mask = cv2.resize(mask_full_bin, (iw, ih), interpolation=cv2.INTER_NEAREST)

    bg_small = cv2.inpaint(inpaint_src, inpaint_mask, 21, cv2.INPAINT_TELEA)

    bg_estimate = (
        cv2.resize(bg_small, (w, h), interpolation=cv2.INTER_LINEAR)
        if inpaint_scale != 1.0
        else bg_small
    )

    # Color decontamination on semi-transparent edges.
    img_f = img_bgr.astype(np.float32)
    bg_f = bg_estimate.astype(np.float32)
    a = alpha[..., None]
    a_safe = np.clip(a, 0.08, 1.0)

    decontam = np.clip(
        (img_f - (1.0 - a) * bg_f) / a_safe,
        0,
        255,
    )

    edge_zone = ((alpha > 0.01) & (alpha < 0.995))[..., None]
    out_bgr = np.where(edge_zone, decontam, img_f).astype(np.uint8)

    alpha_u8 = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)
    alpha_u8[alpha_u8 < ALPHA_LOW_CUTOFF] = 0
    alpha_u8[alpha_u8 > ALPHA_HIGH_CUTOFF] = 255

    rgba = cv2.cvtColor(out_bgr, cv2.COLOR_BGR2RGBA)
    rgba[:, :, 3] = alpha_u8

    result = Image.fromarray(rgba, "RGBA")

    bbox = result.getbbox()
    if bbox:
        pad = 2
        left = max(0, bbox[0] - pad)
        top = max(0, bbox[1] - pad)
        right = min(result.width, bbox[2] + pad)
        bottom = min(result.height, bbox[3] + pad)
        result = result.crop((left, top, right, bottom))

    return result


def place_in_frame(product_rgba: Image.Image, frame_rgba: Image.Image) -> Image.Image:
    """Place the transparent product large and centered in the frame."""
    product_rgba = product_rgba.convert("RGBA")
    frame_rgba = frame_rgba.convert("RGBA")

    fw, fh = frame_rgba.size
    x0, y0, x1, y1 = scaled_zone((fw, fh))

    zone_w = max(1, x1 - x0)
    zone_h = max(1, y1 - y0)

    max_w = max(1, int(zone_w * ZONE_FILL_X))
    max_h = max(1, int(zone_h * ZONE_FILL_Y))

    pw, ph = product_rgba.size
    if pw < 1 or ph < 1:
        raise ValueError("ขนาดรูปสินค้าผิดปกติ")

    scale = min(max_w / pw, max_h / ph)
    target_w = max(1, int(round(pw * scale)))
    target_h = max(1, int(round(ph * scale)))

    product_resized = product_rgba.resize((target_w, target_h), Image.Resampling.LANCZOS)

    zone_cx = (x0 + x1) / 2.0
    zone_cy = (y0 + y1) / 2.0
    zone_cy += zone_h * PRODUCT_Y_OFFSET_RATIO

    px0 = int(round(zone_cx - target_w / 2.0))
    py0 = int(round(zone_cy - target_h / 2.0))

    # Soft floor shadow based on actual visible object.
    alpha_arr = np.array(product_resized.getchannel("A"))
    cols = np.where(alpha_arr.max(axis=0) > 10)[0]
    rows = np.where(alpha_arr.max(axis=1) > 10)[0]

    shadow_layer = Image.new("RGBA", (fw, fh), (0, 0, 0, 0))

    if len(cols) and len(rows):
        draw = ImageDraw.Draw(shadow_layer)

        obj_left = int(cols.min())
        obj_right = int(cols.max())
        obj_bottom = int(rows.max())

        visible_w = max(1, obj_right - obj_left + 1)
        shadow_w = max(36, int(visible_w * 0.82))
        shadow_h = max(8, int(shadow_w * 0.14))

        shadow_cx = px0 + (obj_left + obj_right) // 2
        shadow_cy = py0 + obj_bottom - max(1, shadow_h // 8)

        draw.ellipse(
            (
                shadow_cx - shadow_w // 2,
                shadow_cy - shadow_h // 2,
                shadow_cx + shadow_w // 2,
                shadow_cy + shadow_h // 2,
            ),
            fill=(20, 15, 10, SHADOW_OPACITY),
        )
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))

    out = Image.alpha_composite(frame_rgba, shadow_layer)
    out.alpha_composite(product_resized, (px0, py0))
    return out


def unique_output_path(stem: str) -> Path:
    """
    Preserve original stem whenever possible.
    If stripping "(1)" would cause a duplicate, add _2, _3... to avoid overwriting.
    """
    candidate = OUTPUT_DIR / f"{stem}.png"
    if not candidate.exists():
        return candidate

    n = 2
    while True:
        candidate = OUTPUT_DIR / f"{stem}_{n}.png"
        if not candidate.exists():
            return candidate
        n += 1


def main():
    print("=" * 58)
    print(" TKK ONLINE - Auto Background Remove + Frame")
    print("=" * 58)

    INPUT_DIR.mkdir(exist_ok=True)

    if not FRAME_PATH.exists():
        print(f"[ERROR] ไม่พบไฟล์กรอบ: {FRAME_PATH.name}")
        print("กรุณาวาง frame.png ไว้โฟลเดอร์เดียวกับโปรแกรม")
        return 1

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    zip_path = BASE_DIR / "outputs.zip"
    if zip_path.exists():
        zip_path.unlink()

    frame_rgba = Image.open(FRAME_PATH).convert("RGBA")

    files = sorted(
        [p for p in INPUT_DIR.iterdir() if p.is_file() and p.suffix.lower() in VALID_EXT],
        key=lambda p: p.name.lower(),
    )

    print(f"พบรูปสินค้าทั้งหมด {len(files)} รูป")

    if not files:
        print("")
        print("ยังไม่มีรูปในโฟลเดอร์ products")
        print("นำรูปสินค้าใส่ใน products แล้วดับเบิลคลิก run.bat อีกครั้ง")
        return 0

    success = 0
    fail = 0

    for i, file_path in enumerate(files, start=1):
        print(f"[{i}/{len(files)}] {file_path.name}")

        try:
            output_stem = clean_output_stem(file_path.stem)
            out_path = unique_output_path(output_stem)

            product_rgba = remove_background(file_path)
            final_img = place_in_frame(product_rgba, frame_rgba.copy())

            final_img.save(out_path, "PNG", optimize=True)
            success += 1
            print(f"   OK -> {out_path.name}")

        except Exception as exc:
            fail += 1
            print(f"   ERROR -> {exc}")
            traceback.print_exc(limit=1)

    zip_file = shutil.make_archive(ZIP_BASENAME, "zip", OUTPUT_DIR)

    print("")
    print("=" * 58)
    print("เสร็จเรียบร้อย")
    print(f"สำเร็จ      : {success} ไฟล์")
    print(f"ไม่สำเร็จ   : {fail} ไฟล์")
    print(f"โฟลเดอร์รูป : {OUTPUT_DIR}")
    print(f"ไฟล์ ZIP    : {zip_file}")
    print("=" * 58)
    return 0


if __name__ == "__main__":
    sys.exit(main())
