# core-flux

A fast, layer-based video editing library for Python, built on native FFmpeg filtergraphs. **No required dependencies** — it drives the `ffmpeg` binary directly.

Every edit you chain appends a node to a filter graph. Nothing runs until `render()`, which compiles the whole timeline into **one FFmpeg process**. Frames never cross into Python, so a composite runs at FFmpeg's speed instead of Python's — and when you *do* need the pixels, there's an escape hatch that's still faster than the alternatives.

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

**3–6x faster than MoviePy, or 13–21x with hardware encoding** — and 3.6x faster even on custom per-frame NumPy effects. [Benchmarks below](#performance), reproducible on your machine.

> **Renamed in 0.4.0:** the import is `core_flux`, matching the package name. `import fastvideo` still works with a `DeprecationWarning` until 1.0.

---

## Installation

```bash
pip install core-flux            # zero dependencies
pip install core-flux[all]       # + Pillow (text) + NumPy (raw frames)
```

FFmpeg must be installed separately and on your `PATH`:

| Platform | Command |
|----------|---------|
| macOS | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |
| Windows | `winget install ffmpeg` |

**Requirements:** Python 3.8+, FFmpeg 4.0+.

**Optional extras.** `[text]` installs Pillow, used only when your FFmpeg lacks `drawtext`/`subtitles`. `[frames]` installs NumPy for raw frame access. Neither is needed for normal editing.

---

## Core concepts

**Layers stack; the first is the canvas.** `layers[0]` is the background; the rest composite on top in order.

**Output length follows the base video layer.** Longer music is cut off, shorter is padded with silence. A short overlay disappears when it ends rather than freezing.

**Track volumes are absolute.** `with_volume_scaled_to(0.2)` means 20%, however many tracks are mixed.

**Nothing runs until `render()`.** Layers are cheap to build and rearrange. `get_command()` shows the FFmpeg invocation without running it.

---

## Quick start

```python
from core_flux import VideoLayer, AudioLayer, Composition, crossfade, TextLayer

# Composite with music
Composition(
    layers=[
        VideoLayer("gameplay.mp4").resize(1920, 1080).fade_out(duration=1.5),
        VideoLayer("facecam.mp4").resize(400, 300).set_position(50, 50).mute(),
    ],
    audio_tracks=[AudioLayer("lofi.mp3").with_volume_scaled_to(0.2).fade_out()],
).render("edit.mp4")

# Join clips with crossfades
reel = crossfade([
    VideoLayer("intro.mp4"),
    VideoLayer("main.mp4").trim(5, 45),
    VideoLayer("outro.mp4"),
], duration=1.0, transition="fade")
Composition(layers=[reel]).render("reel.mp4")

# Titles, captions, split screens
Composition(layers=[
    VideoLayer("talk.mp4").resize(1280, 720),
    TextLayer("Chapter One", 1280, 720, duration=4, x=80, y=560, size=64),
]).render("titled.mp4")

# Green screen
Composition(layers=[
    VideoLayer("background.mp4").resize(1920, 1080),
    VideoLayer("presenter.mp4").resize(960, 540)
        .chroma_key("green").set_position(480, 270),
]).render("keyed.mp4")

# Thumbnails, GIFs, audio-only
timeline.save_frame("thumb.jpg", t=12.5)
timeline.render("clip.gif", gif_fps=15, gif_width=480)
timeline.render("mix.wav")
```

---

## API reference

### `VideoLayer(input_path)`

Probed on construction, so a bad path fails immediately. Every method returns `self`, so calls chain.

**Properties:** `.duration`, `.width`, `.height`, `.fps`, `.aspect_ratio`, `.n_frames`, `.has_audio`, `.end`. All track your edits — after `.trim(0, 5).speed(2.0)`, `.duration` is `2.5`.

#### Geometry

| Method | Description |
|--------|-------------|
| `.set_position(x, y)` | Place the top-left corner. Accepts FFmpeg expressions, so `x="20+t*30"` animates. |
| `.resize(w, h)` · `.scale_by(factor)` | Scale exactly, or by a multiplier. |
| `.crop(x1, y1, w, h)` | Keep a rectangle. |
| `.rotate(degrees, fill="black")` | 90/180/270 are lossless; other angles grow the frame. |
| `.flip(axis)` | Mirror `"horizontal"` or `"vertical"`. |
| `.margin(size, color, top, bottom, left, right)` | Add a border. |
| `.even_size()` | Round up to even dimensions, which H.264 requires. |

#### Appearance

| Method | Description |
|--------|-------------|
| `.adjust_colors(contrast, brightness, saturation)` · `.gamma(v)` · `.multiply_color(f)` | Colour grading. |
| `.blackwhite()` · `.invert()` · `.vignette()` | Looks. |
| `.blur(radius)` · `.sharpen(amount)` · `.supersample(frames)` | Softening and detail. |
| `.set_opacity(alpha)` | Semi-transparency for overlays. |
| `.chroma_key(color, similarity, blend)` | Knock out a background colour. |
| `.scroll(horizontal, vertical)` | Scroll the picture. |
| `.add_text(...)` · `.add_subtitles(path, ...)` | Burn in captions. |

#### Time

| Method | Description |
|--------|-------------|
| `.trim(start, end)` · `.subclip(start, end=None)` | Keep a section. |
| `.cut_out(start, end)` | Remove a middle section, joining the rest. |
| `.set_duration(d)` · `.set_end(t)` · `.set_fps(fps)` | Trim/pad to length; resample. |
| `.speed(factor)` | Constant rate change; audio pitch preserved. |
| `.accel_decel(new_duration, strength)` | Ease in and out of motion. Drops audio. |
| `.reverse()` · `.time_symmetrize()` | Backwards; forwards-then-backwards. |
| `.loop(count)` | Repeat, via `-stream_loop` (no frame buffering). |
| `.make_loopable(overlap)` | Crossfade the ending into the opening. |
| `.freeze(t, duration)` · `.hold_last_frame(d)` | Pause mid-clip; hold the final frame. |
| `.set_start(seconds)` | Delay the layer's entry. |
| `.fade_in(...)` · `.fade_out(...)` | Picture and audio together. |
| `.blink(on, off)` | Flash an overlay on and off. |
| `.slide_in(duration, side)` · `.slide_out(duration, side)` | Animate in/out from an edge. |

#### Masks and audio

| Method | Description |
|--------|-------------|
| `.to_mask()` · `.with_mask(layer)` | Greyscale mask; apply one as alpha. |
| `.mask_and(other)` · `.mask_or(other)` | Combine masks. |
| `.freeze_region(t, x, y, w, h)` | Freeze one rectangle, leave the rest live. |
| `.with_volume_scaled_to(f)` · `.mute()` · `.set_audio(track)` | Sound. |
| `.copy()` · `.to_image_layer(t, duration)` | Branch a layer; freeze a frame into a still. |

---

### `AudioLayer(input_path)`

`.with_volume_scaled_to()`, `.mute()`, `.normalize(target=-16.0)`, `.with_stereo_volume(left, right)`, `.delay(seconds, decay)`, `.trim()`, `.subclip()`, `.set_duration()`, `.speed()`, `.reverse()`, `.loop()`, `.set_start()`, `.fade_in()`, `.fade_out()`, `.max_volume()`, plus `.duration` and `.end`.

```python
AudioLayer("podcast.wav").trim(30, 90).normalize().fade_in().fade_out()
```

`.normalize()` targets LUFS (EBU R128): `-16` for podcasts and web, `-14` for music streaming.

---

### Other layer types

```python
ImageLayer("logo.png", duration=3).set_position(20, 20).set_opacity(0.7)
ColorLayer(1920, 1080, duration=10, color="#101014")
TextLayer("Hello", 1280, 720, duration=4, size=64, box=True)
ImageSequenceLayer("frames/%04d.png", fps=24)   # or "frames/*.png"
```

---

### Combining clips

| Function | Description |
|----------|-------------|
| `concatenate(layers, width, height, fps, audio=True)` | Join end to end with hard cuts. |
| `crossfade(layers, duration, transition, ...)` | Join with an overlap. 44 styles in `core_flux.TRANSITIONS`. |
| `clips_array(rows, width, height)` | Arrange in a grid — split screens, contact sheets. |
| `concatenate_audio(tracks)` | Join audio tracks end to end. |

All return an ordinary layer, so the result keeps chaining.

```python
from core_flux import clips_array, crossfade, TRANSITIONS

crossfade(clips, duration=0.75, transition="circleopen")
clips_array([[a, b], [c, d]], width=960, height=540)
```

---

### `Composition(layers=None, audio_tracks=None)`

| Member | Description |
|--------|-------------|
| `.add_layer(l)` · `.add_audio(t)` | Chainable. |
| `.duration` | Expected output length. |
| `.get_command(path, **kw)` | The FFmpeg command that *would* run. |
| `.render(path, ...)` | Compile and write. |
| `.save_frame(path, t)` · `.save_frames(pattern, fps)` | One image; a numbered sequence. |
| `.preview(**kw)` | Play through ffplay instead of writing a file. |
| `.iter_frames(...)` | Stream composed frames into Python. |

#### `render(output_path, format_type=None, quiet=False, verbose=False, overwrite=True, duration=None, hardware=False, **encoder_options)`

| Argument | Description |
|----------|-------------|
| `format_type` | `'video'`, `'gif'`, `'audio'`. Inferred from the extension. |
| `hardware` | Use a hardware H.264 encoder where available. |
| `duration` | Hard-cap the output length. |
| `gif_fps`, `gif_width` | GIF only; default `15` and the source width. |
| `**encoder_options` | Output options: `crf=18`, `preset="slow"`, `r=60`. |

MP4-family containers default to H.264 + AAC, `yuv420p`, `+faststart`. Others use FFmpeg's defaults, so `.webm` correctly gets VP9/Opus.

---

### Raw frames — the escape hatch

Everything above compiles to filters and never touches Python. When you need actual pixels, these decode to raw frames and back:

```python
# Read-only: measure, analyse, extract
for frame in VideoLayer("clip.mp4").resize(640, 360).iter_frames():
    ...                                  # (360, 640, 3) writable uint8 array

frame = VideoLayer("clip.mp4").resize(640, 360).get_frame(t=2.0)

# Transform: any effect you can write in NumPy
def glow(frame):
    frame[:, :, 0] = 255 - frame[:, :, 0]
    return frame

edited = VideoLayer("clip.mp4").resize(1280, 720).apply_frame_function(glow)
Composition(layers=[edited.fade_out()]).render("out.mp4")   # keeps chaining

# Generate video from scratch
from core_flux import FrameWriter
with FrameWriter("generated.mp4", 640, 360, fps=30) as writer:
    for frame in my_frames:
        writer.write(frame)
```

`iter_frames()` streams, so memory stays flat on any clip length. `apply_frame_function()` has to materialise to a scratch file — a filtergraph cannot contain Python — but returns a normal layer you can keep editing, and carries the original audio across. Pass `output_path=` to keep the result.

Needs NumPy (`pip install core-flux[frames]`); without it, pass `as_numpy=False` to work with `bytes`.

---

### Errors

Everything raised on purpose inherits from `CoreFluxError`:

| Exception | Raised when |
|-----------|-------------|
| `FFmpegNotFoundError` | `ffmpeg`/`ffprobe` not on `PATH`. |
| `MediaNotFoundError` | An input file does not exist. |
| `UnsupportedMediaError` | Unreadable, or missing the needed stream. |
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
Workload                                  ffmpeg  core-flux  +hardware   moviepy   speedup
--------------------------------------------------------------------------------------------
transcode (1080p -> 720p, H.264/AAC)       5.97s     6.16s      1.43s     18.86s   3.1x / 13.2x
composite (overlay + audio mix + fade)     5.84s     5.78s      1.52s     32.47s   5.6x / 21.3x
transition (3 clips, crossfades)           3.73s     3.93s      1.47s     22.41s   5.7x / 15.3x
frames (custom per-pixel NumPy effect)         -     4.68s          -     16.68s   3.6x
```

**Reading these honestly:**

- core-flux tracks raw FFmpeg within a few percent. That's the point: it adds a graph builder, not a processing layer.
- The gap widens with compositing (3.1x → 5.6x) because MoviePy composites frames in Python. More layers, wider gap.
- **The frames row is the interesting one.** Neither engine can express a custom NumPy effect as a filter, so both decode to Python — yet core-flux is still 3.6x faster, because the only per-frame Python is *your* function.
- **Ratios depend on your footage and machine load.** Encoding is the floor for everyone; on easily-compressed video the same benchmark shows 12x+ without hardware, which flatters core-flux. Treat **3–6x as the realistic software range**; I've measured the transcode row anywhere from 3.1x to 3.8x across runs.
- Hardware encoding isn't a quality compromise here. The VideoToolbox default (`q:v=50`) was calibrated by SSIM against the source: **0.959 vs libx264 `-crf 23`'s 0.961, in a 20% smaller file.**

```bash
pip install moviepy
python benchmark/benchmark.py     # generates its own inputs
```

---

## Coming from MoviePy

| MoviePy | core-flux |
|---------|-----------|
| `VideoFileClip(p)` | `VideoLayer(p)` |
| `clip.with_effects([Resize(...), FadeOut(1)])` | `layer.resize(...).fade_out(1)` |
| `CompositeVideoClip([a, b])` | `Composition(layers=[a, b])` |
| `concatenate_videoclips([...])` | `concatenate([...])` or `crossfade([...])` |
| `clip.with_position((x, y))` | `layer.set_position(x, y)` |
| `clip.subclipped(a, b)` | `layer.subclip(a, b)` |
| `clip.image_transform(fn)` | `layer.apply_frame_function(fn)` |
| `clip.iter_frames()` | `layer.iter_frames()` |
| `clip.write_videofile(p)` | `composition.render(p)` |

The main structural difference: MoviePy clips are self-contained and you compose at the end; core-flux layers are positioned on a `Composition` that owns the timeline. And core-flux is lazy — nothing decodes until `render()`.

**Still MoviePy-only:** rich text layout (`TextClip` styling), keyframed effect *parameters* (positions animate via expressions, but effect intensities don't), motion tracking, and its `Painting`/`HeadBlur` novelty effects.

---

## Development

```bash
git clone https://github.com/faarisaahmed/core-flux
cd core-flux
pip install -e ".[dev]"
pytest
```

194 tests. The suite generates its own media with FFmpeg's `lavfi` sources, so no fixture files are needed. Tests asserting durations, sizes, volumes or pixels render real files; tests asserting graph shape inspect `get_command()`.

---

## License

MIT
