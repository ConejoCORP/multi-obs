"""Enregistrement des caméras virtuelles « VCam Rouge », « VCam Bleu »… dans Windows.

On reproduit exactement l'enregistrement fait par l'installeur d'OBS, mais avec un
CLSID et un nom par caméra, en pointant vers les modules patchés de data/vcam/N.

Enregistrement pour l'utilisateur (HKCU, sans droits admin) : vu par Discord, le navigateur… Mais un logiciel
lancé en administrateur (TikTok LIVE Studio se lance toujours ainsi) ne voit que les caméras enregistrées pour
tout le PC (HKLM). Option « logiciels lancés en administrateur » : une copie des modules dans Program Files
(modifiable seulement par un administrateur, comme la caméra d'OBS, puisque ces logiciels les chargent) et
le même CLSID enregistré pour tout le PC. Les logiciels lancés normalement gardent l'enregistrement utilisateur,
qui passe en premier.
"""
import base64
import hashlib
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
    """Retire les caméras pour l'utilisateur, puis celles enregistrées pour tout le PC (Windows demande alors
    l'autorisation ; refusée, elles restent : on le note dans le journal)."""
    with _lock:
        regs = [n for n in indices if _inproc_path(winreg.HKEY_CURRENT_USER, n)]
        if regs:
            _run_reg_import(_reg_file(regs, SCOPE_USER, remove=True), elevated=False)
    if any(n in indices for n in admin_registered()) or admin_dir().exists():
        try:
            uninstall_admin()
        except ElevationCancelled:
            from . import applog
            applog.log("Caméras virtuelles pour tout le PC non retirées : autorisation de Windows refusée")
    with _lock:
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


# --------------------------------------------------------------------------- #
# Copie pour tout le PC : logiciels lancés en administrateur (TikTok LIVE Studio…)
# --------------------------------------------------------------------------- #

ADMIN_FILES = ("obs-virtualcam-module64.dll", "obs-virtualcam-module32.dll", "placeholder.png")
_VIEWS = ((winreg.KEY_WOW64_64KEY, ADMIN_FILES[0]), (winreg.KEY_WOW64_32KEY, ADMIN_FILES[1]))
ERROR_CANCELLED = 1223   # demande d'autorisation de Windows refusée


class ElevationCancelled(RuntimeError):
    """L'utilisateur a refusé la demande d'autorisation de Windows."""


def admin_dir() -> Path:
    """Program Files\\Multi OBS\\VCam : seul un administrateur peut y écrire (comme pour la caméra d'OBS)."""
    pf = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files"
    return Path(pf) / "Multi OBS" / "VCam"


def _hklm(sub: str, value: str, view: int) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub, 0, winreg.KEY_READ | view) as k:
            return winreg.QueryValueEx(k, value)[0]
    except OSError:
        return None


def _clsid_sub(n: int) -> str:
    return rf"SOFTWARE\Classes\CLSID\{_guid_str(n)}"


def _instance_sub(n: int) -> str:
    return rf"SOFTWARE\Classes\CLSID\{CATEGORY}\Instance\{_guid_str(n)}"


def admin_registered() -> list[int]:
    """Caméras de Multi OBS enregistrées pour tout le PC, quel que soit leur module."""
    out = []
    for n in range(1, obsfiles.MAX_VCAM + 1):
        for view, _dll in _VIEWS:
            if _hklm(_clsid_sub(n) + r"\InprocServer32", "", view) is not None \
                    or _hklm(_instance_sub(n), "FriendlyName", view) is not None:
                out.append(n)
                break
    return out


def admin_up_to_date(n: int, name: str) -> bool:
    """Enregistrée pour tout le PC sous ce nom, vers sa copie de Program Files, identique à data/vcam/n."""
    d = admin_dir() / str(n)
    for view, dll in _VIEWS:
        path = _hklm(_clsid_sub(n) + r"\InprocServer32", "", view)
        if not path or os.path.normcase(os.path.normpath(path)) != os.path.normcase(str(d / dll)):
            return False
        if _hklm(_instance_sub(n), "FriendlyName", view) != name:
            return False
    try:
        return all((d / f).read_bytes() == (paths.VCAM / str(n) / f).read_bytes() for f in ADMIN_FILES)
    except OSError:
        return False


def admin_pending(names: dict[int, str]) -> tuple[list[int], list[int]]:
    """(caméras à installer ou mettre à jour pour tout le PC, caméras de Multi OBS à y retirer).
    Une caméra dont les modules ne sont pas encore dans data/vcam (instance jamais allumée) attend."""
    ready = {n: name for n, name in names.items()
             if all((paths.VCAM / str(n) / f).exists() for f in ADMIN_FILES)}
    install = sorted(n for n, name in ready.items() if not admin_up_to_date(n, name))
    remove = sorted(n for n in admin_registered() if n not in names)
    return install, remove


def _ps_quote(s: str) -> str:
    """Chaîne PowerShell entre apostrophes (PowerShell prend aussi ‘ ’ ‚ ‛ pour des apostrophes)."""
    for q in "'‘’‚‛":
        s = s.replace(q, q * 2)
    return f"'{s}'"


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest().upper()


_ADMIN_PS = r"""
$ErrorActionPreference = 'Stop'
$H = @HIVE@; $C = @CLASSES@; $R = @ROOT@; $cat = '@CAT@'
$F = [byte[]](@FILTER@)
function MobKey($v) { [Microsoft.Win32.RegistryKey]::OpenBaseKey($H, $v) }
function MobDel($g) {
  foreach ($v in 'Registry64', 'Registry32') {
    $b = MobKey $v; $b.DeleteSubKeyTree("$C\CLSID\$g", $false); $b.DeleteSubKeyTree("$C\CLSID\$cat\Instance\$g", $false); $b.Close()
  }
}
function MobPut($src, $dst, $h) {
  if (Test-Path -LiteralPath $dst) {
    if ((Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash -eq $h) { return }
    try { Remove-Item -LiteralPath $dst -Force }
    catch { Rename-Item -LiteralPath $dst -NewName ((Split-Path $dst -Leaf) + '.' + [guid]::NewGuid().ToString('N') + '.old') -Force }
  }
  Copy-Item -LiteralPath $src -Destination $dst -Force
  if ((Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash -ne $h) { Remove-Item -LiteralPath $dst -Force; throw "copie invalide : $dst" }
}
function MobAdd($n, $g, $name, $src, $h64, $h32, $hpng) {
  $d = Join-Path $R $n
  New-Item -ItemType Directory -Force -Path $d | Out-Null
  MobPut (Join-Path $src 'obs-virtualcam-module64.dll') (Join-Path $d 'obs-virtualcam-module64.dll') $h64
  MobPut (Join-Path $src 'obs-virtualcam-module32.dll') (Join-Path $d 'obs-virtualcam-module32.dll') $h32
  MobPut (Join-Path $src 'placeholder.png') (Join-Path $d 'placeholder.png') $hpng
  foreach ($v in 'Registry64', 'Registry32') {
    if ($v -eq 'Registry64') { $dll = 'obs-virtualcam-module64.dll' } else { $dll = 'obs-virtualcam-module32.dll' }
    $b = MobKey $v
    $k = $b.CreateSubKey("$C\CLSID\$g"); $k.SetValue('', $name); $k.Close()
    $k = $b.CreateSubKey("$C\CLSID\$g\InprocServer32"); $k.SetValue('', (Join-Path $d $dll)); $k.SetValue('ThreadingModel', 'Both'); $k.Close()
    $k = $b.CreateSubKey("$C\CLSID\$cat\Instance\$g"); $k.SetValue('FriendlyName', $name); $k.SetValue('CLSID', $g)
    $k.SetValue('FilterData', $F, [Microsoft.Win32.RegistryValueKind]::Binary); $k.Close()
    $b.Close()
  }
}
function MobDrop($n, $g) { MobDel $g; Remove-Item -LiteralPath (Join-Path $R $n) -Recurse -Force -ErrorAction SilentlyContinue }
function MobClean() {
  if (Test-Path -LiteralPath $R) { Get-ChildItem -LiteralPath $R -Recurse -Filter '*.old' | Remove-Item -Force -ErrorAction SilentlyContinue }
}
function MobPurge() {
  Remove-Item -LiteralPath $R -Recurse -Force -ErrorAction SilentlyContinue
  $P = Split-Path $R
  if ((Test-Path -LiteralPath $P) -and -not (Get-ChildItem -LiteralPath $P -Force)) { Remove-Item -LiteralPath $P -Force -ErrorAction SilentlyContinue }
}
try {
  MobClean
@CALLS@
  exit 0
} catch { exit 2 }
"""


def admin_script(add: dict[int, str], drop: list[int], purge: bool = False, *, hive: str = "LocalMachine",
                 classes: str = r"SOFTWARE\Classes", root: Path | None = None) -> str:
    """Script PowerShell (lancé en administrateur) : copie les modules de data/vcam/n dans Program Files en
    vérifiant leur empreinte, enregistre les caméras `add` pour tout le PC (64 et 32 bits), retire `drop`.
    hive / classes / root : seulement pour les tests (clé de l'utilisateur, dossier temporaire)."""
    calls = []
    for n, name in sorted(add.items()):
        src = paths.VCAM / str(n)
        hashes = " ".join(_sha256(src / f) for f in ADMIN_FILES)
        calls.append(f"  MobAdd {n} '{_guid_str(n)}' {_ps_quote(name)} {_ps_quote(str(src))} {hashes}")
    calls += [f"  MobDrop {n} '{_guid_str(n)}'" for n in sorted(drop)]
    if purge:
        calls.append("  MobPurge")
    return (_ADMIN_PS.replace("@HIVE@", _ps_quote(hive)).replace("@CLASSES@", _ps_quote(classes))
            .replace("@ROOT@", _ps_quote(str(root or admin_dir()))).replace("@CAT@", CATEGORY)
            .replace("@FILTER@", ",".join(f"0x{b:02x}" for b in FILTER_DATA))
            .replace("@CALLS@", "\n".join(calls) or "  # rien"))


def run_powershell(script: str, elevated: bool = True, _runas: bool = True) -> int:
    """Lance le script (passé encodé : aucun fichier qu'un autre programme pourrait modifier entre-temps).
    elevated : via la demande d'autorisation de Windows (« Windows PowerShell »), ERROR_CANCELLED si refusée.
    _runas=False : même chemin sans la demande d'autorisation (tests)."""
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    args = ["-NoProfile", "-NonInteractive", "-EncodedCommand", enc]
    if elevated:
        outer = ("try { $p = Start-Process -FilePath powershell.exe " + ("-Verb RunAs " if _runas else "")
                 + "-WindowStyle Hidden -Wait -PassThru -ArgumentList " + ",".join(f"'{a}'" for a in args)
                 + f"; exit $p.ExitCode }} catch {{ exit {ERROR_CANCELLED} }}")
        args = ["-NoProfile", "-NonInteractive", "-Command", outer]
    if len(" ".join(args)) > 30_000:   # limite d'une ligne de commande Windows : 32 767 caractères
        raise RuntimeError("trop de caméras d'un coup pour la ligne de commande")
    r = subprocess.run(["powershell", *args], creationflags=subprocess.CREATE_NO_WINDOW, check=False,
                       capture_output=True)
    return r.returncode


def install_admin(names: dict[int, str], colors: dict[int, str] | None = None) -> list[int]:
    """Installe ou met à jour la copie pour tout le PC des caméras `names` (et retire les autres caméras de
    Multi OBS enregistrées pour tout le PC), en une seule demande d'autorisation. Renvoie les caméras encore
    pas à jour ensuite. Lève ElevationCancelled si la demande a été refusée."""
    with _lock:
        for n in names:
            if not all((paths.VCAM / str(n) / f).exists() for f in ADMIN_FILES) \
                    and obsfiles.runtime_signature() is not None:
                obsfiles.build_vcam_modules(n, (colors or {}).get(n))
            elif colors and n in colors:
                obsfiles.set_vcam_placeholder(n, colors[n])
        add, drop = admin_pending(names)
        if not add and not drop:
            return []
        code = run_powershell(admin_script({n: names[n] for n in add}, drop))
        if code == ERROR_CANCELLED:
            raise ElevationCancelled()
        add, drop = admin_pending(names)
        return sorted(set(add) | set(drop))


def uninstall_admin() -> bool:
    """Retire toutes les caméras de Multi OBS enregistrées pour tout le PC et leur dossier de Program Files.
    Lève ElevationCancelled si la demande d'autorisation a été refusée."""
    with _lock:
        drop = admin_registered()
        if not drop and not admin_dir().exists():
            return True
        code = run_powershell(admin_script({}, drop, purge=True))
        if code == ERROR_CANCELLED:
            raise ElevationCancelled()
        return not admin_registered()
