"""Windows power-management guard for long local CFD calculations."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
import os


ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _set_system_required(required: bool) -> bool:
    """Set or clear the current thread's Windows system-sleep request."""
    if os.name != "nt":
        return False
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if required else 0)
    try:
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except (AttributeError, OSError):
        # Power hints must never turn an otherwise healthy CFD run into a
        # failure. The solver's checkpoint/recovery path remains authoritative.
        return False


@contextmanager
def keep_system_awake():
    """Prevent idle system sleep for the duration of a solver-owning job.

    Display sleep is intentionally left unchanged. The request is scoped to
    the calling worker thread and is always cleared when the job exits.
    """
    acquired = _set_system_required(True)
    try:
        yield acquired
    finally:
        if acquired:
            _set_system_required(False)
