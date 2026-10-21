// wormscore scoring page. Talks only to the local server. Shows images and saves
// answers; it never receives brightness numbers.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

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

const PALETTE = ["#ff6b6b", "#5aa9ff", "#c58cff", "#ffb347"];
const BG_COLOR = "#b8bec7";
const FLAG_QUESTIONS = {
  repeat: { q: "Count this image for this animal?", options: ["count", "do not count"] },
  edge: { q: "Is the whole cell inside the stack?", options: ["yes", "no", "can't tell"] },
  saturation: { q: "Seen?", options: ["noted"] },
  streak: { q: "Seen?", options: ["noted"] },
};

const st = {
  cfg: null, colors: {}, worms: [], code: null, meta: null, score: null, rois: [], roisDirty: false, formDirty: false,
  view: "mip", z: 0, range: "enhanced", bf: false, zoom: 1, tab: "score", tool: "ellipse", label: null,
  mip: null, bfVals: null, planes: [], drawing: null, geom: null,
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
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

// ---------------------------------------------------------------- connection

// The page checks in every 20 s, and every 3 s once the connection is lost, so it reconnects
// by itself as soon as wormscore runs again (same address). Unsaved answers stay in the form.
function setConnected(ok) {
  const was = st.connected;
  st.connected = ok;
  $("#conn").classList.toggle("hidden", ok);
  if (ok && was === false && st.formDirty) {
    saveDraft();
    saveMessage("Connected again. Click Save to save this image.");
  }
}

async function checkIn() {
  let ok = true;
  try { await api("/api/ping"); } catch { ok = false; }
  if (ok) setConnected(true);
  if (!st.otherProject) setTimeout(checkIn, ok ? 20000 : 3000);
}

function otherProject() {
  st.otherProject = true;
  $("#conn").textContent = "This scoring window belongs to another project. Close it, then click Continue scoring in the wormscore window.";
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
      onclick: e => { st.label = l; $$("#labelSeg button").forEach(x => x.classList.toggle("on", x === e.currentTarget)); } },
    l === "background" ? "Background" : l)));
}

async function loadList() {
  st.worms = (await api("/api/worms")).worms;
  renderList();
}

function renderList() {
  const nav = $("#list");
  nav.replaceChildren(...st.worms.map(w => {
    const dot = w.scored && w.has_roi ? "done" : w.scored ? "scored" : "";
    return h("button", { class: w.code === st.code ? "cur" : "", onclick: () => openWorm(w.code) },
      h("span", { class: `dot ${dot}` }), w.code,
      w.unanswered_flags ? h("span", { class: "flagmark", title: "questions to confirm" }, `${w.unanswered_flags}?`) : null);
  }));
  const n = st.worms.filter(w => w.scored).length;
  $("#progFill").style.width = `${(100 * n) / Math.max(st.worms.length, 1)}%`;
  $("#progText").textContent = `${n} / ${st.worms.length} scored` +
    (n < st.cfg.min_scored ? ` (${st.cfg.min_scored} needed before comparing to numbers)` : "");
}

async function openWorm(code) {
  if (st.formDirty || st.roisDirty) {
    if (!confirm("You have unsaved changes on this image. Leave anyway?")) return;
    if (st.formDirty) {
      clearTimeout(st.draftTimer);
      api(`/api/worm/${st.code}/draft`, { discard: true }).catch(() => {});
    }
  }
  const r = await api(`/api/worm/${code}`);
  Object.assign(st, {
    code, meta: r.meta, score: r.score, rois: r.rois || [], roisDirty: false, formDirty: false,
    view: "mip", z: Math.floor(r.meta.n_z / 2), planes: new Array(r.meta.n_z), drawing: null,
  });
  const { width: w, height: hgt } = st.meta;
  [st.mip, st.bfVals] = await Promise.all([
    loadValues(`/img/${code}/mip.png`, w, hgt),
    st.meta.has_brightfield ? loadValues(`/img/${code}/brightfield.png`, w, hgt, false) : null,
  ]);
  $("#bfToggle").disabled = !st.meta.has_brightfield;
  $("#bfToggle").parentElement.title = st.meta.has_brightfield ? "" : "No brightfield channel was recorded for this image";
  $("#codeTitle").textContent = code;
  $("#zSlider").max = st.meta.n_z - 1;
  syncButtons();
  fillForm();
  renderFlags();
  if (r.draft) applyDraft(r.draft);
  renderSuggestions();
  renderRoiList();
  $("#roiMsg").textContent = "";
  setTab(st.score ? st.tab : "score");
  renderList();
  draw();
  preloadPlanes(code);
}

async function preloadPlanes(code) {
  const { width: w, height: hgt, n_z } = st.meta;
  for (let z = 0; z < n_z; z++) {
    if (st.code !== code) return;
    if (!st.planes[z]) st.planes[z] = await loadValues(`/img/${code}/planes/z${String(z).padStart(2, "0")}.png`, w, hgt);
    if (st.view === "plane" && st.z === z) draw();
  }
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
  const vals = st.view === "mip" ? st.mip : st.planes[st.z];
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
    ctx.fillStyle = "#888";
    ctx.font = `${14 * st.zoom}px system-ui`;
    ctx.fillText("loading plane...", 16, 28);
    return;
  }
  // Fixed display range: the same numbers for every image from this microscope.
  const [lo, hi] = st.range === "full" ? m.full_range : m.enhanced_range;
  off.width = m.width; off.height = m.height;
  const im = offCtx.createImageData(m.width, m.height);
  const d = im.data;
  const showBf = st.bf && st.bfVals;
  for (let i = 0; i < vals.length; i++) {
    const gv = Math.min(255, Math.max(0, ((vals[i] - lo) / (hi - lo)) * 255));
    const base = showBf ? st.bfVals[i] * 0.5 : 0;
    d[i * 4] = base;
    d[i * 4 + 1] = Math.min(255, base + gv);
    d[i * 4 + 2] = base;
    d[i * 4 + 3] = 255;
  }
  offCtx.putImageData(im, 0, 0);
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
  ctx.shadowBlur = 0;

  if (st.tab === "mark") {
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
  ctx.strokeStyle = "#ffd84a";
  ctx.lineWidth = 1.5 * st.zoom;
  ctx.beginPath();
  ctx.arc(x, y, Math.max(c.r * st.geom.s + 4, 8), 0, Math.PI * 2);
  ctx.stroke();
  ctx.fillStyle = "#ffd84a";
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

cv.addEventListener("mousedown", e => {
  if (st.tab !== "mark" || st.tool !== "ellipse") return;
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
  if (st.tab !== "mark" || st.tool !== "polygon") return;
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
      h("span", { class: "sw", style: "background:#ffd84a" }), h("span", { class: "nowrap" }, `Spot S${c.rank}`),
      h("span", { class: "btns" }, neurons.map((n, i) =>
        h("button", { type: "button", title: `Use S${c.rank} as ${n}`,
          onclick: () => commitRoi({ type: "candidate", rank: c.rank, label: n, source: `suggestion S${c.rank}` }) },
        i === 0 ? `Use as ${n}` : n))))));
}

function renderRoiList() {
  $("#roiList").replaceChildren(...st.rois.map(r =>
    h("div", { class: "item" },
      h("span", { class: "sw", style: `background:${st.colors[r.label] || "#fff"}` }), r.label,
      h("span", { class: "small", style: "margin:0" }, r.source),
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
    const q = FLAG_QUESTIONS[f.id] || { q: "Seen?", options: ["noted"] };
    return h("div", { class: "flag" }, h("p", {}, f.text), h("div", { class: "q" }, q.q),
      q.options.map(o => h("label", {},
        h("input", { type: "radio", name: `flag_${f.id}`, value: o, checked: answers[f.id] === o }), ` ${o}`)));
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
  $("#markTab").disabled = !st.score;
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

$("#scoreForm").addEventListener("input", e => {
  st.formDirty = true;
  saveMessage(st.connected === false ? `Unsaved changes (${LOST}; they stay in this window)` : "Unsaved changes");
  if (e.target.name === "usable") syncUsable();
  clearTimeout(st.draftTimer);
  st.draftTimer = setTimeout(saveDraft, 600);
});

$("#scoreForm").addEventListener("submit", async e => {
  e.preventDefault();
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
  const problems = [];
  if (Object.keys(flag_answers).length < st.meta.flags.length) problems.push("answer the questions at the top");
  if (!v.usable) problems.push("say whether the image is usable");
  if (v.usable === "no" && !v.unusable_reason) problems.push("pick a reason it is not usable");
  if (v.usable === "yes") {
    if (!v.cells_seen) problems.push("count the cell bodies");
    if ([...Array(n)].some((_, i) => !v[`n${i + 1}_call`])) problems.push(`score all ${n} neurons`);
    if (!v.confidence) problems.push("pick a confidence");
  }
  if (problems.length) {
    saveMessage(`Before saving: ${problems.join("; ")}.`, true);
    return;
  }
  const count = "repeat" in flag_answers ? (flag_answers.repeat === "count" ? "yes" : "no")
    : (st.meta.counted_by_default ? "yes" : "no");
  const body = { ...v, flag_answers, count_this_image: count, display_preset_when_saved: st.range };
  try {
    const r = await api(`/api/worm/${st.code}/score`, body);
    clearTimeout(st.draftTimer);
    st.score = { ...body, saved: r.saved };
    st.formDirty = false;
    saveMessage(`Saved ${r.saved}`);
    $("#markTab").disabled = false;
    await loadList();
    if (v.usable === "yes") setTab("mark");    // nothing to outline on an unusable image
  } catch (err) {
    saveMessage(err.message === LOST
      ? "Not saved: lost connection to wormscore. Your answers stay here; open wormscore again and click Save once this window reconnects."
      : `Not saved: ${err.message}`, true);
  }
});

// ---------------------------------------------------------------- controls

function setTab(t) {
  st.tab = t;
  $$(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === t));
  $$(".tab").forEach(el => el.classList.toggle("hidden", el.dataset.tab !== t));
  cv.style.cursor = t === "mark" ? "crosshair" : "default";
  st.drawing = null;
  draw();
}

function syncButtons() {
  $$("[data-view]").forEach(b => b.classList.toggle("on", b.dataset.view === st.view));
  $$("[data-range]").forEach(b => b.classList.toggle("on", b.dataset.range === st.range));
  $$("[data-zoom]").forEach(b => b.classList.toggle("on", +b.dataset.zoom === st.zoom));
  $("#zSlider").disabled = st.view !== "plane";
  $("#zSlider").value = st.z;
  $("#zText").textContent = st.meta ? (st.view === "plane" ? `${st.z + 1} / ${st.meta.n_z}` : `all ${st.meta.n_z}`) : "-";
  $("#bfToggle").checked = st.bf && !!st.meta && st.meta.has_brightfield;
}

function setView(v) { st.view = v; syncButtons(); draw(); }
function setZ(z) {
  if (!st.meta) return;
  st.z = Math.max(0, Math.min(st.meta.n_z - 1, z));
  st.view = "plane";
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
$$(".tabs button").forEach(b => (b.onclick = () => !b.disabled && setTab(b.dataset.tab)));
$$("#toolSeg button").forEach(b => (b.onclick = () => {
  st.tool = b.dataset.tool;
  st.drawing = null;
  $$("#toolSeg button").forEach(x => x.classList.toggle("on", x === b));
  draw();
}));
$("#zSlider").oninput = e => setZ(+e.target.value);
$("#bfToggle").onchange = e => { st.bf = e.target.checked; draw(); };
$("#sugToggle").onchange = draw;

document.addEventListener("keydown", e => {
  if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName) && e.target.type !== "range") return;
  if (!st.meta) return;
  const k = e.key.toLowerCase();
  if (k === "arrowright" || k === "arrowup") { setZ(st.view === "plane" ? st.z + 1 : st.z); e.preventDefault(); }
  else if (k === "arrowleft" || k === "arrowdown") { setZ(st.view === "plane" ? st.z - 1 : st.z); e.preventDefault(); }
  else if (k === "p") setView(st.view === "mip" ? "plane" : "mip");
  else if (k === "e") { st.range = st.range === "full" ? "enhanced" : "full"; syncButtons(); draw(); }
  else if (k === "b" && st.meta.has_brightfield) { st.bf = !st.bf; syncButtons(); draw(); }
  else if (k === "n") {
    const i = st.worms.findIndex(w => w.code === st.code);
    if (i + 1 < st.worms.length) openWorm(st.worms[i + 1].code);
  } else if (k === "escape") { st.drawing = null; draw(); }
});

window.addEventListener("resize", draw);
window.addEventListener("beforeunload", e => {
  if (st.formDirty || st.roisDirty) e.preventDefault();
});

(async () => {
  st.cfg = await api("/api/config");
  st.projectId = st.cfg.project_id;
  buildForm();
  await loadList();
  const first = st.worms.find(w => !w.scored) || st.worms[0];
  if (first) openWorm(first.code);
  else $("#codeTitle").textContent = "No images";
})();

// Tells the desktop app this page is still open, and reconnects after a lost connection.
setTimeout(checkIn, 20000);
