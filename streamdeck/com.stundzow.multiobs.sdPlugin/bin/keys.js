"use strict";
// Dessin des touches en SVG (144 × 144). En haut, le bandeau de l'OBS piloté : sa couleur et son nom
// (ou « TOUS » sur les couleurs des instances concernées). En dessous, l'état, toujours dans les mêmes
// couleurs : rouge = enregistre, orange = en pause, vert = allumé / actif, gris = éteint.
// Chargé par le plugin (Node) et par la fenêtre de réglages (navigateur, pour les pictos).
(function (root) {
  const C = {
    off: "#1c1d22",
    offInk: "#5b606d",
    idle: "#2a2c34",
    ink: "#ffffff",
    soft: "#c9ccd6",
    muted: "#8a8f9c",
    rec: "#e5243b",
    pause: "#f5a30a",
    pauseInk: "#2a1c00",
    on: "#1d9f48",
    vcam: "#0a8dd0",
    blue: "#476bd7",
    alert: "#ffcc33",
    critical: "#ff2a2a",
    message: "#3a1118",
    grey: ["#6b7080", "#5b6273", "#4e5566", "#3c404d", "#2d3039", "#22242b"],
  };
  // Couleurs d'une action selon son état (touche « Action OBS »).
  const TONES = {
    idle: { bg: C.idle, ink: C.ink, accent: C.rec },
    on: { bg: C.on, ink: C.ink, accent: C.ink },
    rec: { bg: C.rec, ink: C.ink, accent: C.ink },
    pause: { bg: C.pause, ink: C.pauseInk, accent: C.pauseInk },
    vcam: { bg: C.vcam, ink: C.ink, accent: C.ink },
    dim: { bg: C.idle, ink: C.muted, accent: C.muted },
    off: { bg: C.off, ink: C.offInk, accent: "#5c2a33" },
  };
  const FONT = 'font-family="Segoe UI" font-weight="bold"';
  const BAND = 40;

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  }

  // Largeur approximative d'un texte en gras (Segoe UI), pour choisir une taille qui tient.
  function textWidth(s, size) {
    let w = 0;
    for (const ch of String(s)) {
      if ("il.,:;!|'I".includes(ch)) w += 0.3;
      else if ("mwMW".includes(ch)) w += 0.92;
      else if (ch === " ") w += 0.27;
      else if (/\p{Extended_Pictographic}/u.test(ch)) w += 1.2;   // emoji (affiché en couleur)
      else if (/[‍️]/.test(ch)) continue;
      else if (/[0-9]/.test(ch)) w += 0.57;
      else if (ch === ch.toUpperCase() && ch !== ch.toLowerCase()) w += 0.68;
      else w += 0.56;
    }
    return w * size;
  }

  function fit(s, max, width, min = 13) {
    let size = max;
    while (size > min && textWidth(s, size) > width) size -= 1;
    let txt = String(s);
    while (txt.length > 2 && textWidth(txt, size) > width) txt = Array.from(txt).slice(0, -2).join("") + "…";
    return { txt, size };
  }

  function text(s, x, y, size, color, extra = "") {
    return `<text x="${x}" y="${y}" font-size="${size}" ${FONT} fill="${color}" text-anchor="middle"${extra}>${esc(s)}</text>`;
  }

  // Coupe un texte en deux lignes de largeurs proches.
  function halves(words) {
    let best = 1;
    let bestDiff = Infinity;
    for (let i = 1; i < words.length; i++) {
      const diff = Math.abs(textWidth(words.slice(0, i).join(" "), 10) - textWidth(words.slice(i).join(" "), 10));
      if (diff < bestDiff) [best, bestDiff] = [i, diff];
    }
    return [words.slice(0, best).join(" "), words.slice(best).join(" ")];
  }

  function twoLines(a0, b0, max, width) {
    const a = fit(a0, max, width);
    const b = fit(b0, max, width);
    const size = Math.min(a.size, b.size);
    return { a: fit(a.txt, size, width).txt, b: fit(b.txt, size, width).txt, size };
  }

  // Un nom sur une ligne s'il reste lisible, sinon sur deux (« Caméra principale du live »).
  function nameLines(name, cx, y1, y2, single, max2, width, color) {
    const one = fit(name, single, width);
    const words = String(name).split(" ");
    if (one.size >= Math.min(single, 18) || words.length < 2) return text(one.txt, cx, y2, one.size, color);
    const t = twoLines(...halves(words), max2, width);
    return text(t.a, cx, y1, t.size, color) + text(t.b, cx, y2, t.size, color);
  }

  // Texte seul sur la touche (sans picto) : le plus gros possible, sur une ou deux lignes, centré sur cy.
  function bigLines(s, cx, cy, width, single, max2, color) {
    const one = fit(s, single, width);
    const words = String(s).split(" ");
    if (one.size >= Math.min(single, 22) || words.length < 2) return text(one.txt, cx, cy + one.size * 0.35, one.size, color);
    const t = twoLines(...halves(words), max2, width);
    const gap = t.size * 1.12;
    const y1 = cy - gap / 2 + t.size * 0.35;
    return text(t.a, cx, y1, t.size, color) + text(t.b, cx, y1 + gap, t.size, color);
  }

  function formatTime(ms, hours) {
    const t = Math.max(0, Math.floor((ms || 0) / 1000));
    const h = Math.floor(t / 3600);
    const m = Math.floor(t / 60) % 60;
    const s = t % 60;
    const p = (n) => String(n).padStart(2, "0");
    if (hours) return `${p(h)}:${p(m)}:${p(s)}`;
    return h ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
  }

  // ---------------------------------------------------------------- pictos (boîte de 48)
  function glyph(name, cx, cy, size, color, accent) {
    const k = size / 48;
    const t = `transform="translate(${(cx - 24 * k).toFixed(2)} ${(cy - 24 * k).toFixed(2)}) scale(${k.toFixed(4)})"`;
    const line = `fill="none" stroke="${color}" stroke-linecap="round" stroke-linejoin="round"`;
    const eye = `<path d="M3 24 C10 12.5 16.5 9 24 9 C31.5 9 38 12.5 45 24 C38 35.5 31.5 39 24 39 C16.5 39 10 35.5 3 24 Z" ${line} stroke-width="4"/>`;
    const speaker = `<path d="M5 18 H13 L24 9 V39 L13 30 H5 Z" fill="${color}" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>`;
    let g = "";
    switch (name) {
      case "rec":
        g = `<circle cx="24" cy="24" r="20" ${line} stroke-width="4.5"/><circle cx="24" cy="24" r="11.5" fill="${accent || C.rec}"/>`;
        break;
      case "stop":
        g = `<rect x="10" y="10" width="28" height="28" rx="5" fill="${color}"/>`;
        break;
      case "pause":
        g = `<rect x="10" y="8" width="10" height="32" rx="3" fill="${color}"/><rect x="28" y="8" width="10" height="32" rx="3" fill="${color}"/>`;
        break;
      case "play":
        g = `<path d="M15 9 L39 24 L15 39 Z" fill="${color}" stroke="${color}" stroke-width="4" stroke-linejoin="round"/>`;
        break;
      case "power":
        g = `<path d="M24 5 V23" ${line} stroke-width="5"/><path d="M33.2 12.9 A16 16 0 1 1 14.8 12.9" ${line} stroke-width="5"/>`;
        break;
      case "camera":
        g = `<rect x="3" y="12" width="30" height="24" rx="6" fill="${color}"/><path d="M35 21 L45 14.5 V33.5 L35 27 Z" fill="${color}" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>`;
        break;
      case "eye":
        g = `${eye}<circle cx="24" cy="24" r="7.5" fill="${color}"/>`;
        break;
      case "eyeoff":
        g = `${eye}<circle cx="24" cy="24" r="7.5" fill="${color}"/><path d="M7 41 L41 7" ${line} stroke-width="4.5"/>`;
        break;
      case "scene":
        g = `<rect x="4" y="7" width="40" height="28" rx="5" ${line} stroke-width="4"/><path d="M20 14.5 L30 21 L20 27.5 Z" fill="${color}" stroke="${color}" stroke-width="2.5" stroke-linejoin="round"/><path d="M15 42 H33" ${line} stroke-width="4"/>`;
        break;
      case "filter":
        g = `<path d="M5 8 H43 L28 25 V39 L20 43 V25 Z" ${line} stroke-width="4"/>`;
        break;
      case "speaker":
        g = `${speaker}<path d="M30.5 17.5 A9 9 0 0 1 30.5 30.5 M36 12 A16.5 16.5 0 0 1 36 36" ${line} stroke-width="4"/>`;
        break;
      case "speakeroff":
        g = `${speaker}<path d="M31 18 L43 30 M43 18 L31 30" ${line} stroke-width="4.5"/>`;
        break;
      case "live":
        g = `<circle cx="24" cy="24" r="5.5" fill="${color}"/><path d="M15.5 15.5 A12 12 0 0 0 15.5 32.5 M32.5 15.5 A12 12 0 0 1 32.5 32.5 M9 9 A21 21 0 0 0 9 39 M39 9 A21 21 0 0 1 39 39" ${line} stroke-width="4"/>`;
        break;
      case "photo":
        g = `<path d="M5 15 H14 L18 9 H30 L34 15 H43 V39 H5 Z" ${line} stroke-width="4"/><circle cx="24" cy="27" r="7" ${line} stroke-width="4"/>`;
        break;
      case "tap":
        g = `<rect x="4" y="4" width="40" height="40" rx="9" ${line} stroke-width="4"/><circle cx="16" cy="24" r="5" fill="${color}"/><rect x="25" y="20" width="14" height="8" rx="4" fill="${color}"/>`;
        break;
      // Pictos au choix pour la touche « Action OBS » (scènes surtout).
      case "monitor":
        g = `<rect x="4" y="7" width="40" height="28" rx="5" ${line} stroke-width="4"/><path d="M24 35 V42 M15 42 H33" ${line} stroke-width="4"/>`;
        break;
      case "webcam":
        g = `<circle cx="24" cy="19" r="14" ${line} stroke-width="4"/><circle cx="24" cy="19" r="5.5" fill="${color}"/><path d="M18 33 L14 43 H34 L30 33" ${line} stroke-width="4"/>`;
        break;
      case "person":
        g = `<circle cx="24" cy="14.5" r="9" fill="${color}"/><path d="M7 43 C7 32.5 14 27 24 27 C34 27 41 32.5 41 43 Z" fill="${color}"/>`;
        break;
      case "duo":
        g = `<circle cx="33" cy="12.5" r="6" ${line} stroke-width="3.5"/><path d="M35.5 24.5 C41.5 25.5 45 30 45 38" ${line} stroke-width="3.5"/>`
          + `<circle cx="18" cy="16" r="7.5" fill="${color}"/><path d="M4 43 C4 33.5 10 28.5 18 28.5 C26 28.5 32 33.5 32 43 Z" fill="${color}"/>`;
        break;
      case "gamepad":
        g = `<path d="M15 12 H33 C40 12 44.5 18 45.5 26.5 C46.5 35 43.5 39 39.5 39 C35.5 39 34 35.5 31 32 H17 C14 35.5 12.5 39 8.5 39 C4.5 39 1.5 35 2.5 26.5 C3.5 18 8 12 15 12 Z" ${line} stroke-width="4"/>`
          + `<path d="M14 18.5 V28.5 M9 23.5 H19" ${line} stroke-width="3.6"/><circle cx="32" cy="20.5" r="2.7" fill="${color}"/><circle cx="37" cy="25.5" r="2.7" fill="${color}"/>`;
        break;
      case "chat":
        g = `<path d="M9 7 H39 C42 7 44 9 44 12 V29 C44 32 42 34 39 34 H23 L13 42 V34 H9 C6 34 4 32 4 29 V12 C4 9 6 7 9 7 Z" ${line} stroke-width="4"/>`
          + `<circle cx="15" cy="20.5" r="2.8" fill="${color}"/><circle cx="24" cy="20.5" r="2.8" fill="${color}"/><circle cx="33" cy="20.5" r="2.8" fill="${color}"/>`;
        break;
      case "mic":
        g = `<rect x="16" y="3" width="16" height="25" rx="8" fill="${color}"/><path d="M9 21 C9 30 15.5 35 24 35 C32.5 35 39 30 39 21 M24 35 V43 M16 43 H32" ${line} stroke-width="4"/>`;
        break;
      case "micoff":
        g = `<rect x="16" y="3" width="16" height="25" rx="8" ${line} stroke-width="4"/><path d="M9 21 C9 30 15.5 35 24 35 C32.5 35 39 30 39 21 M24 35 V43 M16 43 H32" ${line} stroke-width="4"/><path d="M6 42 L42 6" ${line} stroke-width="4.5"/>`;
        break;
      case "headset":
        g = `<path d="M7 30 V24 C7 14 14.5 7 24 7 C33.5 7 41 14 41 24 V30" ${line} stroke-width="4"/><rect x="4.5" y="26" width="11" height="16" rx="4.5" fill="${color}"/><rect x="32.5" y="26" width="11" height="16" rx="4.5" fill="${color}"/>`;
        break;
      case "music":
        g = `<path d="M17 37 V11 L41 5 V31" ${line} stroke-width="4"/><path d="M17 10.5 L41 4.5 V12 L17 18 Z" fill="${color}"/><ellipse cx="11.5" cy="37" rx="6.5" ry="5.5" fill="${color}"/><ellipse cx="35.5" cy="31" rx="6.5" ry="5.5" fill="${color}"/>`;
        break;
      case "picture":
        g = `<rect x="4" y="7" width="40" height="34" rx="5" ${line} stroke-width="4"/><circle cx="16" cy="17.5" r="4" fill="${color}"/><path d="M7 36 L18 26 L26 32 L33 24 L41 32" ${line} stroke-width="4"/>`;
        break;
      case "film":
        g = `<rect x="6" y="4" width="36" height="40" rx="4" ${line} stroke-width="4"/><path d="M15 4 V44 M33 4 V44" ${line} stroke-width="3.5"/>`
          + [12, 24, 36].map((y) => `<circle cx="10.5" cy="${y}" r="1.9" fill="${color}"/><circle cx="37.5" cy="${y}" r="1.9" fill="${color}"/>`).join("");
        break;
      case "pencil":
        g = `<path d="M9 39 L11 30 L31.5 9.5 C33.5 7.5 36.5 7.5 38.5 9.5 C40.5 11.5 40.5 14.5 38.5 16.5 L18 37 Z" ${line} stroke-width="4"/><path d="M27.5 13.5 L34.5 20.5" ${line} stroke-width="4"/>`;
        break;
      case "code":
        g = `<path d="M15 13 L4 24 L15 35 M33 13 L44 24 L33 35 M27.5 8 L20.5 40" ${line} stroke-width="4.5"/>`;
        break;
      case "coffee":
        g = `<path d="M6 19 H34 V29 C34 36.5 29 41 22.5 41 H17.5 C11 41 6 36.5 6 29 Z" ${line} stroke-width="4"/><path d="M34 23 H37.5 C41 23 43 25.5 43 28.5 C43 31.5 41 34 37.5 34 H34" ${line} stroke-width="4"/>`
          + `<path d="M14 5 C12 8 16 10 14 13 M22.5 5 C20.5 8 24.5 10 22.5 13" ${line} stroke-width="3.5"/>`;
        break;
      case "clock":
        g = `<circle cx="24" cy="24" r="19" ${line} stroke-width="4"/><path d="M24 13 V24 L31.5 29" ${line} stroke-width="4"/>`;
        break;
      case "moon":
        g = `<path d="M24.27 5.14 A19 19 0 1 0 39.95 30.22 A15 15 0 1 1 24.27 5.14 Z" fill="${color}" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>`;
        break;
      case "home":
        g = `<path d="M5 22 L24 6 L43 22" ${line} stroke-width="4"/><path d="M10 19 V42 H20 V31 H28 V42 H38 V19" ${line} stroke-width="4"/>`;
        break;
      case "phone":
        g = `<rect x="13" y="3" width="22" height="42" rx="5" ${line} stroke-width="4"/><path d="M21 37.5 H27" ${line} stroke-width="4"/>`;
        break;
      case "star":
        g = `<path d="M24 5 L29 18.1 L43 18.8 L32.1 27.6 L35.8 41.2 L24 33.5 L12.2 41.2 L15.9 27.6 L5 18.8 L19 18.1 Z" fill="${color}" stroke="${color}" stroke-width="2.5" stroke-linejoin="round"/>`;
        break;
      case "heart":
        g = `<path d="M24 42 C18 37 4 29 4 17.5 C4 11 9 6 15 6 C19 6 22.5 8.5 24 12 C25.5 8.5 29 6 33 6 C39 6 44 11 44 17.5 C44 29 30 37 24 42 Z" fill="${color}"/>`;
        break;
      case "fire":
        g = `<path d="M24 44 C14.5 44 9 37.5 9 30 C9 22 15 17.5 17 10 C20.5 13 21.5 16.5 21.5 20 C24.5 16.5 26 10 25 4 C34 9 39 19 39 29 C39 37.5 33.5 44 24 44 Z" fill="${color}"/>`;
        break;
      case "bolt":
        g = `<path d="M28 3 L9 27 H22 L19 45 L39 20 H26 Z" fill="${color}" stroke="${color}" stroke-width="2.5" stroke-linejoin="round"/>`;
        break;
      case "sparkle":
        g = `<path d="M24 3 C25.5 15 33 22.5 45 24 C33 25.5 25.5 33 24 45 C22.5 33 15 25.5 3 24 C15 22.5 22.5 15 24 3 Z" fill="${color}"/>`;
        break;
      case "bulb":
        g = `<path d="M17 32 C17 27 11 24.5 11 17 C11 9.5 17 4 24 4 C31 4 37 9.5 37 17 C37 24.5 31 27 31 32 Z" ${line} stroke-width="4"/><path d="M18 38 H30 M20.5 43.5 H27.5" ${line} stroke-width="4"/>`;
        break;
      case "bell":
        g = `<path d="M12 33 V22 C12 14.5 17 9 24 9 C31 9 36 14.5 36 22 V33 L40.5 37.5 H7.5 Z" fill="${color}" stroke="${color}" stroke-width="2" stroke-linejoin="round"/><path d="M24 4 V9 M19.5 42 C20.5 44 22 45 24 45 C26 45 27.5 44 28.5 42" ${line} stroke-width="4"/>`;
        break;
      case "trophy":
        g = `<path d="M14 6 H34 V18 C34 25 30 29 24 29 C18 29 14 25 14 18 Z" fill="${color}"/><path d="M14 10 H8 C8 17 10 20.5 14 21.5 M34 10 H40 C40 17 38 20.5 34 21.5 M24 29 V37" ${line} stroke-width="3.5"/><rect x="15" y="36" width="18" height="7" rx="2.5" fill="${color}"/>`;
        break;
      case "flag":
        g = `<path d="M10 44 V5" ${line} stroke-width="4"/><path d="M10 7 C16 4 21 4 26 7 C31 10 36 10 41 8 V26 C36 28 31 28 26 25 C21 22 16 22 10 25 Z" fill="${color}"/>`;
        break;
      case "check":
        g = `<circle cx="24" cy="24" r="19" ${line} stroke-width="4"/><path d="M15 24.5 L21.5 31 L33 18" ${line} stroke-width="4.5"/>`;
        break;
      case "exit":
        g = `<path d="M21 6 H10 C7.5 6 6 7.5 6 10 V38 C6 40.5 7.5 42 10 42 H21" ${line} stroke-width="4"/><path d="M18 24 H42 M32 14 L42 24 L32 34" ${line} stroke-width="4.5"/>`;
        break;
      case "arrow":
        g = `<path d="M6 24 H40 M28 12 L40 24 L28 36" ${line} stroke-width="4.5"/>`;
        break;
      case "none":
        g = `<path d="M14 24 H34" ${line} stroke-width="5"/>`;
        break;
      default:
        return "";
    }
    return `<g ${t}>${g}</g>`;
  }

  // ---------------------------------------------------------------- bandeau
  function band(m) {
    const b = m.band || {};
    const out = [];
    const side = m.alert || m.recDot ? 24 : 8;   // place des pastilles à gauche (alerte) et à droite (rec)
    if (b.all) {
      // Une bande par instance concernée par la touche ; atténuée si cette instance est éteinte.
      const cols = (b.colors && b.colors.length ? b.colors : [{ color: C.grey[1] }])
        .map((c) => (typeof c === "string" ? { color: c } : c));
      const w = 144 / cols.length;
      cols.forEach((c, i) => {
        const x = (i * w).toFixed(2);
        out.push(`<rect x="${x}" y="0" width="${(w + 0.5).toFixed(2)}" height="${BAND}" fill="${c.color}"/>`);
        if (c.dim || b.dim) out.push(`<rect x="${x}" y="0" width="${(w + 0.5).toFixed(2)}" height="${BAND}" fill="#000000" fill-opacity="0.58"/>`);
      });
      out.push(`<rect x="38" y="7" width="68" height="26" rx="13" fill="#000000" fill-opacity="0.5"/>`);
      out.push(text("TOUS", 72, 27.5, 19, C.ink));
    } else {
      const s = b.shades && b.shades.length === 6 ? b.shades : C.grey;
      const top = b.dim ? s[4] : s[1];
      const bottom = b.dim ? s[5] : s[2];
      out.push(`<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${top}"/><stop offset="1" stop-color="${bottom}"/></linearGradient></defs>`);
      out.push(`<rect x="0" y="0" width="144" height="${BAND}" fill="url(#bg)"/>`);
      const width = 144 - 2 * side;
      const one = fit(b.name || "?", 27, width);
      out.push(one.size >= 18 ? text(one.txt, 72, 20 + one.size * 0.36, one.size, b.dim ? "#d6d8de" : C.ink)
        : nameLines(b.name || "?", 72, 17, 34, 27, 17, width, b.dim ? "#d6d8de" : C.ink));
    }
    out.push(`<rect x="0" y="${BAND}" width="144" height="2" fill="#000000" fill-opacity="0.4"/>`);
    if (m.alert || m.critical) {
      out.push(`<path d="M13 8 L25 31 H1 Z" fill="${m.critical ? C.critical : C.alert}" stroke="#1a1a1a" stroke-width="1.5" stroke-linejoin="round"/>`);
      out.push(text("!", 13, 29, 16, m.critical ? "#ffffff" : "#1a1a1a"));
    }
    if (m.recDot) {
      // Pastille REC (blanc + rouge : visible même sur un bandeau rouge), elle clignote.
      out.push(`<circle cx="131" cy="20" r="9" fill="#ffffff"/>`);
      if (m.blink !== false) out.push(`<circle cx="131" cy="20" r="5.5" fill="${C.rec}"/>`);
    }
    return out.join("");
  }

  // ---------------------------------------------------------------- corps
  function body(color) {
    return `<rect x="0" y="${BAND + 2}" width="144" height="${142 - BAND}" fill="${color}"/>`;
  }

  function iconLabel(bg, name, ink, label, labelColor, accent) {
    const f = fit(label, 23, 128);
    return body(bg) + glyph(name, 72, 81, 46, ink, accent) + text(f.txt, 72, 133, f.size, labelColor || ink);
  }

  function lines(bg, l1, l2, color) {
    const a = fit(l1, 22, 134);
    if (!l2) return body(bg) + text(a.txt, 72, 101, a.size, color);
    const b = fit(l2, 22, 134);
    return body(bg) + text(a.txt, 72, 87, a.size, color) + text(b.txt, 72, 117, b.size, color);
  }

  function timecodeBody(bg, head, digits, ink) {
    const size = digits.length <= 5 ? 46 : digits.length <= 7 ? 37 : 33;
    return body(bg) + head + text(digits, 72, 115 - (46 - size) * 0.25, size, ink);
  }

  function recHead(m, ink) {
    // « ● REC » : le point clignote ; « ❚❚ PAUSE » dessiné en rectangles.
    if (m.rec === "recording") {
      return (m.blink !== false ? `<circle cx="47" cy="63" r="7.5" fill="${ink}"/>` : `<circle cx="47" cy="63" r="6.5" fill="none" stroke="${ink}" stroke-width="2"/>`)
        + text("REC", 80, 70, 20, ink);
    }
    return `<rect x="34" y="55" width="5.5" height="16" rx="1.5" fill="${ink}"/><rect x="43" y="55" width="5.5" height="16" rx="1.5" fill="${ink}"/>`
      + text("PAUSE", 85, 70, 20, ink);
  }

  function bar(p, color, track) {
    const w = Math.max(0, Math.min(1, p)) * 144;
    return `<rect x="0" y="136" width="144" height="8" fill="${track || "#ffffff"}" fill-opacity="0.16"/><rect x="0" y="136" width="${w.toFixed(1)}" height="8" fill="${color}"/>`;
  }

  // Appui long : un cercle blanc grandit depuis le centre jusqu'à couvrir toute la touche (sans
  // toucher au bandeau). Sous le cercle, la même touche en négatif : fond blanc, texte et pictos
  // sombres, donc toujours lisibles. Masque SVG : affiché par le Qt du logiciel Stream Deck (6.8).
  function luminance(hex) {
    const n = parseInt(hex.slice(1), 16);
    return (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
  }

  // En négatif, les images et leurs voiles disparaissent : fond blanc, texte sombre, comme les autres touches.
  function inverted(content) {
    return content
      .replace(/<image [^>]*\/>/g, "")
      .replace(/<rect [^>]*class="ov"[^>]*\/>/g, "")
      .replace(/(id="|url\(#)(im\d)/g, "$1$2i")
      .replace(/(fill|stroke)="(#[0-9a-fA-F]{6})"/g, (all, attr, c) => (luminance(c) > 0.5 ? `${attr}="#1c1d22"` : all))
      .replace(/<rect x="0" y="42" width="144" height="102" fill="#[0-9a-fA-F]{6}"/g, '<rect x="0" y="42" width="144" height="102" fill="#ffffff"')
      .replace(/(<rect [^>]*rx="10" )fill="#[0-9a-fA-F]{6}"/g, '$1fill="#ffffff"');
  }

  function holdLayer(content, p) {
    const r = (Math.max(0, Math.min(1, p)) * 89).toFixed(1);
    return `<defs><mask id="hold" maskUnits="userSpaceOnUse" x="0" y="0" width="144" height="144">`
      + `<rect width="144" height="144" fill="#000000"/><circle cx="72" cy="93" r="${r}" fill="#ffffff"/></mask></defs>`
      + `<g mask="url(#hold)">${inverted(content)}</g>`;
  }

  function stateColors(m) {
    if (m.rec === "recording") return { bg: C.rec, ink: C.ink, dot: C.ink };
    if (m.rec === "paused") return { bg: C.pause, ink: C.pauseInk, dot: C.pauseInk };
    return { bg: C.idle, ink: C.ink, dot: C.rec };
  }

  function recordBody(m) {
    if (m.phase === "stopped") return iconLabel(C.off, "rec", C.offInk, "ÉTEINT", C.offInk, "#5c2a33");
    if (m.phase === "starting" || m.phase === "closing")
      return iconLabel(C.idle, "rec", C.muted, m.phase === "starting" ? "DÉMARRE" : "ARRÊT…", C.soft, "#8c3341");
    if (m.rec !== "idle") {
      const sc = stateColors(m);
      return timecodeBody(sc.bg, recHead(m, sc.ink), formatTime(m.recMs, m.hours), sc.ink);
    }
    if (m.remux != null) return iconLabel(C.idle, "rec", C.ink, `MP4 ${m.remux} %`, C.soft) + bar(m.remux / 100, C.blue);
    return iconLabel(C.idle, "rec", C.ink, "REC");
  }

  function pauseBody(m) {
    if (m.phase === "stopped") return iconLabel(C.off, "pause", C.offInk, "ÉTEINT", C.offInk);
    if (m.phase === "starting" || m.phase === "closing") return iconLabel(C.idle, "pause", C.muted, m.phase === "starting" ? "DÉMARRE" : "ARRÊT…", C.soft);
    if (m.rec === "idle") return iconLabel(C.idle, "pause", "#6f7480", "PAS DE REC", C.muted);
    const sc = stateColors(m);
    if (m.blocked && m.rec === "recording") return iconLabel(sc.bg, "pause", "#ffffff", "BLOQUÉE", sc.ink);
    return iconLabel(sc.bg, m.rec === "paused" ? "play" : "pause", sc.ink, formatTime(m.recMs, m.hours), sc.ink);
  }

  function powerBody(m) {
    switch (m.phase) {
      case "stopped": return iconLabel(C.off, "power", C.muted, "ÉTEINT", C.muted);
      case "starting": return iconLabel(C.idle, "power", C.soft, "DÉMARRE", C.soft);
      case "closing": return iconLabel(C.idle, "power", C.soft, "ARRÊT…", C.soft);
      case "partial": return iconLabel(C.idle, "power", C.ink, m.partial || "EN PARTIE");
      default: return iconLabel(C.on, "power", C.ink, "ALLUMÉ");
    }
  }

  function vcamBody(m) {
    if (m.phase === "stopped") return iconLabel(C.off, "camera", C.offInk, "ÉTEINT", C.offInk);
    if (m.phase === "starting" || m.phase === "closing") return iconLabel(C.idle, "camera", C.muted, m.phase === "starting" ? "DÉMARRE" : "ARRÊT…", C.soft);
    return m.vcam ? iconLabel(C.vcam, "camera", C.ink, "VCAM ON") : iconLabel(C.idle, "camera", C.soft, "VCAM OFF", C.muted);
  }

  function showBody(m) {
    if (m.band && m.band.all) return iconLabel(C.idle, "eye", C.ink, "MULTI OBS");
    if (m.phase === "stopped") return iconLabel(C.off, "eye", C.offInk, "ÉTEINT", C.offInk);
    return iconLabel(C.idle, "eye", C.ink, "VOIR OBS");
  }

  // ---------------------------------------------------------------- images choisies par l'utilisateur
  // Le Qt du logiciel Stream Deck ignore preserveAspectRatio : l'image est placée et recadrée ici
  // (« cover » : remplit la zone, masque pour couper ce qui dépasse ; « contain » : entière dans la zone).
  function picture(img, x, y, w, h, mode, id, rx, extra = "", inside = "") {
    const iw = img.w || w;
    const ih = img.h || h;
    const s = mode === "cover" ? Math.max(w / iw, h / ih) : Math.min(w / iw, h / ih);
    const dw = iw * s;
    const dh = ih * s;
    const tag = `<image x="${(x + (w - dw) / 2).toFixed(1)}" y="${(y + (h - dh) / 2).toFixed(1)}" width="${dw.toFixed(1)}" height="${dh.toFixed(1)}"`
      + ` preserveAspectRatio="none"${extra} xlink:href="${img.src}"/>`;
    if (mode !== "cover") return tag;
    // Masque blanc nommé (pas en #hex) : le négatif de l'appui long ne doit pas l'assombrir.
    return `<defs><mask id="${id}" maskUnits="userSpaceOnUse" x="0" y="0" width="144" height="144">`
      + `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx || 0}" fill="white"/></mask></defs><g mask="url(#${id})">${tag}${inside}</g>`;
  }

  const ACTIVE = ["on", "rec", "pause", "vcam"];

  // Image qui remplit la zone : l'état se lit au cadre et au bandeau du texte, aux couleurs habituelles.
  // Les voiles (class="ov") disparaissent dans le négatif de l'appui long.
  function coverView(v, t, box, id, tile) {
    const { x, y, w, h, rx } = box;
    const veils = [];
    if (v.tone === "off" || v.tone === "dim") veils.push(`<rect class="ov" x="${x}" y="${y}" width="${w}" height="${h}" fill="#000000" fill-opacity="${v.tone === "off" ? 0.66 : 0.55}"/>`);
    const strip = tile ? 20 : 28;
    const active = ACTIVE.includes(v.tone);
    if (tile) veils.push(`<rect class="ov" x="${x}" y="${y}" width="${w}" height="20" fill="#000000" fill-opacity="0.5"/>`);
    if (v.label) {
      veils.push(active ? `<rect x="${x}" y="${y + h - strip}" width="${w}" height="${strip}" fill="${t.bg}"/>`
        : `<rect class="ov" x="${x}" y="${y + h - strip}" width="${w}" height="${strip}" fill="#000000" fill-opacity="0.62"/>`);
    }
    const out = [picture(v.image, x, y, w, h, "cover", id, rx, "", veils.join(""))];
    const sw = tile ? 4 : 5;   // cadre d'état, qui mange le bas du bandeau de texte
    if (v.label) {
      const f = tile ? fit(v.label, 13, w - 6, 9) : fit(v.label, 19, w - 12, 11);
      const mid = y + h - (strip + (active ? sw : 0)) / 2;
      out.push(text(f.txt, x + w / 2, mid + f.size * 0.33, f.size, active ? t.ink : v.tone === "off" ? C.soft : C.ink));
    }
    if (active) {
      out.push(`<rect x="${x + sw / 2}" y="${y + sw / 2}" width="${w - sw}" height="${h - sw}" rx="${rx ? rx - sw / 2 : 0}" fill="none" stroke="${t.bg}" stroke-width="${sw}"/>`);
    }
    return out.join("");
  }

  function dimmed(tone) {
    return tone === "off" || tone === "dim" ? ' opacity="0.4"' : "";
  }

  // Fermer Multi OBS : grisée pendant un rec ou un live (fermeture refusée).
  function quitBody(m) {
    return m.busy ? iconLabel(C.idle, "exit", "#6f7480", "FERMER", C.muted) : iconLabel(C.idle, "exit", C.ink, "FERMER");
  }

  // Touche « Action OBS » : une action (toute la touche) ou deux (appui court à gauche, long à droite).
  // Apparence au choix : picto, image, texte, ou seulement l'un d'eux en plus grand.
  function viewBody(v) {
    const t = TONES[v.tone] || TONES.idle;
    const label = v.label || "";
    if (v.image && v.image.fit !== "contain") return body(t.bg) + coverView({ ...v, label }, t, { x: 0, y: BAND + 2, w: 144, h: 142 - BAND }, "im0", false);
    if (v.image) {
      const pic = label ? picture(v.image, 10, 49, 124, 52, "contain", "", 0, dimmed(v.tone)) : picture(v.image, 10, 50, 124, 86, "contain", "", 0, dimmed(v.tone));
      return body(t.bg) + pic + (label ? nameLines(label, 72, 114, 132, 22, 16, 130, t.ink) : "");
    }
    if (v.glyph && label) return body(t.bg) + glyph(v.glyph, 72, 73, 40, t.ink, t.accent) + nameLines(label, 72, 114, 132, 22, 16, 130, t.ink);
    if (v.glyph) return body(t.bg) + glyph(v.glyph, 72, 93, 62, t.ink, t.accent);
    return body(t.bg) + (label ? bigLines(label, 72, 93, 132, 34, 25, t.ink) : "");
  }

  function tilesBody(views) {
    const out = [body(C.off)];
    views.forEach((v, i) => {
      const t = TONES[v.tone] || TONES.idle;
      const x = i ? 75 : 6;
      const cx = x + 31.5;
      const label = v.label || "";
      out.push(`<rect x="${x}" y="47" width="63" height="91" rx="10" fill="${t.bg}"/>`);
      if (v.image && v.image.fit !== "contain") {
        out.push(coverView({ ...v, label }, t, { x, y: 47, w: 63, h: 91, rx: 10 }, `im${i}`, true));
        out.push(text(i ? "LONG" : "COURT", cx, 61, 11, C.ink, ' fill-opacity="0.9"'));
        return;
      }
      out.push(text(i ? "LONG" : "COURT", cx, 61, 11, t.ink, ' fill-opacity="0.75"'));
      if (v.image) out.push(picture(v.image, x + 5, 67, 53, label ? 45 : 64, "contain", "", 0, dimmed(v.tone)));
      else if (v.glyph) out.push(label ? glyph(v.glyph, cx, 88, 30, t.ink, t.accent) : glyph(v.glyph, cx, 99, 42, t.ink, t.accent));
      else if (label) {
        out.push(bigLines(label, cx, 99, 57, 20, 15, t.ink));
        return;
      }
      if (label) {
        const f = fit(label, 14, 57, 9);
        out.push(text(f.txt, cx, 128, f.size, t.ink));
      }
    });
    return out.join("");
  }

  function customBody(m) {
    const views = (m.views || []).filter(Boolean);
    if (!views.length) return lines(C.off, "CHOISIS", "UNE ACTION", C.soft);
    if (m.phase === "stopped" || m.phase === "starting" || m.phase === "closing") {
      const state = { stopped: "ÉTEINT", starting: "DÉMARRE", closing: "ARRÊT…" }[m.phase];
      const off = views.map((v) => ({ ...v, tone: "off" }));
      // Texte seul (sans picto ni image) : il reste, en gris, sinon on ne saurait plus quelle touche c'est.
      const single = off[0].glyph || off[0].image || !off[0].label ? state : off[0].label;
      return off.length === 1 ? viewBody({ ...off[0], label: single }) : tilesBody(off);
    }
    return views.length === 1 ? viewBody(views[0]) : tilesBody(views);
  }

  function offlineBody(m) {
    const name = { record: "rec", pause: "pause", power: "power", vcam: "camera", show: "eye", custom: "tap", quit: "exit" }[m.kind];
    if (m.phase === "launching") return iconLabel(C.idle, name, C.soft, "OUVERTURE…", C.soft, "#8c3341");
    if (m.phase === "nofile") return lines(C.off, "MULTI OBS", "INTROUVABLE", C.muted);
    return iconLabel(C.off, name, C.offInk, "FERMÉ", C.muted, "#5c2a33");
  }

  function render(m) {
    let content;
    if (m.message) content = lines(C.message, m.message[0], m.message[1], "#ffffff");
    else if (m.alertFlash && m.press == null) {
      // Problème grave : une seconde sur deux, la touche crie « ALERTE » (voir le bandeau de Multi OBS).
      content = body("#16070a") + text("ALERTE", 72, 92, 30, C.critical) + text("VOIR MULTI OBS", 72, 121, 15, "#ffffff");
    }
    else if (m.phase === "offline" || m.phase === "launching" || m.phase === "nofile") content = offlineBody(m);
    else if (m.phase === "unset") content = lines(C.off, "CHOISIS", "UN OBS", C.soft);
    else if (m.phase === "missing") content = lines(C.off, "OBS", "INTROUVABLE", C.muted);
    else {
      content = { record: recordBody, pause: pauseBody, power: powerBody, vcam: vcamBody, show: showBody, custom: customBody, quit: quitBody }[m.kind](m);
      if (m.fired) content = inverted(content);   // appui long parti : toute la touche en négatif
      else if (m.press != null) content += holdLayer(content, m.press);
    }
    // Problème grave (rec bloqué, OBS fermé, son en panne…) : cadre rouge autour de la touche.
    const frame = m.critical ? `<rect x="3" y="3" width="138" height="138" rx="9" fill="none" stroke="${C.critical}" stroke-width="6"/>` : "";
    const xlink = content.includes("xlink:href") ? ' xmlns:xlink="http://www.w3.org/1999/xlink"' : "";
    return `<svg xmlns="http://www.w3.org/2000/svg"${xlink} width="144" height="144" viewBox="0 0 144 144">`
      + `<rect width="144" height="144" fill="${C.off}"/>${band(m)}${content}${frame}</svg>`;
  }

  // Pictos proposés pour l'apparence de la touche « Action OBS », dans l'ordre de la fenêtre de réglages.
  const PICKS = ["scene", "monitor", "camera", "webcam", "person", "duo", "gamepad", "chat", "mic", "micoff", "headset",
    "speaker", "speakeroff", "music", "picture", "photo", "film", "pencil", "code", "coffee", "clock", "moon", "home",
    "phone", "star", "heart", "fire", "bolt", "sparkle", "bulb", "bell", "trophy", "flag", "check", "arrow", "live",
    "rec", "pause", "play", "stop", "power", "eye", "eyeoff", "filter"];

  const api = { render, glyph, formatTime, COLORS: C, PICKS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.MultiObsKeys = api;
})(this);
