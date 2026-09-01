"""Media inspection and FFmpeg capability detection.

``ffprobe`` costs a subprocess launch, so results are cached per
(path, mtime, size). Editing a file on disk invalidates its entry naturally.
"""

import functools
import os
import shutil
import subprocess

import ffmpeg

from .errors import FFmpegNotFoundError, MediaNotFoundError, UnsupportedMediaError

_INSTALL_HINT = (
    "FFmpeg was not found on your PATH. Install it first:\n"
    "  macOS:   brew install ffmpeg\n"
    "  Ubuntu:  sudo apt install ffmpeg\n"
    "  Windows: winget install ffmpeg"
)


@functools.lru_cache(maxsize=1)
def ensure_ffmpeg():
    """Raise a readable error if the ffmpeg/ffprobe binaries are missing."""
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise FFmpegNotFoundError(
            "Missing required binaries: %s.\n%s" % (", ".join(missing), _INSTALL_HINT)
        )
    return True


@functools.lru_cache(maxsize=1)
def available_filters():
    """Return the set of filter names this FFmpeg build actually supports."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()

    names = set()
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split()
        # Rows look like: " T.. drawtext  V->V  Draw text on top of video frames."
        if len(parts) >= 3 and "->" in parts[2]:
            names.add(parts[1])
    return frozenset(names)


def has_filter(name):
    """True if `name` is compiled into the local FFmpeg build."""
    filters = available_filters()
    # An empty set means detection itself failed; assume the filter is present
    # rather than blocking an operation that would probably have worked.
    return not filters or name in filters


class MediaInfo(object):
    """Facts about an input file that the engine needs while building a graph."""

    __slots__ = ("path", "duration", "width", "height", "fps", "has_video", "has_audio")

    def __init__(self, path, duration, width, height, fps, has_video, has_audio):
        self.path = path
        self.duration = duration
        self.width = width
        self.height = height
        self.fps = fps
        self.has_video = has_video
        self.has_audio = has_audio

    def __repr__(self):
        return "MediaInfo(path=%r, duration=%r, size=%rx%r, fps=%r, audio=%r)" % (
            self.path, self.duration, self.width, self.height, self.fps, self.has_audio,
        )


def _to_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # ffprobe reports "N/A" as nan for some containers.
    return number if number == number else None


def _parse_rate(value):
    """Turn an ffprobe rational such as '30000/1001' into a float."""
    if not value or value == "0/0":
        return None
    if "/" in value:
        num, _, den = value.partition("/")
        num, den = _to_float(num), _to_float(den)
        if not num or not den:
            return None
        return num / den
    return _to_float(value)


@functools.lru_cache(maxsize=256)
def _probe_raw(path, mtime, size):
    return ffmpeg.probe(path)


def inspect_media(path):
    """Probe `path` and return a :class:`MediaInfo`.

    Raises :class:`MediaNotFoundError` up front rather than letting a bad path
    surface later as an opaque FFmpeg failure at render time.
    """
    ensure_ffmpeg()
    if not os.path.isfile(path):
        raise MediaNotFoundError("No such media file: %r" % (path,))

    stat = os.stat(path)
    try:
        raw = _probe_raw(os.path.abspath(path), stat.st_mtime, stat.st_size)
    except ffmpeg.Error as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip() if exc.stderr else ""
        raise UnsupportedMediaError(
            "FFmpeg could not read %r. It may be corrupt or not a media file.\n%s"
            % (path, detail)
        )

    streams = raw.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = _to_float(raw.get("format", {}).get("duration"))
    if duration is None:
        for stream in (video, audio):
            if stream is not None:
                duration = _to_float(stream.get("duration"))
                if duration is not None:
                    break

    return MediaInfo(
        path=path,
        duration=duration,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        fps=_parse_rate(video.get("r_frame_rate")) if video else None,
        has_video=video is not None,
        has_audio=audio is not None,
    )
