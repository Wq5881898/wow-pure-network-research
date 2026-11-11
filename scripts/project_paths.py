"""Project-local paths shared by Windows launchers and WSL commands."""
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]


def wsl_path(path):
    value = str(path)
    windows = PureWindowsPath(value)
    if windows.drive:
        if len(windows.drive) != 2 or windows.drive[1] != ':':
            raise ValueError('WSL launchers require a drive-letter path')
        return '/mnt/' + windows.drive[0].lower() + '/' + '/'.join(windows.parts[1:])
    return Path(value).as_posix()
