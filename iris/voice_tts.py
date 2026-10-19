import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = PROJECT_ROOT / "models" / "piper"

_VOICE_PREFIXES = {"es": "es_", "en": "en_"}

_voices: dict[str, object] = {}


def _get_voice(lang: str):
    prefix = _VOICE_PREFIXES.get(lang, _VOICE_PREFIXES["es"])
    if prefix not in _voices:
        from piper import PiperVoice

        onnx_files = sorted(MODEL_DIR.glob(f"{prefix}*.onnx"))
        if not onnx_files:
            raise RuntimeError(
                f"no Piper voice model found in {MODEL_DIR} matching '{prefix}*' — "
                "download the matching voice (.onnx + .onnx.json pair) from the "
                "OHF-Voice/piper1-gpl voice catalog and place it there."
            )
        _voices[prefix] = PiperVoice.load(str(onnx_files[0]))
    return _voices[prefix]


def synthesize(text: str, wav_path: Path, lang: str = "es") -> None:
    voice = _get_voice(lang)
    with wave.open(str(wav_path), "wb") as f:
        voice.synthesize_wav(text, f)
