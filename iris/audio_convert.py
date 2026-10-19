import subprocess
from pathlib import Path


def ogg_to_wav(ogg_path: Path, wav_path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(ogg_path), "-ar", "16000", "-ac", "1", "-f", "wav", str(wav_path)],
        check=True,
        capture_output=True,
    )


def wav_to_ogg_opus(wav_path: Path, ogg_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(wav_path),
            "-c:a", "libopus", "-b:a", "32k", "-vbr", "on", "-application", "voip",
            str(ogg_path),
        ],
        check=True,
        capture_output=True,
    )
