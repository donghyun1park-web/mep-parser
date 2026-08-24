"""Hold the Windows system-awake request while an existing process is alive."""

from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import sys
import time


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cfd_power  # noqa: E402


def _process_exists(pid: int) -> bool:
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            # A scheduler or service context can be denied a query handle for
            # a live interactive process. Access denied still proves the PID
            # exists; only a missing/invalid PID should end the power guard.
            return ctypes.get_last_error() == 5
        try:
            exit_code = ctypes.c_ulong()
            return bool(
                kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                and exit_code.value == still_active
            )
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Prevent Windows idle sleep while a CFD worker PID is alive."
    )
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    args = parser.parse_args(argv)
    if args.pid <= 0 or args.poll_seconds <= 0:
        parser.error("pid and poll-seconds must be positive")
    if not _process_exists(args.pid):
        return 3
    with cfd_power.keep_system_awake() as acquired:
        if not acquired:
            return 2
        while _process_exists(args.pid):
            time.sleep(args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
