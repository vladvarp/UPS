from io import BytesIO
import unittest

from PIL import Image

from ups.screenshot import _borderless_style, _image_is_blank, _trim_letterbox


def _png(color) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (80, 60), color).save(buffer, format="PNG")
    return buffer.getvalue()


class ScreenshotTests(unittest.TestCase):
    def test_black_frame_is_blank(self):
        self.assertTrue(_image_is_blank(_png((0, 0, 0))))
        self.assertTrue(_image_is_blank(None))

    def test_drawn_frame_is_not_blank(self):
        self.assertFalse(_image_is_blank(_png((32, 40, 28))))

    def test_letterbox_is_trimmed(self):
        image = Image.new("RGB", (800, 600), (0, 0, 0))
        for x in range(40, 520):
            for y in range(20, 280):
                image.putpixel((x, y), (40, 80, 90))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        trimmed = Image.open(BytesIO(_trim_letterbox(buffer.getvalue())))
        self.assertLess(trimmed.height, 360)
        self.assertLess(trimmed.width, 580)

    def test_borderless_style_drops_caption(self):
        caption = 0x00C00000 | 0x00040000 | 0x00080000
        cleaned = _borderless_style(caption | 0x10000000)
        self.assertEqual(cleaned & 0x00C00000, 0)
        self.assertEqual(cleaned & 0x00040000, 0)
        self.assertTrue(cleaned & 0x10000000)


if __name__ == "__main__":
    unittest.main()
