"""Shortcut in the Windows Startup folder, pointing at start.bat."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ups import APP_NAME

ROOT = Path(__file__).resolve().parent.parent
CREATE_NO_WINDOW = 0x08000000


def shortcut_dir(folder: Path | None = None) -> Path:
    return folder or Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path(folder: Path | None = None) -> Path:
    return shortcut_dir(folder) / f"{APP_NAME}.lnk"


def _legacy_shortcuts(folder: Path | None = None) -> list[Path]:
    base = shortcut_dir(folder)
    return [base / "ВАХТА.lnk", base / "Берег.lnk"]


def start_bat() -> Path:
    return ROOT / "start.bat"


def is_enabled(folder: Path | None = None) -> bool:
    if shortcut_path(folder).is_file():
        return True
    return any(path.is_file() for path in _legacy_shortcuts(folder))


def enable(folder: Path | None = None) -> Path:
    bat = start_bat()
    if not bat.is_file():
        raise FileNotFoundError(f"Нет файла {bat}")
    link = shortcut_path(folder)
    link.parent.mkdir(parents=True, exist_ok=True)
    icon = ROOT / "static" / "icon.ico"
    lines = [
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps(link)})",
        f"$s.TargetPath = {_ps(bat)}",
        f"$s.WorkingDirectory = {_ps(bat.parent)}",
        "$s.WindowStyle = 7",
    ]
    if icon.is_file():
        lines.append(f"$s.IconLocation = {_ps(icon)}")
    lines.append("$s.Save()")
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "; ".join(lines)],
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    if completed.returncode != 0 or not link.is_file():
        detail = (completed.stderr or completed.stdout or "неизвестная ошибка").strip()
        raise OSError(detail[:200] or "ярлык не создался")
    _remove_legacy(folder)
    return link


def disable(folder: Path | None = None) -> None:
    path = shortcut_path(folder)
    if path.is_file():
        path.unlink()
    _remove_legacy(folder)


def _remove_legacy(folder: Path | None = None) -> None:
    for path in _legacy_shortcuts(folder):
        if path.is_file():
            path.unlink()


def _ps(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"
