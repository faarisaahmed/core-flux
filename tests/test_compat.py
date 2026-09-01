"""The deprecated `fastvideo` import name must keep working for 0.3.x code."""

import importlib
import sys
import warnings

import pytest

import core_flux


def _fresh_fastvideo():
    """Import the shim with a clean slate so the warning always fires."""
    for name in [n for n in sys.modules if n == "fastvideo" or n.startswith("fastvideo.")]:
        del sys.modules[name]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        module = importlib.import_module("fastvideo")
    return module, caught


def test_fastvideo_import_warns():
    _, caught = _fresh_fastvideo()
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_fastvideo_reexports_the_same_objects():
    module, _ = _fresh_fastvideo()
    assert module.__all__ == core_flux.__all__
    for name in core_flux.__all__:
        assert getattr(module, name) is getattr(core_flux, name), name


def test_fastvideo_submodules_resolve():
    _fresh_fastvideo()
    from fastvideo.engine import format_from_path
    from fastvideo.errors import RenderError
    from fastvideo.probe import has_filter

    assert format_from_path is core_flux.engine.format_from_path
    assert RenderError is core_flux.errors.RenderError
    assert has_filter is core_flux.probe.has_filter


def test_versions_match():
    module, _ = _fresh_fastvideo()
    assert module.__version__ == core_flux.__version__


def test_old_style_code_still_renders(media, tmp_path):
    _fresh_fastvideo()
    from fastvideo import AudioLayer, Composition, VideoLayer

    target = str(tmp_path / "legacy.mp4")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        base = VideoLayer(media["bg"]).resize(320, 180).fade_out(start_fade=4.0)
    Composition(
        layers=[base, VideoLayer(media["cam"]).resize(80, 60).set_position(x=5, y=5).mute()],
        audio_tracks=[AudioLayer(media["music"]).with_volume_scaled_to(0.2)],
    ).render(target, quiet=True)
    assert __import__("os").path.exists(target)


@pytest.mark.parametrize("version_source", ["core_flux", "pyproject"])
def test_declared_version_is_consistent(version_source):
    if version_source == "core_flux":
        assert core_flux.__version__ == core_flux.engine._version()
    else:
        import os
        import re

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = open(os.path.join(root, "pyproject.toml")).read()
        declared = re.search(r'^version = "([^"]+)"', text, re.M).group(1)
        assert declared == core_flux.__version__
