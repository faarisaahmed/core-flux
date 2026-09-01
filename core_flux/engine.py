"""The core-flux compositing engine.

Every editing call appends a native FFmpeg filter to a graph; nothing is
executed until :meth:`Composition.render`, which compiles the whole timeline
into a single FFmpeg invocation.
"""

import os
import subprocess
import time
import warnings

from .graph import Graph, InputFile, multi_filter
from .errors import (
    CoreFluxError,
    FilterUnavailableError,
    MediaNotFoundError,
    RenderError,
    UnsupportedMediaError,
)
from .probe import (
    best_h264_encoder,
    ensure_ffmpeg,
    has_filter,
    inspect_media,
)
from .text import (
    parse_srt,
    pillow_available,
    render_caption_png,
    render_text_png,
)

AUDIO_EXTENSIONS = frozenset(
    [".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg", ".opus", ".wma"]
)
FASTSTART_EXTENSIONS = frozenset([".mp4", ".m4v", ".mov"])
# Containers that take H.264/AAC. Anything else (.webm, .avi, .ogv) is left to
# FFmpeg's own codec defaults rather than being forced into an invalid pairing.
H264_EXTENSIONS = frozenset([".mp4", ".m4v", ".mov", ".mkv", ".ts", ".flv"])

# The Pillow subtitle fallback adds one FFmpeg input per cue, so it only suits
# short tracks. Past this, a libass build is the right answer.
MAX_FALLBACK_CUES = 100

# Quality defaults per hardware encoder; none of them accept -crf, and each
# vendor exposes a different knob. The VideoToolbox value was calibrated by
# SSIM against the source: q:v=50 scores 0.959 versus libx264 -crf 23 at 0.961,
# while producing a smaller file. The others follow each vendor's stated
# crf-equivalent.
HARDWARE_QUALITY = {
    "h264_videotoolbox": {"q:v": 50},
    "h264_nvenc": {"cq": 23, "preset": "p4"},
    "h264_qsv": {"global_quality": 23},
    "h264_amf": {"quality": "balanced", "qp_i": 23, "qp_p": 23},
}

# atempo only accepts 0.5-100.0 per instance, so extreme speeds are chained.
_ATEMPO_MIN = 0.5
_ATEMPO_MAX = 100.0


def _version():
    # Imported lazily: core_flux/__init__ imports this module, so a top-level
    # import here would be circular.
    from . import __version__

    return __version__


def format_from_path(output_path):
    """Infer the render mode ('video', 'gif' or 'audio') from a file extension."""
    ext = os.path.splitext(output_path)[1].lower()
    if ext == ".gif":
        return "gif"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    return "video"


def _atempo_chain(audio_stream, factor):
    """Apply `factor` playback speed to audio, chaining atempo past its limits."""
    remaining = float(factor)
    while remaining > _ATEMPO_MAX:
        audio_stream = audio_stream.filter("atempo", _ATEMPO_MAX)
        remaining /= _ATEMPO_MAX
    while remaining < _ATEMPO_MIN:
        audio_stream = audio_stream.filter("atempo", _ATEMPO_MIN)
        remaining /= _ATEMPO_MIN
    if abs(remaining - 1.0) > 1e-9:
        audio_stream = audio_stream.filter("atempo", remaining)
    return audio_stream


def _positive(name, value):
    value = float(value)
    if value <= 0:
        raise ValueError("%s must be greater than 0, got %r" % (name, value))
    return value


class _Layer(object):
    """Shared timeline behaviour for anything that can sit on a Composition."""

    def __init__(self):
        self.start_time = 0.0
        self._duration = None
        # Only meaningful for visual layers, but kept here so _from_streams and
        # every subclass get consistent defaults.
        self.x_pos = 0
        self.y_pos = 0

    @property
    def duration(self):
        """Length of this layer in seconds, tracking every edit applied so far.

        ``None`` when the source duration could not be determined.
        """
        return self._duration

    @property
    def end(self):
        """Timeline position, in seconds, where this layer stops."""
        if self._duration is None:
            return None
        return self.start_time + self._duration

    def set_start(self, start_time):
        """Delay this layer so it begins `start_time` seconds into the timeline."""
        if start_time < 0:
            raise ValueError("start_time cannot be negative, got %r" % (start_time,))
        self.start_time = float(start_time)
        return self

    def _scale_duration(self, factor):
        if self._duration is not None:
            self._duration = self._duration / factor

    def _resolve_fade_out_start(self, start_time, duration):
        """Default a fade-out to the final `duration` seconds of the layer."""
        if start_time is not None:
            return float(start_time)
        if self._duration is None:
            raise CoreFluxError(
                "Cannot infer a fade-out position because the source duration is "
                "unknown. Pass start_time explicitly."
            )
        return max(0.0, self._duration - duration)


class VideoLayer(_Layer):
    """An independent video clip that can be positioned, styled and timed."""

    def __init__(self, input_path):
        _Layer.__init__(self)
        info = inspect_media(input_path)
        if not info.has_video:
            raise UnsupportedMediaError(
                "%r contains no video stream. Use AudioLayer for audio-only files."
                % (input_path,)
            )

        node = InputFile(input_path)
        self._input = node
        self.input_path = input_path
        self.video_stream = node.video()
        self.audio_stream = node.audio() if info.has_audio else None
        self._duration = info.duration
        self._width = info.width
        self._height = info.height
        self._fps = info.fps

    @classmethod
    def _from_streams(cls, video_stream, audio_stream, duration, width, height,
                      fps=None, source="<generated>"):
        """Build a layer around an existing graph node (used by generators)."""
        layer = object.__new__(cls)
        _Layer.__init__(layer)
        layer._input = None
        layer.input_path = source
        layer.video_stream = video_stream
        layer.audio_stream = audio_stream
        layer._duration = duration
        layer._width = width
        layer._height = height
        layer._fps = fps
        return layer

    @property
    def width(self):
        """Current width in pixels, reflecting any resize/crop/rotate applied."""
        return self._width

    @property
    def height(self):
        """Current height in pixels, reflecting any resize/crop/rotate applied."""
        return self._height

    @property
    def fps(self):
        return self._fps

    @property
    def has_audio(self):
        return self.audio_stream is not None

    def __repr__(self):
        return "<VideoLayer %r %sx%s %ss audio=%s>" % (
            self.input_path, self._width, self._height, self._duration, self.has_audio,
        )

    # ---------------------------------------------------------------- geometry

    def set_position(self, x, y):
        """Place this layer's top-left corner on the canvas."""
        self.x_pos = x
        self.y_pos = y
        return self

    def resize(self, width, height):
        """Scale this layer to an exact pixel size."""
        self.video_stream = self.video_stream.filter("scale", width, height)
        self._width, self._height = width, height
        return self

    def scale_by(self, factor):
        """Scale by a multiplier, rounding to even dimensions for H.264."""
        _positive("factor", factor)
        self.video_stream = self.video_stream.filter(
            "scale", "trunc(iw*%s/2)*2" % factor, "trunc(ih*%s/2)*2" % factor
        )
        if self._width and self._height:
            self._width = int(self._width * factor / 2) * 2
            self._height = int(self._height * factor / 2) * 2
        return self

    def crop(self, x1, y1, width, height):
        """Keep a rectangular region starting at the top-left corner (x1, y1)."""
        self.video_stream = self.video_stream.filter("crop", width, height, x1, y1)
        self._width, self._height = width, height
        return self

    def rotate(self, degrees):
        """Rotate clockwise. 90/180/270 use the lossless transpose filter."""
        turns = int(degrees) % 360
        if turns == 0:
            return self
        if turns in (90, 180, 270):
            for _ in range(turns // 90):
                self.video_stream = self.video_stream.filter("transpose", 1)
            if turns in (90, 270):
                self._width, self._height = self._height, self._width
            return self
        raise ValueError(
            "rotate() supports 0, 90, 180 or 270 degrees, got %r" % (degrees,)
        )

    def flip(self, axis="horizontal"):
        """Mirror the layer across 'horizontal' or 'vertical'."""
        if axis == "horizontal":
            self.video_stream = self.video_stream.filter("hflip")
        elif axis == "vertical":
            self.video_stream = self.video_stream.filter("vflip")
        else:
            raise ValueError(
                "axis must be 'horizontal' or 'vertical', got %r" % (axis,)
            )
        return self

    # ------------------------------------------------------------------- looks

    def adjust_colors(self, contrast=1.0, brightness=0.0, saturation=1.0):
        """Tune contrast, brightness and saturation."""
        self.video_stream = self.video_stream.filter(
            "eq", contrast=contrast, brightness=brightness, saturation=saturation
        )
        return self

    def blackwhite(self):
        """Drop all colour from this layer."""
        self.video_stream = self.video_stream.filter("hue", s=0)
        return self

    def set_opacity(self, alpha):
        """Make this layer semi-transparent when overlaid (0.0-1.0)."""
        if not 0.0 <= alpha <= 1.0:
            raise ValueError("alpha must be between 0.0 and 1.0, got %r" % (alpha,))
        self.video_stream = (
            self.video_stream
            .filter("format", "yuva420p")
            .filter("colorchannelmixer", aa=alpha)
        )
        return self

    def blur(self, radius=5):
        """Gaussian blur. Higher radius is softer."""
        self.video_stream = self.video_stream.filter("gblur", sigma=radius)
        return self

    def sharpen(self, amount=1.0):
        """Sharpen using an unsharp mask."""
        self.video_stream = self.video_stream.filter(
            "unsharp", luma_msize_x=5, luma_msize_y=5, luma_amount=amount
        )
        return self

    def invert(self):
        """Invert colours (photographic negative)."""
        self.video_stream = self.video_stream.filter("negate")
        return self

    def gamma(self, value=1.0):
        """Gamma-correct the layer. Below 1.0 darkens, above 1.0 brightens."""
        _positive("value", value)
        self.video_stream = self.video_stream.filter("eq", gamma=value)
        return self

    def vignette(self):
        """Darken the corners for a lens-falloff look."""
        self.video_stream = self.video_stream.filter("vignette")
        return self

    def margin(self, size=0, color="black", top=None, bottom=None,
               left=None, right=None):
        """Add a border, expanding the frame.

        `size` sets every side at once; the per-side arguments override it.
        """
        top = size if top is None else top
        bottom = size if bottom is None else bottom
        left = size if left is None else left
        right = size if right is None else right
        self.video_stream = self.video_stream.filter(
            "pad",
            "iw+%d" % (left + right),
            "ih+%d" % (top + bottom),
            left, top, color=color,
        )
        if self._width and self._height:
            self._width += left + right
            self._height += top + bottom
        return self

    def chroma_key(self, color="green", similarity=0.3, blend=0.1):
        """Knock out a background colour, making it transparent.

        Use on an overlay layer; the layer beneath shows through. `similarity`
        widens the range of colours removed, `blend` softens the edge.
        """
        self.video_stream = (
            self.video_stream
            .filter("format", "yuva420p")
            .filter("colorkey", color=color, similarity=similarity, blend=blend)
        )
        return self

    def add_subtitles(self, subtitle_path, force_style=None, size=36,
                      color="white", font=None, box=True):
        """Burn subtitles from an .srt or .ass file into the picture.

        Uses FFmpeg's ``subtitles`` filter when the build has libass. Otherwise
        each cue is rendered with Pillow and overlaid for its own time window,
        which handles .srt only and needs a known layer size.
        """
        if not os.path.isfile(subtitle_path):
            raise MediaNotFoundError("No such subtitle file: %r" % (subtitle_path,))

        if has_filter("subtitles"):
            options = {}
            if force_style:
                options["force_style"] = force_style
            self.video_stream = self.video_stream.filter(
                "subtitles", subtitle_path, **options
            )
            return self

        if not pillow_available():
            raise FilterUnavailableError(
                "Cannot burn subtitles: this FFmpeg build has no 'subtitles' "
                "filter (it needs libass), and Pillow is not installed for the "
                "fallback.\nFix either one:\n"
                "  pip install core-flux[text]      # use the Pillow fallback\n"
                "  brew reinstall ffmpeg            # get a build with libass"
            )
        if not subtitle_path.lower().endswith(".srt"):
            raise FilterUnavailableError(
                "The Pillow subtitle fallback reads .srt only, and this FFmpeg "
                "build has no 'subtitles' filter for %r." % (subtitle_path,)
            )
        return self._add_subtitles_overlay(subtitle_path, size, color, font, box)

    def _add_subtitles_overlay(self, subtitle_path, size, color, font, box):
        if not self._width or not self._height:
            raise CoreFluxError(
                "The Pillow subtitle fallback needs to know the layer size. "
                "Call resize() first, or install an FFmpeg with libass."
            )
        cues = parse_srt(subtitle_path)
        if not cues:
            return self
        if len(cues) > MAX_FALLBACK_CUES:
            raise CoreFluxError(
                "%r has %d cues; the Pillow fallback overlays one input per cue "
                "and stops at %d. Install an FFmpeg with libass for long "
                "subtitle tracks."
                % (subtitle_path, len(cues), MAX_FALLBACK_CUES)
            )

        for start, end, text in cues:
            png = render_caption_png(
                text, self._width, self._height, size, color, font=font, box=box
            )
            # The PNG must stay available for the whole timeline: `enable`
            # decides when it is drawn, but an input that has already ended
            # leaves nothing to draw.
            span = {"loop": 1}
            if self._duration is not None:
                span["t"] = self._duration
            cue = InputFile(png, span).video()
            self.video_stream = multi_filter(
                [self.video_stream, cue], "overlay",
                kwargs={"x": 0, "y": 0, "eof_action": "pass",
                        "enable": "between(t,%s,%s)" % (start, end)},
            )[0]
        return self

    def add_text(self, text, x=10, y=10, size=48, color="white", font=None,
                 box=False, box_color="black@0.5", start=None, end=None):
        """Burn text into the layer.

        Uses FFmpeg's ``drawtext`` when the local build has it. Builds without
        libfreetype (Homebrew's default, among others) fall back to rendering
        the caption with Pillow and overlaying it, which looks the same.

        Install the fallback with ``pip install core-flux[text]``.
        """
        window = None
        if start is not None or end is not None:
            lower = 0 if start is None else start
            upper = self._duration if end is None else end
            if upper is None:
                raise CoreFluxError(
                    "Cannot infer the text end time; pass end= explicitly."
                )
            window = "between(t,%s,%s)" % (lower, upper)

        if has_filter("drawtext"):
            return self._add_text_drawtext(
                text, x, y, size, color, font, box, box_color, window
            )
        if pillow_available():
            return self._add_text_overlay(
                text, x, y, size, color, font, box, window
            )
        raise FilterUnavailableError(
            "Cannot render text: this FFmpeg build has no 'drawtext' filter "
            "(it needs libfreetype), and Pillow is not installed for the "
            "fallback.\nFix either one:\n"
            "  pip install core-flux[text]      # use the Pillow fallback\n"
            "  brew reinstall ffmpeg            # get a build with drawtext"
        )

    def _add_text_drawtext(self, text, x, y, size, color, font, box, box_color,
                           window):
        options = {
            "text": text,
            "x": x,
            "y": y,
            "fontsize": size,
            "fontcolor": color,
        }
        if font:
            options["fontfile"] = font
        if box:
            options["box"] = 1
            options["boxcolor"] = box_color
        if window:
            options["enable"] = window
        self.video_stream = self.video_stream.filter("drawtext", **options)
        return self

    def _add_text_overlay(self, text, x, y, size, color, font, box, window):
        if not self._width or not self._height:
            raise CoreFluxError(
                "The Pillow text fallback needs to know the layer size. Call "
                "resize() first, or install an FFmpeg with drawtext."
            )
        png = render_text_png(
            text, self._width, self._height, x, y, size, color,
            font=font, box=box,
        )
        # The caption is already positioned within a full-frame transparent
        # PNG, so it overlays at the origin.
        options = {"x": 0, "y": 0, "eof_action": "pass"}
        if window:
            options["enable"] = window
        overlay_options = {"loop": 1}
        if self._duration is not None:
            overlay_options["t"] = self._duration
        caption = InputFile(png, overlay_options).video()
        self.video_stream = multi_filter(
            [self.video_stream, caption], "overlay", kwargs=options
        )[0]
        return self

    # ------------------------------------------------------------------- audio

    def with_volume_scaled_to(self, factor):
        """Scale this layer's embedded audio. 0.5 halves it, 2.0 doubles it."""
        if self.audio_stream is not None:
            self.audio_stream = self.audio_stream.filter("volume", volume=factor)
        return self

    def mute(self):
        """Drop this layer's embedded audio entirely."""
        self.audio_stream = None
        return self

    # -------------------------------------------------------------------- time

    def trim(self, start, end):
        """Keep only the section between two timestamps, in seconds."""
        if end <= start:
            raise ValueError(
                "trim() end must be greater than start, got start=%r end=%r"
                % (start, end)
            )
        self.video_stream = (
            self.video_stream
            .filter("trim", start=start, end=end)
            .filter("setpts", "PTS-STARTPTS")
        )
        if self.audio_stream is not None:
            self.audio_stream = (
                self.audio_stream
                .filter("atrim", start=start, end=end)
                .filter("asetpts", "PTS-STARTPTS")
            )
        self._duration = float(end - start)
        return self

    def subclip(self, start=0, end=None):
        """Alias of :meth:`trim`; `end` defaults to the end of the clip."""
        if end is None:
            if self._duration is None:
                raise CoreFluxError("Unknown duration; pass end= explicitly.")
            end = self._duration
        return self.trim(start, end)

    def speed(self, factor):
        """Change playback speed. 2.0 is twice as fast, 0.5 is half speed."""
        _positive("factor", factor)
        self.video_stream = self.video_stream.filter("setpts", "PTS/%s" % factor)
        if self.audio_stream is not None:
            self.audio_stream = _atempo_chain(self.audio_stream, factor)
        self._scale_duration(factor)
        return self

    def reverse(self):
        """Play the layer backwards, audio included."""
        self.video_stream = self.video_stream.filter("reverse")
        if self.audio_stream is not None:
            self.audio_stream = self.audio_stream.filter("areverse")
        return self

    def loop(self, count):
        """Repeat the layer `count` times in total.

        Implemented with FFmpeg's input-level ``-stream_loop``, which re-reads
        the file rather than buffering decoded frames in RAM the way the
        ``loop`` filter does.
        """
        count = int(count)
        if count < 1:
            raise ValueError("loop() count must be at least 1, got %r" % (count,))
        if self._input is None:
            raise CoreFluxError(
                "loop() needs a file-backed layer; this one was generated "
                "(by concatenate(), for example)."
            )
        if count > 1:
            self._input.options["stream_loop"] = count - 1
        if self._duration is not None:
            self._duration *= count
        return self

    def hold_last_frame(self, duration):
        """Freeze on the final frame for an extra `duration` seconds."""
        _positive("duration", duration)
        self.video_stream = self.video_stream.filter(
            "tpad", stop_duration=duration, stop_mode="clone"
        )
        if self.audio_stream is not None:
            self.audio_stream = self.audio_stream.filter("apad", pad_dur=duration)
        if self._duration is not None:
            self._duration += duration
        return self

    def fade_in(self, start_time=0.0, duration=1.0):
        """Fade both picture and native audio up from black/silence."""
        self.video_stream = self.video_stream.filter(
            "fade", type="in", start_time=start_time, duration=duration
        )
        if self.audio_stream is not None:
            self.audio_stream = self.audio_stream.filter(
                "afade", type="in", start_time=start_time, duration=duration
            )
        return self

    def fade_out(self, start_time=None, duration=1.0, start_fade=None):
        """Fade both picture and native audio down to black/silence.

        With no `start_time`, the fade lands on the final `duration` seconds.
        """
        if start_fade is not None:
            warnings.warn(
                "fade_out(start_fade=...) is deprecated; use start_time=... instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            start_time = start_fade
        start_time = self._resolve_fade_out_start(start_time, duration)
        self.video_stream = self.video_stream.filter(
            "fade", type="out", start_time=start_time, duration=duration
        )
        if self.audio_stream is not None:
            self.audio_stream = self.audio_stream.filter(
                "afade", type="out", start_time=start_time, duration=duration
            )
        return self


class ImageLayer(VideoLayer):
    """A still image held on screen for a fixed duration (logos, title cards)."""

    def __init__(self, input_path, duration=5.0, fps=30):
        _Layer.__init__(self)
        _positive("duration", duration)
        info = inspect_media(input_path)
        if not info.has_video:
            raise UnsupportedMediaError("%r is not a readable image." % (input_path,))

        node = InputFile(input_path, {"loop": 1, "framerate": fps, "t": duration})
        self._input = node
        self.input_path = input_path
        self.video_stream = node.video()
        self.audio_stream = None
        self._duration = float(duration)
        self._width = info.width
        self._height = info.height
        self._fps = fps


class ColorLayer(VideoLayer):
    """A solid colour canvas, useful as a background behind other layers."""

    def __init__(self, width, height, duration, color="black", fps=30):
        _Layer.__init__(self)
        _positive("duration", duration)
        ensure_ffmpeg()
        source = "color=c=%s:s=%dx%d:r=%d" % (color, width, height, fps)
        node = InputFile(source, {"f": "lavfi", "t": duration})
        self._input = node
        self.input_path = source
        self.video_stream = node.video()
        self.audio_stream = None
        self._duration = float(duration)
        self._width = width
        self._height = height
        self._fps = fps


class AudioLayer(_Layer):
    """A standalone audio track, such as music or a sound effect."""

    def __init__(self, input_path):
        _Layer.__init__(self)
        info = inspect_media(input_path)
        if not info.has_audio:
            raise UnsupportedMediaError(
                "%r contains no audio stream." % (input_path,)
            )
        node = InputFile(input_path)
        self._input = node
        self.input_path = input_path
        self.audio_stream = node.audio()
        self._duration = info.duration

    def __repr__(self):
        return "<AudioLayer %r %ss>" % (self.input_path, self._duration)

    def with_volume_scaled_to(self, factor):
        """Scale this track's volume. 0.5 halves it, 2.0 doubles it."""
        self.audio_stream = self.audio_stream.filter("volume", volume=factor)
        return self

    def mute(self):
        return self.with_volume_scaled_to(0.0)

    def trim(self, start, end):
        """Keep only the section between two timestamps, in seconds."""
        if end <= start:
            raise ValueError(
                "trim() end must be greater than start, got start=%r end=%r"
                % (start, end)
            )
        self.audio_stream = (
            self.audio_stream
            .filter("atrim", start=start, end=end)
            .filter("asetpts", "PTS-STARTPTS")
        )
        self._duration = float(end - start)
        return self

    def subclip(self, start=0, end=None):
        """Alias of :meth:`trim`; `end` defaults to the end of the track."""
        if end is None:
            if self._duration is None:
                raise CoreFluxError("Unknown duration; pass end= explicitly.")
            end = self._duration
        return self.trim(start, end)

    def speed(self, factor):
        """Change playback speed without changing pitch."""
        _positive("factor", factor)
        self.audio_stream = _atempo_chain(self.audio_stream, factor)
        self._scale_duration(factor)
        return self

    def normalize(self, target=-16.0):
        """Loudness-normalise to `target` LUFS (EBU R128).

        -16 LUFS suits podcasts and web video; -14 is common for music
        streaming. This is a single-pass measurement, so it is fast but
        slightly less exact than a two-pass analysis.
        """
        self.audio_stream = self.audio_stream.filter("loudnorm", I=target)
        return self

    def reverse(self):
        """Play the track backwards."""
        self.audio_stream = self.audio_stream.filter("areverse")
        return self

    def loop(self, count):
        """Repeat the track `count` times in total."""
        count = int(count)
        if count < 1:
            raise ValueError("loop() count must be at least 1, got %r" % (count,))
        if count > 1:
            self._input.options["stream_loop"] = count - 1
        if self._duration is not None:
            self._duration *= count
        return self

    def fade_in(self, start_time=0.0, duration=1.0):
        """Fade this track up from silence."""
        self.audio_stream = self.audio_stream.filter(
            "afade", type="in", start_time=start_time, duration=duration
        )
        return self

    def fade_out(self, start_time=None, duration=1.0):
        """Fade this track down to silence, defaulting to its final seconds."""
        start_time = self._resolve_fade_out_start(start_time, duration)
        self.audio_stream = self.audio_stream.filter(
            "afade", type="out", start_time=start_time, duration=duration
        )
        return self


def concatenate(layers, width=None, height=None, fps=None, audio=True):
    """Join clips end to end into a single :class:`VideoLayer`.

    Concat requires every segment to share a resolution, pixel aspect and frame
    rate, so each one is normalised first. Segments with no audio contribute
    matching silence, which keeps picture and sound aligned across the join.

    The result is an ordinary :class:`VideoLayer`, so it can be trimmed, faded
    and overlaid like any other clip.
    """
    layers = list(layers)
    if not layers:
        raise ValueError("concatenate() needs at least one layer.")
    if len(layers) == 1:
        return layers[0]

    first = layers[0]
    width = width or first.width
    height = height or first.height
    fps = fps or first.fps or 30
    if not width or not height:
        raise CoreFluxError(
            "Could not determine an output size for concatenate(); "
            "pass width= and height= explicitly."
        )

    include_audio = audio and any(layer.has_audio for layer in layers)
    total = 0.0
    known_durations = True
    parts = []

    for layer in layers:
        video = (
            layer.video_stream
            .filter("scale", width, height)
            .filter("setsar", 1)
            .filter("fps", fps)
        )
        parts.append(video)

        if include_audio:
            if layer.has_audio:
                track = layer.audio_stream.filter(
                    "aformat", sample_rates=48000, channel_layouts="stereo"
                )
            else:
                if layer.duration is None:
                    raise CoreFluxError(
                        "Cannot pad %r with silence because its duration is unknown."
                        % (layer.input_path,)
                    )
                track = InputFile(
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                    {"f": "lavfi", "t": layer.duration},
                ).audio()
            parts.append(track)

        if layer.duration is None:
            known_durations = False
        else:
            total += layer.duration

    kinds = ("v", "a") if include_audio else ("v",)
    joined = multi_filter(
        parts, "concat", output_kinds=kinds,
        kwargs={"n": len(layers), "v": 1, "a": 1 if include_audio else 0},
    )

    return VideoLayer._from_streams(
        video_stream=joined[0],
        audio_stream=joined[1] if include_audio else None,
        duration=total if known_durations else None,
        width=width,
        height=height,
        fps=fps,
        source="<concatenate of %d clips>" % len(layers),
    )


# The transition names FFmpeg's xfade filter accepts. Kept as a tuple so a
# typo is caught in Python with a readable list rather than deep inside FFmpeg.
TRANSITIONS = (
    "fade", "fadeblack", "fadewhite", "fadegrays", "distance", "wipeleft",
    "wiperight", "wipeup", "wipedown", "slideleft", "slideright", "slideup",
    "slidedown", "smoothleft", "smoothright", "smoothup", "smoothdown",
    "circlecrop", "rectcrop", "circleclose", "circleopen", "horzclose",
    "horzopen", "vertclose", "vertopen", "diagbl", "diagbr", "diagtl", "diagtr",
    "hlslice", "hrslice", "vuslice", "vdslice", "dissolve", "pixelize",
    "radial", "hblur", "wipetl", "wipetr", "wipebl", "wipebr", "zoomin",
    "squeezev", "squeezeh",
)


def crossfade(layers, duration=1.0, transition="fade", width=None, height=None,
              fps=None):
    """Join clips with a crossfade instead of a hard cut.

    Each clip overlaps the next by `duration` seconds, so the total runtime is
    ``sum(durations) - duration * (len(layers) - 1)``. `transition` picks the
    visual style; see :data:`TRANSITIONS` for the full list. Audio is
    crossfaded to match whenever every clip has a soundtrack.

    Returns an ordinary :class:`VideoLayer`, so the result can be trimmed,
    faded and overlaid like any other clip.
    """
    layers = list(layers)
    if not layers:
        raise ValueError("crossfade() needs at least one layer.")
    if len(layers) == 1:
        return layers[0]
    if transition not in TRANSITIONS:
        raise ValueError(
            "Unknown transition %r. Choose one of: %s"
            % (transition, ", ".join(TRANSITIONS))
        )
    _positive("duration", duration)

    for layer in layers:
        if layer.duration is None:
            raise CoreFluxError(
                "crossfade() needs known durations; %r could not be probed."
                % (layer.input_path,)
            )
        if layer.duration <= duration:
            raise ValueError(
                "Clip %r is %.2fs, which is not longer than the %.2fs "
                "transition. Use a shorter transition or a longer clip."
                % (layer.input_path, layer.duration, duration)
            )

    first = layers[0]
    width = width or first.width
    height = height or first.height
    fps = fps or first.fps or 30
    if not width or not height:
        raise CoreFluxError(
            "Could not determine an output size for crossfade(); "
            "pass width= and height= explicitly."
        )

    include_audio = all(layer.has_audio for layer in layers)

    def normalise(layer):
        return (
            layer.video_stream
            .filter("scale", width, height)
            .filter("setsar", 1)
            .filter("fps", fps)
            .filter("format", "yuv420p")
        )

    video = normalise(first)
    audio = first.audio_stream if include_audio else None
    # Each xfade offset is measured on its left input, which already contains
    # every clip merged so far, minus the overlap consumed by each transition.
    elapsed = first.duration

    for layer in layers[1:]:
        offset = elapsed - duration
        video = multi_filter(
            [video, normalise(layer)], "xfade",
            kwargs={"transition": transition, "duration": duration,
                    "offset": round(offset, 6)},
        )[0]
        if include_audio:
            audio = multi_filter(
                [audio, layer.audio_stream], "acrossfade",
                output_kinds=("a",), kwargs={"d": duration},
            )[0]
        elapsed = offset + layer.duration

    return VideoLayer._from_streams(
        video_stream=video,
        audio_stream=audio,
        duration=elapsed,
        width=width,
        height=height,
        fps=fps,
        source="<crossfade of %d clips>" % len(layers),
    )


class Composition(object):
    """The timeline. Layers stack bottom-up; the first one is the canvas.

    Output length follows the base video layer: a longer music bed is cut off
    at the end of the picture, and a shorter one is padded with silence.
    """

    def __init__(self, layers=None, audio_tracks=None):
        self.layers = list(layers) if layers is not None else []
        self.audio_tracks = list(audio_tracks) if audio_tracks is not None else []

    def __repr__(self):
        return "<Composition layers=%d audio_tracks=%d duration=%s>" % (
            len(self.layers), len(self.audio_tracks), self.duration,
        )

    def add_layer(self, layer):
        """Stack another visual layer on top of the current ones."""
        self.layers.append(layer)
        return self

    def add_audio(self, track):
        """Add another audio track to the mix."""
        self.audio_tracks.append(track)
        return self

    @property
    def duration(self):
        """Expected output length: the base video layer, or the longest track."""
        if self.layers:
            return self.layers[0].end
        ends = [t.end for t in self.audio_tracks if t.end is not None]
        return max(ends) if ends else None

    # ------------------------------------------------------------ graph building

    def _compose_video(self):
        base_layer = self.layers[0]
        base = base_layer.video_stream
        if base_layer.start_time > 0:
            base = base.filter("tpad", start_duration=base_layer.start_time)

        for layer in self.layers[1:]:
            overlay = layer.video_stream
            options = {
                "x": layer.x_pos,
                "y": layer.y_pos,
                # 'pass' lets a short overlay disappear when it ends. FFmpeg's
                # default of 'repeat' would freeze its last frame on screen.
                "eof_action": "pass",
            }
            if layer.start_time > 0:
                # tpad delays the clip; enable keeps the padding from being drawn.
                overlay = overlay.filter("tpad", start_duration=layer.start_time)
                options["enable"] = "gte(t,%s)" % layer.start_time
            base = multi_filter([base, overlay], "overlay", kwargs=options)[0]
        return base

    def _collect_audio(self):
        streams = []
        for source in list(self.layers) + list(self.audio_tracks):
            track = getattr(source, "audio_stream", None)
            if track is None:
                continue
            if source.start_time > 0:
                track = track.filter(
                    "adelay", delays=int(source.start_time * 1000), all=1
                )
            streams.append(track)
        return streams

    def _mix_audio(self, streams):
        if not streams:
            return None
        if len(streams) == 1:
            return streams[0]
        # normalize=0 keeps the volume each track was set to. FFmpeg's default
        # divides every input by the number of tracks, so with_volume_scaled_to
        # would silently mean something different as tracks were added.
        return multi_filter(
            streams, "amix", output_kinds=("a",),
            kwargs={"inputs": len(streams), "normalize": 0, "dropout_transition": 0},
        )[0]

    def _build_output(self, output_path, format_type=None, duration=None,
                      gif_fps=15, gif_width=None, hardware=False,
                      **encoder_options):
        ensure_ffmpeg()
        if format_type is None:
            format_type = format_from_path(output_path)
        if format_type not in ("video", "gif", "audio", "frame"):
            raise ValueError(
                "format_type must be 'video', 'gif' or 'audio', got %r" % (format_type,)
            )
        if format_type != "audio" and not self.layers:
            raise CoreFluxError(
                "Cannot render %r without any video layers. Add one with "
                "Composition(layers=[...]) or use format_type='audio'."
                % (format_type,)
            )

        audio_streams = self._collect_audio()
        final_audio = self._mix_audio(audio_streams)
        options = {}

        if format_type == "audio":
            if final_audio is None:
                raise CoreFluxError(
                    "format_type='audio' but no layer or track carries audio."
                )
            # No codec is forced here: FFmpeg's per-container default is already
            # correct (pcm_s16le for .wav, libmp3lame for .mp3, aac for .m4a),
            # and hardcoding one produces unplayable files for some containers.
            streams = [final_audio]

        elif format_type == "frame":
            # Still images take the picture only; mapping audio into a PNG
            # makes FFmpeg fail looking for an image2 audio encoder.
            streams = [self._compose_video()]

        elif format_type == "gif":
            video = self._compose_video().filter("fps", gif_fps)
            if gif_width:
                video = video.filter("scale", gif_width, -1, flags="lanczos")
            # palettegen and paletteuse both read the same frames; the graph
            # builder inserts the required split automatically.
            palette = video.filter("palettegen")
            streams = [multi_filter([video, palette], "paletteuse")[0]]
            options.update({"f": "gif", "loop": 0})

        else:
            video = self._compose_video()
            streams = [video]
            if os.path.splitext(output_path)[1].lower() in H264_EXTENSIONS:
                encoder = best_h264_encoder(hardware)
                options.update({
                    "vcodec": encoder,
                    "acodec": "aac",
                    "pix_fmt": "yuv420p",
                })
                # Hardware encoders ignore -crf and default to a very high
                # bitrate, so each family needs its own quality knob.
                options.update(HARDWARE_QUALITY.get(encoder, {}))
            if final_audio is not None:
                # apad plus -shortest pins the output to the picture length:
                # a short track is padded with silence, a long one is cut off.
                streams.append(final_audio.filter("apad"))
                options["shortest"] = None
            if os.path.splitext(output_path)[1].lower() in FASTSTART_EXTENSIONS:
                options["movflags"] = "+faststart"

        if duration is not None:
            options["t"] = duration

        # Caller-supplied encoder settings win over the defaults.
        options.update(encoder_options)
        return streams, options, format_type

    @staticmethod
    def _option_args(options):
        """Turn an output-option mapping into FFmpeg arguments.

        A value of ``None`` means a bare flag, so ``{"shortest": None}`` emits
        ``-shortest`` rather than ``-shortest None``.
        """
        args = []
        for name, value in options.items():
            args.append("-" + name)
            if value is not None:
                args.append(str(value))
        return args

    def _compile(self, output_path, format_type=None, overwrite=True, **kwargs):  # noqa: E501
        """Build the full FFmpeg argument list for this composition."""
        streams, options, resolved = self._build_output(
            output_path, format_type, **kwargs
        )
        input_args, filter_complex, maps = Graph().build(streams)

        command = ["ffmpeg", "-hide_banner", "-nostdin", "-y" if overwrite else "-n"]
        command += input_args
        if filter_complex:
            command += ["-filter_complex", filter_complex]
        for label in maps:
            # Input streams map as 0:v; filter outputs map as [s3].
            command += ["-map", label if ":" in label else "[%s]" % label]
        command += self._option_args(options)
        command.append(output_path)
        return command, resolved

    def save_frame(self, output_path, t=0.0, **kwargs):
        """Write a single frame at `t` seconds to an image file.

        Useful for thumbnails and contact sheets without decoding the whole
        timeline.
        """
        return self.render(
            output_path, format_type="frame", quiet=kwargs.pop("quiet", True),
            ss=t, **dict({"frames:v": 1, "update": 1}, **kwargs)
        )

    def get_command(self, output_path, format_type=None, **kwargs):
        """Return the FFmpeg command this composition would run, as a string.

        Useful for debugging a graph, or for lifting the command into a shell
        script or CI job.
        """
        command, _ = self._compile(output_path, format_type, **kwargs)
        return " ".join(command)

    def render(self, output_path, format_type=None, quiet=False, verbose=False,
               overwrite=True, duration=None, hardware=False, **kwargs):
        """Compile the timeline and write it to `output_path`.

        `format_type` is inferred from the extension when omitted. Extra keyword
        arguments are passed to FFmpeg as output options, so `crf=18` or
        `preset='slow'` override the defaults.
        """
        if not overwrite and os.path.exists(output_path):
            raise CoreFluxError(
                "%r already exists and overwrite=False." % (output_path,)
            )

        command, resolved_format = self._compile(
            output_path, format_type, overwrite=overwrite, duration=duration,
            hardware=hardware, **kwargs
        )
        if not quiet:
            print("CORE-FLUX v%s: rendering %s -> %s"
                  % (_version(), resolved_format, output_path))

        started = time.time()
        result = subprocess.run(
            command,
            stdout=None if verbose else subprocess.PIPE,
            stderr=None if verbose else subprocess.PIPE,
        )
        if result.returncode != 0:
            stderr = (result.stderr or b"").decode("utf-8", "replace")
            raise RenderError(
                "FFmpeg failed while rendering %r." % (output_path,),
                command=" ".join(command),
                stderr="\n".join(stderr.strip().splitlines()[-25:]),
            )

        elapsed = time.time() - started
        if not quiet:
            print("Done in %.2fs -> %s" % (elapsed, output_path))
        return output_path
