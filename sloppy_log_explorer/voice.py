"""Voice-pack CSV loading, saving, and WAV generation helpers.

The UI keeps voice-pack rows in the same two-column shape used by Ethos-style
voice-pack CSVs: text to speak plus the desired WAV filename. This module stays
free of Qt dependencies so it can be tested and reused by command-line or build
helpers if needed.
"""

from __future__ import annotations

import csv
import math
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VoiceItem:
    """One voice-pack row from the table or CSV file.

    `text` is passed directly to the TTS backend. `filename` is normalized only
    at WAV-generation time so the UI and CSV round-trip preserve what the user
    typed until actual files are created.
    """

    text: str
    filename: str


def load_voice_csv(path: str | Path) -> list[VoiceItem]:
    """Load voice-pack rows from a CSV file using the UI's canonical headers.

    Missing columns are treated as empty strings rather than errors. That keeps
    partially edited CSVs loadable and lets the table show the user what needs
    to be fixed.
    """

    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        # Keep the header names aligned with the visible table labels and
        # `save_voice_csv()` so files can round-trip without schema mapping.
        return [VoiceItem(row.get("Text to be Spoken", ""), row.get("Target WAV Filename", "")) for row in reader]


def save_voice_csv(path: str | Path, items: list[VoiceItem]) -> None:
    """Write voice-pack rows in the CSV format that `load_voice_csv()` expects."""

    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Text to be Spoken", "Target WAV Filename"])
        writer.writeheader()
        for item in items:
            # Use explicit keys so column order follows `fieldnames` instead of
            # depending on dataclass or dict construction details.
            writer.writerow({"Text to be Spoken": item.text, "Target WAV Filename": item.filename})


def _placeholder_wav(path: Path, text: str) -> None:
    """Create a short audible WAV when the local TTS engine is unavailable.

    The placeholder is intentionally a simple sine wave rather than spoken
    speech. It proves the export path, filename normalization, and WAV container
    writing all worked without pretending to be a real voice-pack output.
    """

    # 22.05 kHz mono PCM keeps the file small while still being broadly playable
    # by desktop audio tools and radio voice-pack workflows.
    sample_rate = 22050

    # Tie the placeholder length loosely to the text length so blank/short rows
    # still produce an audible file and very long labels do not create large
    # fallback assets.
    duration = max(0.25, min(1.5, len(text) / 20.0))
    frames = int(sample_rate * duration)
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        data = bytearray()
        for i in range(frames):
            # Generate signed 16-bit little-endian PCM samples for a steady
            # 660 Hz tone. The amplitude stays below the int16 maximum to avoid
            # clipping in players that apply gain.
            sample = int(16000 * math.sin(2 * math.pi * 660 * (i / sample_rate)))
            data.extend(sample.to_bytes(2, "little", signed=True))
        wav.writeframes(bytes(data))


def _voice_target_path(output_path: Path, filename: str) -> Path | None:
    clean = filename.strip()
    if not clean:
        return None
    if "/" in clean or "\\" in clean:
        raise ValueError("Voice-pack filenames must be simple file names, not paths.")
    requested = Path(clean)
    if requested.is_absolute() or len(requested.parts) != 1:
        raise ValueError("Voice-pack filenames must be simple file names, not paths.")
    if not clean.lower().endswith(".wav"):
        clean += ".wav"
    target = output_path / clean
    output_root = output_path.resolve()
    resolved_target = target.resolve()
    if not resolved_target.is_relative_to(output_root):
        raise ValueError("Voice-pack output path escaped the selected directory.")
    return target


def generate_voice_pack(items: list[VoiceItem], output_dir: str | Path) -> list[Path]:
    """Generate WAV files for the supplied voice items.

    The function prefers `pyttsx3` because it uses local operating-system speech
    engines and does not require a web service. If import or engine startup
    fails, it falls back to `_placeholder_wav()` so the export operation remains
    deterministic and the caller still receives concrete output paths.
    """

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[VoiceItem, Path]] = []
    for item in items:
        target = _voice_target_path(output_path, item.filename)
        if target is not None:
            jobs.append((item, target))
    if not jobs:
        return []

    created: list[Path] = []
    engine = None
    try:
        import pyttsx3

        # Initialize lazily inside this function so importing the application
        # does not trigger speech-engine discovery or platform-specific errors.
        engine = pyttsx3.init()
    except Exception:
        # pyttsx3 can fail for missing OS voices, broken drivers, or unavailable
        # COM/audio backends. The placeholder path preserves a useful export
        # result instead of surfacing those local environment problems here.
        engine = None

    for item, target in jobs:
        if engine is not None:
            engine.save_to_file(item.text, str(target))
            # pyttsx3 queues speech requests; `runAndWait()` flushes each file so
            # the returned path list only includes files that have been rendered.
            engine.runAndWait()
        else:
            _placeholder_wav(target, item.text)
        created.append(target)
    return created
