"""Copie d'OBS, construction des installations portables par instance et patch des DLL.

Chaque instance est une installation OBS portable complète (dossier `config/` à
elle), mais les fichiers du programme sont des liens physiques vers une copie de
référence (`data/runtime`) : 3 instances ne prennent donc pas 3 x 450 Mo.

Seul `win-dshow.dll` est une vraie copie, patchée pour que l'instance envoie sa
caméra virtuelle dans sa propre file mémoire (« OBS Virtual Camera N »).
"""
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Callable

from . import paths

Progress = Callable[[int, int, str], None]

ORIGINAL_VCAM_GUID = uuid.UUID("A3FCE0F5-3493-419F-958A-ABA1250EC20B")
_GUID_NAMESPACE = uuid.UUID("6b1f3e2a-8a77-4c55-9d7e-0b5d6c3f9a10")

WINDSHOW_REL = Path("obs-plugins") / "64bit" / "win-dshow.dll"
SYNC_PLUGIN_REL = Path("obs-plugins") / "64bit" / "multiobs-sync.dll"
MODULE_DIR_REL = Path("data") / "obs-plugins" / "win-dshow"
# OBS 33 et plus : les modules officiels sont dans core/<module>/ (données dans core/<module>/data),
# et ceux d'une installation portable dans plugins/<module>/<module>.dll.
CORE_WINDSHOW_REL = Path("core") / "win-dshow" / "win-dshow.dll"
CORE_MODULE_DIR_REL = Path("core") / "win-dshow" / "data"
CORE_SYNC_PLUGIN_REL = Path("plugins") / "multiobs-sync" / "multiobs-sync.dll"
PORTABLE_MARKER = "obs_portable_mode.txt"
TREE_MARKER = ".multiobs_tree.json"
RUNTIME_MARKER = ".multiobs_runtime.json"
SKIP_TOP = {"config", PORTABLE_MARKER, "portable_mode.txt", TREE_MARKER, RUNTIME_MARKER}

MAX_VCAM = 9
TREE_VERSION = 2


class PatchError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Identité des caméras virtuelles
# --------------------------------------------------------------------------- #

def vcam_guid(n: int) -> uuid.UUID:
    return uuid.uuid5(_GUID_NAMESPACE, f"multiobs-vcam-{n}")


def vcam_name(n: int) -> str:
    return f"OBS Virtual Camera {n}"


def vcam_res_name(n: int) -> str:
    """Fichier de %APPDATA% où l'OBS de la caméra n note sa taille et sa cadence, et où le module de la
    caméra les relit quand cet OBS est fermé (« obs-virtualcam.txt » pour l'OBS habituel, 18 caractères)."""
    return f"obs-vcam-mob{n:02d}.txt"


def core_layout(root: Path) -> bool:
    """OBS 33 ou plus : modules officiels rangés dans core/."""
    return (root / CORE_WINDSHOW_REL).exists()


def windshow_rel(root: Path) -> Path:
    return CORE_WINDSHOW_REL if core_layout(root) else WINDSHOW_REL


def module_dir(root: Path | None = None) -> Path:
    """Dossier des modules DirectShow de la caméra virtuelle d'OBS (et de son image d'attente)."""
    root = root or paths.RUNTIME
    return root / (CORE_MODULE_DIR_REL if core_layout(root) else MODULE_DIR_REL)


def _w(s: str) -> bytes:
    return s.encode("utf-16-le")


def _replace(data: bytes, old: bytes, new: bytes, what: str, required: bool = True) -> bytes:
    if len(old) != len(new):
        raise PatchError(f"longueurs différentes pour {what}")
    if old not in data:
        if required:
            raise PatchError(f"motif introuvable : {what} (version d'OBS non prise en charge ?)")
        return data
    return data.replace(old, new)


def _common_patches(data: bytes, n: int) -> bytes:
    data = _replace(data, ORIGINAL_VCAM_GUID.bytes_le, vcam_guid(n).bytes_le, "GUID caméra virtuelle")
    # Nom de la file mémoire partagée entre OBS et le module DirectShow (18 caractères).
    data = _replace(data, _w("OBSVirtualCamVideo"), _w(f"MultiOBS_VCamVid{n:02d}"), "file vidéo")
    return data


def patch_windshow(data: bytes, n: int) -> bytes:
    data = _common_patches(data, n)
    # Taille et cadence notées au démarrage de la caméra virtuelle : dans le fichier de la caméra n, que son
    # module relit quand l'OBS est fermé (sinon ce module ne propose aucun format et Discord charge à l'infini).
    return _replace(data, b"obs-virtualcam.txt", vcam_res_name(n).encode("ascii"), "fichier de résolution",
                    required=False)


def patch_vcam_module(data: bytes, n: int) -> bytes:
    data = _common_patches(data, n)
    # Fichier où le module relit la taille et la cadence de son OBS (19 caractères).
    data = _replace(data, _w("\\obs-virtualcam.txt"), _w("\\" + vcam_res_name(n)),
                    "fichier de résolution", required=False)
    # Nom interne du filtre (on garde la longueur, le reste est rempli de zéros).
    old = _w("OBS Virtual Camera") + b"\x00\x00"
    new = _w(f"OBS Virtual Cam {n}").ljust(len(old), b"\x00")
    data = _replace(data, old, new, "nom du filtre", required=False)
    # Le module affiche l'image d'attente au lieu du flux quand il est chargé dans
    # obs64.exe (anti-boucle). On le neutralise pour qu'une instance puisse
    # capturer la caméra virtuelle d'une autre instance.
    data = _replace(data, _w("obs64.exe"), _w("obs64.ex_"), "détection OBS 64", required=False)
    data = _replace(data, _w("obs32.exe"), _w("obs32.ex_"), "détection OBS 32", required=False)
    return data


# --------------------------------------------------------------------------- #
# Copie de référence d'OBS
# --------------------------------------------------------------------------- #

def source_signature(src: Path) -> dict:
    exe = src / paths.OBS_EXE_REL
    core = src / "bin" / "64bit" / "obs.dll"
    sig = {"source": str(src), "version": paths.file_version(exe)}
    for key, f in (("exe", exe), ("core", core)):
        try:
            st = f.stat()
            sig[key] = [st.st_size, int(st.st_mtime)]
        except OSError:
            sig[key] = None
    return sig


def runtime_signature() -> dict | None:
    try:
        return json.loads((paths.RUNTIME / RUNTIME_MARKER).read_text("utf-8"))
    except (OSError, ValueError):
        return None


_remux_titles: frozenset[str] | None = None


def remux_titles() -> frozenset[str]:
    """Titres de la fenêtre de conversion d'OBS dans toutes ses langues (« Convertir des enregistrements »…)."""
    global _remux_titles
    if _remux_titles is None:
        titles = {"Remux Recordings", "Convertir des enregistrements"}
        for f in (paths.RUNTIME / "data" / "obs-studio" / "locale").glob("*.ini"):
            try:
                for line in f.read_text("utf-8", errors="replace").splitlines():
                    if line.startswith("RemuxRecordings="):
                        titles.add(line.split("=", 1)[1].strip().strip('"'))
                        break
            except OSError:
                continue
        _remux_titles = frozenset(titles)
    return _remux_titles


def runtime_status(src: Path | None) -> str:
    """'missing', 'outdated' ou 'ok'."""
    sig = runtime_signature()
    if not sig or not (paths.RUNTIME / paths.OBS_EXE_REL).exists():
        return "missing"
    if src is None:
        return "ok"
    cur = source_signature(src)
    same = all(sig.get(k) == cur.get(k) for k in ("version", "exe", "core"))
    return "ok" if same else "outdated"


def _list_files(root: Path) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        if rel_dir == Path("."):
            dirnames[:] = [d for d in dirnames if d not in SKIP_TOP]
            filenames = [f for f in filenames if f not in SKIP_TOP]
        for f in filenames:
            out.append(rel_dir / f)
    return out


def sync_runtime(src: Path, progress: Progress) -> None:
    """Copie l'installation d'OBS dans data/runtime (remplace l'ancienne copie)."""
    files = _list_files(src)
    tmp = paths.RUNTIME.with_name("runtime.new")
    if tmp.exists():
        shutil.rmtree(tmp)
    total = len(files)
    for i, rel in enumerate(files):
        if i % 25 == 0:
            progress(i, total, f"Copie d'OBS… {rel.as_posix()}")
        dst = tmp / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, dst)
    (tmp / RUNTIME_MARKER).write_text(json.dumps(source_signature(src), indent=2), "utf-8")
    old = paths.RUNTIME.with_name("runtime.old")
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    if paths.RUNTIME.exists():
        paths.RUNTIME.rename(old)
    tmp.rename(paths.RUNTIME)
    shutil.rmtree(old, ignore_errors=True)
    progress(total, total, "Copie d'OBS terminée")


# --------------------------------------------------------------------------- #
# Installation portable d'une instance
# --------------------------------------------------------------------------- #

def tree_info(inst_dir: Path) -> dict | None:
    try:
        return json.loads((inst_dir / TREE_MARKER).read_text("utf-8"))
    except (OSError, ValueError):
        return None


def tree_is_current(inst_dir: Path, vcam: int) -> bool:
    info = tree_info(inst_dir)
    rt = runtime_signature()
    # Avant la version 2, le win-dshow d'OBS 33 (rangé dans core/) n'était pas patché.
    return bool(info and rt and info.get("runtime") == rt and info.get("vcam") == vcam
                and (inst_dir / paths.OBS_EXE_REL).exists()
                and (info.get("tree", 1) >= TREE_VERSION or not core_layout(inst_dir)))


def clear_tree(inst_dir: Path) -> None:
    """Supprime les fichiers du programme d'une instance, en gardant sa config."""
    if not inst_dir.exists():
        return
    for child in inst_dir.iterdir():
        if child.name == "config":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def build_tree(inst_dir: Path, vcam: int, progress: Progress) -> None:
    rt = runtime_signature()
    if not rt:
        raise RuntimeError("La copie d'OBS est absente.")
    clear_tree(inst_dir)
    inst_dir.mkdir(parents=True, exist_ok=True)
    files = _list_files(paths.RUNTIME)
    windshow = windshow_rel(paths.RUNTIME)
    total = len(files)
    for i, rel in enumerate(files):
        if i % 100 == 0:
            progress(i, total, f"Préparation de l'instance… {i}/{total}")
        src = paths.RUNTIME / rel
        dst = inst_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if rel == windshow:
            dst.write_bytes(patch_windshow(src.read_bytes(), vcam))
            continue
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
    (inst_dir / PORTABLE_MARKER).write_text("", "utf-8")
    (inst_dir / TREE_MARKER).write_text(json.dumps({"runtime": rt, "vcam": vcam, "tree": TREE_VERSION}, indent=2),
                                        "utf-8")
    install_sync_plugin(inst_dir)
    progress(total, total, "Instance prête")


def refresh_windshow(inst_dir: Path, vcam: int) -> None:
    """Re-patche le win-dshow.dll d'une instance déjà construite quand le patch a changé (Multi OBS mis à jour)."""
    rel = windshow_rel(paths.RUNTIME)
    src, dst = paths.RUNTIME / rel, inst_dir / rel
    try:
        data = patch_windshow(src.read_bytes(), vcam)
        if dst.exists() and dst.read_bytes() != data:
            dst.write_bytes(data)
    except (OSError, PatchError):
        pass   # OBS ouvert (DLL chargée) : au prochain allumage


def install_sync_plugin(inst_dir: Path) -> None:
    """Ajoute à l'instance le module OBS qui permet d'aligner les recs à l'image près (framesync.py)."""
    core = core_layout(inst_dir)   # faux aussi tant que l'instance n'est pas construite
    src, dst = paths.SYNC_PLUGIN, inst_dir / (CORE_SYNC_PLUGIN_REL if core else SYNC_PLUGIN_REL)
    if not src.exists() or not (core or dst.parent.is_dir()):
        return
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        if not dst.exists() or dst.read_bytes() != data:
            dst.write_bytes(data)
    except OSError:
        pass   # OBS ouvert (DLL chargée) : la nouvelle version sera posée au prochain allumage


def build_vcam_modules(n: int, color: str | None = None) -> Path:
    """Écrit les modules DirectShow patchés de la caméra n dans data/vcam/n."""
    src = module_dir()
    if not (src / "obs-virtualcam-module64.dll").exists():
        raise RuntimeError("La caméra virtuelle d'OBS est absente de la copie d'OBS "
                           f"({src / 'obs-virtualcam-module64.dll'}).")
    out = paths.VCAM / str(n)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("obs-virtualcam-module64.dll", "obs-virtualcam-module32.dll"):
        data = (src / name).read_bytes()
        target = out / name
        patched = patch_vcam_module(data, n)
        try:
            target.write_bytes(patched)
        except PermissionError:
            # DLL chargée par une appli (Discord…) : on ne la remplace que si elle a changé.
            if target.read_bytes() != patched:
                raise
    set_vcam_placeholder(n, color)
    return out


def set_vcam_placeholder(n: int, color: str | None) -> None:
    """Image affichée par la caméra virtuelle quand elle est éteinte, à la couleur de l'instance."""
    out = paths.VCAM / str(n)
    if not out.exists():
        return
    colored = paths.ASSETS / "placeholders" / f"{color}.png" if color else None
    src = colored if colored and colored.exists() else module_dir() / "placeholder.png"
    target = out / "placeholder.png"
    try:
        if src.exists() and (not target.exists() or target.read_bytes() != src.read_bytes()):
            shutil.copyfile(src, target)
    except OSError:
        pass   # fichier ouvert par une appli : il sera mis à jour la prochaine fois
