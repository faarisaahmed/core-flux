import ffmpeg
import time

class VideoLayer:
    def __init__(self, input_path):
        """Represents an independent video clip piece that can be positioned and styled."""
        self.input_path = input_path
        
        input_node = ffmpeg.input(input_path)
        self.video_stream = input_node.video
        
        # Check if the file has audio, if not safe-skip it
        try:
            probe = ffmpeg.probe(input_path)
            has_audio = any(stream['codec_type'] == 'audio' for stream in probe['streams'])
            self.audio_stream = input_node.audio if has_audio else None
        except Exception:
            self.audio_stream = None

        self.x_pos = 0
        self.y_pos = 0

    def set_position(self, x, y):
        self.x_pos = x
        self.y_pos = y
        return self

    def resize(self, width, height):
        self.video_stream = self.video_stream.filter('scale', width, height)
        return self

    def crop(self, x1, y1, width, height):
        self.video_stream = self.video_stream.filter('crop', width, height, x1, y1)
        return self

    def adjust_colors(self, contrast=1.0, brightness=0.0, saturation=1.0):
        self.video_stream = self.video_stream.filter('eq', contrast=contrast, brightness=brightness, saturation=saturation)
        return self

    def blackwhite(self):
        self.video_stream = self.video_stream.filter('hue', s=0)
        return self

    def with_volume_scaled_to(self, factor):
        """Adjusts the native audio volume embedded inside this video layer."""
        if self.audio_stream is not None:
            self.audio_stream = self.audio_stream.filter('volume', volume=factor)
        return self

    def mute(self):
        """Completely silences this video layer's embedded audio track."""
        return self.with_volume_scaled_to(0.0)

    def fade_in(self, start_time, duration=1.0):
        """Smoothly fades BOTH video visuals and native audio in from black/silence."""
        self.video_stream = self.video_stream.filter('fade', type='in', start_time=start_time, duration=duration)
        if self.audio_stream is not None:
            self.audio_stream = self.audio_stream.filter('afade', type='in', start_time=start_time, duration=duration)
        return self

    def fade_out(self, start_fade, duration=1.0):
        """Smoothly fades BOTH video visuals and native audio out to black/silence."""
        self.video_stream = self.video_stream.filter('fade', type='out', start_time=start_fade, duration=duration)
        if self.audio_stream is not None:
            self.audio_stream = self.audio_stream.filter('afade', type='out', start_time=start_fade, duration=duration)
        return self

    def trim(self, start, end):
        self.video_stream = self.video_stream.filter('trim', start=start, end=end).filter('setpts', 'PTS-STARTPTS')
        if self.audio_stream is not None:
            self.audio_stream = self.audio_stream.filter('atrim', start=start, end=end).filter('asetpts', 'PTS-STARTPTS')
        return self


class AudioLayer:
    def __init__(self, input_path):
        """Represents a standalone secondary audio layer (like background tracks)."""
        self.input_path = input_path
        self.audio_stream = ffmpeg.input(input_path).audio

    def with_volume_scaled_to(self, factor):
        self.audio_stream = self.audio_stream.filter('volume', volume=factor)
        return self

    def fade_out(self, start_time, duration=1.0):
        self.audio_stream = self.audio_stream.filter('afade', type='out', start_time=start_time, duration=duration)
        return self

    def trim(self, start, end):
        self.audio_stream = self.audio_stream.filter('atrim', start=start, end=end).filter('asetpts', 'PTS-STARTPTS')
        return self


class Composition:
    def __init__(self, layers=None, audio_tracks=None):
        self.layers = layers if layers is not None else []
        self.audio_tracks = audio_tracks if audio_tracks is not None else []

    def render(self, output_path, format_type='video'):
        if not self.layers and format_type != 'audio':
            raise ValueError("❌ Core-Flux Error: Cannot render without any VideoLayers!")

        print(f"🚀 CORE-FLUX [v0.3.2b1]: Rendering layout graph to {output_path}")
        start_clock = time.time()
        
        # 1. COMPOSE VIDEO LAYERS
        if format_type != 'audio':
            base = self.layers[0].video_stream
            for overlay_layer in self.layers[1:]:
                base = ffmpeg.overlay(base, overlay_layer.video_stream, x=overlay_layer.x_pos, y=overlay_layer.y_pos)
            final_video = base
            
        # 2. COMPOSE AUDIO TRACKS
        all_audio_streams = []
        for layer in self.layers:
            if layer.audio_stream is not None:
                all_audio_streams.append(layer.audio_stream)
        for track in self.audio_tracks:
            all_audio_streams.append(track.audio_stream)

        final_audio = None
        if all_audio_streams:
            if len(all_audio_streams) > 1:
                final_audio = ffmpeg.filter(all_audio_streams, 'amix', inputs=len(all_audio_streams))
            else:
                final_audio = all_audio_streams[0]

        # 3. BUILD OUTPUT SPECIFICS BASED ON FORMAT TYPE
        output_args = {}
        streams = []

        if format_type == 'audio':
            if not all_audio_streams:
                raise ValueError("❌ Core-Flux Error: No audio streams detected for format_type='audio'")
            streams = [final_audio]
            output_args.update({'acodec': 'mp3' if output_path.endswith('.mp3') else 'aac'})
        
        elif format_type == 'gif':
            # Create high-fidelity color palette optimization for high quality GIFs
            palette = final_video.filter('palettegen')
            final_video = ffmpeg.filter([final_video, palette], 'paletteuse')
            streams = [final_video]
            output_args.update({'f': 'gif'})
            
        else: # Default: 'video' (MP4)
            streams = [final_video]
            if final_audio is not None:
                streams.append(final_audio)
            output_args.update({
                'vcodec': 'libx264',   
                'acodec': 'aac',       
                'pix_fmt': 'yuv420p'
            })

        output = ffmpeg.output(*streams, output_path, **output_args)
        output.overwrite_output().run()
        
        print(f"✅ Render Complete in {time.time() - start_clock:.2f} seconds!")