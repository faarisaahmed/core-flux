import ffmpeg
import time  # New import to measure speed!

class FastVideo:
    def __init__(self, input_path):
        """Initialize the toolkit with an input video path."""
        self.input_path = input_path
        self.video_stream = ffmpeg.input(input_path)
        self.audio_stream = ffmpeg.input(input_path).audio

    def resize(self, width, height):
        """Resizes the video using FFmpeg's native scale filter."""
        self.video_stream = self.video_stream.filter('scale', width, height)
        return self

    def adjust_colors(self, contrast=1.0, brightness=0.0, saturation=1.0):
        """Adjusts video contrast, brightness, and saturation."""
        self.video_stream = self.video_stream.filter(
            'eq', 
            contrast=contrast, 
            brightness=brightness, 
            saturation=saturation
        )
        return self

    def trim(self, start_time, end_time):
        """Cuts the video and audio to only keep the section between start_time and end_time (in seconds)."""
        # Trim the video pixels
        self.video_stream = self.video_stream.filter('trim', start=start_time, end=end_time).filter('setpts', 'PTS-STARTPTS')
        # Trim the audio sync'd up with the video
        self.audio_stream = self.audio_stream.filter('atrim', start=start_time, end=end_time).filter('asetpts', 'PTS-STARTPTS')
        return self

    def fade_out(self, start_fade, duration=1.0):
        """Smoothly fades the video to black. start_fade is when it begins, duration is how long it takes."""
        self.video_stream = self.video_stream.filter('fade', type='out', start_time=start_fade, duration=duration)
        return self

    def render(self, output_path):
        """Fuses video/audio together, runs the engine, and times the execution."""
        print(f"🚀 Sending commands to FFmpeg... Rendering {output_path}")
        
        # Start the stopwatch
        start_clock = time.time()
        
        output = ffmpeg.output(
            self.video_stream, 
            self.audio_stream, 
            output_path,
            vcodec='libx264',   
            acodec='aac',       
            pix_fmt='yuv420p'   
        )
        
        output.overwrite_output().run()
        
        # Stop the stopwatch
        end_clock = time.time()
        time_taken = end_clock - start_clock
        
        print("✅ Render Complete!")
        print(f"⏱️  Execution Time: {time_taken:.2f} seconds")