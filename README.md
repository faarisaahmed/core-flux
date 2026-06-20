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
- **Audio Control** — Easily scale volume or mute streams.

## API Reference

### `FastVideo(input_path)`

Load a media file for editing.

```python
video = FastVideo("input.mp4")
```

### `.trim(start_time, end_time)`

Cut the video and audio between two timestamps (in seconds).

```python
video.trim(0, 10)
```

### `.resize(width, height)`

Scale the video to the given dimensions in pixels.

```python
video.resize(1920, 1080)
```

### `.fade_out(start_fade, duration=1.0)`

Add a smooth fade-to-black effect starting at `start_fade` seconds.

```python
video.fade_out(start_fade=8.0, duration=2.0)
```

### `.adjust_colors(contrast=1.0, brightness=0.0, saturation=1.0)`

Adjust contrast, brightness, and saturation. Values above `1.0` increase the effect; below `1.0` decrease it.

```python
video.adjust_colors(contrast=1.2, saturation=1.5)
```

### `.with_volume_scaled_to(factor)`

Scale the audio volume (e.g., `0.5` for half volume, `2.0` to boost it).

```python
video.with_volume_scaled_to(0.5)
```

### `.render(output_path)`

Compile the filter graph and write the result to disk.

```python
video.render("output.mp4")
```

## Requirements

- Python 3.8+
- FFmpeg installed and available on your `PATH`

## License

MIT