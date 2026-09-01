"""core-flux: a fast, layer-based video editing library built on FFmpeg filtergraphs."""

from .engine import (
    TRANSITIONS,
    AudioLayer,
    ColorLayer,
    Composition,
    ImageLayer,
    ImageSequenceLayer,
    TextLayer,
    VideoLayer,
    clips_array,
    concatenate,
    concatenate_audio,
    crossfade,
)
from .frames import FrameWriter, numpy_available
from .errors import (
    CoreFluxError,
    FFmpegNotFoundError,
    FilterUnavailableError,
    MediaNotFoundError,
    RenderError,
    UnsupportedMediaError,
)
from .probe import inspect_media

__version__ = "0.6.0"

__all__ = [
    "VideoLayer",
    "AudioLayer",
    "ImageLayer",
    "ImageSequenceLayer",
    "ColorLayer",
    "TextLayer",
    "Composition",
    "concatenate",
    "concatenate_audio",
    "crossfade",
    "clips_array",
    "FrameWriter",
    "numpy_available",
    "TRANSITIONS",
    "inspect_media",
    "CoreFluxError",
    "FFmpegNotFoundError",
    "MediaNotFoundError",
    "UnsupportedMediaError",
    "FilterUnavailableError",
    "RenderError",
    "__version__",
]
