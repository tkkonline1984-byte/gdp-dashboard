from __future__ import annotations

import re


BARCODE_PATTERN = re.compile(r"^\d{13}$")


def normalize_barcode(value: str) -> str:
    """Remove whitespace while preserving every entered digit."""
    return "".join(str(value or "").split())


def ean13_check_digit(first_twelve_digits: str) -> int:
    if not re.fullmatch(r"\d{12}", first_twelve_digits):
        raise ValueError("EAN-13 ต้องใช้ตัวเลข 12 หลักก่อนคำนวณเลขตรวจสอบ")
    total = sum(int(value) for value in first_twelve_digits[::2])
    total += 3 * sum(int(value) for value in first_twelve_digits[1::2])
    return (10 - (total % 10)) % 10


def is_valid_ean13(value: str) -> bool:
    barcode = normalize_barcode(value)
    return bool(
        BARCODE_PATTERN.fullmatch(barcode)
        and ean13_check_digit(barcode[:12]) == int(barcode[-1])
    )


def validate_product_code(value: str, strict_ean13: bool = True) -> tuple[bool, str]:
    barcode = normalize_barcode(value)
    if not BARCODE_PATTERN.fullmatch(barcode):
        return False, "กรุณาพิมพ์รหัสสินค้าเป็นตัวเลข 13 หลัก"
    if strict_ean13 and not is_valid_ean13(barcode):
        expected = ean13_check_digit(barcode[:12])
        return False, f"เลขตรวจสอบหลักสุดท้ายไม่ถูกต้อง ควรเป็น {expected}"
    return True, "รหัสสินค้าถูกต้อง"
