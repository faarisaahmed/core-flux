"""End-to-end and graph-level tests for the core-flux engine.

Anything asserting a *duration* renders for real; anything asserting graph
*shape* inspects get_command(), which is fast and needs no encoding.
"""

import json
import subprocess

import pytest

from core_flux import (
    AudioLayer,
    ColorLayer,
    Composition,
    CoreFluxError,
    FilterUnavailableError,
    ImageLayer,
    MediaNotFoundError,
    RenderError,
    UnsupportedMediaError,
    VideoLayer,
    concatenate,
    inspect_media,
)
from core_flux.engine import format_from_path
from core_flux.probe import has_filter
from core_flux.text import pillow_available


def duration_of(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        stdout=subprocess.PIPE, check=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def mean_volume(path):
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
        stderr=subprocess.PIPE, check=True,
    )
    for line in result.stderr.decode().splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0])
    raise AssertionError("volumedetect produced no reading for %s" % path)


def render(composition, path, **kwargs):
    kwargs.setdefault("quiet", True)
    return composition.render(path, **kwargs)


# --------------------------------------------------------------------- probing

def test_inspect_media_reads_video_facts(media):
    info = inspect_media(media["bg"])
    assert info.has_video and info.has_audio
    assert (info.width, info.height) == (1920, 1080)
    assert info.duration == pytest.approx(6.0, abs=0.1)
    assert info.fps == pytest.approx(30.0)


def test_inspect_media_detects_missing_audio(media):
    assert inspect_media(media["silent"]).has_audio is False


@pytest.mark.parametrize("path,expected", [
    ("a.mp4", "video"), ("a.MOV", "video"), ("a.gif", "gif"),
    ("a.mp3", "audio"), ("a.WAV", "audio"), ("a.m4a", "audio"),
])
def test_format_inferred_from_extension(path, expected):
    assert format_from_path(path) == expected


# ------------------------------------------------------------ input validation

def test_missing_file_fails_at_construction_not_render(media):
    with pytest.raises(MediaNotFoundError):
        VideoLayer("definitely-not-here.mp4")


def test_corrupt_file_reports_clearly(media):
    with pytest.raises(UnsupportedMediaError):
        VideoLayer(media["junk"])


def test_audio_file_rejected_as_video_layer(media):
    with pytest.raises(UnsupportedMediaError):
        VideoLayer(media["music"])


def test_silent_file_rejected_as_audio_layer(media):
    with pytest.raises(UnsupportedMediaError):
        AudioLayer(media["silent"])


@pytest.mark.parametrize("call", [
    lambda layer: layer.trim(5, 2),
    lambda layer: layer.set_start(-1),
    lambda layer: layer.speed(0),
    lambda layer: layer.flip("sideways"),
    lambda layer: layer.flip("diagonal"),
    lambda layer: layer.set_opacity(1.5),
])
def test_invalid_arguments_raise_value_error(media, call):
    with pytest.raises(ValueError):
        call(VideoLayer(media["bg"]))


def test_render_without_layers_is_rejected():
    with pytest.raises(CoreFluxError):
        Composition().render("nope.mp4")


def test_audio_render_without_audio_is_rejected(media, out):
    with pytest.raises(CoreFluxError):
        render(Composition(layers=[VideoLayer(media["silent"])]), out("x.mp3"))


def test_render_error_carries_command_and_stderr(media):
    with pytest.raises(RenderError) as excinfo:
        render(Composition(layers=[VideoLayer(media["bg"])]), "/no/such/dir/out.mp4")
    error = excinfo.value
    assert "ffmpeg" in error.command
    assert "No such file or directory" in error.stderr


# ------------------------------------------------------------ duration tracking

def test_layer_duration_follows_edits(media):
    layer = VideoLayer(media["bg"])
    assert layer.duration == pytest.approx(6.0, abs=0.1)
    assert layer.trim(1, 5).duration == pytest.approx(4.0)
    assert layer.speed(2.0).duration == pytest.approx(2.0)


def test_subclip_defaults_to_end_of_clip(media):
    assert VideoLayer(media["bg"]).subclip(2).duration == pytest.approx(4.0, abs=0.1)


def test_set_start_shifts_the_end_marker(media):
    layer = VideoLayer(media["cam"]).set_start(2)
    assert layer.end == pytest.approx(5.0, abs=0.1)


def test_arbitrary_rotation_is_supported(media, out):
    """Angles other than 90/180/270 use the general rotate filter."""
    command = Composition(
        layers=[VideoLayer(media["bg"]).resize(320, 180).rotate(45)]
    ).get_command(out("r.mp4"))
    assert "rotate=" in command and "transpose" not in command


def test_resize_and_rotate_update_reported_size(media):
    layer = VideoLayer(media["bg"]).resize(640, 360)
    assert (layer.width, layer.height) == (640, 360)
    layer.rotate(90)
    assert (layer.width, layer.height) == (360, 640)


# ----------------------------------------------------- output duration contract

def test_longer_music_is_cut_to_the_video_length(media, out):
    """A 10s track over a 6s video must not stretch the render to 10s."""
    path = render(Composition(
        layers=[VideoLayer(media["bg"]).resize(320, 180)],
        audio_tracks=[AudioLayer(media["music"])],
    ), out("long.mp4"))
    assert duration_of(path) == pytest.approx(6.0, abs=0.25)


def test_shorter_music_is_padded_to_the_video_length(media, out):
    path = render(Composition(
        layers=[VideoLayer(media["bg"]).resize(320, 180)],
        audio_tracks=[AudioLayer(media["short"])],
    ), out("short.mp4"))
    assert duration_of(path) == pytest.approx(6.0, abs=0.25)


def test_short_overlay_does_not_extend_or_freeze(media, out):
    composition = Composition(layers=[
        VideoLayer(media["bg"]).resize(320, 180),
        VideoLayer(media["cam"]).resize(160, 120),
    ])
    assert "eof_action=pass" in composition.get_command(out("o.mp4"))
    assert duration_of(render(composition, out("o.mp4"))) == pytest.approx(6.0, abs=0.25)


def test_explicit_duration_overrides(media, out):
    path = render(Composition(layers=[VideoLayer(media["bg"]).resize(320, 180)]),
                  out("cut.mp4"), duration=2)
    assert duration_of(path) == pytest.approx(2.0, abs=0.25)


# --------------------------------------------------------------- audio mixing

def test_mixing_preserves_each_track_volume(media, out):
    """amix must not divide every track by the number of inputs."""
    solo = render(Composition(audio_tracks=[AudioLayer(media["music"])]), out("solo.wav"))
    mixed = render(Composition(audio_tracks=[
        AudioLayer(media["music"]),
        AudioLayer(media["music"]).mute(),
    ]), out("mixed.wav"))
    assert mean_volume(mixed) == pytest.approx(mean_volume(solo), abs=0.5)


def test_volume_scaling_is_applied(media, out):
    loud = render(Composition(audio_tracks=[AudioLayer(media["music"])]), out("loud.wav"))
    quiet = render(Composition(
        audio_tracks=[AudioLayer(media["music"]).with_volume_scaled_to(0.5)]
    ), out("quiet.wav"))
    # Halving amplitude is about -6 dB.
    assert mean_volume(quiet) == pytest.approx(mean_volume(loud) - 6.0, abs=1.0)


def test_muting_a_video_layer_drops_its_audio(media, out):
    composition = Composition(layers=[VideoLayer(media["bg"]).resize(320, 180).mute()])
    assert "amix" not in composition.get_command(out("m.mp4"))
    assert VideoLayer(media["bg"]).mute().has_audio is False


def test_audio_only_render(media, out):
    path = render(Composition(audio_tracks=[AudioLayer(media["music"])]), out("a.mp3"))
    assert duration_of(path) == pytest.approx(10.0, abs=0.25)


# ---------------------------------------------------------------- GIF export

def test_gif_export_with_a_filter_applied(media, out):
    """Regression: reusing a filtered node for palettegen needs an explicit split."""
    path = render(Composition(layers=[VideoLayer(media["cam"]).resize(160, 120)]),
                  out("a.gif"))
    assert duration_of(path) > 0


def test_gif_export_with_multiple_layers(media, out):
    path = render(Composition(layers=[
        VideoLayer(media["bg"]).resize(320, 180),
        VideoLayer(media["cam"]).resize(80, 60),
    ]), out("b.gif"))
    assert duration_of(path) > 0


def test_gif_graph_splits_before_palettegen(media, out):
    command = Composition(
        layers=[VideoLayer(media["cam"]).resize(160, 120)]
    ).get_command(out("c.gif"))
    assert "split" in command and "palettegen" in command and "paletteuse" in command


# ------------------------------------------------------------------- timeline

def test_concatenate_sums_durations(media, out):
    joined = concatenate([VideoLayer(media["bg"]), VideoLayer(media["cam"])],
                         width=320, height=180)
    assert joined.duration == pytest.approx(9.0, abs=0.2)
    assert duration_of(render(Composition(layers=[joined]), out("cat.mp4"))) \
        == pytest.approx(9.0, abs=0.3)


def test_concatenate_pads_silent_clips_with_silence(media, out):
    joined = concatenate([VideoLayer(media["bg"]), VideoLayer(media["silent"])],
                         width=320, height=180)
    path = render(Composition(layers=[joined]), out("cat2.mp4"))
    assert duration_of(path) == pytest.approx(10.0, abs=0.3)
    assert joined.has_audio


def test_concatenate_single_layer_is_a_passthrough(media):
    layer = VideoLayer(media["bg"])
    assert concatenate([layer]) is layer


def test_concatenate_requires_layers():
    with pytest.raises(ValueError):
        concatenate([])


def test_set_start_delays_video_and_audio(media, out):
    command = Composition(layers=[
        VideoLayer(media["bg"]).resize(320, 180),
        VideoLayer(media["cam"]).resize(160, 120).set_start(2),
    ]).get_command(out("s.mp4"))
    assert "tpad" in command          # picture is held back
    # ffmpeg-python escapes commas inside filter expressions: gte(t\,2)
    assert "enable=gte(t" in command  # and not drawn before its cue
    assert "adelay" in command        # sound is shifted to match


# ------------------------------------------------------------- generated layers

def test_color_layer_as_canvas(media, out):
    path = render(Composition(layers=[
        ColorLayer(320, 180, 3, color="navy"),
        VideoLayer(media["cam"]).resize(160, 120).set_position(10, 10),
    ]), out("col.mp4"))
    assert duration_of(path) == pytest.approx(3.0, abs=0.25)


def test_image_layer_holds_for_its_duration(media, out):
    path = render(Composition(layers=[ImageLayer(media["image"], duration=3)]),
                  out("img.mp4"))
    assert duration_of(path) == pytest.approx(3.0, abs=0.25)


# --------------------------------------------------------------------- effects

def test_speed_change_shortens_output(media, out):
    path = render(Composition(layers=[VideoLayer(media["bg"]).resize(320, 180).speed(2.0)]),
                  out("fast.mp4"))
    assert duration_of(path) == pytest.approx(3.0, abs=0.3)


def test_extreme_slowdown_chains_atempo(media, out):
    command = Composition(
        layers=[VideoLayer(media["bg"]).resize(160, 90).speed(0.2)]
    ).get_command(out("slow.mp4"))
    assert command.count("atempo") >= 2


def test_fade_out_defaults_to_the_end_of_the_clip(media, out):
    command = Composition(
        layers=[VideoLayer(media["bg"]).resize(320, 180).fade_out(duration=2)]
    ).get_command(out("f.mp4"))
    assert "start_time=4" in command  # 6s clip, 2s fade


def test_legacy_start_fade_argument_still_works(media, out):
    with pytest.deprecated_call():
        VideoLayer(media["bg"]).fade_out(start_fade=3.0)


def test_audio_layer_has_fade_in(media, out):
    command = Composition(
        audio_tracks=[AudioLayer(media["music"]).fade_in().fade_out()]
    ).get_command(out("af.mp3"))
    assert "afade" in command


@pytest.mark.skipif(
    not (has_filter("drawtext") or pillow_available()),
    reason="neither drawtext nor Pillow is available",
)
def test_add_text_renders(media, out):
    """Works via drawtext, or via the Pillow overlay fallback."""
    path = render(Composition(
        layers=[VideoLayer(media["bg"]).resize(320, 180).add_text("hi", size=24)]
    ), out("text.mp4"))
    assert duration_of(path) > 0


@pytest.mark.skipif(has_filter("drawtext"), reason="this build has drawtext")
@pytest.mark.skipif(not pillow_available(), reason="Pillow not installed")
def test_add_text_uses_pillow_fallback(media, out):
    """Without drawtext the caption becomes a PNG overlay, not an error."""
    command = Composition(
        layers=[VideoLayer(media["bg"]).resize(320, 180).add_text("hi")]
    ).get_command(out("t.mp4"))
    assert "drawtext" not in command
    assert "overlay" in command and ".png" in command


def test_add_text_without_drawtext_or_pillow_explains_both(media, monkeypatch):
    import core_flux.engine as engine

    monkeypatch.setattr(engine, "has_filter", lambda name: False)
    monkeypatch.setattr(engine, "pillow_available", lambda: False)
    with pytest.raises(FilterUnavailableError) as excinfo:
        VideoLayer(media["bg"]).add_text("hello")
    message = str(excinfo.value)
    assert "core-flux[text]" in message and "drawtext" in message


def test_pillow_fallback_needs_a_known_size(media, monkeypatch):
    import core_flux.engine as engine

    monkeypatch.setattr(engine, "has_filter", lambda name: False)
    layer = VideoLayer(media["bg"])
    layer._width = None
    with pytest.raises(CoreFluxError):
        layer.add_text("hello")


# ---------------------------------------------------------------- composition

def test_encoder_options_override_defaults(media, out):
    command = Composition(
        layers=[VideoLayer(media["bg"]).resize(320, 180)]
    ).get_command(out("q.mp4"), crf=30, preset="ultrafast")
    assert "-crf 30" in command and "-preset ultrafast" in command


def test_mp4_output_gets_faststart(media, out):
    command = Composition(
        layers=[VideoLayer(media["bg"]).resize(320, 180)]
    ).get_command(out("w.mp4"))
    assert "+faststart" in command


def test_add_layer_and_add_audio_chain(media, out):
    composition = (Composition()
                   .add_layer(VideoLayer(media["bg"]).resize(320, 180))
                   .add_audio(AudioLayer(media["music"])))
    assert len(composition.layers) == 1 and len(composition.audio_tracks) == 1
    assert composition.duration == pytest.approx(6.0, abs=0.1)


def test_render_returns_the_output_path(media, out):
    target = out("ret.mp4")
    assert render(Composition(layers=[VideoLayer(media["bg"]).resize(160, 90)]), target) == target


@pytest.mark.parametrize("name", ["mix.wav", "mix.mp3", "mix.m4a", "mix.flac"])
def test_audio_containers_get_a_playable_codec(media, out, name):
    """Forcing one codec for every audio container produced unplayable files."""
    path = render(Composition(audio_tracks=[AudioLayer(media["short"])]), out(name))
    assert mean_volume(path) < 0  # decodes, so volumedetect can read it


def test_non_h264_container_is_left_to_ffmpeg(media, out):
    command = Composition(
        layers=[VideoLayer(media["bg"]).resize(160, 90)]
    ).get_command(out("v.webm"))
    assert "libx264" not in command


def test_overwrite_false_refuses_to_clobber(media, out):
    target = out("keep.mp4")
    composition = Composition(layers=[VideoLayer(media["bg"]).resize(160, 90)])
    render(composition, target)
    before = open(target, "rb").read()
    with pytest.raises(CoreFluxError):
        render(composition, target, overwrite=False)
    assert open(target, "rb").read() == before  # untouched
