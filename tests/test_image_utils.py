import io
import unittest

from PIL import Image

from image_utils import ImageValidationError, prepare_product_image


class ImageUtilsTests(unittest.TestCase):
    def test_converts_transparent_png_to_jpeg(self):
        source = io.BytesIO()
        Image.new("RGBA", (500, 400), (255, 0, 0, 120)).save(source, "PNG")
        prepared = prepare_product_image(source.getvalue(), "สินค้า.png")
        self.assertEqual((prepared.width, prepared.height), (500, 400))
        self.assertTrue(prepared.data.startswith(b"\xff\xd8"))
        self.assertEqual(len(prepared.sha256), 64)

    def test_resizes_large_image(self):
        source = io.BytesIO()
        Image.new("RGB", (3000, 1500), "white").save(source, "JPEG")
        prepared = prepare_product_image(source.getvalue(), "large.jpg")
        self.assertEqual(max(prepared.width, prepared.height), 2400)

    def test_rejects_invalid_and_tiny_images(self):
        with self.assertRaises(ImageValidationError):
            prepare_product_image(b"not-an-image", "bad.jpg")
        source = io.BytesIO()
        Image.new("RGB", (20, 20), "white").save(source, "PNG")
        with self.assertRaises(ImageValidationError):
            prepare_product_image(source.getvalue(), "tiny.png")


if __name__ == "__main__":
    unittest.main()
