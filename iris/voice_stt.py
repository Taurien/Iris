import wave
from pathlib import Path

import numpy as np

_model = None

SUPPORTED_LANGUAGES = {"es", "en"}


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel("small", device="cpu", compute_type="int8")
    return _model


def _wav_to_array(wav_path: Path) -> np.ndarray:
    with wave.open(str(wav_path), "rb") as wf:
        assert wf.getframerate() == 16000, f"expected 16kHz wav, got {wf.getframerate()}"
        assert wf.getnchannels() == 1, "expected mono wav"
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe_array(audio: np.ndarray) -> tuple[str, str]:
    """Transcribes audio, auto-detecting between Spanish and English.
    Returns (text, language_code), where language_code is "es" or "en"
    (falls back to "es" if Whisper detects something else entirely)."""
    model = _get_model()
    segments, info = model.transcribe(audio, language=None, beam_size=5)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    lang = info.language if info.language in SUPPORTED_LANGUAGES else "es"
    return text, lang


def transcribe(wav_path: Path) -> tuple[str, str]:
    return transcribe_array(_wav_to_array(wav_path))
