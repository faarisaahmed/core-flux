# core-flux

A fast, layer-based video editing library for Python, built on native FFmpeg filtergraphs. **No Python dependencies** — it talks to the `ffmpeg` binary directly.

Every edit you chain appends a node to a filter graph. Nothing executes until `render()`, which compiles the whole timeline into **a single FFmpeg process**. Frames never cross into Python, so a composite runs at FFmpeg's speed instead of Python's.

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

**4–7x faster than MoviePy**, or **12–20x with hardware encoding.** [Benchmarks below](#performance), reproducible on your own machine.

> **Renamed in 0.4.0:** the import is now `core_flux`, matching the package name. `import fastvideo` still works but emits a `DeprecationWarning`, and will be removed in 1.0.

---

## Installation

```bash
pip install core-flux
```

FFmpeg must be installed separately and on your `PATH`:

| Platform | Command |
|----------|---------|
| macOS | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |
| Windows | `winget install ffmpeg` |

**Requirements:** Python 3.8+, FFmpeg 4.0+. No Python packages required.

Optional: `pip install core-flux[text]` adds Pillow. It's used only as a fallback for `add_text()` and `add_subtitles()` when your FFmpeg was built without `drawtext` (libfreetype) or `subtitles` (libass) — which is the case for Homebrew's default build, among others. With it installed, text works everywhere.

---

## Core concepts

**Layers stack; the first is the canvas.** `layers[0]` is the background, everything after composites on top in order.

**Output length follows the base video layer.** Music longer than the video is cut off; shorter is padded with silence. Overlays never extend the render, and a short overlay disappears when it ends rather than freezing.

**Track volumes are absolute.** `with_volume_scaled_to(0.2)` means 20%, regardless of how many other tracks are mixed in.

**Nothing runs until `render()`.** Layers are cheap to build and rearrange. `get_command()` shows the FFmpeg invocation without running it.

---

## Quick start

### Composite with music

```python
from core_flux import VideoLayer, AudioLayer, Composition

Composition(
    layers=[
        VideoLayer("gameplay.mp4").resize(1920, 1080).fade_out(duration=1.5),
        VideoLayer("facecam.mp4").resize(400, 300).set_position(x=50, y=50).mute(),
    ],
    audio_tracks=[AudioLayer("lofi.mp3").with_volume_scaled_to(0.2).fade_out()],
).render("edit.mp4")
```

### Join clips with crossfades

```python
from core_flux import VideoLayer, Composition, crossfade

reel = crossfade([
    VideoLayer("intro.mp4"),
    VideoLayer("main.mp4").trim(5, 45),
    VideoLayer("outro.mp4"),
], duration=1.0, transition="fade")

Composition(layers=[reel]).render("reel.mp4")
```

### Titles over a colour card

```python
from core_flux import ColorLayer, Composition

Composition(layers=[
    ColorLayer(1920, 1080, duration=4, color="#101014")
        .add_text("Chapter One", x=120, y=460, size=96)
]).render("title.mp4")
```

### Green-screen a presenter over a background

```python
Composition(layers=[
    VideoLayer("background.mp4").resize(1920, 1080),
    VideoLayer("presenter.mp4").resize(960, 540)
        .chroma_key("green", similarity=0.3)
        .set_position(480, 270),
]).render("keyed.mp4")
```

### A watermark that slides in partway through

```python
watermark = (VideoLayer("logo.mp4")
             .resize(200, 200)
             .set_position(x="min(40, -200 + (t-8)*200)", y=40)   # expressions work
             .set_start(8.0)
             .set_opacity(0.6))

Composition(layers=[VideoLayer("talk.mp4"), watermark]).render("talk.mp4")
```

### Export a GIF, a thumbnail, an audio mix

```python
timeline.render("clip.gif", gif_fps=15, gif_width=480)
timeline.save_frame("thumb.jpg", t=12.5)
timeline.render("mix.wav")
```

---

## API reference

### `VideoLayer(input_path)`

A video clip. Probed on construction, so a bad path fails immediately rather than deep inside FFmpeg. Every method returns `self`, so calls chain.

**Read-only properties:** `.duration`, `.width`, `.height`, `.fps`, `.has_audio`, `.end`. Sizes and durations track your edits — after `.trim(0, 5).speed(2.0)`, `.duration` is `2.5`.

#### Geometry

| Method | Description |
|--------|-------------|
| `.set_position(x, y)` | Place the top-left corner. Accepts FFmpeg expressions, so `x="20+t*30"` animates. |
| `.resize(width, height)` | Scale to an exact size. |
| `.scale_by(factor)` | Scale by a multiplier, rounded to even dimensions. |
| `.crop(x1, y1, width, height)` | Keep a rectangle from the top-left corner. |
| `.rotate(degrees)` | Rotate clockwise by 90, 180 or 270. |
| `.flip(axis)` | Mirror `"horizontal"` or `"vertical"`. |
| `.margin(size, color, top, bottom, left, right)` | Add a border, expanding the frame. |

#### Appearance

| Method | Description |
|--------|-------------|
| `.adjust_colors(contrast, brightness, saturation)` | Tune the picture. |
| `.blackwhite()` | Remove colour. |
| `.invert()` | Photographic negative. |
| `.gamma(value)` | Gamma correction; below 1.0 darkens. |
| `.blur(radius)` | Gaussian blur. |
| `.sharpen(amount)` | Unsharp mask. |
| `.vignette()` | Darken the corners. |
| `.set_opacity(alpha)` | Semi-transparency for overlays, `0.0`–`1.0`. |
| `.chroma_key(color, similarity, blend)` | Knock out a background colour. |
| `.add_text(text, x, y, size, color, font, box, box_color, start, end)` | Burn in a caption. |
| `.add_subtitles(path, force_style, size, color, font, box)` | Burn in an `.srt` or `.ass` file. |

#### Time

| Method | Description |
|--------|-------------|
| `.trim(start, end)` | Keep a section, in seconds. |
| `.subclip(start, end=None)` | Same, but `end` defaults to the clip end. |
| `.speed(factor)` | `2.0` twice as fast; audio pitch preserved. |
| `.reverse()` | Play backwards, audio included. |
| `.loop(count)` | Repeat `count` times total, via `-stream_loop`. |
| `.hold_last_frame(duration)` | Freeze on the final frame for longer. |
| `.set_start(seconds)` | Delay the layer's entry on the timeline. |
| `.fade_in(start_time, duration)` | Fade picture and native audio up. |
| `.fade_out(start_time=None, duration)` | Fade both down; defaults to the final seconds. |

#### Audio

| Method | Description |
|--------|-------------|
| `.with_volume_scaled_to(factor)` | Scale embedded audio. |
| `.mute()` | Drop embedded audio entirely. |

---

### `AudioLayer(input_path)`

A standalone track. Supports `.with_volume_scaled_to()`, `.mute()`, `.normalize(target=-16.0)`, `.trim()`, `.subclip()`, `.speed()`, `.reverse()`, `.loop()`, `.set_start()`, `.fade_in()`, `.fade_out()`, plus `.duration` and `.end`.

```python
AudioLayer("podcast.wav").trim(30, 90).normalize().fade_in().fade_out()
```

`.normalize()` targets LUFS (EBU R128): `-16` suits podcasts and web video, `-14` matches music streaming.

---

### `ImageLayer(path, duration=5.0, fps=30)` · `ColorLayer(width, height, duration, color="black", fps=30)`

A still image held on screen, and a solid colour canvas. Both accept every `VideoLayer` method.

```python
ImageLayer("logo.png", duration=3).set_position(20, 20).set_opacity(0.7)
ColorLayer(1920, 1080, duration=10, color="#101014")
```

---

### `concatenate(layers, width=None, height=None, fps=None, audio=True)`

Join clips end to end with hard cuts. Segments are normalised to a common resolution, aspect and frame rate; silent segments contribute matching silence so audio stays aligned. Returns a `VideoLayer`.

### `crossfade(layers, duration=1.0, transition="fade", width=None, height=None, fps=None)`

Join clips with an overlap instead of a cut. Total runtime is `sum(durations) - duration * (len(layers) - 1)`. Audio is crossfaded whenever every clip has a soundtrack. Returns a `VideoLayer`.

44 transition styles are available in `core_flux.TRANSITIONS`, including `fade`, `fadeblack`, `dissolve`, `wipeleft`/`right`/`up`/`down`, `slideleft`/`right`/`up`/`down`, `circleopen`, `circlecrop`, `radial`, `pixelize`, `zoomin`.

```python
from core_flux import crossfade, TRANSITIONS

crossfade(clips, duration=0.75, transition="circleopen")
```

---

### `Composition(layers=None, audio_tracks=None)`

| Member | Description |
|--------|-------------|
| `.add_layer(layer)` / `.add_audio(track)` | Chainable. |
| `.duration` | Expected output length. |
| `.get_command(path, **kwargs)` | The FFmpeg command that *would* run, as a string. |
| `.save_frame(path, t=0.0)` | Write one frame as an image. |
| `.render(path, ...)` | Compile and write. Returns the path. |

#### `render(output_path, format_type=None, quiet=False, verbose=False, overwrite=True, duration=None, hardware=False, **encoder_options)`

| Argument | Description |
|----------|-------------|
| `format_type` | `'video'`, `'gif'`, `'audio'`. Inferred from the extension when omitted. |
| `hardware` | Use a hardware H.264 encoder when one exists. See [performance](#performance). |
| `quiet` / `verbose` | Suppress core-flux's output / stream FFmpeg's raw output. |
| `overwrite` | Refuses rather than clobbering when `False`. |
| `duration` | Hard-cap the output length. |
| `gif_fps`, `gif_width` | GIF only; default `15` and the source width. |
| `**encoder_options` | Passed to FFmpeg as output options: `crf=18`, `preset="slow"`, `r=60`. |

MP4-family containers default to H.264 + AAC, `yuv420p`, `+faststart`. Other containers use FFmpeg's own defaults, so `.webm` correctly gets VP9/Opus.

---

### Errors

Everything raised on purpose inherits from `CoreFluxError`:

| Exception | Raised when |
|-----------|-------------|
| `FFmpegNotFoundError` | `ffmpeg`/`ffprobe` are not on `PATH`. |
| `MediaNotFoundError` | An input file does not exist. |
| `UnsupportedMediaError` | Unreadable, or missing the stream the layer needs. |
| `FilterUnavailableError` | Your FFmpeg build lacks a required filter. |
| `RenderError` | FFmpeg exited non-zero. Carries `.command` and `.stderr`. |

```python
try:
    timeline.render("out.mp4")
except RenderError as e:
    print(e.stderr)   # what FFmpeg actually said
    print(e.command)  # the exact command, to re-run by hand
```

---

## Performance

Apple M1 (8-core, 16 GB, native arm64, Python 3.10), three runs each after a warmup, on a 15-second 1080p clip at a realistic ~8 Mbps.

```text
Workload                                 ffmpeg   core-flux   +hardware   moviepy
---------------------------------------------------------------------------------
transcode (1080p -> 720p, H.264/AAC)      4.24s      4.32s       1.36s     16.28s
composite (overlay + audio mix + fade)    4.29s      4.35s       1.40s     28.51s
transition (3 clips, crossfades)          3.52s      3.71s       1.44s     22.44s
---------------------------------------------------------------------------------
Speedup vs MoviePy                                   3.8-6.6x    12-20x
```

**Reading these honestly:**

- core-flux tracks raw FFmpeg within ~2–5%. That's the point: it adds a graph builder, not a processing layer.
- The gap over MoviePy widens with compositing (3.8x → 6.6x) because MoviePy composites frames in Python/NumPy. More layers, wider gap.
- **Ratios depend on your footage.** Encoding is the floor for everyone; harder footage means that floor dominates and the ratio shrinks. On easily-compressed video the same benchmark shows 12x+ without hardware, which flatters core-flux. Treat **4–7x as the realistic software range**.
- Hardware encoding is not free quality-wise, but it is not worse either. The VideoToolbox default (`q:v=50`) was calibrated by SSIM against the source: **0.959 vs libx264 `-crf 23`'s 0.961, in a 20% smaller file.** Other vendors use their documented crf-equivalents.

Reproduce it — the script generates its own inputs, or drop in your own `benchmark/sample.mp4`:

```bash
pip install moviepy
python benchmark/benchmark.py
```

---

## How it compares to MoviePy

**Choose core-flux when** you're layering, trimming, transitioning, mixing and exporting — especially in bulk or on a server. It's several times faster, has no Python dependencies, and the API is built around compositing.

**Choose MoviePy when** you need to touch raw pixels. Its `.iter_frames()` / `.image_transform()` give you NumPy arrays, so you can write any effect you can imagine. core-flux structurally cannot do this — everything is an FFmpeg filter, which is exactly why it's fast. That's the real dividing line, not a feature gap.

MoviePy is also ten years old with a correspondingly larger body of tutorials, StackOverflow answers and battle-testing against strange codecs.

---

## Text and subtitles without libfreetype/libass

Many FFmpeg builds ship without `drawtext` or `subtitles`. core-flux detects this and falls back to rendering with Pillow, then overlaying the result — so `add_text()` and `add_subtitles()` work on builds where they otherwise couldn't.

```python
VideoLayer("talk.mp4").resize(1280, 720).add_text("Hello", x=40, y=40, size=54, box=True)
VideoLayer("talk.mp4").resize(1280, 720).add_subtitles("captions.srt")
```

Caveats for the fallback path specifically: it needs a known layer size (call `resize()` first), it reads `.srt` only, and it adds one FFmpeg input per cue, so it caps at 100 cues. For feature-length subtitle tracks, install a build with libass. You'll get a clear error rather than a confusing failure in every one of these cases.

---

## Limitations

- **No per-frame Python access.** See above. This is architectural.
- **No masks beyond `chroma_key()`** — no arbitrary alpha masks or mask compositing.
- **`add_text()` styling is basic** compared to MoviePy's `TextClip` — no rich layout or per-character control.
- **No motion tracking, no keyframed effect parameters.** Positions can be animated with expressions; effect intensities cannot.

---

## Development

```bash
git clone https://github.com/Faaris/core-flux
cd core-flux
pip install -e ".[dev]"
pytest
```

134 tests. The suite generates its own media with FFmpeg's `lavfi` sources, so no fixture files are needed. Tests asserting durations, sizes or volumes render real files; tests asserting graph shape inspect `get_command()`.

---

## License

MIT
