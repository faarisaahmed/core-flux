"""Unit tests for the filtergraph builder that replaced ffmpeg-python."""

import pytest

from core_flux.graph import Graph, InputFile, escape_value, multi_filter


def build(outputs):
    return Graph().build(outputs)


def test_simple_chain():
    node = InputFile("a.mp4")
    inputs, fc, maps = build([node.video().filter("scale", 320, 180)])
    assert inputs == ["-i", "a.mp4"]
    assert fc == "[0:v]scale=320:180[s0]"
    assert maps == ["s0"]


def test_input_options_precede_the_file():
    node = InputFile("img.png", {"loop": 1, "t": 5})
    inputs, _, _ = build([node.video().filter("null")])
    assert inputs[-2:] == ["-i", "img.png"]
    assert "-loop" in inputs and "-t" in inputs


def test_fan_out_inserts_a_split():
    """ffmpeg-python raised here; the split should be automatic."""
    node = InputFile("a.mp4")
    stream = node.video().filter("scale", 320, 180)
    joined = multi_filter([stream, stream.filter("hflip")], "overlay")[0]
    _, fc, _ = build([joined])
    assert "split=2" in fc


def test_audio_fan_out_uses_asplit():
    node = InputFile("a.mp3")
    stream = node.audio().filter("volume", volume=1)
    joined = multi_filter(
        [stream, stream], "amix", output_kinds=("a",), kwargs={"inputs": 2}
    )[0]
    _, fc, _ = build([joined])
    assert "asplit=2" in fc


def test_three_way_fan_out():
    node = InputFile("a.mp4")
    stream = node.video().filter("scale", 320, 180)
    joined = multi_filter([stream, stream, stream], "hstack",
                          kwargs={"inputs": 3})[0]
    _, fc, _ = build([joined])
    assert "split=3" in fc


def test_identical_inputs_are_decoded_once():
    """Two layers on the same file should produce one -i, not two."""
    first = InputFile("a.mp4").video().filter("scale", 320, 180)
    second = InputFile("a.mp4").video().filter("scale", 160, 90)
    inputs, fc, _ = build([multi_filter([first, second], "overlay")[0]])
    assert inputs.count("-i") == 1
    assert "split=2" in fc


def test_inputs_with_different_options_stay_separate():
    first = InputFile("a.mp4").video()
    second = InputFile("a.mp4", {"stream_loop": 2}).video()
    inputs, _, _ = build([multi_filter([first, second], "overlay")[0]])
    assert inputs.count("-i") == 2


def test_multiple_outputs_are_mapped_in_order():
    node = InputFile("a.mp4")
    video = node.video().filter("scale", 320, 180)
    audio = node.audio().filter("volume", volume=0.5)
    _, _, maps = build([video, audio])
    assert len(maps) == 2


def test_input_streams_map_without_brackets():
    node = InputFile("a.mp4")
    _, fc, maps = build([node.video()])
    assert fc == ""
    assert maps == ["0:v"]


@pytest.mark.parametrize("raw,expected", [
    ("gte(t,2)", "gte(t\\,2)"),
    ("a:b", "a\\:b"),
    ("say 'hi'", "say \\'hi\\'"),
    ("x;y", "x\\;y"),
    ("[tag]", "\\[tag\\]"),
    ("plain", "plain"),
])
def test_escaping(raw, expected):
    assert escape_value(raw) == expected


def test_kwargs_are_sorted_for_stable_output():
    node = InputFile("a.mp4")
    stream = node.video().filter("overlay", y=2, x=1, enable="gte(t,1)")
    _, fc, _ = build([stream])
    assert "enable=gte(t\\,1):x=1:y=2" in fc


def test_none_valued_kwargs_are_dropped():
    node = InputFile("a.mp4")
    stream = node.video().filter("scale", w=320, h=None)
    _, fc, _ = build([stream])
    assert "h=" not in fc and "w=320" in fc


def test_positional_and_keyword_args_combine():
    node = InputFile("a.mp4")
    stream = node.video().filter("pad", "iw+10", "ih+10", 5, 5, color="red")
    _, fc, _ = build([stream])
    assert fc == "[0:v]pad=iw+10:ih+10:5:5:color=red[s0]"
