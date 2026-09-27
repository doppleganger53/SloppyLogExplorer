"""Voice-pack CSV loading, saving, and WAV generation helpers.

The UI keeps voice-pack rows in the same two-column shape used by Ethos-style
voice-pack CSVs: text to speak plus the desired WAV filename. This module stays
free of Qt dependencies so it can be tested and reused by command-line or build
helpers if needed.
"""

from __future__ import annotations

import csv
import re
import tempfile
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

    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        # Keep the header names aligned with the visible table labels and
        # `save_voice_csv()` so files can round-trip without schema mapping.
        return [VoiceItem(row.get("Text to be Spoken") or "", row.get("Target WAV Filename") or "") for row in reader]


def save_voice_csv(path: str | Path, items: list[VoiceItem]) -> None:
    """Write voice-pack rows in the CSV format that `load_voice_csv()` expects."""

    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Text to be Spoken", "Target WAV Filename"])
        writer.writeheader()
        for item in items:
            # Use explicit keys so column order follows `fieldnames` instead of
            # depending on dataclass or dict construction details.
            writer.writerow({"Text to be Spoken": item.text, "Target WAV Filename": item.filename})


def _voice_target_path(output_path: Path, filename: str) -> Path | None:
    clean = filename.strip()
    if not clean:
        return None
    if "/" in clean or "\\" in clean:
        raise ValueError("Voice-pack filenames must be simple file names, not paths.")
    if (
        re.search(r'[<>:"|?*\x00-\x1f]', clean)
        or clean.endswith(".")
        or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])", clean.split(".", 1)[0].rstrip(), re.IGNORECASE)
    ):
        raise ValueError("Voice-pack filenames must be valid Windows file names, without device names or special characters.")
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

    Speech is rendered to temporary files and verified before replacing any
    existing pack entries. An unavailable or failed speech engine is reported;
    an alarm tone must never silently replace a spoken radio announcement.
    """

    output_path = Path(output_dir)
    jobs: list[tuple[VoiceItem, Path]] = []
    names: set[str] = set()
    for item in items:
        target = _voice_target_path(output_path, item.filename)
        if target is not None:
            if not item.text.strip():
                raise ValueError(f"Enter text to speak for {target.name}.")
            name = target.name.casefold()
            if name in names:
                raise ValueError(f"Duplicate voice-pack filename: {target.name}")
            names.add(name)
            jobs.append((item, target))
    if not jobs:
        return []

    try:
        import pyttsx3

        # Initialize lazily inside this function so importing the application
        # does not trigger speech-engine discovery or platform-specific errors.
        engine = pyttsx3.init()
    except Exception as exc:
        raise RuntimeError("The local speech engine could not start. Install or enable a system voice and try again.") from exc

    try:
        output_path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".sloppy-voice-", dir=output_path) as temporary:
            staged: list[tuple[Path, Path]] = []
            for item, target in jobs:
                temporary_path = Path(temporary) / target.name
                try:
                    engine.save_to_file(item.text, str(temporary_path))
                    engine.runAndWait()
                    with wave.open(str(temporary_path), "rb") as rendered:
                        if rendered.getnframes() == 0 or not rendered.readframes(1):
                            raise ValueError("The speech engine produced an empty WAV.")
                except Exception as exc:
                    raise RuntimeError(f"The speech engine could not create {target.name}. Existing voice files were preserved.") from exc
                staged.append((temporary_path, target))
            for temporary_path, target in staged:
                _voice_target_path(output_path, target.name)
                temporary_path.replace(target)
    finally:
        engine.stop()
    return [target for _, target in jobs]
