# FastVideo 🚀

A high-performance video editing library built on top of native FFmpeg filters.

---

## Installation

```bash
pip install fastvideo
```

## Quick Start

```python
from fastvideo import FastVideo

video = FastVideo("input.mp4")

# Trim, resize, punch up colors, and fade out
(
    video
    .trim(start_time=2, end_time=7)
    .resize(1280, 720)
    .adjust_colors(contrast=1.3, saturation=1.4)
    .fade_out(start_fade=4.0, duration=1.0)
    .render("output.mp4")
)
```

## Features

- **Trimming** — cut clips to exact start and end times
- **Resizing** — scale video to any resolution
- **Color grading** — tweak contrast and saturation with simple parameters
- **Fade effects** — add smooth fade-outs with configurable timing
- **Chainable API** — compose operations fluently in a single expression
- **FFmpeg-native** — all processing runs through battle-tested FFmpeg filters for maximum performance

## API Reference

### `FastVideo(path)`

Load a video file for editing.

```python
video = FastVideo("input.mp4")
```

### `.trim(start_time, end_time)`

Cut the video between two timestamps (in seconds).

```python
video.trim(start_time=2, end_time=7)
```

### `.resize(width, height)`

Scale the video to the given dimensions in pixels.

```python
video.resize(1280, 720)
```

### `.adjust_colors(contrast, saturation)`

Adjust contrast and saturation. Values above `1.0` increase the effect; below `1.0` decrease it.

```python
video.adjust_colors(contrast=1.3, saturation=1.4)
```

### `.fade_out(start_fade, duration)`

Add a fade-to-black effect starting at `start_fade` seconds, lasting `duration` seconds.

```python
video.fade_out(start_fade=4.0, duration=1.0)
```

### `.render(output_path)`

Write the processed video to disk.

```python
video.render("output.mp4")
```

## Requirements

- Python 3.8+
- FFmpeg installed and available on your `PATH`

## License

MIT