from __future__ import annotations

import csv
import math
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VoiceItem:
    text: str
    filename: str


def load_voice_csv(path: str | Path) -> list[VoiceItem]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [VoiceItem(row.get("Text to be Spoken", ""), row.get("Target WAV Filename", "")) for row in reader]


def save_voice_csv(path: str | Path, items: list[VoiceItem]) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Text to be Spoken", "Target WAV Filename"])
        writer.writeheader()
        for item in items:
            writer.writerow({"Text to be Spoken": item.text, "Target WAV Filename": item.filename})


def _placeholder_wav(path: Path, text: str) -> None:
    sample_rate = 22050
    duration = max(0.25, min(1.5, len(text) / 20.0))
    frames = int(sample_rate * duration)
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        data = bytearray()
        for i in range(frames):
            sample = int(16000 * math.sin(2 * math.pi * 660 * (i / sample_rate)))
            data.extend(sample.to_bytes(2, "little", signed=True))
        wav.writeframes(bytes(data))


def generate_voice_pack(items: list[VoiceItem], output_dir: str | Path) -> list[Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    engine = None
    try:
        import pyttsx3

        engine = pyttsx3.init()
    except Exception:
        engine = None

    for item in items:
        filename = item.filename.strip()
        if not filename:
            continue
        if not filename.lower().endswith(".wav"):
            filename += ".wav"
        target = output_path / filename
        if engine is not None:
            engine.save_to_file(item.text, str(target))
            engine.runAndWait()
        else:
            _placeholder_wav(target, item.text)
        created.append(target)
    return created

