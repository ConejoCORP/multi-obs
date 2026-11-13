"""Pilotage d'une instance : processus OBS, connexion WebSocket, état, aperçu et audio."""
import math
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import psutil
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QImage

from . import applog, health, obsfiles, taskbar, winapi
from .framesync import sync_usable
from .instance_config import InstanceDef, record_directory
from .obsws import ObsWs
from .preview import PreviewChannel

LAUNCH_ARGS = ["--portable", "--multi", "--disable-updater",
               "--disable-shutdown-check", "--disable-missing-files-check"]
SILENCE_DB = -50.0
# OBS ignore une pause demandée juste après une reprise (tout en répondant « OK ») : on attend un peu.
RESUME_GUARD_S = 0.3
# Alerte grave (bandeau, Stream Deck) : au moins 6 images perdues en 10 s. Les seuils réglables (silence,
# images perdues, disque presque plein) sont dans les paramètres, onglet SafeGuard.
DROP_BURST_FRAMES, DROP_BURST_S = 6, 10
METER_FLOOR_DB = -60.0
# Rec (hors pause) dont le fichier ne grossit plus pendant 10 s : bloqué (encodeur, son ou disque en panne).
# Pas moins : sous très forte charge, l'encodeur peut avoir plusieurs secondes de retard après une reprise.
STALL_S = 10
# Après « rec arrêté », OBS ferme encore l'encodeur quelques instants (il se dit toujours actif).
STOP_SETTLE_S = 4
# Images manquantes : verdict seulement sur des images rendues au moins 1 s après le départ / la reprise.
SKIP_SETTLE_NS = 1_000_000_000
SYNC_STATUS = {"vendorName": "multiobs-sync", "requestType": "status"}


def _db(mul: float) -> float:
    return 20 * math.log10(mul) if mul > 0 else -math.inf


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def duration_text(seconds: float) -> str:
    """45 s, 2 min, 2 min 05 s."""
    m, s = divmod(int(seconds), 60)
    if not m:
        return f"{s} s"
    return f"{m} min {s:02d} s" if s else f"{m} min"


class InstanceController(QObject):
    changed = Signal()
    preview = Signal(QImage)
    meters = Signal()
    defaultAudioAdded = Signal(str)
    actionFailed = Signal(str)
    alertRaised = Signal(str)     # nouveau problème grave (rec bloqué, OBS fermé, son en panne…)
    obsReady = Signal(str)
    previewFps = Signal(float)
    _diskDone = Signal(object, object)   # (suite, résultat) d'un appel disque fait hors de l'interface

    def __init__(self, inst: InstanceDef, preview_fps: int = 60, preview_divisor: int = 4,
                 preview_background_fps: int = -1, parent=None):
        super().__init__(parent)
        self._diskDone.connect(self._on_disk_done, Qt.ConnectionType.QueuedConnection)
        self.inst = inst
        self.proc: psutil.Process | None = None
        self.ws: ObsWs | None = None
        self.record_dir = ""
        self.disk_free = 0
        self.disk_total = 0
        self._disk_tick = 0
        self._rate_samples: list[tuple[float, int]] = []
        self._failure = ""
        self._failure_until = 0.0
        self.obs_dialogs: list[str] = []
        self.pause_blocked = False     # OBS désactive la pause avec ce réglage de sortie
        self._close_after_stop = False
        self.close_after_remux = False   # s'éteindra quand OBS aura fini de convertir le rec en MP4
        self.settings: dict = {}
        self._remux_watch: list[dict] = []
        self._remux_checking = False
        self._disk_pending = False
        self._remux_timer = QTimer(self, interval=3000, timeout=self._check_remux)
        # Conversion MKV → MP4 en cours (barre sur la carte) : {"mp4", "total", "progress"…}
        self.remux: dict | None = None
        self._remux_track = QTimer(self, interval=300, timeout=self._track_remux)
        # Ce que les touches du Stream Deck affichent : (« filter », source, filtre), (« item », scène,
        # source), (« mute », entrée, "") ; suivi par les événements d'OBS.
        self._watch_wanted: set[tuple[str, str, str]] = set()
        # Problèmes graves en cours : clé → (message, fin ; 0 = tant que le problème dure). Gardés quand
        # OBS se ferme (« OBS s'est fermé pendant le rec » doit rester affiché).
        self.alerts: dict[str, tuple[str, float]] = {}
        self._expected_exit = False
        self.logwatch = health.LogWatcher(inst.config_dir / "logs")
        self.actionFailed.connect(self._remember_failure)
        self._reset_state()
        self._refresh_disk()

        self._poll = QTimer(self, interval=1000, timeout=self._tick)
        self._poll.start()
        self._launch_timer = QTimer(self, interval=250, timeout=self._watch_window)
        self._launch_started = 0.0
        self._want_minimize = False
        self.preview_channel = PreviewChannel(self)
        self.preview_channel.frame.connect(self.preview)
        self.preview_channel.fpsMeasured.connect(self.previewFps)
        self.preview_fps_measured = 0.0
        self.preview_note = ""
        self.previewFps.connect(lambda v: setattr(self, "preview_fps_measured", v))
        self.preview_channel.noteChanged.connect(self._on_preview_note)
        self.preview_channel.configure(preview_fps, preview_divisor, preview_background_fps)
        self.taskbar = taskbar.TaskbarDecor()
        self._hwnd = None
        self._make_ws()
        self._adopt_running()

    # ------------------------------------------------------------------ #
    # État
    # ------------------------------------------------------------------ #
    def _reset_state(self) -> None:
        self.obs_version = ""
        self.scene = ""
        self.recording = False
        self.paused = False
        self.streaming = False
        self.vcam_on = False
        self.rec_time = ""
        self.rec_ms = 0                 # durée du rec au dernier relevé (gelée pendant une pause)
        self._rec_ms_at = 0.0
        self.resumed_at = 0.0           # dernière reprise (horloge monotone)
        self.rec_bytes = 0
        self.rec_path = ""
        self.stream_time = ""
        self.stream_ms = 0
        self._stream_ms_at = 0.0
        self.watched: dict[tuple[str, str, str], bool | None] = {}   # filtres activés, sources visibles
        self._item_ids: dict[tuple[str, int], str] = {}               # (scène, id) → source
        self.fps = 0.0
        self.cpu = 0.0
        self.memory = 0.0
        self.render_skipped = 0
        self.output_skipped = 0
        self._stats_seen = False
        self.levels: dict[str, list[tuple[float, float]]] = {}
        self.meter_order: list[str] = []
        self.mutes: dict[str, bool] = {}
        self._mute_pending: set[str] = set()
        self._last_sound = time.monotonic()
        self._drop_warning_until = 0.0
        self._drop_warning = ""
        self._drops_recent: list[tuple[float, int]] = []   # images perdues récemment : (heure, nombre)
        self._bytes_seen = (-1, time.monotonic())   # taille du rec au dernier changement, et quand
        self._stopped_at = 0.0
        self._stop_settled = True                    # OBS a fini de fermer le rec précédent
        self._stop_asked = 0.0                       # arrêt du rec demandé (et pas encore fini)
        self._start_asked = 0.0                      # départ du rec demandé (et pas encore vu)
        self._rec_event_at = 0.0                     # dernier changement d'état du rec annoncé par OBS
        self._sync_origin = None                     # origine du fichier d'après le module (images sautées)
        self._sync_gen = None
        self._skipped = 0
        self._resumed_qpc = 0                        # dernier départ / dernière reprise, horloge d'OBS (ns)
        self._priority_raised = False
        self.closing = False
        self.obs_ready = False

    @property
    def running(self) -> bool:
        return self.proc is not None

    @property
    def connected(self) -> bool:
        return bool(self.ws and self.ws.ready and self.obs_ready)

    @property
    def status(self) -> str:
        if not self.running:
            return "stopped"
        if not self.connected:
            return "starting"
        return "ready"

    def _remember_failure(self, message: str) -> None:
        self._failure = message.split(" : ", 1)[-1]
        self._failure_until = time.monotonic() + 15
        self.changed.emit()

    def rec_elapsed_ms(self) -> int:
        """Durée du rec en cours, comme le timecode d'OBS, prolongée depuis le dernier relevé."""
        if not self.recording:
            return 0
        if self.paused:
            return self.rec_ms
        return int(self.rec_ms + (time.monotonic() - self._rec_ms_at) * 1000)

    def bytes_per_second(self) -> float:
        s = self._rate_samples
        if not self.recording or self.paused or len(s) < 3 or s[-1][0] - s[0][0] < 4:
            return 0.0
        return max(0.0, (s[-1][1] - s[0][1]) / (s[-1][0] - s[0][0]))

    # ------------------------------------------------------------------ #
    # Alertes graves (bandeau rouge, barre des tâches, Stream Deck)
    # ------------------------------------------------------------------ #
    def _raise(self, key: str, message: str, ttl: float = 0) -> None:
        """Problème grave : annoncé une fois (alertRaised), affiché tant qu'il dure (ttl = 0) ou ttl s."""
        new = key not in self.alerts
        self.alerts[key] = (message, time.monotonic() + ttl if ttl else 0.0)
        if new:
            applog.log(f"ALERTE {self.inst.name} : {message}")
            self.alertRaised.emit(f"{self.inst.name} : {message}")

    def _clear(self, key: str) -> None:
        self.alerts.pop(key, None)

    def critical(self) -> list[str]:
        now = time.monotonic()
        for key in [k for k, (_m, end) in self.alerts.items() if end and now > end]:
            del self.alerts[key]
        return [m for m, _end in self.alerts.values()]

    def _check_health(self) -> None:
        now = time.monotonic()
        rec = self.connected and self.recording
        if self.proc:
            for message in self.logwatch.poll():   # ce qu'OBS écrit dans son journal
                if rec or self.streaming:
                    self._raise("log:" + message, message, 120)
                else:
                    applog.log(f"{self.inst.name} (journal d'OBS) : {message}")
        # Le fichier ne grossit plus alors que le rec n'est pas en pause : OBS n'écrit plus rien. (Pendant
        # un arrêt, c'est normal : « l'arrêt ne se termine pas » prend le relais.)
        if rec and not self.paused and not self._stop_asked:
            if self.rec_bytes != self._bytes_seen[0]:
                if "stall" in self.alerts:
                    self._raise("stall_end", f"Le rec a été bloqué {int(now - self._bytes_seen[1])} s puis est reparti",
                                300)
                    self._clear("stall")
                self._bytes_seen = (self.rec_bytes, now)
            elif now - self._bytes_seen[1] >= STALL_S:
                self._raise("stall", f"REC BLOQUÉ : plus rien n'est écrit depuis {int(now - self._bytes_seen[1])} s "
                                     "(arrête et relance le rec de cet OBS)")
        else:
            self._bytes_seen = (-1, now)
            self._clear("stall")
        # Rec demandé (et redemandé) mais jamais commencé : le dire fort, on croirait enregistrer.
        if self._start_asked and not self.recording and now - self._start_asked > 12:
            self._start_asked = 0.0
            self._raise("nostart", "Le rec n'a pas démarré : relance-le", 60)
        # OBS bloqué ne peut plus finir son fichier : l'arrêt attend une image qui ne vient jamais.
        if rec and self._stop_asked and now - self._stop_asked > 15:
            self._raise("stopstuck", "L'arrêt du rec ne se termine pas (OBS bloqué) : ferme cet OBS, le fichier "
                                     "déjà écrit est gardé")
        elif not rec:
            self._stop_asked = 0.0
            self._clear("stopstuck")
        if self.obs_dialogs and (rec or self.streaming or now - self._stopped_at < 30):
            self._raise("dialog", f"OBS affiche une erreur : « {self.obs_dialogs[0]} » — clique « Voir OBS »")
        else:
            self._clear("dialog")
        low_disk = float(self.settings.get("low_disk_gb", 10)) * 1024 ** 3
        if self.disk_total and self.disk_free < low_disk and (rec or self.streaming):
            self._raise("disk", f"Disque presque plein ({self.disk_free / 1024 ** 3:.1f} Go libres)")
        else:
            self._clear("disk")
        if rec and self._disk_tick % 2 == 0:
            self.ws.call("CallVendorRequest", SYNC_STATUS, self._on_sync_status)

    def _on_sync_status(self, ok: bool, d: dict, _s: dict) -> None:
        """Module multiobs-sync : l'origine du fichier ne bouge qu'avec une pause. Si elle avance sans
        pause, des images manquent au fichier : ce rec se décale par rapport aux autres."""
        data = (d.get("responseData") or {}) if ok else {}
        if not (sync_usable(data) and data.get("active") and data.get("frames") and data.get("frame_ns")):
            self._sync_origin = None
            return
        # Juger seulement sur des images rendues bien après le dernier départ / la dernière reprise : sous
        # forte charge, l'encodeur peut avoir plusieurs secondes de retard (même horloge qu'OBS : QPC).
        settled = not self.paused and data.get("last_cts", 0) > self._resumed_qpc + SKIP_SETTLE_NS
        prev, self._sync_origin = self._sync_origin, data["origin"]
        if not settled:
            self._sync_origin = None
            return
        if prev is None or data.get("gen") != self._sync_gen:
            self._sync_gen = data.get("gen")
            return
        skipped = round((data["origin"] - prev) / data["frame_ns"])
        if skipped >= 1:
            self._skipped += skipped
            self._raise("skip", f"Vidéo : {self._skipped} image(s) sautée(s) à l'encodage, ce rec est décalé "
                                "d'autant par rapport aux autres", 300)

    def warnings(self) -> list[str]:
        out = self.critical()
        if not self.connected:
            return out
        now = time.monotonic()
        if self.obs_dialogs and "dialog" not in self.alerts:
            out.append(f"OBS attend une réponse : « {self.obs_dialogs[0]} » — clique « Voir OBS »")
        if self.pause_blocked and self.recording:
            out.append("Pause impossible avec la qualité « Identique au stream » (clic sur Pause pour corriger)")
        if now < self._failure_until:
            out.append(self._failure)
        if now < self._drop_warning_until and self._drop_warning and "drops" not in self.alerts:
            out.append(self._drop_warning)
        active = (self.recording and not self.paused) or self.streaming
        silence = int(self.settings.get("silence_warn_s", 120))   # 0 : jamais (SafeGuard)
        if active:
            if not self.meter_order:
                out.append("Aucune source audio")
            elif all(self.mutes.get(n, False) for n in self.meter_order):
                out.append("Toutes les sources audio sont coupées")
            elif silence and now - self._last_sound > silence:
                out.append(f"Pas de son depuis {duration_text(now - self._last_sound)}")
        return out

    # ------------------------------------------------------------------ #
    # Processus
    # ------------------------------------------------------------------ #
    def _adopt_running(self) -> None:
        exe = str(self.inst.exe)
        for p in psutil.process_iter(["name", "exe"]):
            try:
                if p.info["name"] and p.info["name"].lower() == "obs64.exe" and p.info["exe"] \
                        and _same_path(p.info["exe"], exe):
                    self.proc = p
                    self.apply_throttle()
                    self.ws.open()
                    self.preview_channel.activate(self.inst.port, self.inst.password)
                    return
            except (psutil.Error, OSError):
                continue

    def start(self) -> None:
        if self.running:
            return
        exe = self.inst.exe
        # OBS a son propre Qt : on ne lui passe pas les réglages Qt de Multi OBS (QT_QPA_PLATFORM…),
        # qui peuvent l'empêcher de démarrer.
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("QT_", "QML"))}
        p = subprocess.Popen([str(exe), *LAUNCH_ARGS], cwd=str(exe.parent), close_fds=True, env=env,
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        self.proc = psutil.Process(p.pid)
        self.apply_throttle()
        self._expected_exit = False
        self._clear("crash")
        self._reset_state()
        self._want_minimize = self.inst.start_minimized
        self._launch_started = time.monotonic()
        self._launch_timer.start()
        self.preview_channel.activate(self.inst.port, self.inst.password)
        self.changed.emit()

    def shutdown_gracefully(self) -> bool:
        """Éteint l'instance. Si elle enregistre : arrête d'abord le rec et laisse OBS finir la
        conversion MP4 avant de le fermer (sinon le fichier reste en MKV)."""
        if not self.proc:
            return True
        if self.recording and self.connected:
            self.closing = True
            self._close_after_stop = True
            self.stop_record()
            # Filet de sécurité : si l'arrêt n'aboutit pas, on ferme quand même.
            QTimer.singleShot(45000, lambda: self._close_after_stop and self.request_close())
            self.changed.emit()
            return True
        return self.request_close()

    @property
    def closing_after_stop(self) -> bool:
        """Éteinte pendant un rec : attend l'arrêt du rec (puis la conversion MP4) pour se fermer."""
        return self._close_after_stop

    def close_after_conversion(self) -> None:
        """Éteint l'instance quand OBS a fini de convertir le rec en MP4 (tout de suite s'il ne convertit
        pas). Éteint en pleine conversion, OBS l'abandonne : le MP4 n'existerait pas (le MKV reste complet)."""
        if self.remux is None:
            if not self.shutdown_gracefully():
                self.kill()
            return
        self.close_after_remux = True
        self.closing = True
        self.changed.emit()

    def request_close(self) -> bool:
        """Demande à OBS de se fermer (comme le bouton X). False si aucune fenêtre trouvée."""
        if not self.proc:
            return True
        hwnd = winapi.main_window(self.proc.pid, visible_only=False)
        if not hwnd:
            return False
        self.closing = True
        winapi.request_close(hwnd)
        self.changed.emit()
        return True

    def kill(self) -> None:
        if self.proc:
            self._expected_exit = True
            try:
                self.proc.kill()
            except psutil.Error:
                pass

    def show_window(self) -> bool:
        if not self.proc:
            return False
        hwnd = winapi.main_window(self.proc.pid)
        if hwnd:
            winapi.bring_to_front(hwnd)
            return True
        return False

    def minimize_window(self) -> None:
        if self.proc:
            hwnd = winapi.main_window(self.proc.pid)
            if hwnd:
                winapi.minimize(hwnd)

    def just_launched(self, pid: int) -> bool:
        """`pid` est cet OBS, ouvert par Multi OBS il y a moins de 2 minutes (le temps qu'il
        finisse de démarrer, même lourd et lancé avec les autres)."""
        return (bool(self.proc) and self.proc.pid == pid and bool(self._launch_started)
                and time.monotonic() - self._launch_started < 120)

    def _watch_window(self) -> None:
        if not self.proc or time.monotonic() - self._launch_started > 60:
            self._launch_timer.stop()
            return
        hwnd = winapi.main_window(self.proc.pid)
        if hwnd:
            if self._want_minimize:
                winapi.minimize(hwnd)
            self._launch_timer.stop()

    def _on_exit(self) -> None:
        if (self.recording or self.streaming) and not self.closing and not self._expected_exit:
            self._raise("crash", "OBS s'est fermé pendant le " + ("rec !" if self.recording else "live !"), 3600)
        self.proc = None
        self._close_after_stop = False
        self.close_after_remux = False
        self._hwnd = None
        self.taskbar.hwnd = None
        self.record_dir = ""
        self._rate_samples.clear()
        self._launch_timer.stop()
        if self.ws:
            self.ws.close()
        self.preview_channel.deactivate()
        self._reset_state()
        self.preview.emit(QImage())
        self.meters.emit()
        self.changed.emit()

    # ------------------------------------------------------------------ #
    # WebSocket
    # ------------------------------------------------------------------ #
    def _make_ws(self) -> None:
        if self.ws:
            self.ws.close()
            self.ws.deleteLater()
        self.ws = ObsWs(self.inst.port, self.inst.password, self)
        self.ws.identified.connect(self._on_identified)
        self.ws.closed.connect(self._on_ws_closed)
        self.ws.event.connect(self._on_event)

    def update_definition(self, inst: InstanceDef) -> None:
        reconnect = (inst.port, inst.password) != (self.inst.port, self.inst.password)
        self.inst = inst
        if reconnect:
            self._make_ws()
            if self.running:
                self.preview_channel.activate(inst.port, inst.password)
        self.changed.emit()

    def set_preview(self, fps: int, divisor: int, background_fps: int) -> None:
        self.preview_channel.configure(fps, divisor, background_fps)

    def set_preview_visible(self, visible: bool) -> None:
        self.preview_channel.set_visible(visible)

    def set_preview_state(self, state: str) -> None:
        self.preview_channel.set_app_state(state)

    def _on_preview_note(self, note: str) -> None:
        self.preview_note = note
        self.changed.emit()

    def set_preview_target(self, w: int, h: int) -> None:
        self.preview_channel.set_target(w, h)

    def shutdown(self) -> None:
        """Arrête le thread d'aperçu et remet l'icône d'origine de la fenêtre OBS."""
        try:
            self.taskbar.restore()
        except Exception:
            pass
        self.preview_channel.shutdown()

    def _decorate_window(self) -> None:
        """Titre « OBS Blue — Caméra » et logo OBS coloré dans la barre des tâches."""
        if not self.proc:
            return
        if not self.settings.get("taskbar_rename", True):
            if self.taskbar.hwnd:
                self.taskbar.restore()
            return
        try:
            if not (self._hwnd and taskbar.user32.IsWindow(self._hwnd)):
                self._hwnd = winapi.main_window(self.proc.pid)
            if self._hwnd:
                self.taskbar.apply(self._hwnd, self.inst.name, self.inst.color)
        except Exception:
            self._hwnd = None

    # ------------------------------------------------------------------ #
    # Disque : jamais dans le fil de l'interface
    # ------------------------------------------------------------------ #
    def _disk(self, work, then) -> None:
        """work() dans un fil à part, puis then(résultat) dans l'interface (None si work a échoué).
        Pendant une conversion MP4 sur un disque dur, un simple stat bloque jusqu'à 0,7 s (et quelques
        secondes au réveil d'un disque en veille) : l'interface et les aperçus se figeaient."""
        def run():
            try:
                result = work()
            except Exception:
                result = None
            try:
                self._diskDone.emit(then, result)
            except RuntimeError:   # Multi OBS fermé entre-temps
                pass
        threading.Thread(target=run, daemon=True).start()

    def _on_disk_done(self, then, result) -> None:
        then(result)

    def _refresh_disk(self) -> None:
        if self._disk_pending:   # la lecture précédente n'est pas revenue (disque lent)
            return
        self._disk_pending = True
        folder = self.record_dir or str(record_directory(self.inst))

        def work():
            target = Path(folder)
            while not target.exists() and target.parent != target:
                target = target.parent
            return shutil.disk_usage(target)

        def then(usage):
            self._disk_pending = False
            self.disk_free, self.disk_total = (usage.free, usage.total) if usage else (0, 0)
            self.disk_path = folder
        self._disk(work, then)

    def _refresh_record_dir(self) -> None:
        def cb(ok, d, _s):
            if ok and d.get("recordDirectory"):
                self.record_dir = d["recordDirectory"]
                self._refresh_disk()
        self.ws.call("GetRecordDirectory", cb=cb)

    def _tick(self) -> None:
        self._disk_tick += 1
        if self._disk_tick % 5 == 0:
            self._refresh_disk()
        if self.connected and self._disk_tick % 15 == 0:
            self._refresh_record_dir()
            self._check_pause_support()   # l'utilisateur a pu changer ses réglages dans OBS
        if self.proc:
            try:
                alive = self.proc.is_running() and self.proc.status() != psutil.STATUS_ZOMBIE
            except psutil.Error:
                alive = False
            if not alive:
                self._on_exit()
                return
            if not self.ws.ready and not self.ws.connecting:
                self.ws.open()
        if self.connected:
            self.ws.call("GetStats", cb=self._on_stats)
            self._poll_record_status()
            self.ws.call("GetStreamStatus", cb=self._on_stream_status)
            self._decorate_window()
        if self.proc and self._disk_tick % 2 == 0:
            try:
                self.obs_dialogs = winapi.pending_dialogs(self.proc.pid, obsfiles.remux_titles())
            except Exception:
                self.obs_dialogs = []
        elif not self.proc:
            self.obs_dialogs = []
        self._check_health()
        self.changed.emit()

    def _on_identified(self) -> None:
        # Le WebSocket répond avant qu'OBS ait fini de charger (erreur 207) : on attend.
        def cb(ok, d, status):
            if not self.ws.ready:
                return
            if not ok:
                QTimer.singleShot(400, self._on_identified)
                return
            self.obs_ready = True
            self.scene = d.get("sceneName") or d.get("currentProgramSceneName", "")
            self._on_obs_ready()
        self.ws.call("GetCurrentProgramScene", cb=cb)

    def apply_priority(self) -> None:
        """Rec d'un jeu : OBS passe devant le jeu quand le processeur sature (sinon son encodeur décroche
        et répète des images). Seulement depuis la priorité normale (celle d'OBS par défaut) : un autre
        choix fait dans OBS est respecté."""
        if not self.proc:
            return
        want = bool(self.settings.get("obs_priority", True))
        try:
            current = self.proc.nice()
            if want and current == psutil.NORMAL_PRIORITY_CLASS:
                self.proc.nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
                self._priority_raised = True
            elif not want and self._priority_raised and current == psutil.ABOVE_NORMAL_PRIORITY_CLASS:
                self.proc.nice(psutil.NORMAL_PRIORITY_CLASS)
                self._priority_raised = False
        except psutil.Error:
            pass

    def apply_throttle(self) -> None:
        """Windows 11 bride les programmes réduits ou en arrière-plan : bridé, OBS rend ses images jusqu'à
        15 ms en retard (petites saccades dans le rec). Débridé sauf si SafeGuard dit le contraire."""
        if self.proc:
            winapi.unthrottle(self.proc.pid, bool(self.settings.get("obs_unthrottle", True)))

    def _on_obs_ready(self) -> None:
        self.apply_priority()   # OBS a déjà appliqué sa propre priorité à ce stade
        self._refresh_record_dir()
        self._check_pause_support()
        self.obsReady.emit(self.inst.id)
        self.ws.call("GetVersion", cb=lambda ok, d, s: setattr(self, "obs_version", d.get("obsVersion", "")))
        if self.inst.needs_default_audio and self.scene:
            self._add_default_audio()
        self.ws.call("GetVirtualCamStatus", cb=self._on_vcam_status)
        self._poll_record_status()
        self.ws.call("GetStreamStatus", cb=self._on_stream_status)
        self._requery_watches()
        self.changed.emit()

    # ------------------------------------------------------------------ #
    # Suivi pour le Stream Deck : filtres, visibilité des sources, sons coupés
    # ------------------------------------------------------------------ #
    def set_watches(self, wanted: set[tuple[str, str, str]]) -> None:
        new = wanted - self._watch_wanted
        self._watch_wanted = set(wanted)
        for key in list(self.watched):
            if key not in wanted:
                del self.watched[key]
        if self.connected:
            for key in new:
                self._query_watch(key)

    def _requery_watches(self) -> None:
        if self.connected:
            for key in self._watch_wanted:
                self._query_watch(key)

    def _query_watch(self, key: tuple[str, str, str]) -> None:
        kind, a, b = key

        def store(value) -> None:
            if key in self._watch_wanted:
                self.watched[key] = value
                self.changed.emit()
        if kind == "filter":
            self.ws.call("GetSourceFilter", {"sourceName": a, "filterName": b},
                         lambda ok, d, _s: store(bool(d.get("filterEnabled")) if ok else None))
        elif kind == "item":
            def with_id(ok, d, _s):
                if not ok:
                    store(None)
                    return
                item_id = d.get("sceneItemId")
                self._item_ids[(a, item_id)] = b
                self.ws.call("GetSceneItemEnabled", {"sceneName": a, "sceneItemId": item_id},
                             lambda ok2, d2, _s2: store(bool(d2.get("sceneItemEnabled")) if ok2 else None))
            self.ws.call("GetSceneItemId", {"sceneName": a, "sourceName": b}, with_id)
        elif kind == "mute":
            def muted(ok, d, _s):
                if ok:
                    self.mutes[a] = bool(d.get("inputMuted"))
                    self.changed.emit()
            self.ws.call("GetInputMute", {"inputName": a}, muted)

    def _add_default_audio(self) -> None:
        # Une instance vierge n'a aucune source audio (pas d'assistant de premier lancement).
        for name, kind in (("Audio du bureau", "wasapi_output_capture"), ("Mic/Aux", "wasapi_input_capture")):
            self.ws.call("CreateInput", {"sceneName": self.scene, "inputName": name, "inputKind": kind,
                                         "inputSettings": {"device_id": "default"}})
        self.defaultAudioAdded.emit(self.inst.id)

    def _on_ws_closed(self) -> None:
        self.obs_ready = False
        self.levels.clear()
        self.meter_order.clear()
        self.meters.emit()
        self.changed.emit()

    def _refresh_scene(self) -> None:
        def cb(ok, d, _s):
            if ok:
                self.scene = d.get("sceneName") or d.get("currentProgramSceneName", "")
                self.changed.emit()
        self.ws.call("GetCurrentProgramScene", cb=cb)

    def _on_stats(self, ok: bool, d: dict, _s: dict) -> None:
        if not ok:
            return
        self.fps = d.get("activeFps", 0.0)
        self.cpu = d.get("cpuUsage", 0.0)
        self.memory = d.get("memoryUsage", 0.0)
        rs, os_ = d.get("renderSkippedFrames", 0) or 0, d.get("outputSkippedFrames", 0) or 0
        new_render = self._stats_seen and rs > self.render_skipped
        new_output = self._stats_seen and os_ > self.output_skipped
        if new_render or new_output:
            now = time.monotonic()
            # Quelques images perdues au démarrage d'OBS sont courantes : avertissement à partir du seuil SafeGuard.
            if rs + os_ >= max(1, int(self.settings.get("drop_warn_frames", 10))):
                self._drop_warning = (f"Images perdues à l'encodage (+{os_ - self.output_skipped})" if new_output
                                      else f"Images perdues au rendu (+{rs - self.render_skipped})")
                self._drop_warning_until = now + 8
            # Alerte grave seulement pour de vraies saccades (≥ 6 images en 10 s, 1 % à 60 i/s) ; une image
            # perdue de temps en temps ne se voit pas : avertissement simple sur la carte.
            self._drops_recent = [(t, n) for t, n in self._drops_recent if now - t < DROP_BURST_S]
            # Une image ratée au rendu est aussi répétée à l'encodage : comptée une seule fois.
            self._drops_recent.append((now, max(rs - self.render_skipped, os_ - self.output_skipped)))
            burst = sum(n for _t, n in self._drops_recent)
            if burst >= DROP_BURST_FRAMES and (self.recording or self.streaming):
                kind = "à l'encodage" if new_output else "au rendu"
                self._raise("drops", f"Vidéo : {burst} images perdues {kind} en {DROP_BURST_S} s, saccades dans le rec",
                            20)
            # OBS est chargé : l'aperçu se fait discret pour protéger l'enregistrement.
            self.preview_channel.throttle()
        self.render_skipped, self.output_skipped = rs, os_
        self._stats_seen = True

    def _poll_record_status(self) -> None:
        sent = time.perf_counter()
        self.ws.call("GetRecordStatus", cb=lambda ok, d, s: self._on_record_status(ok, d, s, sent))

    def _on_record_status(self, ok: bool, d: dict, _s: dict, sent: float = 0.0) -> None:
        # Réponse partie avant le dernier changement d'état (rec arrêté juste après la demande…) : périmée.
        # Et juste après « arrêté », OBS dit encore « actif » le temps de fermer l'encodeur. Sinon un rec
        # qui vient de s'arrêter paraîtrait encore en cours, et le départ suivant serait ignoré.
        if ok and not d.get("outputActive"):
            self._stop_settled = True
        if ok and (sent < self._rec_event_at or (d.get("outputActive") and self._settling())):
            return
        if ok:
            self.recording = d.get("outputActive", False)
            self.paused = d.get("outputPaused", False)
            self.rec_time = (d.get("outputTimecode") or "")[:8]
            self.rec_ms, self._rec_ms_at = int(d.get("outputDuration") or 0), time.monotonic()
            self.rec_bytes = d.get("outputBytes", 0)
            if self.recording and not self.paused:
                self._rate_samples.append((time.monotonic(), self.rec_bytes))
                self._rate_samples = self._rate_samples[-12:]
            else:
                self._rate_samples.clear()

    def _on_stream_status(self, ok: bool, d: dict, _s: dict) -> None:
        if ok:
            self.streaming = d.get("outputActive", False)
            self.stream_time = (d.get("outputTimecode") or "")[:8]
            self.stream_ms, self._stream_ms_at = int(d.get("outputDuration") or 0), time.monotonic()

    def stream_elapsed_ms(self) -> int:
        if not self.streaming:
            return 0
        return int(self.stream_ms + (time.monotonic() - self._stream_ms_at) * 1000)

    def _on_vcam_status(self, ok: bool, d: dict, _s: dict) -> None:
        if ok:
            self.vcam_on = d.get("outputActive", False)
            self.changed.emit()

    def _on_event(self, etype: str, d: dict) -> None:
        if not self.obs_ready:
            return
        if etype == "InputVolumeMeters":
            self._on_meters(d.get("inputs") or [])
            return
        if etype == "RecordStateChanged":
            state = d.get("outputState", "")
            self._rec_event_at = time.perf_counter()
            # obs-websocket annonce « outputActive: false » pendant une PAUSE : l'enregistrement
            # continue pourtant. On se fie donc à l'état, pas à ce booléen.
            if state.endswith("PAUSED"):
                self.rec_ms, self._rec_ms_at = self.rec_elapsed_ms(), time.monotonic()   # timecode figé
                self.recording, self.paused = True, True
            elif state.endswith(("RESUMED", "STARTED")):
                self._resumed_qpc = time.perf_counter_ns()   # même horloge que les images d'OBS
                self._last_sound = time.monotonic()   # silence compté pendant le rec (pas avant, ni en pause)
                if state.endswith("STARTED"):
                    self._stop_asked = 0.0   # rec arrêté puis relancé entre deux relevés
                    self._start_asked = 0.0
                    self._clear("nostart")
                    self.rec_ms = 0
                    self._sync_origin, self._skipped = None, 0
                    self._clear("skip")
                else:
                    self.resumed_at = time.monotonic()
                self._rec_ms_at = time.monotonic()
                self.recording, self.paused = True, False
            elif state.endswith("STOPPING"):   # arrêt demandé (Multi OBS, Tout arrêter, fenêtre d'OBS…)
                self._stop_asked = self._stop_asked or time.monotonic()
            elif state.endswith("STOPPED"):
                self.recording, self.paused = False, False
                self._stop_asked = 0.0
            if not self.recording:
                self.rec_time = ""
            if state.endswith("STARTED") and d.get("outputPath"):
                self.rec_path = d["outputPath"]
            if state.endswith("STOPPED"):
                self._stopped_at = time.monotonic()
                self._stop_settled = False
                self.rec_path = ""
                self._watch_remux(d.get("outputPath") or "")
                self._start_remux_tracking(d.get("outputPath") or "")
                if self._close_after_stop:   # éteinte pendant le rec : après la conversion (suivie ci-dessus)
                    self._close_after_stop = False
                    self.close_after_conversion()
        elif etype == "StreamStateChanged":
            self.streaming = d.get("outputActive", False)
            if d.get("outputState", "").endswith("STARTED"):
                self.stream_ms, self._stream_ms_at = 0, time.monotonic()
                self._last_sound = time.monotonic()
        elif etype == "SourceFilterEnableStateChanged":
            key = ("filter", d.get("sourceName", ""), d.get("filterName", ""))
            if key in self._watch_wanted:
                self.watched[key] = bool(d.get("filterEnabled"))
        elif etype == "SceneItemEnableStateChanged":
            source = self._item_ids.get((d.get("sceneName", ""), d.get("sceneItemId")))
            key = ("item", d.get("sceneName", ""), source or "")
            if source is not None and key in self._watch_wanted:
                self.watched[key] = bool(d.get("sceneItemEnabled"))
        elif etype in ("SceneItemCreated", "SceneItemRemoved", "SourceFilterCreated", "SourceFilterRemoved",
                       "SourceFilterNameChanged", "InputCreated"):
            self._requery_watches()
        elif etype == "VirtualcamStateChanged":
            self.vcam_on = d.get("outputActive", False)
        elif etype == "CurrentProgramSceneChanged":
            self.scene = d.get("sceneName", "")
        elif etype in ("CurrentSceneCollectionChanged", "CurrentProfileChanged", "SceneNameChanged"):
            self._refresh_scene()
            self._refresh_record_dir()
            self._check_pause_support()
            self.mutes.clear()
            self._requery_watches()
        elif etype == "InputMuteStateChanged":
            self.mutes[d.get("inputName", "")] = d.get("inputMuted", False)
            self.meters.emit()
        elif etype == "InputNameChanged":
            old, new = d.get("oldInputName"), d.get("inputName")
            if old in self.mutes:
                self.mutes[new] = self.mutes.pop(old)
            self._requery_watches()
        elif etype == "InputRemoved":
            self.mutes.pop(d.get("inputName", ""), None)
        elif etype == "ExitStarted":
            self.closing = True
        self.changed.emit()

    def _watch_remux(self, path: str) -> None:
        """OBS convertit le MKV en MP4 à l'arrêt ; on met le MKV à la corbeille une fois le MP4 complet."""
        if not path.lower().endswith(".mkv"):
            return
        if not (self.settings.get("auto_remux") and self.settings.get("trash_mkv_after_remux")):
            return
        mkv = Path(path)
        self._remux_watch.append({"mkv": mkv, "mp4": mkv.with_suffix(".mp4"), "since": time.monotonic(),
                                  "size": -1, "stable": 0})
        self._remux_timer.start()

    @property
    def remux_progress(self) -> float | None:
        """Avancement de la conversion MP4 (0 à 1), None s'il n'y en a pas."""
        return self.remux["progress"] if self.remux else None

    def _start_remux_tracking(self, path: str) -> None:
        """OBS convertit le MKV à l'arrêt (sa petite fenêtre reste derrière) : on suit l'avancement
        pour l'afficher sur la carte, d'après la taille du MP4 qui se remplit."""
        if not (path.lower().endswith(".mkv") and self.settings.get("auto_remux", True)):
            return
        mkv = Path(path)
        now = time.monotonic()
        r = self.remux = {"mp4": mkv.with_suffix(".mp4"), "total": 0, "progress": 0.0, "size": -1,
                          "changed": now, "since": now, "window_seen": False, "polling": False}
        self._disk(lambda: mkv.stat().st_size, lambda total: r.update(total=total or 0))
        self._remux_track.start()
        self.changed.emit()

    def _track_remux(self) -> None:
        r = self.remux
        if r is None:
            self._remux_track.stop()
            return
        now = time.monotonic()
        # Lecture précédente encore en cours (disque saturé) : on ne peut pas conclure que c'est fini.
        reading = bool(r.get("polling"))
        if not reading:   # taille du MP4 lue hors de l'interface, une lecture à la fois
            r["polling"] = True
            self._disk(lambda: r["mp4"].stat().st_size, lambda size: self._remux_size(r, size))
        try:
            window = bool(self.proc) and winapi.has_window(self.proc.pid, obsfiles.remux_titles())
        except Exception:
            window = False
        r["window_seen"] |= window
        # Fenêtre de conversion d'OBS ouverte, ou fichier qui grossit encore (fenêtre pas trouvée) ;
        # au début, on laisse quelques secondes à OBS pour lancer la conversion.
        converting = (window or reading or now - r["changed"] < 1.5
                      or (not r["window_seen"] and now - r["since"] < 4))
        if not converting or now - r["since"] > 3600:
            self.remux = None
            self._remux_track.stop()
            self.changed.emit()
            if self.close_after_remux:   # éteinte pendant la conversion : c'est le moment
                self.close_after_remux = False
                if not self.shutdown_gracefully():
                    self.kill()

    def _remux_size(self, r: dict, size: int | None) -> None:
        """Taille du MP4 lue (hors de l'interface) : avancement mis à jour aussitôt."""
        r["polling"] = False
        size = size or 0
        if size != r["size"]:
            r["size"], r["changed"] = size, time.monotonic()
        if self.remux is r and r["total"]:
            progress = min(0.99, size / r["total"]) if size > 0 else 0.0
            if progress - r["progress"] >= 0.005:
                r["progress"] = progress
                self.changed.emit()

    def _check_remux(self) -> None:
        if self._remux_checking:   # vérification précédente pas finie (disque lent)
            return
        self._remux_checking = True
        watch = list(self._remux_watch)

        def work():
            keep = []
            for w in watch:
                if time.monotonic() - w["since"] > 3600 or not w["mkv"].exists():
                    continue
                try:
                    size = w["mp4"].stat().st_size
                    mkv_size = w["mkv"].stat().st_size
                except OSError:
                    keep.append(w)
                    continue
                w["stable"] = w["stable"] + 1 if size == w["size"] and size > 0 else 0
                w["size"] = size
                # MP4 qui ne grossit plus depuis ~6 s et de taille cohérente : conversion terminée.
                if w["stable"] >= 2 and size >= 0.9 * mkv_size:
                    winapi.send_to_recycle_bin(str(w["mkv"]))
                else:
                    keep.append(w)
            return keep

        def then(keep):
            self._remux_checking = False
            added = [w for w in self._remux_watch if w not in watch]   # arrivés pendant la vérification
            self._remux_watch = (watch if keep is None else keep) + added
            if not self._remux_watch:
                self._remux_timer.stop()
        self._disk(work, then)

    def _on_meters(self, inputs: list) -> None:
        order = []
        levels = {}
        now = time.monotonic()
        for inp in inputs:
            name = inp.get("inputName", "")
            chans = []
            for ch in inp.get("inputLevelsMul") or []:
                if len(ch) >= 2:
                    chans.append((_db(ch[0]), _db(ch[1])))
            if not chans:
                continue
            order.append(name)
            levels[name] = chans
            if name not in self.mutes and name not in self._mute_pending:
                self._request_mute(name)
            if not self.mutes.get(name, False) and max(p for _, p in chans) > SILENCE_DB:
                self._last_sound = now
        self.meter_order, self.levels = order, levels
        self.meters.emit()

    def _request_mute(self, name: str) -> None:
        self._mute_pending.add(name)

        def cb(ok, d, _s):
            self._mute_pending.discard(name)
            if ok:
                self.mutes[name] = d.get("inputMuted", False)
        self.ws.call("GetInputMute", {"inputName": name}, cb)

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def _report(self, what: str):
        def cb(ok, _d, status):
            if not ok and self.connected:
                self.actionFailed.emit(f"{self.inst.name} : {what} impossible ({status.get('comment') or 'erreur OBS'})")
            QTimer.singleShot(150, lambda: self.connected and self._poll_record_status())
        return cb

    def start_record(self, attempt: int = 0):
        # Juste après un arrêt, OBS dit encore « actif » le temps de fermer l'encodeur (et ignorerait
        # sans rien dire un départ) : on n'en tient pas compte, on attend qu'il ait vraiment fini.
        just_stopped = self._settling()
        if not self.connected or (self.recording and not just_stopped):
            return
        if attempt == 0:
            self._start_asked = time.monotonic()
        if just_stopped:
            def check(ok, d, _s):
                if ok and d.get("outputActive") and self._settling():
                    QTimer.singleShot(200, lambda: self.start_record(attempt))
                else:
                    self._stop_settled = True
                    self.start_record(attempt)
            self.ws.call("GetRecordStatus", cb=check)
            return

        def started(ok, d, status):
            # Juste après un arrêt, OBS finalise encore le fichier précédent et refuse : on réessaie.
            if not ok and attempt < 4 and self.connected and not self.recording:
                QTimer.singleShot(600, lambda: self.start_record(attempt + 1))
                return
            self._report("démarrer l'enregistrement")(ok, d, status)
            if ok:   # OBS a dit oui : on vérifie que le rec a vraiment démarré, sinon on redemande
                QTimer.singleShot(3000, lambda: self._confirm_start(attempt))

        def cb(ok, d, _s):
            # Dossier absent (disque débranché…) : OBS ouvrirait une fenêtre d'erreur cachée
            # derrière sa fenêtre réduite. On prévient ici à la place.
            folder = (d.get("recordDirectory") or "") if ok else ""
            if folder and not Path(folder).is_dir():
                self._start_asked = 0.0   # déjà signalé ici
                self.actionFailed.emit(f"{self.inst.name} : dossier d'enregistrement introuvable "
                                       f"({folder}). Change-le dans OBS > Paramètres > Sortie.")
                return
            if self.connected and not self.recording:
                self.ws.call("StartRecord", cb=started)
        self.ws.call("GetRecordDirectory", cb=cb)

    def _settling(self) -> bool:
        """Rec qui vient de s'arrêter : OBS ferme encore l'encodeur (il se dit toujours actif)."""
        return not self._stop_settled and time.monotonic() - self._stopped_at < STOP_SETTLE_S

    def _confirm_start(self, attempt: int) -> None:
        if self._start_asked and self.connected and not self.recording and attempt < 6:
            applog.log(f"{self.inst.name} : rec pas démarré après « OK » d'OBS, nouvelle demande")
            self.start_record(attempt + 1)

    def stop_record(self):
        if self.connected and self.recording:
            self._stop_asked = self._stop_asked or time.monotonic()
            self.ws.call("StopRecord", cb=self._report("arrêter l'enregistrement"))

    def pause_record(self):
        if self.connected and self.recording and not self.paused:
            wait = self.resumed_at + RESUME_GUARD_S - time.monotonic()
            if wait > 0:
                QTimer.singleShot(int(wait * 1000) + 1, self.pause_record)
                return
            report = self._report("mettre en pause")

            def cb(ok, d, status):
                report(ok, d, status)
                if ok:
                    QTimer.singleShot(800, self._check_paused)
            self.ws.call("PauseRecord", cb=cb)

    def _check_paused(self):
        # OBS répond « OK » même quand il ne met pas en pause : on vérifie.
        def cb(ok, d, _s):
            if ok and d.get("outputActive") and not d.get("outputPaused"):
                if self.pause_blocked:
                    msg = (f"{self.inst.name} : pause impossible avec la qualité « Identique au stream » "
                           "— clic sur Pause sur la carte pour corriger.")
                else:
                    msg = f"{self.inst.name} : OBS n'a pas mis l'enregistrement en pause."
                self.actionFailed.emit(msg)
        if self.connected:
            self.ws.call("GetRecordStatus", cb=cb)

    def _param(self, category: str, name: str, cb) -> None:
        def done(ok, d, _s):
            cb((d.get("parameterValue") or d.get("defaultParameterValue") or "") if ok else None)
        self.ws.call("GetProfileParameter", {"parameterCategory": category, "parameterName": name}, done)

    def _check_pause_support(self) -> None:
        """OBS n'autorise pas la pause quand l'enregistrement réutilise l'encodeur du stream :
        mode simple « Identique au stream », ou mode avancé « Utiliser l'encodeur du stream »."""
        if not self.connected:
            return

        def set_blocked(blocked: bool) -> None:
            if blocked != self.pause_blocked:
                self.pause_blocked = blocked
                self.changed.emit()

        def mode(value):
            if value is None:
                return
            if value.lower() == "advanced":
                self._param("AdvOut", "RecType", lambda rt: (
                    self._param("AdvOut", "FFOutputToFile", lambda f: set_blocked((f or "").lower() == "true"))
                    if (rt or "").lower() == "ffmpeg" else
                    self._param("AdvOut", "RecEncoder", lambda enc: set_blocked((enc or "none").lower() == "none"))))
            else:
                self._param("SimpleOutput", "RecQuality", lambda q: set_blocked((q or "Stream") == "Stream"))
        self._param("Output", "Mode", mode)

    def fix_pause(self) -> bool:
        """Mode simple : qualité « Haute qualité, taille moyenne » (encodeur du stream conservé).
        Prend effet au prochain enregistrement. False si la correction n'est pas automatique."""
        if not self.connected:
            return False

        def apply(mode):
            if (mode or "Simple").lower() == "advanced":
                return

            def with_encoder(stream_enc):
                def rec_encoder(ok, d, _s):
                    # Encodeur d'enregistrement jamais choisi : OBS prendrait x264 (processeur).
                    # On reprend celui du stream (souvent NVENC).
                    explicit = d.get("parameterValue") if ok else None
                    if not explicit and stream_enc:
                        self._set_param("SimpleOutput", "RecEncoder", stream_enc)
                    self._set_param("SimpleOutput", "RecQuality", "Small")
                    QTimer.singleShot(300, self._check_pause_support)
                self.ws.call("GetProfileParameter", {"parameterCategory": "SimpleOutput",
                                                     "parameterName": "RecEncoder"}, rec_encoder)
            self._param("SimpleOutput", "StreamEncoder", with_encoder)
        self._param("Output", "Mode", apply)
        return True

    def _set_param(self, category: str, name: str, value: str) -> None:
        self.ws.call("SetProfileParameter", {"parameterCategory": category, "parameterName": name,
                                             "parameterValue": value})

    def resume_record(self):
        if self.connected and self.recording and self.paused:
            self.ws.call("ResumeRecord", cb=self._report("reprendre"))

    def toggle_record(self):
        self.stop_record() if self.recording else self.start_record()

    def toggle_pause(self):
        self.resume_record() if self.paused else self.pause_record()

    def toggle_stream(self):
        if self.connected:
            self.ws.call("ToggleStream", cb=self._report("live"))

    def toggle_vcam(self):
        if self.connected:
            self.ws.call("ToggleVirtualCam", cb=lambda ok, d, s: self._on_vcam_status(ok, d, s))

    def toggle_mute(self, name: str):
        if self.connected:
            self.ws.call("ToggleInputMute", {"inputName": name})

    def open_record_folder(self):
        def cb(ok, d, _s):
            if ok and d.get("recordDirectory"):
                os.startfile(d["recordDirectory"])
        if self.connected:
            self.ws.call("GetRecordDirectory", cb=cb)
