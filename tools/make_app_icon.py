"""Encode the app's PNG artwork as a multi-resolution Windows ICO using Qt."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from PyQt6.QtCore import QBuffer, QIODevice, Qt
from PyQt6.QtGui import QImage

ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
ROOT = Path(__file__).resolve().parents[1]


def make_icon(source: Path, output: Path) -> None:
    image = QImage(str(source))
    if image.isNull() or image.width() != image.height():
        raise ValueError("Icon artwork must be a readable square image")
    frames: list[bytes] = []
    for size in ICON_SIZES:
        scaled = image.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not scaled.save(buffer, "PNG"):
            raise RuntimeError(f"Could not encode the {size}px icon frame")
        frames.append(buffer.data().data())
    offset = 6 + 16 * len(frames)
    entries = []
    for size, frame in zip(ICON_SIZES, frames):
        dimension = size if size < 256 else 0
        entries.append(struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(frame), offset))
        offset += len(frame)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(struct.pack("<HHH", 0, 1, len(frames)) + b"".join(entries) + b"".join(frames))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "sloppy_log_explorer/assets/app-icon.png")
    parser.add_argument("--output", type=Path, default=ROOT / "sloppy_log_explorer/assets/app-icon.ico")
    args = parser.parse_args()
    make_icon(args.source, args.output)
    print(f"Wrote {args.output} with sizes {ICON_SIZES}")
