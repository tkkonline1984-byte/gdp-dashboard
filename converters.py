from __future__ import annotations

import io
import math
import re
import shutil
import subprocess
import zipfile
import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape

import fitz
import pandas as pd
import pdfplumber
from PIL import Image, ImageOps, UnidentifiedImageError
try:
    import pytesseract
    from pytesseract import Output, TesseractNotFoundError
except ModuleNotFoundError:  # Non-OCR converters can still run during diagnostics.
    pytesseract = None  # type: ignore[assignment]
    Output = None  # type: ignore[assignment]

    class TesseractNotFoundError(RuntimeError):
        pass
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle


MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_EXCEL_ROWS_PER_SHEET = 10_000


class ConversionError(ValueError):
    pass


@dataclass(frozen=True)
class ConversionResult:
    data: bytes
    filename: str
    media_type: str
    items: int
    warnings: tuple[str, ...] = ()


def _validate_size(data: bytes) -> None:
    if not data:
        raise ConversionError("ไฟล์ว่าง")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ConversionError("ไฟล์มีขนาดเกิน 30 MB")


def _open_pdf(data: bytes) -> fitz.Document:
    _validate_size(data)
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ConversionError("เปิด PDF ไม่สำเร็จหรือไฟล์เสีย") from exc
    if document.needs_pass:
        document.close()
        raise ConversionError("PDF มีรหัสผ่าน กรุณาปลดรหัสก่อน")
    if document.page_count < 1:
        document.close()
        raise ConversionError("PDF ไม่มีหน้าเอกสาร")
    if document.page_count > MAX_PDF_PAGES:
        document.close()
        raise ConversionError(f"รองรับ PDF ไม่เกิน {MAX_PDF_PAGES} หน้าในหนึ่งครั้ง")
    return document


def pdf_to_jpg_zip(data: bytes, dpi: int = 200, quality: int = 92) -> ConversionResult:
    dpi = max(120, min(int(dpi), 300))
    quality = max(75, min(int(quality), 96))
    document = _open_pdf(data)
    output = io.BytesIO()
    try:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
            for page_number, page in enumerate(document, start=1):
                pixmap = page.get_pixmap(matrix=matrix, alpha=False, colorspace=fitz.csRGB)
                with Image.open(io.BytesIO(pixmap.tobytes("png"))) as opened:
                    image = opened.convert("RGB")
                    encoded = io.BytesIO()
                    image.save(encoded, "JPEG", quality=quality, optimize=True, progressive=True)
                archive.writestr(f"page_{page_number:03d}.jpg", encoded.getvalue())
    finally:
        page_count = document.page_count
        document.close()
    return ConversionResult(
        data=output.getvalue(),
        filename="PDF_TO_JPG.zip",
        media_type="application/zip",
        items=page_count,
    )


def _safe_sheet_name(name: str, used: set[str]) -> str:
    cleaned = re.sub(r"[\\/*?:\[\]]+", "_", str(name or "Sheet")).strip()[:31] or "Sheet"
    candidate = cleaned
    counter = 2
    while candidate.casefold() in used:
        suffix = f"_{counter}"
        candidate = f"{cleaned[: 31 - len(suffix)]}{suffix}"
        counter += 1
    used.add(candidate.casefold())
    return candidate


def _frames_to_excel(frames: Iterable[tuple[str, pd.DataFrame]]) -> tuple[bytes, int, int]:
    output = io.BytesIO()
    used: set[str] = set()
    sheet_count = 0
    row_count = 0
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for raw_name, frame in frames:
            safe_frame = frame.copy().fillna("")
            if len(safe_frame) > MAX_EXCEL_ROWS_PER_SHEET:
                safe_frame = safe_frame.iloc[:MAX_EXCEL_ROWS_PER_SHEET]
            sheet_name = _safe_sheet_name(raw_name, used)
            safe_frame.to_excel(writer, sheet_name=sheet_name, index=False)
            worksheet = writer.book[sheet_name]
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for column_cells in worksheet.columns:
                width = min(60, max(10, max(len(str(cell.value or "")) for cell in column_cells) + 2))
                worksheet.column_dimensions[column_cells[0].column_letter].width = width
            sheet_count += 1
            row_count += len(safe_frame)
        if sheet_count == 0:
            pd.DataFrame({"ข้อมูล": ["ไม่พบข้อมูลที่แปลงได้"]}).to_excel(
                writer, sheet_name="Result", index=False
            )
            sheet_count = 1
            row_count = 1
    return output.getvalue(), sheet_count, row_count


def _open_image(data: bytes) -> Image.Image:
    _validate_size(data)
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.verify()
        with Image.open(io.BytesIO(data)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ConversionError("เปิดรูปไม่สำเร็จหรือไฟล์เสีย") from exc
    if max(image.size) > 3200:
        image.thumbnail((3200, 3200), Image.Resampling.LANCZOS)
    return image


def _tesseract_cli_data(image: Image.Image, language: str) -> dict[str, list[object]]:
    executable = shutil.which("tesseract")
    if not executable:
        raise TesseractNotFoundError("tesseract executable was not found")
    source = io.BytesIO()
    image.save(source, "PNG")
    try:
        process = subprocess.run(
            [executable, "stdin", "stdout", "-l", language, "--oem", "3", "--psm", "6", "tsv"],
            input=source.getvalue(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TesseractNotFoundError("tesseract command failed") from exc
    if process.returncode != 0:
        message = process.stderr.decode("utf-8", errors="replace")[:300]
        raise RuntimeError(message or "tesseract returned an error")
    rows = csv.DictReader(io.StringIO(process.stdout.decode("utf-8", errors="replace")), delimiter="\t")
    keys = ("text", "conf", "block_num", "par_num", "line_num", "left", "top", "width", "height")
    result: dict[str, list[object]] = {key: [] for key in keys}
    for row in rows:
        for key in keys:
            result[key].append(row.get(key, ""))
    return result


def _run_ocr(image: Image.Image, language: str) -> dict[str, list[object]]:
    if pytesseract is not None and Output is not None:
        return pytesseract.image_to_data(
            image,
            lang=language,
            config="--oem 3 --psm 6",
            output_type=Output.DICT,
        )
    return _tesseract_cli_data(image, language)


def _ocr_lines(image: Image.Image, language: str = "tha+eng") -> pd.DataFrame:
    try:
        result = _run_ocr(image, language)
    except Exception:
        try:
            result = _run_ocr(image, "eng")
        except Exception as exc:
            raise ConversionError("OCR ไม่พร้อมใช้งาน กรุณาตรวจ packages.txt และ Tesseract") from exc

    grouped: dict[tuple[int, int, int], list[dict[str, object]]] = {}
    total = len(result.get("text", []))
    for index in range(total):
        text = str(result["text"][index] or "").strip()
        try:
            confidence = float(result["conf"][index])
        except (TypeError, ValueError):
            confidence = -1
        if not text or confidence < 15:
            continue
        key = (
            int(result["block_num"][index]),
            int(result["par_num"][index]),
            int(result["line_num"][index]),
        )
        grouped.setdefault(key, []).append(
            {
                "text": text,
                "confidence": confidence,
                "left": int(result["left"][index]),
                "top": int(result["top"][index]),
                "width": int(result["width"][index]),
                "height": int(result["height"][index]),
            }
        )

    rows: list[dict[str, object]] = []
    for line_number, words in enumerate(
        sorted(grouped.values(), key=lambda line: (min(int(w["top"]) for w in line), min(int(w["left"]) for w in line))),
        start=1,
    ):
        words.sort(key=lambda word: int(word["left"]))
        left = min(int(word["left"]) for word in words)
        top = min(int(word["top"]) for word in words)
        right = max(int(word["left"]) + int(word["width"]) for word in words)
        bottom = max(int(word["top"]) + int(word["height"]) for word in words)
        rows.append(
            {
                "ลำดับบรรทัด": line_number,
                "ข้อความ": " ".join(str(word["text"]) for word in words),
                "ความมั่นใจเฉลี่ย": round(sum(float(word["confidence"]) for word in words) / len(words), 1),
                "ตำแหน่ง X": left,
                "ตำแหน่ง Y": top,
                "ความกว้าง": right - left,
                "ความสูง": bottom - top,
            }
        )
    if not rows:
        raise ConversionError("OCR ไม่พบข้อความในรูป")
    return pd.DataFrame(rows)


def images_to_excel(files: Iterable[tuple[str, bytes]], language: str = "tha+eng") -> ConversionResult:
    frames: list[tuple[str, pd.DataFrame]] = []
    warnings: list[str] = []
    for index, (filename, data) in enumerate(files, start=1):
        image = _open_image(data)
        try:
            frame = _ocr_lines(image, language=language)
        except ConversionError as exc:
            warnings.append(f"{Path(filename).name}: {exc}")
            continue
        frame.insert(0, "ไฟล์ต้นฉบับ", Path(filename).name)
        frames.append((f"Image_{index:02d}", frame))
    if not frames:
        raise ConversionError("ไม่มีรูปที่ OCR เป็น Excel ได้")
    output, sheets, rows = _frames_to_excel(frames)
    return ConversionResult(
        data=output,
        filename="JPG_TO_EXCEL.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        items=rows,
        warnings=tuple(warnings + [f"สร้าง {sheets} Sheet"]),
    )


def _render_pdf_page(document: fitz.Document, page_index: int, dpi: int = 200) -> Image.Image:
    page = document.load_page(page_index)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
    with Image.open(io.BytesIO(pixmap.tobytes("png"))) as opened:
        return opened.convert("RGB")


def pdf_to_excel(data: bytes, language: str = "tha+eng") -> ConversionResult:
    document = _open_pdf(data)
    frames: list[tuple[str, pd.DataFrame]] = []
    warnings: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page_index, page in enumerate(pdf.pages):
                page_number = page_index + 1
                tables = page.extract_tables() or []
                valid_tables = [table for table in tables if table and any(any(cell for cell in row) for row in table)]
                if valid_tables:
                    for table_index, table in enumerate(valid_tables, start=1):
                        width = max(len(row) for row in table)
                        normalized = [list(row) + [""] * (width - len(row)) for row in table]
                        columns = [f"Column {column + 1}" for column in range(width)]
                        frames.append((f"P{page_number}_Table{table_index}", pd.DataFrame(normalized, columns=columns)))
                    continue

                text = page.extract_text() or ""
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                if lines:
                    frames.append(
                        (
                            f"Page_{page_number}",
                            pd.DataFrame({"ลำดับบรรทัด": range(1, len(lines) + 1), "ข้อความ": lines}),
                        )
                    )
                    continue

                try:
                    ocr_frame = _ocr_lines(_render_pdf_page(document, page_index), language=language)
                    frames.append((f"P{page_number}_OCR", ocr_frame))
                    warnings.append(f"หน้า {page_number}: ใช้ OCR เพราะเป็น PDF แบบสแกน")
                except ConversionError as exc:
                    warnings.append(f"หน้า {page_number}: {exc}")
    except Exception as exc:
        if isinstance(exc, ConversionError):
            raise
        raise ConversionError("อ่านข้อมูลจาก PDF ไม่สำเร็จ") from exc
    finally:
        document.close()

    if not frames:
        raise ConversionError("ไม่พบตารางหรือข้อความที่แปลงเป็น Excel ได้")
    output, sheets, rows = _frames_to_excel(frames)
    return ConversionResult(
        data=output,
        filename="PDF_TO_EXCEL.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        items=rows,
        warnings=tuple(warnings + [f"สร้าง {sheets} Sheet"]),
    )


def _find_thai_font() -> Path | None:
    candidates = (
        Path("C:/Windows/Fonts/tahoma.ttf"),
        Path("C:/Windows/Fonts/THSarabunNew.ttf"),
        Path("/usr/share/fonts/truetype/tlwg/Garuda.ttf"),
        Path("/usr/share/fonts/truetype/tlwg/Loma.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    return next((path for path in candidates if path.is_file()), None)


def _register_document_font() -> str:
    font_path = _find_thai_font()
    if font_path is None:
        return "Helvetica"
    font_name = "TKKDocumentFont"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    return font_name


def _cell_text(value: object, style: ParagraphStyle) -> Paragraph:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        text = ""
    else:
        text = str(value)
    text = text[:500]
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def excel_to_pdf(data: bytes, source_name: str = "workbook.xlsx") -> ConversionResult:
    _validate_size(data)
    try:
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=object)
    except Exception as exc:
        raise ConversionError("เปิด Excel ไม่สำเร็จ รองรับไฟล์ XLSX และ XLS") from exc
    if not sheets:
        raise ConversionError("Excel ไม่มี Worksheet")

    font_name = _register_document_font()
    page_size = landscape(A4)
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=page_size,
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=16 * mm,
        bottomMargin=14 * mm,
        title=f"TKK ONLINE - {Path(source_name).name}",
        author="TKK ONLINE",
    )
    header_style = ParagraphStyle(
        "TKKHeader",
        fontName=font_name,
        fontSize=7,
        leading=9,
        textColor=colors.white,
        alignment=TA_LEFT,
    )
    cell_style = ParagraphStyle(
        "TKKCell",
        fontName=font_name,
        fontSize=7,
        leading=9,
        textColor=colors.HexColor("#111827"),
        alignment=TA_LEFT,
    )
    title_style = ParagraphStyle(
        "TKKTitle",
        fontName=font_name,
        fontSize=15,
        leading=19,
        textColor=colors.HexColor("#041328"),
    )
    story: list[object] = []
    total_rows = 0

    for sheet_index, (sheet_name, raw_frame) in enumerate(sheets.items()):
        frame = raw_frame.iloc[:MAX_EXCEL_ROWS_PER_SHEET].fillna("")
        total_rows += len(frame)
        if sheet_index:
            story.append(PageBreak())
        story.append(Paragraph(f"TKK ONLINE — {escape(str(sheet_name))}", title_style))
        story.append(Spacer(1, 4 * mm))
        columns = [str(column) for column in frame.columns]
        if not columns:
            columns = ["ข้อมูล"]
            frame = pd.DataFrame({"ข้อมูล": []})
        table_data = [[_cell_text(column, header_style) for column in columns]]
        for row in frame.itertuples(index=False, name=None):
            table_data.append([_cell_text(value, cell_style) for value in row])
        available_width = page_size[0] - 20 * mm
        column_widths = [available_width / len(columns)] * len(columns)
        table = LongTable(table_data, colWidths=column_widths, repeatRows=1, splitByRow=True)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#041328")),
                    ("LINEBELOW", (0, 0), (-1, 0), 2, colors.HexColor("#FF6A00")),
                    ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#CBD5E1")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(table)

    def draw_page(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont(font_name, 7)
        canvas.setFillColor(colors.HexColor("#667085"))
        canvas.drawString(10 * mm, 7 * mm, f"TKK ONLINE • {Path(source_name).name}")
        canvas.drawRightString(page_size[0] - 10 * mm, 7 * mm, f"Page {doc.page}")
        canvas.restoreState()

    try:
        document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    except Exception as exc:
        raise ConversionError("สร้าง PDF จาก Excel ไม่สำเร็จ") from exc
    return ConversionResult(
        data=output.getvalue(),
        filename="EXCEL_TO_PDF.pdf",
        media_type="application/pdf",
        items=total_rows,
        warnings=(f"สร้างจาก {len(sheets)} Worksheet", f"เวลา {datetime.now().isoformat(timespec='seconds')}"),
    )
