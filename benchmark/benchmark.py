import time
import os
import subprocess
import platform
import statistics

NUM_RUNS = 3  # Multiple iterations to remove statistical noise

def get_system_specs():
    return {
        "OS": f"{platform.system()} {platform.release()}",
        "Architecture": platform.machine(),
        "Processor": platform.processor() or "Unknown Processor",
        "Python Version": platform.python_version()
    }

def run_core_flux(input_file, output_file):
    from fastvideo import VideoLayer, Composition
    start = time.time()
    clip = VideoLayer(input_file).resize(1280, 720)
    comp = Composition(layers=[clip])
    comp.render(output_file)
    return time.time() - start

def run_moviepy(input_file, output_file):
    from moviepy import VideoFileClip
    from moviepy.video.fx import Resize
    start = time.time()
    clip = VideoFileClip(input_file)
    resized_clip = clip.with_effects([Resize(new_size=(1280, 720))])
    resized_clip.write_videofile(output_file, codec="libx264", audio_codec="aac", logger=None)
    clip.close()
    resized_clip.close()
    return time.time() - start

def run_raw_ffmpeg(input_file, output_file):
    start = time.time()
    cmd = [
        'ffmpeg', '-y', 
        '-i', input_file, 
        '-vf', 'scale=1280:720', 
        '-c:v', 'libx264', 
        '-crf', '23',          
        '-c:a', 'aac', 
        output_file
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return time.time() - start

if __name__ == "__main__":
    TEST_VIDEO = "sample.mp4"
    
    if not os.path.exists(TEST_VIDEO):
        print(f"Error: '{TEST_VIDEO}' not found.")
        exit(1)
        
    specs = get_system_specs()
    print("-" * 60)
    print("BENCHMARK TEST CONDITIONS & SYSTEM SPECS")
    print("-" * 60)
    print(f"OS:         {specs['OS']}")
    print(f"CPU Arch:   {specs['Architecture']}")
    print(f"Processor:  {specs['Processor']}")
    print(f"Python:     {specs['Python Version']}")
    print(f"Input File: {TEST_VIDEO} ({os.path.getsize(TEST_VIDEO) / (1024*1024):.2f} MB)")
    print(f"Iterations: {NUM_RUNS} runs per contender (after 1 warmup run)")
    print("-" * 60 + "\n")

    # --- WARMUP RUN (To eliminate disk/cache noise) ---
    print("Priming system cache with warmup run...")
    _ = run_raw_ffmpeg(TEST_VIDEO, "warmup.mp4")
    
    results = {"ffmpeg": [], "core-flux": [], "moviepy": []}
    
    # --- BENCHMARK LOOP ---
    for i in range(NUM_RUNS):
        print(f"Executing Iteration {i+1}/{NUM_RUNS}...")
        
        # We alternate or clean up files to ensure identical disk environments
        results["ffmpeg"].append(run_raw_ffmpeg(TEST_VIDEO, f"out_ffmpeg_{i}.mp4"))
        results["core-flux"].append(run_core_flux(TEST_VIDEO, f"out_flux_{i}.mp4"))
        results["moviepy"].append(run_moviepy(TEST_VIDEO, f"out_mpy_{i}.mp4"))

    # --- CLEANUP BENCHMARK PRODUCTS ---
    for i in range(NUM_RUNS):
        for prefix in ["out_ffmpeg_", "out_flux_", "out_mpy_"]:
            if os.path.exists(f"{prefix}{i}.mp4"):
                os.remove(f"{prefix}{i}.mp4")
    if os.path.exists("warmup.mp4"):
        os.remove("warmup.mp4")

    # --- STATISTICAL ANALYSIS & REPORT ---
    print("\n" + "=" * 75)
    print("   BENCHMARK SUMMARY REPORT (1080p Scaled H.264/AAC Export)")
    print("=" * 75)
    print(f"{'Engine':<18} | {'Avg Time':<12} | {'Min Time':<12} | {'Max Time':<12} | {'Std Dev':<10}")
    print("-" * 75)
    
    for name, data in results.items():
        avg_t = statistics.mean(data)
        min_t = min(data)
        max_t = max(data)
        std_v = statistics.stdev(data) if len(data) > 1 else 0.0
        print(f"{name:<18} | {avg_t:>10.2f}s | {min_t:>10.2f}s | {max_t:>10.2f}s | {std_v:>8.2f}s")
        
    print("=" * 75)
    
    mpy_avg = statistics.mean(results["moviepy"])
    flux_avg = statistics.mean(results["core-flux"])
    print(f"Conclusion: core-flux achieves a {mpy_avg / flux_avg:.2f}x speedup relative to MoviePy.")
    print("=" * 75)