"use strict";
// Fenêtre de réglages d'une touche (affichée par le logiciel Stream Deck sous la grille des touches).
// Les réglages sont gardés par Stream Deck ; la liste des instances et les menus (scènes, sources,
// filtres, sons) viennent de Multi OBS, via le plugin.

const DEFAULTS = {
  record: { target: "all", short: "record", long: "none", longMs: 600 },
  pause: { target: "all" },
  power: { target: "all" },
  vcam: { target: "" },
  show: { target: "all" },
  custom: { target: "all", short: { op: "pause" }, long: { op: "none" }, longMs: 600 },   // appui long : en option
  quit: { hold: true, longMs: 600 },
};

const OPS = {
  record: { label: "Rec / Stop", color: "#e5243b", glyph: "rec" },
  pause: { label: "Pause / Reprendre", color: "#f5a30a", glyph: "pause" },
  power: { label: "Allumer / Éteindre", color: "#1d9f48", glyph: "power" },
  vcam: { label: "Caméra virtuelle", color: "#0a8dd0", glyph: "camera" },
  show: { label: "Voir OBS", color: "#c9ccd6", glyph: "eye" },
  scene: { label: "Changer de scène", color: "#9b8cff", glyph: "scene" },
  item: { label: "Montrer / cacher une source", color: "#34d399", glyph: "eye" },
  filter: { label: "Activer / couper un filtre", color: "#f472b6", glyph: "filter" },
  mute: { label: "Couper / remettre le son", color: "#fb7185", glyph: "speakeroff" },
  stream: { label: "Lancer / arrêter le live", color: "#ef4444", glyph: "live" },
  screenshot: { label: "Capture d'écran", color: "#e5e7eb", glyph: "photo" },
  none: { label: "Rien", color: "#80858f", glyph: "none" },
};
const ALL_OPS = ["record", "pause", "power", "vcam", "show", "scene", "item", "filter", "mute", "stream", "screenshot", "none"];
const PRESS_OPS = {
  record: { short: ["record", "pause"], long: ["none", "record", "pause"] },
  custom: { short: ALL_OPS, long: ALL_OPS },
};
const SINGLE_ONLY = new Set(["vcam", "scene", "item", "filter", "mute", "stream", "screenshot"]);

const ABOUT = {
  record: "Démarre / arrête l'enregistrement, avec le timecode sur la touche. Au choix : appui court = pause et appui long = rec / stop, pour ne jamais couper un rec par erreur.",
  pause: "Met l'enregistrement en pause, puis le reprend. Avec « Tous », toutes les instances liées s'arrêtent sur la même image.",
  power: "Allume ou éteint l'OBS. Éteindre est refusé pendant un enregistrement, pour ne rien perdre.",
  custom: "La touche à tout faire : scène, source, filtre, son, live, capture, rec… Choisis son action, puis son apparence : ton image, ton texte ou un picto. Tu peux ajouter une 2e action en appui long : la touche se partage en deux, et un cercle blanc grandit pendant le maintien.",
  vcam: "Allume ou éteint la caméra virtuelle de l'OBS (« VCam Rouge »…), pour Discord, Zoom ou une autre instance.",
  show: "Affiche la fenêtre de l'OBS pour régler ses scènes. Avec « Tous » : la fenêtre de Multi OBS.",
  quit: "Ferme Multi OBS. Refusé pendant un enregistrement ou un live, pour ne rien perdre. Quand Multi OBS est fermé, un appui sur la touche l'ouvre.",
};

let ws = null;
let piUUID = "";
let actionUUID = "";
let kind = "";
let settings = {};
let state = { connected: false, launching: false, instances: [] };
const lists = {};          // menus déjà demandés : clé → { status, items, reason }
const pendingLists = {};   // numéro de demande → clé
let nextReq = 1;

window.connectElgatoStreamDeckSocket = function (port, uuid, registerEvent, _info, actionInfo) {
  piUUID = uuid;
  const info = JSON.parse(actionInfo);
  actionUUID = info.action;
  kind = actionUUID.split(".").pop();
  settings = (info.payload && info.payload.settings) || {};
  ws = new WebSocket("ws://127.0.0.1:" + port);
  ws.onopen = () => {
    ws.send(JSON.stringify({ event: registerEvent, uuid: piUUID }));
    toPlugin({ type: "hello" });
  };
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.event === "sendToPropertyInspector" && m.payload) {
      if (m.payload.type === "state") {
        const statuses = (s) => JSON.stringify([s.connected, (s.instances || []).map((i) => i.status)]);
        // Multi OBS revenu, OBS allumé ou éteint : les menus sont redemandés.
        if (statuses(m.payload) !== statuses(state)) clearLists();
        state = m.payload;
        render();
      } else if (m.payload.type === "list") {
        const key = pendingLists[m.payload.req];
        delete pendingLists[m.payload.req];
        if (key) {
          lists[key] = m.payload.ok ? { status: "ok", items: m.payload.items } : { status: "error", reason: m.payload.reason, items: [] };
          render();
        }
      }
    } else if (m.event === "didReceiveSettings") {
      settings = (m.payload && m.payload.settings) || {};
      render();
    }
  };
  render();
};

function toPlugin(payload) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ event: "sendToPlugin", action: actionUUID, context: piUUID, payload }));
}

function save(patch, quiet) {
  Object.assign(settings, patch);
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ event: "setSettings", context: piUUID, payload: settings }));
  if (!quiet) render();
}

function current() {
  return Object.assign({}, DEFAULTS[kind] || {}, settings);
}

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function rgba(hex, a) {
  const n = parseInt(String(hex).replace("#", ""), 16) || 0;
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

function newer(a, b) {
  const pa = String(a || "").split(".").map(Number);
  const pb = String(b || "").split(".").map(Number);
  for (let i = 0; i < 3; i++) {
    if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) > (pb[i] || 0);
  }
  return false;
}

// Action d'un appui : chaîne pour « Enregistrer », objet { op, scene… } pour « Action OBS ».
function getOp(s, press) {
  const v = s[press];
  if (typeof v === "string") return { op: v };
  if (v && typeof v === "object" && OPS[v.op]) return { ...v };
  return { op: "none" };
}

function setOp(press, o) {
  save({ [press]: kind === "custom" ? o : o.op });
}

// Instances que « Tous » pilote, pour cette touche (comme les boutons du haut de Multi OBS).
function allTargets(list) {
  if (kind === "power") return list.filter((i) => !i.minimized || (i.status !== "stopped" && i.status !== "unknown"));
  if (kind === "show") return list.filter((i) => !i.minimized);
  return list.filter((i) => i.linked);
}

// ---------------------------------------------------------------- affichage
function render() {
  if (!kind) return;
  const s = current();
  renderStatus();
  document.getElementById("about").textContent = ABOUT[kind] || "";
  renderTargets(s);
  document.getElementById("targetSection").hidden = kind === "quit";
  document.getElementById("quitSection").hidden = kind !== "quit";
  if (kind === "quit") renderQuit(s);
  const pressing = !!PRESS_OPS[kind];
  document.getElementById("pressSection").hidden = !pressing;
  // « Action OBS » : une action, l'appui long en plus si on veut ; « Enregistrer » : les deux appuis.
  document.getElementById("shortTitle").textContent = kind === "custom" ? "Action" : "Appui court";
  document.getElementById("longTitle").textContent = kind === "custom" ? "Appui long (en option)" : "Appui long";
  document.getElementById("longSub").textContent = kind === "custom" ? "— une 2e action en maintenant la touche" : "— maintenir la touche";
  if (pressing) renderPresses(s);
}

function renderStatus() {
  const box = document.getElementById("status");
  box.className = "status";
  let title;
  let detail = "";
  let button = null;
  if (state.connected) {
    title = `Connecté à Multi OBS ${esc(state.version || "")}`;
    if (newer(state.version, state.pluginVersion)) detail = "Plugin à mettre à jour : Multi OBS → Paramètres → Stream Deck";
  } else if (state.launching) {
    box.classList.add("launching");
    title = "Ouverture de Multi OBS…";
  } else {
    box.classList.add("closed");
    title = state.exeFound ? "Multi OBS est fermé" : "Multi OBS introuvable";
    detail = state.exeFound ? "Un appui sur une touche l'ouvre aussi" : "Installe Multi OBS, puis ouvre-le une fois";
    if (state.exeFound) button = "Ouvrir";
  }
  box.innerHTML = `<span class="dot"></span><span class="txt">${title}${detail ? `<small>${esc(detail)}</small>` : ""}</span>`;
  if (button) {
    const b = el("button", "", button);
    b.onclick = () => toPlugin({ type: "launch" });
    box.appendChild(b);
  }
}

function statusText(i) {
  if (!state.connected) return i.colorName || "";
  if (i.status === "stopped") return "éteint";
  if (i.status === "starting") return "démarre…";
  if (i.status === "closing") return "s'éteint…";
  if (i.recording) return i.paused ? "en pause" : "● enregistre";
  return "allumé";
}

function renderTargets(s) {
  const box = document.getElementById("targets");
  box.innerHTML = "";
  const list = state.instances || [];
  const chips = [];
  if (kind !== "vcam") {
    const picked = allTargets(list);
    const colors = picked.map((i) => (i.shades || [])[2]).filter(Boolean);
    const grad = colors.length > 1 ? `linear-gradient(135deg, ${colors.join(", ")})` : colors[0] || "#476bd7";
    const dot = colors.length > 1 ? `conic-gradient(${colors.map((c, n) => `${c} ${(n * 100) / colors.length}% ${((n + 1) * 100) / colors.length}%`).join(", ")})` : grad;
    chips.push({ id: "all", name: "Tous", sub: kind === "power" || kind === "show" ? "Multi OBS" : "instances liées", bg: grad, dot });
  }
  for (const i of list) {
    const c = (i.shades || [])[2] || "#5b6273";
    chips.push({ id: i.id, name: i.name, sub: statusText(i), bg: c, dot: c, shades: i.shades });
  }
  if (s.target && s.target !== "all" && !list.some((i) => i.id === s.target)) {
    const c = (s.targetShades || [])[2] || "#5b6273";
    chips.push({ id: s.target, name: s.targetName || "OBS", sub: list.length ? "supprimée" : "", bg: c, dot: c, missing: list.length > 0, shades: s.targetShades });
  }
  for (const c of chips) {
    const b = el("button", "chip" + (c.id === s.target ? " selected" : "") + (c.missing ? " missing" : ""));
    b.type = "button";
    b.style.setProperty("--c", c.bg);
    b.innerHTML = `<span class="dot" style="background:${c.dot}"></span><span class="txt">${esc(c.name)}${c.sub ? `<small>${esc(c.sub)}</small>` : ""}</span>`;
    b.onclick = () => {
      if (c.id === s.target) return;
      clearLists();
      save(c.id === "all" ? { target: "all" } : { target: c.id, targetName: c.name, targetShades: c.shades || [] });
    };
    box.appendChild(b);
  }

  const hint = document.getElementById("targetHint");
  let text = "";
  if (!list.length && !state.connected) text = "Ouvre Multi OBS pour voir tes instances ici.";
  else if (kind === "vcam" && !s.target) text = "Choisis l'OBS dont cette touche allume la caméra virtuelle.";
  else if (s.target === "all") {
    const names = allTargets(list).map((i) => i.name);
    const others = list.filter((i) => !names.includes(i.name)).map((i) => i.name);
    if (kind === "show") text = "« Tous » affiche la fenêtre de Multi OBS.";
    else if (kind === "power") {
      text = `« Tous » allume / éteint : ${names.join(", ") || "aucune instance"}, comme les boutons du haut de Multi OBS.`;
      if (others.length) text += ` Les instances réduites (${others.join(", ")}) restent éteintes.`;
    } else {
      text = `« Tous » pilote les instances liées (Link) : ${names.join(", ") || "aucune"}.`;
      if (others.length) text += ` ${others.join(", ")} ${others.length > 1 ? "ne sont pas liées" : "n'est pas liée"}.`;
    }
  }
  hint.textContent = text;
  hint.hidden = !text;
}

function renderPresses(s) {
  const ops = PRESS_OPS[kind];
  const single = s.target && s.target !== "all";
  for (const press of ["short", "long"]) {
    const box = document.querySelector(`.tiles[data-press="${press}"]`);
    const chosen = getOp(s, press);
    box.innerHTML = "";
    const K = window.MultiObsKeys;
    for (const op of ops[press]) {
      const o = OPS[op];
      const b = el("button", "tile" + (chosen.op === op ? " selected" : ""));
      b.type = "button";
      b.disabled = !single && SINGLE_ONLY.has(op) && kind === "custom";
      if (b.disabled) b.title = "Pour un seul OBS : choisis-le en haut";
      b.style.setProperty("--c", o.color);
      b.style.setProperty("--ct", rgba(o.color, 0.18));
      const ink = op === "record" ? "#ffffff" : o.color;
      b.innerHTML = `<svg viewBox="0 0 48 48">${K ? K.glyph(o.glyph, 24, 24, 44, ink, "#e5243b") : ""}</svg><span>${esc(o.label)}</span>`;
      b.onclick = () => {
        if (chosen.op !== op) setOp(press, { op });
      };
      box.appendChild(b);
    }
    renderParams(press, chosen, single ? s.target : "");
    renderLook(press, s, chosen);
  }
  const long = getOp(s, "long");
  document.getElementById("delay").hidden = long.op === "none";
  const slider = document.getElementById("longMs");
  const out = document.getElementById("longMsOut");
  const showMs = (v) => (out.textContent = (v / 1000).toLocaleString("fr-FR", { minimumFractionDigits: 1 }) + " s");
  slider.value = s.longMs || 600;
  showMs(slider.value);
  slider.oninput = () => showMs(slider.value);
  slider.onchange = () => save({ longMs: Number(slider.value) });

  const hint = document.getElementById("pressHint");
  let text = "";
  if (kind === "record" && getOp(s, "short").op === "pause" && long.op === "record") {
    text = "Appui court : pause / reprise. Maintiens la touche pour lancer ou couper le rec.";
  } else if (kind === "custom" && !single) {
    text = "Scène, source, filtre, son, live, capture et caméra virtuelle : pour un seul OBS, choisis-le en haut.";
  }
  hint.textContent = text;
  hint.hidden = !text;
}

// ---------------------------------------------------------------- touche « Fermer Multi OBS »
const ON_CLOSE = {
  stop: "Tes OBS s'éteignent avec Multi OBS (réglage « Tout éteindre »).",
  keep: "Tes OBS continuent de tourner, et Multi OBS les reprend à sa prochaine ouverture (réglage « Laisser tourner les instances »).",
  ask: "Multi OBS passe devant et te demande quoi faire de tes OBS (réglage « Demander »).",
};

function renderQuit(s) {
  const box = document.getElementById("quitTiles");
  box.innerHTML = "";
  const hold = s.hold !== false;
  for (const [value, label, glyph] of [[true, "Maintenir la touche (conseillé)", "exit"], [false, "Appui simple", "tap"]]) {
    const b = el("button", "tile" + (hold === value ? " selected" : ""));
    b.type = "button";
    b.style.setProperty("--c", "#476bd7");
    b.style.setProperty("--ct", rgba("#476bd7", 0.18));
    const K = window.MultiObsKeys;
    b.innerHTML = `<svg viewBox="0 0 48 48">${K ? K.glyph(glyph, 24, 24, 44, "#9fb4f5") : ""}</svg><span>${esc(label)}</span>`;
    b.onclick = () => {
      if (hold !== value) save({ hold: value });
    };
    box.appendChild(b);
  }
  document.getElementById("quitDelay").hidden = !hold;
  const slider = document.getElementById("quitMs");
  const out = document.getElementById("quitMsOut");
  const showMs = (v) => (out.textContent = (v / 1000).toLocaleString("fr-FR", { minimumFractionDigits: 1 }) + " s");
  slider.value = s.longMs || 600;
  showMs(slider.value);
  slider.oninput = () => showMs(slider.value);
  slider.onchange = () => save({ longMs: Number(slider.value) });
  const where = "Multi OBS → ⚙ → Général → « À la fermeture de Multi OBS ».";
  const known = ON_CLOSE[state.onClose];
  document.getElementById("quitHint").textContent = known ? `${known} Modifiable dans ${where}` : `Tes OBS suivent le réglage ${where}`;
}

// ---------------------------------------------------------------- menus : scène, source, filtre, son
function clearLists() {
  for (const k of Object.keys(lists)) delete lists[k];
}

function list(target, what, params) {
  const key = JSON.stringify([target, what, params || {}]);
  if (!lists[key]) {
    lists[key] = { status: "loading", items: [] };
    const req = nextReq++;
    pendingLists[req] = key;
    toPlugin({ type: "list", req, target, what, params: params || {} });
  }
  return lists[key];
}

function select(labelText, value, entry, onPick, placeholder) {
  const row = el("div", "param");
  row.appendChild(el("label", "", esc(labelText)));
  const sel = document.createElement("select");
  const items = entry ? entry.items : [];
  const add = (v, text, disabled) => {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = text;
    opt.disabled = !!disabled;
    sel.appendChild(opt);
  };
  if (!entry) add("", placeholder, true);
  else if (entry.status === "loading") add("", "Chargement…", true);
  else {
    add("", placeholder || "— choisir —", true);
    for (const it of items) add(it, it);
  }
  if (value && !items.includes(value)) add(value, entry && entry.status === "ok" ? `${value} (introuvable)` : value);
  sel.value = value || "";
  sel.disabled = !entry || entry.status === "loading" || (entry.status === "error" && !value);
  sel.onchange = () => onPick(sel.value);
  row.appendChild(sel);
  return row;
}

function renderParams(press, o, target) {
  const box = document.querySelector(`.params[data-press="${press}"]`);
  box.innerHTML = "";
  if (!["scene", "item", "filter", "mute"].includes(o.op)) return;
  if (!target) return;
  const rows = [];
  let error = "";
  if (o.op === "scene") {
    const scenes = list(target, "scenes");
    error = scenes.status === "error" ? scenes.reason : "";
    rows.push(select("Scène", o.scene, scenes, (v) => setOp(press, { ...o, scene: v }), "— choisir une scène —"));
  } else if (o.op === "item") {
    const scenes = list(target, "scenes");
    error = scenes.status === "error" ? scenes.reason : "";
    rows.push(select("Dans la scène", o.scene, scenes, (v) => setOp(press, { op: "item", scene: v, source: "" }), "— choisir une scène —"));
    const items = o.scene ? list(target, "items", { scene: o.scene }) : null;
    rows.push(select("Source", o.source, items, (v) => setOp(press, { ...o, source: v }), o.scene ? "— choisir une source —" : "— d'abord la scène —"));
  } else if (o.op === "filter") {
    const sources = list(target, "sources");
    error = sources.status === "error" ? sources.reason : "";
    rows.push(select("Source", o.source, sources, (v) => setOp(press, { op: "filter", source: v, filter: "" }), "— choisir une source —"));
    const filters = o.source ? list(target, "filters", { source: o.source }) : null;
    rows.push(select("Filtre", o.filter, filters, (v) => setOp(press, { ...o, filter: v }), o.source ? "— choisir un filtre —" : "— d'abord la source —"));
    if (filters && filters.status === "ok" && !filters.items.length) rows.push(el("p", "note", "Cette source n'a pas de filtre."));
  } else if (o.op === "mute") {
    const audio = list(target, "audio");
    error = audio.status === "error" ? audio.reason : "";
    rows.push(select("Source audio", o.input, audio, (v) => setOp(press, { ...o, input: v }), "— choisir une source audio —"));
  }
  for (const r of rows) box.appendChild(r);
  if (error) {
    const name = (state.instances.find((i) => i.id === target) || {}).name || "l'OBS";
    const text = error === "Éteint" || error === "Démarrage" ? `Allume « ${name} » pour voir ses scènes et ses sources.` : `Liste indisponible : ${error}.`;
    box.appendChild(el("p", "note", esc(text)));
  }
}

// ---------------------------------------------------------------- apparence : image, texte, picto
// Réglage look.short / look.long : { icon: picto | "image" | "none" (absent = auto), text, notext, image, iw, ih, fit }.
// L'image est réduite ici (petit côté ≤ 144 px) et gardée dans les réglages de la touche : elle suit les
// profils exportés. Opaque → JPEG, qui remplit la touche ; transparente → PNG, posée entière sur la couleur d'état.
const ICON_NAMES = {
  scene: "Scène", monitor: "Écran", camera: "Caméra", webcam: "Webcam", person: "Personne", duo: "Duo", gamepad: "Manette",
  chat: "Discussion", mic: "Micro", micoff: "Micro coupé", headset: "Casque", speaker: "Son", speakeroff: "Son coupé",
  music: "Musique", picture: "Image", photo: "Photo", film: "Vidéo", pencil: "Dessin", code: "Code", coffee: "Pause café",
  clock: "Horloge", moon: "Nuit", home: "Accueil", phone: "Téléphone", star: "Étoile", heart: "Cœur", fire: "Feu",
  bolt: "Éclair", sparkle: "Étincelle", bulb: "Idée", bell: "Cloche", trophy: "Trophée", flag: "Drapeau", check: "Validé",
  arrow: "Suivant", live: "Live", rec: "Rec", pause: "Pause", play: "Lecture", stop: "Stop", power: "Marche",
  eye: "Visible", eyeoff: "Caché", filter: "Filtre",
};
const lookUI = {};
const lookOpen = {};    // panneau ouvert / fermé par l'utilisateur (sinon : ouvert si l'apparence est perso)
const textTimers = {};

function getLook(s, press) {
  const l = (s.look || {})[press];
  return l && typeof l === "object" ? l : {};
}

function setLook(press, patch, quiet) {
  const look = Object.assign({}, settings.look || {});
  const next = patch ? Object.assign({}, look[press] || {}, patch) : {};
  for (const k of Object.keys(next)) if (next[k] === "" || next[k] == null || next[k] === false) delete next[k];
  if (Object.keys(next).length) look[press] = next;
  else delete look[press];
  save({ look }, quiet);
}

function defaultLabel(o) {
  return {
    record: "REC", pause: "PAUSE", power: "ALLUMÉ / ÉTEINT", vcam: "VCAM ON / OFF", show: "VOIR OBS", stream: "LIVE", screenshot: "CAPTURE",
    scene: o.scene, item: o.source, filter: o.filter, mute: o.input,
  }[o.op] || "";
}

function glyphSvg(name, color) {
  const K = window.MultiObsKeys;
  return `<svg viewBox="0 0 48 48">${K ? K.glyph(name, 24, 24, 42, color, "#e5243b") : ""}</svg>`;
}

function buildLook(press) {
  const root = document.querySelector(`.look[data-press="${press}"]`);
  root.innerHTML = `
    <button type="button" class="look-head"><span class="look-title">Apparence</span><span class="look-sum"></span><span class="look-chev"></span></button>
    <div class="look-body">
      <div class="look-label">Image ou picto</div>
      <div class="seg">
        <button type="button" data-icon="">Auto</button>
        <button type="button" data-icon="image">Image</button>
        <button type="button" data-icon="none">Aucun</button>
      </div>
      <div class="imgzone">
        <div class="thumb"></div>
        <div class="imgside">
          <div class="imgbtns"><button type="button" class="pick">Choisir une image…</button><button type="button" class="remove">Retirer</button></div>
          <div class="seg small fitseg">
            <button type="button" data-fit="cover">Remplir la touche</button>
            <button type="button" data-fit="contain">Image entière</button>
          </div>
          <div class="imgnote">ou glisse une image ici</div>
        </div>
        <input type="file" accept="image/png,image/jpeg,image/gif,image/webp,image/bmp,image/svg+xml" hidden>
      </div>
      <div class="icons"></div>
      <div class="look-label">Texte</div>
      <div class="textrow">
        <input type="text" class="ltext" maxlength="40" spellcheck="false">
        <label class="notext"><input type="checkbox"> Aucun</label>
      </div>
      <p class="look-hint"></p>
      <button type="button" class="look-reset">Remettre l'apparence par défaut</button>
    </div>`;
  const ui = {
    root,
    sum: root.querySelector(".look-sum"),
    chev: root.querySelector(".look-chev"),
    body: root.querySelector(".look-body"),
    seg: [...root.querySelectorAll(".seg:not(.fitseg) button")],
    fit: [...root.querySelectorAll(".fitseg button")],
    zone: root.querySelector(".imgzone"),
    thumb: root.querySelector(".thumb"),
    note: root.querySelector(".imgnote"),
    remove: root.querySelector(".remove"),
    file: root.querySelector("input[type=file]"),
    icons: root.querySelector(".icons"),
    text: root.querySelector(".ltext"),
    notext: root.querySelector(".notext input"),
    hint: root.querySelector(".look-hint"),
    iconBtns: {},
  };
  root.querySelector(".look-head").onclick = () => {
    lookOpen[press] = ui.body.hidden;
    render();
  };
  for (const b of ui.seg) {
    b.onclick = () => {
      const icon = b.dataset.icon;
      if (icon === "image" && !getLook(current(), press).image) ui.file.click();
      else setLook(press, { icon });
    };
  }
  for (const b of ui.fit) b.onclick = () => setLook(press, { fit: b.dataset.fit });
  root.querySelector(".pick").onclick = () => ui.file.click();
  ui.remove.onclick = () => setLook(press, { icon: "", image: "", iw: "", ih: "", fit: "" });
  ui.file.onchange = () => {
    if (ui.file.files[0]) loadImage(press, ui.file.files[0]);
    ui.file.value = "";
  };
  root.addEventListener("dragover", (e) => {
    e.preventDefault();
    root.classList.add("drop");
  });
  root.addEventListener("dragleave", () => root.classList.remove("drop"));
  root.addEventListener("drop", (e) => {
    e.preventDefault();
    root.classList.remove("drop");
    const f = e.dataTransfer && e.dataTransfer.files[0];
    if (f) loadImage(press, f);
  });
  const K = window.MultiObsKeys;
  for (const name of (K && K.PICKS) || []) {
    const b = el("button", "icon");
    b.type = "button";
    b.title = ICON_NAMES[name] || name;
    b.innerHTML = glyphSvg(name, "#e6e8ee");
    b.onclick = () => setLook(press, { icon: name });
    ui.icons.appendChild(b);
    ui.iconBtns[name] = b;
  }
  // Texte : enregistré pendant la frappe (la touche suit), sans redessiner le champ.
  ui.text.oninput = () => {
    clearTimeout(textTimers[press]);
    textTimers[press] = setTimeout(() => setLook(press, { text: ui.text.value }, true), 250);
  };
  ui.text.onchange = () => {
    clearTimeout(textTimers[press]);
    setLook(press, { text: ui.text.value });
  };
  ui.notext.onchange = () => setLook(press, { notext: ui.notext.checked });
  root.querySelector(".look-reset").onclick = () => {
    ui.text.value = "";
    setLook(press, null);
  };
  return (lookUI[press] = ui);
}

function lookSummary(l) {
  const parts = [];
  if (l.icon === "image" && l.image) parts.push("image");
  else if (l.icon === "none") parts.push("sans picto");
  else if (l.icon && ICON_NAMES[l.icon]) parts.push(`picto ${ICON_NAMES[l.icon].toLowerCase()}`);
  if (l.notext) parts.push("sans texte");
  else if (l.text && l.text.trim()) parts.push(`« ${l.text.trim()} »`);
  return parts.join(" · ");
}

function renderLook(press, s, o) {
  const root = document.querySelector(`.look[data-press="${press}"]`);
  root.hidden = kind !== "custom" || o.op === "none";
  if (root.hidden) return;
  const ui = lookUI[press] || buildLook(press);
  const l = getLook(s, press);
  const sum = lookSummary(l);
  const open = lookOpen[press] != null ? lookOpen[press] : !!sum;
  ui.body.hidden = !open;
  ui.root.classList.toggle("open", open);
  ui.sum.textContent = sum || "par défaut";
  ui.sum.classList.toggle("custom", !!sum);
  ui.chev.textContent = open ? "Fermer ▴" : "Personnaliser ▾";
  if (!open) return;
  const icon = l.icon === "image" && !l.image ? "" : l.icon || "";
  for (const b of ui.seg) b.classList.toggle("selected", b.dataset.icon === icon);
  ui.seg[0].innerHTML = `${glyphSvg((OPS[o.op] || OPS.none).glyph, "#e6e8ee")}<span>Auto</span>`;
  ui.seg[1].innerHTML = `${glyphSvg("picture", "#e6e8ee")}<span>Image</span>`;
  ui.seg[2].innerHTML = `${glyphSvg("none", "#e6e8ee")}<span>Aucun</span>`;
  for (const [name, b] of Object.entries(ui.iconBtns)) b.classList.toggle("selected", icon === name);
  ui.zone.hidden = icon !== "image";
  ui.thumb.style.backgroundImage = l.image ? `url("${l.image}")` : "";
  ui.thumb.classList.toggle("contain", l.fit === "contain");
  for (const b of ui.fit) b.classList.toggle("selected", (l.fit || "cover") === b.dataset.fit);
  if (document.activeElement !== ui.text) ui.text.value = l.text || "";
  ui.text.disabled = !!l.notext;
  ui.notext.checked = !!l.notext;
  const auto = defaultLabel(o);
  ui.text.placeholder = auto ? `Auto : ${auto}` : "Auto";
  const live = ["record", "pause", "stream"].includes(o.op);
  ui.hint.textContent = l.notext ? "Rien d'écrit sur la touche : seulement l'image ou le picto."
    : live ? "Vide = texte de l'action. Pendant un rec ou un live, le timecode reste affiché."
      : "Vide = texte de l'action (le nom de la scène, de la source…).";
}

function loadImage(press, file) {
  const ui = lookUI[press];
  if (!/^image\//.test(file.type)) {
    ui.note.textContent = "Ce fichier n'est pas une image.";
    return;
  }
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => {
    URL.revokeObjectURL(url);
    let w = img.naturalWidth;
    let h = img.naturalHeight;
    if (!w || !h) w = h = 144;   // SVG sans taille
    const k = Math.min(1, 144 / Math.min(w, h), 256 / Math.max(w, h));
    const cw = Math.max(1, Math.round(w * k));
    const ch = Math.max(1, Math.round(h * k));
    const c = document.createElement("canvas");
    c.width = cw;
    c.height = ch;
    const g = c.getContext("2d");
    g.imageSmoothingQuality = "high";
    g.drawImage(img, 0, 0, cw, ch);
    const px = g.getImageData(0, 0, cw, ch).data;
    let alpha = false;
    for (let i = 3; i < px.length; i += 4) {
      if (px[i] < 250) {
        alpha = true;
        break;
      }
    }
    ui.note.textContent = "ou glisse une image ici";
    setLook(press, { icon: "image", image: alpha ? c.toDataURL("image/png") : c.toDataURL("image/jpeg", 0.9), iw: cw, ih: ch, fit: alpha ? "contain" : "cover" });
  };
  img.onerror = () => {
    URL.revokeObjectURL(url);
    ui.note.textContent = "Image illisible : essaie un PNG ou un JPG.";
  };
  img.src = url;
}
