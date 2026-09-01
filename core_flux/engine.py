"""The core-flux compositing engine.

Every editing call appends a native FFmpeg filter to a graph; nothing is
executed until :meth:`Composition.render`, which compiles the whole timeline
into a single FFmpeg invocation.
"""

import os
import time
import warnings

import ffmpeg

from .errors import (
    CoreFluxError,
    FilterUnavailableError,
    RenderError,
    UnsupportedMediaError,
)
from .probe import ensure_ffmpeg, has_filter, inspect_media

AUDIO_EXTENSIONS = frozenset(
    [".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg", ".opus", ".wma"]
)
FASTSTART_EXTENSIONS = frozenset([".mp4", ".m4v", ".mov"])
# Containers that take H.264/AAC. Anything else (.webm, .avi, .ogv) is left to
# FFmpeg's own codec defaults rather than being forced into an invalid pairing.
H264_EXTENSIONS = frozenset([".mp4", ".m4v", ".mov", ".mkv", ".ts", ".flv"])

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

        node = ffmpeg.input(input_path)
        self.input_path = input_path
        self.video_stream = node.video
        self.audio_stream = node.audio if info.has_audio else None
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

    def add_text(self, text, x=10, y=10, size=48, color="white", font=None,
                 box=False, box_color="black@0.5", start=None, end=None):
        """Burn text into the layer.

        Requires an FFmpeg built with libfreetype; raises
        :class:`~core_flux.errors.FilterUnavailableError` if the local build
        lacks the ``drawtext`` filter.
        """
        if not has_filter("drawtext"):
            raise FilterUnavailableError(
                "This FFmpeg build has no 'drawtext' filter (it needs libfreetype).\n"
                "On macOS: brew reinstall ffmpeg. Check with: ffmpeg -filters | grep drawtext"
            )
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
        if start is not None or end is not None:
            lower = 0 if start is None else start
            upper = self._duration if end is None else end
            if upper is None:
                raise CoreFluxError(
                    "Cannot infer the text end time; pass end= explicitly."
                )
            options["enable"] = "between(t,%s,%s)" % (lower, upper)
        self.video_stream = self.video_stream.filter("drawtext", **options)
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

        node = ffmpeg.input(input_path, loop=1, framerate=fps, t=duration)
        self.input_path = input_path
        self.video_stream = node.video
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
        node = ffmpeg.input(source, f="lavfi", t=duration)
        self.input_path = source
        self.video_stream = node.video
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
        self.input_path = input_path
        self.audio_stream = ffmpeg.input(input_path).audio
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
                track = ffmpeg.input(
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                    f="lavfi",
                    t=layer.duration,
                ).audio
            parts.append(track)

        if layer.duration is None:
            known_durations = False
        else:
            total += layer.duration

    joined = ffmpeg.concat(
        *parts, v=1, a=1 if include_audio else 0, n=len(layers)
    ).node

    return VideoLayer._from_streams(
        video_stream=joined[0],
        audio_stream=joined[1] if include_audio else None,
        duration=total if known_durations else None,
        width=width,
        height=height,
        fps=fps,
        source="<concatenate of %d clips>" % len(layers),
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
            base = ffmpeg.overlay(base, overlay, **options)
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
        return ffmpeg.filter(
            streams, "amix", inputs=len(streams), normalize=0, dropout_transition=0
        )

    def _build_output(self, output_path, format_type=None, duration=None,
                      gif_fps=15, gif_width=None, **encoder_options):
        ensure_ffmpeg()
        if format_type is None:
            format_type = format_from_path(output_path)
        if format_type not in ("video", "gif", "audio"):
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

        elif format_type == "gif":
            video = self._compose_video().filter("fps", gif_fps)
            if gif_width:
                video = video.filter("scale", gif_width, -1, flags="lanczos")
            # palettegen and paletteuse both read the same frames, so the stream
            # has to be split explicitly; reusing the node directly is an error.
            branch = video.split()
            palette = branch[0].filter("palettegen")
            streams = [ffmpeg.filter([branch[1], palette], "paletteuse")]
            options.update({"f": "gif", "loop": 0})

        else:
            video = self._compose_video()
            streams = [video]
            if os.path.splitext(output_path)[1].lower() in H264_EXTENSIONS:
                options.update(
                    {"vcodec": "libx264", "acodec": "aac", "pix_fmt": "yuv420p"}
                )
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
        return ffmpeg.output(*streams, filename=output_path, **options), format_type

    def get_command(self, output_path, format_type=None, **kwargs):
        """Return the FFmpeg command this composition would run, as a string.

        Useful for debugging a graph, or for lifting the command into a shell
        script or CI job.
        """
        output, _ = self._build_output(output_path, format_type, **kwargs)
        return " ".join(["ffmpeg"] + output.overwrite_output().get_args())

    def render(self, output_path, format_type=None, quiet=False, verbose=False,
               overwrite=True, duration=None, **kwargs):
        """Compile the timeline and write it to `output_path`.

        `format_type` is inferred from the extension when omitted. Extra keyword
        arguments are passed to FFmpeg as output options, so `crf=18` or
        `preset='slow'` override the defaults.
        """
        if not overwrite and os.path.exists(output_path):
            raise CoreFluxError(
                "%r already exists and overwrite=False." % (output_path,)
            )

        output, resolved_format = self._build_output(
            output_path, format_type, duration=duration, **kwargs
        )
        if overwrite:
            output = output.overwrite_output()
        else:
            # -n makes FFmpeg refuse rather than prompt. Without it, -nostdin
            # leaves the prompt unanswerable and the file gets clobbered.
            output = output.global_args("-n")

        command = " ".join(["ffmpeg"] + output.get_args())
        if not quiet:
            print("CORE-FLUX v%s: rendering %s -> %s"
                  % (_version(), resolved_format, output_path))

        started = time.time()
        try:
            output.run(
                cmd=["ffmpeg", "-hide_banner", "-nostdin"],
                capture_stdout=not verbose,
                capture_stderr=not verbose,
            )
        except ffmpeg.Error as exc:
            stderr = exc.stderr.decode("utf-8", "replace") if exc.stderr else ""
            raise RenderError(
                "FFmpeg failed while rendering %r." % (output_path,),
                command=command,
                stderr="\n".join(stderr.strip().splitlines()[-25:]),
            )

        elapsed = time.time() - started
        if not quiet:
            print("Done in %.2fs -> %s" % (elapsed, output_path))
        return output_path
