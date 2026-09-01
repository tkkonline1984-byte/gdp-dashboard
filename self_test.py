from __future__ import annotations

import io
import tempfile
from pathlib import Path

import pandas as pd
from PIL import Image

from barcode import is_valid_ean13, validate_product_code
from converters import excel_to_pdf, pdf_to_jpg_zip
from image_utils import prepare_product_image
from storage import LocalSubmissionStore


def run() -> None:
    assert is_valid_ean13("8850127000016")
    assert validate_product_code("8850127000016")[0]
    assert not validate_product_code("123")[0]

    raw_image = io.BytesIO()
    Image.new("RGB", (640, 480), "white").save(raw_image, "PNG")
    prepared = prepare_product_image(raw_image.getvalue(), "product.png")
    assert prepared.width == 640 and prepared.height == 480
    assert prepared.data.startswith(b"\xff\xd8")

    workbook = io.BytesIO()
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        pd.DataFrame({"Barcode": ["8850127000016"], "Name": ["Test"]}).to_excel(
            writer, index=False, sheet_name="Products"
        )
    pdf = excel_to_pdf(workbook.getvalue(), "self-test.xlsx")
    assert pdf.data.startswith(b"%PDF")
    images = pdf_to_jpg_zip(pdf.data, dpi=120)
    assert images.data.startswith(b"PK") and images.items >= 1

    with tempfile.TemporaryDirectory() as temporary:
        store = LocalSubmissionStore(Path(temporary))
        metadata = {
            "submission_id": "self-test",
            "submitted_at": "2026-09-01T09:00:00+07:00",
            "barcode": "8850127000016",
            "location": {
                "branch": "TEST",
                "floor": "ชั้น 1",
                "zone": "ทางเดิน 1",
                "map_x": 2,
                "map_y": 3,
            },
        }
        result = store.save_submission(metadata, prepared.data)
        assert len(store.list_submissions()) == 1
        assert store.get_file_bytes(result.image_path) == prepared.data
        moved = dict(metadata)
        moved["submission_id"] = "self-test-moved"
        moved["submitted_at"] = "2026-09-01T09:05:00+07:00"
        moved["location"] = dict(metadata["location"], zone="ทางเดิน 2", map_x=7)
        moved_result = store.save_submission(moved, prepared.data)
        assert moved_result.location_changed
        assert moved_result.previous_location["zone"] == "ทางเดิน 1"
        assert store.get_current_location("8850127000016")["location"]["zone"] == "ทางเดิน 2"
        assert len(store.list_submissions()) == 2

    print("TKK safe self-test OK - v2.1.0")


if __name__ == "__main__":
    run()
