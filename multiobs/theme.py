"""Palette Yami (thème par défaut d'OBS) : style de Multi OBS et thèmes colorés des instances."""
from . import paths

YAMI = {
    "grey1": "#5B6273", "grey2": "#4E5566", "grey3": "#464B59", "grey4": "#3C404D",
    "grey5": "#323540", "grey6": "#272A33", "grey7": "#1D1F26", "grey8": "#13141A",
    "white1": "#FFFFFF", "white4": "#B4B4B4", "white5": "#969696",
    "blue1": "#718CDC", "blue2": "#476BD7", "blue3": "#284CB8", "blue4": "#213E97",
    "red1": "#E85E75", "red2": "#E33B57", "red3": "#C01C37", "red4": "#A1172E",
    "green2": "#37D247", "green3": "#25A231", "yellow2": "#E5AF24", "yellow3": "#B88A16",
}

# Familles de couleurs de la palette OBS : clair -> foncé.
ACCENTS = {
    "blue":   ("Bleu",   ["#718CDC", "#476BD7", "#284CB8", "#213E97", "#1A3278", "#162A64"]),
    "red":    ("Rouge",  ["#E85E75", "#E33B57", "#C01C37", "#A1172E", "#7D1224", "#590D1A"]),
    "green":  ("Vert",   ["#59D966", "#37D247", "#25A231", "#1E8528", "#17641E", "#0F4313"]),
    "purple": ("Violet", ["#997FDC", "#805FD3", "#5B34BF", "#4D2CA0", "#3D2380", "#2E1A60"]),
    "teal":   ("Cyan",   ["#3DBEF5", "#16B1F3", "#0981B4", "#086F9B", "#065374", "#04374E"]),
    "pink":   ("Rose",   ["#E5619A", "#E03E84", "#C11F65", "#9E1A53", "#7B1441", "#580E2E"]),
    "yellow": ("Jaune",  ["#EABC48", "#E5AF24", "#B88A16", "#926E11", "#6E520D", "#493709"]),
}
ACCENT_ORDER = ["red", "teal", "green", "purple", "yellow", "pink", "blue"]


def accent(key: str, shade: int = 2) -> str:
    return ACCENTS.get(key, ACCENTS["blue"])[1][shade]


def mix(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(a, b))


def tinted_greys(key: str) -> dict:
    """Gris Yami teintés de la couleur de l'instance (fond coloré bien visible)."""
    tint = accent(key, 2)
    amounts = {1: 0.16, 2: 0.17, 3: 0.18, 4: 0.19, 5: 0.20, 6: 0.20, 7: 0.20, 8: 0.18}
    return {f"grey{i}": mix(YAMI[f"grey{i}"], tint, amounts[i]) for i in range(1, 9)}


def instance_theme_id(inst_id: str) -> str:
    return f"com.multiobs.{inst_id}"


def instance_ovt(inst_id: str, name: str, key: str) -> str:
    shades = ACCENTS.get(key, ACCENTS["blue"])[1]
    greys = tinted_greys(key)
    safe_name = name.replace("'", " ")
    vars_ = "\n".join(f"    --{k}: {v};" for k, v in greys.items())
    return f"""@OBSThemeMeta {{
    name: 'Multi OBS - {safe_name}';
    id: '{instance_theme_id(inst_id)}';
    extends: 'com.obsproject.Yami';
    author: 'Multi OBS';
    dark: 'true';
}}

@OBSThemeVars {{
{vars_}
    --primary: {shades[2]};
    --primary_light: {shades[1]};
    --primary_lighter: {shades[0]};
    --primary_dark: {shades[3]};
    --primary_darker: {shades[4]};
}}
"""


def app_qss() -> str:
    y = YAMI
    ic = paths.ICONS.as_posix()
    # Pastille « LIVE » à la couleur choisie dans les paramètres (état live_<couleur>)
    live_chips = "\n".join(f'QLabel#chip[state="live_{k}"] {{ background: {shades[2]}; color: white; }}'
                           for k, (_name, shades) in ACCENTS.items())
    return f"""
* {{ font-family: "Segoe UI", "Open Sans", sans-serif; font-size: 10pt; color: {y['white1']}; }}
QMainWindow, QDialog, QWidget#central, QScrollArea, QWidget#cardsContainer {{ background: {y['grey7']}; }}
QToolTip {{ background: {y['grey6']}; color: {y['white1']}; border: 1px solid {y['grey4']}; padding: 4px; }}
QLabel {{ background: transparent; }}
QLabel[muted="true"] {{ color: {y['white5']}; }}
QLabel#sectionTitle {{ color: {y['white5']}; font-size: 8pt; font-weight: 600; }}
QLabel#appTitle {{ font-size: 14pt; font-weight: 600; }}

QFrame#topBar {{ background: {y['grey6']}; border-bottom: 1px solid {y['grey8']}; }}
QFrame#vsep {{ background: {y['grey4']}; max-width: 1px; min-width: 1px; margin: 4px 6px; }}
QStatusBar {{ background: {y['grey6']}; color: {y['white5']}; border-top: 1px solid {y['grey8']}; }}
QStatusBar QLabel {{ color: {y['white5']}; padding: 0 8px; }}
QStatusBar QLabel#deckDot {{ padding: 0 0 1px 8px; font-size: 9pt; }}
QStatusBar QLabel#deckText {{ padding: 0 2px; }}
QToolButton#deckButton {{ background: {y['blue3']}; border: 1px solid {y['blue2']}; border-radius: 4px;
    padding: 0 9px; min-height: 16px; font-size: 9pt; }}
QToolButton#deckButton:hover {{ background: {y['blue2']}; border-color: {y['blue1']}; }}

QPushButton, QToolButton {{
    background: {y['grey4']}; border: 1px solid {y['grey4']}; border-radius: 4px;
    padding: 5px 12px; min-height: 18px;
}}
QToolButton {{ padding: 5px 6px; }}
QPushButton:hover, QToolButton:hover {{ background: {y['grey3']}; border-color: {y['grey1']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {y['grey7']}; }}
QPushButton:disabled, QToolButton:disabled {{ background: {y['grey5']}; border-color: {y['grey5']}; color: {y['grey1']}; }}
QPushButton:checked, QToolButton:checked {{ background: {y['blue3']}; border-color: {y['blue2']}; }}
QPushButton[role="primary"] {{ background: {y['blue3']}; border-color: {y['blue3']}; }}
QPushButton[role="primary"]:hover {{ background: {y['blue2']}; border-color: {y['blue1']}; }}
QPushButton[active="rec"], QToolButton[active="rec"] {{ background: {y['red3']}; border-color: {y['red2']}; }}
QPushButton[active="rec"]:hover, QToolButton[active="rec"]:hover {{ background: {y['red2']}; }}
QPushButton[active="pause"], QToolButton[active="pause"] {{ background: {y['yellow3']}; border-color: {y['yellow2']}; }}
QPushButton[active="on"], QToolButton[active="on"] {{ background: {y['blue3']}; border-color: {y['blue2']}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

QLineEdit, QComboBox, QSpinBox, QKeySequenceEdit {{
    background: {y['grey4']}; border: 1px solid {y['grey4']}; border-radius: 4px;
    padding: 4px 8px; min-height: 18px; selection-background-color: {y['blue3']};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ border-color: {y['grey1']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {y['blue3']}; background: {y['grey7']}; }}
QLineEdit:disabled {{ background: {y['grey6']}; border-color: {y['grey5']}; color: {y['grey1']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url({ic}/down.svg); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {y['grey6']}; border: 1px solid {y['grey4']}; selection-background-color: {y['blue3']}; outline: 0; }}

QCheckBox {{ spacing: 6px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator:unchecked {{ image: url({ic}/checkbox_unchecked.svg); }}
QCheckBox::indicator:unchecked:hover {{ image: url({ic}/checkbox_unchecked_focus.svg); }}
QCheckBox::indicator:checked {{ image: url({ic}/checkbox_checked.svg); }}
QCheckBox::indicator:checked:hover {{ image: url({ic}/checkbox_checked_focus.svg); }}
QCheckBox::indicator:checked:disabled {{ image: url({ic}/checkbox_checked_disabled.svg); }}
QCheckBox::indicator:unchecked:disabled {{ image: url({ic}/checkbox_unchecked_disabled.svg); }}

QMenu {{ background: {y['grey6']}; border: 1px solid {y['grey4']}; padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 12px; border-radius: 3px; }}
QMenu::item:selected {{ background: {y['blue3']}; }}
QMenu::item:disabled {{ color: {y['white5']}; }}
QMenu::separator {{ height: 1px; background: {y['grey4']}; margin: 4px 6px; }}

QScrollBar:vertical {{ background: {y['grey6']}; width: 12px; margin: 0; border-radius: 6px; }}
QScrollBar::handle:vertical {{ background: {y['grey4']}; min-height: 24px; border-radius: 5px; margin: 1px; }}
QScrollBar::handle:vertical:hover {{ background: {y['grey3']}; }}
QScrollBar:horizontal {{ background: {y['grey6']}; height: 12px; border-radius: 6px; }}
QScrollBar::handle:horizontal {{ background: {y['grey4']}; min-width: 24px; border-radius: 5px; margin: 1px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QGroupBox {{ border: 1px solid {y['grey5']}; border-radius: 6px; margin-top: 14px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {y['white5']}; }}

QSlider {{ min-height: 22px; }}
QSlider::groove:horizontal {{ height: 6px; background: {y['grey4']}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {y['blue2']}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {y['white1']}; border: 3px solid {y['blue2']}; width: 12px; height: 12px;
                             margin: -6px 0; border-radius: 9px; }}
QSlider::handle:horizontal:hover {{ border-color: {y['blue1']}; }}
QSlider::sub-page:horizontal:disabled {{ background: {y['grey2']}; }}
QSlider::handle:horizontal:disabled {{ background: {y['grey1']}; border-color: {y['grey2']}; }}
QLabel#sliderValue {{ font-weight: 700; min-width: 74px; }}

QProgressBar {{ background: {y['grey4']}; border: none; border-radius: 4px; text-align: center; min-height: 16px; }}
QTextBrowser, QTextEdit, QPlainTextEdit, QListWidget {{
    background: {y['grey8']}; border: 1px solid {y['grey5']}; border-radius: 4px; padding: 6px;
    selection-background-color: {y['blue3']};
}}
QListWidget::item {{ padding: 4px 6px; border-radius: 3px; }}
QListWidget::item:selected {{ background: {y['blue3']}; }}
QProgressBar::chunk {{ background: {y['blue3']}; border-radius: 4px; }}

QFrame#instanceCard {{ background: {y['grey6']}; border: 1px solid {y['grey5']}; border-radius: 6px; }}
QFrame#cardHeader {{ background: transparent; }}
QLabel#cardName {{ font-size: 11pt; font-weight: 600; }}
QLabel#chip {{ border-radius: 9px; padding: 1px 9px; font-size: 8pt; font-weight: 600; background: {y['grey4']}; color: {y['white4']}; }}
QLabel#chip[state="rec"] {{ background: {y['red3']}; color: white; }}
QLabel#chip[state="pause"] {{ background: {y['yellow3']}; color: white; }}
QLabel#chip[state="live"] {{ background: {y['red4']}; color: white; }}
{live_chips}
QLabel#chip[state="ready"] {{ background: {y['green3']}; color: white; }}
QLabel#chip[state="busy"] {{ background: {y['blue4']}; color: white; }}
QLabel#warning {{ color: {y['yellow2']}; font-size: 9pt; }}
QLabel#warning[critical="true"] {{ color: white; background: {y['red4']}; border-radius: 4px; padding: 4px 7px;
                                  font-weight: 600; }}
QFrame#alertBanner {{ background: {y['red4']}; border-bottom: 2px solid {y['red2']}; }}
QLabel#alertText {{ color: white; font-size: 10pt; font-weight: 600; }}
QPushButton#alertHide {{ background: rgba(0, 0, 0, 0.25); border: 1px solid rgba(255, 255, 255, 0.35); color: white;
                        padding: 4px 12px; }}
QPushButton#alertHide:hover {{ background: rgba(0, 0, 0, 0.4); }}
QLabel#stats {{ color: {y['white5']}; font-size: 8.5pt; }}

QTabWidget::pane {{ border: 1px solid {y['grey5']}; border-radius: 6px; top: -1px; background: {y['grey7']}; }}
QTabBar::tab {{ background: {y['grey6']}; color: {y['white4']}; border: 1px solid {y['grey5']}; padding: 6px 14px;
               margin-right: 3px; border-top-left-radius: 4px; border-top-right-radius: 4px; }}
QTabBar::tab:selected {{ background: {y['blue3']}; border-color: {y['blue2']}; color: {y['white1']}; }}
QTabBar::tab:hover:!selected {{ background: {y['grey4']}; color: {y['white1']}; }}
QFrame#instanceCard QPushButton {{ padding: 5px 8px; }}
QToolButton#chatButton {{ padding: 5px 8px; padding-right: 24px; }}
QToolButton#chatButton::menu-button {{ border: none; border-left: 1px solid {y['grey3']}; width: 18px;
    border-top-right-radius: 4px; border-bottom-right-radius: 4px; }}
QToolButton#chatButton::menu-button:hover {{ background: {y['grey2']}; }}
QToolButton#chatButton::menu-arrow {{ image: url({ic}/down.svg); width: 9px; height: 9px; }}
QToolButton#flatIcon {{ background: transparent; border: none; padding: 3px; }}
QToolButton#flatIcon:hover {{ background: rgba(255, 255, 255, 0.08); border-radius: 4px; }}
QToolButton#linkToggle {{ background: transparent; border: none; padding: 3px 4px; color: {y['white5']}; font-weight: 600; }}
QToolButton#linkToggle:checked {{ background: transparent; color: {y['white1']}; }}
QToolButton#linkToggle:hover {{ color: {y['white1']}; background: rgba(255, 255, 255, 0.06); border-radius: 4px; }}
QPushButton#vcamToggle {{ padding: 2px 8px; min-height: 16px; font-size: 9pt; }}
QComboBox#typeCombo {{ padding: 2px 6px; min-height: 16px; font-size: 9pt; }}
QFrame#namingBar {{ background: {y['grey7']}; border-bottom: 1px solid {y['grey8']}; }}
QCheckBox#namingCheck {{ color: {y['white5']}; spacing: 5px; }}
QCheckBox#namingCheck:hover {{ color: {y['white1']}; }}
QLabel#namingPreview {{ color: {y['grey1']}; font-size: 8.5pt; }}
QFrame#emptyState {{ background: {y['grey6']}; border: 1px dashed {y['grey3']}; border-radius: 10px; }}
QLabel#allMinimized {{ color: {y['white5']}; padding: 40px; }}
QFrame#miniBar {{ background: {y['grey7']}; border-top: 1px solid {y['grey8']}; }}
QLabel#miniLabel {{ color: {y['grey1']}; font-size: 8.5pt; }}
QLabel#miniName {{ font-weight: 600; }}
QLabel#miniTag {{ color: {y['white4']}; font-size: 8pt; border: 1px solid {y['grey3']}; border-radius: 4px; padding: 0 4px; }}
QLabel#miniAlert {{ color: {y['yellow2']}; font-weight: 700; }}
QLabel#miniAlert[critical="true"] {{ color: {y['red1']}; }}
QToolButton#miniRestore {{ background: transparent; border: none; border-radius: 4px; padding: 3px 7px;
                          color: {y['white4']}; font-weight: 600; }}
QToolButton#miniRestore:hover {{ background: rgba(255, 255, 255, 0.08); color: {y['white1']}; }}
QPushButton#swatch {{ border-radius: 13px; min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; padding: 0; }}
"""
