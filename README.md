# core-flux (Concisely Optimized Render Engine - FFmpeg Linear User Xtension)

A high-performance video editing library built entirely on top of native FFmpeg filters. By bypassing Python-level pixel manipulation and compiling operations directly into a single native filter graph, `core-flux` runs at maximum speed with a beautiful, chainable API.

---

## Installation

```bash
pip install core-flux
```

## Quick Start

```python
from core_flux import FastVideo

# Trim, crop, speed up, convert to B&W, and render instantly
(FastVideo("input.mp4")
 .trim(start_time=2, end_time=12)
 .crop(x1=100, y1=100, width=1280, height=720)
 .speedx(1.5)
 .blackwhite()
 .render("output.mp4"))
```

## Features

- **Chainable API** — Compose operations fluently in a single, readable expression.
- **FFmpeg-Native Speed** — Zero Python processing bottleneck; your timeline is compiled into a native C-level graph.
- **Advanced Multi-Format Export** — Render high-quality crisp GIFs using dual-pass palette generation or pull out pure audio streams instantly.
- **Audio Control** — Easily scale volume, mute streams, or add entirely new external audio backdrops.

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

### `.crop(x1, y1, width, height)`

Crop a specific region of the video frame starting from coordinates `(x1, y1)`.

```python
video.crop(100, 100, 640, 480)
```

### `.rotate(angle)`

Rotate the video frame natively. Accepts `90`, `180`, or `270` degrees.

```python
video.rotate(90)
```

### `.speedx(factor)`

Speed up or slow down both video playback and audio pitch together (e.g., `2.0` for 2x speed, `0.5` for slow-mo).

```python
video.speedx(2.0)
```

### `.blackwhite()`

Convert the video to cinematic black and white.

```python
video.blackwhite()
```

### `.adjust_colors(contrast=1.0, brightness=0.0, saturation=1.0)`

Adjust contrast, brightness, and saturation. Values above `1.0` increase the effect; below `1.0` decrease it.

```python
video.adjust_colors(contrast=1.2, saturation=1.5)
```

### `.fade_out(start_fade, duration=1.0)`

Add a smooth fade-to-black effect starting at `start_fade` seconds.

```python
video.fade_out(start_fade=8.0, duration=2.0)
```

### `.with_volume_scaled_to(factor)`

Scale the audio volume (e.g., `0.5` for half volume, `2.0` to boost it).

```python
video.with_volume_scaled_to(0.5)
```

### `.without_audio()`

Completely strip the audio track from the output.

```python
video.without_audio()
```

### `.replace_audio(new_audio_path)`

Swap the current audio track for a separate external media file.

```python
video.replace_audio("soundtrack.mp3")
```

### `.render(output_path, format_type='video')`

Compile the filter graph and write the result to disk.

- `format_type='video'`: Exports standard `.mp4` video.
- `format_type='gif'`: Generates a high-quality, palette-optimized GIF.
- `format_type='audio'`: Drops the video stream and exports raw audio (e.g., `.mp3` or `.aac`).

```python
# Save as video
video.render("output.mp4")

# Save as crisp high-quality GIF
video.render("reaction.gif", format_type="gif")
```

## Requirements

- Python 3.8+
- FFmpeg installed and available on your `PATH`

## License

MIT