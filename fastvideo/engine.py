import ffmpeg
import time

class FastVideo:
    def __init__(self, input_path):
        """Initialize the toolkit and safely detect if audio exists."""
        self.input_path = input_path
        input_node = ffmpeg.input(input_path)
        self.video_stream = input_node.video

        # Check if the file actually contains an audio stream
        try:
            probe = ffmpeg.probe(input_path)
            has_audio = any(stream['codec_type'] == 'audio' for stream in probe['streams'])
            self.audio_stream = input_node.audio if has_audio else None
        except Exception:
            # Fallback if probe fails or file is corrupted
            self.audio_stream = None

    # --- EXISTING METHODS ---

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
        """Cuts the video and safely cuts audio only if it exists."""
        self.video_stream = self.video_stream.filter('trim', start=start_time, end=end_time).filter('setpts', 'PTS-STARTPTS')
        
        # ONLY apply audio filters if an audio stream actually exists!
        if self.audio_stream is not None:
            self.audio_stream = self.audio_stream.filter('atrim', start=start_time, end=end_time).filter('asetpts', 'PTS-STARTPTS')
        return self

    def fade_out(self, start_fade, duration=1.0):
        """Smoothly fades the video to black."""
        self.video_stream = self.video_stream.filter('fade', type='out', start_time=start_fade, duration=duration)
        return self

    # --- NEW MOVIEPY-STYLE RECIPES ---

    def crop(self, x1, y1, width, height):
        """Crops a specific region of the video."""
        self.video_stream = self.video_stream.filter('crop', w=width, h=height, x=x1, y=y1)
        return self

    def rotate(self, angle):
        """Rotates the video. angle can be 90, 180, or 270 degrees."""
        if angle == 90:
            self.video_stream = self.video_stream.filter('transpose', 1)
        elif angle == 180:
            self.video_stream = self.video_stream.filter('transpose', 2).filter('transpose', 2)
        elif angle == 270:
            self.video_stream = self.video_stream.filter('transpose', 2)
        return self

    def speedx(self, factor):
        """Speeds up or slows down both video and audio by a factor."""
        self.video_stream = self.video_stream.filter('setpts', f'{1/factor}*PTS')
        
        # Guard the audio stream filter
        if self.audio_stream is not None:
            self.audio_stream = self.audio_stream.filter('atempo', factor)
        return self

    def blackwhite(self):
        """Converts the video to black and white using the hue/saturation filter."""
        self.video_stream = self.video_stream.filter('hue', s=0)
        return self

    def with_volume_scaled_to(self, factor):
        """Adjusts audio volume cleanly if audio exists."""
        if self.audio_stream is not None:
            self.audio_stream = self.audio_stream.filter('volume', volume=factor)
        return self

    def without_audio(self):
        """Removes the audio stream entirely from the graph."""
        self.audio_stream = None
        return self

    def replace_audio(self, new_audio_path):
        """Replaces the current audio track with an external audio file."""
        new_audio_node = ffmpeg.input(new_audio_path)
        self.audio_stream = new_audio_node.audio
        return self

    # --- RENDER ENGINE ---

    def render(self, output_path, format_type='video'):
        """
        Fuses the stream together and exports it.
        format_type can be 'video', 'gif', or 'audio'.
        """
        # Guard against trying to extract audio from a silent video track
        if format_type == 'audio' and self.audio_stream is None:
            raise ValueError("❌ Core-Flux Error: Cannot extract audio from a source file with no audio stream!")

        print(f"🚀 CORE-FLUX: Sending graph to FFmpeg... Rendering {output_path}")
        start_clock = time.time()
        
        # Build the final output mapping based on format type
        output_args = {}
        if format_type == 'gif':
            # Optimize filters for a clean, non-grainy GIF output using a custom palette
            split_stream = self.video_stream.split()
            palette = split_stream[0].filter('palettegen')
            self.video_stream = ffmpeg.filter([split_stream[1], palette], 'paletteuse')
            streams = [self.video_stream]
        elif format_type == 'audio':
            streams = [self.audio_stream]
            output_args.update({'acodec': 'mp3' if output_path.endswith('.mp3') else 'aac'})
        else: # Standard Video
            streams = [self.video_stream]
            if self.audio_stream is not None:
                streams.append(self.audio_stream)
            output_args.update({
                'vcodec': 'libx264',   
                'acodec': 'aac',       
                'pix_fmt': 'yuv420p'
            })

        output = ffmpeg.output(*streams, output_path, **output_args)
        output.overwrite_output().run()
        
        time_taken = time.time() - start_clock
        print("✅ Render Complete!")
        print(f"⏱️ Execution Time: {time_taken:.2f} seconds")