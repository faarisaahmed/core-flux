"""Coverage for the MoviePy-parity feature set added in 0.6."""

import json
import os
import subprocess

import pytest

from core_flux import (
    AudioLayer,
    Composition,
    CoreFluxError,
    FrameWriter,
    ImageSequenceLayer,
    TextLayer,
    VideoLayer,
    clips_array,
    concatenate_audio,
    numpy_available,
)

pytestmark = pytest.mark.skipif(
    not numpy_available(), reason="NumPy is not installed"
)


def duration_of(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path], stdout=subprocess.PIPE, check=True)
    return float(json.loads(result.stdout)["format"]["duration"])


def stream_kinds(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "json", path], stdout=subprocess.PIPE, check=True)
    return [s["codec_type"] for s in json.loads(result.stdout)["streams"]]


def render(composition, path, **kwargs):
    kwargs.setdefault("quiet", True)
    return composition.render(path, **kwargs)


def base(media, w=320, h=180):
    return VideoLayer(media["bg"]).resize(w, h)


# ------------------------------------------------------------- time editing

def test_cut_out_removes_a_middle_section(media, out):
    layer = base(media).cut_out(2, 4)
    assert layer.duration == pytest.approx(4.0, abs=0.1)
    assert duration_of(render(Composition(layers=[layer]), out("c.mp4"))) \
        == pytest.approx(4.0, abs=0.3)


def test_cut_out_rejects_backwards_range(media):
    with pytest.raises(ValueError):
        base(media).cut_out(4, 2)


def test_time_symmetrize_doubles(media, out):
    layer = base(media).time_symmetrize()
    assert layer.duration == pytest.approx(12.0, abs=0.1)
    assert duration_of(render(Composition(layers=[layer]), out("s.mp4"))) \
        == pytest.approx(12.0, abs=0.4)


def test_freeze_extends_by_the_pause(media, out):
    layer = base(media).freeze(2, 2)
    assert layer.duration == pytest.approx(8.0, abs=0.1)
    assert duration_of(render(Composition(layers=[layer]), out("f.mp4"))) \
        == pytest.approx(8.0, abs=0.4)


def test_freeze_outside_the_clip_is_rejected(media):
    with pytest.raises(ValueError):
        base(media).freeze(99, 1)


def test_set_duration_trims_and_pads(media, out):
    short = base(media).set_duration(3)
    assert duration_of(render(Composition(layers=[short]), out("d1.mp4"))) \
        == pytest.approx(3.0, abs=0.3)
    long = base(media).set_duration(9)
    assert duration_of(render(Composition(layers=[long]), out("d2.mp4"))) \
        == pytest.approx(9.0, abs=0.4)


def test_accel_decel_retimes_and_drops_audio(media, out):
    layer = base(media).accel_decel(4.0)
    assert layer.duration == pytest.approx(4.0)
    assert layer.has_audio is False
    assert duration_of(render(Composition(layers=[layer]), out("a.mp4"))) \
        == pytest.approx(4.0, abs=0.3)


def test_accel_decel_strength_is_bounded(media):
    with pytest.raises(ValueError):
        base(media).accel_decel(4.0, strength=2.0)


def test_make_loopable_keeps_duration(media, out):
    layer = base(media).make_loopable(1.0)
    path = render(Composition(layers=[layer]), out("l.mp4"))
    assert duration_of(path) == pytest.approx(6.0, abs=0.3)


def test_make_loopable_rejects_overlong_overlap(media):
    with pytest.raises(ValueError):
        base(media).make_loopable(99)


def test_set_fps_resamples(media, out):
    assert "fps=15" in Composition(
        layers=[base(media).set_fps(15)]
    ).get_command(out("f.mp4"))


# --------------------------------------------------------------- appearance

@pytest.mark.parametrize("apply", [
    lambda layer: layer.even_size(),
    lambda layer: layer.multiply_color(1.3),
    lambda layer: layer.supersample(3),
    lambda layer: layer.scroll(horizontal=0.01),
    lambda layer: layer.rotate(45),
])
def test_appearance_effects_render(media, out, apply):
    assert duration_of(render(
        Composition(layers=[apply(base(media))]), out("fx.mp4")
    )) > 0


def test_even_size_rounds_up(media):
    layer = VideoLayer(media["bg"]).resize(321, 181).even_size()
    assert (layer.width, layer.height) == (322, 182)


def test_freeze_region_renders(media, out):
    assert duration_of(render(
        Composition(layers=[base(media).freeze_region(2, 10, 10, 100, 80)]),
        out("fr.mp4"))) == pytest.approx(6.0, abs=0.3)


def test_blink_sets_an_enable_window(media, out):
    command = Composition(layers=[
        base(media), VideoLayer(media["cam"]).resize(80, 60).blink(0.3, 0.3)
    ]).get_command(out("b.mp4"))
    assert "mod(t" in command and "enable=" in command


def test_blink_combines_with_start_offset(media, out):
    """Both gates must apply, not just the last one set."""
    command = Composition(layers=[
        base(media),
        VideoLayer(media["cam"]).resize(80, 60).blink(0.3, 0.3).set_start(2),
    ]).get_command(out("b.mp4"))
    assert "gte(t" in command and "mod(t" in command


@pytest.mark.parametrize("side", ["left", "right", "top", "bottom"])
def test_slide_in_renders(media, out, side):
    moving = VideoLayer(media["cam"]).resize(80, 60).set_position(20, 20) \
        .slide_in(1.0, side)
    assert duration_of(render(
        Composition(layers=[base(media), moving]), out("sl.mp4"))) > 0


def test_slide_rejects_unknown_side(media):
    with pytest.raises(ValueError):
        VideoLayer(media["cam"]).slide_in(1.0, "diagonally")


def test_masks_render(media, out):
    mask = VideoLayer(media["silent"]).resize(320, 180).to_mask()
    masked = VideoLayer(media["cam"]).resize(320, 180).with_mask(mask)
    assert duration_of(render(
        Composition(layers=[base(media), masked]), out("m.mp4"))) > 0


def test_mask_and_or_render(media, out):
    a = VideoLayer(media["cam"]).resize(160, 90).to_mask()
    b = VideoLayer(media["silent"]).resize(160, 90).to_mask()
    for name, combined in (("and", a.copy().mask_and(b)),
                           ("or", a.copy().mask_or(b))):
        assert duration_of(render(
            Composition(layers=[combined]), out("mask_%s.mp4" % name))) > 0


# ------------------------------------------------------------- layer types

def test_text_layer_over_video(media, out):
    caption = TextLayer("Hello", 320, 180, duration=3, size=32)
    assert caption.duration == 3
    assert duration_of(render(
        Composition(layers=[base(media), caption]), out("t.mp4"))) > 0


def test_image_sequence_layer(media, out, tmp_path):
    folder = tmp_path / "frames"
    Composition(layers=[base(media, 160, 90)]).save_frames(
        str(folder / "%03d.png"), fps=2
    )
    layer = ImageSequenceLayer(str(folder / "%03d.png"), fps=2)
    assert layer.duration == pytest.approx(len(os.listdir(str(folder))) / 2.0)
    assert duration_of(render(Composition(layers=[layer]), out("is.mp4"))) > 0


def test_image_sequence_glob(media, out, tmp_path):
    folder = tmp_path / "g"
    Composition(layers=[base(media, 160, 90)]).save_frames(
        str(folder / "%03d.png"), fps=2
    )
    layer = ImageSequenceLayer(str(folder / "*.png"), fps=2)
    assert duration_of(render(Composition(layers=[layer]), out("ig.mp4"))) > 0


def test_image_sequence_missing_pattern(tmp_path):
    from core_flux import MediaNotFoundError

    with pytest.raises(MediaNotFoundError):
        ImageSequenceLayer(str(tmp_path / "nothing" / "*.png"))


def test_to_image_layer_freezes_a_frame(media, out):
    still = base(media).to_image_layer(t=2, duration=3)
    assert still.duration == 3
    assert duration_of(render(Composition(layers=[still]), out("i.mp4"))) \
        == pytest.approx(3.0, abs=0.3)


# ------------------------------------------------------------- composition

def test_clips_array_builds_a_grid(media, out):
    grid = clips_array([
        [VideoLayer(media["bg"]), VideoLayer(media["cam"])],
        [VideoLayer(media["cam"]), VideoLayer(media["silent"])],
    ], width=160, height=90)
    assert (grid.width, grid.height) == (320, 180)
    assert duration_of(render(Composition(layers=[grid]), out("g.mp4"))) > 0


def test_clips_array_single_row(media, out):
    grid = clips_array([[VideoLayer(media["bg"]), VideoLayer(media["cam"])]],
                       width=160, height=90)
    assert (grid.width, grid.height) == (320, 90)


def test_clips_array_needs_rows():
    with pytest.raises(ValueError):
        clips_array([])


def test_set_audio_replaces_the_soundtrack(media, out):
    layer = base(media).set_audio(AudioLayer(media["music"]))
    assert "audio" in stream_kinds(render(Composition(layers=[layer]), out("sa.mp4")))


def test_aspect_ratio_and_frame_count(media):
    layer = base(media)
    assert layer.aspect_ratio == pytest.approx(320 / 180.0)
    assert layer.n_frames == 180


def test_copy_isolates_edits(media):
    original = base(media)
    clone = original.copy().blur(4)
    assert clone.video_stream is not original.video_stream


# ------------------------------------------------------------------- audio

def test_audio_delay_extends_slightly(media, out):
    path = render(Composition(
        audio_tracks=[AudioLayer(media["short"]).delay(0.3)]), out("ad.wav"))
    assert duration_of(path) > 2.0


def test_stereo_volume(media, out):
    assert duration_of(render(Composition(
        audio_tracks=[AudioLayer(media["short"]).with_stereo_volume(1.0, 0.2)]
    ), out("sv.wav"))) > 0


def test_audio_set_duration(media, out):
    track = AudioLayer(media["music"]).set_duration(4)
    assert track.duration == 4
    assert duration_of(render(Composition(audio_tracks=[track]), out("asd.wav"))) \
        == pytest.approx(4.0, abs=0.3)


def test_concatenate_audio_sums(media, out):
    joined = concatenate_audio([
        AudioLayer(media["music"]).trim(0, 2),
        AudioLayer(media["music"]).trim(0, 3),
    ])
    assert joined.duration == pytest.approx(5.0)
    assert duration_of(render(Composition(audio_tracks=[joined]), out("ca.wav"))) \
        == pytest.approx(5.0, abs=0.3)


def test_concatenate_audio_single_is_passthrough(media):
    track = AudioLayer(media["music"])
    assert concatenate_audio([track]) is track


def test_max_volume_reads_a_level(media):
    assert AudioLayer(media["music"]).max_volume() < 0


# ------------------------------------------------------------- raw frames

def test_iter_frames_yields_writable_arrays(media):
    layer = base(media, 64, 36)
    frames = list(layer.iter_frames())
    assert len(frames) == 180
    assert frames[0].shape == (36, 64, 3)
    assert frames[0].flags.writeable, "callers must be able to edit in place"


def test_iter_frames_as_bytes(media):
    frame = next(iter(base(media, 64, 36).iter_frames(as_numpy=False)))
    assert isinstance(frame, bytes) and len(frame) == 64 * 36 * 3


def test_iter_frames_can_be_abandoned_early(media):
    """Closing the pipe early must not surface as a RenderError."""
    stream = base(media, 64, 36).iter_frames()
    assert next(stream) is not None
    stream.close()


def test_iter_frames_needs_a_size(media):
    layer = VideoLayer(media["bg"])
    layer._width = None
    with pytest.raises(CoreFluxError):
        list(layer.iter_frames())


def test_iter_frames_rejects_unknown_pixel_format(media):
    with pytest.raises(ValueError):
        list(base(media, 64, 36).iter_frames(pix_fmt="yuv420p"))


def test_get_frame_at_a_time(media):
    frame = base(media, 64, 36).get_frame(t=2.0)
    assert frame.shape == (36, 64, 3)


def test_apply_frame_function_transforms_pixels(media, out):
    def invert_red(frame):
        frame[:, :, 0] = 255 - frame[:, :, 0]
        return frame

    source = base(media, 64, 36)
    before = source.copy().get_frame(t=1.0)[:, :, 0].mean()
    transformed = source.apply_frame_function(invert_red)
    after = transformed.get_frame(t=1.0)[:, :, 0].mean()
    assert abs(after - before) > 3, "the transform should change the picture"


def test_apply_frame_function_returns_a_usable_layer(media, out):
    transformed = base(media, 64, 36).apply_frame_function(lambda f: f)
    path = render(Composition(layers=[transformed.fade_out(duration=0.5)]),
                  out("t.mp4"))
    assert duration_of(path) == pytest.approx(6.0, abs=0.4)


def test_apply_frame_function_keeps_audio(media, out):
    transformed = base(media, 64, 36).apply_frame_function(lambda f: f)
    assert transformed.has_audio
    assert "audio" in stream_kinds(
        render(Composition(layers=[transformed]), out("ta.mp4")))


def test_frame_writer_generates_video(media, tmp_path):
    import numpy

    target = str(tmp_path / "gen.mp4")
    with FrameWriter(target, 64, 36, fps=30) as writer:
        for index in range(60):
            frame = numpy.zeros((36, 64, 3), numpy.uint8)
            frame[:, :, 1] = index * 4
            writer.write(frame)
    assert duration_of(target) == pytest.approx(2.0, abs=0.2)


def test_frame_writer_rejects_wrong_frame_size(tmp_path):
    import numpy

    with pytest.raises(ValueError):
        with FrameWriter(str(tmp_path / "x.mp4"), 64, 36) as writer:
            writer.write(numpy.zeros((10, 10, 3), numpy.uint8))


def test_composition_iter_frames(media):
    composition = Composition(layers=[
        base(media, 64, 36), VideoLayer(media["cam"]).resize(20, 15)
    ])
    assert sum(1 for _ in composition.iter_frames()) == 180


def test_save_frames_writes_images(media, tmp_path):
    pattern = str(tmp_path / "seq" / "%03d.png")
    Composition(layers=[base(media, 64, 36)]).save_frames(pattern, fps=1)
    assert len(os.listdir(str(tmp_path / "seq"))) >= 6


def test_save_frames_requires_a_pattern(media, out):
    with pytest.raises(ValueError):
        Composition(layers=[base(media)]).save_frames(out("nope.png"))


def test_frames_without_numpy_explains(media, monkeypatch):
    import core_flux.frames as frames

    monkeypatch.setattr(frames, "numpy_available", lambda: False)
    with pytest.raises(CoreFluxError) as excinfo:
        list(base(media, 64, 36).iter_frames())
    assert "core-flux[frames]" in str(excinfo.value)
