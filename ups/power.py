"""Windows power actions for this computer."""

from __future__ import annotations

import ctypes
import subprocess

CREATE_NO_WINDOW = 0x08000000


def shutdown(seconds: int = 20, comment: str = "UPS: компьютер выключается") -> None:
    delay = max(0, int(seconds))
    subprocess.Popen(
        ["shutdown", "/s", "/t", str(delay), "/c", comment],
        creationflags=CREATE_NO_WINDOW,
    )


def restart(seconds: int = 20, comment: str = "UPS: компьютер перезагружается") -> None:
    delay = max(0, int(seconds))
    subprocess.Popen(
        ["shutdown", "/r", "/t", str(delay), "/c", comment],
        creationflags=CREATE_NO_WINDOW,
    )


def abort_shutdown() -> None:
    subprocess.Popen(["shutdown", "/a"], creationflags=CREATE_NO_WINDOW)


def sleep() -> None:
    # FALSE = suspend, not hibernate. FORCE=TRUE skips the permission prompt.
    ctypes.windll.powrprof.SetSuspendState(False, True, False)


def hibernate() -> None:
    subprocess.Popen(["shutdown", "/h"], creationflags=CREATE_NO_WINDOW)
