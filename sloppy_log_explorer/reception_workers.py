"""Cancelable single-purpose background tasks used by the Reception Map UI."""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot


ProgressCallback = Callable[[int, int, str], None]
TaskFunction = Callable[[ProgressCallback, Callable[[], bool]], Any]


class ReceptionTaskSignals(QObject):
    """Signals emitted by a :class:`ReceptionTask` on the GUI thread."""

    progress = pyqtSignal(int, int, str)
    result = pyqtSignal(object)
    failed = pyqtSignal(str)
    finished = pyqtSignal()


class ReceptionTask(QRunnable):
    """Run reception work off the GUI thread with cooperative cancellation."""

    def __init__(self, function: TaskFunction) -> None:
        super().__init__()
        self.function = function
        self.signals = ReceptionTaskSignals()
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = self.function(self.signals.progress.emit, self.is_cancelled)
            if not self.is_cancelled():
                self.signals.result.emit(result)
        except Exception:
            if not self.is_cancelled():
                self.signals.failed.emit(traceback.format_exc())
        finally:
            self.signals.finished.emit()
