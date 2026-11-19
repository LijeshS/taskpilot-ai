"""
Run once to generate tray/icon.ico from the SVG design.
Requires: pip install Pillow cairosvg
If cairosvg is not available, falls back to a simple coloured icon.

Usage:  python tray/create_icon.py
"""
import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(__file__), "icon.ico")

def _make_simple_ico():
    """Create a minimal 32x32 blue .ico without any extra libraries."""
    # 32x32 RGB solid #1E90FF (DodgerBlue) with no transparency
    w = h = 32
    r, g, b = 0x1E, 0x90, 0xFF

    # BMP header for ICO (40-byte BITMAPINFOHEADER, height doubled for AND mask)
    bmp_header = struct.pack(
        "<IiiHHIIiiII",
        40,         # biSize
        w, h * 2,   # width, height (×2 because ICO includes XOR+AND bitmaps)
        1,          # biPlanes
        32,         # biBitCount (RGBA)
        0,          # biCompression (BI_RGB)
        0,          # biSizeImage
        0, 0, 0, 0, # resolution + colours
    )

    # XOR bitmap: 32-bit BGRA rows, bottom-up
    row = bytes([b, g, r, 0xFF]) * w    # fully opaque blue
    xor_data = row * h

    # AND mask: 4 bytes per row (1 bit per pixel, 0 = opaque), bottom-up
    and_row = b"\x00" * 4   # all opaque
    and_data = and_row * h

    image_data = bmp_header + xor_data + and_data

    # ICO file header + one directory entry
    ico_header = struct.pack("<HHH", 0, 1, 1)   # reserved, type=1(ICO), count=1
    img_offset = 6 + 16                          # header + 1 dir entry
    dir_entry = struct.pack(
        "<BBBBHHIi",
        w, h, 0, 0,                 # width, height, colorcount, reserved
        1,                          # planes
        32,                         # bit count
        len(image_data),            # bytes in image
        img_offset,                 # offset
    )

    with open(OUT, "wb") as f:
        f.write(ico_header + dir_entry + image_data)
    print(f"Created: {OUT}  ({os.path.getsize(OUT)} bytes)")


if __name__ == "__main__":
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io

        sizes = [16, 32, 48, 64, 128, 256]
        images = []
        for s in sizes:
            img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            # Circle background
            draw.ellipse([0, 0, s-1, s-1], fill=(30, 144, 255, 255))
            # "T" letter
            font_size = max(int(s * 0.55), 8)
            try:
                font = ImageFont.truetype("arial.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), "T", font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text(((s - tw) // 2, (s - th) // 2 - bbox[1]),
                      "T", fill=(255, 255, 255, 255), font=font)
            images.append(img)

        images[0].save(OUT, format="ICO", sizes=[(s, s) for s in sizes],
                       append_images=images[1:])
        print(f"Created high-quality icon: {OUT}")

    except ImportError:
        print("Pillow not available — creating minimal .ico")
        _make_simple_ico()
