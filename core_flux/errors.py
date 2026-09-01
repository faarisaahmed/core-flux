"""Exception types raised by core-flux.

Everything raised on purpose by this library inherits from :class:`CoreFluxError`,
so ``except CoreFluxError`` is enough to catch any of it.
"""


class CoreFluxError(Exception):
    """Base class for every error raised by core-flux."""


class FFmpegNotFoundError(CoreFluxError):
    """FFmpeg (or ffprobe) is not installed or not on PATH."""


class MediaNotFoundError(CoreFluxError):
    """The requested input file does not exist or cannot be read."""


class UnsupportedMediaError(CoreFluxError):
    """The file exists but does not contain the stream the layer needs."""


class FilterUnavailableError(CoreFluxError):
    """This FFmpeg build was compiled without a filter the operation needs."""


class RenderError(CoreFluxError):
    """FFmpeg exited non-zero while rendering.

    The FFmpeg command line and the tail of its stderr are attached so the
    real cause is visible without re-running anything by hand.
    """

    def __init__(self, message, command=None, stderr=None):
        self.command = command
        self.stderr = stderr
        detail = [message]
        if stderr:
            detail.append("\n--- FFmpeg said ---\n" + stderr.strip())
        if command:
            detail.append("\n--- Command ---\n" + command)
        super(RenderError, self).__init__("".join(detail))
