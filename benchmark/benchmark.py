"""Benchmark core-flux against raw FFmpeg and MoviePy.

Run it with no arguments; the fixture clips are generated on first use, so the
numbers are reproducible on any machine with FFmpeg installed:

    pip install moviepy
    python benchmark/benchmark.py

Two workloads are measured:

  transcode  - scale 1080p to 720p and re-encode H.264/AAC. This is almost
               pure FFmpeg encode time, so every engine should land close
               together; it exists to show core-flux adds no overhead.
  composite  - overlay a second clip, mute it, mix in a music bed and fade
               out. This is the workload the library is actually built for.
"""

import os
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "sample.mp4")
OVERLAY = os.path.join(HERE, "overlay.mp4")
MUSIC = os.path.join(HERE, "music.mp3")
NUM_RUNS = 3


def ffmpeg(args):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + args, check=True
    )


def build_fixtures():
    """Generate any missing inputs. Existing files are never overwritten, so
    dropping your own real footage in as sample.mp4 just works.

    The synthetic source carries added noise and is capped at a realistic
    ~8 Mbps. A clean synthetic pattern compresses so well that encoding costs
    almost nothing, which flatters any engine that only wraps FFmpeg.
    """
    if not os.path.exists(SAMPLE):
        print("Generating 1080p sample clip (no sample.mp4 found)...")
        ffmpeg(["-f", "lavfi",
                "-i", "testsrc2=size=1920x1080:rate=30:duration=15,"
                      "noise=alls=14:allf=t",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=15",
                "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
                "-b:v", "8M", "-maxrate", "8M", "-bufsize", "16M",
                "-c:a", "aac", "-shortest", SAMPLE])
    if not os.path.exists(OVERLAY):
        print("Generating overlay clip...")
        ffmpeg(["-f", "lavfi",
                "-i", "testsrc2=size=640x480:rate=30:duration=15,"
                      "noise=alls=14:allf=t",
                "-f", "lavfi", "-i", "sine=frequency=880:duration=15",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-b:v", "2M",
                "-c:a", "aac", "-shortest", OVERLAY])
    if not os.path.exists(MUSIC):
        print("Generating music bed...")
        ffmpeg(["-f", "lavfi", "-i", "sine=frequency=220:duration=20", MUSIC])


# ------------------------------------------------------------------ transcode

def flux_transcode(output):
    from core_flux import Composition, VideoLayer

    Composition(layers=[VideoLayer(SAMPLE).resize(1280, 720)]).render(
        output, quiet=True
    )


def ffmpeg_transcode(output):
    ffmpeg(["-i", SAMPLE, "-vf", "scale=1280:720", "-c:v", "libx264",
            "-crf", "23", "-c:a", "aac", output])


def moviepy_transcode(output):
    from moviepy import VideoFileClip
    from moviepy.video.fx import Resize

    clip = VideoFileClip(SAMPLE)
    resized = clip.with_effects([Resize(new_size=(1280, 720))])
    resized.write_videofile(output, codec="libx264", audio_codec="aac", logger=None)
    clip.close()
    resized.close()


# ------------------------------------------------------------------ composite

def flux_composite(output):
    from core_flux import AudioLayer, Composition, VideoLayer

    base = VideoLayer(SAMPLE).resize(1280, 720).fade_out(duration=1.5)
    overlay = VideoLayer(OVERLAY).resize(320, 240).set_position(40, 40).mute()
    music = AudioLayer(MUSIC).with_volume_scaled_to(0.2)
    Composition(layers=[base, overlay], audio_tracks=[music]).render(
        output, quiet=True
    )


def ffmpeg_composite(output):
    ffmpeg([
        "-i", SAMPLE, "-i", OVERLAY, "-i", MUSIC,
        "-filter_complex",
        "[0:v]scale=1280:720,fade=t=out:st=13.5:d=1.5[base];"
        "[1:v]scale=320:240[ov];"
        "[base][ov]overlay=40:40:eof_action=pass[v];"
        "[2:a]volume=0.2[m];"
        "[0:a][m]amix=inputs=2:normalize=0:dropout_transition=0,apad[a]",
        "-map", "[v]", "-map", "[a]", "-shortest",
        "-c:v", "libx264", "-crf", "23", "-c:a", "aac", "-pix_fmt", "yuv420p",
        output,
    ])


def moviepy_composite(output):
    from moviepy import (
        AudioFileClip, CompositeAudioClip, CompositeVideoClip, VideoFileClip,
    )
    from moviepy.audio.fx import MultiplyVolume
    from moviepy.video.fx import FadeOut, Resize

    base = VideoFileClip(SAMPLE).with_effects(
        [Resize(new_size=(1280, 720)), FadeOut(1.5)]
    )
    overlay = (VideoFileClip(OVERLAY)
               .with_effects([Resize(new_size=(320, 240))])
               .with_position((40, 40))
               .without_audio())
    music = AudioFileClip(MUSIC).with_effects([MultiplyVolume(0.2)])
    music = music.subclipped(0, base.duration)
    composite = CompositeVideoClip([base, overlay])
    composite.audio = CompositeAudioClip([base.audio, music])
    composite.write_videofile(
        output, codec="libx264", audio_codec="aac", logger=None
    )
    for clip in (base, overlay, music, composite):
        clip.close()


WORKLOADS = [
    ("transcode (1080p -> 720p, H.264/AAC)", {
        "ffmpeg": ffmpeg_transcode,
        "core-flux": flux_transcode,
        "moviepy": moviepy_transcode,
    }),
    ("composite (overlay + audio mix + fade)", {
        "ffmpeg": ffmpeg_composite,
        "core-flux": flux_composite,
        "moviepy": moviepy_composite,
    }),
]


def time_run(fn, output):
    start = time.time()
    fn(output)
    elapsed = time.time() - start
    if os.path.exists(output):
        os.remove(output)
    return elapsed


def main():
    build_fixtures()

    import platform
    print("-" * 75)
    print("CORE-FLUX BENCHMARK")
    print("-" * 75)
    print("OS:         %s %s" % (platform.system(), platform.release()))
    print("CPU arch:   %s" % platform.machine())
    print("Python:     %s" % platform.python_version())
    print("Input:      %s (%.2f MB)"
          % (os.path.basename(SAMPLE), os.path.getsize(SAMPLE) / (1024 * 1024)))
    print("Iterations: %d per engine, after one warmup" % NUM_RUNS)
    print("-" * 75)

    print("\nWarming the page cache...")
    time_run(ffmpeg_transcode, os.path.join(HERE, "warmup.mp4"))

    for title, engines in WORKLOADS:
        results = {}
        print("\nWorkload: %s" % title)
        for name, fn in engines.items():
            timings = []
            for index in range(NUM_RUNS):
                output = os.path.join(HERE, "bench_%s_%d.mp4" % (name.replace("-", ""), index))
                try:
                    timings.append(time_run(fn, output))
                except Exception as exc:  # a missing engine shouldn't kill the run
                    print("  %-12s skipped (%s)" % (name, exc))
                    timings = []
                    break
            if timings:
                results[name] = timings
                print("  %-12s done" % name)

        print("\n  %-12s | %-10s | %-10s | %-10s | %-8s"
              % ("Engine", "Avg", "Min", "Max", "Std dev"))
        print("  " + "-" * 63)
        for name, data in results.items():
            print("  %-12s | %8.2fs | %8.2fs | %8.2fs | %6.2fs"
                  % (name, statistics.mean(data), min(data), max(data),
                     statistics.stdev(data) if len(data) > 1 else 0.0))

        if "moviepy" in results and "core-flux" in results:
            ratio = statistics.mean(results["moviepy"]) / statistics.mean(results["core-flux"])
            print("\n  core-flux is %.2fx the speed of MoviePy on this workload." % ratio)

    print("\n" + "-" * 75)


if __name__ == "__main__":
    main()
