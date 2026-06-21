# core-flux (Concisely Optimized Render Engine - FFmpeg Linear User Xtension)
A high-performance video editing library built entirely on top of native FFmpeg filters. By bypassing Python-level pixel manipulation and compiling operations directly into a single native filter graph, `core-flux` runs at maximum speed with a beautiful, chainable API.

---

## Installation
```bash
pip install core-flux
```

## Quick Start
```python
from fastvideo import FastVideo

# Trim, resize, adjust colors, and render instantly
video = FastVideo("input.mp4")
video.trim(2, 12).resize(1280, 720).adjust_colors(contrast=1.2, saturation=1.5).render("output.mp4")
```

## Features
- **Chainable API** — Compose operations fluently in a single, readable expression.
- **FFmpeg-Native Speed** — Zero Python processing bottleneck; your timeline is compiled into a native C-level graph.
- **Audio Control** — Easily scale, replace, or remove audio streams.
- **Silent-File Safe** — Audio filters are automatically skipped when no audio stream is detected.

---

## API Reference

### `FastVideo(input_path: str)`
Load a media file for editing. Automatically detects whether the file contains an audio stream.
```python
video = FastVideo("input.mp4")
```

---

### Video Operations

#### `.resize(width: int, height: int)`
Scale the video to the given dimensions in pixels.
```python
video.resize(1920, 1080)
```

#### `.crop(x1: int, y1: int, width: int, height: int)`
Crop a rectangular region of the video, starting from the top-left corner `(x1, y1)`.
```python
video.crop(100, 50, 1280, 720)
```

#### `.rotate(angle: int)`
Rotate the video. Supported angles are `90`, `180`, and `270` degrees.
```python
video.rotate(90)
```

#### `.trim(start_time: float, end_time: float)`
Cut the video (and audio, if present) between two timestamps in seconds.
```python
video.trim(0, 10)
```

#### `.fade_out(start_fade: float, duration: float = 1.0)`
Add a smooth fade-to-black effect starting at `start_fade` seconds.
```python
video.fade_out(start_fade=8.0, duration=2.0)
```

#### `.adjust_colors(contrast: float = 1.0, brightness: float = 0.0, saturation: float = 1.0)`
Adjust contrast, brightness, and saturation. Values above `1.0` increase the effect; below `1.0` decrease it.
```python
video.adjust_colors(contrast=1.2, saturation=1.5)
```

#### `.blackwhite()`
Convert the video to black and white.
```python
video.blackwhite()
```

#### `.speedx(factor: float)`
Speed up or slow down both video and audio by a multiplier. `2.0` is double speed; `0.5` is half speed.
```python
video.speedx(1.5)
```

---

### Audio Operations

#### `.with_volume_scaled_to(factor: float)`
Scale the audio volume. `0.5` halves it; `2.0` doubles it. No-op if the source has no audio.
```python
video.with_volume_scaled_to(0.5)
```

#### `.without_audio()`
Remove the audio stream entirely from the output.
```python
video.without_audio()
```

#### `.replace_audio(new_audio_path: str)`
Swap the current audio track with an external audio file.
```python
video.replace_audio("soundtrack.mp3")
```

---

### Rendering

#### `.render(output_path: str, format_type: str = 'video')`
Compile the filter graph and write the result to disk.

The `format_type` parameter controls the output mode:

| Value | Description |
|---|---|
| `'video'` | Standard video with audio (default). Encodes with H.264 + AAC. |
| `'gif'` | Animated GIF with palette optimization for clean output. |
| `'audio'` | Extracts the audio stream only. Outputs MP3 or AAC based on file extension. |

```python
# Standard video
video.render("output.mp4")

# Animated GIF
video.render("output.gif", format_type='gif')

# Audio only
video.render("output.mp3", format_type='audio')
```

> **Note:** Passing `format_type='audio'` on a source file with no audio stream raises a `ValueError`.

---

## Requirements
- Python 3.8+
- FFmpeg installed and available on your `PATH`

## License
MIT