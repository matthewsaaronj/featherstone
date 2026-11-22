# Needs Pillow (not an app dependency): python -m pip install pillow; python scripts/make_icon.py webapp/static/icons
"""Index-card app icon (option 1). Drawn at 2048px in 100-unit design space, then downscaled."""
import sys
from PIL import Image, ImageDraw

S = 2048
k = S / 100.0
def u(v): return v * k

NAVY, CREAM, BACK2, BACK1 = "#1C2A44", "#F7F3EA", "#DCE3EC", "#B9C6D8"
RED, BLUE = "#C8574A", "#9DB7D6"

img = Image.new("RGBA", (S, S), NAVY)

def card(fill, y, angle, cy):
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle([u(24), u(y), u(76), u(y + 34)], radius=u(2.5), fill=fill)
    if angle:
        # SVG rotate(-a) is counter-clockwise; PIL's positive angle is counter-clockwise too.
        layer = layer.rotate(-angle, center=(u(50), u(cy)), resample=Image.BICUBIC)
    img.alpha_composite(layer)

card(BACK1, 30, -10, 47)
card(BACK2, 32, -4, 49)
card(CREAM, 36, 0, 0)
d = ImageDraw.Draw(img)
d.line([u(24), u(44), u(76), u(44)], fill=RED, width=round(u(1.4)))
for y in (50, 55.5, 61):
    d.line([u(24), u(y), u(76), u(y)], fill=BLUE, width=round(u(1.0)))

out = sys.argv[1]
rgb = img.convert("RGB")
for size in (512, 192, 180, 32):
    rgb.resize((size, size), Image.LANCZOS).save(f"{out}/icon-{size}.png", optimize=True)
