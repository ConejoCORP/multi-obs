"""État global de Multi OBS : instances, réglages, préconfigs."""
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import applog, naming, obsfiles, paths, theme, vcam, winapi
from .controller import InstanceController
from .instance_config import InstanceDef, copy_tree_tolerant, ensure_config

BASE_PORT = 4461
AUTOSAVE_SLUG = "_derniere_session"

DEFAULT_SETTINGS = {
    "obs_source": "",
    "preview_fps": 60,
    "preview_divisor": 4,
    "preview_background_fps": -1,     # -1 : comme au premier plan
    "live_color": "purple",           # pastille LIVE de l'aperçu et de la carte (le rec reste rouge)
    "taskbar_rename": True,
    "check_updates": True,
    "on_close": "ask",                # instances allumées à la fermeture : ask / keep / stop
    "rec_types": [["cam", "Cam"], ["ecran", "Écran"], ["live", "Live"]],   # modifiables dans les paramètres
    "skipped_version": "",
    "hotkeys_enabled": False,        # raccourcis globaux : à activer dans les paramètres
    "hotkey_record": "Ctrl+Alt+R",
    "hotkey_pause": "Ctrl+Alt+P",
    "vcam_scope": vcam.SCOPE_USER,
    "current_preset": "",
    "creator": "",
    "twitch_channel": "",             # lien de la chaîne Twitch : bouton « Chat Twitch » sur l'OBS du live
    "youtube_channel": "",            # lien de la chaîne YouTube (ou @pseudo) : chat YouTube et multi-chat
    "chat_platform": "twitch",        # chat des OBS du live sans choix à eux (chacun choisit avec la flèche de son bouton)
    "chat_native_single": False,      # Twitch / YouTube seul : vrai chat du site au lieu de la fenêtre du chat
    "chat_feed": True,                # fenêtre du chat : fil d'actualité en bas (abonnements, raids, bits…)
    "chat_feed_pct": 20,              # … sur 20 % de la hauteur ; chat_feed_events : ce qu'il montre (absent : tout)
    "chat_font_pt": 11,               # fenêtre du chat : taille du texte, toujours devant, place de la fenêtre
    "chat_on_top": False,             # chat_geometries : place de la fenêtre du chat de chaque instance
    "creator_in_name": True,          # décoché : les fichiers n'ont pas ton nom (enregistrements solo)
    "video_name": "",
    "filename_template": naming.DEFAULT_TEMPLATE,
    "auto_remux": True,
    "trash_mkv_after_remux": False,
    "ask_video_name": False,            # fenêtre « Nom de la vidéo » au lancement d'un rec
    # Synchro à l'image près de « Tout enregistrer / Tout en pause / Tout arrêter » (paramètres → Enregistrements)
    "sync_start": True,                 # départ aligné (courte pause dans la première seconde)
    "sync_stop": True,                  # tous les recs finis sur la même image
    "sync_resume": True,                # fichiers recalés à chaque reprise après une pause
    "remote_enabled": True,             # pilotage par le plugin Stream Deck (serveur local, 127.0.0.1)
    # SafeGuard (onglet des paramètres) : surveillance des recs et des lives
    "silence_warn_s": 120,              # « Pas de son » après ce silence (0 : jamais)
    "drop_warn_frames": 10,             # avertissement « images perdues » à partir de ce total
    "low_disk_gb": 10,                  # alerte « disque presque plein » sous ce nombre de Go
    "alert_taskbar": True,              # bouton de Multi OBS qui clignote dans la barre des tâches
    "alert_streamdeck": True,           # cadre rouge et « ALERTE » sur les touches du Stream Deck
    "obs_priority": True,               # OBS en priorité « au-dessus de la normale » (rec d'un jeu)
    "obs_unthrottle": True,             # Windows ne bride pas les OBS réduits ou en arrière-plan
}


SETTINGS_VERSION = 2


def _migrate_v1(s: dict) -> None:
    """Réglages de la 1.0.0 / 1.0.1 : les valeurs restées sur les anciens défauts passent aux nouveaux
    (aperçu 60 i/s en 1/4, identique en arrière-plan ; raccourcis globaux désactivés)."""
    if (s.get("preview_fps"), s.get("preview_divisor"), s.get("preview_background_fps")) in \
            ((30, 2, 5), (30, 2, None)):
        s["preview_fps"], s["preview_divisor"], s["preview_background_fps"] = 60, 4, -1
    if s.get("hotkeys_enabled") is True:
        s["hotkeys_enabled"] = False


def _slugify(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    return s or "preconfig"


class Manager(QObject):
    instancesChanged = Signal()

    def __init__(self):
        super().__init__()
        paths.DATA.mkdir(parents=True, exist_ok=True)
        self.settings = dict(DEFAULT_SETTINGS)
        self.instances: list[InstanceDef] = []
        self._load()
        self.controllers: dict[str, InstanceController] = {}
        for inst in self.instances:
            self._make_controller(inst)

    # ------------------------------------------------------------------ #
    # Persistance
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        try:
            data = json.loads(paths.STATE_FILE.read_text("utf-8"))
        except (OSError, ValueError):
            return
        saved = data.get("settings") or {}
        migrate = data.get("settings_version", 1) < SETTINGS_VERSION
        if migrate:
            _migrate_v1(saved)
        if "drop_alert_always" in saved:   # case « dès la première image perdue » (1.1.0) → seuil SafeGuard
            saved.setdefault("drop_warn_frames", 1 if saved.pop("drop_alert_always") else 10)
        self.settings.update(saved)
        self.instances = [InstanceDef.from_dict(d) for d in data.get("instances") or []]
        if migrate:
            self.save()

    def save(self) -> None:
        data = {"settings_version": SETTINGS_VERSION, "settings": self.settings,
                "instances": [i.to_dict() for i in self.instances]}
        tmp = paths.STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        tmp.replace(paths.STATE_FILE)

    def _make_controller(self, inst: InstanceDef) -> InstanceController:
        c = InstanceController(inst, int(self.settings["preview_fps"]),
                               int(self.settings.get("preview_divisor", 4)),
                               int(self.settings.get("preview_background_fps", -1)), self)
        c.settings = self.settings
        c.defaultAudioAdded.connect(self._on_default_audio_added)
        c.obsReady.connect(lambda inst_id: self.apply_naming(inst_id))
        self.controllers[inst.id] = c
        return c

    def _on_default_audio_added(self, inst_id: str) -> None:
        for inst in self.instances:
            if inst.id == inst_id:
                inst.needs_default_audio = False
        self.save()

    # ------------------------------------------------------------------ #
    # Nom des enregistrements
    # ------------------------------------------------------------------ #
    def creator(self) -> str:
        """Nom mis dans les fichiers : vide si la case « Nom » est décochée (le nom reste gardé)."""
        return self.settings.get("creator", "") if self.settings.get("creator_in_name", True) else ""

    def filename_format(self, inst: InstanceDef) -> str:
        # Deux instances du même type enregistreraient sous le même nom : on les distingue.
        same_type = sum(1 for i in self.instances if i.rec_type == inst.rec_type)
        suffix = inst.name if same_type > 1 else ""
        return naming.obs_format(self.settings.get("filename_template"), self.creator(),
                                 inst.rec_type, self.settings.get("video_name", ""), suffix)

    def filename_preview(self, inst: InstanceDef) -> str:
        same_type = sum(1 for i in self.instances if i.rec_type == inst.rec_type)
        return naming.preview(self.settings.get("filename_template"), self.creator(),
                              inst.rec_type, self.settings.get("video_name", ""),
                              inst.name if same_type > 1 else "")

    def set_naming(self, creator: str | None = None, video: str | None = None,
                   template: str | None = None, creator_in_name: bool | None = None) -> None:
        if creator is not None:
            self.settings["creator"] = creator
        if creator_in_name is not None:
            self.settings["creator_in_name"] = bool(creator_in_name)
        if video is not None:
            self.settings["video_name"] = video
        if template is not None:
            self.settings["filename_template"] = template or naming.DEFAULT_TEMPLATE
        self.save()
        self.apply_naming()

    def set_rec_type(self, inst_id: str, rec_type: str) -> None:
        for inst in self.instances:
            if inst.id == inst_id:
                inst.rec_type = rec_type
        self.save()
        self.apply_naming()  # les doublons de type changent aussi le nom des autres

    def apply_naming(self, only_id: str | None = None) -> None:
        """Envoie le nom des fichiers et la conversion MP4 aux instances (en direct si ouvertes)."""
        auto_remux = bool(self.settings.get("auto_remux", True))
        for inst in self.instances:
            if only_id and inst.id != only_id:
                continue
            fmt = self.filename_format(inst)
            c = self.controllers.get(inst.id)
            if c and c.connected:
                for cat, name, value in (("Output", "FilenameFormatting", fmt),
                                         ("Video", "AutoRemux", "true" if auto_remux else "false")):
                    c.ws.call("SetProfileParameter", {"parameterCategory": cat, "parameterName": name,
                                                      "parameterValue": value})
            elif c and not c.running and inst.config_dir.exists():
                from .instance_config import apply_profile_output
                apply_profile_output(inst.config_dir, fmt, auto_remux)

    # ------------------------------------------------------------------ #
    # OBS source / copie de référence
    # ------------------------------------------------------------------ #
    def obs_source(self) -> Path | None:
        s = self.settings.get("obs_source")
        if s and (Path(s) / paths.OBS_EXE_REL).exists():
            return Path(s)
        return paths.find_obs_install()

    def runtime_status(self) -> str:
        return obsfiles.runtime_status(self.obs_source())

    def any_running(self) -> bool:
        return any(c.running for c in self.controllers.values())

    # ------------------------------------------------------------------ #
    # Instances
    # ------------------------------------------------------------------ #
    def controller(self, inst_id: str) -> InstanceController:
        return self.controllers[inst_id]

    def free_vcams(self, exclude_id: str | None = None) -> list[int]:
        used = {i.vcam for i in self.instances if i.id != exclude_id}
        return [n for n in range(1, obsfiles.MAX_VCAM + 1) if n not in used]

    def next_color(self) -> str:
        used = {i.color for i in self.instances}
        for c in theme.ACCENT_ORDER:
            if c not in used:
                return c
        return theme.ACCENT_ORDER[len(self.instances) % len(theme.ACCENT_ORDER)]

    def new_definition(self, name: str, color: str, vcam_index: int) -> InstanceDef:
        ids = {i.id for i in self.instances}
        if paths.INSTANCES.exists():
            ids |= {p.name for p in paths.INSTANCES.iterdir()}
        n = 1
        while f"inst{n}" in ids:
            n += 1
        ports = {i.port for i in self.instances}
        port = BASE_PORT
        while port in ports:
            port += 1
        return InstanceDef(id=f"inst{n}", name=name, color=color, vcam=vcam_index,
                           port=port, password=secrets.token_urlsafe(18))

    def add_instance(self, inst: InstanceDef) -> InstanceController:
        self.instances.append(inst)
        self.save()
        c = self._make_controller(inst)
        self.instancesChanged.emit()
        return c

    def update_instance(self, inst: InstanceDef) -> None:
        for idx, old in enumerate(self.instances):
            if old.id == inst.id:
                self.instances[idx] = inst
        self.save()
        self.controllers[inst.id].update_definition(inst)
        obsfiles.set_vcam_placeholder(inst.vcam, inst.color)   # couleur de la caméra virtuelle éteinte
        if inst.config_dir.exists():
            ensure_config(inst, None, self.filename_format(inst), bool(self.settings.get("auto_remux", True)))
        self.instancesChanged.emit()

    def remove_instance(self, inst_id: str) -> None:
        inst = next(i for i in self.instances if i.id == inst_id)
        c = self.controllers.pop(inst_id)
        c.shutdown()
        c.deleteLater()
        if inst.config_dir.exists():
            paths.TRASH.mkdir(parents=True, exist_ok=True)
            dest = paths.TRASH / f"{inst.name}_{time.strftime('%Y%m%d_%H%M%S')}"
            shutil.move(str(inst.config_dir), str(dest))
        shutil.rmtree(inst.dir, ignore_errors=True)
        self.instances = [i for i in self.instances if i.id != inst_id]
        self.save()
        self.instancesChanged.emit()

    def move_instance(self, inst_id: str, delta: int) -> None:
        idx = next(k for k, i in enumerate(self.instances) if i.id == inst_id)
        new = max(0, min(len(self.instances) - 1, idx + delta))
        if new != idx:
            self.instances.insert(new, self.instances.pop(idx))
            self.save()
            self.instancesChanged.emit()

    # ------------------------------------------------------------------ #
    # Préparation (exécuté dans un thread de travail)
    # ------------------------------------------------------------------ #
    def prepare(self, insts: list[InstanceDef], progress, import_from: dict | None = None,
                force_runtime: bool = False) -> list[int]:
        """Copie OBS si besoin, construit les installations, écrit les configs.

        Renvoie les numéros de caméras virtuelles qui n'ont pas pu être installées.
        """
        src = self.obs_source()
        status = obsfiles.runtime_status(src)
        if status == "missing" or force_runtime:
            if not src:
                raise RuntimeError("OBS Studio est introuvable. Indique son dossier dans les paramètres.")
            obsfiles.sync_runtime(src, progress)
        for inst in insts:
            if not obsfiles.tree_is_current(inst.dir, inst.vcam):
                obsfiles.build_tree(inst.dir, inst.vcam, progress)
            obsfiles.install_sync_plugin(inst.dir)
            ensure_config(inst, (import_from or {}).get(inst.id), self.filename_format(inst),
                          bool(self.settings.get("auto_remux", True)))
        colors = {i.vcam: i.color for i in insts}
        names = self.vcam_names()
        stale = sorted({i.vcam for i in insts if not vcam.up_to_date(i.vcam, names.get(i.vcam, ""))})
        missing = [n for n in stale if not vcam.is_registered(n)]
        for inst in insts:
            obsfiles.set_vcam_placeholder(inst.vcam, inst.color)
        if stale and self.settings.get("vcam_scope") == vcam.SCOPE_USER:
            progress(0, 0, "Installation des caméras virtuelles…")
            try:
                failed = vcam.install(stale, vcam.SCOPE_USER, colors, names)
            except (OSError, RuntimeError) as e:
                # Caméras virtuelles impossibles (OBS inconnu…) : les instances s'allument quand même.
                applog.log(f"Allumage : caméras virtuelles {[names.get(n, n) for n in stale]} non installées ({e})")
                return stale
            applog.log(f"Allumage : caméras virtuelles {[names.get(n, n) for n in stale]} installées"
                       + (f" (échec pour {failed})" if failed else ""))
            return failed
        return missing

    def vcam_names(self) -> dict[int, str]:
        """{numéro de caméra: nom dans Windows} — « VCam Rouge », « VCam Bleu »…"""
        return vcam.display_names(self.instances)

    def update_obs(self, progress) -> None:
        """Recopie OBS (après une mise à jour) et reconstruit toutes les instances."""
        src = self.obs_source()
        if not src:
            raise RuntimeError("OBS Studio est introuvable.")
        obsfiles.sync_runtime(src, progress)
        for inst in self.instances:
            obsfiles.build_tree(inst.dir, inst.vcam, progress)
            ensure_config(inst, None, self.filename_format(inst), bool(self.settings.get("auto_remux", True)))
        for inst in self.instances:
            if vcam.registered_scope(inst.vcam):
                try:
                    obsfiles.build_vcam_modules(inst.vcam, inst.color)
                except (OSError, RuntimeError) as e:
                    applog.log(f"Mise à jour d'OBS : caméra virtuelle {inst.vcam} non mise à jour ({e})")

    # ------------------------------------------------------------------ #
    # Préconfigs
    # ------------------------------------------------------------------ #
    def list_presets(self) -> list[dict]:
        out = []
        if not paths.PRESETS.exists():
            return out
        for d in sorted(paths.PRESETS.iterdir()):
            try:
                meta = json.loads((d / "preset.json").read_text("utf-8"))
            except (OSError, ValueError):
                continue
            meta["slug"] = d.name
            out.append(meta)
        out.sort(key=lambda m: (m["slug"] == AUTOSAVE_SLUG, m.get("name", "").lower()))
        return out

    def preset_exists(self, name: str) -> bool:
        return (paths.PRESETS / _slugify(name)).exists()

    def save_preset(self, name: str, progress=None, slug: str | None = None) -> str:
        slug = slug or _slugify(name)
        target = paths.PRESETS / slug
        tmp = paths.PRESETS / (slug + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        (tmp / "configs").mkdir(parents=True)
        for k, inst in enumerate(self.instances):
            if progress:
                progress(k, len(self.instances), f"Sauvegarde de « {inst.name} »…")
            if inst.config_dir.exists():
                copy_tree_tolerant(inst.config_dir, tmp / "configs" / inst.id / "obs-studio")
        meta = {
            "name": name,
            "saved_at": time.strftime("%Y-%m-%d %H:%M"),
            "instances": [i.to_dict() for i in self.instances],
            "naming": {"creator": self.settings.get("creator", ""),
                       "creator_in_name": bool(self.settings.get("creator_in_name", True)),
                       "filename_template": self.settings.get("filename_template", naming.DEFAULT_TEMPLATE)},
        }
        (tmp / "preset.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), "utf-8")
        shutil.rmtree(target, ignore_errors=True)
        tmp.rename(target)
        if slug != AUTOSAVE_SLUG:
            self.settings["current_preset"] = slug
            self.save()
        return slug

    def load_preset(self, slug: str, progress) -> None:
        """Remplace les instances actuelles par celles de la préconfig (tout doit être fermé)."""
        src = paths.PRESETS / slug
        meta = json.loads((src / "preset.json").read_text("utf-8"))
        if self.instances and slug != AUTOSAVE_SLUG:
            self.save_preset("Dernière session (auto)", progress, slug=AUTOSAVE_SLUG)
        new_defs = [InstanceDef.from_dict(d) for d in meta.get("instances") or []]
        new_ids = {i.id for i in new_defs}
        for inst in self.instances:
            if inst.id not in new_ids:
                shutil.rmtree(inst.dir, ignore_errors=True)
        for k, inst in enumerate(new_defs):
            progress(k, len(new_defs), f"Restauration de « {inst.name} »…")
            shutil.rmtree(inst.config_dir, ignore_errors=True)
            saved = src / "configs" / inst.id / "obs-studio"
            if saved.exists():
                shutil.copytree(saved, inst.config_dir)
        self.instances = new_defs
        for k, v in (meta.get("naming") or {}).items():
            self.settings[k] = v
        if slug != AUTOSAVE_SLUG:
            self.settings["current_preset"] = slug
        self.save()

    def rebuild_controllers(self) -> None:
        """À appeler dans le thread de l'interface après load_preset."""
        for c in self.controllers.values():
            c.shutdown()
            c.deleteLater()
        self.controllers = {}
        for inst in self.instances:
            self._make_controller(inst)
        self.instancesChanged.emit()

    def delete_preset(self, slug: str) -> None:
        shutil.rmtree(paths.PRESETS / slug, ignore_errors=True)
        if self.settings.get("current_preset") == slug:
            self.settings["current_preset"] = ""
            self.save()

    def set_preview(self, fps: int | None = None, divisor: int | None = None,
                    background_fps: int | None = None) -> None:
        if fps is not None:
            self.settings["preview_fps"] = fps
        if divisor is not None:
            self.settings["preview_divisor"] = divisor
        if background_fps is not None:
            self.settings["preview_background_fps"] = background_fps
        self.save()
        for c in self.controllers.values():
            c.set_preview(int(self.settings["preview_fps"]), int(self.settings["preview_divisor"]),
                          int(self.settings["preview_background_fps"]))

    def apply_unthrottle(self) -> None:
        """SafeGuard : Windows ne bride ni les OBS ni Multi OBS. Bridé derrière un jeu, Multi OBS perd la
        précision de ses minuteries : aperçus saccadés (18 % d'images en retard mesurés, 5 % débridé)."""
        winapi.unthrottle(os.getpid(), bool(self.settings.get("obs_unthrottle", True)))
        for c in self.controllers.values():
            c.apply_throttle()

    def set_app_state(self, state: str) -> None:
        """Premier plan / arrière-plan / absent (réduit, verrouillé, écran éteint)."""
        for c in self.controllers.values():
            c.set_preview_state(state)

    def set_preview_hidden(self, inst_id: str, hidden: bool) -> None:
        for inst in self.instances:
            if inst.id == inst_id:
                inst.preview_hidden = hidden
        self.save()

    def set_card_minimized(self, inst_id: str, minimized: bool) -> None:
        for inst in self.instances:
            if inst.id == inst_id:
                inst.card_minimized = minimized
        self.save()

    def shutdown(self) -> None:
        for c in self.controllers.values():
            c.shutdown()
