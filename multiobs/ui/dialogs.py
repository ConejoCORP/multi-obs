"""Fenêtres : création / modification d'instance, paramètres."""
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog,
                               QKeySequenceEdit, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QSlider, QTabWidget, QVBoxLayout, QWidget)

from .. import applog, naming, obsfiles, paths, streamdeck, theme, vcam
from ..controller import duration_text
from ..instance_config import InstanceDef
from .tasks import run_task
from .widgets import SwatchButtonPainter

PREVIEW_FPS_CHOICES = [(0, "Désactivé"), (1, "1 image/s"), (5, "5 images/s"), (10, "10 images/s"),
                       (15, "15 images/s"), (30, "30 images/s"), (60, "60 images/s")]
PREVIEW_QUALITY_CHOICES = [(1, "1/1 — pleine résolution"), (2, "1/2"), (4, "1/4"), (8, "1/8")]
PREVIEW_BACKGROUND_CHOICES = [(-1, "Comme au premier plan"), (5, "5 images/s"), (1, "1 image/s"),
                              (0, "En pause")]
# SafeGuard : crans des curseurs (silence en secondes, 0 = jamais ; images ; Go)
SILENCE_CHOICES = [10, 20, 30, 45, 60, 90, 120, 180, 300, 600, 0]
DROP_WARN_CHOICES = [1, 2, 3, 5, 10, 20, 30, 50, 100]
LOW_DISK_CHOICES = [2, 5, 10, 20, 50, 100]


class VideoNameDialog(QDialog):
    """Nom de la vidéo demandé au lancement d'un rec (option : on oublie de le changer d'une vidéo à l'autre).
    Fenêtre à part, toujours devant : visible même si Multi OBS est réduit (rec lancé du Stream Deck)."""

    def __init__(self, manager, current: str):
        super().__init__(None)
        self.manager = manager
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        lay = _base_dialog(self, "Nom de la vidéo")
        lay.addWidget(QLabel("Nom de la vidéo pour ce rec :"))
        self.edit = QLineEdit(current)
        self.edit.setPlaceholderText("nom de la vidéo")
        self.edit.setMinimumWidth(420)
        self.edit.selectAll()   # taper remplace l'ancien nom
        lay.addWidget(self.edit)
        self.preview = QLabel()
        self.preview.setProperty("muted", True)
        lay.addWidget(self.preview)
        buttons = QDialogButtonBox()
        go = buttons.addButton("Lancer le rec", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Annuler", QDialogButtonBox.ButtonRole.RejectRole)
        go.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.edit.textChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        s = self.manager.settings
        self.preview.setText("output : " + naming.preview(s.get("filename_template"), self.manager.creator(),
                                                          "cam", self.name()))

    def name(self) -> str:
        return self.edit.text().strip()


def ask_conversions(parent, converting) -> str | None:
    """Instances qui convertissent encore leur rec en MP4 : « wait » (les éteindre à la fin de la conversion),
    « now » (tout de suite : le MP4 ne sera pas fini, le MKV reste complet) ou None (annuler)."""
    one = len(converting) == 1
    lines = "\n".join(f"• {c.inst.name} : {int((c.remux_progress or 0) * 100)} %" for c in converting)
    box = QMessageBox(QMessageBox.Icon.Warning, "Conversion en MP4 en cours",
                      ("Cette instance convertit" if one else "Ces instances convertissent")
                      + " encore l'enregistrement en MP4 :\n" + lines
                      + ("\n\nÉteinte" if one else "\n\nÉteintes") + " maintenant, le MP4 ne serait pas fini "
                      "(le MKV, lui, est complet).", parent=parent)
    wait = box.addButton("Éteindre à la fin de la conversion", QMessageBox.ButtonRole.AcceptRole)
    now = box.addButton("Éteindre maintenant", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(wait)
    box.exec()
    clicked = box.clickedButton()
    if clicked is wait:
        return "wait"
    if clicked is now:
        return "now"
    return None


class StepSlider(QWidget):
    """Curseur à crans sur une liste de valeurs, la valeur choisie écrite à droite."""

    def __init__(self, choices: list[int], current: int, text):
        super().__init__()
        self.choices, self.text = choices, text
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, len(choices) - 1)
        self.slider.setPageStep(1)
        self.value_label = QLabel(objectName="sliderValue")
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.value_label)
        # Valeur écrite à la main dans multiobs.json : le cran le plus proche (« jamais » seulement pour 0).
        if current in choices:
            index = choices.index(current)
        else:
            index = min((i for i, v in enumerate(choices) if v), key=lambda i: abs(choices[i] - current))
        self.slider.setValue(index)
        self.slider.valueChanged.connect(self._show)
        self._show()

    def _show(self) -> None:
        self.value_label.setText(self.text(self.value()))

    def value(self) -> int:
        return self.choices[self.slider.value()]


def _base_dialog(dlg: QDialog, title: str) -> QVBoxLayout:
    dlg.setWindowTitle(title)
    dlg.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(12)
    return lay


class ColorPicker(QHBoxLayout):
    def __init__(self, current: str):
        super().__init__()
        self.setSpacing(6)
        self.group = QButtonGroup()
        self.buttons = {}
        self.value = current
        for key in theme.ACCENT_ORDER:
            label, _shades = theme.ACCENTS[key]
            b = QPushButton()
            b.setObjectName("swatch")
            b.setToolTip(label)
            b.setCheckable(True)
            b.setStyleSheet("QPushButton#swatch, QPushButton#swatch:checked, QPushButton#swatch:hover"
                            " { background: transparent; border: none; }")
            b.clicked.connect(lambda _=False, k=key: self.select(k))
            self.group.addButton(b)
            self.buttons[key] = b
            self.addWidget(b)
        self.addStretch(1)
        self.select(current)

    def select(self, key: str) -> None:
        self.value = key
        for k, b in self.buttons.items():
            b.setIcon(SwatchButtonPainter.icon(theme.accent(k, 2), k == key))
            b.setIconSize(QSize(26, 26))
            b.setChecked(k == key)
        if getattr(self, "on_change", None):
            self.on_change(key)


class InstanceDialog(QDialog):
    def __init__(self, manager, inst: InstanceDef | None = None, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.inst = inst
        creating = inst is None
        lay = _base_dialog(self, "Nouvelle instance" if creating else f"Paramètres — {inst.name}")
        self.setMinimumWidth(480)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        default_name = f"Instance {len(manager.instances) + 1}"
        self.name = QLineEdit(inst.name if inst else default_name)
        self.name.selectAll()
        form.addRow("Nom", self.name)

        self.colors = ColorPicker(inst.color if inst else manager.next_color())
        form.addRow("Couleur", self.colors)

        # Caméra virtuelle : nommée d'après la couleur (« VCam Rouge »), numéro attribué tout seul.
        free = manager.free_vcams(inst.id if inst else None)
        self.vcam_index = inst.vcam if inst else (free[0] if free else None)
        self.vcam_label = QLabel()
        self.vcam_label.setToolTip("Nom de la caméra dans Discord, Zoom… — il suit la couleur de l'instance")
        form.addRow("Caméra virtuelle", self.vcam_label)
        self.colors.on_change = lambda _k: self._refresh_vcam_label()
        self._refresh_vcam_label()

        self.rec_type = QComboBox()
        for key, label in naming.rec_types(manager.settings):
            self.rec_type.addItem(label, key)
        current_type = inst.rec_type if inst else naming.guess_type(self.name.text(), manager.settings)
        if self.rec_type.findData(current_type) < 0:   # type supprimé depuis : on le garde affiché
            self.rec_type.addItem(current_type, current_type)
        self.rec_type.setCurrentIndex(max(0, self.rec_type.findData(current_type)))
        self.rec_type.setToolTip("Utilisé dans le nom des fichiers : NOM-TYPE-VIDÉO-DATE")
        form.addRow("Type d'enregistrement", self.rec_type)
        if not inst:
            self.name.textEdited.connect(lambda t: self.rec_type.setCurrentIndex(
                max(0, self.rec_type.findData(naming.guess_type(t, manager.settings)))))
        lay.addLayout(form)

        self.minimized = QCheckBox("Ouvrir la fenêtre OBS réduite (on la pilote depuis Multi OBS)")
        self.minimized.setChecked(inst.start_minimized if inst else True)
        lay.addWidget(self.minimized)

        self.import_cfg = None
        self.launch = None
        if creating:
            has_obs_cfg = (paths.USER_OBS_CONFIG / "user.ini").exists()
            self.import_cfg = QCheckBox("Partir de mes scènes et réglages OBS actuels")
            self.import_cfg.setToolTip(f"Copie depuis {paths.USER_OBS_CONFIG}\n"
                                       "(ton OBS habituel n'est pas modifié)")
            self.import_cfg.setChecked(has_obs_cfg)
            self.import_cfg.setEnabled(has_obs_cfg)
            lay.addWidget(self.import_cfg)
            self.launch = QCheckBox("Allumer l'instance maintenant")
            self.launch.setChecked(True)
            lay.addWidget(self.launch)
        else:
            hint = QLabel("Le nom et la couleur s'appliquent à OBS au prochain lancement de l'instance. "
                          "La caméra virtuelle change de nom tout de suite (redémarre Discord pour le voir).")
            hint.setWordWrap(True)
            hint.setProperty("muted", True)
            lay.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Créer" if creating else "Enregistrer")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("role", "primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _refresh_vcam_label(self) -> None:
        if self.vcam_index is None:
            self.vcam_label.setText("aucune de libre (9 maximum)")
            return
        others = [i for i in self.manager.instances if not self.inst or i.id != self.inst.id]
        me = InstanceDef(id="_", name="", color=self.colors.value, vcam=self.vcam_index, port=0, password="")
        self.vcam_label.setText(vcam.display_names(others + [me])[self.vcam_index])

    def _accept(self):
        if not self.name.text().strip():
            self.name.setFocus()
            return
        if self.vcam_index is None:
            QMessageBox.warning(self, "Multi OBS", "Plus aucune caméra virtuelle libre (9 maximum).")
            return
        self.accept()

    def result_definition(self) -> InstanceDef:
        name = self.name.text().strip()
        if self.inst:
            from dataclasses import replace
            return replace(self.inst, name=name, color=self.colors.value,
                           vcam=self.vcam_index, start_minimized=self.minimized.isChecked(),
                           rec_type=self.rec_type.currentData())
        d = self.manager.new_definition(name, self.colors.value, self.vcam_index)
        d.start_minimized = self.minimized.isChecked()
        d.rec_type = self.rec_type.currentData()
        d.needs_default_audio = not self.import_cfg.isChecked()
        return d


class SettingsDialog(QDialog):
    hotkeysChanged = Signal()

    def __init__(self, manager, main, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.main = main
        lay = _base_dialog(self, "Paramètres de Multi OBS")
        self.setMinimumWidth(770)   # les sept onglets visibles d'un coup
        tabs = QTabWidget()
        lay.addWidget(tabs)

        def page(title: str) -> QVBoxLayout:
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(12, 14, 12, 12)
            v.setSpacing(12)
            tabs.addTab(w, title)
            return v

        general, safe, recs, preview, vcams, keys, deck = (
            page("Général"), page("SafeGuard"), page("Enregistrements"), page("Aperçu"),
            page("Caméras virtuelles"), page("Raccourcis"), page("Stream Deck"))

        # ---- Général : OBS, fenêtre, mises à jour
        g = QGroupBox("OBS Studio")
        gl = QVBoxLayout(g)
        row = QHBoxLayout()
        self.src = QLineEdit(str(manager.obs_source() or ""))
        self.src.setPlaceholderText("Dossier d'installation d'OBS Studio")
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.src, 1)
        row.addWidget(browse)
        gl.addLayout(row)
        self.version = QLabel()
        self.version.setProperty("muted", True)
        self.version.setWordWrap(True)
        gl.addWidget(self.version)
        self.update_btn = QPushButton("Mettre à jour les instances avec cet OBS")
        self.update_btn.setToolTip("À faire après avoir mis à jour ton OBS habituel.\n"
                                   "Les scènes et réglages des instances sont conservés.")
        self.update_btn.clicked.connect(self._update_obs)
        gl.addWidget(self.update_btn, 0, Qt.AlignmentFlag.AlignLeft)
        general.addWidget(g)

        g = QGroupBox("Fenêtre")
        form = QFormLayout(g)
        form.setVerticalSpacing(10)
        self.on_close = QComboBox()
        for v, label in (("ask", "Demander"), ("keep", "Laisser tourner les instances"), ("stop", "Tout éteindre")):
            self.on_close.addItem(label, v)
        self.on_close.setCurrentIndex(max(0, self.on_close.findData(manager.settings.get("on_close", "ask"))))
        form.addRow("À la fermeture de Multi OBS", self.on_close)
        self.taskbar_rename = QCheckBox("Barre des tâches : « OBS Blue — Caméra » avec le logo OBS à la couleur de l'instance")
        self.taskbar_rename.setChecked(bool(manager.settings.get("taskbar_rename", True)))
        form.addRow(self.taskbar_rename)
        general.addWidget(g)

        g = QGroupBox("Twitch")
        form = QFormLayout(g)
        self.twitch = QLineEdit(manager.settings.get("twitch_channel", ""))
        self.twitch.setPlaceholderText("https://www.twitch.tv/tachaine")
        self.twitch.setToolTip("Le bouton « Chat Twitch » de l'OBS du live (type Live, ou « live » dans son nom) "
                               "ouvre le chat de cette chaîne dans une fenêtre à part.")
        form.addRow("Lien de ta chaîne", self.twitch)
        general.addWidget(g)

        from .. import APP_VERSION
        g = QGroupBox("Mises à jour")
        row = QHBoxLayout(g)
        self.check_updates = QCheckBox(f"Vérifier au démarrage (version actuelle : {APP_VERSION})")
        self.check_updates.setChecked(bool(manager.settings.get("check_updates", True)))
        row.addWidget(self.check_updates, 1)
        b = QPushButton("Vérifier maintenant")
        b.clicked.connect(lambda: getattr(main, "updater", None) and main.updater.check_now())
        row.addWidget(b)
        general.addWidget(g)
        general.addStretch(1)

        # ---- SafeGuard : surveillance des recs et des lives, alertes, stabilité des OBS
        # Deux lignes fixes : un texte à la ligne automatique fausse la hauteur de la page (onglets).
        intro = QLabel("SafeGuard surveille tes OBS pendant les recs et les lives, et te prévient au moindre souci.\n"
                       "Les problèmes graves (rec bloqué, OBS fermé, micro perdu…) ont toujours le bandeau rouge.")
        intro.setProperty("muted", True)
        safe.addWidget(intro)

        g = QGroupBox("Quand prévenir")
        grid = QGridLayout(g)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(2)
        self.silence = StepSlider(SILENCE_CHOICES, int(manager.settings.get("silence_warn_s", 120)),
                                  lambda v: duration_text(v) if v else "Jamais")
        self.drop_warn = StepSlider(DROP_WARN_CHOICES, int(manager.settings.get("drop_warn_frames", 10)),
                                    lambda v: f"{v} image" if v == 1 else f"{v} images")
        self.low_disk = StepSlider(LOW_DISK_CHOICES, int(manager.settings.get("low_disk_gb", 10)),
                                   lambda v: f"{v} Go")
        for row, (label, slider, hint) in enumerate((
                ("Silence", self.silence, "« Pas de son » sur la carte après ce temps sans aucun son pendant un rec"),
                ("Images perdues", self.drop_warn, "Avertissement sur la carte ; l'alerte rouge part toujours dès "
                                                   "6 images en 10 s"),
                ("Disque presque plein", self.low_disk, "Alerte rouge pendant un rec quand il reste moins que ça "
                                                        "sur le disque"))):
            grid.addWidget(QLabel(label), 2 * row, 0)
            grid.addWidget(slider, 2 * row, 1)
            note = QLabel(hint)
            note.setProperty("muted", True)
            note.setContentsMargins(0, 0, 0, 10 if row < 2 else 0)
            grid.addWidget(note, 2 * row + 1, 1)
        grid.setColumnStretch(1, 1)
        safe.addWidget(g)

        g = QGroupBox("Où prévenir")
        gl = QVBoxLayout(g)
        self.alert_taskbar = QCheckBox("Faire clignoter Multi OBS dans la barre des tâches")
        self.alert_taskbar.setChecked(bool(manager.settings.get("alert_taskbar", True)))
        self.alert_streamdeck = QCheckBox("Stream Deck : cadre rouge et « ALERTE » sur les touches de l'OBS concerné")
        self.alert_streamdeck.setChecked(bool(manager.settings.get("alert_streamdeck", True)))
        gl.addWidget(self.alert_taskbar)
        gl.addWidget(self.alert_streamdeck)
        safe.addWidget(g)

        g = QGroupBox("Stabilité des OBS")
        gl = QVBoxLayout(g)
        self.obs_priority = QCheckBox("Donner la priorité aux OBS sur les jeux (conseillé)")
        self.obs_priority.setChecked(bool(manager.settings.get("obs_priority", True)))
        self.obs_priority.setToolTip("Quand un jeu sature le processeur, l'encodeur d'OBS décroche et répète des "
                                     "images (saccades dans le rec).\nAvec cette option, les OBS passent en priorité "
                                     "« au-dessus de la normale » : 0 à 0,1 % d'images perdues dans nos tests,\n"
                                     "contre 9 à 14 % sans. Un autre choix fait dans OBS (Paramètres → Avancé) "
                                     "est respecté.")
        self.obs_unthrottle = QCheckBox("Empêcher Windows de ralentir les OBS et Multi OBS en arrière-plan (conseillé)")
        self.obs_unthrottle.setChecked(bool(manager.settings.get("obs_unthrottle", True)))
        self.obs_unthrottle.setToolTip("Windows 11 bride les programmes réduits ou en arrière-plan (derrière un jeu, "
                                       "par exemple) :\nOBS rendait alors chaque image jusqu'à 15 ms en retard "
                                       "(petites saccades dans le rec), et les aperçus de Multi OBS saccadaient.\n"
                                       "Débridés, les OBS restent à moins de 2 ms et les aperçus restent fluides.")
        gl.addWidget(self.obs_priority)
        gl.addWidget(self.obs_unthrottle)
        safe.addWidget(g)
        safe.addStretch(1)

        # ---- Enregistrements : nom des fichiers, conversion, types
        g = QGroupBox("Fichiers")
        gl = QVBoxLayout(g)
        form = QFormLayout()
        self.template = QLineEdit(manager.settings.get("filename_template") or naming.DEFAULT_TEMPLATE)
        self.template.setToolTip("{user} = ton nom, {type} = le type de l'instance (cam, ecran, live…), "
                                 "{video} = nom de la vidéo,\n"
                                 "{date} = date du jour, {time} = heure de début (évite d'écraser une prise)")
        self.template.textChanged.connect(self._refresh_template_preview)
        form.addRow("Modèle de nom", self.template)
        self.template_preview = QLabel()
        self.template_preview.setProperty("muted", True)
        form.addRow("", self.template_preview)
        gl.addLayout(form)
        self.auto_remux = QCheckBox("Convertir automatiquement les MKV en MP4 à la fin de l'enregistrement")
        self.auto_remux.setChecked(bool(manager.settings.get("auto_remux", True)))
        self.auto_remux.setToolTip("Conversion sans perte faite par OBS (quelques secondes). Le MKV reste "
                                   "un enregistrement sûr en cas de plantage pendant le rec.")
        gl.addWidget(self.auto_remux)
        self.trash_mkv = QCheckBox("Mettre le MKV à la corbeille une fois le MP4 prêt")
        self.trash_mkv.setChecked(bool(manager.settings.get("trash_mkv_after_remux", False)))
        self.trash_mkv.setToolTip("Seulement si le MP4 est complet (taille vérifiée). Le MKV reste récupérable "
                                  "dans la corbeille Windows.")
        gl.addWidget(self.trash_mkv)
        self.auto_remux.toggled.connect(self.trash_mkv.setEnabled)
        self.trash_mkv.setEnabled(self.auto_remux.isChecked())
        self.ask_video_name = QCheckBox("Demander le nom de la vidéo à chaque lancement de rec")
        self.ask_video_name.setChecked(bool(manager.settings.get("ask_video_name", False)))
        self.ask_video_name.setToolTip("Au lancement d'un rec (Tout enregistrer, Rec d'une carte, Stream Deck, "
                                       "raccourci), une petite fenêtre propose le nom de la vidéo à changer.\n"
                                       "Pas redemandé pour un OBS lancé pendant qu'un autre enregistre déjà.")
        gl.addWidget(self.ask_video_name)
        recs.addWidget(g)

        g = QGroupBox("Synchro à l'image près")
        gl = QVBoxLayout(g)
        self.sync_start = QCheckBox("Aligner le départ des recs (courte pause dans la première seconde)")
        self.sync_start.setChecked(bool(manager.settings.get("sync_start", True)))
        self.sync_start.setToolTip("Chaque OBS démarre son rec quand il peut (jusqu'à 16 images d'écart mesurées).\n"
                                   "Juste après le départ, les OBS en avance font une courte pause pour que tous les "
                                   "fichiers commencent sur la même image.\nLe début de chaque fichier a alors un petit "
                                   "saut (0,1 s) : attends une seconde avant de parler.")
        self.sync_stop = QCheckBox("Finir tous les recs sur la même image")
        self.sync_stop.setChecked(bool(manager.settings.get("sync_stop", True)))
        self.sync_stop.setToolTip("« Tout arrêter » coupe chaque OBS à la même image du fichier : même nombre "
                                  "d'images partout.")
        self.sync_resume = QCheckBox("Recaler les fichiers à chaque reprise après une pause")
        self.sync_resume.setChecked(bool(manager.settings.get("sync_resume", True)))
        self.sync_resume.setToolTip("Si un fichier a pris quelques images d'écart (images sautées par un OBS "
                                    "surchargé…), il est rattrapé pendant la pause,\nsans coupure de plus. "
                                    "Au plus un quart de seconde.")
        for box in (self.sync_start, self.sync_stop, self.sync_resume):
            gl.addWidget(box)
        note = QLabel("Avec « Tout enregistrer », « Tout en pause » et « Tout arrêter ». La pause, elle, est "
                      "toujours faite à la même image partout.")
        note.setWordWrap(True)
        note.setProperty("muted", True)
        gl.addWidget(note)
        recs.addWidget(g)

        g = QGroupBox("Types d'enregistrement")
        gl = QHBoxLayout(g)
        self.types_list = QListWidget()
        self.types_list.setMinimumHeight(130)
        self.types_list.setToolTip("Chaque instance a un type (menu sur sa carte), utilisé dans le nom des fichiers.")
        for key, label in naming.rec_types(manager.settings):
            self._add_type_item(label, key)
        gl.addWidget(self.types_list, 1)
        col = QVBoxLayout()
        for text, slot in (("Ajouter…", self._type_add), ("Renommer…", self._type_rename),
                           ("Supprimer", self._type_remove), ("Par défaut", self._type_reset)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            col.addWidget(b)
        col.addStretch(1)
        gl.addLayout(col)
        recs.addWidget(g)
        recs.addStretch(1)

        # ---- Aperçu
        g = QGroupBox("Aperçus des instances")
        form = QFormLayout(g)
        form.setVerticalSpacing(10)
        self.fps = QComboBox()
        for v, label in PREVIEW_FPS_CHOICES:
            self.fps.addItem(label, v)
        self.fps.setCurrentIndex(max(0, self.fps.findData(int(manager.settings["preview_fps"]))))
        self.fps.currentIndexChanged.connect(lambda: manager.set_preview(fps=self.fps.currentData()))
        form.addRow("Rafraîchissement", self.fps)
        self.quality = QComboBox()
        for v, label in PREVIEW_QUALITY_CHOICES:
            self.quality.addItem(label, v)
        self.quality.setCurrentIndex(max(0, self.quality.findData(int(manager.settings.get("preview_divisor", 4)))))
        self.quality.currentIndexChanged.connect(lambda: manager.set_preview(divisor=self.quality.currentData()))
        form.addRow("Qualité", self.quality)
        self.bg_fps = QComboBox()
        for v, label in PREVIEW_BACKGROUND_CHOICES:
            self.bg_fps.addItem(label, v)
        self.bg_fps.setCurrentIndex(max(0, self.bg_fps.findData(
            int(manager.settings.get("preview_background_fps", -1)))))
        self.bg_fps.currentIndexChanged.connect(lambda: manager.set_preview(background_fps=self.bg_fps.currentData()))
        self.bg_fps.setToolTip("Quand une autre appli est au premier plan. Fenêtre réduite, session "
                               "verrouillée ou écran éteint : les aperçus sont toujours en pause.")
        form.addRow("Quand Multi OBS est en arrière-plan", self.bg_fps)
        self.live_color = ColorPicker(manager.settings.get("live_color", "purple"))
        live_label = QLabel("Couleur du live")
        live_label.setToolTip("Pastille « LIVE » avec la durée du live, en haut à gauche de l'aperçu, et étiquette "
                              "« LIVE » à côté du nom de l'instance.\nLe rec reste en rouge.")
        form.addRow(live_label, self.live_color)
        perf = QLabel("Aperçu sans compression, jamais plus de pixels que l'écran n'en affiche. "
                      "Pause automatique quand il ne sert à rien, et ralenti tout seul si OBS perd des "
                      "images. En 1/1 sur un grand aperçu, compte une dizaine d'images/s par instance.")
        perf.setWordWrap(True)
        perf.setProperty("muted", True)
        form.addRow(perf)
        preview.addWidget(g)
        preview.addStretch(1)

        # ---- Caméras virtuelles
        g = QGroupBox("Caméras virtuelles")
        gl = QVBoxLayout(g)
        self.vcam_status = QLabel()
        self.vcam_status.setWordWrap(True)
        gl.addWidget(self.vcam_status)
        info = QLabel("Chaque instance a sa propre caméra virtuelle, au nom de sa couleur (« VCam Rouge », "
                      "« VCam Bleu »…), utilisable dans Discord, Zoom, le navigateur… ou dans une autre instance "
                      "(source « Périphérique de capture vidéo »). Pense à redémarrer Discord pour voir un "
                      "nouveau nom.")
        info.setWordWrap(True)
        info.setProperty("muted", True)
        gl.addWidget(info)
        row = QHBoxLayout()
        b = QPushButton("Installer / réparer")
        b.clicked.connect(self._install_vcams)
        row.addWidget(b)
        b = QPushButton("Désinstaller")
        b.clicked.connect(self._uninstall_vcams)
        row.addWidget(b)
        row.addStretch(1)
        gl.addLayout(row)
        vcams.addWidget(g)
        vcams.addStretch(1)

        # ---- Raccourcis
        g = QGroupBox("Raccourcis clavier globaux")
        form = QFormLayout(g)
        form.setVerticalSpacing(10)
        self.hk_enabled = QCheckBox("Activer (ils marchent même si Multi OBS n'est pas au premier plan)")
        self.hk_enabled.setChecked(bool(manager.settings["hotkeys_enabled"]))
        form.addRow(self.hk_enabled)
        self.hk_record = QKeySequenceEdit(QKeySequence(manager.settings["hotkey_record"]))
        self.hk_pause = QKeySequenceEdit(QKeySequence(manager.settings["hotkey_pause"]))
        for w in (self.hk_record, self.hk_pause):
            w.setMaximumSequenceLength(1)
        form.addRow("Tout enregistrer / arrêter", self.hk_record)
        form.addRow("Tout mettre en pause / reprendre", self.hk_pause)
        hint = QLabel("Ils agissent sur les instances liées (Link). Pour un Stream Deck, le plugin Multi OBS "
                      "fait bien plus : onglet « Stream Deck ».")
        hint.setWordWrap(True)
        hint.setProperty("muted", True)
        form.addRow(hint)
        keys.addWidget(g)
        keys.addStretch(1)

        # ---- Stream Deck : plugin livré avec Multi OBS + serveur local qu'il utilise
        g = QGroupBox("Plugin Stream Deck")
        gl = QVBoxLayout(g)
        self.sd_status = QLabel()
        self.sd_status.setWordWrap(True)
        gl.addWidget(self.sd_status)
        info = QLabel("Des touches colorées pour chaque OBS, ou pour toutes les instances : enregistrer, pause "
                      "à l'image près, allumer / éteindre, caméra virtuelle, timecode du rec, et une touche à "
                      "appui court / appui long. Une fois installé : catégorie « Multi OBS » dans la liste des "
                      "actions du logiciel Stream Deck, glisse les actions sur tes touches.")
        info.setWordWrap(True)
        info.setProperty("muted", True)
        gl.addWidget(info)
        row = QHBoxLayout()
        self.sd_install = QPushButton("Installer le plugin")
        self.sd_install.clicked.connect(self._install_streamdeck)
        row.addWidget(self.sd_install)
        self.sd_hint = QLabel()
        self.sd_hint.setWordWrap(True)
        self.sd_hint.setProperty("muted", True)
        row.addWidget(self.sd_hint, 1)
        gl.addLayout(row)
        deck.addWidget(g)

        g = QGroupBox("Connexion")
        gl = QVBoxLayout(g)
        self.remote_enabled = QCheckBox("Autoriser le pilotage par le Stream Deck")
        self.remote_enabled.setChecked(bool(manager.settings.get("remote_enabled", True)))
        self.remote_enabled.toggled.connect(self._toggle_remote)
        gl.addWidget(self.remote_enabled)
        self.remote_status = QLabel()
        self.remote_status.setWordWrap(True)
        gl.addWidget(self.remote_status)
        hint = QLabel("Connexion locale (cet ordinateur seulement), protégée par un jeton : une page web ne peut "
                      "pas piloter tes OBS.")
        hint.setWordWrap(True)
        hint.setProperty("muted", True)
        gl.addWidget(hint)
        deck.addWidget(g)
        deck.addStretch(1)
        self._deck_timer = QTimer(self, interval=1000, timeout=self._refresh_streamdeck)
        self._deck_timer.start()
        self._refresh_streamdeck()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Fermer")
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._refresh()

    # Stream Deck ---------------------------------------------------------- #
    def _refresh_streamdeck(self) -> None:
        bundled, installed = streamdeck.bundled_version(), streamdeck.installed_version()
        if not streamdeck.software_installed():
            text, button = "Logiciel Stream Deck introuvable : installe-le depuis elgato.com, puis reviens ici.", ""
        elif not installed:
            text, button = "Plugin pas encore installé.", "Installer le plugin"
        elif streamdeck.is_outdated(installed):
            text, button = (f"Plugin installé en version {installed} : la version {bundled} est prête.",
                            "Mettre à jour le plugin")
        else:
            text, button = f"✓ Plugin installé (version {streamdeck.short(installed)}).", "Réinstaller le plugin"
        self.sd_status.setText(text)
        self.sd_install.setVisible(bool(button))
        self.sd_install.setText(button)
        remote = getattr(self.main, "remote", None)
        if not self.manager.settings.get("remote_enabled", True):
            status = "Pilotage désactivé."
        elif remote is None:
            status = "Serveur local arrêté."
        elif remote.connected_clients():
            status = "✓ Stream Deck connecté."
        else:
            status = f"En attente du Stream Deck (port {remote.port})."
        self.remote_status.setText(status)

    def _install_streamdeck(self) -> None:
        if not streamdeck.source().is_dir():
            QMessageBox.warning(self, "Multi OBS", "Plugin introuvable dans cette installation de Multi OBS.")
            return
        try:
            streamdeck.install()
        except OSError as e:
            QMessageBox.warning(self, "Multi OBS", f"Le logiciel Stream Deck n'a pas pu ouvrir le plugin :\n{e}")
            return
        self.sd_hint.setText("Le logiciel Stream Deck s'ouvre : accepte l'installation, c'est tout.")

    def _toggle_remote(self, on: bool) -> None:
        self.manager.settings["remote_enabled"] = on
        self.manager.save()
        if hasattr(self.main, "apply_remote"):
            self.main.apply_remote()
        self._refresh_streamdeck()

    # Types d'enregistrement ---------------------------------------------- #
    def _add_type_item(self, label: str, key: str | None) -> None:
        it = QListWidgetItem(label)
        it.setData(Qt.ItemDataRole.UserRole, key)
        self.types_list.addItem(it)

    def _type_keys(self, skip=None) -> set:
        return {naming.type_key(self.types_list.item(i).text()) for i in range(self.types_list.count())
                if self.types_list.item(i) is not skip}

    def _type_add(self):
        label, ok = QInputDialog.getText(self, "Nouveau type", "Nom du type (ex. Podcast, Face cam…) :")
        label = naming.clean(label)
        if not ok or not label:
            return
        if naming.type_key(label) in self._type_keys():
            QMessageBox.information(self, "Multi OBS", f"Le type « {label} » existe déjà.")
            return
        self._add_type_item(label, None)

    def _type_rename(self):
        it = self.types_list.currentItem()
        if not it:
            return
        label, ok = QInputDialog.getText(self, "Renommer le type", "Nouveau nom :", text=it.text())
        label = naming.clean(label)
        if not ok or not label:
            return
        if naming.type_key(label) in self._type_keys(skip=it):
            QMessageBox.information(self, "Multi OBS", f"Le type « {label} » existe déjà.")
            return
        it.setText(label)

    def _type_remove(self):
        it = self.types_list.currentItem()
        if not it:
            return
        if self.types_list.count() <= 1:
            QMessageBox.information(self, "Multi OBS", "Il faut garder au moins un type.")
            return
        self.types_list.takeItem(self.types_list.row(it))

    def _type_reset(self):
        self.types_list.clear()
        for key, label in naming.DEFAULT_REC_TYPES:
            self._add_type_item(label, key)

    def _save_types(self) -> None:
        types, seen, renamed = [], set(), {}
        for i in range(self.types_list.count()):
            it = self.types_list.item(i)
            label = it.text().strip()
            key = naming.type_key(label)
            if not label or key in seen:
                continue
            seen.add(key)
            types.append([key, label])
            old = it.data(Qt.ItemDataRole.UserRole)
            if old and old != key:
                renamed[old] = key
        self.manager.settings["rec_types"] = types or [list(t) for t in naming.DEFAULT_REC_TYPES]
        for inst in self.manager.instances:   # type renommé : les instances suivent
            if inst.rec_type in renamed:
                inst.rec_type = renamed[inst.rec_type]

    def _refresh_template_preview(self):
        s = self.manager.settings
        ex = naming.preview(self.template.text(), s.get("creator", "") or "Nom", "cam",
                            s.get("video_name", "") or "Vidéo")
        self.template_preview.setText(f"output : {ex}.mp4")

    def _refresh(self):
        self._refresh_template_preview()
        src = Path(self.src.text()) if self.src.text() else None
        src_ver = paths.file_version(src / paths.OBS_EXE_REL) if src else ""
        rt = obsfiles.runtime_signature()
        rt_ver = rt.get("version") if rt else None
        status = self.manager.runtime_status()
        txt = f"OBS installé : {src_ver or 'introuvable'}   ·   Copie utilisée par Multi OBS : {rt_ver or 'pas encore créée'}"
        if status == "outdated":
            txt += "\nTon OBS a été mis à jour : clique sur « Mettre à jour les instances »."
        self.version.setText(txt)
        running = self.manager.any_running()
        self.update_btn.setEnabled(bool(src_ver) and not running)
        if running:
            self.update_btn.setToolTip("Éteins d'abord toutes les instances.")
        lines = []
        names = self.manager.vcam_names()
        for inst in self.manager.instances:
            scope = vcam.registered_scope(inst.vcam)
            ok = vcam.up_to_date(inst.vcam, names[inst.vcam])
            state = "installée ✓" if ok else ("à réparer" if scope else "non installée")
            lines.append(f"{names[inst.vcam]} ({inst.name}) : {state}")
        self.vcam_status.setText("\n".join(lines) or "Aucune instance pour l'instant.")

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Dossier d'OBS Studio", self.src.text())
        if d:
            if not (Path(d) / paths.OBS_EXE_REL).exists():
                QMessageBox.warning(self, "Multi OBS", "Ce dossier ne contient pas bin\\64bit\\obs64.exe.")
                return
            self.src.setText(d)
            self.manager.settings["obs_source"] = d
            self.manager.save()
            self._refresh()

    def _update_obs(self):
        if self.manager.any_running():
            QMessageBox.information(self, "Multi OBS", "Éteins d'abord toutes les instances.")
            return
        run_task(self, "Mise à jour d'OBS", self.manager.update_obs,
                 on_done=lambda _r: (self._refresh(), QMessageBox.information(
                     self, "Multi OBS", "Les instances utilisent maintenant la nouvelle version d'OBS.")))

    def _install_vcams(self):
        indices = sorted({i.vcam for i in self.manager.instances})
        if not indices:
            return
        if obsfiles.runtime_signature() is None:
            QMessageBox.information(self, "Multi OBS", "Lance d'abord une instance une fois.")
            return

        def done(failed):
            self._refresh()
            if failed:
                QMessageBox.warning(self, "Multi OBS", "Échec pour : " + ", ".join(map(str, failed)))
            else:
                QMessageBox.information(self, "Multi OBS", "Caméras virtuelles installées.\n"
                                        "Redémarre les instances et les applis (Discord…) qui doivent les voir.")
        # De nouveau gérées par Multi OBS : réinstallées à l'allumage et si elles disparaissent.
        self.manager.settings["vcam_scope"] = vcam.SCOPE_USER
        self.manager.save()
        applog.log(f"Paramètres : « Installer / réparer » les caméras virtuelles {indices}")
        watch = getattr(self.main, "_vcam_watch", None)
        if watch is not None:
            watch.start(60_000)
        colors = {i.vcam: i.color for i in self.manager.instances}
        names = self.manager.vcam_names()
        run_task(self, "Caméras virtuelles", lambda _p: vcam.install(indices, vcam.SCOPE_USER, colors, names),
                 on_done=done)

    def _uninstall_vcams(self):
        r = QMessageBox.question(self, "Multi OBS", "Désinstaller toutes les caméras virtuelles de Multi OBS ?\n"
                                 "(la caméra « OBS Virtual Camera » d'OBS n'est pas touchée)\n\n"
                                 "Elles ne reviendront qu'avec « Installer / réparer ».")
        if r == QMessageBox.StandardButton.Yes:
            self.manager.settings["vcam_scope"] = "none"   # sinon réinstallées au prochain allumage
            self.manager.save()
            applog.log("Paramètres : « Désinstaller » les caméras virtuelles")
            vcam.uninstall(list(range(1, obsfiles.MAX_VCAM + 1)))
            self._refresh()

    def done(self, r):
        s = self.manager.settings
        s["hotkeys_enabled"] = self.hk_enabled.isChecked()
        s["hotkey_record"] = self.hk_record.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        s["hotkey_pause"] = self.hk_pause.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        s["auto_remux"] = self.auto_remux.isChecked()
        s["taskbar_rename"] = self.taskbar_rename.isChecked()
        s["on_close"] = self.on_close.currentData()
        s["check_updates"] = self.check_updates.isChecked()
        s["trash_mkv_after_remux"] = self.trash_mkv.isChecked()
        s["live_color"] = self.live_color.value
        s["twitch_channel"] = self.twitch.text().strip()
        s["ask_video_name"] = self.ask_video_name.isChecked()
        s["sync_start"] = self.sync_start.isChecked()
        s["sync_stop"] = self.sync_stop.isChecked()
        s["sync_resume"] = self.sync_resume.isChecked()
        s["silence_warn_s"] = self.silence.value()
        s["drop_warn_frames"] = self.drop_warn.value()
        s["low_disk_gb"] = self.low_disk.value()
        s["alert_taskbar"] = self.alert_taskbar.isChecked()
        s["alert_streamdeck"] = self.alert_streamdeck.isChecked()
        s["obs_priority"] = self.obs_priority.isChecked()
        s["obs_unthrottle"] = self.obs_unthrottle.isChecked()
        for c in self.manager.controllers.values():
            c.apply_priority()
        self.manager.apply_unthrottle()
        self._save_types()
        self.manager.set_naming(template=self.template.text().strip())  # enregistre + applique
        self.hotkeysChanged.emit()
        super().done(r)
