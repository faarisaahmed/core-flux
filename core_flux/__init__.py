"""core-flux: a fast, layer-based video editing library built on FFmpeg filtergraphs."""

from .engine import (
    AudioLayer,
    ColorLayer,
    Composition,
    ImageLayer,
    VideoLayer,
    concatenate,
)
from .errors import (
    CoreFluxError,
    FFmpegNotFoundError,
    FilterUnavailableError,
    MediaNotFoundError,
    RenderError,
    UnsupportedMediaError,
)
from .probe import inspect_media

__version__ = "0.4.0"

__all__ = [
    "VideoLayer",
    "AudioLayer",
    "ImageLayer",
    "ColorLayer",
    "Composition",
    "concatenate",
    "inspect_media",
    "CoreFluxError",
    "FFmpegNotFoundError",
    "MediaNotFoundError",
    "UnsupportedMediaError",
    "FilterUnavailableError",
    "RenderError",
    "__version__",
]
