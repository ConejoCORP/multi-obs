"""Carte d'une instance : aperçu, audio, état et commandes."""
import os

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox,
                               QPushButton, QToolButton, QVBoxLayout, QWidget)

from .. import naming, theme, twitch
from ..controller import InstanceController
from ..instance_config import record_directory
from ..theme import YAMI
from .dialogs import ask_conversions
from .widgets import DiskBar, MeterWidget, PreviewWidget, RemuxBar, color_dot, human_size, icon


def _restyle(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


def _live_color(c: InstanceController) -> str:
    """Couleur du live (Paramètres → Aperçu), violet par défaut : jamais confondu avec le rouge du rec."""
    key = (c.settings or {}).get("live_color", "purple")
    return key if key in theme.ACCENTS else "purple"


def _status_chip(c: InstanceController) -> tuple[str, str]:
    """Texte et état (style QLabel#chip) de la pastille d'état d'une instance."""
    if c.status == "stopped":
        return "Éteinte", ""
    if c.status == "starting":
        return ("Extinction…" if c.closing else "Démarrage…"), "busy"
    if c.recording and c.paused:
        return "PAUSE", "pause"
    if c.recording:
        return "REC", "rec"
    if c.streaming:
        return "LIVE", f"live_{_live_color(c)}"
    return "Actif", "ready"


class InstanceCard(QFrame):
    launchRequested = Signal(str)
    editRequested = Signal(str)
    deleteRequested = Signal(str)
    moveRequested = Signal(str, int)
    includeChanged = Signal(str, bool)
    recTypeChanged = Signal(str, str)
    previewHiddenChanged = Signal(str, bool)
    minimizeRequested = Signal(str)

    def __init__(self, ctrl: InstanceController, parent=None, vcam_name: str = ""):
        super().__init__(parent)
        self.ctrl = ctrl
        self.filename_preview = None   # fourni par la fenêtre principale
        self.before_record = None      # idem : demande le nom de la vidéo puis lance le rec (option)
        self.open_chat = None          # idem : ouvre le chat Twitch (OBS du live)
        self.vcam_name = vcam_name     # « VCam Rouge » : nom de la caméra virtuelle dans Windows
        self.setObjectName("instanceCard")
        self.setMinimumWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.stripe = QFrame()
        self.stripe.setFixedHeight(4)
        outer.addWidget(self.stripe)

        body = QVBoxLayout()
        body.setContentsMargins(10, 8, 10, 10)
        body.setSpacing(7)
        outer.addLayout(body)

        # En-tête : nom, état, caméra virtuelle, type, menu
        head = QHBoxLayout()
        head.setSpacing(8)
        self.dot = QLabel()
        self.name = QLabel(objectName="cardName")
        self.chip = QLabel(objectName="chip")
        self.vcam_btn = QPushButton(objectName="vcamToggle")
        self.vcam_btn.setIcon(icon("webcam.svg"))
        self.vcam_btn.setIconSize(QSize(13, 13))
        self.vcam_btn.clicked.connect(ctrl.toggle_vcam)
        self.type_combo = QComboBox(objectName="typeCombo")
        self.type_combo.setToolTip("Type d'enregistrement (utilisé dans le nom des fichiers)")
        self.type_combo.currentIndexChanged.connect(
            lambda: self.recTypeChanged.emit(self.ctrl.inst.id, self.type_combo.currentData()))
        self.menu_btn = QToolButton()
        self.menu_btn.setIcon(icon("dots-vert.svg"))
        self.menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_btn.setToolTip("Plus d'options")
        self.min_btn = QToolButton(objectName="flatIcon")
        self.min_btn.setIcon(icon("eye-off.svg"))
        self.min_btn.setIconSize(QSize(16, 16))
        self.min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.min_btn.setToolTip("Masquer : ranger cette instance dans la barre du bas\n"
                                "(elle continue de tourner si elle est allumée)")
        self.min_btn.clicked.connect(lambda: self.minimizeRequested.emit(self.ctrl.inst.id))
        head.addWidget(self.dot)
        head.addWidget(self.name)
        head.addWidget(self.chip)
        head.addStretch(1)
        head.addWidget(self.vcam_btn)
        head.addWidget(self.type_combo)
        head.addWidget(self.min_btn)
        head.addWidget(self.menu_btn)
        body.addLayout(head)
        self._build_menu()

        self.preview = PreviewWidget()
        self.preview.doubleClicked.connect(self._show_window)
        self.preview.expandRequested.connect(lambda: self._set_preview_hidden(False))
        self.preview.targetChanged.connect(ctrl.set_preview_target)
        body.addWidget(self.preview)

        self.meters = MeterWidget()
        self.meters.muteToggled.connect(ctrl.toggle_mute)
        body.addWidget(self.meters)

        self.warning = QLabel(objectName="warning")
        self.warning.setWordWrap(True)
        body.addWidget(self.warning)

        self.stats = QLabel(objectName="stats")
        body.addWidget(self.stats)

        self.disk = DiskBar()
        body.addWidget(self.disk)
        self.remux_bar = RemuxBar()    # à la place du stockage pendant la conversion MP4
        self.remux_bar.hide()
        body.addWidget(self.remux_bar)

        # Commandes
        row = QHBoxLayout()
        row.setSpacing(5)
        self.power_btn = self._button("power.svg", "Allumer",
                                      "Allumer / éteindre l'OBS de cette instance.\n"
                                      "Il tourne réduit : tu le pilotes d'ici.")
        self.power_btn.clicked.connect(self._power)
        self.show_btn = self._button("window.svg", "Voir OBS",
                                     "Ouvrir la fenêtre OBS de cette instance (pour régler scènes et sources)")
        self.show_btn.clicked.connect(self._show_window)
        self.rec_btn = self._button("rec.svg", "Rec", "Démarrer / arrêter l'enregistrement de cette instance")
        self.rec_btn.clicked.connect(self._rec_clicked)
        self.pause_btn = self._button("pause.svg", "Pause", "Mettre en pause / reprendre l'enregistrement")
        self.pause_btn.clicked.connect(self._pause_clicked)
        self.folder_btn = QToolButton(objectName="flatIcon")
        self.folder_btn.setIcon(icon("folder.svg"))
        self.folder_btn.setIconSize(QSize(18, 18))
        self.folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folder_btn.setToolTip("Ouvrir le dossier des enregistrements de cette instance")
        self.folder_btn.clicked.connect(self._open_record_folder)
        # OBS du live : chat Twitch dans une fenêtre à part, en un clic (chaîne dans les paramètres).
        self.chat_btn = QToolButton(objectName="flatIcon")
        self.chat_btn.setIcon(icon("twitch.svg"))
        self.chat_btn.setIconSize(QSize(18, 18))
        self.chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chat_btn.setToolTip("Ouvrir le chat Twitch de ta chaîne dans une fenêtre à part\n"
                                 "(lien de la chaîne : Paramètres → Général)")
        self.chat_btn.clicked.connect(lambda: self.open_chat and self.open_chat())
        # Link : l'instance suit les boutons du haut (Tout enregistrer / Tout en pause) et les raccourcis.
        self.include = QToolButton(objectName="linkToggle")
        self.include.setText("Link")
        self.include.setCheckable(True)
        self.include.setIconSize(QSize(16, 16))
        self.include.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.include.setCursor(Qt.CursorShape.PointingHandCursor)
        self.include.setToolTip("Lié : cette instance suit les boutons « Tout enregistrer » / « Tout en pause »\n"
                                "et les raccourcis clavier. Délie-la pour la piloter à part (ex. l'OBS du live).")
        self.include.toggled.connect(self._link_toggled)
        row.addWidget(self.power_btn)
        row.addWidget(self.show_btn)
        row.addSpacing(8)
        row.addWidget(self.rec_btn)
        row.addWidget(self.pause_btn)
        row.addWidget(self.folder_btn)
        row.addWidget(self.chat_btn)
        row.addStretch(1)
        row.addWidget(self.include)
        body.addLayout(row)

        ctrl.changed.connect(self.refresh)
        ctrl.preview.connect(self.preview.set_image)
        ctrl.meters.connect(self._refresh_meters)
        self.apply_definition()
        self.refresh()
        self._refresh_meters()

    # ------------------------------------------------------------------ #
    def _button(self, icon_name: str, text: str, tip: str) -> QPushButton:
        b = QPushButton(text)
        b.setIcon(icon(icon_name))
        b.setIconSize(QSize(14, 14))
        b.setToolTip(tip)
        return b

    def _build_menu(self) -> None:
        m = QMenu(self)
        m.addAction("Paramètres de l'instance…", lambda: self.editRequested.emit(self.ctrl.inst.id))
        m.addAction("Voir la fenêtre OBS", self._show_window)
        m.addAction("Réduire la fenêtre OBS", self.ctrl.minimize_window)
        self.act_preview = m.addAction("Masquer l'aperçu",
                                       lambda: self._set_preview_hidden(not self.ctrl.inst.preview_hidden))
        m.addSeparator()
        self.act_rec_folder = m.addAction("Ouvrir le dossier des enregistrements", self.ctrl.open_record_folder)
        m.addAction("Ouvrir le dossier de l'instance", lambda: os.startfile(self.ctrl.inst.dir)
                    if self.ctrl.inst.dir.exists() else None)
        m.addSeparator()
        m.addAction("Déplacer à gauche", lambda: self.moveRequested.emit(self.ctrl.inst.id, -1))
        m.addAction("Déplacer à droite", lambda: self.moveRequested.emit(self.ctrl.inst.id, 1))
        m.addAction("Masquer dans la barre du bas", lambda: self.minimizeRequested.emit(self.ctrl.inst.id))
        m.addSeparator()
        self.act_kill = m.addAction("Forcer l'arrêt (si OBS ne répond plus)", self.ctrl.kill)
        m.addAction("Supprimer l'instance…", lambda: self.deleteRequested.emit(self.ctrl.inst.id))
        m.aboutToShow.connect(self._menu_state)
        self.menu_btn.setMenu(m)

    def _menu_state(self) -> None:
        self.act_kill.setEnabled(self.ctrl.running)
        self.act_rec_folder.setEnabled(self.ctrl.connected)
        self.act_preview.setText("Afficher l'aperçu" if self.ctrl.inst.preview_hidden else "Masquer l'aperçu")

    def _link_toggled(self, linked: bool) -> None:
        self.include.setIcon(icon("link.svg" if linked else "link-off.svg"))
        self.includeChanged.emit(self.ctrl.inst.id, linked)

    def _open_record_folder(self) -> None:
        if self.ctrl.connected:
            self.ctrl.open_record_folder()   # dossier du profil OBS actif
            return
        folder = record_directory(self.ctrl.inst)
        if folder.is_dir():
            os.startfile(folder)

    def reload_types(self) -> None:
        """(Re)remplit le menu des types d'enregistrement (modifiables dans les paramètres)."""
        inst = self.ctrl.inst
        self.type_combo.blockSignals(True)
        self.type_combo.clear()
        for key, label in naming.rec_types(self.ctrl.settings):
            self.type_combo.addItem(label, key)
        if self.type_combo.findData(inst.rec_type) < 0:   # type supprimé depuis : on le garde affiché
            self.type_combo.addItem(inst.rec_type, inst.rec_type)
        self.type_combo.setCurrentIndex(max(0, self.type_combo.findData(inst.rec_type)))
        self.type_combo.blockSignals(False)

    def _set_preview_hidden(self, hidden: bool) -> None:
        self.preview.set_collapsed(hidden)
        self.previewHiddenChanged.emit(self.ctrl.inst.id, hidden)

    def apply_definition(self) -> None:
        inst = self.ctrl.inst
        col = theme.accent(inst.color, 2)
        bg = theme.mix(YAMI['grey6'], col, 0.07)
        self.stripe.setStyleSheet(
            f"background: {col}; border-top-left-radius: 6px; border-top-right-radius: 6px;")
        self.setStyleSheet(
            f"QFrame#instanceCard {{ background: {bg}; "
            f"border: 1px solid {theme.mix(YAMI['grey5'], col, 0.25)}; }}")
        self.preview.background = QColor(bg)
        self.preview.update()
        self.remux_bar.track = QColor(theme.mix(YAMI["grey7"], theme.accent(inst.color, 3), 0.55))
        self.dot.setPixmap(color_dot(col, 12))
        self.name.setText(inst.name)
        vcam_name = self.vcam_name or f"VCam {inst.vcam}"
        self.vcam_btn.setText(vcam_name)
        self.vcam_btn.setToolTip(f"Allumer / éteindre la caméra virtuelle « {vcam_name} »\n"
                                 "(pour Discord, ou comme source dans une autre instance)")
        self.include.blockSignals(True)
        self.include.setChecked(inst.include_global)
        self.include.setIcon(icon("link.svg" if inst.include_global else "link-off.svg"))
        self.include.blockSignals(False)
        self.reload_types()
        self.preview.set_collapsed(inst.preview_hidden)

    # ------------------------------------------------------------------ #
    def _power(self) -> None:
        if self.ctrl.running:
            if self.ctrl.recording or self.ctrl.streaming:
                r = QMessageBox.question(self, "Éteindre l'instance",
                                         f"« {self.ctrl.inst.name} » enregistre ou est en live.\n"
                                         "L'éteindre quand même ? (l'enregistrement est finalisé proprement)")
                if r != QMessageBox.StandardButton.Yes:
                    return
            elif self.ctrl.remux is not None:   # OBS convertit encore le rec en MP4
                choice = ask_conversions(self, [self.ctrl])
                if choice is None:
                    return
                if choice == "wait":
                    self.ctrl.close_after_conversion()
                    return
            if not self.ctrl.shutdown_gracefully():
                self.ctrl.kill()
        else:
            self.launchRequested.emit(self.ctrl.inst.id)

    def _rec_clicked(self) -> None:
        if self.ctrl.recording or self.before_record is None:
            self.ctrl.toggle_record()
        else:
            self.before_record(self.ctrl.start_record)

    def _pause_clicked(self) -> None:
        if self.ctrl.pause_blocked and not self.ctrl.paused:
            r = QMessageBox.question(
                self, "Activer la pause",
                f"OBS désactive la pause de « {self.ctrl.inst.name} » : sa qualité d'enregistrement est "
                "« Identique au stream ».\n\nPasser en « Haute qualité, taille moyenne » pour pouvoir mettre en "
                "pause ? (prend effet au prochain enregistrement)")
            if r == QMessageBox.StandardButton.Yes:
                self.ctrl.fix_pause()
            return
        self.ctrl.toggle_pause()

    def _show_window(self) -> None:
        if self.ctrl.running:
            self.ctrl.show_window()
        else:
            self.launchRequested.emit(self.ctrl.inst.id)

    def _set_active(self, w: QWidget, value: str) -> None:
        if w.property("active") != value:
            w.setProperty("active", value)
            _restyle(w)

    def _set_chip(self, text: str, state: str) -> None:
        self.chip.setText(text)
        if self.chip.property("state") != state:
            self.chip.setProperty("state", state)
            _restyle(self.chip)

    def refresh(self) -> None:
        c = self.ctrl
        status = c.status
        connected = c.connected
        fps_setting = int(c.settings.get("preview_fps", 60)) if c.settings else 60

        self._set_chip(*_status_chip(c))
        self.chat_btn.setVisible(twitch.is_live_instance(c.inst))   # suit le nom et le type de l'instance
        if status == "stopped":
            self.preview.placeholder = "Instance éteinte — double-clic pour l'allumer"
        elif status == "starting":
            self.preview.placeholder = "Connexion à OBS…"
        else:
            self.preview.placeholder = ("Aperçu désactivé (Paramètres)" if fps_setting <= 0
                                        else "En attente de l'image…")

        badges = []
        if c.recording:
            badges.append((("PAUSE  " if c.paused else "● ") + (c.rec_time or "00:00:00"),
                           YAMI["yellow3"] if c.paused else YAMI["red3"]))
        if c.streaming:
            badges.append(("LIVE " + (c.stream_time or ""), theme.accent(_live_color(c), 2)))
        if (badges, self.preview.corner) != (self.preview.badges, "VCam active" if c.vcam_on else "") \
                or status == "stopped":
            self.preview.badges = badges
            self.preview.corner = "VCam active" if c.vcam_on else ""
            if status == "stopped":
                self.preview.image = None
            self.preview.update()

        self.power_btn.setText("Éteindre" if c.running else "Allumer")
        self._set_active(self.power_btn, "on" if c.running else "")
        self.show_btn.setEnabled(c.running)
        self.rec_btn.setEnabled(connected)
        self.vcam_btn.setEnabled(connected)
        self.pause_btn.setEnabled(connected and (c.recording or c.pause_blocked))
        self.pause_btn.setToolTip(
            "Pause désactivée par OBS (qualité « Identique au stream ») — clic pour corriger"
            if c.pause_blocked else "Mettre en pause / reprendre l'enregistrement")
        self.rec_btn.setText("Stop" if c.recording else "Rec")
        self.rec_btn.setIcon(icon("stop.svg" if c.recording else "rec.svg"))
        self._set_active(self.rec_btn, "rec" if c.recording else "")
        self.pause_btn.setText("Reprendre" if c.paused else "Pause")
        self.pause_btn.setIcon(icon("play.svg" if c.paused else "pause.svg"))
        self._set_active(self.pause_btn, "pause" if c.paused else "")
        self._set_active(self.vcam_btn, "on" if c.vcam_on else "")

        if connected:
            parts = [f"{c.fps or 0:.0f} ips", f"CPU {c.cpu or 0:.1f} %", f"{c.memory or 0:.0f} Mo"]
            if c.render_skipped or c.output_skipped:
                parts.append(f"perdues {c.render_skipped + c.output_skipped}")
            if c.recording and c.rec_bytes:
                parts.append(human_size(c.rec_bytes))
            if c.scene:
                parts.append(f"Scène : {c.scene}")
            if not c.inst.preview_hidden and fps_setting > 0:
                preview = f"aperçu {c.preview_fps_measured:.0f} i/s" if c.preview_fps_measured else "aperçu"
                if c.preview_note:
                    preview += f" ({c.preview_note})"
                parts.append(preview)
            self.stats.setText("  ·  ".join(parts))
        elif status == "starting":
            self.stats.setText("En attente d'OBS…")
        else:
            self.stats.setText(f"Port {c.inst.port}  ·  dossier {c.inst.id}")

        remux = c.remux_progress
        self.remux_bar.setVisible(remux is not None)
        self.disk.setVisible(remux is None)
        if remux is not None:
            self.remux_bar.set_state(remux, str(c.remux["mp4"]), c.close_after_remux)
        else:
            folder = getattr(c, "disk_path", "")
            display = folder
            if c.recording and c.rec_path:
                display = c.rec_path
            elif folder and self.filename_preview:
                display = folder.rstrip("/\\") + "\\" + self.filename_preview()
            self.disk.set_state(c.disk_free, c.disk_total, folder, c.bytes_per_second(), display)

        warns = c.warnings()
        self.warning.setVisible(bool(warns))
        self.warning.setText("⚠ " + "   ⚠ ".join(warns))
        critical = bool(c.critical())   # problème grave : en rouge
        if self.warning.property("critical") != critical:
            self.warning.setProperty("critical", critical)
            _restyle(self.warning)

    def _refresh_meters(self) -> None:
        c = self.ctrl
        rows = [(n, c.levels.get(n, []), c.mutes.get(n, False)) for n in c.meter_order]
        empty = ""
        if c.connected and not rows:
            empty = "Aucune source audio active"
        self.meters.set_rows(rows, empty)


class MiniChip(QFrame):
    """Instance réduite : pastille de la barre du bas (état, alertes) avec « Remettre »."""
    restoreRequested = Signal(str)

    def __init__(self, ctrl: InstanceController, parent=None, vcam_name: str = ""):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setObjectName("miniChip")
        col = theme.accent(ctrl.inst.color, 2)
        self.setStyleSheet(f"QFrame#miniChip {{ background: {theme.mix(YAMI['grey6'], col, 0.10)}; "
                           f"border: 1px solid {theme.mix(YAMI['grey5'], col, 0.35)}; border-radius: 6px; }}")
        self.setToolTip("Double-clic pour remettre l'instance en haut")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 3, 3, 3)
        lay.setSpacing(7)
        dot = QLabel()
        dot.setPixmap(color_dot(col, 10))
        name = QLabel(ctrl.inst.name, objectName="miniName")
        self.state = QLabel(objectName="chip")
        self.vcam = QLabel("VCam", objectName="miniTag")
        self.vcam.setToolTip(f"Caméra virtuelle « {vcam_name or 'VCam ' + str(ctrl.inst.vcam)} » allumée")
        self.alert = QLabel("⚠", objectName="miniAlert")
        restore = QToolButton(objectName="miniRestore")
        restore.setIcon(icon("up.svg"))
        restore.setIconSize(QSize(10, 10))
        restore.setText("Remettre")
        restore.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        restore.setCursor(Qt.CursorShape.PointingHandCursor)
        restore.setToolTip("Remettre cette instance en haut, parmi les autres")
        restore.clicked.connect(lambda: self.restoreRequested.emit(self.ctrl.inst.id))
        for w in (dot, name, self.state, self.vcam, self.alert, restore):
            lay.addWidget(w)
        ctrl.changed.connect(self.refresh)
        self.refresh()

    def mouseDoubleClickEvent(self, e) -> None:
        self.restoreRequested.emit(self.ctrl.inst.id)

    def refresh(self) -> None:
        c = self.ctrl
        text, state = _status_chip(c)
        if c.recording and c.rec_time:   # pas d'aperçu ici : la durée est dans la pastille
            text += "  " + c.rec_time
        elif c.remux_progress is not None:
            text, state = f"Conversion MP4  {int(c.remux_progress * 100)} %", "busy"
            if c.close_after_remux:
                text += "  · puis extinction"
        self.state.setText(text)
        if self.state.property("state") != state:
            self.state.setProperty("state", state)
            _restyle(self.state)
        self.vcam.setVisible(c.connected and c.vcam_on)
        warns = c.warnings()
        self.alert.setVisible(bool(warns))
        self.alert.setToolTip("\n".join(warns))
        critical = bool(c.critical())
        if self.alert.property("critical") != critical:
            self.alert.setProperty("critical", critical)
            _restyle(self.alert)
