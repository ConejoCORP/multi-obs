"""Génère les images « caméra virtuelle inactive » aux couleurs des instances.

    python tools/make_placeholders.py

Part de l'image d'OBS (placeholder.png) : les aplats bleus prennent la teinte de chaque
couleur d'instance (même luminosité), l'icône de caméra barrée reste neutre, et le logo
d'OBS est remplacé par celui de Multi OBS (même place, même taille, cerclé de clair).
Résultat : multiobs/assets/placeholders/<couleur>.png (embarqué dans l'appli).
"""
import colorsys
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402

from multiobs import obsfiles, paths, theme  # noqa: E402
from multiobs.__main__ import paint_logo  # noqa: E402

OUT = ROOT / "multiobs" / "assets" / "placeholders"
# Logo d'OBS dans placeholder.png (1920 x 1080) : centre et rayon de son anneau blanc, épaisseur de l'anneau.
LOGO_CX, LOGO_CY, LOGO_R, LOGO_RING = 959, 378, 243, 13


def source() -> Path:
    for base in (paths.RUNTIME, paths.find_obs_install()):
        if base:
            p = obsfiles.module_dir(Path(base)) / "placeholder.png"
            if p.exists():
                return p
    raise SystemExit("placeholder.png d'OBS introuvable")


def recolor(rgb: np.ndarray, hex_color: str) -> np.ndarray:
    """Remplace la teinte des pixels colorés (HSV), garde saturation et luminosité."""
    target_h = colorsys.rgb_to_hsv(*(int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)))[0]
    f = rgb.astype(np.float32) / 255
    mx, mn = f.max(axis=2), f.min(axis=2)
    v = mx
    s = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    # Teinte cible pour les pixels saturés ; le logo et l'icône (gris) ne bougent pas.
    weight = np.clip((s - 0.12) / 0.15, 0, 1)
    h6 = target_h * 6
    i = np.floor(h6)
    frac = h6 - i
    p, q, t = v * (1 - s), v * (1 - s * frac), v * (1 - s * (1 - frac))
    i = int(i) % 6
    channels = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]
    new = np.stack(channels, axis=2)
    out = f * (1 - weight[..., None]) + new * weight[..., None]
    return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)


def logo() -> Image.Image:
    """Logo de Multi OBS (celui de l'appli) cerclé de clair comme le logo d'OBS de l'image d'origine."""
    size = 2 * LOGO_R
    img = QImage(size, size, QImage.Format.Format_RGBA8888)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#f2f3f5"))
    p.drawEllipse(QRectF(0, 0, size, size))
    paint_logo(p, LOGO_RING, LOGO_RING, size - 2 * LOGO_RING)
    p.end()
    data = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes()).reshape(size, img.bytesPerLine() // 4, 4)
    return Image.fromarray(data[:, :size].copy(), "RGBA")


def main() -> None:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    rgb = np.array(Image.open(source()).convert("RGB"))
    mark = logo()
    for key in theme.ACCENTS:
        img = Image.fromarray(recolor(rgb, theme.accent(key, 2))).convert("RGBA")
        img.alpha_composite(mark, (LOGO_CX - LOGO_R, LOGO_CY - LOGO_R))   # couvre entièrement le logo d'OBS
        img.convert("RGB").save(OUT / f"{key}.png", optimize=True)
        print("ok", key)


if __name__ == "__main__":
    main()
