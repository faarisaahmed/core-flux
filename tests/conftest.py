import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="FFmpeg is not installed"
)


def _run(args):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + args, check=True
    )


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    """Synthesise the fixture clips once per session, so tests need no assets."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg is not installed")

    root = tmp_path_factory.mktemp("media")
    paths = {
        "bg": str(root / "bg.mp4"),          # 6s, 1920x1080, with audio
        "cam": str(root / "cam.mp4"),        # 3s, 640x480, with audio
        "silent": str(root / "silent.mp4"),  # 4s, 640x480, no audio
        "music": str(root / "music.mp3"),    # 10s audio only
        "short": str(root / "short.mp3"),    # 2s audio only
        "image": str(root / "logo.png"),     # 320x240 still
        "junk": str(root / "junk.mp4"),      # not a media file
    }

    _run(["-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=6",
          "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", paths["bg"]])
    _run(["-f", "lavfi", "-i", "testsrc2=size=640x480:rate=30:duration=3",
          "-f", "lavfi", "-i", "sine=frequency=880:duration=3",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", paths["cam"]])
    _run(["-f", "lavfi", "-i", "testsrc=size=640x480:rate=30:duration=4",
          "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", paths["silent"]])
    _run(["-f", "lavfi", "-i", "sine=frequency=220:duration=10", paths["music"]])
    _run(["-f", "lavfi", "-i", "sine=frequency=330:duration=2", paths["short"]])
    _run(["-f", "lavfi", "-i", "color=c=red:s=320x240", "-frames:v", "1", paths["image"]])

    with open(paths["junk"], "wb") as handle:
        handle.write(b"definitely not a video" * 40)

    return paths


@pytest.fixture
def out(tmp_path):
    """Return a helper that builds output paths inside a per-test temp dir."""
    return lambda name: str(tmp_path / name)
