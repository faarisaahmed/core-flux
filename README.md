# core-flux

A fast, layer-based video editing library for Python, built on native FFmpeg filtergraphs.

Every edit you chain — resize, crop, fade, overlay, mix — appends a node to a filter graph. Nothing executes until you call `render()`, which compiles the whole timeline into **a single FFmpeg process**. Frames never cross into Python, so a composite runs at FFmpeg's own speed instead of Python's.

```python
from core_flux import VideoLayer, AudioLayer, Composition

Composition(
    layers=[
        VideoLayer("gameplay.mp4").resize(1920, 1080).fade_out(),
        VideoLayer("facecam.mp4").resize(400, 300).set_position(50, 50).mute(),
    ],
    audio_tracks=[AudioLayer("lofi.mp3").with_volume_scaled_to(0.2)],
).render("edit.mp4")
```

> **Renamed in 0.4.0:** the import name is now `core_flux`, matching the package name. `import fastvideo` still works but emits a `DeprecationWarning`, and will be removed in 1.0.

---

## Installation

```bash
pip install core-flux
```

FFmpeg must be installed separately and available on your `PATH`:

| Platform | Command |
|----------|---------|
| macOS | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |
| Windows | `winget install ffmpeg` |

**Requirements:** Python 3.8+, FFmpeg 4.0+.

---

## Core concepts

**Layers stack, first one is the canvas.** `layers[0]` is the background; everything after is composited on top of it in order, at the position you give it.

**Output length follows the base video layer.** A music bed longer than your video is cut off at the end of the picture; a shorter one is padded with silence. Overlays never extend the render, and a short overlay disappears when it ends rather than freezing on its last frame.

**Track volumes are absolute.** `with_volume_scaled_to(0.2)` means 20% of the original, regardless of how many other tracks are in the mix.

**Nothing runs until `render()`.** Layers are cheap to build and rearrange. Use `get_command()` to see the FFmpeg invocation without running it.

---

## Quick start

### Composite a facecam over gameplay with background music

```python
from core_flux import VideoLayer, AudioLayer, Composition

background = VideoLayer("gameplay.mp4").resize(1920, 1080).fade_out(duration=1.5)

facecam = (VideoLayer("facecam.mp4")
           .resize(400, 300)
           .set_position(x=50, y=50)
           .mute())

music = AudioLayer("lofi.mp3").with_volume_scaled_to(0.2).fade_out()

Composition(layers=[background, facecam], audio_tracks=[music]).render("edit.mp4")
```

### Join clips end to end

```python
from core_flux import VideoLayer, Composition, concatenate

reel = concatenate([
    VideoLayer("intro.mp4"),
    VideoLayer("main.mp4").trim(5, 45),
    VideoLayer("outro.mp4"),
], width=1920, height=1080)

Composition(layers=[reel.fade_in().fade_out()]).render("reel.mp4")
```

### Time an overlay to appear partway through

```python
watermark = (VideoLayer("logo_anim.mp4")
             .resize(200, 200)
             .set_position(x=1650, y=50)
             .set_start(8.0)       # appears 8 seconds in
             .set_opacity(0.6))

Composition(layers=[VideoLayer("talk.mp4"), watermark]).render("talk.mp4")
```

### Export a GIF

```python
Composition(layers=[VideoLayer("clip.mp4").trim(0, 4)]).render(
    "clip.gif", gif_fps=15, gif_width=480
)
```

---

## API reference

### `VideoLayer(input_path)`

A video clip. The file is probed on construction, so a missing or unreadable path fails immediately rather than deep inside FFmpeg later. Every method returns `self`, so calls chain.

**Read-only properties:** `.duration`, `.width`, `.height`, `.fps`, `.has_audio`, `.end` (`start_time + duration`). `.duration`, `.width` and `.height` track the edits you apply — after `.trim(0, 5).speed(2.0)`, `.duration` is `2.5`.

#### Geometry

| Method | Description |
|--------|-------------|
| `.set_position(x, y)` | Place the layer's top-left corner on the canvas. |
| `.resize(width, height)` | Scale to an exact pixel size. |
| `.scale_by(factor)` | Scale by a multiplier, rounded to even dimensions for H.264. |
| `.crop(x1, y1, width, height)` | Keep a rectangle starting at top-left `(x1, y1)`. |
| `.rotate(degrees)` | Rotate clockwise by 90, 180 or 270. |
| `.flip(axis)` | Mirror across `"horizontal"` or `"vertical"`. |

#### Appearance

| Method | Description |
|--------|-------------|
| `.adjust_colors(contrast=1.0, brightness=0.0, saturation=1.0)` | Tune the picture. |
| `.blackwhite()` | Remove all colour. |
| `.set_opacity(alpha)` | Make the layer semi-transparent when overlaid (`0.0`–`1.0`). |
| `.add_text(text, x=10, y=10, size=48, color="white", font=None, box=False, box_color="black@0.5", start=None, end=None)` | Burn in text. Requires an FFmpeg built with libfreetype. |

#### Time

| Method | Description |
|--------|-------------|
| `.trim(start, end)` | Keep the section between two timestamps, in seconds. |
| `.subclip(start=0, end=None)` | Same as `trim`, but `end` defaults to the end of the clip. |
| `.speed(factor)` | `2.0` is twice as fast, `0.5` half speed. Audio pitch is preserved. |
| `.set_start(seconds)` | Delay the layer so it begins partway into the timeline. |
| `.fade_in(start_time=0.0, duration=1.0)` | Fade picture and native audio up together. |
| `.fade_out(start_time=None, duration=1.0)` | Fade both down. With no `start_time`, lands on the clip's final `duration` seconds. |

#### Audio

| Method | Description |
|--------|-------------|
| `.with_volume_scaled_to(factor)` | Scale the embedded audio. `0.5` halves, `2.0` doubles. |
| `.mute()` | Drop the embedded audio entirely. |

---

### `AudioLayer(input_path)`

A standalone audio track. Supports `.with_volume_scaled_to(factor)`, `.mute()`, `.trim(start, end)`, `.subclip(start, end)`, `.speed(factor)`, `.set_start(seconds)`, `.fade_in(start_time=0.0, duration=1.0)` and `.fade_out(start_time=None, duration=1.0)`, plus the `.duration` and `.end` properties.

```python
AudioLayer("podcast.wav").trim(30, 90).fade_in().fade_out()
```

---

### `ImageLayer(input_path, duration=5.0, fps=30)`

A still image held on screen — title cards, logos, watermarks. Accepts every `VideoLayer` method.

```python
ImageLayer("logo.png", duration=3).set_position(20, 20).set_opacity(0.7)
```

### `ColorLayer(width, height, duration, color="black", fps=30)`

A solid colour canvas to composite onto, when you don't want a video as the background.

```python
Composition(layers=[
    ColorLayer(1920, 1080, duration=10, color="#101014"),
    VideoLayer("clip.mp4").resize(1280, 720).set_position(320, 180),
]).render("framed.mp4")
```

---

### `concatenate(layers, width=None, height=None, fps=None, audio=True)`

Join clips end to end. FFmpeg's concat requires matching resolution, pixel aspect and frame rate, so each segment is normalised first; segments with no audio contribute matching silence to keep sound aligned across the joins.

Returns an ordinary `VideoLayer`, so the result can be trimmed, faded and overlaid like any other clip. `width`/`height`/`fps` default to the first clip's.

---

### `Composition(layers=None, audio_tracks=None)`

The timeline.

| Member | Description |
|--------|-------------|
| `.add_layer(layer)` | Stack another visual layer on top. Chainable. |
| `.add_audio(track)` | Add another track to the mix. Chainable. |
| `.duration` | Expected output length. |
| `.get_command(output_path, **kwargs)` | The FFmpeg command that *would* run, as a string. |
| `.render(output_path, ...)` | Compile and write the file. Returns `output_path`. |

#### `render(output_path, format_type=None, quiet=False, verbose=False, overwrite=True, duration=None, **encoder_options)`

| Argument | Description |
|----------|-------------|
| `format_type` | `'video'`, `'gif'` or `'audio'`. Inferred from the file extension when omitted. |
| `quiet` | Suppress core-flux's own progress lines. |
| `verbose` | Stream FFmpeg's raw output to the console instead of capturing it. |
| `overwrite` | Overwrite an existing file (default `True`). |
| `duration` | Hard-cap the output length in seconds. |
| `gif_fps`, `gif_width` | GIF only. Default to `15` and the source width. |
| `**encoder_options` | Passed to FFmpeg as output options, overriding the defaults — `crf=18`, `preset="slow"`, `r=60`. |

Defaults for MP4-family containers are H.264 + AAC, `yuv420p`, with `+faststart` for web playback. Other containers are left to FFmpeg's own codec defaults, so `.webm` correctly gets VP9/Opus.

```python
timeline.render("out.mp4", crf=18, preset="slow")
timeline.render("out.gif", gif_fps=20, gif_width=600)
timeline.render("mix.wav")
print(timeline.get_command("out.mp4"))
```

---

### Errors

Everything raised on purpose inherits from `CoreFluxError`, so one `except` clause covers the library:

| Exception | Raised when |
|-----------|-------------|
| `FFmpegNotFoundError` | `ffmpeg`/`ffprobe` are not on `PATH`. |
| `MediaNotFoundError` | An input file does not exist. |
| `UnsupportedMediaError` | The file is unreadable, or lacks the stream the layer needs. |
| `FilterUnavailableError` | Your FFmpeg build lacks a required filter (e.g. `drawtext`). |
| `RenderError` | FFmpeg exited non-zero. Carries `.command` and `.stderr`. |

```python
from core_flux import CoreFluxError, RenderError

try:
    timeline.render("out.mp4")
except RenderError as e:
    print(e.stderr)   # what FFmpeg actually complained about
    print(e.command)  # the exact command, to re-run by hand
except CoreFluxError as e:
    print(e)
```

---

## Performance

Two workloads, three engines, on an Apple M1 (8-core, 16 GB, native arm64, Python 3.10). Three runs each after a warmup. The input is a 15-second 1080p clip at a realistic ~8 Mbps.

```text
Workload: transcode (1080p -> 720p, H.264/AAC)
  Engine       | Avg        | Min        | Max        | Std dev
  ---------------------------------------------------------------
  ffmpeg       |     4.24s  |     4.24s  |     4.25s  |   0.00s
  core-flux    |     4.35s  |     4.32s  |     4.40s  |   0.04s
  moviepy      |    16.31s  |    16.18s  |    16.56s  |   0.21s
  -> core-flux is 3.75x the speed of MoviePy

Workload: composite (overlay + audio mix + fade)
  Engine       | Avg        | Min        | Max        | Std dev
  ---------------------------------------------------------------
  ffmpeg       |     4.36s  |     4.29s  |     4.46s  |   0.09s
  core-flux    |     4.40s  |     4.36s  |     4.46s  |   0.05s
  moviepy      |    28.38s  |    28.19s  |    28.53s  |   0.17s
  -> core-flux is 6.45x the speed of MoviePy
```

**Reading these honestly:**

- core-flux tracks raw FFmpeg within ~2%, which is the point — it adds a graph builder and one `ffprobe` call, not a processing layer.
- The gap over MoviePy widens on the composite workload (3.75x to 6.45x) because MoviePy composites frames in Python/NumPy, while core-flux hands the overlay to FFmpeg. The more layers you add, the wider it gets.
- **These ratios depend heavily on your source.** Encoding is the floor for every engine; the harder your footage is to encode, the more that floor dominates and the smaller the ratio. On easily-compressed footage the same benchmark shows over 12x, which flatters core-flux. Treat 3–6x as the realistic range for ordinary video.

Reproduce it yourself — the script generates its own inputs, or drop in your own `benchmark/sample.mp4`:

```bash
pip install moviepy
python benchmark/benchmark.py
```

---

## Upgrading from 0.3.x

0.4.0 changes several behaviours that were producing wrong output. Existing code keeps working, but the *results* change:

| Change | Before | Now |
|--------|--------|-----|
| Output duration | The longest audio track set the length, so a 10s music bed over a 6s video produced a 10s file. | The base video layer sets the length. Long tracks are cut, short ones padded with silence. |
| Track volume | `amix` divided every track by the number of inputs, so `with_volume_scaled_to(0.2)` became 0.1 once a second track existed. | Volumes are absolute, whatever the track count. |
| Short overlays | Froze on their last frame for the rest of the render. | Disappear when they end. |
| GIF export | Crashed with a `split` filter error on any composition with a filter or a second layer. | Works. |
| Audio containers | Every non-MP3 audio output was forced to AAC, producing unplayable `.wav` files. | The container's correct default codec is used. |
| Missing files | Surfaced later as an opaque FFmpeg error. | `MediaNotFoundError`, at construction. |
| `mute()` | Scaled volume to zero, leaving a silent stream in the mix. | Drops the stream. |
| Import name | `import fastvideo` | `import core_flux`. The old name still works with a `DeprecationWarning`. |

`fade_out(start_fade=...)` still works but is deprecated; use `start_time=`, or omit it to fade the final seconds.

---

## Limitations

Worth knowing before you pick this over MoviePy:

- **No per-frame Python access.** Everything is an FFmpeg filter, which is exactly why it's fast — but you cannot write a custom NumPy effect over raw frames. MoviePy can. If you need that, use MoviePy.
- **`add_text()` needs libfreetype.** Many FFmpeg builds omit it. Check with `ffmpeg -filters | grep drawtext`; you'll get a clear `FilterUnavailableError` if it's missing.
- **No clip looping**, and no crossfade transition between concatenated clips (each cut is hard).
- **Built on [`ffmpeg-python`](https://github.com/kkroening/ffmpeg-python)**, which has not seen a release since 2019. It works, but it is not actively maintained.

---

## Development

```bash
git clone https://github.com/Faaris/core-flux
cd core-flux
pip install -e ".[dev]"
pytest
```

The test suite generates its own media with FFmpeg's `lavfi` sources, so no fixture files are needed. Tests that assert durations or volumes render real files; tests that assert graph shape inspect `get_command()`.

---

## License

MIT
