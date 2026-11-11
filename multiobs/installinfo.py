"""Intégration Windows de l'installation (sans Qt : sert aussi à l'installateur).

Tout est par utilisateur (pas de droits admin) : raccourcis menu Démarrer / bureau et
entrée « Applications installées » (HKCU) pour la désinstallation.
"""
import os
import subprocess
import winreg
from pathlib import Path

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\MultiOBS"
EXE_NAME = "Multi OBS.exe"
SHORTCUT_NAME = "Multi OBS.lnk"


def default_install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / "Multi OBS"


def _ps(script: str) -> bool:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-Command", script], capture_output=True, text=True, timeout=30,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _ps_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def create_shortcuts(exe: Path, desktop: bool, start_menu: bool = True) -> bool:
    folders = []
    if start_menu:
        folders.append("[Environment]::GetFolderPath('Programs')")
    if desktop:
        folders.append("[Environment]::GetFolderPath('Desktop')")
    if not folders:
        return True
    script = "$w = New-Object -ComObject WScript.Shell; "
    for folder in folders:
        # Parenthèses obligatoires : sans elles PowerShell passe « [Environment]::… » comme du texte.
        script += (f"$s = $w.CreateShortcut((Join-Path ({folder}) {_ps_str(SHORTCUT_NAME)})); "
                   f"$s.TargetPath = {_ps_str(str(exe))}; $s.WorkingDirectory = {_ps_str(str(exe.parent))}; "
                   f"$s.IconLocation = {_ps_str(str(exe) + ',0')}; $s.Description = 'Multi OBS'; $s.Save(); ")
    return _ps(script)


def remove_shortcuts() -> None:
    _ps("foreach ($f in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) "
        f"{{ $p = Join-Path $f {_ps_str(SHORTCUT_NAME)}; if (Test-Path $p) {{ Remove-Item $p -Force }} }}")


def register(install_dir: Path, version: str) -> None:
    exe = install_dir / EXE_NAME
    size_kb = 0
    try:
        size_kb = exe.stat().st_size // 1024
    except OSError:
        pass
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        values = {
            "DisplayName": "Multi OBS",
            "DisplayVersion": version,
            "Publisher": "StundZow",
            "InstallLocation": str(install_dir),
            "DisplayIcon": f"{exe},0",
            "UninstallString": f'"{exe}" --uninstall',
            "URLInfoAbout": "https://github.com/ConejoCORP/multi-obs",
        }
        for name, value in values.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


def registered_location() -> Path | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            return Path(winreg.QueryValueEx(k, "InstallLocation")[0])
    except OSError:
        return None


def update_registered_version(version: str, exe: Path) -> None:
    """Après une mise à jour : version affichée dans « Applications installées »."""
    loc = registered_location()
    if loc and exe.parent.resolve() == loc.resolve():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, version)
        except OSError:
            pass


def unregister() -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass
