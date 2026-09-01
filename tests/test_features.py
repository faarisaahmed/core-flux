"""Coverage for the 0.5 feature set: transitions, effects, and speed options."""

import json
import subprocess

import pytest

from core_flux import (
    TRANSITIONS,
    AudioLayer,
    ColorLayer,
    Composition,
    CoreFluxError,
    VideoLayer,
    concatenate,
    crossfade,
)
from core_flux.probe import available_encoders, best_h264_encoder


def duration_of(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        stdout=subprocess.PIPE, check=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def size_of(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", path],
        stdout=subprocess.PIPE, check=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    return stream["width"], stream["height"]


def render(composition, path, **kwargs):
    kwargs.setdefault("quiet", True)
    return composition.render(path, **kwargs)


def base(media, w=320, h=180):
    return VideoLayer(media["bg"]).resize(w, h)


# ------------------------------------------------------------- transitions

def test_crossfade_duration_accounts_for_the_overlap(media, out):
    """6s + 3s with a 1s crossfade is 8s, not 9s."""
    joined = crossfade([VideoLayer(media["bg"]), VideoLayer(media["cam"])],
                       duration=1.0, width=320, height=180)
    assert joined.duration == pytest.approx(8.0, abs=0.1)
    assert duration_of(render(Composition(layers=[joined]), out("x.mp4"))) \
        == pytest.approx(8.0, abs=0.3)


def test_crossfade_chains_three_clips(media, out):
    joined = crossfade(
        [VideoLayer(media["bg"]), VideoLayer(media["cam"]), VideoLayer(media["silent"])],
        duration=0.5, width=320, height=180,
    )
    # 6 + 3 + 4 - (2 x 0.5)
    assert joined.duration == pytest.approx(12.0, abs=0.1)
    assert duration_of(render(Composition(layers=[joined]), out("x3.mp4"))) \
        == pytest.approx(12.0, abs=0.3)


@pytest.mark.parametrize("style", ["fade", "wipeleft", "circleopen", "dissolve"])
def test_crossfade_transition_styles(media, out, style):
    joined = crossfade([VideoLayer(media["bg"]), VideoLayer(media["cam"])],
                       duration=0.5, transition=style, width=160, height=90)
    assert duration_of(render(Composition(layers=[joined]), out("t.mp4"))) > 0


def test_crossfade_rejects_unknown_transition(media):
    with pytest.raises(ValueError) as excinfo:
        crossfade([VideoLayer(media["bg"]), VideoLayer(media["cam"])],
                  transition="teleport", width=320, height=180)
    assert "teleport" in str(excinfo.value)


def test_crossfade_rejects_clip_shorter_than_the_transition(media):
    with pytest.raises(ValueError) as excinfo:
        crossfade([VideoLayer(media["bg"]), VideoLayer(media["cam"])],
                  duration=5.0, width=320, height=180)
    assert "transition" in str(excinfo.value)


def test_crossfade_single_layer_is_a_passthrough(media):
    layer = VideoLayer(media["bg"])
    assert crossfade([layer]) is layer


def test_all_advertised_transitions_are_accepted_by_ffmpeg(media, out):
    """TRANSITIONS should not advertise a name this FFmpeg rejects."""
    supported = subprocess.run(
        ["ffmpeg", "-hide_banner", "-h", "filter=xfade"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    ).stdout.decode()
    missing = [name for name in TRANSITIONS
               if (" %s " % name) not in supported]
    assert not missing, "advertised but unsupported: %s" % missing


# ----------------------------------------------------------------- effects

@pytest.mark.parametrize("apply", [
    lambda layer: layer.blur(6),
    lambda layer: layer.sharpen(1.2),
    lambda layer: layer.invert(),
    lambda layer: layer.gamma(1.3),
    lambda layer: layer.vignette(),
    lambda layer: layer.reverse(),
    lambda layer: layer.chroma_key("green"),
])
def test_effects_render(media, out, apply):
    path = render(Composition(layers=[apply(base(media))]), out("fx.mp4"))
    assert duration_of(path) > 0


def test_margin_expands_the_frame(media, out):
    layer = base(media).margin(20, color="red")
    assert (layer.width, layer.height) == (360, 220)
    assert size_of(render(Composition(layers=[layer]), out("m.mp4"))) == (360, 220)


def test_margin_per_side(media):
    layer = base(media).margin(top=10, bottom=20, left=5, right=5)
    assert (layer.width, layer.height) == (330, 210)


def test_loop_multiplies_duration(media, out):
    layer = VideoLayer(media["bg"]).loop(3).resize(160, 90)
    assert layer.duration == pytest.approx(18.0, abs=0.1)
    assert duration_of(render(Composition(layers=[layer]), out("l.mp4"))) \
        == pytest.approx(18.0, abs=0.4)


def test_loop_uses_stream_loop_not_frame_buffering(media, out):
    """The loop filter would buffer every decoded frame in RAM."""
    command = Composition(
        layers=[VideoLayer(media["bg"]).loop(2).resize(160, 90)]
    ).get_command(out("l.mp4"))
    assert "-stream_loop 1" in command


def test_loop_rejects_zero(media):
    with pytest.raises(ValueError):
        VideoLayer(media["bg"]).loop(0)


def test_loop_rejects_generated_layers(media):
    joined = concatenate([VideoLayer(media["bg"]), VideoLayer(media["cam"])],
                         width=160, height=90)
    with pytest.raises(CoreFluxError):
        joined.loop(2)


def test_hold_last_frame_extends(media, out):
    layer = base(media).hold_last_frame(2)
    assert layer.duration == pytest.approx(8.0, abs=0.1)
    assert duration_of(render(Composition(layers=[layer]), out("h.mp4"))) \
        == pytest.approx(8.0, abs=0.3)


def test_audio_normalize_renders(media, out):
    path = render(Composition(
        audio_tracks=[AudioLayer(media["music"]).normalize()]
    ), out("n.wav"))
    assert duration_of(path) > 0


def test_audio_loop_multiplies_duration(media, out):
    track = AudioLayer(media["short"]).loop(3)
    assert track.duration == pytest.approx(6.0, abs=0.1)
    assert duration_of(render(Composition(audio_tracks=[track]), out("al.wav"))) \
        == pytest.approx(6.0, abs=0.3)


def test_audio_reverse_renders(media, out):
    path = render(Composition(
        audio_tracks=[AudioLayer(media["short"]).reverse()]
    ), out("ar.wav"))
    assert duration_of(path) > 0


def test_animated_position_accepts_an_expression(media, out):
    """set_position takes FFmpeg expressions, so overlays can move."""
    moving = VideoLayer(media["cam"]).resize(80, 60).set_position(x="20+t*30", y=20)
    path = render(Composition(layers=[base(media), moving]), out("anim.mp4"))
    assert duration_of(path) > 0


def test_subtitles_missing_file_is_reported(media):
    from core_flux import MediaNotFoundError

    with pytest.raises(MediaNotFoundError):
        base(media).add_subtitles("nope.srt")


# ------------------------------------------------------------ frames, speed

def test_save_frame_writes_an_image(media, tmp_path):
    target = str(tmp_path / "thumb.png")
    Composition(layers=[base(media)]).save_frame(target, t=2.0)
    import os

    assert os.path.getsize(target) > 0


def test_save_frame_does_not_map_audio(media, out):
    """Mapping audio into a PNG makes FFmpeg fail to pick an encoder."""
    command = Composition(
        layers=[base(media)], audio_tracks=[AudioLayer(media["music"])]
    )._compile(out("f.png"), format_type="frame")[0]
    assert command.count("-map") == 1


def test_hardware_flag_selects_an_available_encoder(media, out):
    command = Composition(layers=[base(media)]).get_command(
        out("hw.mp4"), hardware=True
    )
    assert best_h264_encoder(True) in command


def test_hardware_encoders_get_a_quality_setting(media, out):
    """They ignore -crf, so without this they default to a huge bitrate."""
    encoder = best_h264_encoder(True)
    if encoder == "libx264":
        pytest.skip("no hardware encoder on this machine")
    command = Composition(layers=[base(media)]).get_command(
        out("hw.mp4"), hardware=True
    )
    assert "-crf" not in command


def test_software_is_the_default(media, out):
    command = Composition(layers=[base(media)]).get_command(out("sw.mp4"))
    assert "libx264" in command


def test_hardware_render_actually_runs(media, out):
    if best_h264_encoder(True) == "libx264":
        pytest.skip("no hardware encoder on this machine")
    path = render(Composition(layers=[base(media)]), out("hw.mp4"), hardware=True)
    assert duration_of(path) == pytest.approx(6.0, abs=0.3)


def test_encoder_detection_finds_something():
    assert len(available_encoders()) > 10


# --------------------------------------------------------------- efficiency

def test_same_file_on_two_layers_decodes_once(media, out):
    """The graph should merge identical inputs into a single decode."""
    command = Composition(layers=[
        VideoLayer(media["bg"]).resize(320, 180),
        VideoLayer(media["bg"]).resize(160, 90).set_position(10, 10),
    ]).get_command(out("dedupe.mp4"))
    assert command.count("-i ") == 1
    assert "split" in command


def test_color_layer_needs_no_input_file(media, out):
    command = Composition(layers=[ColorLayer(320, 180, 2)]).get_command(out("c.mp4"))
    assert "lavfi" in command


# -------------------------------------------------------------- subtitles

SRT_SAMPLE = """1
00:00:00,500 --> 00:00:02,000
First caption here

2
00:00:02,500 --> 00:00:04,000
Second one, with a comma
"""


@pytest.fixture
def srt(tmp_path):
    path = tmp_path / "subs.srt"
    path.write_text(SRT_SAMPLE, encoding="utf-8")
    return str(path)


def test_parse_srt_reads_cues(srt):
    from core_flux.text import parse_srt

    assert parse_srt(srt) == [
        (0.5, 2.0, "First caption here"),
        (2.5, 4.0, "Second one, with a comma"),
    ]


def test_parse_srt_tolerates_missing_indices_and_bom(tmp_path):
    from core_flux.text import parse_srt

    path = tmp_path / "odd.srt"
    path.write_text(
        "﻿00:00:01,000 --> 00:00:02,000\nNo index\n\n\n"
        "2\n00:00:03,000 --> 00:00:04,500\nTwo\nlines\n",
        encoding="utf-8",
    )
    assert parse_srt(str(path)) == [(1.0, 2.0, "No index"), (3.0, 4.5, "Two\nlines")]


def test_parse_srt_skips_malformed_blocks(tmp_path):
    from core_flux.text import parse_srt

    path = tmp_path / "bad.srt"
    path.write_text("1\nnot a timing line\ntext\n\n"
                    "2\n00:00:01,000 --> 00:00:02,000\nGood\n", encoding="utf-8")
    assert parse_srt(str(path)) == [(1.0, 2.0, "Good")]


def test_subtitles_render(media, out, srt):
    path = render(Composition(layers=[
        VideoLayer(media["bg"]).resize(320, 180).add_subtitles(srt)
    ]), out("sub.mp4"))
    assert duration_of(path) == pytest.approx(6.0, abs=0.3)


def test_subtitle_cues_span_the_whole_timeline(media, out, srt):
    """A cue input that ends early leaves nothing for `enable` to draw."""
    from core_flux.probe import has_filter

    if has_filter("subtitles"):
        pytest.skip("this build uses the libass filter, not the fallback")
    command = Composition(layers=[
        VideoLayer(media["bg"]).resize(320, 180).add_subtitles(srt)
    ]).get_command(out("sub.mp4"))
    assert command.count("between(t") == 2
    assert command.count("-t 6.0") == 2


def test_subtitles_missing_file(media):
    from core_flux import MediaNotFoundError

    with pytest.raises(MediaNotFoundError):
        VideoLayer(media["bg"]).add_subtitles("nope.srt")


def test_subtitle_fallback_needs_known_size(media, srt, monkeypatch):
    import core_flux.engine as engine

    monkeypatch.setattr(engine, "has_filter", lambda name: False)
    layer = VideoLayer(media["bg"])
    layer._width = None
    with pytest.raises(CoreFluxError):
        layer.add_subtitles(srt)


def test_subtitle_fallback_rejects_huge_tracks(media, tmp_path, monkeypatch):
    import core_flux.engine as engine

    monkeypatch.setattr(engine, "has_filter", lambda name: False)
    path = tmp_path / "many.srt"
    blocks = ["%d\n00:00:%02d,000 --> 00:00:%02d,500\nCue %d\n"
              % (i + 1, i % 60, i % 60, i)
              for i in range(engine.MAX_FALLBACK_CUES + 5)]
    path.write_text("\n".join(blocks), encoding="utf-8")
    with pytest.raises(CoreFluxError) as excinfo:
        VideoLayer(media["bg"]).resize(320, 180).add_subtitles(str(path))
    assert "libass" in str(excinfo.value)


def test_subtitle_fallback_without_pillow_explains(media, srt, monkeypatch):
    import core_flux.engine as engine
    from core_flux import FilterUnavailableError

    monkeypatch.setattr(engine, "has_filter", lambda name: False)
    monkeypatch.setattr(engine, "pillow_available", lambda: False)
    with pytest.raises(FilterUnavailableError) as excinfo:
        VideoLayer(media["bg"]).resize(320, 180).add_subtitles(srt)
    assert "core-flux[text]" in str(excinfo.value)
