"""Carte d'une instance : aperçu, audio, état et commandes."""
import os

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (QBoxLayout, QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox,
                               QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from .. import naming, theme, twitch
from ..controller import InstanceController
from ..instance_config import record_directory
from ..theme import YAMI
from . import cardlayout
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


def _clear(layout) -> None:
    """Vide un layout sans détruire ses widgets (ils restent dans la carte)."""
    while layout.count():
        layout.takeAt(0)


class InstanceCard(QFrame):
    STRIPE_H = 4
    BORDER = 1                  # bordure de la carte (feuille de style), de chaque côté
    MARGINS = (10, 8, 10, 10)   # gauche, haut, droite, bas
    SPACING = 7
    BUTTON_ROW_GAP = 5
    layoutNeeded = Signal()     # la taille prévue pour la carte a changé (vumètres en plus)
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
        self.open_chat = None          # idem : open_chat(id) ouvre le chat de cette instance (OBS du live)
        self.fill_chat_menu = None     # idem : fill_chat_menu(menu, id), menu du bouton Chat (flèche, clic droit)
        self.vcam_name = vcam_name     # « VCam Rouge » : nom de la caméra virtuelle dans Windows
        self.setObjectName("instanceCard")
        self._mode = ""                # "vertical" (commandes sous l'aperçu) ou "side" (à droite)
        self._layout_args = None       # dernière disposition demandée par la fenêtre : (mode, aperçu, carte)
        self._meter_rows = 2           # vumètres prévus dans la taille des cartes (le plus vu depuis l'ouverture)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.stripe = QFrame()
        self.stripe.setFixedHeight(self.STRIPE_H)
        outer.addWidget(self.stripe)

        body = self._body = QVBoxLayout()
        body.setContentsMargins(*self.MARGINS)
        body.setSpacing(self.SPACING)
        outer.addLayout(body)

        # En-tête : nom, état, caméra virtuelle, type, menu
        head = self._head = QHBoxLayout()
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

        # Aperçu puis le reste (vumètres, infos, disque, boutons) : dessous, ou à droite (« side »).
        self._content = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self._content.setSpacing(self.SPACING)
        body.addLayout(self._content)
        self.preview = PreviewWidget()
        self.preview.doubleClicked.connect(self._show_window)
        self.preview.expandRequested.connect(lambda: self._set_preview_hidden(False))
        self.preview.targetChanged.connect(ctrl.set_preview_target)
        self._content.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)
        self.details = QWidget()
        details = QVBoxLayout(self.details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(self.SPACING)
        self._content.addWidget(self.details, 0, Qt.AlignmentFlag.AlignTop)

        self.meters = MeterWidget()
        self.meters.muteToggled.connect(ctrl.toggle_mute)
        details.addWidget(self.meters)

        self.warning = QLabel(objectName="warning")
        self.warning.setWordWrap(True)
        details.addWidget(self.warning)

        # Infos (ips, CPU, scène…) : hauteur fixe, texte coupé à la largeur (une ligne sous l'aperçu, deux à
        # côté). Sans retour à la ligne automatique : mis à jour chaque seconde, il ferait recalculer toute
        # la fenêtre à chaque fois (interface moins fluide).
        self.stats = QLabel(objectName="stats")
        self.stats.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._stats_parts: tuple[list[str], list[str]] = ([], [])
        self.stats.installEventFilter(self)   # largeur changée : texte recoupé
        details.addWidget(self.stats)

        self.disk = DiskBar()
        details.addWidget(self.disk)
        self.remux_bar = RemuxBar()    # à la place du stockage pendant la conversion MP4
        self.remux_bar.hide()
        details.addWidget(self.remux_bar)

        # Commandes : une ligne sous l'aperçu, deux lignes à sa droite (voir _arrange)
        self.controls = QWidget()
        controls = QVBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(self.BUTTON_ROW_GAP)
        self._row1, self._row2 = QHBoxLayout(), QHBoxLayout()
        for row in (self._row1, self._row2):
            row.setSpacing(5)
            controls.addLayout(row)
        details.addWidget(self.controls)
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
        # OBS du live : bouton « Chat » (logo du chat choisi) ; le clic ouvre le chat Twitch, YouTube ou le
        # multi-chat dans une fenêtre à part, la flèche (ou le clic droit) le menu pour choisir.
        self.chat_btn = QToolButton(objectName="chatButton")
        self.chat_btn.setText("Chat")
        self.chat_btn.setIconSize(QSize(14, 14))
        self.chat_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.chat_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        chat_menu = QMenu(self.chat_btn)
        chat_menu.aboutToShow.connect(
            lambda: self.fill_chat_menu and self.fill_chat_menu(chat_menu, self.ctrl.inst.id))
        self.chat_btn.setMenu(chat_menu)
        self.chat_btn.clicked.connect(lambda: self.open_chat and self.open_chat(self.ctrl.inst.id))
        self.chat_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_btn.customContextMenuRequested.connect(lambda _pos: self.chat_btn.showMenu())
        self._chat_platform = None
        self.refresh_chat_button(self.chat_choice())
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
        self._arrange("vertical")

        ctrl.changed.connect(self.refresh)
        ctrl.preview.connect(self.preview.set_image)
        ctrl.meters.connect(self._refresh_meters)
        self.apply_definition()
        self.refresh()
        self._refresh_meters()

    # ------------------------------------------------------------------ #
    # Disposition (choisie par la fenêtre selon le nombre de cartes et la place, voir cardlayout.py)
    # ------------------------------------------------------------------ #
    def _arrange(self, mode: str) -> None:
        """Commandes sous l'aperçu (une ligne de boutons) ou à sa droite (colonne, deux lignes de boutons)."""
        if mode == self._mode:
            return
        self._mode = mode
        side = mode == "side"
        self._content.setDirection(QBoxLayout.Direction.LeftToRight if side else QBoxLayout.Direction.TopToBottom)
        self._content.setSpacing(cardlayout.GAP if side else self.SPACING)
        if side:
            self.details.setFixedWidth(cardlayout.SIDE_W)
        else:
            self.details.setMinimumWidth(0)
            self.details.setMaximumWidth(16777215)
        self.stats.ensurePolished()
        self.stats.setFixedHeight(self._stats_lines() * QFontMetrics(self.stats.font()).lineSpacing())
        self._set_stats(*self._stats_parts)
        _clear(self._row1)
        _clear(self._row2)
        if side:
            for w in (self.power_btn, self.show_btn):
                self._row1.addWidget(w)
            self._row1.addStretch(1)
            self._row1.addWidget(self.include)
            for w in (self.rec_btn, self.pause_btn, self.folder_btn, self.chat_btn):
                self._row2.addWidget(w)
            self._row2.addStretch(1)
        else:
            self._row1.addWidget(self.power_btn)
            self._row1.addWidget(self.show_btn)
            self._row1.addSpacing(8)
            for w in (self.rec_btn, self.pause_btn, self.folder_btn, self.chat_btn):
                self._row1.addWidget(w)
            self._row1.addStretch(1)
            self._row1.addWidget(self.include)

    def chrome(self, meter_rows: int) -> cardlayout.Chrome:
        """Place prise autour de l'aperçu, avec meter_rows vumètres (pour calculer la disposition)."""
        for w in (self, self.stats, self.power_btn, self.name, self.type_combo):
            w.ensurePolished()
        left, top, right, bottom = self.MARGINS
        head_h = self._head.sizeHint().height()
        stats_h = QFontMetrics(self.stats.font()).lineSpacing()   # par ligne (1 dessous, 2 à côté)
        btn_h = max(w.sizeHint().height() for w in (self.power_btn, self.rec_btn, self.chat_btn, self.include))
        meters_h = meter_rows * MeterWidget.ROW_H
        disk_h = self.disk.minimumHeight()
        sp = self.SPACING
        return cardlayout.Chrome(
            pad_w=left + right + 2 * self.BORDER,
            top=self.STRIPE_H + self.BORDER + top + head_h + sp,
            below=sp + meters_h + sp + stats_h + sp + disk_h + sp + btn_h + bottom + self.BORDER,
            bottom=bottom + self.BORDER,
            side_h=meters_h + sp + 2 * stats_h + sp + disk_h + sp + 2 * btn_h + self.BUTTON_ROW_GAP)

    def eventFilter(self, obj, e):
        if obj is self.stats and e.type() == QEvent.Type.Resize:
            self._set_stats(*self._stats_parts)
        return super().eventFilter(obj, e)

    def _stats_lines(self) -> int:
        return 2 if self._mode == "side" else 1

    def _set_stats(self, first: list[str], second: list[str]) -> None:
        """Infos de la carte : performances, puis scène et aperçu (à la suite, ou sur une 2e ligne à côté de
        l'aperçu), chaque ligne coupée à la largeur."""
        self._stats_parts = (first, second)
        sep = "  ·  "
        lines = [sep.join(first + second)] if self._stats_lines() == 1 else [sep.join(first), sep.join(second)]
        fm = QFontMetrics(self.stats.font())
        width = max(40, self.stats.width())
        text = "\n".join(fm.elidedText(line, Qt.TextElideMode.ElideRight, width) for line in lines)
        if text != self.stats.text():
            self.stats.setText(text)
        self.stats.setToolTip(sep.join(first + second))

    def meter_rows(self) -> int:
        """Vumètres prévus pour cette carte : le plus qu'elle en a eu (2 au moins), pour une taille stable."""
        return self._meter_rows

    def apply_layout(self, mode: str, pw: int, card_w: int) -> None:
        """Disposition donnée par la fenêtre : mode, largeur de l'aperçu, largeur de la carte."""
        self._layout_args = (mode, pw, card_w)
        if self.preview.collapsed:   # aperçu masqué : un bandeau en haut, les commandes dessous
            self._arrange("vertical")
            self.preview.set_box(card_w - (self.MARGINS[0] + self.MARGINS[2] + 2 * self.BORDER))
        else:
            self._arrange(mode)
            self.preview.set_box(pw)
        self.setFixedWidth(card_w)

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

    CHAT_BUTTONS = {"twitch": ("twitch.svg", "Ouvrir le chat Twitch de ta chaîne dans une fenêtre à part"),
                    "youtube": ("youtube.svg", "Ouvrir le chat YouTube de ton live dans une fenêtre à part"),
                    "multi": ("multichat.svg", "Ouvrir le multi-chat : Twitch et YouTube dans une seule fenêtre")}

    def chat_choice(self) -> str:
        """Chat de cette instance : le sien (menu du bouton), sinon celui par défaut."""
        return self.ctrl.inst.chat_platform or (self.ctrl.settings or {}).get("chat_platform", "twitch")

    def refresh_chat_button(self, platform: str) -> None:
        """Logo du bouton du chat : celui du chat qu'il ouvre (Twitch, YouTube ou les deux)."""
        if platform == self._chat_platform:
            return
        self._chat_platform = platform
        name, tip = self.CHAT_BUTTONS.get(platform, self.CHAT_BUTTONS["twitch"])
        self.chat_btn.setIcon(icon(name))
        self.chat_btn.setToolTip(tip + "\nFlèche ou clic droit : choisir Twitch, YouTube ou le multi-chat, et tes chaînes")

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
        if self._layout_args:
            self.apply_layout(*self._layout_args)
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
        if self._layout_args:
            self.apply_layout(*self._layout_args)

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
        live = twitch.is_live_instance(c.inst)   # suit le nom et le type de l'instance
        self.chat_btn.setVisible(live)
        if live:
            self.refresh_chat_button(self.chat_choice())
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
            perf = [f"{c.fps or 0:.0f} ips", f"CPU {c.cpu or 0:.1f} %", f"{c.memory or 0:.0f} Mo"]
            if c.render_skipped or c.output_skipped:
                perf.append(f"perdues {c.render_skipped + c.output_skipped}")
            if c.recording and c.rec_bytes:
                perf.append(human_size(c.rec_bytes))
            where = [f"Scène : {c.scene}"] if c.scene else []
            if not c.inst.preview_hidden and fps_setting > 0:
                preview = f"aperçu {c.preview_fps_measured:.0f} i/s" if c.preview_fps_measured else "aperçu"
                if c.preview_note:
                    preview += f" ({c.preview_note})"
                where.append(preview)
            self._set_stats(perf, where)
        elif status == "starting":
            self._set_stats(["En attente d'OBS…"], [])
        else:
            self._set_stats([f"Port {c.inst.port}", f"dossier {c.inst.id}"], [])

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
        if len(rows) > self._meter_rows:   # plus de vumètres que prévu : la fenêtre recalcule les tailles
            self._meter_rows = len(rows)
            self.layoutNeeded.emit()


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
