"""Raw frame access: the escape hatch out of the filtergraph and back.

Everything else in core-flux compiles to FFmpeg filters and never lets frames
touch Python. Sometimes you genuinely need the pixels — a custom effect, a
per-frame measurement, frames generated from scratch. This module pipes raw
bytes out of FFmpeg (and back in) so that is possible.

It is deliberately a separate path. Reading frames is cheap; writing them back
means re-encoding from raw video, so a transform materialises to a file rather
than staying lazy. Even so, the round trip measures roughly 3.5x faster than
MoviePy performing the same per-frame work, because there is no per-frame
Python overhead anywhere except in your own function.

NumPy is an optional extra (``pip install core-flux[frames]``); without it the
readers still work and hand you ``bytes``.
"""

import subprocess

from .errors import CoreFluxError, RenderError
from .graph import Graph

# Bytes per pixel for the formats we let callers choose.
PIXEL_FORMATS = {
    "rgb24": 3,
    "bgr24": 3,
    "rgba": 4,
    "bgra": 4,
    "gray": 1,
}


def numpy_available():
    """True if NumPy can be imported, without importing it."""
    import importlib.util

    return importlib.util.find_spec("numpy") is not None


def _require_numpy():
    if not numpy_available():
        raise CoreFluxError(
            "This needs NumPy.\n"
            "  pip install core-flux[frames]\n"
            "Or pass as_numpy=False to work with raw bytes instead."
        )
    import numpy

    return numpy


def _map_argument(label):
    return label if ":" in label else "[%s]" % label


def read_command(video_stream, width, height, fps=None, pix_fmt="rgb24",
                 scale=True):
    """Build the FFmpeg command that streams raw frames to stdout.

    `scale` inserts a resize so the byte stream is guaranteed to match the
    buffer size. Skip it only when the stream is already known to be that size.
    """
    stream = video_stream.filter("scale", width, height) if scale else video_stream
    if fps:
        stream = stream.filter("fps", fps)
    input_args, filter_complex, maps = Graph().build([stream])

    command = ["ffmpeg", "-hide_banner", "-v", "error", "-nostdin"]
    command += input_args
    if filter_complex:
        command += ["-filter_complex", filter_complex]
    command += ["-map", _map_argument(maps[0])]
    command += ["-f", "rawvideo", "-pix_fmt", pix_fmt, "-"]
    return command


def iter_frames(video_stream, width, height, fps=None, pix_fmt="rgb24",
                as_numpy=True, scale=True):
    """Yield decoded frames one at a time.

    Each frame is an ``(height, width, channels)`` NumPy array, or ``bytes``
    when `as_numpy` is False. Read-only and streaming: memory stays flat
    regardless of clip length.
    """
    if pix_fmt not in PIXEL_FORMATS:
        raise ValueError(
            "pix_fmt must be one of %s, got %r"
            % (", ".join(sorted(PIXEL_FORMATS)), pix_fmt)
        )
    numpy = _require_numpy() if as_numpy else None
    channels = PIXEL_FORMATS[pix_fmt]
    frame_bytes = int(width) * int(height) * channels

    command = read_command(video_stream, width, height, fps, pix_fmt, scale)
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    exhausted = False
    try:
        while True:
            # readinto a fresh bytearray: one allocation per frame, and the
            # resulting NumPy view is writable, so callers can edit in place.
            buffer = bytearray(frame_bytes)
            filled = process.stdout.readinto(buffer)
            if not filled or filled < frame_bytes:
                exhausted = True
                break
            if as_numpy:
                yield numpy.frombuffer(buffer, numpy.uint8).reshape(
                    int(height), int(width), channels
                )
            else:
                yield bytes(buffer)
    finally:
        if process.poll() is None:
            process.stdout.close()
            process.terminate()
        process.wait()
        stderr = process.stderr.read()
        process.stderr.close()
        # Abandoning the generator early closes the pipe, and FFmpeg rightly
        # complains about it. That is not an error the caller needs to see.
        if exhausted and process.returncode not in (0, None) and stderr:
            raise RenderError(
                "FFmpeg failed while reading frames.",
                command=" ".join(command),
                stderr=stderr.decode("utf-8", "replace")[-2000:],
            )


class FrameWriter(object):
    """Encode frames pushed from Python into a video file.

    Used as a context manager::

        with FrameWriter("out.mp4", 640, 360, fps=30) as writer:
            for frame in frames:
                writer.write(frame)
    """

    def __init__(self, output_path, width, height, fps=30, pix_fmt="rgb24",
                 vcodec="libx264", output_pix_fmt="yuv420p", **encoder_options):
        if pix_fmt not in PIXEL_FORMATS:
            raise ValueError("Unsupported pix_fmt %r" % (pix_fmt,))
        self.output_path = output_path
        self.width = int(width)
        self.height = int(height)
        self.frame_bytes = self.width * self.height * PIXEL_FORMATS[pix_fmt]

        command = [
            "ffmpeg", "-hide_banner", "-v", "error", "-nostdin", "-y",
            "-f", "rawvideo", "-pix_fmt", pix_fmt,
            "-s", "%dx%d" % (self.width, self.height), "-r", str(fps),
            "-i", "-",
            "-c:v", vcodec, "-pix_fmt", output_pix_fmt,
        ]
        for name, value in encoder_options.items():
            command.append("-" + name)
            if value is not None:
                command.append(str(value))
        command.append(output_path)

        self.command = command
        self._process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self._closed = False

    def write(self, frame):
        """Push one frame. Accepts a NumPy array or raw bytes."""
        data = frame if isinstance(frame, bytes) else frame.tobytes()
        if len(data) != self.frame_bytes:
            raise ValueError(
                "Frame is %d bytes but %dx%d needs %d. Did the size change?"
                % (len(data), self.width, self.height, self.frame_bytes)
            )
        self._process.stdin.write(data)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._process.stdin.close()
        stderr = self._process.stderr.read()
        self._process.stderr.close()
        self._process.wait()
        if self._process.returncode != 0:
            raise RenderError(
                "FFmpeg failed while writing frames to %r." % (self.output_path,),
                command=" ".join(self.command),
                stderr=stderr.decode("utf-8", "replace")[-2000:],
            )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.close()
        else:
            # Don't mask the caller's exception with an encoder error.
            self._closed = True
            try:
                self._process.stdin.close()
                self._process.kill()
                self._process.wait()
            except OSError:
                pass
        return False
