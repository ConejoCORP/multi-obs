"""Enregistrement des caméras virtuelles « VCam Rouge », « VCam Bleu »… dans Windows.

On reproduit exactement l'enregistrement fait par l'installeur d'OBS, mais avec un
CLSID et un nom par caméra, en pointant vers les modules patchés de data/vcam/N.
"""
import os
import re
import subprocess
import tempfile
import threading
import winreg
from collections import Counter
from pathlib import Path

from . import obsfiles, paths, theme

_lock = threading.Lock()   # allumage et réparation automatique peuvent tourner en même temps

CATEGORY = "{860BB310-5D01-11d0-BD3B-00A0C911CE86}"  # CLSID_VideoInputDeviceCategory
# FilterData de la caméra virtuelle d'OBS (formats NV12, 1 broche de sortie).
FILTER_DATA = bytes.fromhex(
    "0200000000002000010000000000000030706933080000000000000001000000"
    "0000000000000000307479330000000038000000480000007669647300001000"
    "800000AA00389B714E56313200001000800000AA00389B71"
)

SCOPE_USER = "user"        # HKCU : pas besoin des droits admin
SCOPE_MACHINE = "machine"  # HKLM : comme l'installeur d'OBS (demande les droits admin)


def _guid_str(n: int) -> str:
    return "{" + str(obsfiles.vcam_guid(n)).upper() + "}"


def display_names(instances) -> dict[int, str]:
    """Nom de chaque caméra dans Windows (Discord…) : la couleur de son instance, « VCam Rouge ».
    Deux instances de la même couleur : on ajoute le numéro de la caméra pour les distinguer."""
    counts = Counter(i.color for i in instances)
    names = {}
    for i in instances:
        label = theme.ACCENTS.get(i.color, theme.ACCENTS["blue"])[0]
        names[i.vcam] = f"VCam {label}" + (f" {i.vcam}" if counts[i.color] > 1 else "")
    return names


def registered_name(n: int) -> str | None:
    """Nom sous lequel la caméra n est enregistrée pour l'utilisateur (None si absente)."""
    sub = rf"Software\Classes\CLSID\{CATEGORY}\Instance\{_guid_str(n)}"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            return winreg.QueryValueEx(k, "FriendlyName")[0]
    except OSError:
        return None


def up_to_date(n: int, name: str) -> bool:
    """Enregistrée vers nos modules et sous le bon nom (nom vérifié pour l'enregistrement
    utilisateur seulement : celui de la machine demanderait les droits admin)."""
    if not is_registered(n):
        return False
    return registered_scope(n) != SCOPE_USER or registered_name(n) == name


def _roots(scope: str) -> tuple[str, str]:
    base = r"HKEY_CURRENT_USER\Software\Classes" if scope == SCOPE_USER \
        else r"HKEY_LOCAL_MACHINE\SOFTWARE\Classes"
    return base, base + r"\WOW6432Node"


def _reg_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _reg_file(indices: list[int], scope: str, remove: bool, names: dict[int, str] | None = None) -> str:
    lines = ["Windows Registry Editor Version 5.00", ""]
    hexdata = ",".join(f"{b:02x}" for b in FILTER_DATA)
    root64, root32 = _roots(scope)
    for n in indices:
        guid = _guid_str(n)
        name = (names or {}).get(n) or obsfiles.vcam_name(n)
        mod_dir = paths.VCAM / str(n)
        for root, dll in ((root64, "obs-virtualcam-module64.dll"), (root32, "obs-virtualcam-module32.dll")):
            clsid_key = rf"{root}\CLSID\{guid}"
            inst_key = rf"{root}\CLSID\{CATEGORY}\Instance\{guid}"
            if remove:
                lines += [f"[-{clsid_key}]", "", f"[-{inst_key}]", ""]
                continue
            lines += [
                f"[{clsid_key}]", f"@={_reg_str(name)}", "",
                f"[{clsid_key}\\InprocServer32]",
                f"@={_reg_str(str(mod_dir / dll))}",
                '"ThreadingModel"="Both"', "",
                f"[{inst_key}]",
                f'"FriendlyName"={_reg_str(name)}',
                f'"CLSID"={_reg_str(guid)}',
                f'"FilterData"=hex:{hexdata}', "",
            ]
    return "\r\n".join(lines) + "\r\n"


def _run_reg_import(content: str, elevated: bool) -> None:
    # Un fichier par appel : deux enregistrements simultanés (allumage + réparation, deux
    # Multi OBS…) ne peuvent pas importer le fichier de l'autre.
    fd, name = tempfile.mkstemp(prefix="multiobs_vcam_", suffix=".reg")
    os.close(fd)
    tmp = Path(name)
    tmp.write_text(content, encoding="utf-16")
    if elevated:
        ps = (f"$p = Start-Process -FilePath reg.exe -ArgumentList 'import','\"{tmp}\"' "
              f"-Verb RunAs -WindowStyle Hidden -Wait -PassThru; exit $p.ExitCode")
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    else:
        subprocess.run(["reg.exe", "import", str(tmp)],
                       creationflags=subprocess.CREATE_NO_WINDOW, check=False,
                       capture_output=True)
    try:
        tmp.unlink()
    except OSError:
        pass


def install(indices: list[int], scope: str = SCOPE_USER, colors: dict[int, str] | None = None,
            names: dict[int, str] | None = None) -> list[int]:
    """Prépare les modules et enregistre (ou renomme) les caméras. Renvoie celles qui ont échoué."""
    with _lock:
        for n in indices:
            obsfiles.build_vcam_modules(n, (colors or {}).get(n))
        _run_reg_import(_reg_file(indices, scope, remove=False, names=names), elevated=(scope == SCOPE_MACHINE))
        return [n for n in indices if not is_registered(n)
                or (scope == SCOPE_USER and names and n in names and registered_name(n) != names[n])]


def uninstall(indices: list[int]) -> None:
    with _lock:
        for scope in (SCOPE_USER, SCOPE_MACHINE):
            regs = [n for n in indices if registered_scope(n) == scope]
            if regs:
                _run_reg_import(_reg_file(regs, scope, remove=True), elevated=(scope == SCOPE_MACHINE))
        for n in indices:
            try:
                resolution_file(n).unlink(missing_ok=True)
            except OSError:
                pass


def resolution_file(n: int) -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / obsfiles.vcam_res_name(n)


def ensure_resolution_file(n: int, cx: int, cy: int, interval: int) -> None:
    """Sans ce fichier, la caméra n ne propose aucun format tant que son OBS est fermé : Discord charge
    à l'infini au lieu d'afficher l'image d'attente. L'OBS de l'instance le réécrit à chaque démarrage
    de sa caméra virtuelle ; on ne l'écrit que s'il manque."""
    f = resolution_file(n)
    try:
        if re.fullmatch(r"\d+x\d+x\d+", f.read_text("ascii").strip()):
            return
    except (OSError, UnicodeDecodeError):
        pass
    try:
        f.write_text(f"{cx}x{cy}x{interval}", "ascii")
    except OSError:
        pass


def _inproc_path(hive, n: int) -> str | None:
    sub = r"Software\Classes\CLSID" + "\\" + _guid_str(n) + r"\InprocServer32"
    try:
        with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            value, _ = winreg.QueryValueEx(k, "")
            return value
    except OSError:
        return None


def registered_scope(n: int) -> str | None:
    if _inproc_path(winreg.HKEY_CURRENT_USER, n):
        return SCOPE_USER
    if _inproc_path(winreg.HKEY_LOCAL_MACHINE, n):
        return SCOPE_MACHINE
    return None


def is_registered(n: int) -> bool:
    """Enregistrée ET pointant vers un module qui existe encore."""
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        p = _inproc_path(hive, n)
        if p:
            return Path(p).exists() and Path(p).parent == paths.VCAM / str(n)
    return False


def original_installed() -> bool:
    try:
        sub = r"Software\Classes\CLSID\{" + str(obsfiles.ORIGINAL_VCAM_GUID).upper() + "}"
        winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY).Close()
        return True
    except OSError:
        return False
