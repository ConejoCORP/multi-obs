"""Images fixes du plugin Stream Deck (liste des actions, touches par défaut, icône du plugin).

    python streamdeck/make_images.py

Les touches sont dessinées par bin/keys.js (comme dans le plugin), via Node ; l'icône du plugin
(PNG imposé) est rendue avec QtSvg. À relancer après avoir modifié keys.js.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent / "com.stundzow.multiobs.sdPlugin"
IMGS = PLUGIN / "imgs"

NODE_SCRIPT = r"""
const keys = require(process.env.KEYS_JS);
// Bandeau neutre : la vraie touche prend la couleur de l'OBS choisi dès que le plugin tourne.
const band = { name: "Multi OBS", shades: ["#6b7080", "#5b6273", "#4e5566", "#3c404d", "#2d3039", "#22242b"] };
const base = { band, phase: "ready", rec: "idle", recMs: 0 };
const views = [{ glyph: "pause", tone: "idle", label: "PAUSE" }, { glyph: "rec", tone: "idle", label: "REC" }];
const out = { keys: {}, actions: {} };
for (const kind of ["record", "pause", "power", "custom", "vcam", "show", "quit"]) {
  out.keys[kind] = keys.render({ ...base, kind, views: views.slice(0, 1), vcam: false });   // une action par défaut
}
const icon = (name, color, accent) =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 48 48">${keys.glyph(name, 24, 24, 44, color, accent)}</svg>`;
out.actions = {
  record: icon("rec", "#ffffff", "#e5243b"),
  pause: icon("pause", "#f5a30a"),
  power: icon("power", "#35d36a"),
  custom: icon("tap", "#b497ff"),
  vcam: icon("camera", "#16b1f3"),
  show: icon("eye", "#ffffff"),
  quit: icon("exit", "#ff7a85"),
};
process.stdout.write(JSON.stringify(out));
"""

RINGS = [(0.5, 0.30, "#E85E75"), (0.30, 0.64, "#3DBEF5"), (0.70, 0.64, "#59D966")]


def rings_svg(size: int, background: bool, color: str | None = None) -> str:
    r, w = size * 0.22, max(1.5, size * 0.075)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">']
    if background:
        parts.append(f'<circle cx="{size / 2}" cy="{size / 2}" r="{size / 2}" fill="#1D1F26"/>')
    for cx, cy, c in RINGS:
        parts.append(f'<circle cx="{cx * size:.2f}" cy="{cy * size:.2f}" r="{r:.2f}" fill="none" '
                     f'stroke="{color or c}" stroke-width="{w:.2f}"/>')
    parts.append("</svg>")
    return "".join(parts)


def render_png(svg: str, size: int, path: Path) -> None:
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(p, QRectF(0, 0, size, size))
    p.end()
    img.save(str(path))


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841 — QtSvg en a besoin

    env = dict(os.environ, KEYS_JS=str(PLUGIN / "bin" / "keys.js"))
    data = json.loads(subprocess.run(["node", "-e", NODE_SCRIPT], env=env, check=True,
                                     capture_output=True, text=True, encoding="utf-8").stdout)
    for folder, items in (("keys", data["keys"]), ("actions", data["actions"])):
        (IMGS / folder).mkdir(parents=True, exist_ok=True)
        for name, svg in items.items():
            (IMGS / folder / f"{name}.svg").write_text(svg, "utf-8")
    (IMGS / "category.svg").write_text(rings_svg(56, background=False, color="#FFFFFF"), "utf-8")
    render_png(rings_svg(512, background=True), 256, IMGS / "plugin.png")
    render_png(rings_svg(512, background=True), 512, IMGS / "plugin@2x.png")
    print("Images écrites dans", IMGS)


if __name__ == "__main__":
    main()
