"""Convert audio to other formats"""

import subprocess
import os
import shutil
import librosa
import soundfile as sf

from modules.console_colors import ULTRASINGER_HEAD


def convert_audio_to_mono_wav(input_file_path: str, output_file_path: str) -> None:
    """Convert audio to mono wav"""
    print(f"{ULTRASINGER_HEAD} Converting audio for AI")
    y, sr = librosa.load(input_file_path, mono=True, sr=None)
    sf.write(output_file_path, y, sr)


def convert_audio_format(input_file_path: str, output_file_path: str) -> None:
    """Convert audio to the format specified by the output file extension using ffmpeg"""
    output_ext = os.path.splitext(output_file_path)[1].lower()
    output_format = output_ext.lstrip(".")
    input_ext = os.path.splitext(input_file_path)[1].lower()

    print(f"{ULTRASINGER_HEAD} Converting audio to {output_format}. -> {output_file_path}")
    # Preserve quality: avoid re-encode if extension already matches.
    if input_ext == output_ext:
        shutil.copy2(input_file_path, output_file_path)
        return

    codec_args: list[str]
    if output_ext == ".mp3":
        codec_args = ["-c:a", "libmp3lame", "-q:a", "0"]
    elif output_ext == ".ogg":
        codec_args = ["-c:a", "libvorbis", "-q:a", "8"]
    elif output_ext == ".opus":
        codec_args = ["-c:a", "libopus", "-b:a", "192k"]
    elif output_ext in (".m4a", ".aac"):
        codec_args = ["-c:a", "aac", "-b:a", "320k"]
    elif output_ext == ".flac":
        codec_args = ["-c:a", "flac"]
    elif output_ext == ".wav":
        codec_args = ["-c:a", "pcm_s16le"]
    else:
        # Unknown container: let ffmpeg pick sane default.
        codec_args = []

    cmd = [
        "ffmpeg",
        "-i", input_file_path,
        "-y",
        "-loglevel", "error",
        *codec_args,
        output_file_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg audio conversion failed: {result.stderr}")
