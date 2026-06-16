"""Manual smoke test: does the configured vision model locate elements?

Draws a 400x200 white PNG with a red rectangle centred at (200, 100) and
asks the live VLM for its coordinates. Run from backend/:
    venv\\Scripts\\python smoke_vision.py
"""

import base64
import struct
import zlib

from services.vision import create_vision

W, H = 400, 200
RECT = (160, 80, 240, 120)  # red rectangle, centre (200, 100)


def make_png() -> str:
    rows = b""
    for y in range(H):
        rows += b"\x00"
        for x in range(W):
            inside = RECT[0] <= x < RECT[2] and RECT[1] <= y < RECT[3]
            rows += bytes((255, 0, 0)) if inside else bytes((255, 255, 255))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data)))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows))
           + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


if __name__ == "__main__":
    vision = create_vision()
    if vision is None:
        raise SystemExit("Vision is disabled or no API key configured.")
    print(f"Model: {vision.model}")
    point = vision.locate(make_png(), W, H, 'the red rectangle')
    if point is None:
        raise SystemExit("FAIL: model did not find the rectangle.")
    fx, fy = point
    print(f"Located at fractions ({fx:.3f}, {fy:.3f}) "
          f"= pixels ({fx*W:.0f}, {fy*H:.0f}); expected ~(200, 100)")
    ok = abs(fx * W - 200) <= 40 and abs(fy * H - 100) <= 20
    print("PASS" if ok else "FAIL: too far from the rectangle centre")
