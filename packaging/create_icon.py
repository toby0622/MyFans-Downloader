"""Generate the complete application icon set from one geometric design."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ICO_OUTPUT = ROOT / "packaging" / "app.ico"
PNG_OUTPUT = ROOT / "packaging" / "app-icon.png"
SVG_OUTPUT = ROOT / "packaging" / "app-icon.svg"
FAVICON_OUTPUT = ROOT / "src" / "myfans_downloader" / "ui" / "favicon.svg"

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
  <defs>
    <linearGradient id="background" x1="36" y1="24" x2="220" y2="236" gradientUnits="userSpaceOnUse">
      <stop stop-color="#151A33"/>
      <stop offset="1" stop-color="#05050D"/>
    </linearGradient>
    <linearGradient id="mark" x1="80" y1="58" x2="160" y2="210" gradientUnits="userSpaceOnUse">
      <stop stop-color="#FFFFFF"/>
      <stop offset="1" stop-color="#DDE3FF"/>
    </linearGradient>
  </defs>
  <rect x="8" y="8" width="240" height="240" rx="54" fill="url(#background)" stroke="#6366F1" stroke-width="8"/>
  <path d="M46 69H80L128 117L176 69H210V107H188L148 147V160H181L128 216L75 160H108V147L68 107H46Z" fill="#4338CA" opacity=".72" transform="translate(0 5)"/>
  <path d="M46 64H80L128 112L176 64H210V102H188L148 142V158H181L128 214L75 158H108V142L68 102H46Z" fill="url(#mark)"/>
</svg>
"""

MARK_POINTS = (
    (46, 64),
    (80, 64),
    (128, 112),
    (176, 64),
    (210, 64),
    (210, 102),
    (188, 102),
    (148, 142),
    (148, 158),
    (181, 158),
    (128, 214),
    (75, 158),
    (108, 158),
    (108, 142),
    (68, 102),
    (46, 102),
)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _interpolate(start: str, end: str, amount: float) -> tuple[int, int, int, int]:
    first = _rgb(start)
    second = _rgb(end)
    return tuple(
        round(a + (b - a) * amount) for a, b in zip(first, second, strict=True)
    ) + (255,)


def _scaled_points(scale: int, y_offset: int = 0) -> list[tuple[int, int]]:
    return [(x * scale, (y + y_offset) * scale) for x, y in MARK_POINTS]


def create_master(scale: int = 4) -> Image.Image:
    size = 256 * scale
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    gradient = Image.new("RGBA", (size, size))
    gradient_draw = ImageDraw.Draw(gradient)
    for y in range(size):
        gradient_draw.line(
            (0, y, size, y),
            fill=_interpolate("#151A33", "#05050D", y / (size - 1)),
        )
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (8 * scale, 8 * scale, 248 * scale, 248 * scale),
        radius=54 * scale,
        fill=255,
    )
    gradient.putalpha(mask)
    canvas.alpha_composite(gradient)

    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle(
        (8 * scale, 8 * scale, 248 * scale, 248 * scale),
        radius=54 * scale,
        outline="#6366F1",
        width=8 * scale,
    )
    draw.polygon(_scaled_points(scale, y_offset=5), fill=(67, 56, 202, 184))
    draw.polygon(_scaled_points(scale), fill="#F8FAFF")
    return canvas


def main() -> None:
    for output in (ICO_OUTPUT, PNG_OUTPUT, SVG_OUTPUT, FAVICON_OUTPUT):
        output.parent.mkdir(parents=True, exist_ok=True)

    SVG_OUTPUT.write_text(ICON_SVG, encoding="utf-8", newline="\n")
    FAVICON_OUTPUT.write_text(ICON_SVG, encoding="utf-8", newline="\n")

    master = create_master()
    master.resize((512, 512), Image.Resampling.LANCZOS).save(PNG_OUTPUT, "PNG")
    master.save(
        ICO_OUTPUT,
        format="ICO",
        sizes=[
            (16, 16),
            (24, 24),
            (32, 32),
            (48, 48),
            (64, 64),
            (128, 128),
            (256, 256),
        ],
    )


if __name__ == "__main__":
    main()
