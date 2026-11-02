"""Fenêtre principale de Multi OBS."""
import time

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
                               QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME, APP_VERSION, applog, obsfiles, paths, twitch, vcam, winapi
from ..controller import RESUME_GUARD_S
from ..framesync import OP_NAMES, FrameSync
from ..hotkeys import GlobalHotkeys
from ..manager import AUTOSAVE_SLUG, Manager
from ..preview import STATE_ACTIVE, STATE_AWAY, STATE_BACKGROUND
from ..remote import RemoteServer
from ..syswatch import SystemEvents
from .card import InstanceCard, MiniChip
from .deck_status import DeckStatus
from .dialogs import InstanceDialog, SettingsDialog, VideoNameDialog, ask_conversions
from .tasks import Task, run_task
from .widgets import FlowLayout, icon

CARD_MIN_W = 440
COMPACT_BELOW = 1060
VCAM_CHECK_MS = 60_000   # vérification des caméras virtuelles dans Windows


def _vsep() -> QFrame:
    f = QFrame()
    f.setObjectName("vsep")
    return f


class MainWindow(QMainWindow):
    def __init__(self, manager: Manager):
        super().__init__()
        self.manager = manager
        self.cards: dict[str, InstanceCard] = {}
        self.mini_chips: dict[str, MiniChip] = {}
        self._columns = 0
        self._compact = False
        self.setWindowTitle(APP_NAME)
        self.resize(1320, 900)
        self.setMinimumSize(900, 520)

        central = QWidget(objectName="central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())
        root.addWidget(self._build_naming_bar())
        root.addWidget(self._build_alert_banner())

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.container = QWidget(objectName="cardsContainer")
        self.grid = QGridLayout(self.container)
        self.grid.setContentsMargins(14, 14, 14, 14)
        self.grid.setSpacing(12)
        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll, 1)
        self.empty = self._build_empty_state()
        self.all_minimized = QLabel("Toutes les instances sont réduites : « Remettre » dans la barre du bas.",
                                    objectName="allMinimized")
        self.all_minimized.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Barre du bas : instances réduites (ex. l'OBS du live quand on ne live pas).
        self.mini_bar = QFrame(objectName="miniBar")
        self.mini_flow = FlowLayout(self.mini_bar, spacing=8)
        self.mini_flow.setContentsMargins(14, 6, 14, 6)
        self.mini_flow.addWidget(QLabel("Instances réduites", objectName="miniLabel"))
        self.mini_bar.hide()
        root.addWidget(self.mini_bar)

        self.remote = None   # pilotage par le Stream Deck : lancé par apply_remote()
        self._close_choice = ""   # « stop » : fermeture en attente de la fin des conversions MP4
        self._name_dialog = None  # fenêtre « Nom de la vidéo » ouverte (option du lancement de rec)
        self._quit_timer: QTimer | None = None
        self.status_obs = QLabel()
        self.status_vcam = QLabel()
        self.status_hotkeys = QLabel()
        self.deck_status = DeckStatus(self)
        sb = self.statusBar()
        sb.addWidget(self.status_obs)
        sb.addWidget(self.status_vcam)
        sb.addPermanentWidget(self.deck_status)
        sb.addPermanentWidget(self.status_hotkeys)

        manager.instancesChanged.connect(self.rebuild_cards)
        self.rebuild_cards()
        self.refresh_presets()

        self.hotkeys = GlobalHotkeys(int(self.winId()))
        QApplication.instance().installNativeEventFilter(self.hotkeys)
        self.apply_hotkeys()

        # « Tout en pause / reprendre » synchronisés à l'image près (thread et connexions dédiés).
        self.framesync = FrameSync(self)
        self.framesync.finished.connect(self._on_framesync)
        self._sync_busy, self._sync_started = False, 0.0
        self._sync_op = ""                     # opération de synchro en cours : align / pause / resume
        self._pending_pause: str | None = None  # pause demandée pendant l'alignement du départ
        self._stopping: set[str] = set()   # instances confiées à l'arrêt synchronisé

        # Contexte des aperçus : premier plan, arrière-plan, absent ; cartes visibles ou non.
        self.system = SystemEvents(int(self.winId()))
        QApplication.instance().installNativeEventFilter(self.system.filter)
        self.system.awayChanged.connect(lambda _a: self._schedule_context())
        QGuiApplication.instance().applicationStateChanged.connect(lambda _s: self._schedule_context())
        self._context_timer = QTimer(self, singleShot=True, interval=120, timeout=self._update_preview_context)
        self.scroll.verticalScrollBar().valueChanged.connect(lambda _v: self._schedule_context())

        self._timer = QTimer(self, interval=500, timeout=self.refresh_global)
        self._timer.start()
        self.refresh_global()
        self.refresh_status()

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def _btn(self, text: str, icon_name: str | None, tip: str, slot) -> QPushButton:
        b = QPushButton(text)
        if icon_name:
            b.setIcon(icon(icon_name))
            b.setIconSize(QSize(14, 14))
        b.setToolTip(tip)
        b.clicked.connect(slot)
        return b

    def _build_top_bar(self) -> QFrame:
        bar = QFrame(objectName="topBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(6)

        logo = QLabel()
        logo.setPixmap(QApplication.instance().windowIcon().pixmap(26, 26))
        title = QLabel(APP_NAME, objectName="appTitle")
        lay.addWidget(logo)
        lay.addWidget(title)
        lay.addWidget(_vsep())

        self.launch_all_btn = self._btn("Tout allumer", "power.svg",
                                        "Allumer toutes les instances (sauf celles réduites en bas)", self.launch_all)
        self.close_all_btn = self._btn("Tout éteindre", "close.svg", "Éteindre toutes les instances", self.close_all)
        lay.addWidget(self.launch_all_btn)
        lay.addWidget(self.close_all_btn)
        lay.addWidget(_vsep())

        self.rec_all_btn = self._btn("Tout enregistrer", "rec.svg",
                                     "Démarrer / arrêter l'enregistrement des instances liées (Link)",
                                     self.toggle_record_all)
        self.pause_all_btn = self._btn("Tout en pause", "pause.svg",
                                       "Mettre en pause / reprendre les enregistrements des instances liées (Link)",
                                       self.toggle_pause_all)
        lay.addWidget(self.rec_all_btn)
        lay.addWidget(self.pause_all_btn)
        lay.addStretch(1)

        new_btn = self._btn("Nouvelle instance", "plus.svg", "Créer une nouvelle instance OBS", self.new_instance)
        new_btn.setProperty("role", "primary")
        lay.addWidget(new_btn)
        lay.addWidget(self._btn("", "cogs.svg", "Paramètres", self.open_settings))
        return bar

    def _build_alert_banner(self) -> QFrame:
        """Bandeau rouge des problèmes graves (rec bloqué, OBS fermé, son en panne…)."""
        self.alert_banner = QFrame(objectName="alertBanner")
        lay = QHBoxLayout(self.alert_banner)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(12)
        self.alert_text = QLabel(objectName="alertText")
        self.alert_text.setWordWrap(True)
        hide = QPushButton("Masquer", objectName="alertHide")
        hide.setToolTip("Masquer ce bandeau (il revient au prochain problème)")
        hide.clicked.connect(self._hide_alerts)
        lay.addWidget(self.alert_text, 1)
        lay.addWidget(hide, 0, Qt.AlignmentFlag.AlignTop)
        self.alert_banner.hide()
        self._alerts_hidden: set[str] = set()
        self._global_alerts: dict[str, str] = {}   # problèmes qui concernent plusieurs instances
        return self.alert_banner

    def _current_alerts(self) -> list[str]:
        out = [f"{c.inst.name} : {m}" for c in self.manager.controllers.values() for m in c.critical()]
        return out + list(self._global_alerts.values())

    def _hide_alerts(self) -> None:
        self._alerts_hidden = set(self._current_alerts())
        self.alert_banner.hide()

    def _refresh_alerts(self) -> None:
        alerts = self._current_alerts()
        self._alerts_hidden &= set(alerts)   # un problème réglé puis revenu se réaffiche
        shown = [a for a in alerts if a not in self._alerts_hidden]
        if shown:
            self.alert_text.setText("⚠ " + "\n⚠ ".join(alerts))
        self.alert_banner.setVisible(bool(shown))

    def _on_alert(self, message: str) -> None:
        """Nouveau problème grave : bandeau, barre d'état et bouton de la barre des tâches qui clignote."""
        self.statusBar().showMessage("⚠ " + message, 20000)
        self._refresh_alerts()
        if self.manager.settings.get("alert_taskbar", True):
            QApplication.alert(self, 0)   # clignote jusqu'à ce que Multi OBS soit ouvert

    def _build_naming_bar(self) -> QFrame:
        bar = QFrame(objectName="namingBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 7, 14, 7)
        lay.setSpacing(6)
        s = self.manager.settings
        # Case décochée : les fichiers n'ont pas le nom (enregistrements solo), mais il reste écrit ici.
        self.creator_on = QCheckBox("Nom", objectName="namingCheck")
        self.creator_on.setChecked(bool(s.get("creator_in_name", True)))
        self.creator_on.setToolTip("Décoche pour que tes fichiers n'aient pas ton nom (enregistrements solo).\n"
                                   "Ton nom reste écrit à côté, prêt à revenir.")
        lay.addWidget(self.creator_on)
        self.creator_edit = QLineEdit(s.get("creator", ""))
        self.creator_edit.setPlaceholderText("ton nom")
        self.creator_edit.setFixedWidth(150)
        self.creator_edit.setToolTip("Ton nom, au début du nom de chaque fichier")
        self.creator_edit.setEnabled(self.creator_on.isChecked())
        self.creator_on.toggled.connect(self.creator_edit.setEnabled)
        self.creator_on.toggled.connect(lambda: (self._apply_naming(), self._refresh_naming_preview()))
        lay.addWidget(self.creator_edit)
        lbl = QLabel("Vidéo")
        lbl.setProperty("muted", True)
        lay.addWidget(lbl)
        self.video_edit = QLineEdit(s.get("video_name", ""))
        self.video_edit.setPlaceholderText("nom de la vidéo")
        self.video_edit.setToolTip("Nom de la vidéo, appliqué au nom des fichiers de toutes les instances")
        self.video_edit.setMinimumWidth(180)
        lay.addWidget(self.video_edit, 2)
        self.naming_preview = QLabel(objectName="namingPreview")
        self.naming_preview.setMinimumWidth(80)
        self.naming_preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.naming_preview, 3)
        self._naming_timer = QTimer(self, singleShot=True, interval=500, timeout=self._apply_naming)
        self.creator_edit.textChanged.connect(lambda: (self._naming_timer.start(), self._refresh_naming_preview()))
        self.video_edit.textChanged.connect(lambda: (self._naming_timer.start(), self._refresh_naming_preview()))
        lay.addWidget(_vsep())

        lbl = QLabel("Préconfig")
        lbl.setProperty("muted", True)
        lay.addWidget(lbl)
        self.preset_combo = QComboBox()
        self.preset_combo.setFixedWidth(230)
        lay.addWidget(self.preset_combo)
        self.preset_open_btn = self._btn("Ouvrir", None, "Ouvrir la préconfig choisie (remplace les instances)",
                                         self.open_preset)
        lay.addWidget(self.preset_open_btn)
        lay.addWidget(self._btn("Enregistrer", "save.svg", "Enregistrer la configuration actuelle comme préconfig",
                                self.save_preset))
        self.preset_del_btn = self._btn("", "trash.svg", "Supprimer la préconfig choisie", self.delete_preset)
        lay.addWidget(self.preset_del_btn)
        return bar

    def _apply_naming(self) -> None:
        self.manager.set_naming(self.creator_edit.text(), self.video_edit.text(),
                                creator_in_name=self.creator_on.isChecked())
        for card in self.cards.values():
            card.refresh()

    def _refresh_naming_preview(self) -> None:
        from .. import naming
        s = self.manager.settings
        example = naming.preview(s.get("filename_template"),
                                 self.creator_edit.text() if self.creator_on.isChecked() else "",
                                 "cam", self.video_edit.text())
        fm = self.naming_preview.fontMetrics()
        self.naming_preview.setToolTip(f"Nom du prochain fichier : {example}")
        self.naming_preview.setText(fm.elidedText(f"output : {example}", Qt.TextElideMode.ElideRight,
                                                  max(60, self.naming_preview.width())))

    def _build_empty_state(self) -> QFrame:
        f = QFrame(objectName="emptyState")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.setSpacing(12)
        t = QLabel("Aucune instance OBS")
        t.setStyleSheet("font-size: 14pt; font-weight: 600;")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d = QLabel("Chaque instance est un OBS indépendant avec ses propres scènes, réglages, couleur\n"
                   "et caméra virtuelle. Crée-en une par usage : caméra, écran, live…")
        d.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d.setProperty("muted", True)
        b = QPushButton("Créer une instance")
        b.setIcon(icon("plus.svg"))
        b.setProperty("role", "primary")
        b.clicked.connect(self.new_instance)
        lay.addWidget(t)
        lay.addWidget(d)
        lay.addWidget(b, 0, Qt.AlignmentFlag.AlignCenter)
        return f

    # ------------------------------------------------------------------ #
    # Cartes
    # ------------------------------------------------------------------ #
    def rebuild_cards(self) -> None:
        ids = [i.id for i in self.manager.instances]
        for inst_id in list(self.cards):
            card = self.cards[inst_id]
            if inst_id not in ids or card.ctrl is not self.manager.controllers.get(inst_id):
                card.setParent(None)
                card.deleteLater()
                del self.cards[inst_id]
        names = self.manager.vcam_names()
        for inst in self.manager.instances:
            if inst.id not in self.cards:
                card = InstanceCard(self.manager.controller(inst.id), vcam_name=names.get(inst.vcam, ""))
                card.ctrl.actionFailed.connect(lambda m: self.statusBar().showMessage("⚠ " + m, 10000))
                card.ctrl.alertRaised.connect(self._on_alert)
                card.launchRequested.connect(lambda i: self.launch([i]))
                card.editRequested.connect(self.edit_instance)
                card.deleteRequested.connect(self.delete_instance)
                card.moveRequested.connect(self._move_card)
                card.includeChanged.connect(self._set_include)
                card.recTypeChanged.connect(self.manager.set_rec_type)
                card.previewHiddenChanged.connect(self._set_preview_hidden)
                card.minimizeRequested.connect(lambda i: self._set_card_minimized(i, True))
                card.filename_preview = (lambda i=inst.id: self.manager.filename_preview(
                    next(x for x in self.manager.instances if x.id == i)))
                card.before_record = self.ask_video_name_then
                card.open_chat = self.open_twitch_chat
                self.cards[inst.id] = card
            else:
                self.cards[inst.id].vcam_name = names.get(inst.vcam, "")
                self.cards[inst.id].apply_definition()
        self._columns = 0
        self._relayout()
        self._refresh_mini_bar()
        self.refresh_status()
        self._schedule_context()

    def _relayout(self) -> None:
        shown = [i for i in self.manager.instances if i.id in self.cards and not i.card_minimized]
        avail = self.scroll.viewport().width() - 28
        cols = max(1, min(len(shown) or 1, (avail + 12) // (CARD_MIN_W + 12)))
        if cols == self._columns and self.grid.count() == max(1, len(shown)):
            return
        self._columns = cols
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is self.empty or w is self.all_minimized:
                w.setParent(None)
        for c in range(self.grid.columnCount()):
            self.grid.setColumnStretch(c, 0)
        for r in range(self.grid.rowCount()):
            self.grid.setRowStretch(r, 0)
        for inst in self.manager.instances:   # réduites : rangées dans la barre du bas, aperçu arrêté
            if inst.card_minimized and inst.id in self.cards:
                self.cards[inst.id].hide()
        if not shown:
            self.grid.addWidget(self.all_minimized if self.cards else self.empty, 0, 0, Qt.AlignmentFlag.AlignTop)
            self.grid.setColumnStretch(0, 1)
            self.grid.setRowStretch(1, 1)
            return
        for k, inst in enumerate(shown):
            card = self.cards[inst.id]
            self.grid.addWidget(card, k // cols, k % cols, Qt.AlignmentFlag.AlignTop)
            card.show()
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)
        rows = (len(shown) + cols - 1) // cols
        self.grid.setRowStretch(rows, 1)

    def _refresh_mini_bar(self) -> None:
        """Une pastille par instance réduite, dans l'ordre des instances."""
        for chip in self.mini_chips.values():
            self.mini_flow.removeWidget(chip)
            chip.setParent(None)
            chip.deleteLater()
        self.mini_chips = {}
        names = self.manager.vcam_names()
        for inst in self.manager.instances:
            if inst.card_minimized and inst.id in self.manager.controllers:
                chip = MiniChip(self.manager.controller(inst.id), vcam_name=names.get(inst.vcam, ""))
                chip.restoreRequested.connect(lambda i: self._set_card_minimized(i, False))
                self.mini_flow.addWidget(chip)
                self.mini_chips[inst.id] = chip
        self.mini_bar.setVisible(bool(self.mini_chips))

    def _set_card_minimized(self, inst_id: str, minimized: bool) -> None:
        self.manager.set_card_minimized(inst_id, minimized)
        self._columns = 0
        self._relayout()
        self._refresh_mini_bar()
        self.refresh_global()
        self._schedule_context()

    def _move_card(self, inst_id: str, delta: int) -> None:
        """Déplacer à gauche / à droite parmi les cartes affichées (les réduites ne comptent pas)."""
        insts = self.manager.instances
        shown = [i.id for i in insts if not i.card_minimized or i.id == inst_id]
        k = shown.index(inst_id) + delta
        if 0 <= k < len(shown):
            idx = next(n for n, i in enumerate(insts) if i.id == inst_id)
            target = next(n for n, i in enumerate(insts) if i.id == shown[k])
            self.manager.move_instance(inst_id, target - idx)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()
        self._schedule_context()
        compact = self.width() < COMPACT_BELOW
        if compact != self._compact:
            self._compact = compact
            self.refresh_global()
        self._refresh_naming_preview()

    def _label(self, btn: QPushButton, text: str) -> None:
        """Texte des boutons globaux (icône seule quand la fenêtre est étroite)."""
        btn.setText("" if self._compact else text)
        if self._compact:
            btn.setToolTip(text)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._relayout)
        self._schedule_context()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._schedule_context()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (e.Type.WindowStateChange, e.Type.ActivationChange):
            self._schedule_context()

    def _schedule_context(self) -> None:
        if hasattr(self, "_context_timer"):
            self._context_timer.start()

    def _update_preview_context(self) -> None:
        """Met en pause / ralentit les aperçus selon ce qui est réellement visible."""
        if self.isMinimized() or not self.isVisible() or self.system.away:
            state = STATE_AWAY
        elif QGuiApplication.applicationState() == Qt.ApplicationState.ApplicationActive:
            state = STATE_ACTIVE
        else:
            state = STATE_BACKGROUND
        self.manager.set_app_state(state)
        for card in self.cards.values():
            visible = (not card.ctrl.inst.preview_hidden and card.isVisible()
                       and not card.preview.visibleRegion().isEmpty())
            card.ctrl.set_preview_visible(visible)

    def _set_preview_hidden(self, inst_id: str, hidden: bool) -> None:
        self.manager.set_preview_hidden(inst_id, hidden)
        self._schedule_context()

    def _set_include(self, inst_id: str, value: bool) -> None:
        for inst in self.manager.instances:
            if inst.id == inst_id:
                inst.include_global = value
        self.manager.save()

    # ------------------------------------------------------------------ #
    # Lancement / fermeture
    # ------------------------------------------------------------------ #
    def launch(self, inst_ids: list[str], import_from: dict | None = None, interactive: bool = True) -> None:
        """interactive=False (Stream Deck) : aucune question, OBS des instances gardé tel quel."""
        insts = [i for i in self.manager.instances
                 if i.id in inst_ids and not self.manager.controller(i.id).running]
        if not insts:
            return
        if interactive and self.manager.runtime_status() == "outdated" and not self.manager.any_running():
            r = QMessageBox.question(
                self, APP_NAME,
                "Ton OBS a été mis à jour depuis la création des instances.\n\n"
                "Mettre à jour les instances maintenant ? (scènes et réglages conservés)")
            if r == QMessageBox.StandardButton.Yes:
                run_task(self, "Mise à jour d'OBS", self.manager.update_obs,
                         on_done=lambda _r: self.launch(inst_ids, import_from))
                return

        def done(missing_vcams):
            for inst in insts:
                self.manager.controller(inst.id).start()
            if missing_vcams and self.manager.settings.get("vcam_scope") == vcam.SCOPE_USER:
                self.statusBar().showMessage(
                    "Caméra(s) virtuelle(s) non installée(s) : " + ", ".join(map(str, missing_vcams))
                    + " — voir Paramètres.", 10000)
            self.refresh_status()

        names = ", ".join(i.name for i in insts)
        run_task(self, f"Préparation de {names}",
                 lambda p: self.manager.prepare(insts, p, import_from), on_done=done)

    def launch_all(self, interactive: bool = True) -> None:
        """Allume les instances affichées : les réduites (ex. l'OBS du live) restent comme elles sont."""
        self.launch([i.id for i in self.manager.instances if not i.card_minimized], interactive=interactive)

    def close_all(self) -> str:
        """Éteint toutes les instances. Renvoie « done », « waiting » (certaines s'éteindront à la fin de
        leur rec ou de leur conversion MP4 : Multi OBS doit rester ouvert jusque-là) ou « cancelled »."""
        ctrls = list(self.manager.controllers.values())
        recording = [c.inst.name for c in ctrls if c.recording or c.streaming]
        if recording:
            r = QMessageBox.question(self, APP_NAME,
                                     "Ces instances enregistrent ou sont en live :\n• " + "\n• ".join(recording)
                                     + "\n\nTout éteindre quand même ?")
            if r != QMessageBox.StandardButton.Yes:
                return "cancelled"
        converting = [c for c in ctrls if c.running and c.remux is not None and not c.recording]
        wait = False
        if converting:
            choice = ask_conversions(self, converting)
            if choice is None:
                return "cancelled"
            wait = choice == "wait"
        for c in ctrls:
            if not c.running:
                continue
            if wait and c in converting:
                c.close_after_conversion()
            elif not c.shutdown_gracefully():
                c.kill()
        return "waiting" if self._closing_later() else "done"

    def _closing_later(self) -> bool:
        """Des instances attendent la fin de leur rec ou de leur conversion pour s'éteindre."""
        return any(c.running and (c.close_after_remux or c.closing_after_stop)
                   for c in self.manager.controllers.values())

    def _quit_when_off(self) -> None:
        """Fermeture demandée pendant une conversion MP4 : Multi OBS reste ouvert le temps qu'elle finisse
        (les instances s'éteignent alors), puis se ferme."""
        self.statusBar().showMessage("Multi OBS se fermera à la fin des conversions MP4…")
        if self._quit_timer is None:
            self._quit_timer = QTimer(self, interval=1000, timeout=self._check_quit)
        self._quit_timer.start()

    def _check_quit(self) -> None:
        if self._closing_later():
            return
        self._quit_timer.stop()
        self.close()

    # ------------------------------------------------------------------ #
    # Actions globales
    # ------------------------------------------------------------------ #
    def _targets(self):
        return [self.manager.controller(i.id) for i in self.manager.instances
                if i.include_global and self.manager.controller(i.id).connected]

    def toggle_record_all(self) -> None:
        self.record_all()

    def toggle_pause_all(self) -> None:
        self.pause_all()

    def record_all(self, mode: str = "toggle") -> str:
        """Rec des instances liées ; mode toggle / on / off. Renvoie "" ou la raison d'un refus."""
        targets = self._targets()
        if not targets:
            return "Rien d'allumé"
        if mode == "on" or (mode == "toggle" and not any(c.recording for c in targets)):
            if not self.ask_video_name_then(self._start_all):
                return "Nom de la vidéo ?"
        else:
            self._pending_pause = None
            recs = [c for c in targets if c.recording]
            if len(recs) >= 2 and self.manager.settings.get("sync_stop", True):   # même dernière image partout
                self._stopping = {c.inst.id for c in recs}
                self.framesync.stop([self._sync_target(c) for c in recs])
            else:
                for c in recs:
                    c.stop_record()
        return ""

    def _start_all(self) -> None:
        starting = [c for c in self._targets() if not c.recording]
        for c in starting:
            c.start_record()
        # À l'image près : début des fichiers aligné par de courtes pauses (voir framesync.py) ;
        # impossible pour une instance qui ne peut pas se mettre en pause.
        aligned = [c for c in starting if not c.pause_blocked]
        if len(aligned) >= 2 and self.manager.settings.get("sync_start", True):
            self._sync_busy, self._sync_started, self._sync_op = True, time.monotonic(), "align"
            self.framesync.align([self._sync_target(c) for c in aligned])

    def open_twitch_chat(self) -> None:
        """Bouton « Chat Twitch » de l'OBS du live : la première fois, demande le lien de la chaîne (gardé)."""
        link = self.manager.settings.get("twitch_channel", "")
        if not twitch.channel(link):
            text, ok = QInputDialog.getText(self, "Chat Twitch", "Lien de ta chaîne Twitch (ou son nom) :",
                                            QLineEdit.EchoMode.Normal, link)
            if not ok:
                return
            if not twitch.channel(text):
                QMessageBox.warning(self, APP_NAME, "Ce n'est pas un lien de chaîne Twitch.\n"
                                    "Exemple : https://www.twitch.tv/tachaine")
                return
            link = text.strip()
            self.manager.settings["twitch_channel"] = link
            self.manager.save()
        if not twitch.open_chat(link):
            QMessageBox.warning(self, APP_NAME, "Impossible d'ouvrir le chat Twitch.")

    def ask_video_name_then(self, start) -> bool:
        """Option « Demander le nom de la vidéo à chaque lancement de rec » : quand rien n'enregistre encore
        (pas pour un 2e OBS lancé à côté), la fenêtre « Nom de la vidéo » s'ouvre et start() n'est appelé
        qu'une fois le nom validé. Renvoie False tant que la fenêtre attend (rec pas encore lancé)."""
        if not self.manager.settings.get("ask_video_name", False) \
                or any(c.recording for c in self.manager.controllers.values()):
            start()
            return True
        if self._name_dialog is not None:   # déjà ouverte (deuxième appui) : remise devant
            winapi.bring_to_front(int(self._name_dialog.winId()))
            return False
        dlg = self._name_dialog = VideoNameDialog(self.manager, self.video_edit.text())

        def done(result):
            self._name_dialog = None
            dlg.deleteLater()
            if result != QDialog.DialogCode.Accepted:
                self.statusBar().showMessage("Rec annulé", 4000)
                return
            if dlg.name() != self.video_edit.text():
                self.video_edit.setText(dlg.name())
            self._apply_naming()   # le nouveau nom part vers OBS avant le rec (même connexion, dans l'ordre)
            start()
        dlg.finished.connect(done)
        dlg.show()
        # Rec lancé du Stream Deck ou d'un raccourci pendant un jeu : la fenêtre passe devant.
        QTimer.singleShot(0, lambda: dlg.isVisible() and winapi.bring_to_front(int(dlg.winId())))
        return False

    def pause_all(self, mode: str = "toggle") -> str:
        """Pause / reprise des instances liées, à l'image près ; mode toggle / on (pause) / off (reprise)."""
        if self._sync_busy and self._sync_op == "align" and time.monotonic() - self._sync_started < 10:
            # Départ des recs en cours (OBS n'a peut-être même pas encore confirmé le rec) puis alignement :
            # la pause est faite juste après, au lieu d'être perdue. Deux bascules s'annulent.
            if mode == "toggle":
                self._pending_pause = None if self._pending_pause == "toggle" else "toggle"
            else:
                self._pending_pause = mode
            self.statusBar().showMessage("Pause dans un instant : départ des recs en cours d'alignement…", 4000)
            return ""
        recs = [c for c in self._targets() if c.recording]
        blocked = [c.inst.name for c in recs if c.pause_blocked and not c.paused]
        if blocked:
            self.statusBar().showMessage(
                "⚠ Pause impossible pour : " + ", ".join(blocked)
                + " (qualité « Identique au stream ») — clic sur Pause sur la carte pour corriger.", 12000)
        recs = [c for c in recs if not (c.pause_blocked and not c.paused)]
        if not recs:
            return "Pause bloquée" if blocked else "Pas de rec"
        if self._sync_busy and time.monotonic() - self._sync_started < 10:
            # Pause ou reprise encore en cours : refusé (double clic)… sauf si elle est déjà faite partout
            # (OBS l'a confirmée, le compte rendu de la synchro arrive juste après) : sinon un « Tout
            # reprendre » cliqué dès l'affichage de la pause serait perdu.
            done = all(c.paused for c in recs) if self._sync_op == "pause" else not any(c.paused for c in recs)
            if not done:
                return "Patiente"
        self._pending_pause = None   # une demande faite maintenant remplace celle en attente
        resume = mode == "off" or (mode == "toggle" and all(c.paused for c in recs))
        todo = [c for c in recs if c.paused == resume]
        if not todo:
            return ""
        # À l'image près : chaque instance enlève exactement la même durée (voir framesync.py).
        self._sync_busy, self._sync_started = True, time.monotonic()
        self._sync_op = "resume" if resume else "pause"
        targets = [self._sync_target(c) for c in todo]
        if resume:
            self.framesync.resume(targets, resync=bool(self.manager.settings.get("sync_resume", True)))
            return ""
        wait = max(c.resumed_at for c in todo) + RESUME_GUARD_S - time.monotonic()
        if wait > 0:   # OBS ignorerait une pause demandée juste après une reprise
            QTimer.singleShot(int(wait * 1000) + 1, lambda: self.framesync.pause(targets))
        else:
            self.framesync.pause(targets)
        return ""

    @staticmethod
    def _sync_target(c) -> tuple[str, int, str, str]:
        return c.inst.id, c.inst.port, c.inst.password, c.inst.name

    def _on_framesync(self, op: str, ids: list, warnings: list) -> None:
        if op != "stop" and op == self._sync_op:   # pas le compte rendu d'une opération déjà dépassée
            self._sync_busy, self._sync_op = False, ""
        if op == "align" and self._pending_pause:   # pause demandée pendant l'alignement : maintenant
            mode, self._pending_pause = self._pending_pause, None
            QTimer.singleShot(0, lambda: self.pause_all(mode))
        for inst_id in ids:
            c = self.manager.controllers.get(inst_id)
            if c and op == "pause":
                QTimer.singleShot(800, c._check_paused)   # OBS répond « OK » même s'il refuse la pause
        if op == "stop":   # instance injoignable pour l'arrêt synchronisé : arrêt simple
            for inst_id in self._stopping - set(ids):
                c = self.manager.controllers.get(inst_id)
                if c:
                    c.stop_record()
            self._stopping = set()
        if op == "align":
            rounds = (self.framesync.last_report or [{}])[0].get("rounds") or []
            if rounds:
                applog.log("Synchro du départ (écarts en images) : " + " → ".join(map(str, rounds)))
            if not warnings and len(rounds) > 1:
                self.statusBar().showMessage("✓ Départ des recs aligné à l'image près", 6000)
        if warnings:
            self.statusBar().showMessage("⚠ " + " · ".join(warnings), 15000)
            applog.log(f"Synchro {OP_NAMES.get(op, op)} : " + " · ".join(warnings))

    def refresh_global(self) -> None:
        self._refresh_alerts()
        targets = self._targets()
        recording = any(c.recording for c in targets)
        recs = [c for c in targets if c.recording and not (c.pause_blocked and not c.paused)]
        all_paused = bool(recs) and all(c.paused for c in recs)
        self.rec_all_btn.setEnabled(bool(targets))
        self._label(self.rec_all_btn, "Tout arrêter" if recording else "Tout enregistrer")
        self.rec_all_btn.setIcon(icon("stop.svg" if recording else "rec.svg"))
        self._set_prop(self.rec_all_btn, "active", "rec" if recording else "")
        self.pause_all_btn.setEnabled(bool(recs))
        self._label(self.pause_all_btn, "Tout reprendre" if all_paused else "Tout en pause")
        self._label(self.launch_all_btn, "Tout allumer")
        self._label(self.close_all_btn, "Tout éteindre")
        self.pause_all_btn.setIcon(icon("play.svg" if all_paused else "pause.svg"))
        self._set_prop(self.pause_all_btn, "active", "pause" if all_paused else "")
        running = self.manager.any_running()
        self.close_all_btn.setEnabled(running)
        self.launch_all_btn.setEnabled(any(not self.manager.controller(i.id).running
                                           for i in self.manager.instances if not i.card_minimized))
        self.preset_open_btn.setEnabled(self.preset_combo.count() > 0)
        self.preset_del_btn.setEnabled(self.preset_combo.count() > 0)

    @staticmethod
    def _set_prop(w: QWidget, name: str, value) -> None:
        if w.property(name) != value:
            w.setProperty(name, value)
            w.style().unpolish(w)
            w.style().polish(w)

    # ------------------------------------------------------------------ #
    # Instances
    # ------------------------------------------------------------------ #
    def new_instance(self) -> None:
        if not self.manager.obs_source() and obsfiles.runtime_signature() is None:
            QMessageBox.warning(self, APP_NAME, "OBS Studio est introuvable. Indique son dossier dans les paramètres.")
            self.open_settings()
            return
        dlg = InstanceDialog(self.manager, parent=self)
        if not dlg.exec():
            return
        inst = dlg.result_definition()
        self.manager.add_instance(inst)
        if dlg.launch.isChecked():
            imp = {inst.id: paths.USER_OBS_CONFIG} if dlg.import_cfg.isChecked() else None
            self.launch([inst.id], imp)
        elif dlg.import_cfg.isChecked():
            run_task(self, "Préparation de l'instance",
                     lambda p: self.manager.prepare([inst], p, {inst.id: paths.USER_OBS_CONFIG}))

    def edit_instance(self, inst_id: str) -> None:
        inst = next(i for i in self.manager.instances if i.id == inst_id)
        dlg = InstanceDialog(self.manager, inst, self)
        if dlg.exec():
            self.manager.update_instance(dlg.result_definition())
            self.repair_vcams()   # couleur changée : la caméra virtuelle change de nom tout de suite

    def delete_instance(self, inst_id: str) -> None:
        inst = next(i for i in self.manager.instances if i.id == inst_id)
        ctrl = self.manager.controller(inst_id)
        if ctrl.running:
            QMessageBox.information(self, APP_NAME, f"Éteins d'abord l'instance « {inst.name} ».")
            return
        r = QMessageBox.question(
            self, APP_NAME,
            f"Supprimer l'instance « {inst.name} » ?\n\n"
            f"Ses scènes et réglages sont déplacés dans data\\corbeille (récupérables).\n"
            f"Tes enregistrements vidéo ne sont pas touchés.")
        if r == QMessageBox.StandardButton.Yes:
            self.manager.remove_instance(inst_id)

    # ------------------------------------------------------------------ #
    # Préconfigs
    # ------------------------------------------------------------------ #
    def refresh_presets(self) -> None:
        current = self.preset_combo.currentData() or self.manager.settings.get("current_preset")
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for p in self.manager.list_presets():
            n = len(p.get("instances") or [])
            self.preset_combo.addItem(f"{p.get('name')}  ({n} OBS · {p.get('saved_at', '')})", p["slug"])
        idx = self.preset_combo.findData(current)
        if idx >= 0:
            self.preset_combo.setCurrentIndex(idx)
        if self.preset_combo.count() == 0:
            self.preset_combo.setPlaceholderText("Aucune préconfig")
        self.preset_combo.blockSignals(False)

    def save_preset(self) -> None:
        if not self.manager.instances:
            QMessageBox.information(self, APP_NAME, "Crée d'abord au moins une instance.")
            return
        presets = {p["slug"]: p for p in self.manager.list_presets()}
        current = presets.get(self.manager.settings.get("current_preset") or "")
        name, ok = QInputDialog.getText(self, "Enregistrer la préconfig",
                                        "Nom de la préconfig (instances, scènes, réglages…) :",
                                        text=current["name"] if current else "")
        name = name.strip()
        if not ok or not name:
            return
        if self.manager.preset_exists(name):
            r = QMessageBox.question(self, APP_NAME, f"La préconfig « {name} » existe déjà. La remplacer ?")
            if r != QMessageBox.StandardButton.Yes:
                return

        def done(slug):
            self.refresh_presets()
            self.preset_combo.setCurrentIndex(self.preset_combo.findData(slug))
            self.statusBar().showMessage(f"Préconfig « {name} » enregistrée.", 6000)

        run_task(self, "Enregistrement de la préconfig", lambda p: self.manager.save_preset(name, p), on_done=done)

    def open_preset(self) -> None:
        slug = self.preset_combo.currentData()
        if not slug:
            return
        meta = next((p for p in self.manager.list_presets() if p["slug"] == slug), None)
        if not meta:
            return
        if self.manager.any_running():
            box = QMessageBox(QMessageBox.Icon.Information, APP_NAME,
                              "Éteins d'abord toutes les instances avant d'ouvrir une préconfig.", parent=self)
            close_btn = box.addButton("Tout éteindre", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is close_btn:
                self.close_all()
            return
        names = "\n• ".join(i["name"] for i in meta.get("instances") or [])
        box = QMessageBox(QMessageBox.Icon.Question, "Ouvrir la préconfig",
                          f"Ouvrir « {meta['name']} » ?\n\n• {names}\n\n"
                          "Les instances actuelles sont remplacées. Elles sont d'abord sauvegardées "
                          "dans la préconfig « Dernière session (auto) ».", parent=self)
        launch_cb = QCheckBox("Allumer les instances après l'ouverture")
        launch_cb.setChecked(True)
        box.setCheckBox(launch_cb)
        ok_btn = box.addButton("Ouvrir", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not ok_btn:
            return
        launch_after = launch_cb.isChecked()

        def done(_r):
            self.manager.rebuild_controllers()
            self.refresh_presets()
            if launch_after:
                self.launch_all()

        run_task(self, f"Ouverture de « {meta['name']} »", lambda p: self.manager.load_preset(slug, p),
                 on_done=done, on_error=lambda m: (self.manager.rebuild_controllers(),
                                                   QMessageBox.critical(self, APP_NAME, m)))

    def delete_preset(self) -> None:
        slug = self.preset_combo.currentData()
        if not slug:
            return
        name = self.preset_combo.currentText().split("  (")[0]
        r = QMessageBox.question(self, APP_NAME, f"Supprimer la préconfig « {name} » ?\n"
                                 "(les instances actuelles ne sont pas touchées)")
        if r == QMessageBox.StandardButton.Yes:
            self.manager.delete_preset(slug)
            self.refresh_presets()

    # ------------------------------------------------------------------ #
    # Paramètres, raccourcis, barre d'état
    # ------------------------------------------------------------------ #
    def open_settings(self) -> None:
        dlg = SettingsDialog(self.manager, self, self)
        dlg.hotkeysChanged.connect(self.apply_hotkeys)
        dlg.exec()
        for card in self.cards.values():   # types d'enregistrement peut-être modifiés
            card.reload_types()
            card.refresh()
        self.refresh_status()

    def apply_hotkeys(self) -> None:
        s = self.manager.settings
        if not s.get("hotkeys_enabled"):
            self.hotkeys.clear()
            self.status_hotkeys.setText("")
            return
        failed = self.hotkeys.set([(s["hotkey_record"], self.toggle_record_all),
                                   (s["hotkey_pause"], self.toggle_pause_all)])
        txt = f"Raccourcis : {s['hotkey_record']} enregistrer · {s['hotkey_pause']} pause"
        if failed:
            txt += f"  (déjà utilisé : {', '.join(failed)})"
        self.status_hotkeys.setText(txt)

    def refresh_status(self) -> None:
        rt = obsfiles.runtime_signature()
        status = self.manager.runtime_status()
        src = self.manager.obs_source()
        if rt:
            txt = f"OBS {rt.get('version')}"
            if status == "outdated":
                txt += "  ·  mise à jour d'OBS disponible pour les instances (Paramètres)"
        elif src:
            txt = f"OBS {paths.file_version(src / paths.OBS_EXE_REL)} détecté"
        else:
            txt = "OBS Studio introuvable"
        self.status_obs.setText(txt)
        insts = self.manager.instances
        ok = sum(1 for i in insts if vcam.is_registered(i.vcam))
        self.status_vcam.setText(f"Caméras virtuelles : {ok}/{len(insts)} installées" if insts else "")

    def apply_remote(self) -> None:
        """Démarre ou arrête le serveur local du plugin Stream Deck, selon les paramètres."""
        want = bool(self.manager.settings.get("remote_enabled", True))
        if want and self.remote is None:
            server = RemoteServer(self)
            if server.start():
                server.clientsChanged.connect(self._refresh_remote_status)
                self.remote = server
            else:
                server.deleteLater()
        elif not want and self.remote is not None:
            self.remote.stop()
            self.remote.deleteLater()
            self.remote = None
        self._refresh_remote_status()

    def _refresh_remote_status(self) -> None:
        self.deck_status.refresh()

    def watch_vcams(self) -> None:
        """Remet les caméras virtuelles qui ont disparu de Windows : au démarrage, puis chaque minute
        (sinon elles ne revenaient qu'au prochain allumage des instances)."""
        self._vcam_watch = QTimer(self, interval=VCAM_CHECK_MS, timeout=self.repair_vcams)
        self._vcam_watch.start()
        QTimer.singleShot(1500, self.repair_vcams)

    def repair_vcams(self) -> None:
        self.refresh_status()   # « Caméras virtuelles : x/y » reste juste même sans réparation
        if self.manager.settings.get("vcam_scope") != vcam.SCOPE_USER or obsfiles.runtime_signature() is None \
                or getattr(self, "_vcam_task", None) is not None:
            return
        names = self.manager.vcam_names()
        colors = {i.vcam: i.color for i in self.manager.instances if not vcam.up_to_date(i.vcam, names[i.vcam])}
        if not colors:
            return
        applog.log(f"Caméras virtuelles absentes de Windows ou à renommer : {[names[n] for n in sorted(colors)]}"
                   " — réinstallation")

        def done(failed):
            self._vcam_task = None
            watch = getattr(self, "_vcam_watch", None)
            if watch is not None:
                # Échec (droits, antivirus…) : on réessaie de moins en moins souvent, sans abandonner.
                watch.setInterval(min(watch.interval() * 2, 16 * VCAM_CHECK_MS) if failed else VCAM_CHECK_MS)
            applog.log(f"  échec pour {failed}, nouvel essai dans {watch.interval() // 60000 if watch else '?'} min"
                       if failed else "  réinstallées")
            self.refresh_status()

        task = Task(lambda _p: vcam.install(sorted(colors), vcam.SCOPE_USER, colors, names), self)
        task.succeeded.connect(done)
        task.failed.connect(lambda _m: done(sorted(colors)))
        task.finished.connect(task.deleteLater)
        self._vcam_task = task
        task.start()

    def remote_quit(self) -> str:
        """Fermeture demandée par le Stream Deck : comme la croix, selon « À la fermeture de Multi OBS », mais
        sans question sur un rec (refusé, comme éteindre) ni sur une conversion MP4 (attendue). Renvoie "" si
        Multi OBS se ferme, sinon la raison affichée sur la touche."""
        ctrls = list(self.manager.controllers.values())
        if any(c.recording for c in ctrls):
            return "REC en cours"
        if any(c.streaming for c in ctrls):
            return "Live en cours"
        running = [c for c in ctrls if c.running]
        choice = self.manager.settings.get("on_close", "ask")
        if running and choice not in ("keep", "stop"):
            QTimer.singleShot(0, self._show_and_close)   # la question habituelle, fenêtre au premier plan
            return "Réponds dans Multi OBS"
        if running and choice == "stop" and any(c.remux is not None for c in running):
            for c in running:
                if c.remux is not None:   # conversion MP4 : l'OBS s'éteint à la fin, puis Multi OBS se ferme
                    c.close_after_conversion()
                elif not c.shutdown_gracefully():
                    c.kill()
            self._close_choice = "stop"
            self._quit_when_off()
            return "Fermeture après la conversion"
        QTimer.singleShot(0, self.close)   # après la réponse au Stream Deck
        return ""

    def _show_and_close(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        winapi.bring_to_front(int(self.winId()))
        self.close()

    def quit_for_update(self) -> None:
        """Ferme Multi OBS sans rien demander : les instances continuent de tourner et
        la nouvelle version les reprend au démarrage."""
        self._quitting_for_update = True
        self.close()
        QApplication.instance().quit()

    def closeEvent(self, e: QCloseEvent) -> None:
        running = [c.inst.name for c in self.manager.controllers.values() if c.running]
        if running and not getattr(self, "_quitting_for_update", False):
            # Déjà choisi « Tout éteindre » et en attente de la fin des conversions : on ne redemande pas.
            choice = self._close_choice or self.manager.settings.get("on_close", "ask")
            if choice not in ("keep", "stop"):
                box = QMessageBox(QMessageBox.Icon.Question, APP_NAME,
                                  "Des instances OBS sont encore allumées :\n• " + "\n• ".join(running), parent=self)
                keep = box.addButton("Les laisser tourner", QMessageBox.ButtonRole.AcceptRole)
                close = box.addButton("Tout éteindre", QMessageBox.ButtonRole.DestructiveRole)
                cancel = box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
                remember = QCheckBox("Se souvenir de mon choix (modifiable dans les paramètres)")
                box.setCheckBox(remember)
                box.setDefaultButton(keep)
                box.exec()
                clicked = box.clickedButton()
                if clicked is cancel or clicked is None:
                    e.ignore()
                    return
                choice = "stop" if clicked is close else "keep"
                if remember.isChecked():
                    self.manager.settings["on_close"] = choice
                    self.manager.save()
            if choice == "stop":
                result = self.close_all()
                if result == "cancelled":
                    self._close_choice = ""
                    e.ignore()
                    return
                if result == "waiting":   # rec ou conversion MP4 à finir : Multi OBS reste ouvert jusque-là
                    self._close_choice = "stop"
                    e.ignore()
                    self._quit_when_off()
                    return
        self.hotkeys.clear()
        if self.remote is not None:
            self.remote.stop()
        self.framesync.shutdown()
        self.manager.shutdown()
        e.accept()
