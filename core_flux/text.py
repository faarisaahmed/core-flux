"""Text rendering fallback for FFmpeg builds without ``drawtext``.

``drawtext`` needs libfreetype, and plenty of FFmpeg builds ship without it —
Homebrew's default among them. When it is missing we render the caption to a
transparent PNG with Pillow and overlay that instead, which produces the same
result through a different route.

Pillow is an optional extra (``pip install core-flux[text]``); core-flux itself
still has no required dependencies.
"""

import atexit
import os
import tempfile

# Ordered by preference, first hit wins. Covers macOS, common Linux distros
# and Windows.
FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
)

_TEMP_FILES = []


def _cleanup():
    for path in _TEMP_FILES:
        try:
            os.unlink(path)
        except OSError:
            pass


atexit.register(_cleanup)


def pillow_available():
    """True if Pillow can be imported, without actually importing it."""
    import importlib.util

    return importlib.util.find_spec("PIL") is not None


def find_font(explicit=None):
    """Return a usable TrueType font path, or None to use Pillow's default."""
    if explicit:
        return explicit
    for candidate in FONT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return None


def render_text_png(text, width, height, x, y, size, color, font=None,
                    box=False, box_color=(0, 0, 0, 128)):
    """Draw `text` onto a transparent PNG the size of the video frame.

    Returns the path to a temporary file, deleted when the process exits.
    """
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    font_path = find_font(font)
    if font_path:
        try:
            face = ImageFont.truetype(font_path, int(size))
        except (OSError, IOError):
            face = ImageFont.load_default()
    else:
        face = ImageFont.load_default()

    if box:
        left, top, right, bottom = draw.textbbox((x, y), text, font=face)
        pad = max(4, int(size * 0.15))
        draw.rectangle(
            (left - pad, top - pad, right + pad, bottom + pad), fill=box_color
        )

    draw.text((x, y), text, font=face, fill=color)

    handle, path = tempfile.mkstemp(prefix="coreflux_text_", suffix=".png")
    os.close(handle)
    image.save(path)
    _TEMP_FILES.append(path)
    return path


def _parse_timestamp(value):
    """Turn an SRT timestamp (``00:01:02,500``) into seconds."""
    value = value.strip().replace(",", ".")
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_srt(path):
    """Parse an .srt file into ``[(start, end, text), ...]``.

    Deliberately lenient: numbering is optional and blank lines between cues
    may repeat, which is common in files produced by transcription tools.
    """
    with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
        raw = handle.read()

    cues = []
    for block in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n\n"):
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        # An index line is optional; the timing line is the one with "-->".
        timing = next((line for line in lines if "-->" in line), None)
        if timing is None:
            continue
        text = "\n".join(lines[lines.index(timing) + 1:]).strip()
        if not text:
            continue
        start_text, _, end_text = timing.partition("-->")
        try:
            cues.append((
                _parse_timestamp(start_text), _parse_timestamp(end_text), text
            ))
        except (ValueError, IndexError):
            continue
    return cues


def render_caption_png(text, width, height, size, color, font=None,
                       box=True, box_color=(0, 0, 0, 160), bottom_margin=None):
    """Render a centred, bottom-aligned caption onto a transparent PNG."""
    from PIL import Image, ImageDraw, ImageFont

    width, height = int(width), int(height)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    font_path = find_font(font)
    try:
        face = ImageFont.truetype(font_path, int(size)) if font_path \
            else ImageFont.load_default()
    except (OSError, IOError):
        face = ImageFont.load_default()

    if bottom_margin is None:
        bottom_margin = int(height * 0.08)

    left, top, right, bottom = draw.textbbox((0, 0), text, font=face,
                                             align="center")
    x = (width - (right - left)) // 2 - left
    y = height - bottom_margin - (bottom - top) - top

    if box:
        pad = max(6, int(size * 0.25))
        draw.rectangle(
            (x + left - pad, y + top - pad, x + right + pad, y + bottom + pad),
            fill=box_color,
        )
    draw.text((x, y), text, font=face, fill=color, align="center")

    handle, path = tempfile.mkstemp(prefix="coreflux_cue_", suffix=".png")
    os.close(handle)
    image.save(path)
    _TEMP_FILES.append(path)
    return path
