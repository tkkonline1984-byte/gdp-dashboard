from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_EDGE = 2400


class ImageValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedImage:
    data: bytes
    extension: str
    media_type: str
    width: int
    height: int
    sha256: str
    original_name: str


def prepare_product_image(raw: bytes, original_name: str = "camera.jpg") -> PreparedImage:
    if not raw:
        raise ImageValidationError("ไม่พบข้อมูลรูปภาพ")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageValidationError("รูปมีขนาดเกิน 20 MB")

    try:
        with Image.open(io.BytesIO(raw)) as opened:
            opened.verify()
        with Image.open(io.BytesIO(raw)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageValidationError("ไฟล์ที่เลือกไม่ใช่รูปภาพที่ระบบรองรับ") from exc

    if image.width < 80 or image.height < 80:
        raise ImageValidationError("รูปมีขนาดเล็กเกินไป ต้องไม่น้อยกว่า 80×80 Pixels")

    if max(image.size) > MAX_IMAGE_EDGE:
        image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)

    background = Image.new("RGB", image.size, "white")
    if image.getchannel("A").getextrema()[0] < 255:
        background.paste(image, mask=image.getchannel("A"))
    else:
        background.paste(image.convert("RGB"))

    output = io.BytesIO()
    background.save(
        output,
        format="JPEG",
        quality=92,
        optimize=True,
        progressive=True,
        subsampling=0,
    )
    data = output.getvalue()
    return PreparedImage(
        data=data,
        extension=".jpg",
        media_type="image/jpeg",
        width=background.width,
        height=background.height,
        sha256=hashlib.sha256(data).hexdigest(),
        original_name=Path(original_name or "camera.jpg").name,
    )
