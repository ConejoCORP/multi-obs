"use strict";
// Plugin Stream Deck de Multi OBS : des touches colorées pour piloter chaque OBS, ou tout Multi OBS.
//
// Stream Deck ⇄ plugin : protocole WebSocket du SDK (arguments -port -pluginUUID -registerEvent).
// Plugin ⇄ Multi OBS : API locale de Multi OBS (multiobs/remote.py), trouvée grâce au fichier
// %LOCALAPPDATA%\Multi OBS\remote.json (port + jeton). Multi OBS pousse l'état des instances, le
// plugin envoie les commandes. Aucune dépendance : seulement Node (fourni par le logiciel Stream Deck).
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");
const { WebSocketClient } = require("./ws");
const keys = require("./keys");

const PLUGIN_DIR = path.resolve(__dirname, "..");
const PREFIX = "com.stundzow.multiobs.";
const KINDS = ["record", "pause", "power", "vcam", "show", "custom", "quit"];
const OPS = ["record", "pause", "power", "vcam", "show", "scene", "item", "filter", "mute", "stream", "screenshot", "none"];
const SINGLE_ONLY = ["vcam", "scene", "item", "filter", "mute", "stream", "screenshot"];   // pas avec « Tous »
const DEFAULTS = {
  record: { target: "all", short: "record", long: "none", longMs: 600 },
  pause: { target: "all" },
  power: { target: "all" },
  vcam: { target: "" },
  show: { target: "all" },
  custom: { target: "all", short: { op: "pause" }, long: { op: "none" }, longMs: 600 },   // appui long : en option
  quit: { hold: true, longMs: 600 },   // fermer Multi OBS : en maintenant la touche, sauf si on choisit l'appui simple
};
const APP_SHADES = ["#718CDC", "#476BD7", "#284CB8", "#213E97", "#1A3278", "#162A64"];   // bleu de Multi OBS
const SEP = "\u001f";   // clés de suivi de Multi OBS : type, puis noms
const LOCAL = process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
const DISCOVERY = process.env.MULTIOBS_REMOTE_FILE || path.join(LOCAL, "Multi OBS", "remote.json");
const DEFAULT_EXE = path.join(LOCAL, "Programs", "Multi OBS", "Multi OBS.exe");
const LOG_DIR = process.env.MULTIOBS_PLUGIN_LOGS || path.join(PLUGIN_DIR, "logs");
const LAUNCH_WAIT_MS = 30000;
const MESSAGE_MS = 1800;
const VERSION = (() => {
  try {
    return JSON.parse(fs.readFileSync(path.join(PLUGIN_DIR, "manifest.json"), "utf8")).Version;
  } catch {
    return "?";
  }
})();

// ------------------------------------------------------------------ journal
const LOG_FILE = path.join(LOG_DIR, "multiobs.log");
function log(...parts) {
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    try {
      if (fs.statSync(LOG_FILE).size > 200000) fs.renameSync(LOG_FILE, LOG_FILE + ".1");
    } catch {}
    fs.appendFileSync(LOG_FILE, `${new Date().toISOString()} ${parts.join(" ")}\n`);
  } catch {}
}
process.on("uncaughtException", (e) => log("Erreur :", e && e.stack ? e.stack : e));
process.on("unhandledRejection", (e) => log("Erreur :", e && e.stack ? e.stack : e));

// ------------------------------------------------------------------ Stream Deck
const args = {};
for (let i = 2; i < process.argv.length - 1; i++) {
  if (process.argv[i].startsWith("-")) args[process.argv[i].slice(1)] = process.argv[i + 1];
}
const PLUGIN_UUID = args.pluginUUID;
const sd = new WebSocketClient(Number(args.port));

function sdSend(msg) {
  sd.send(JSON.stringify(msg));
}

sd.on("open", () => {
  log(`Plugin ${VERSION} démarré`);
  sdSend({ event: args.registerEvent, uuid: PLUGIN_UUID });
  sdSend({ event: "getGlobalSettings", context: PLUGIN_UUID });
  connectLink();
});
sd.on("message", (text) => {
  let m;
  try {
    m = JSON.parse(text);
  } catch {
    return;
  }
  try {
    onStreamDeck(m);
  } catch (e) {
    log("Erreur :", e.stack || e);
  }
});
sd.on("error", (e) => log("Stream Deck :", e.message));
sd.on("close", () => {
  log("Stream Deck fermé : fin du plugin");
  process.exit(0);
});
sd.connect(5000);

const actions = new Map(); // contexte → { ctx, kind, uuid, settings, image, press, message, pi }

function kindOf(uuid) {
  const kind = String(uuid || "").startsWith(PREFIX) ? uuid.slice(PREFIX.length) : "";
  return KINDS.includes(kind) ? kind : null;
}

function withDefaults(kind, settings) {
  return Object.assign({}, DEFAULTS[kind], settings || {});
}

// Action d'un appui : { op, scene, source, filter, input } (anciens réglages : juste le nom de l'action).
function normalizeOp(v) {
  if (typeof v === "string") return { op: v };
  if (v && typeof v === "object" && OPS.includes(v.op)) return { ...v };
  return { op: "none" };
}

function onStreamDeck(m) {
  const ctx = m.context;
  const p = m.payload || {};
  switch (m.event) {
    case "willAppear": {
      const kind = kindOf(m.action);
      if (!kind) return;
      actions.set(ctx, { ctx, kind, uuid: m.action, settings: withDefaults(kind, p.settings), image: "", press: null, message: null, pi: false });
      render(ctx);
      syncWatches();
      wake();
      break;
    }
    case "willDisappear": {
      const a = actions.get(ctx);
      if (a && a.press) clearTimeout(a.press.timer);
      actions.delete(ctx);
      syncWatches();
      break;
    }
    case "didReceiveSettings": {
      const a = actions.get(ctx);
      if (a) {
        a.settings = withDefaults(a.kind, p.settings);
        render(ctx);
        syncWatches();
        if (a.pi) sendToInspector(a);
      }
      break;
    }
    case "keyDown":
      keyDown(m.action, ctx, p);
      break;
    case "keyUp":
      keyUp(ctx, p);
      break;
    case "propertyInspectorDidAppear": {
      const a = actions.get(ctx);
      if (a) {
        a.pi = true;
        sendToInspector(a);
      }
      break;
    }
    case "propertyInspectorDidDisappear": {
      const a = actions.get(ctx);
      if (a) a.pi = false;
      break;
    }
    case "sendToPlugin": {
      const a = actions.get(ctx);
      if (p.type === "launch") launchMultiObs(null);
      else if (p.type === "list") listForInspector(ctx, m.action, p);
      else if (a) sendToInspector(a);
      break;
    }
    case "didReceiveGlobalSettings":
      loadCache(p.settings);
      renderAll();
      break;
    case "systemDidWakeUp":
      connectLink();
      break;
  }
}

// ------------------------------------------------------------------ Multi OBS
const link = {
  ws: null, connected: false, version: "", state: null, receivedAt: 0,
  pending: new Map(), nextId: 1, retry: null, exe: "", launching: 0, queued: null, exeMissing: false, piSig: "",
  watchSig: "",
};

function connectLink() {
  if (link.ws || link.connected) return;
  let info = null;
  try {
    info = JSON.parse(fs.readFileSync(DISCOVERY, "utf8"));
  } catch {}
  if (info && info.exe) link.exe = info.exe;
  if (!info || !info.port || !info.token) {
    scheduleReconnect();
    return;
  }
  const ws = (link.ws = new WebSocketClient(info.port));
  ws.on("open", () => ws.send(JSON.stringify({ op: "hello", token: info.token, client: "Stream Deck", version: VERSION })));
  ws.on("message", onLinkMessage);
  ws.on("error", () => {});
  ws.on("close", () => {
    if (link.ws === ws) link.ws = null;
    const was = link.connected;
    link.connected = false;
    link.state = null;
    for (const p of link.pending.values()) {
      clearTimeout(p.timer);
      p.resolve({ ok: false, reason: "Multi OBS fermé" });
    }
    link.pending.clear();
    if (was) {
      log("Multi OBS déconnecté");
      renderAll();
      notifyInspectors(true);
    }
    scheduleReconnect();
  });
  ws.connect(2500);
}

function scheduleReconnect(delay) {
  if (link.retry) return;
  link.retry = setTimeout(() => {
    link.retry = null;
    connectLink();
  }, delay || (link.launching ? 600 : 2000));
}

function onLinkMessage(text) {
  let m;
  try {
    m = JSON.parse(text);
  } catch {
    return;
  }
  if (m.op === "welcome") {
    link.connected = true;
    link.version = m.version || "";
    log(`Connecté à Multi OBS ${link.version}`);
    const queued = link.queued;
    link.launching = 0;
    link.queued = null;
    setState(m.state);
    syncWatches(true);
    notifyInspectors(true);
    if (queued) command(queued.target, queued.action, queued.mode);
  } else if (m.op === "state") {
    setState(m.state);
  } else if (m.op === "result" || m.op === "list") {
    const p = link.pending.get(m.id);
    if (p) {
      link.pending.delete(m.id);
      clearTimeout(p.timer);
      p.resolve(m);
    }
  } else if (m.op === "error") {
    log("Multi OBS refuse la connexion :", m.reason);
  }
}

function setState(state) {
  if (!state || !Array.isArray(state.instances)) return;
  link.state = state;
  link.receivedAt = Date.now();
  saveCache(state.instances);
  renderAll();
  notifyInspectors(false);
  wake();
}

function request(msg, failReason) {
  return new Promise((resolve) => {
    if (!link.connected) {
      resolve({ ok: false, reason: "Multi OBS fermé", items: [] });
      return;
    }
    const id = link.nextId++;
    const timer = setTimeout(() => {
      link.pending.delete(id);
      resolve({ ok: false, reason: failReason, items: [] });
    }, 4000);
    link.pending.set(id, { resolve, timer });
    link.ws.send(JSON.stringify({ ...msg, id }));
  });
}

function command(target, action, mode = "toggle", params = {}) {
  return request({ op: "cmd", target, action, mode, params }, "Pas de réponse");
}

// Ce que les touches affichent et que Multi OBS doit suivre : filtres, sources visibles, sons coupés.
function syncWatches(force) {
  if (!link.connected) return;
  const items = [];
  for (const a of actions.values()) {
    const target = a.settings.target;
    if (a.kind !== "custom" || !target || target === "all") continue;
    for (const o of [normalizeOp(a.settings.short), normalizeOp(a.settings.long)]) {
      if (o.op === "item") items.push({ target, kind: "item", a: o.scene || "", b: o.source || "" });
      else if (o.op === "filter") items.push({ target, kind: "filter", a: o.source || "", b: o.filter || "" });
      else if (o.op === "mute") items.push({ target, kind: "mute", a: o.input || "", b: "" });
    }
  }
  const sig = JSON.stringify(items);
  if (!force && sig === link.watchSig) return;
  link.watchSig = sig;
  link.ws.send(JSON.stringify({ op: "watch", items }));
}

function multiObsExe() {
  if ("MULTIOBS_EXE" in process.env) return process.env.MULTIOBS_EXE && fs.existsSync(process.env.MULTIOBS_EXE) ? process.env.MULTIOBS_EXE : "";
  for (const exe of [link.exe, DEFAULT_EXE]) if (exe && fs.existsSync(exe)) return exe;
  return "";
}

function launchMultiObs(queued) {
  if (link.connected) return;
  if (link.launching && Date.now() - link.launching < LAUNCH_WAIT_MS) {
    if (queued) link.queued = queued;
    return;
  }
  const exe = multiObsExe();
  if (!exe) {
    link.exeMissing = true;
    renderAll();
    setTimeout(() => {
      link.exeMissing = false;
      renderAll();
    }, 2500);
    return;
  }
  log("Ouverture de Multi OBS :", exe);
  // Le logiciel Stream Deck est une appli Qt : ses réglages Qt ne doivent pas passer à Multi OBS.
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !/^(QT_|QML|QTWEBENGINE)/i.test(k)));
  try {
    spawn(exe, [], { detached: true, stdio: "ignore", cwd: path.dirname(exe), env }).unref();
  } catch (e) {
    log("Ouverture impossible :", e.message);
    return;
  }
  link.launching = Date.now();
  link.queued = queued || null;
  renderAll();
  notifyInspectors(true);
  setTimeout(() => {
    if (link.launching && !link.connected) {
      link.launching = 0;
      link.queued = null;
      renderAll();
      notifyInspectors(true);
    }
  }, LAUNCH_WAIT_MS);
  if (link.retry) clearTimeout(link.retry);
  link.retry = null;
  scheduleReconnect(600);
}

// Dernière liste d'instances connue, gardée dans les réglages globaux du plugin : noms et couleurs
// restent affichés quand Multi OBS est fermé.
let cache = { instances: [], sig: "[]" };
function loadCache(settings) {
  if (settings && Array.isArray(settings.instances)) cache = { instances: settings.instances, sig: JSON.stringify(settings.instances) };
}
function saveCache(instances) {
  const list = instances.map((i) => ({ id: i.id, name: i.name, color: i.color, colorName: i.colorName, shades: i.shades,
                                       linked: i.linked, minimized: i.minimized }));
  const sig = JSON.stringify(list);
  if (sig === cache.sig) return;
  cache = { instances: list, sig };
  sdSend({ event: "setGlobalSettings", context: PLUGIN_UUID, payload: { instances: list } });
}

function knownInstances() {
  return link.state ? link.state.instances : cache.instances;
}

// ------------------------------------------------------------------ appuis
function opsFor(a, inMultiAction) {
  const s = a.settings;
  if (a.kind === "quit") {
    // Multi OBS fermé : un appui l'ouvre. Ouvert : maintenir pour le fermer (un appui court explique quoi faire).
    if (!link.connected) return { short: { op: "open" }, long: null };
    const quit = { op: "quit" };
    return s.hold === false || inMultiAction ? { short: quit, long: null } : { short: { op: "quithint" }, long: quit };
  }
  if (a.kind === "record") {
    const short = ["record", "pause"].includes(s.short) ? s.short : "record";
    const long = ["record", "pause"].includes(s.long) && !inMultiAction ? s.long : null;
    return { short: { op: short }, long: long && { op: long } };
  }
  if (a.kind === "custom") {
    const valid = (o) => (o.op !== "none" ? o : null);
    const short = valid(normalizeOp(s.short));
    const long = valid(normalizeOp(s.long));
    return inMultiAction ? { short: short || long, long: null } : { short, long };
  }
  return { short: { op: a.kind }, long: null };
}

function keyDown(uuid, ctx, p) {
  let a = actions.get(ctx);
  if (!a) {
    // Action d'une multi-action : pas de willAppear, les réglages arrivent avec l'appui.
    const kind = kindOf(uuid);
    if (!kind) return;
    a = { ctx, kind, uuid, settings: withDefaults(kind, p.settings), transient: true };
  }
  a.message = null; // un nouvel appui efface le message précédent
  const ops = opsFor(a, p.isInMultiAction);
  if (p.isInMultiAction || !ops.long) {
    run(a, ops.short);
    return;
  }
  const ms = Math.min(3000, Math.max(250, Number(a.settings.longMs) || 600));
  const press = (a.press = { start: Date.now(), ms, ops, fired: false });
  press.timer = setTimeout(() => {
    if (a.press !== press) return;
    press.fired = true;
    run(a, ops.long);
    render(ctx);
  }, ms);
  render(ctx);
  wake();
}

function keyUp(ctx, p) {
  const a = actions.get(ctx);
  if (!a || p.isInMultiAction || !a.press) return;
  const press = a.press;
  a.press = null;
  clearTimeout(press.timer);
  if (!press.fired) run(a, press.ops.short);
  render(ctx);
}

function splitReason(reason) {
  const words = String(reason || "Impossible").toUpperCase().split(" ");
  if (words.join(" ").length <= 10 || words.length < 2) return [words.join(" ")];
  let best = 1;
  for (let i = 1; i < words.length; i++) {
    const d = Math.abs(words.slice(0, i).join(" ").length - words.slice(i).join(" ").length);
    if (d < Math.abs(words.slice(0, best).join(" ").length - words.slice(best).join(" ").length)) best = i;
  }
  return [words.slice(0, best).join(" "), words.slice(best).join(" ")];
}

function flash(a, lines) {
  if (a.transient) {
    sdSend({ event: "showAlert", context: a.ctx });
    return;
  }
  a.message = { lines, until: Date.now() + MESSAGE_MS };
  render(a.ctx);
  wake();
}

async function run(a, o) {
  if (!o) return;
  if (a.kind === "quit") {
    runQuit(a, o);
    return;
  }
  const target = a.settings.target;
  if (!target || (target === "all" && SINGLE_ONLY.includes(o.op))) {
    flash(a, ["CHOISIS", "UN OBS"]);
    return;
  }
  if (!link.connected) {
    // Multi OBS fermé : on l'ouvre ; « allumer » est fait dès qu'il répond.
    const queued = o.op === "power" ? { target, action: "power", mode: "on" }
      : o.op === "show" && target !== "all" ? { target, action: "show", mode: "toggle" } : null;
    launchMultiObs(queued);
    return;
  }
  // Rec réglé « appui court = pause » : sans rec en cours, on dit comment le lancer.
  if (a.kind === "record" && o.op === "pause") {
    const e = entity(a);
    const long = opsFor(a).long;
    if (e && !e.recording && long && long.op === "record") {
      flash(a, ["MAINTIENS", "POUR LANCER"]);
      return;
    }
  }
  const params = { scene: o.scene || "", source: o.source || "", filter: o.filter || "", input: o.input || "" };
  const res = await command(target, o.op, "toggle", params);
  if (!res.ok) flash(a, splitReason(res.reason));
  else if (o.op === "screenshot" && !a.transient) sdSend({ event: "showOk", context: a.ctx });
}

// Fermer Multi OBS : il suit son réglage « À la fermeture » ; refusé pendant un rec ou un live.
async function runQuit(a, o) {
  if (o.op === "open" || !link.connected) {
    launchMultiObs(null);
    return;
  }
  if (o.op === "quithint") {
    flash(a, ["MAINTIENS", "POUR FERMER"]);
    return;
  }
  log("Fermeture de Multi OBS demandée");
  const res = await command("app", "quit");
  if (!res.ok) flash(a, splitReason(res.reason));
}

// ------------------------------------------------------------------ dessin
function entity(a) {
  const st = link.state;
  const t = a.settings.target;
  if (!st || !t) return null;
  return t === "all" ? st.all : st.instances.find((i) => i.id === t) || null;
}

function liveMs(e) {
  if (!e || !e.recording) return 0;
  return e.paused ? e.recMs : e.recMs + (Date.now() - link.receivedAt);
}

function streamMs(e) {
  return e && e.streaming ? (e.streamMs || 0) + (Date.now() - link.receivedAt) : 0;
}

// Couleurs du bandeau « TOUS » : les instances que la touche pilote vraiment, atténuées si éteintes.
function bandColors(kind) {
  const list = knownInstances();
  let picked;
  if (kind === "power") picked = list.filter((i) => !i.minimized || (i.status && i.status !== "stopped"));
  else if (kind === "show") picked = list.filter((i) => !i.minimized);
  else picked = list.filter((i) => i.linked !== false);
  return picked.map((i) => ({ color: (i.shades || [])[2] || "#5b6273", dim: !link.connected || !i.status || i.status === "stopped" }));
}

function watchValue(e, kind, a, b) {
  return e && e.watch ? e.watch[[kind, a || "", b || ""].join(SEP)] : undefined;
}

// Ce qu'affiche une action de la touche « Action OBS » : picto, couleur d'état, texte.
function opView(o, e, all) {
  const rec = e && e.recording ? (e.paused ? "paused" : "recording") : "idle";
  const recTone = rec === "recording" ? "rec" : rec === "paused" ? "pause" : "idle";
  const on = (v) => (v === true ? "on" : v === false ? "dim" : "idle");
  switch (o.op) {
    case "record":
      return { glyph: rec === "idle" ? "rec" : "stop", tone: recTone, label: rec === "idle" ? "REC" : keys.formatTime(liveMs(e)), live: rec !== "idle" };
    case "pause":
      return { glyph: rec === "paused" ? "play" : "pause", tone: recTone, label: rec === "idle" ? "PAUSE" : keys.formatTime(liveMs(e)), live: rec !== "idle" };
    case "power": {
      const running = !!e && e.status !== "stopped";
      return { glyph: "power", tone: running ? "on" : "idle", label: running ? "ALLUMÉ" : "ÉTEINT" };
    }
    case "vcam":
      return { glyph: "camera", tone: e && e.vcam ? "vcam" : "idle", label: e && e.vcam ? "VCAM ON" : "VCAM OFF" };
    case "show":
      return { glyph: "eye", tone: "idle", label: all ? "MULTI OBS" : "VOIR OBS" };
    case "scene":
      return { glyph: "scene", tone: e && o.scene && e.scene === o.scene ? "on" : "idle", label: o.scene || "SCÈNE ?" };
    case "item": {
      const v = watchValue(e, "item", o.scene, o.source);
      return { glyph: v === false ? "eyeoff" : "eye", tone: on(v), label: o.source || "SOURCE ?" };
    }
    case "filter":
      return { glyph: "filter", tone: on(watchValue(e, "filter", o.source, o.filter)), label: o.filter || "FILTRE ?" };
    case "mute": {
      const muted = e && e.mutes ? e.mutes[o.input] : undefined;
      return { glyph: muted ? "speakeroff" : "speaker", tone: muted ? "rec" : "idle", label: o.input || "SON ?" };
    }
    case "stream":
      return { glyph: "live", tone: e && e.streaming ? "rec" : "idle", label: e && e.streaming ? keys.formatTime(streamMs(e)) : "LIVE", live: !!(e && e.streaming) };
    case "screenshot":
      return { glyph: "photo", tone: "idle", label: "CAPTURE" };
    default:
      return { glyph: "none", tone: "idle", label: "" };
  }
}

// Apparence choisie dans la fenêtre de réglages : { icon: picto | "image" | "none", text, notext, image, iw, ih, fit }.
// Le texte perso remplace le texte de l'action, sauf un timecode en cours (rec, pause, live).
const IMAGE_RE = /^data:image\/(png|jpeg);base64,[A-Za-z0-9+/=]+$/;
function applyLook(v, look) {
  if (!look || typeof look !== "object") return v;
  const out = { ...v };
  if (look.icon === "none") out.glyph = "";
  else if (look.icon === "image") {
    if (typeof look.image === "string" && IMAGE_RE.test(look.image)) {
      out.image = { src: look.image, w: Number(look.iw) || 0, h: Number(look.ih) || 0, fit: look.fit === "contain" ? "contain" : "cover" };
    }
  } else if (keys.PICKS.includes(look.icon)) out.glyph = look.icon;
  if (look.notext) out.label = "";
  else if (typeof look.text === "string" && look.text.trim() && !v.live) out.label = look.text.trim().slice(0, 40);
  return out;
}

function model(a) {
  const s = a.settings;
  const all = s.target === "all";
  const m = { kind: a.kind, hours: false, rec: "idle", recMs: 0 };
  const meta = all ? null : knownInstances().find((i) => i.id === s.target);
  m.band = all ? { all: true, colors: bandColors(a.kind) }
    : { name: meta ? meta.name : s.targetName || "?", shades: meta ? meta.shades : s.targetShades };
  const press = a.press;
  if (press) {
    m.press = press.fired ? null : Math.min(1, (Date.now() - press.start) / press.ms);
    m.fired = press.fired;
  }
  if (a.message && Date.now() < a.message.until) m.message = a.message.lines;
  if (a.kind === "quit") {
    m.band = { name: "Multi OBS", shades: APP_SHADES };
    if (!link.connected) {
      m.phase = link.launching ? "launching" : link.exeMissing ? "nofile" : "offline";
      m.band.dim = true;
      return m;
    }
    const insts = link.state.instances;
    const recording = insts.some((i) => i.recording);
    m.phase = "ready";
    m.busy = recording || insts.some((i) => i.streaming);   // fermeture refusée : touche grisée
    m.recDot = recording;
    m.blink = Math.floor(Date.now() / 1000) % 2 === 0;
    return m;
  }
  if (!s.target || (a.kind === "vcam" && all)) {
    m.phase = "unset";
    m.band = { name: "?" };
    return m;
  }
  if (!link.connected) {
    m.phase = link.launching ? "launching" : link.exeMissing ? "nofile" : "offline";
    m.band.dim = true;
    return m;
  }
  const e = entity(a);
  if (!e) {
    m.phase = "missing";
    m.band.dim = true;
    return m;
  }
  m.rec = e.recording ? (e.paused ? "paused" : "recording") : "idle";
  m.recMs = liveMs(e);
  m.blink = Math.floor(m.recMs / 1000) % 2 === 0;
  m.remux = e.remux;
  m.alert = !!e.alert;
  m.critical = !!e.critical;
  m.alertFlash = m.critical && Math.floor(Date.now() / 1000) % 2 === 1;   // une seconde sur deux : « ALERTE »
  if (all) {
    const st = link.state.all;
    if (a.kind === "power") {
      m.phase = st.status;
      m.partial = `${st.shownRunning}/${st.shown}`;
    } else if (a.kind === "show") {
      m.phase = "ready";
    } else {
      m.phase = st.targets > 0 ? "ready" : st.status === "starting" ? "starting" : "stopped";
    }
    m.blocked = st.recording && !st.canPause;
    m.recDot = st.recording && (a.kind === "power" || a.kind === "show");
  } else {
    m.phase = e.status;
    m.band.dim = e.status === "stopped";
    m.vcam = e.vcam;
    m.blocked = e.pauseBlocked;
    m.recDot = e.recording && (a.kind === "power" || a.kind === "vcam" || a.kind === "show");
  }
  if (a.kind === "custom") {
    const ops = [normalizeOp(s.short), normalizeOp(s.long)];
    if (ops[1].op === "none") ops.pop();
    const look = s.look || {};
    m.views = ops.length === 1 && ops[0].op === "none" ? [] : ops.map((o, i) => applyLook(opView(o, e, all), look[i ? "long" : "short"]));
    if (m.views.some((v, i) => SINGLE_ONLY.includes(ops[i].op)) && all) m.phase = "unset";
  }
  return m;
}

function render(ctx) {
  const a = actions.get(ctx);
  if (!a) return;
  if (a.message && Date.now() >= a.message.until) a.message = null;
  const svg = keys.render(model(a));
  if (svg === a.image) return;
  a.image = svg;
  sdSend({ event: "setImage", context: ctx, payload: { image: "data:image/svg+xml;base64," + Buffer.from(svg).toString("base64") } });
}

function renderAll() {
  for (const ctx of actions.keys()) render(ctx);
}

// Horloge : seulement quand quelque chose bouge (rec ou live en cours, appui long, message affiché).
let ticker = null;
let ticks = 0;
function busy() {
  const st = link.state;
  if (st && (st.all.recording || st.instances.some((i) => i.recording || i.streaming || i.critical))) return true;
  for (const a of actions.values()) if (a.press || a.message) return true;
  return false;
}
function wake() {
  if (ticker || !busy()) return;
  ticker = setInterval(() => {
    // Appui long : 20 images/s pour le cercle ; sinon le timecode suffit à ~7 images/s.
    const pressing = [...actions.values()].some((a) => a.press && !a.press.fired);
    if (pressing || ++ticks % 3 === 0) renderAll();
    if (!busy()) {
      clearInterval(ticker);
      ticker = null;
    }
  }, 50);
}

// ------------------------------------------------------------------ fenêtre de réglages
function inspectorPayload() {
  return {
    type: "state",
    connected: link.connected,
    launching: !!link.launching,
    version: link.version,
    pluginVersion: VERSION,
    exeFound: !!multiObsExe(),
    onClose: (link.state && link.state.onClose) || "",   // réglage « À la fermeture de Multi OBS » (touche Fermer)
    instances: knownInstances().map((i) => ({
      id: i.id, name: i.name, color: i.color, colorName: i.colorName, shades: i.shades,
      linked: i.linked !== false, minimized: !!i.minimized, status: i.status || "unknown",
      recording: !!i.recording, paused: !!i.paused, vcamName: i.vcamName || "",
    })),
  };
}

function sendToInspector(a) {
  sdSend({ event: "sendToPropertyInspector", action: a.uuid, context: a.ctx, payload: inspectorPayload() });
}

function notifyInspectors(force) {
  const p = inspectorPayload();
  const sig = JSON.stringify([p.connected, p.launching, p.onClose, p.instances.map((i) => [i.id, i.name, i.color, i.linked, i.minimized, i.status])]);
  if (!force && sig === link.piSig) return;
  link.piSig = sig;
  for (const a of actions.values()) if (a.pi) sdSend({ event: "sendToPropertyInspector", action: a.uuid, context: a.ctx, payload: p });
}

// Listes des menus de la fenêtre de réglages (scènes, sources, filtres, sons) : demandées à Multi OBS.
async function listForInspector(ctx, uuid, p) {
  const res = await request({ op: "list", target: p.target, what: p.what, params: p.params || {} }, "Pas de réponse");
  sdSend({ event: "sendToPropertyInspector", action: uuid, context: ctx,
           payload: { type: "list", req: p.req, ok: !!res.ok, items: res.items || [], reason: res.reason || "" } });
}
