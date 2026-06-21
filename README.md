# core-flux (Concisely Optimized Render Engine - FFmpeg Linear User Xtension)

A high-performance video editing and compositing library built entirely on top of native FFmpeg complex filtergraphs. By bypassing Python-level pixel manipulation and compiling layers directly into a single native filter graph, `core-flux` runs at maximum speed with a beautiful, modern API.

---

## Installation

### Stable Channel (Compositing Layer Engine v0.3.x)

```bash
pip install core-flux
```

### Legacy Channel (Linear Engine v0.2.x)

If you need the older linear pipeline structure, lock your installation to the 0.2 series:

```bash
pip install core-flux<0.3.0
```

---

## Quick Start

Create independent video and audio pieces, layer them onto a master canvas, change volume, add fades, and compile instantly:

```python
from fastvideo import VideoLayer, AudioLayer, Composition

# 1. Initialize and configure independent pieces
background = VideoLayer("bg.mp4").resize(1920, 1080).fade_out(start_fade=10.0)
facecam = (VideoLayer("gamer_cam.mp4")
           .resize(400, 300)
           .set_position(x=50, y=50)
           .mute())  # Mute native webcam background noise

# 2. Add an independent audio track
bg_music = AudioLayer("lofi_beats.mp3").with_volume_scaled_to(0.2)

# 3. Stack layers and tracks onto the composition timeline canvas
timeline = Composition(
    layers=[background, facecam],
    audio_tracks=[bg_music]
)

# 4. Render directly to a native FFmpeg stream
timeline.render("gaming_edit.mp4")
```

---

## Features

- **Compositing Canvas** — Treat video and audio tracks as individual layers that can be stacked, sized, and placed anywhere.
- **FFmpeg-Native Speed** — Zero Python processing bottleneck; your timeline is compiled into a native C-level complex graph.
- **Granular Audio Routing** — Scale or completely mute individual video stream audio tracks independently before final mixing.
- **Cinematic Transitions** — Native hardware-accelerated video and audio fades.
- **Audio Mixing** — Automatically mixes all distinct audio tracks using FFmpeg's `amix` filter.

---

## API Reference

### `VideoLayer(input_path: str)`

Represents an independent video clip layer. Automatically detects whether the file contains native audio.

```python
clip = VideoLayer("input.mp4")
```

#### `.set_position(x: int, y: int)`

Sets the pixel coordinates where the layer will sit relative to the base canvas background.

```python
clip.set_position(x=100, y=50)
```

#### `.resize(width: int, height: int)`

Scale this specific layer to the given dimensions in pixels.

```python
clip.resize(1280, 720)
```

#### `.crop(x1: int, y1: int, width: int, height: int)`

Crop a rectangular region of the layer, starting from the top-left corner `(x1, y1)`.

```python
clip.crop(100, 50, 640, 480)
```

#### `.trim(start_time: float, end_time: float)`

Cut this layer between two timestamps in seconds. Handles internal audio timing safely if audio is present.

```python
clip.trim(0, 10)
```

#### `.adjust_colors(contrast: float = 1.0, brightness: float = 0.0, saturation: float = 1.0)`

Adjust contrast, brightness, and saturation for this layer.

```python
clip.adjust_colors(contrast=1.2, saturation=1.5)
```

#### `.blackwhite()`

Convert this layer to black and white.

```python
clip.blackwhite()
```

#### `.with_volume_scaled_to(factor: float)`

Scale the video's embedded native audio stream volume. `0.5` halves volume; `2.0` doubles it.

```python
clip.with_volume_scaled_to(0.5)
```

#### `.mute()`

Completely silence the embedded native audio stream on this video layer.

```python
clip.mute()
```

#### `.fade_in(start_time: float, duration: float = 1.0)`

Smoothly fades both video visuals and its native audio in from black/silence.

```python
clip.fade_in(start_time=0.0, duration=2.0)
```

#### `.fade_out(start_fade: float, duration: float = 1.0)`

Smoothly fades both video visuals and its native audio out to black/silence.

```python
clip.fade_out(start_fade=13.5, duration=1.5)
```

---

### `AudioLayer(input_path: str)`

Represents an independent secondary audio track (e.g., sound effects, background music).

```python
music = AudioLayer("music.mp3")
```

#### `.with_volume_scaled_to(factor: float)`

Scale the audio volume. `0.5` halves volume; `2.0` doubles it.

```python
music.with_volume_scaled_to(0.5)
```

#### `.fade_out(start_time: float, duration: float = 1.0)`

Smoothly fades the audio track out to complete silence.

```python
music.fade_out(start_time=45.0, duration=2.0)
```

#### `.trim(start_time: float, end_time: float)`

Cut the audio track between two timestamps in seconds.

```python
music.trim(15, 45)
```

---

### `Composition(layers: list = None, audio_tracks: list = None)`

The central timeline manager. The first object in `layers` behaves as the background video canvas; subsequent layers are overlaid sequentially on top.

```python
timeline = Composition(layers=[bg, overlay_1], audio_tracks=[music])
```

#### `.render(output_path: str, format_type: str = 'video')`

Compiles all layer blocks into an optimal FFmpeg filtergraph and writes the result to disk.

The `format_type` parameter controls the output mode:

| Value | Description |
|-------|-------------|
| `'video'` | Standard multi-layer video with mixed audio tracks (default). Encodes with H.264 + AAC. |
| `'gif'` | Animated GIF output optimized with a custom high-fidelity color palette. |
| `'audio'` | Compiles and mixes audio streams only into an MP3 or AAC file. |

```python
# Render standard multi-layer video
timeline.render("output.mp4")

# Render high-fidelity GIF
timeline.render("output.gif", format_type='gif')

# Render audio compilation only
timeline.render("master_mix.mp3", format_type='audio')
```

> **Note:** Attempting to render with `format_type='audio'` when no tracks or layer audio streams are active raises a `ValueError`.

---

## Requirements

- Python 3.8+
- FFmpeg installed and available on your system `PATH`

---

## License

MIT