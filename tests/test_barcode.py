import unittest

from barcode import ean13_check_digit, is_valid_ean13, normalize_barcode, validate_product_code


class BarcodeTests(unittest.TestCase):
    def test_normalizes_spaces(self):
        self.assertEqual(normalize_barcode("8850 1270 0001 6"), "8850127000016")

    def test_accepts_valid_ean13(self):
        self.assertTrue(is_valid_ean13("8850127000016"))
        self.assertTrue(validate_product_code("8850127000016")[0])

    def test_rejects_wrong_length_and_checksum(self):
        self.assertFalse(validate_product_code("123")[0])
        self.assertFalse(validate_product_code("8850127000017")[0])

    def test_length_only_mode(self):
        self.assertTrue(validate_product_code("1234567890123", strict_ean13=False)[0])

    def test_check_digit(self):
        self.assertEqual(ean13_check_digit("885012700001"), 6)


if __name__ == "__main__":
    unittest.main()
