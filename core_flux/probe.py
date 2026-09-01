"""Media inspection and FFmpeg capability detection.

``ffprobe`` costs a subprocess launch, so results are cached per
(path, mtime, size). Editing a file on disk invalidates its entry naturally.
"""

import functools
import os
import shutil
import subprocess

import json

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
    """Run ffprobe and return its parsed JSON. Cached per (path, mtime, size)."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams",
         "-of", "json", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise UnsupportedMediaError(
            "FFmpeg could not read %r. It may be corrupt or not a media file.\n%s"
            % (path, result.stderr.decode("utf-8", "replace").strip())
        )
    return json.loads(result.stdout.decode("utf-8", "replace"))


def inspect_media(path):
    """Probe `path` and return a :class:`MediaInfo`.

    Raises :class:`MediaNotFoundError` up front rather than letting a bad path
    surface later as an opaque FFmpeg failure at render time.
    """
    ensure_ffmpeg()
    if not os.path.isfile(path):
        raise MediaNotFoundError("No such media file: %r" % (path,))

    stat = os.stat(path)
    raw = _probe_raw(os.path.abspath(path), stat.st_mtime, stat.st_size)

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


@functools.lru_cache(maxsize=1)
def available_encoders():
    """Return the set of encoder names this FFmpeg build supports."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()

    names = set()
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split()
        # Rows look like: " V....D h264_videotoolbox  VideoToolbox H.264 Encoder"
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return frozenset(names)


# Hardware H.264 encoders, best-supported first.
_HARDWARE_H264 = (
    "h264_videotoolbox",  # Apple Silicon / macOS
    "h264_nvenc",         # NVIDIA
    "h264_qsv",           # Intel Quick Sync
    "h264_amf",           # AMD
)


def best_h264_encoder(hardware=True):
    """Pick an H.264 encoder, preferring hardware when one is available."""
    if hardware:
        encoders = available_encoders()
        for name in _HARDWARE_H264:
            if name in encoders:
                return name
    return "libx264"


def measure_volume(audio_stream):
    """Decode an audio stream and return its mean/max volume in dBFS."""
    from .graph import Graph

    input_args, filter_complex, maps = Graph().build([audio_stream])
    command = ["ffmpeg", "-hide_banner", "-nostdin"] + input_args
    if filter_complex:
        command += ["-filter_complex", filter_complex]
    label = maps[0]
    command += ["-map", label if ":" in label else "[%s]" % label]
    command += ["-af", "volumedetect", "-f", "null", "-"]

    result = subprocess.run(command, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    readings = {}
    for line in result.stderr.decode("utf-8", "replace").splitlines():
        for key in ("mean_volume", "max_volume"):
            if key + ":" in line:
                readings[key] = float(line.split(key + ":")[1].split("dB")[0])
    if not readings:
        raise UnsupportedMediaError("Could not measure the volume of this track.")
    return readings
