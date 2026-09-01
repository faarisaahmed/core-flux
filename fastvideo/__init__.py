"""Deprecated import alias for :mod:`core_flux`.

The project is published as ``core-flux``; ``fastvideo`` was the original
module name and is kept so code written against 0.3.x keeps working. It
forwards everything to :mod:`core_flux` and will be removed in 1.0.
"""

import sys
import warnings

import core_flux

warnings.warn(
    "Importing 'fastvideo' is deprecated and will be removed in core-flux 1.0. "
    "Use 'import core_flux' instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from core_flux.__all__ rather than a star import, so the two
# namespaces cannot drift apart as the public API changes.
__all__ = list(core_flux.__all__)
for _name in __all__:
    globals()[_name] = getattr(core_flux, _name)
del _name

# Keep `from fastvideo.engine import ...` working for existing code.
sys.modules[__name__ + ".engine"] = core_flux.engine
sys.modules[__name__ + ".errors"] = core_flux.errors
sys.modules[__name__ + ".probe"] = core_flux.probe
