"""Définition d'une instance et écriture de sa configuration OBS portable."""
import configparser
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from . import paths, theme

DEFAULT_FILENAME_FORMAT = "%CCYY-%MM-%DD %hh-%mm-%ss"
CONFIG_IGNORE = shutil.ignore_patterns(
    "logs", "crashes", "profiler_data", "updates", ".sentinel",
    "Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache",
)


@dataclass
class InstanceDef:
    id: str
    name: str
    color: str
    vcam: int
    port: int
    password: str
    start_minimized: bool = True
    include_global: bool = True
    needs_default_audio: bool = False   # instance vierge : ajouter Audio du bureau + Micro
    rec_type: str = "cam"               # clé du type (cam, ecran, live… ou perso), utilisée dans le nom des fichiers
    preview_hidden: bool = False        # aperçu masqué sur la carte (aucune ressource utilisée)
    card_minimized: bool = False        # carte rangée dans la barre « Instances réduites » en bas
    chat_platform: str = ""             # bouton Chat (OBS du live) : twitch / youtube / multi ("" : par défaut)

    @property
    def dir(self) -> Path:
        return paths.INSTANCES / self.id

    @property
    def config_dir(self) -> Path:
        return self.dir / "config" / "obs-studio"

    @property
    def exe(self) -> Path:
        return self.dir / paths.OBS_EXE_REL

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "InstanceDef":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


def _obs_unescape(value: str) -> str:
    """OBS échappe \\, retour à la ligne et retour chariot dans ses .ini."""
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value) and value[i + 1] in "\\nr":
            out.append({"\\": "\\", "n": "\n", "r": "\r"}[value[i + 1]])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _obs_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")


class Ini:
    """Fichier .ini d'OBS (clé=valeur, sans espaces, ordre conservé, échappement d'OBS)."""

    def __init__(self, path: Path):
        self.path = path
        self.cp = configparser.ConfigParser(interpolation=None, strict=False, delimiters=("=",))
        self.cp.optionxform = str
        if path.exists():
            try:
                self.cp.read(path, encoding="utf-8-sig")
            except configparser.Error:
                pass

    def get(self, section: str, key: str, default=None):
        value = self.cp.get(section, key, fallback=None)
        return default if value is None else _obs_unescape(value)

    def set(self, section: str, key: str, value) -> None:
        if not self.cp.has_section(section):
            self.cp.add_section(section)
        self.cp.set(section, key, _obs_escape(str(value)))

    def remove_section(self, section: str) -> None:
        self.cp.remove_section(section)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            self.cp.write(f, space_around_delimiters=False)


def safe_filename(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]+', "", name).strip()
    return s or "Instance"


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", safe_filename(name)).strip("_")
    return s or "Instance"


def copy_tree_tolerant(src: Path, dst: Path, ignore=CONFIG_IGNORE) -> list[str]:
    """Comme copytree, mais saute les fichiers verrouillés (OBS ouvert : cookies du navigateur…)."""
    try:
        shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)
        return []
    except shutil.Error as e:
        return [err[0] for err in e.args[0]]


def import_user_config(src: Path, dst: Path) -> None:
    """Copie scènes, profils et réglages de l'OBS habituel dans une instance."""
    dst.mkdir(parents=True, exist_ok=True)
    if (src / "basic").exists():
        copy_tree_tolerant(src / "basic", dst / "basic")
    if (src / "plugin_config").exists():
        copy_tree_tolerant(src / "plugin_config", dst / "plugin_config",
                           shutil.ignore_patterns("obs-websocket", "Cache", "Code Cache", "GPUCache"))
    for f in ("user.ini", "global.ini"):
        if (src / f).exists():
            shutil.copy2(src / f, dst / f)


def record_directory(inst: "InstanceDef") -> Path:
    """Dossier d'enregistrement du profil actif, lu dans la config (instance arrêtée)."""
    cfg = inst.config_dir
    default = Path.home() / "Videos"
    profile = Ini(cfg / "user.ini").get("Basic", "ProfileDir")
    if not profile:
        return default
    ini = Ini(cfg / "basic" / "profiles" / profile / "basic.ini")
    if (ini.get("Output", "Mode") or "Simple").lower() == "advanced":
        if (ini.get("AdvOut", "RecType") or "Standard") == "FFmpeg":
            value = ini.get("AdvOut", "FFFilePath")
        else:
            value = ini.get("AdvOut", "RecFilePath")
    else:
        value = ini.get("SimpleOutput", "FilePath")
    return Path(value) if value else default


def _nvenc_available(obs_bin: Path) -> bool:
    exe = obs_bin / "obs-nvenc-test.exe"
    if not exe.exists():
        return False
    try:
        out = subprocess.run([str(exe)], capture_output=True, text=True, timeout=15,
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "nvenc_supported=true" in out


def _create_profile(cfg: Path, name: str, user: Ini, obs_bin: Path | None = None) -> None:
    slug = _slug(name)
    pdir = cfg / "basic" / "profiles" / slug
    ini = Ini(pdir / "basic.ini")
    ini.set("General", "Name", name)
    ini.set("Output", "Mode", "Simple")
    ini.set("Output", "FilenameFormatting", f"{safe_filename(name)} {DEFAULT_FILENAME_FORMAT}")
    # MKV : supporte la pause (pas le MP4 hybride) et ne se corrompt pas en cas de plantage.
    # OBS le remuxe en MP4 automatiquement à la fin de l'enregistrement.
    ini.set("SimpleOutput", "RecFormat2", "mkv")
    ini.set("AdvOut", "RecFormat2", "mkv")
    ini.set("Video", "AutoRemux", "true")
    # « Identique au stream » (défaut d'OBS) désactive la pause : haute qualité, taille moyenne.
    ini.set("SimpleOutput", "RecQuality", "Small")
    if obs_bin and _nvenc_available(obs_bin):
        # Encodage par la carte graphique NVIDIA : 3 instances en x264 chargeraient trop le
        # processeur, et sur un PC avec puce AMD intégrée OBS proposerait l'encodeur AMD.
        ini.set("SimpleOutput", "StreamEncoder", "nvenc")
        ini.set("SimpleOutput", "RecEncoder", "nvenc")
        # Mode avancé : NVENC aussi, avec un encodeur d'enregistrement à part (la pause marche).
        ini.set("AdvOut", "Encoder", "obs_nvenc_h264_tex")
        ini.set("AdvOut", "RecEncoder", "obs_nvenc_h264_tex")
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "streamEncoder.json").write_text(
            json.dumps({"rate_control": "CBR", "bitrate": 6000, "preset2": "p5"}), "utf-8")
        (pdir / "recordEncoder.json").write_text(
            json.dumps({"rate_control": "CQP", "cqp": 20, "preset2": "p5"}), "utf-8")
    ini.save()
    user.set("Basic", "Profile", name)
    user.set("Basic", "ProfileDir", slug)


def apply_profile_output(cfg: Path, filename_format: str, auto_remux: bool) -> None:
    """Nom des fichiers + conversion MKV -> MP4 automatique, dans tous les profils de l'instance."""
    profiles = cfg / "basic" / "profiles"
    if not profiles.exists():
        return
    for basic in profiles.glob("*/basic.ini"):
        ini = Ini(basic)
        changed = False
        if filename_format and ini.get("Output", "FilenameFormatting") != filename_format:
            ini.set("Output", "FilenameFormatting", filename_format)
            changed = True
        want = "true" if auto_remux else "false"
        if ini.get("Video", "AutoRemux") != want:
            ini.set("Video", "AutoRemux", want)
            changed = True
        if changed:
            ini.save()


def write_websocket_config(inst: InstanceDef) -> None:
    p = inst.config_dir / "plugin_config" / "obs-websocket" / "config.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    try:
        data = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        pass
    data.update({
        "alerts_enabled": False,
        "auth_required": True,
        "first_load": False,
        "server_enabled": True,
        "server_password": inst.password,
        "server_port": inst.port,
    })
    p.write_text(json.dumps(data, indent=2), "utf-8")


def ensure_config(inst: InstanceDef, import_from: Path | None = None,
                  filename_format: str = "", auto_remux: bool = True) -> None:
    cfg = inst.config_dir
    fresh = not (cfg / "user.ini").exists()
    cfg.mkdir(parents=True, exist_ok=True)
    if fresh and import_from and (import_from / "user.ini").exists():
        import_user_config(import_from, cfg)

    themes = cfg / "themes"
    themes.mkdir(exist_ok=True)
    (themes / f"MultiOBS_{inst.id}.ovt").write_text(
        theme.instance_ovt(inst.id, inst.name, inst.color), "utf-8")

    user = Ini(cfg / "user.ini")
    if fresh:
        for k in ("Pre19Defaults", "Pre21Defaults", "Pre23Defaults", "Pre24.1Defaults"):
            if user.get("General", k) is None:
                user.set("General", k, "false")
    user.set("General", "FirstRun", "true")           # pas d'assistant de config auto
    # La confirmation de fermeture est faite par Multi OBS : celle d'OBS s'ouvrirait
    # dans une fenêtre réduite, invisible, et bloquerait la fermeture.
    user.set("General", "ConfirmOnExit", "false")
    user.set("Appearance", "Theme", theme.instance_theme_id(inst.id))
    profile_dir = user.get("Basic", "ProfileDir")
    if not profile_dir or not (cfg / "basic" / "profiles" / profile_dir / "basic.ini").exists():
        _create_profile(cfg, inst.name, user, inst.exe.parent)
    user.save()

    glob = Ini(cfg / "global.ini")
    glob.remove_section("Locations")                    # sinon la config repointerait vers %APPDATA%
    glob.set("General", "EnableAutoUpdates", "false")  # les mises à jour passent par Multi OBS
    glob.set("General", "Pre31Migrated", "true")
    glob.save()

    write_websocket_config(inst)
    apply_profile_output(cfg, filename_format, auto_remux)
