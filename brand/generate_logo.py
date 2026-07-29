r"""
UpperCircuit — brand asset generator.

Produces the channel mark as a vector master plus the raster sizes YouTube
actually asks for. Re-run after editing the constants below; nothing here is
hand-tuned per-file.

    cd backend && .\venv\Scripts\python.exe ..\brand\generate_logo.py

The mark
--------
An "upper circuit" is a stock hitting its maximum permitted daily gain, at which
point the exchange halts trading. So the mark is literal: three ascending bars,
the tallest struck flush against a ceiling rule it cannot pass. The ceiling is
the brass element because the ceiling is the story — a generic up-arrow would
have said "growth" and lost the specific meaning of the name.

Geometry is drawn in a 200x200 design space and scaled, so every output is
consistent and the safe area holds at any size.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent

# ---- palette -------------------------------------------------------------
INK        = (14, 17, 22)       # #0E1116  near-black, blue bias
PAPER      = (237, 239, 242)    # #EDEFF2  cool grey, deliberately not cream
BRASS_LT   = (168, 121, 47)     # #A8792F  on light grounds
BRASS_DK   = (201, 151, 63)     # #C9973F  on ink
BONE       = (231, 234, 239)    # #E7EAEF  mark on ink

# ---- geometry, in a 200x200 design space ---------------------------------
# Everything sits inside ~82% of the circle radius, so YouTube's circular crop
# never clips it.
#
# The ceiling is DASHED, not solid, for two reasons: a dashed threshold is how a
# limit line is actually drawn on a chart, and a solid rule fused with the
# tallest bar into a table silhouette that read as furniture rather than a price
# striking a ceiling.
CEIL_Y0, CEIL_Y1 = 44, 55

BASE = (40, 145, 160, 154)           # the baseline, thinner so bars stay legible
BARS = [                             # x0, y0, x1, y1 — ascending toward the ceiling
    (48, 112, 72, 145),
    (88, 88, 112, 145),
    (128, 55, 152, 145),             # meets the threshold: circuit hit
]

# Each dash sits directly above its own bar, at the same width. Phasing them
# independently left a notch of background above the tallest bar, which read as
# a chip in the line; column-aligned they read as a level marked across the
# chart, and the third bar meets its dash squarely.
DASHES = [(x0, CEIL_Y0, x1, CEIL_Y1) for x0, _, x1, _ in BARS]

FONTS = [
    r"C:\Windows\Fonts\georgiab.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _scaled(box, k: float, pad: float = 0.0):
    """Scale a design-space box into pixels, offset by pad."""
    x0, y0, x1, y1 = box
    return [x0 * k + pad, y0 * k + pad, x1 * k + pad, y1 * k + pad]


def draw_mark(draw: ImageDraw.ImageDraw, size: int, fg, brass, pad: float = 0.0) -> None:
    """Render the mark into a size x size area starting at (pad, pad)."""
    k = size / 200.0
    for dash in DASHES:
        draw.rectangle(_scaled(dash, k, pad), fill=brass)
    for bar in BARS:
        draw.rectangle(_scaled(bar, k, pad), fill=fg)
    draw.rectangle(_scaled(BASE, k, pad), fill=fg)


def avatar(path: Path, size: int, bg, fg, brass) -> None:
    """Square avatar. YouTube crops it to a circle at display time."""
    img = Image.new("RGB", (size, size), bg)
    draw_mark(ImageDraw.Draw(img), size, fg, brass)
    img.save(path)
    print(f"  {path.name:44} {size}x{size}")


def letterspaced(draw, xy, text, font, fill, tracking: int):
    """Pillow has no letter-spacing, so place each glyph by hand."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x


def measure(draw, text, font, tracking: int) -> float:
    return sum(draw.textlength(c, font=font) for c in text) + tracking * (len(text) - 1)


def banner(path: Path, bg, fg, brass, muted) -> None:
    """
    2048x1152 channel banner.

    Only the centre 1235x338 is guaranteed visible on every device, so the mark,
    wordmark and tagline all live inside it.
    """
    W, H = 2048, 1152
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)

    mark_px = 210
    gap = 56
    name_font = _font(122)
    tag_font = _font(38)
    name, tag = "UPPERCIRCUIT", "INDIAN MARKETS, ONE MINUTE A DAY"
    name_track, tag_track = 6, 9

    name_w = measure(draw, name, name_font, name_track)
    tag_w = measure(draw, tag, tag_font, tag_track)
    block_w = mark_px + gap + max(name_w, tag_w)

    x = (W - block_w) / 2
    cy = H / 2

    # Compose the mark on its own tile, then paste it at the right offset.
    tile = Image.new("RGB", (mark_px, mark_px), bg)
    draw_mark(ImageDraw.Draw(tile), mark_px, fg, brass)
    img.paste(tile, (int(x), int(cy - mark_px / 2)))

    tx = x + mark_px + gap
    letterspaced(draw, (tx, cy - 96), name, name_font, fg, name_track)
    letterspaced(draw, (tx, cy + 44), tag, tag_font, muted, tag_track)

    # Hairline rule under the wordmark, stopping where the tagline ends.
    rule_y = cy + 26
    draw.rectangle([tx, rule_y, tx + max(name_w, tag_w), rule_y + 3], fill=brass)

    img.save(path)
    print(f"  {path.name:44} {W}x{H}")


def svg(path: Path, fg: str, brass: str) -> None:
    """Vector master — the file to hand a designer or resize from."""
    rects = [
        f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="{brass}"/>'
        for x0, y0, x1, y1 in DASHES
    ]
    for x0, y0, x1, y1 in BARS:
        rects.append(f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="{fg}"/>')
    rects.append(f'<rect x="{BASE[0]}" y="{BASE[1]}" '
                 f'width="{BASE[2] - BASE[0]}" height="{BASE[3] - BASE[1]}" fill="{fg}"/>')
    body = "\n  ".join(rects)
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" '
        f'width="800" height="800" role="img" aria-label="UpperCircuit">\n'
        f'  <title>UpperCircuit</title>\n  {body}\n</svg>\n',
        encoding="utf-8",
    )
    print(f"  {path.name:44} vector")


def hexa(rgb) -> str:
    return "#%02X%02X%02X" % rgb


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("UpperCircuit brand assets ->", OUT)

    svg(OUT / "uppercircuit-mark-light.svg", hexa(INK), hexa(BRASS_LT))
    svg(OUT / "uppercircuit-mark-dark.svg", hexa(BONE), hexa(BRASS_DK))

    # Avatars. 800px is well past YouTube's 98px minimum and safely under 4MB.
    avatar(OUT / "uppercircuit-avatar-light-800.png", 800, PAPER, INK, BRASS_LT)
    avatar(OUT / "uppercircuit-avatar-dark-800.png", 800, INK, BONE, BRASS_DK)
    # 98px proves the mark survives the smallest size YouTube accepts.
    avatar(OUT / "uppercircuit-avatar-dark-98.png", 98, INK, BONE, BRASS_DK)

    banner(OUT / "uppercircuit-banner-2048x1152.png", INK, BONE, BRASS_DK, (148, 157, 171))

    print("done")


if __name__ == "__main__":
    main()
