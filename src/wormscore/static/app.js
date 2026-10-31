// wormscore scoring page. Talks only to the local server. Shows images and saves
// answers; it never receives brightness numbers.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
// Mac keyboards: the hints name Cmd (Ctrl works there too)
const MOD = /Mac|iPhone|iPad/.test(navigator.platform) ? "Cmd" : "Ctrl";
$$(".mod").forEach(el => (el.textContent = MOD));

// Small DOM builder so text from config files is never parsed as HTML.
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid);
  return el;
}

// Region colours come from style.css, so the outlines and the page always match.
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const PALETTE = ["--l1", "--l2", "--l3", "--l4"].map(css);
const BG_COLOR = css("--lbg");
const SUGGEST = css("--suggest");
const MUTED = css("--muted");
const FLAG_QUESTIONS = {
  repeat: { q: "Count this image for this animal?", options: ["count", "do not count"] },
  edge: { q: "Is the whole cell inside the stack?", options: ["yes", "no", "can't tell"] },
  saturation: { q: "Before saving:", options: ["noted"] },
  streak: { q: "Before saving:", options: ["noted"] },
};
// What an answer says on screen, where that differs from the value saved with the score.
const ANSWER_TEXT = { noted: "I have read this" };

const st = {
  cfg: null, colors: {}, worms: [], code: null, meta: null, score: null, rois: [], roisDirty: false, formDirty: false,
  view: "mip", z: 0, range: "enhanced", bf: false, zoom: 1, tab: "score", tool: "ellipse", label: null,
  mip: null, bfVals: null, planes: [], drawing: null, geom: null,
  angle: 0, rot: new Map(),   // 3D: the turn in degrees, and turned projections already drawn for this image
  next: null,   // the next image's projection and brightfield, decoded ahead: { code, mip, bfVals }
};

const cv = $("#cv");
const ctx = cv.getContext("2d");
const off = document.createElement("canvas");
const offCtx = off.getContext("2d", { willReadFrequently: true });

// ---------------------------------------------------------------- loading

const LOST = "lost connection to wormscore";

async function api(path, body) {
  // Every request names this window's project: a window left open from another project is
  // refused (409) instead of saving into the project now being scored on this port.
  const headers = st.projectId ? { "X-Wormscore-Project": st.projectId } : {};
  let res;
  try {
    res = await fetch(path, body === undefined ? { headers } : {
      method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
  } catch {
    setConnected(false);
    throw new Error(LOST);
  }
  if (res.status === 409) {
    otherProject();
    throw new Error("this window belongs to another project");
  }
  if (!res.ok) {
    const said = await res.json().then(d => d.error).catch(() => null);
    throw new Error(said || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

// ---------------------------------------------------------------- connection

// Connection to the wormscore program. The page checks in every 20 s; once a check fails it
// says "Reconnecting...", retries every 2 s and at once when the computer wakes or the window
// gets focus. If the program stays away it says so, with a button to start it. Unsaved answers
// stay in the form the whole time (and in the draft the app already has).
const GONE_AFTER_MS = 8000;

function setConnected(ok) {
  if (st.otherProject) return;
  const bar = $("#conn");
  if (!ok) {
    st.downSince ??= Date.now();
    const long = Date.now() - st.downSince > GONE_AFTER_MS;
    $("#connText").textContent = long
      ? "wormscore is not running. Your answers stay in this window. " +
        (st.cfg?.can_start ? "Click Start wormscore, then Open when your browser asks; this window reconnects by itself."
                            : "Open wormscore again; this window reconnects by itself.")
      : "Reconnecting to wormscore...";
    $("#connStart").classList.toggle("hidden", !(long && st.cfg?.can_start));
    bar.classList.remove("hidden");
    return;
  }
  const was = st.downSince;
  st.downSince = null;
  bar.classList.add("hidden");
  if (!st.ready) start();
  if (was && st.formDirty) {
    saveDraft();
    saveMessage("Connected again. Click Save to save this image.");
  }
}

async function checkIn() {
  clearTimeout(st.checkTimer);
  let ok = true;
  try { await api("/api/ping"); } catch { ok = false; }
  if (ok) setConnected(true);
  if (!st.otherProject) st.checkTimer = setTimeout(checkIn, ok ? 20000 : 2000);
}

for (const ev of ["focus", "online"]) window.addEventListener(ev, checkIn);
document.addEventListener("visibilitychange", () => { if (!document.hidden) checkIn(); });

function otherProject() {
  st.otherProject = true;
  $("#connStart").classList.add("hidden");
  $("#connText").textContent = "This scoring window belongs to another project. Close it, then click Continue scoring in the wormscore window.";
  $("#conn").classList.remove("hidden");
}

// ---------------------------------------------------------------- drafts

// Unsaved answers are kept by the app (scoring_drafts/ in the project) a moment after each
// change, so a crash or restart loses nothing. A saved score replaces its draft.
function formValues() {
  const out = {};
  for (const [k, v] of new FormData($("#scoreForm")).entries()) out[k] = String(v);
  return out;
}

function saveDraft() {
  clearTimeout(st.draftTimer);
  if (!st.code || !st.formDirty) return;
  const code = st.code, values = formValues();
  api(`/api/worm/${code}/draft`, { values }).catch(() => {});
}

function applyDraft(draft) {
  const f = $("#scoreForm");
  for (const el of f.elements) {
    if (!el.name || !(el.name in draft.values)) continue;
    if (el.type === "radio" || el.type === "checkbox") el.checked = draft.values[el.name] === el.value;
    else el.value = draft.values[el.name];
  }
  st.formDirty = true;
  syncUsable();
  saveMessage(`Restored answers you had not saved (${draft.at}). Check them, then click Save.`, true);
}

// Decode a PNG into its values. Image planes hold exact 16-bit values packed as
// red = high byte, green = low byte; brightfield is plain 8-bit gray (packed = false).
function loadValues(url, w, h, packed = true) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = w; c.height = h;
      const cx = c.getContext("2d", { willReadFrequently: true });
      cx.drawImage(img, 0, 0);
      const d = cx.getImageData(0, 0, w, h).data;
      const v = new Uint16Array(w * h);
      for (let i = 0; i < v.length; i++) v[i] = packed ? d[i * 4] * 256 + d[i * 4 + 1] : d[i * 4];
      resolve(v);
    };
    img.onerror = reject;
    img.src = url;
  });
}

function radios(name, options) {
  return options.map(o => h("label", {}, h("input", { type: "radio", name, value: o }), ` ${o}`));
}

function select(name, placeholder, options) {
  return h("select", { name }, h("option", { value: "" }, placeholder), options.map(o => h("option", {}, o)));
}

function buildForm() {
  const sc = st.cfg.scoring;
  document.title = `Scoring: ${st.cfg.project_name} - wormscore`;   // the app finds its scoring windows by "Scoring"
  $("#projectName").textContent = st.cfg.project_name;
  $("#rules").replaceChildren(...sc.rules.map(r => h("li", {}, r)));
  $("#reasonSelect").append(...sc.unusable_reasons.map(r => h("option", {}, r)));
  $("#countLabel").textContent = sc.count_question.label;
  $("#countOptions").replaceChildren(...radios("cells_seen", sc.count_question.options));

  const neurons = sc.neurons.map((name, i) => {
    const n = i + 1;
    st.colors[name] = PALETTE[i % PALETTE.length];
    return h("fieldset", { class: `neuron n${n}` },
      h("legend", {}, name),
      h("div", { class: "calls" }, radios(`n${n}_call`, sc.calls)),
      sc.sides.length ? h("select", { name: `n${n}_side` }, h("option", { value: "unknown" }, "side unknown"),
        sc.sides.map(s => h("option", {}, s))) : null,
      sc.neuron_questions.map(q => select(`n${n}_${q.id}`, q.label, q.options)));
  });
  $("#neurons").replaceChildren(...neurons);

  st.colors.background = BG_COLOR;
  const labels = [...sc.neurons, "background"];
  st.label = labels[0];
  $("#labelSeg").replaceChildren(...labels.map((l, i) =>
    h("button", { type: "button", "data-label": l, class: `${i === 0 ? "on " : ""}${l === "background" ? "lbg" : `l${i + 1}`}`,
      "aria-pressed": String(i === 0),
      onclick: e => { st.label = l; $$("#labelSeg button").forEach(x => setOn(x, x === e.currentTarget)); } },
    l === "background" ? "Background" : l)));
}

async function loadList() {
  st.worms = (await api("/api/worms")).worms;
  renderList();
}

function renderList() {
  const nav = $("#list");
  nav.replaceChildren(...st.worms.map(w => {
    // A saved score is what counts, so it gets the tick. Marked cells are said in the label and
    // counted in the header; an image with no visible cells has nothing to mark.
    const said = w.scored ? (w.has_roi ? "scored, cells marked" : "scored") : w.draft ? "started, not saved" : "not scored";
    return h("button", { class: w.code === st.code ? "cur" : "", "aria-current": w.code === st.code ? "true" : null,
      "aria-label": `${w.code}, ${said}` + (w.unanswered_flags ? `, ${w.unanswered_flags} questions to confirm` : ""),
      title: said, onclick: () => openWorm(w.code) },
      h("span", { class: w.scored ? "dot done" : w.draft ? "dot draft" : "dot", "aria-hidden": "true" }), w.code,
      w.unanswered_flags ? h("span", { class: "flagmark", title: "questions to confirm" }, `${w.unanswered_flags}?`) : null);
  }));
  const n = st.worms.filter(w => w.scored).length;
  const marked = st.worms.filter(w => w.scored && w.has_roi).length;
  $("#progFill").style.transform = `scaleX(${n / Math.max(st.worms.length, 1)})`;
  $("#progText").textContent = `${n} / ${st.worms.length} scored, ${marked} with cells marked` +
    (n < st.cfg.min_scored ? ` (at least ${st.cfg.min_scored} before unblinding)` : "");
}

// Asks before unsaved outlines are left behind, in the app's own dialog. Stay has the focus, so one
// Enter press never throws outlines away; Escape stays too. Resolves true to leave.
function confirmLeave() {
  const m = $("#leaveDlg"), stay = $("#leaveStay"), go = $("#leaveGo"), opener = document.activeElement;
  return new Promise(resolve => {
    const done = leave => {
      m.classList.add("hidden");
      st.dialogOpen = false;
      m.onkeydown = stay.onclick = go.onclick = null;
      if (!leave) opener?.focus?.();
      resolve(leave);
    };
    m.onkeydown = e => {
      if (e.key === "Escape") { e.preventDefault(); done(false); }
      else if (e.key === "Tab") { e.preventDefault(); (document.activeElement === stay ? go : stay).focus(); }
    };
    stay.onclick = () => done(false);
    go.onclick = () => done(true);
    st.dialogOpen = true;
    m.classList.remove("hidden");
    stay.focus();
  });
}

async function openWorm(code) {
  // Outlines are only kept once saved, so leaving them asks first. Unsaved answers stay as a
  // draft (never a score) and come back when this image is opened again.
  if (st.roisDirty && !(await confirmLeave())) return;
  if (st.formDirty) {
    saveDraft();
    const w = st.worms.find(x => x.code === st.code);
    if (w) w.draft = true;
  }
  st.formDirty = st.roisDirty = false;
  // Clicking codes quickly starts several loads; only the latest may fill the page.
  const seq = (st.openSeq = (st.openSeq || 0) + 1);
  // Saving waits until this image is on screen, so a held Ctrl+Enter can never save a score
  // for an image the scorer has not seen.
  st.loading = true;
  let r, pixels;
  try {
    r = await api(`/api/worm/${code}`);
  } catch (err) {
    if (seq === st.openSeq) loadFailed(code, err);
    return;
  }
  if (seq !== st.openSeq) return;
  Object.assign(st, {
    code, meta: r.meta, imgV: r.img_v, score: r.score, rois: r.rois || [], roisDirty: false, formDirty: false,
    view: "mip", z: Math.floor(r.meta.n_z / 2), planes: new Array(r.meta.n_z), planeFailed: new Set(),
    drawing: null, mip: null, bfVals: null, angle: 0, rot: new Map(), planesDone: false, rendering: null, vol: null,
  });
  $("#aSlider").value = 0;
  // The form shows this image's answers at once, never the last image's under this code.
  $("#loadErr").classList.add("hidden");
  $("#bfToggle").disabled = !st.meta.has_brightfield;
  $("#bfToggle").parentElement.title = st.meta.has_brightfield ? "" : "No brightfield channel was recorded for this image";
  $("#codeTitle").textContent = code;
  cv.setAttribute("aria-label", `Image ${code}`);
  $("#zSlider").max = st.meta.n_z - 1;
  syncButtons();
  fillForm();
  renderFlags();
  markMissing([]);       // outlines from pressing Save on the last image do not carry over
  if (r.draft) applyDraft(r.draft);
  renderSuggestions();
  renderRoiList();
  $("#roiMsg").textContent = "";
  setTab(st.score ? st.tab : "score");
  renderList();
  try {
    const ahead = st.next && st.next.code === code ? await st.next.ready : null;
    st.next = null;
    pixels = ahead && ahead.v === r.img_v ? ahead.pixels : await loadImages(code, st.meta, r.img_v);
  } catch (err) {
    if (seq === st.openSeq) loadFailed(code, err);
    return;
  }
  if (seq !== st.openSeq) return;
  [st.mip, st.bfVals] = pixels;
  st.loading = false;
  draw();
  loadNext(code);
  preloadPlanes(code);
}

// An image that cannot be loaded says so and offers to try again; saving stays locked.
function loadFailed(code, err) {
  st.failedCode = code;
  $("#loadErrText").textContent = `Could not load ${code}: ${err?.message || "the image file could not be read"}.`;
  $("#loadErr").classList.remove("hidden");
  draw();
}
$("#loadErrRetry").onclick = () => st.failedCode && openWorm(st.failedCode);

// Image addresses carry the project and build (img_v): the browser keeps images for an hour, and
// every project scores on the same port, so a plain /img/W0001/... could show another image.
const imgUrl = (code, file, v) => `/img/${code}/${file}?v=${encodeURIComponent(v)}`;

function loadImages(code, meta, v) {
  const { width: w, height: hgt } = meta;
  return Promise.all([
    loadValues(imgUrl(code, "mip.png", v), w, hgt),
    meta.has_brightfield ? loadValues(imgUrl(code, "brightfield.png", v), w, hgt, false) : null,
  ]);
}

// Decode the next image in the list while this one is being scored, so moving on is instant.
// Only pixels are kept ahead; its answers and regions are always fetched fresh when it opens.
function loadNext(code) {
  const i = st.worms.findIndex(w => w.code === code);
  const next = st.worms[i + 1];
  if (!next) return;
  const ready = api(`/api/worm/${next.code}`)
    .then(r => loadImages(next.code, r.meta, r.img_v).then(pixels => ({ v: r.img_v, pixels })))
    .catch(() => null);
  st.next = { code: next.code, ready };
}

async function loadPlane(code, z) {
  const { width: w, height: hgt } = st.meta;
  try {
    const vals = await loadValues(imgUrl(code, `planes/z${String(z).padStart(2, "0")}.png`, st.imgV), w, hgt);
    if (st.code !== code) return;
    st.planes[z] = vals;
    st.planeFailed.delete(z);
  } catch {
    if (st.code === code) st.planeFailed.add(z);
  }
  if (st.code === code && ((st.view === "plane" && st.z === z) || st.view === "3d")) draw();
}

async function preloadPlanes(code) {
  for (let z = 0; z < st.meta.n_z; z++) {
    if (st.code !== code) return;
    if (!st.planes[z]) await loadPlane(code, z);
  }
  if (st.code !== code) return;
  st.planesDone = true;
  if (st.view === "3d") { pump3d(); draw(); }
}

// ---------------------------------------------------------------- 3D view
// The stack turned about the vertical axis, drawn here from the planes the page already has:
// each output pixel is the brightest value along a straight line through the turned stack, so
// at 0 degrees it is the normal maximum projection. The z axis is stretched by z step / pixel
// size so depth looks true. Display only: nothing is measured or saved from it, and it uses the
// same fixed display range as every other view.

const TURN_STEP = 5, TURN_MAX = 60;

function why3dOff() {
  const m = st.meta;
  if (!m) return "No image";
  if (m.n_z < 2) return "This image has one plane only";
  if (!(m.z_step_um > 0) || !(m.pixel_um > 0)) return "3D needs a blind set built with wormscore 0.14.0 or later";
  return "";
}

// The turned projection for this angle, or null while it is still being drawn.
function turned(a) {
  return a === 0 ? st.mip : st.rot.get(a) || null;
}

// The angles worth keeping for this image: the nearest ones to the current turn, as many as fit
// in about 80 MB (all of them for a 512 x 512 image, fewer for very large ones).
function wantedAngles() {
  const keep = Math.max(4, Math.floor(40e6 / (st.meta.width * st.meta.height)));
  const all = [];
  for (let a = -TURN_MAX; a <= TURN_MAX; a += TURN_STEP) if (a !== 0) all.push(a);
  all.sort((p, q) => Math.abs(p - st.angle) - Math.abs(q - st.angle) || p - q);
  return all.slice(0, keep);
}

// Draws the current angle first, then the nearby ones while the page is idle.
async function pump3d() {
  if (st.rendering || st.view !== "3d" || !st.planesDone || st.planeFailed.size || why3dOff()) return;
  const want = wantedAngles();
  for (const a of [...st.rot.keys()]) if (!want.includes(a)) st.rot.delete(a);
  const a = st.angle !== 0 && !st.rot.has(st.angle) ? st.angle : want.find(x => !st.rot.has(x));
  if (a === undefined) return;
  // The open-image counter, not the code: opening A, then B, then A again starts afresh.
  const seq = st.openSeq;
  st.rendering = a;
  let out = null, failed = false;
  try {
    out = await render3d(seq, a);
  } catch (err) {
    failed = true;          // the nearest drawn angle stays on screen; the page never locks up
    console.warn("3D", err);
  } finally {
    if (st.openSeq === seq) st.rendering = null;
  }
  if (st.openSeq !== seq) return;
  if (out) st.rot.set(a, out);
  if (a === st.angle) draw();
  if (!failed) setTimeout(pump3d, 0);
}

// The planes 3D turns. Very large images (over 1.5 million pixels, such as 2000 x 2000) are turned
// at half size, each pixel the brightest of 2 x 2, so a cell never fades and a turn stays quick.
async function volume3d(seq) {
  if (st.vol) return st.vol;
  const m = st.meta;
  if (m.width * m.height <= 1.5e6) return (st.vol = { r: 1, W: m.width, H: m.height, P: st.planes });
  const W = Math.ceil(m.width / 2), H = Math.ceil(m.height / 2), P = [];
  for (const p of st.planes) {
    const q = new Uint16Array(W * H);
    for (let y = 0; y < m.height; y++) {
      const row = y * m.width, qrow = (y >> 1) * W;
      for (let x = 0; x < m.width; x++) {
        const v = p[row + x], j = qrow + (x >> 1);
        if (v > q[j]) q[j] = v;
      }
    }
    P.push(q);
    await new Promise(r => setTimeout(r, 0));
    if (st.openSeq !== seq) return null;
  }
  return (st.vol = { r: 2, W, H, P });
}

async function render3d(seq, a) {
  const vol = await volume3d(seq);
  if (!vol) return null;
  const m = st.meta, { W, H, P, r } = vol, nz = m.n_z;
  const zs = m.z_step_um / (m.pixel_um * r);
  const rad = (a * Math.PI) / 180, c = Math.cos(rad), s = Math.sin(rad);
  // Between two planes, extra planes are blended in so a thin process does not break into
  // dots when the stack is turned (at most 6, so very large images stay quick).
  const sub = Math.max(1, Math.min(6, Math.ceil(zs * Math.abs(s))));
  const cx = (W - 1) / 2, cz = ((nz - 1) * zs) / 2;
  const out = new Uint16Array(W * H);
  let t0 = performance.now();
  for (let y = 0; y < H; y++) {
    const row = y * W;
    for (let z = 0; z < nz; z++) {
      const p0 = P[z], p1 = P[Math.min(z + 1, nz - 1)];
      const steps = z === nz - 1 ? 1 : sub;
      for (let k = 0; k < steps; k++) {
        const f = k / steps, g = 1 - f;
        // output column u = x cos a + b for this (blended) plane
        const b = cx - cx * c + ((z + f) * zs - cz) * s + 0.5;
        const x0 = Math.max(0, Math.ceil(-b / c)), x1 = Math.min(W - 1, Math.floor((W - b) / c - 1e-9));
        for (let x = x0; x <= x1; x++) {
          const i = row + x;
          const v = f ? p0[i] * g + p1[i] * f : p0[i];
          const o = row + ((x * c + b) | 0);
          if (v > out[o]) out[o] = v;
        }
      }
    }
    // Hand the page back now and then, so clicks and keys never wait for a large image.
    if (performance.now() - t0 > 25) {
      await new Promise(r => setTimeout(r, 0));
      if (st.openSeq !== seq || !wantedAngles().includes(a)) return null;
      t0 = performance.now();
    }
  }
  if (r === 1) return out;
  const full = new Uint16Array(m.width * m.height);       // back to the image's own size
  for (let y = 0; y < m.height; y++) {
    const row = y * m.width, srow = (y >> 1) * W;
    for (let x = 0; x < m.width; x++) full[row + x] = out[srow + (x >> 1)];
  }
  return full;
}

function setAngle(a) {
  if (!st.meta || why3dOff()) return;
  a = Math.max(-TURN_MAX, Math.min(TURN_MAX, Math.round(a / TURN_STEP) * TURN_STEP));
  const changed = a !== st.angle || st.view !== "3d";
  st.angle = a;
  st.view = "3d";
  if (!changed) return;
  syncButtons();
  draw();
  pump3d();
}

// A plane that failed to load is tried again when 3D is opened.
async function retryFailedPlanes() {
  const code = st.code;
  for (const z of [...st.planeFailed]) {
    if (st.code !== code) return;
    await loadPlane(code, z);
  }
  if (st.code === code && st.view === "3d") { pump3d(); draw(); }
}

// ---------------------------------------------------------------- drawing

function geometry() {
  const m = st.meta;
  const s = (m.pixel_um / m.display_um_per_px) * st.zoom;
  // The frame is frame_px wide and shaped like the window, the same for every image.
  const wrap = $("#canvasWrap");
  const frameH = Math.round((m.frame_px * wrap.offsetHeight) / Math.max(wrap.offsetWidth, 1)) || m.frame_px;
  const fw = Math.max(m.frame_px * st.zoom, Math.ceil(m.width * s));
  const fh = Math.max(frameH * st.zoom, Math.ceil(m.height * s));
  return { s, fw, fh, ox: (fw - m.width * s) / 2, oy: (fh - m.height * s) / 2 };
}

function draw() {
  if (!st.meta) return;
  const m = st.meta;
  const in3d = st.view === "3d";
  let vals = st.view === "mip" ? st.mip : in3d ? turned(st.angle) : st.planes[st.z];
  // In 3D, until this angle is drawn, the nearest angle already drawn stays on screen, labelled.
  let status3d = "";
  if (in3d && !vals && st.mip) {
    const near = [0, ...st.rot.keys()].sort((p, q) => Math.abs(p - st.angle) - Math.abs(q - st.angle))[0];
    vals = turned(near);
    const have = st.planes.filter(Boolean).length;
    status3d = st.planesDone && st.planeFailed.size ? "3D is not available for this image: a plane could not be loaded. Choose 3D again to retry."
      : have < m.n_z ? `Loading planes for 3D... ${have} / ${m.n_z}`
      : `Turning to ${st.angle}°... (showing ${near}°)`;
  }
  const g = (st.geom = geometry());
  cv.width = g.fw;
  cv.height = g.fh;
  // At 1x the whole frame fills the window; 2x and 3x are that size doubled or tripled and scroll.
  // The frame is the same for every image, so every image is shown at the same scale.
  const wrap = $("#canvasWrap");
  const fit = Math.min((wrap.offsetWidth * st.zoom) / g.fw, (wrap.offsetHeight * st.zoom) / g.fh) * 0.995;
  cv.style.width = `${g.fw * fit}px`;
  cv.style.height = `${g.fh * fit}px`;
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, g.fw, g.fh);
  if (!vals) {
    if (!$("#loadErr").classList.contains("hidden")) return;     // the error box says it instead
    ctx.fillStyle = MUTED;
    ctx.font = `${14 * st.zoom}px system-ui`;
    const failed = st.view === "plane" && st.planeFailed?.has(st.z);
    ctx.fillText(failed ? "Could not load this plane. Move to another plane and back to try again." : "Loading...", 16, 28);
    return;
  }
  // Fixed display range: the same numbers for every image from this microscope.
  const [lo, hi] = st.range === "full" ? m.full_range : m.enhanced_range;
  const showBf = st.bf && st.bfVals && !in3d;     // brightfield is one flat image; it cannot turn
  // The image itself is recomputed only when what is shown changes, not while a region is
  // being drawn (a large image has over a million pixels).
  const drawn = st.drawn;
  if (!drawn || drawn.vals !== vals || drawn.bf !== showBf || drawn.lo !== lo || drawn.hi !== hi) {
    off.width = m.width; off.height = m.height;
    const im = offCtx.createImageData(m.width, m.height);
    const d = im.data;
    for (let i = 0; i < vals.length; i++) {
      const gv = Math.min(255, Math.max(0, ((vals[i] - lo) / (hi - lo)) * 255));
      const base = showBf ? st.bfVals[i] * 0.5 : 0;
      d[i * 4] = base;
      d[i * 4 + 1] = Math.min(255, base + gv);
      d[i * 4 + 2] = base;
      d[i * 4 + 3] = 255;
    }
    offCtx.putImageData(im, 0, 0);
    st.drawn = { vals, bf: showBf, lo, hi };
  }
  ctx.imageSmoothingEnabled = st.zoom === 1;
  ctx.drawImage(off, g.ox, g.oy, m.width * g.s, m.height * g.s);

  // Scale bar at the image's lower left corner, so it stays next to the worm.
  const len = (10 / m.display_um_per_px) * st.zoom;
  const bx = g.ox + 10 * st.zoom, by = g.oy + m.height * g.s - 10 * st.zoom;
  ctx.fillStyle = "#fff";
  ctx.shadowColor = "#000";
  ctx.shadowBlur = 3 * st.zoom;
  ctx.fillRect(bx, by, len, 3 * st.zoom);
  ctx.font = `${14 * st.zoom}px system-ui`;
  ctx.fillText("10 µm", bx, by - 6 * st.zoom);
  if (status3d) {
    const wrap = $("#canvasWrap");
    const sx = cv.width / (parseFloat(cv.style.width) || cv.width);    // canvas pixels per screen pixel
    ctx.font = `${14 * sx}px system-ui`;
    ctx.fillText(status3d, (wrap.scrollLeft + 16) * sx, (wrap.scrollTop + 26) * sx);
  }
  ctx.shadowBlur = 0;

  if (st.tab === "mark" && !in3d) {
    if ($("#sugToggle").checked) for (const c of m.candidates) drawCandidate(c);
    for (const r of st.rois) drawRoi(r);
    if (st.drawing) drawRoi(st.drawing, true);
  }
}

const toScreen = (x, y) => [st.geom.ox + x * st.geom.s, st.geom.oy + y * st.geom.s];

function drawCandidate(c) {
  const [x, y] = toScreen(c.x, c.y);
  ctx.save();
  ctx.setLineDash([4, 4]);
  ctx.strokeStyle = SUGGEST;
  ctx.lineWidth = 1.5 * st.zoom;
  ctx.beginPath();
  ctx.arc(x, y, Math.max(c.r * st.geom.s + 4, 8), 0, Math.PI * 2);
  ctx.stroke();
  ctx.fillStyle = SUGGEST;
  ctx.font = `bold ${13 * st.zoom}px system-ui`;
  ctx.fillText(`S${c.rank}`, x + c.r * st.geom.s + 6, y - 4);
  ctx.restore();
}

function drawRoi(r, live = false) {
  ctx.save();
  ctx.strokeStyle = st.colors[r.label] || "#fff";
  ctx.lineWidth = 2 * st.zoom;
  if (live) ctx.setLineDash([3, 3]);
  ctx.beginPath();
  let lx, ly;
  if (r.type === "ellipse") {
    const [x, y] = toScreen(r.cx, r.cy);
    ctx.ellipse(x, y, Math.max(r.rx * st.geom.s, 1), Math.max(r.ry * st.geom.s, 1), 0, 0, Math.PI * 2);
    [lx, ly] = [x + r.rx * st.geom.s + 4, y - r.ry * st.geom.s];
  } else if (r.type === "candidate") {
    const c = st.meta.candidates.find(c => c.rank === r.rank);
    const [x, y] = toScreen(c.x, c.y);
    ctx.arc(x, y, Math.max(c.r * st.geom.s, 3), 0, Math.PI * 2);
    [lx, ly] = [x + c.r * st.geom.s + 4, y - c.r * st.geom.s];
  } else {
    r.points.forEach(([px, py], i) => {
      const [x, y] = toScreen(px, py);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    if (!live) ctx.closePath();
    [lx, ly] = toScreen(...r.points[0]);
  }
  ctx.stroke();
  ctx.fillStyle = st.colors[r.label] || "#fff";
  ctx.font = `bold ${13 * st.zoom}px system-ui`;
  ctx.fillText(r.label, lx, ly);
  ctx.restore();
}

// ---------------------------------------------------------------- region tools

function toRaw(e) {
  const rect = cv.getBoundingClientRect();
  const x = ((e.clientX - rect.left) * cv.width) / rect.width;
  const y = ((e.clientY - rect.top) * cv.height) / rect.height;
  return [(x - st.geom.ox) / st.geom.s, (y - st.geom.oy) / st.geom.s];
}

function roiMessage(text, err = false) {
  $("#roiMsg").textContent = text;
  $("#roiMsg").className = err ? "err" : "";
}

function commitRoi(r) {
  const [x, y] = r.type === "ellipse" ? [r.cx, r.cy] : r.type === "polygon" ? r.points[0] : [0, 0];
  st.drawing = null;
  if (x < 0 || y < 0 || x >= st.meta.width || y >= st.meta.height) {
    roiMessage("That region is outside the image. Draw it on the animal.", true);
    draw();
    return;
  }
  st.rois = st.rois.filter(o => o.label !== r.label).concat([r]);
  st.roisDirty = true;
  roiMessage("Unsaved changes");
  renderRoiList();
  draw();
}

// In 3D, dragging left or right turns the stack (the full width of the view is 120 degrees).
cv.addEventListener("pointerdown", e => {
  if (st.view !== "3d" || e.button !== 0) return;
  st.turnDrag = { x: e.clientX, a: st.angle, w: cv.getBoundingClientRect().width || 1 };
  cv.setPointerCapture(e.pointerId);
  cv.style.cursor = "grabbing";
  e.preventDefault();
});
cv.addEventListener("pointermove", e => {
  if (!st.turnDrag) return;
  const d = st.turnDrag;
  setAngle(d.a + ((e.clientX - d.x) / d.w) * 2 * TURN_MAX);
});
const endTurnDrag = () => { if (st.turnDrag) { st.turnDrag = null; syncCursor(); } };
cv.addEventListener("pointerup", endTurnDrag);
cv.addEventListener("pointercancel", endTurnDrag);

cv.addEventListener("mousedown", e => {
  if (st.tab !== "mark" || st.tool !== "ellipse" || st.view === "3d") return;
  const [x, y] = toRaw(e);
  st.drawing = { type: "ellipse", label: st.label, cx: x, cy: y, rx: 0, ry: 0, source: "drawn" };
});
cv.addEventListener("mousemove", e => {
  if (!st.drawing || st.drawing.type !== "ellipse") return;
  const [x, y] = toRaw(e);
  st.drawing.rx = st.drawing.ry = Math.hypot(x - st.drawing.cx, y - st.drawing.cy);
  draw();
});
cv.addEventListener("mouseup", () => {
  if (!st.drawing || st.drawing.type !== "ellipse") return;
  if (st.drawing.rx > 1.5) commitRoi(st.drawing);
  else st.drawing = null;
  draw();
});
cv.addEventListener("click", e => {
  if (st.tab !== "mark" || st.tool !== "polygon" || st.view === "3d") return;
  if (!st.drawing) st.drawing = { type: "polygon", label: st.label, points: [], source: "drawn" };
  st.drawing.points.push(toRaw(e));
  draw();
});
cv.addEventListener("dblclick", () => {
  if (!st.drawing || st.drawing.type !== "polygon") return;
  const pts = st.drawing.points;
  // a double-click also fires two clicks; drop the duplicate point
  while (pts.length > 1 && Math.hypot(pts.at(-1)[0] - pts.at(-2)[0], pts.at(-1)[1] - pts.at(-2)[1]) < 1) pts.pop();
  if (pts.length >= 3) commitRoi(st.drawing);
  else st.drawing = null;
  draw();
});

function renderSuggestions() {
  const box = $("#sugList");
  if (!st.meta.candidates.length) {
    box.replaceChildren(h("p", { class: "small" }, "No suggested spots for this image."));
    return;
  }
  const neurons = st.cfg.scoring.neurons;
  box.replaceChildren(...st.meta.candidates.map(c =>
    h("div", { class: "item" },
      h("span", { class: "sw suggest" }), h("span", { class: "nowrap" }, `Spot S${c.rank}`),
      h("span", { class: "btns" }, h("span", { class: "use", "aria-hidden": "true" }, "use as"), neurons.map(n =>
        h("button", { type: "button", title: `Use S${c.rank} as ${n}`, "aria-label": `Use S${c.rank} as ${n}`,
          onclick: () => commitRoi({ type: "candidate", rank: c.rank, label: n, source: `suggestion S${c.rank}` }) },
        n))))));
}

function renderRoiList() {
  $("#roiList").replaceChildren(...st.rois.map(r =>
    h("div", { class: "item" },
      h("span", { class: "sw", style: `background:${st.colors[r.label] || "#fff"}` }), r.label,
      h("span", { class: "small" }, r.source),
      h("span", { class: "btns" }, h("button", { type: "button", onclick: () => {
        st.rois = st.rois.filter(x => x !== r);
        st.roisDirty = true;
        roiMessage("Unsaved changes");
        renderRoiList();
        draw();
      } }, "Remove")))));
}

$("#saveRois").onclick = async () => {
  try {
    await api(`/api/worm/${st.code}/rois`, { rois: st.rois });
    st.roisDirty = false;
    roiMessage(`Saved ${st.rois.length} region${st.rois.length === 1 ? "" : "s"}`);
    await loadList();
  } catch (err) {
    roiMessage(`Not saved: ${err.message}`, true);
  }
};

// ---------------------------------------------------------------- score form

function renderFlags() {
  const answers = (st.score && st.score.flag_answers) || {};
  $("#flags").replaceChildren(...st.meta.flags.map(f => {
    const q = FLAG_QUESTIONS[f.id] || { q: "Before saving:", options: ["noted"] };
    return h("div", { class: "flag" }, h("p", {}, f.text), h("div", { class: "q" }, q.q),
      q.options.map(o => h("label", {},
        h("input", { type: "radio", name: `flag_${f.id}`, value: o, checked: answers[f.id] === o }), ` ${ANSWER_TEXT[o] || o}`)));
  }));
}

function saveMessage(text, err = false) {
  $("#saveMsg").textContent = text;
  $("#saveMsg").className = err ? "err" : "";
}

function fillForm() {
  const f = $("#scoreForm");
  f.reset();
  const s = st.score || {};
  for (const el of f.elements) {
    if (!el.name || el.name.startsWith("flag_") || !(el.name in s)) continue;
    if (el.type === "radio") el.checked = s[el.name] === el.value;
    else el.value = s[el.name];
  }
  saveMessage(st.score ? `Saved ${st.score.saved}` : "");
  markAvailable(!!st.score);
  syncUsable();
}

// The reason only applies to an unusable image; the cell questions only to a usable one.
function syncUsable() {
  const usable = $("#scoreForm").querySelector('input[name="usable"]:checked')?.value;
  $("#reasonSelect").classList.toggle("hidden", usable !== "no");
  if (usable !== "no") $("#reasonSelect").value = "";
  $("#countField").classList.toggle("hidden", usable === "no");
  $("#neurons").classList.toggle("hidden", usable === "no");
}

// Sections still missing an answer when Save was pressed are outlined until they are answered.
function markMissing(places) {
  $$("#scoreForm .missing").forEach(el => { el.classList.remove("missing"); el.removeAttribute("aria-invalid"); });
  for (const el of places) { el.classList.add("missing"); el.setAttribute("aria-invalid", "true"); }
}

$("#scoreForm").addEventListener("input", e => {
  const sec = e.target.closest(".missing");
  if (sec) { sec.classList.remove("missing"); sec.removeAttribute("aria-invalid"); }
  st.formDirty = true;
  st.formRev = (st.formRev || 0) + 1;
  saveMessage(st.connected === false ? `Unsaved changes (${LOST}; they stay in this window)` : "Unsaved changes");
  if (e.target.name === "usable") syncUsable();
  clearTimeout(st.draftTimer);
  st.draftTimer = setTimeout(saveDraft, 600);
});

$("#scoreForm").addEventListener("submit", async e => {
  e.preventDefault();
  // One save at a time, and only for an image that is on screen.
  if (st.saving || st.loading) {
    if (st.loading) saveMessage("Wait for the image to load, then save.", true);
    st.saveAndNext = false;
    return;
  }
  const fd = new FormData(e.target);
  const v = Object.fromEntries([...fd.entries()].filter(([k]) => !k.startsWith("flag_")));
  if (v.usable === "no") {
    // answers about cells do not apply to an unusable image, so they are not saved
    for (const k of Object.keys(v)) if (k === "cells_seen" || /^n\d+_/.test(k)) delete v[k];
  }
  const flag_answers = {};
  for (const f of st.meta.flags) {
    const a = fd.get(`flag_${f.id}`);
    if (a) flag_answers[f.id] = a;
  }
  const n = st.cfg.scoring.neurons.length;
  const f = e.target;
  const box = name => f.querySelector(`[name="${name}"]`)?.closest("fieldset, .flag");
  const problems = [], places = [];
  const need = (text, ...where) => { problems.push(text); places.push(...where.filter(Boolean)); };
  const unanswered = st.meta.flags.filter(fl => !flag_answers[fl.id]);
  if (unanswered.length) need("answer the questions at the top", ...unanswered.map(fl => box(`flag_${fl.id}`)));
  if (!v.usable) need("say whether the image is usable", box("usable"));
  if (v.usable === "no" && !v.unusable_reason) need("pick a reason it is not usable", box("usable"));
  if (v.usable === "yes") {
    if (!v.cells_seen) need("count the cell bodies", box("cells_seen"));
    const open = [...Array(n)].map((_, i) => i + 1).filter(i => !v[`n${i}_call`]);
    if (open.length) need(`score all ${n} neurons`, ...open.map(i => box(`n${i}_call`)));
    if (!v.confidence) need("pick a confidence", box("confidence"));
  }
  markMissing(places);
  if (problems.length) {
    st.saveAndNext = false;
    saveMessage(`Before saving: ${problems.join("; ")}.`, true);
    places[0]?.querySelector("input, select")?.focus();
    return;
  }
  const count = "repeat" in flag_answers ? (flag_answers.repeat === "count" ? "yes" : "no")
    : (st.meta.counted_by_default ? "yes" : "no");
  const body = { ...v, flag_answers, count_this_image: count, display_preset_when_saved: st.range };
  const rev = st.formRev;
  st.saving = true;
  try {
    const r = await api(`/api/worm/${st.code}/score`, body);
    st.saving = false;
    st.score = { ...body, saved: r.saved };
    if (st.formRev === rev) {
      clearTimeout(st.draftTimer);
      st.formDirty = false;
      saveMessage(`Saved ${r.saved}`);
    } else {
      // an answer was changed while saving: that change is not in the saved score yet
      st.saveAndNext = false;
      saveMessage(`Saved ${r.saved}, but you changed an answer while it saved. Click Save again.`, true);
    }
    markAvailable(true);
    await loadList();
    if (st.saveAndNext) {
      st.saveAndNext = false;
      const i = st.worms.findIndex(w => w.code === st.code);
      const next = [...st.worms.slice(i + 1), ...st.worms.slice(0, i)].find(w => !w.scored);
      if (next) return openWorm(next.code);
      return saveMessage(`Saved ${r.saved}. Every image is scored.`);
    }
    if (v.usable === "yes" && !st.formDirty) setTab("mark");    // nothing to outline on an unusable image
  } catch (err) {
    st.saving = false;
    st.saveAndNext = false;
    saveMessage(err.message === LOST
      ? "Not saved: lost connection to wormscore. Your answers stay here; open wormscore again and click Save once this window reconnects."
      : `Not saved: ${err.message}`, true);
  }
});

// ---------------------------------------------------------------- controls

function setTab(t) {
  st.tab = t;
  $$(".tabs button").forEach(b => {
    b.classList.toggle("on", b.dataset.tab === t);
    b.setAttribute("aria-selected", String(b.dataset.tab === t));
  });
  $$(".tab").forEach(el => el.classList.toggle("hidden", el.dataset.tab !== t));
  st.drawing = null;
  syncCursor();
  draw();
}

function syncCursor() {
  cv.style.touchAction = st.view === "3d" ? "none" : "";    // a finger drag turns the stack instead of scrolling
  cv.style.cursor = st.view === "3d" ? "grab" : st.tab === "mark" ? "crosshair" : "default";
}

// Toggle buttons show their state with a class and tell screen readers with aria-pressed.
function setOn(b, on) {
  b.classList.toggle("on", on);
  b.setAttribute("aria-pressed", String(on));
}

function syncButtons() {
  $$("[data-view]").forEach(b => setOn(b, b.dataset.view === st.view));
  $$("[data-range]").forEach(b => setOn(b, b.dataset.range === st.range));
  $$("[data-zoom]").forEach(b => setOn(b, +b.dataset.zoom === st.zoom));
  $("#zSlider").disabled = st.view !== "plane";
  $("#zSlider").value = st.z;
  $("#zText").textContent = st.meta ? (st.view === "plane" ? `${st.z + 1} / ${st.meta.n_z}` : `all ${st.meta.n_z}`) : "-";
  const in3d = st.view === "3d", off3d = st.meta ? why3dOff() : "No image";
  $("#view3d").disabled = !!off3d;
  $("#view3d").title = off3d || "Turn the stack to see depth (drag the image or use left/right)";
  $("#planeCtl").classList.toggle("off", in3d);
  $("#turnCtl").classList.toggle("off", !in3d);
  $("#aSlider").value = st.angle;
  $("#aText").textContent = `${st.angle}°`;
  $("#aSlider").setAttribute("aria-valuetext", `${st.angle} degrees`);
  if (st.meta) cv.setAttribute("aria-label", `Image ${st.code}, ${in3d ? `3D, turned ${st.angle} degrees` : st.view === "plane" ? `plane ${st.z + 1}` : "projection"}`);
  $$("#toolSeg button").forEach(b => (b.disabled = in3d));
  $("#mark3d").classList.toggle("hidden", !in3d);
  $("#bfToggle").checked = st.bf && !!st.meta && st.meta.has_brightfield;
  $("#bfToggle").disabled = !st.meta || !st.meta.has_brightfield || in3d;
  if (st.meta) $("#bfToggle").parentElement.title = !st.meta.has_brightfield
    ? "No brightfield channel was recorded for this image" : in3d ? "Brightfield is one flat image, so 3D does not show it" : "";
  syncCursor();
}

function setView(v) {
  if (v === "3d" && (!st.meta || why3dOff())) return;
  st.view = v;
  st.drawing = null;
  syncButtons();
  draw();
  if (v === "3d") {
    if (st.planesDone && st.planeFailed.size) retryFailedPlanes();
    else pump3d();
  }
}
function setZ(z) {
  if (!st.meta) return;
  st.z = Math.max(0, Math.min(st.meta.n_z - 1, z));
  st.view = "plane";
  if (st.planeFailed.has(st.z)) loadPlane(st.code, st.z);
  syncButtons();
  draw();
}

$$("[data-view]").forEach(b => (b.onclick = () => setView(b.dataset.view)));
$$("[data-range]").forEach(b => (b.onclick = () => { st.range = b.dataset.range; syncButtons(); draw(); }));
// Zooming keeps the point at the middle of the view in the middle.
$$("[data-zoom]").forEach(b => (b.onclick = () => {
  const wrap = $("#canvasWrap");
  const cx = (wrap.scrollLeft + wrap.clientWidth / 2) / Math.max(wrap.scrollWidth, 1);
  const cy = (wrap.scrollTop + wrap.clientHeight / 2) / Math.max(wrap.scrollHeight, 1);
  st.zoom = +b.dataset.zoom;
  syncButtons();
  draw();
  wrap.scrollLeft = cx * wrap.scrollWidth - wrap.clientWidth / 2;
  wrap.scrollTop = cy * wrap.scrollHeight - wrap.clientHeight / 2;
}));
$$(".tabs button").forEach(b => (b.onclick = () => {
  if (b.getAttribute("aria-disabled") === "true") return saveMessage("Save the score first, then mark cells.", true);
  setTab(b.dataset.tab);
}));

function markAvailable(on) {
  const b = $("#markTab");
  b.setAttribute("aria-disabled", String(!on));
  b.title = on ? "" : "Save the score first";
}
$$("#toolSeg button").forEach(b => (b.onclick = () => {
  st.tool = b.dataset.tool;
  st.drawing = null;
  $$("#toolSeg button").forEach(x => setOn(x, x === b));
  draw();
}));
$("#zSlider").oninput = e => setZ(+e.target.value);
$("#aSlider").oninput = e => setAngle(+e.target.value);
$("#bfToggle").onchange = e => { st.bf = e.target.checked; draw(); };
$("#sugToggle").onchange = draw;

document.addEventListener("keydown", e => {
  if (st.dialogOpen) return;     // the dialog handles its own keys; no shortcut acts behind it
  // Ctrl+Enter (Cmd+Enter on a Mac) saves the score and opens the next unscored image, from anywhere.
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && st.meta && st.tab === "score") {
    e.preventDefault();
    if (e.repeat) return;      // a held key repeats; one press saves once
    st.saveAndNext = true;
    $("#scoreForm").requestSubmit();
    return;
  }
  if (!st.meta || e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key.toLowerCase();
  const t = e.target;
  // Text boxes and lists keep every key. Radio buttons and check boxes keep the arrow keys
  // (they move between answers) but pass on the letter shortcuts.
  if (t.tagName === "TEXTAREA" || t.tagName === "SELECT") return;
  if (t.tagName === "INPUT" && t.type !== "range") {
    if (!["radio", "checkbox"].includes(t.type) || k.startsWith("arrow")) return;
  }
  if (st.view === "3d" && k.startsWith("arrow")) {
    // in 3D left/right turn the stack; up/down do nothing (they would leave 3D for Planes)
    if (k === "arrowright" || k === "arrowleft") setAngle(st.angle + (k === "arrowright" ? TURN_STEP : -TURN_STEP));
    e.preventDefault();
  } else if (k === "arrowright" || k === "arrowup") { setZ(st.view === "plane" ? st.z + 1 : st.z); e.preventDefault(); }
  else if (k === "arrowleft" || k === "arrowdown") { setZ(st.view === "plane" ? st.z - 1 : st.z); e.preventDefault(); }
  else if (k === "p") setView(st.view === "mip" ? "plane" : "mip");
  else if (k === "d") setView(st.view === "3d" ? "mip" : "3d");
  else if (k === "e") { st.range = st.range === "full" ? "enhanced" : "full"; syncButtons(); draw(); }
  else if (k === "b" && st.meta.has_brightfield) { st.bf = !st.bf; syncButtons(); draw(); }
  else if (k === "n" && !e.repeat) {
    const i = st.worms.findIndex(w => w.code === st.code);
    if (i + 1 < st.worms.length) openWorm(st.worms[i + 1].code);
  } else if (k === "escape") { st.drawing = null; draw(); }
});

// Resizing fires many events a second; draw at most once per frame.
window.addEventListener("resize", () => {
  if (st.resizeFrame) return;
  st.resizeFrame = requestAnimationFrame(() => { st.resizeFrame = 0; draw(); });
});
window.addEventListener("beforeunload", e => {
  if (st.formDirty || st.roisDirty) e.preventDefault();
});

// Start the page; if wormscore cannot be reached yet, try again when the connection is back.
async function start() {
  if (st.starting) return;
  st.starting = true;
  try {
    if (!st.cfg) {
      const cfg = await api("/api/config");
      st.projectId = cfg.project_id;
      st.cfg = cfg;
      buildForm();
    }
    await loadList();
    st.ready = true;
    const first = st.worms.find(w => !w.scored) || st.worms[0];
    if (first) openWorm(first.code);
    else $("#codeTitle").textContent = "No images";
  } catch (err) {
    if (err.message !== LOST) saveMessage(`Could not start: ${err.message}`, true);
    clearTimeout(st.checkTimer);
    st.checkTimer = setTimeout(checkIn, 2000);
  } finally {
    st.starting = false;
  }
}
start();

// Tells the desktop app this page is still open, and reconnects after a lost connection.
st.checkTimer = setTimeout(checkIn, 20000);
$("#connStart").onclick = () => { location.href = "wormscore://start"; };
