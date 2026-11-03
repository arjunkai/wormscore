// Home screen: projects, the four steps, progress and the safety questions.
// Every action calls the same code as the command line (see home.py).
"use strict";

const $ = s => document.querySelector(s);
const MOD = /Mac|iPhone|iPad/.test(navigator.platform) ? "Cmd" : "Ctrl";   // the copy key the hint names
const $$ = s => [...document.querySelectorAll(s)];

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el[k] = v;
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false)
    el.append(kid instanceof Node ? kid : String(kid));
  return el;
}

// Small line icons (24 x 24 grid, drawn with currentColor) so they match the text in both themes.
const ICONS = {
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  down: '<path d="M12 5v14M6 13l6 6 6-6"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
};
function icon(name, size = 16) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("width", size);
  s.setAttribute("height", size);
  s.setAttribute("fill", "none");
  s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "2");
  s.setAttribute("stroke-linecap", "round");
  s.setAttribute("stroke-linejoin", "round");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name];
  return s;
}

const st = { data: null, since: 0, log: [], showLog: false, finished: false, lastRunning: false, timer: null,
  updOpen: false, updAgree: false, updKey: "", resultShown: false, closing: false, downSince: null };

const GONE = "wormscore is not running right now. This window reconnects by itself once it starts again.";

async function api(path, body) {
  let res;
  try {
    res = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
  } catch {
    connectionLost();
    throw new Error(GONE);   // the browser's own "Failed to fetch" says nothing useful
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false && data.error) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

// Errors (and notes the user must act on) stay until closed; other notes go after 6 s.
function toast(msg, kind = "error", stay = kind === "error") {
  const t = $("#toast");
  t.className = `toast ${kind}`;
  t.setAttribute("role", kind === "error" ? "alert" : "status");
  t.replaceChildren(h("span", {}, msg), stay ? h("button", {
    class: "ghost icon t-x", title: "Close", "aria-label": "Close message", onclick: () => (t.hidden = true),
  }, icon("close", 14)) : "");     // replaceChildren would print a null as the word "null"
  t.hidden = false;
  clearTimeout(toast.t);
  if (!stay) toast.t = setTimeout(() => (t.hidden = true), 6000);
}

async function act(path, body) {
  try {
    const r = await api(path, body);
    await refresh(true);
    return r;
  } catch (e) {
    toast(e.message);
    return null;
  }
}

// ---------------------------------------------------------------- modal

// Puts focus back on a control after the page was redrawn (controls carry a data-k name).
function refocus(k, fallback) {
  const el = k ? document.querySelector(`[data-k="${CSS.escape(k)}"]`) : null;
  (el || (fallback?.isConnected ? fallback : null))?.focus();
}

// opts.wide: a wider box for forms. opts.check: runs when a choice is picked and returns an
// error to show (the dialog then stays open) or nothing to close it.
function ask(title, body, choices, cancel = "Cancel", opts = {}) {
  const opener = document.activeElement, openerKey = opener?.dataset?.k;
  return new Promise(resolve => {
    $("#mTitle").textContent = title;
    if (body instanceof Node) $("#mBody").replaceChildren(body);
    else $("#mBody").textContent = body;
    $("#mCancel").textContent = cancel;
    $("#mErr").hidden = true;
    // A plain yes/no question gets a small box with its button next to Cancel; choices that
    // need explaining are listed as cards.
    const simple = choices.length > 0 && choices.every(c => !c.desc);
    $("#modal .dialog").classList.toggle("sm", simple && !opts.wide);
    $("#modal .dialog").classList.toggle("wide", !!opts.wide);
    const box = simple ? $("#mFootChoices") : $("#mChoices");
    (simple ? $("#mChoices") : $("#mFootChoices")).replaceChildren();
    box.replaceChildren(...choices.map(c => simple
      // red only for what cannot be undone; plain for harmless ones like taking a project off the list
      ? h("button", { class: c.danger ? "btn danger" : c.plain ? "btn" : "primary", onclick: () => pick(c.value) }, c.label)
      : h("button", { class: "choice" + (c.danger ? " danger" : ""), onclick: () => pick(c.value) },
        h("b", {}, c.label), c.desc ? h("span", {}, c.desc) : null)));
    const m = $("#modal");
    const pick = async v => {
      const err = opts.check ? await opts.check(v) : null;
      // shown in the dialog, next to its buttons: a toast would cover them
      $("#mErr").textContent = err || "";
      $("#mErr").hidden = !err;
      if (err) $("#mErr").scrollIntoView({ block: "nearest" });
      if (!err) close(v);
    };
    const close = v => {
      m.hidden = true;
      $("#modal .dialog").classList.remove("wide");
      document.removeEventListener("keydown", esc);
      refocus(openerKey, opener);
      resolve(v);
    };
    const esc = e => { if (e.key === "Escape") close(null); };
    $("#mCancel").onclick = () => close(null);
    document.addEventListener("keydown", esc);
    m.hidden = false;
    // A choice that cannot be undone never gets the focus: one Enter press must not pick it.
    (choices.some(c => c.danger) ? $("#mCancel")
      : box.querySelector("button") || $("#mBody").querySelector("input, button") || $("#mCancel"))?.focus();
  });
}

// ---------------------------------------------------------------- formatting

const plural = (n, one, many = one + "s") => `${n.toLocaleString()} ${n === 1 ? one : many}`;

function duration(s) {
  if (s === null || s === undefined) return "";
  if (s < 60) return `${s} s`;
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

function cardStatus(p) {
  if (!p.has_config) return "Folder or config.json missing";
  if (p.package) return `Second-scorer package: scored ${p.scored} of ${p.coded ?? 0}`;
  if (p.unblinded) return "Unblinded";
  if (p.coded !== null && p.coded !== undefined) return `Scored ${p.scored} of ${p.coded}`;
  if (p.scanned !== null && p.scanned !== undefined) return "Scanned; blind set not built";
  return "Not scanned yet";
}

// ---------------------------------------------------------------- render

// The X only takes a project off this list; people reasonably fear it deletes the folder.
async function forgetProject(name, path) {
  const go = await ask(`Remove "${name}" from the list?`,
    h("span", {}, "The folder stays on your computer. Use ", h("b", {}, "Open project"), " to add it back."),
    [{ value: true, label: "Remove", plain: true }]);
  if (go) act("/api/forget", { path });
}

function renderSide(d) {
  const cur = d.project?.path;
  const list = d.recent.map(p => {
    const name = p.name || p.path.split(/[\\/]/).pop();
    return h("div", { class: "card-p" + (p.path === cur ? " cur" : "") },
      h("button", { class: "pick", "data-k": `pick:${p.path}`, "aria-current": p.path === cur ? "true" : null,
        onclick: () => p.path !== cur && act("/api/select", { path: p.path }) },
        h("span", { class: "nm" }, name),
        h("span", { class: "pth", title: p.path }, h("bdi", { dir: "ltr" }, p.path)),
        h("span", { class: "st" }, cardStatus(p))),
      h("button", {
        class: "x", "data-k": `x:${p.path}`, title: "Remove from this list (the folder itself is not changed)",
        "aria-label": `Remove ${name} from the list`, onclick: () => forgetProject(name, p.path),
      }, icon("close", 14)));
  });
  $("#recent").replaceChildren(...(list.length ? list : [h("div", { class: "empty-side" }, "No projects yet.")]));
}

function welcome() {
  const tile = (act_, title, text) => h("div", { class: "tile", role: "button", tabindex: "0", "data-k": `tile:${act_}`,
    onclick: () => sideAction(act_), onkeydown: e => e.key === "Enter" && sideAction(act_) },
    h("b", {}, title), h("span", {}, text));
  return h("div", { class: "welcome" },
    h("img", { src: "/static/icon.svg", width: 48, height: 48, alt: "" }),
    h("h2", {}, "Blind scoring for fluorescent neurons"),
    h("p", {}, "Open a project, start a new one, or try the demo with simulated or real worm images."),
    h("div", { class: "tiles" },
      tile("open-project", "Open project", "A folder that already has a config.json."),
      tile("new-project", "New project", "Make a config.json in an empty folder, then point it at your images."),
      tile("demo", "Try a demo", "20 simulated worms, or 4 real heads (CC BY 4.0).")));
}

// A long path may wrap only after a folder separator, never inside a folder name.
const breakable = path => path.split(/(?<=[\\/])/).map(part => h("span", {}, part));

function stepCard(n, state, title, desc, fact, actions, extra) {
  const chip = { done: ["done", "Done"], next: ["next", "Next"], running: ["running", "Running"], wait: ["wait", "Waiting"] }[state];
  return h("li", { class: `step ${state}` },
    h("div", { class: "num", "aria-label": state === "done" ? `Step ${n}, done` : `Step ${n}` },
      state === "done" ? icon("check", 18) : n),
    h("div", {},
      h("div", { class: "s-title" }, title, h("span", { class: `chip ${chip[0]}` }, chip[1])),
      h("div", { class: "s-desc" }, desc),
      fact ? h("div", { class: "s-fact" }, fact) : null,
      extra || null),
    h("div", { class: "s-act" }, actions));
}

function renderMain(d) {
  const main = $("#main");
  const p = d.project;
  if (!p) return main.replaceChildren(welcome());
  if (p.package) return main.replaceChildren(packageView(p, d.job));
  const job = d.job;
  const busy = job.running;
  const runningHere = busy && job.project === p.path;
  const stepRunning = name => runningHere && job.title.toLowerCase().includes(name);

  const scanned = p.scanned !== null && p.scanned !== undefined;
  const built = p.coded !== null && p.coded !== undefined;
  const allScored = built && p.coded > 0 && p.scored >= p.coded;
  const ok = p.has_config && !p.config_error;
  const needFolders = p.has_config && !scanned && p.folders_found === 0;

  const building = stepRunning("build") || stepRunning("rebuild") || stepRunning("add new");
  const s1 = stepRunning("scan") ? "running" : scanned ? "done" : "next";
  const s2 = building ? "running" : built ? "done" : scanned ? "next" : "wait";
  const s3 = building ? "wait" : built ? (allScored ? "done" : "next") : "wait";
  const s4 = stepRunning("unblind") ? "running" : p.unblinded ? "done" : allScored ? "next" : "wait";

  const steps = h("ol", { class: "steps" },
    stepCard(1, s1, "Scan data", "List every file and image and identify channels from metadata.",
      needFolders ? "First tell wormscore where your images are and how file names show the genotype."
        : scanned ? `${plural(p.scanned, "raw image")} found. Scan again if you added files.` : null,
      [needFolders && !p.scoring_started ? h("button", { class: "primary", "data-k": "folders", disabled: busy, onclick: () => openProjectSettings("folders") }, "Set image folders...") : null,
       h("button", { class: s1 === "next" && !needFolders ? "primary" : "btn", "data-k": "step1", disabled: busy || !ok || needFolders, onclick: scan },
        scanned ? "Scan again" : "Scan data")]),
    stepCard(2, s2, "Build blind set", "Apply the inclusion rules, give every image a random code, and record the analysis plan and score form in log.md.",
      building ? "Building..." : built ? `${plural(p.coded, "image")} coded.` : null,
      [h("button", { class: s2 === "next" ? "primary" : "btn", "data-k": "step2", disabled: busy || !ok || !scanned, onclick: build },
        !built ? "Build blind set" : p.scoring_started ? "Add or rebuild..." : "Rebuild...")],
      // what the scorer will answer can change until the first score, so it is offered here
      !p.scoring_started && !building && ok ? h("div", { class: "s-fact" }, "Questions the scorer answers: ",
        h("button", { class: "linkish", "data-k": "form", disabled: busy, onclick: () => openProjectSettings("form") }, "check the score form"),
        ". It locks at the first score.") : null),
    stepCard(3, s3, "Score", "Score each coded image in the scoring window. File names and genotypes stay hidden.",
      building ? "Available when the blind set is built." : built ? `Scored ${p.scored} of ${p.coded}.` : null,
      [h("button", { class: s3 === "next" ? "primary" : "btn", "data-k": "step3", disabled: busy || !built, onclick: score },
        p.scored ? "Continue scoring" : "Start scoring")],
      built && p.coded && !building ? h("div", { class: "mini" }, h("div", { style: `width:${Math.min(100, 100 * p.scored / p.coded)}%` })) : null),
    stepCard(4, s4, "Unblind", "Reveal which genotype each code was, then write the results tables and a draft Methods text. Do this only when scoring is finished.",
      p.unblinded ? "Results are in the outputs\\unblinded folder." + ((p.second_scorers || []).some(s => s.imported)
        ? " Agreement with second scorers is in between_scorers.csv." : "") : null,
      [h("label", { class: "check" },
        h("input", { type: "checkbox", "data-k": "finished", checked: st.finished, onchange: e => { st.finished = e.target.checked; render(); } }),
        "I have finished scoring"),
       h("button", { class: s4 === "next" && st.finished ? "primary" : "btn", "data-k": "step4", disabled: busy || !built || !p.scored || !st.finished,
         onclick: unblind }, p.unblinded ? "Unblind again" : "Unblind"),
       p.unblinded ? h("button", { class: "btn", "data-k": "results", onclick: () => act("/api/open", { what: "results" }) }, "Open results") : null]));

  main.replaceChildren(h("div", { class: "wrap" },
    h("div", { class: "p-head" },
      h("div", {}, h("h2", {}, p.name || "Project"), h("div", { class: "p-path" }, breakable(p.path))),
      h("div", { class: "p-tools" },
        h("button", { class: "btn", "data-k": "folder", disabled: !p.exists, onclick: () => act("/api/open", { what: "folder" }) }, "Open folder"),
        h("button", { class: "btn", "data-k": "settings", disabled: !p.has_config || busy, onclick: () => openProjectSettings() }, "Project settings..."))),
    !p.has_config ? h("div", { class: "banner" }, "This folder has no config.json. Open another project or make a new one.") : null,
    p.config_error ? h("div", { class: "banner" }, "config.json needs a fix before the next step:\n" + p.config_error) : null,
    busy && !runningHere ? h("div", { class: "banner" }, `"${job.title}" is running for another project.`) : null,
    activity(job),
    steps,
    built && !building ? secondScorerCard(p, busy) : null));
  const log = $("#log");
  if (log) log.scrollTop = log.scrollHeight;
}

function activity(job) {
  if (!st.log.length) return null;
  const done = !job.running;
  const pct = job.frac !== null && job.frac !== undefined ? Math.round(100 * job.frac) : null;
  const cls = done ? (job.outcome === "done" ? " ok" : job.outcome ? " bad" : "") : "";
  const meta = job.running
    ? [pct !== null ? `${pct}%` : "working", job.eta_s ? `about ${duration(job.eta_s)} left` : null, `${duration(job.elapsed_s)} so far`].filter(Boolean).join(" · ")
    : job.outcome === "done" ? `Done in ${duration(job.elapsed_s)}` : job.outcome ? "Stopped. See the log." : "";
  const width = done ? 100 : pct ?? 0;
  const last = [...st.log].reverse().find(l => l.trim() && !l.startsWith("---")) || "";
  return h("section", { class: "activity" + cls, "aria-live": "polite" },
    h("div", { class: "a-head" }, h("span", { class: "a-title" }, job.running ? job.title : `Last step: ${job.title}`),
      h("span", { class: "a-meta" }, meta)),
    h("div", { class: "bar" + (job.running && pct === null ? " indet" : "") }, h("div", { style: `transform:scaleX(${width / 100})` })),
    h("div", { class: "a-last", title: last }, last),
    h("button", { class: "btn subtle log-toggle", "data-k": "log", onclick: () => { st.showLog = !st.showLog; render(); } },
      st.showLog ? "Hide log" : "Show log"),
    st.showLog ? h("pre", { class: "log", id: "log" }, st.log.join("\n")) : null);
}

// ---------------------------------------------------------------- second scorer

const SCORER_NAME = /^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$/;

function secondScorerCard(p, busy) {
  const list = p.second_scorers || [];
  const rows = list.map(s => h("li", { class: "pk" },
    h("div", { class: "pk-text" }, h("b", {}, s.scorer),
      h("span", { class: "pk-meta" }, `${plural(s.images, "image")} · made ${s.made.slice(0, 10)}`)),
    h("span", { class: `chip ${s.imported ? "done" : "wait"}` },
      s.imported ? `Imported: ${s.scored} of ${s.images} scored` : "Waiting for scores"),
    !s.imported && s.folder_found ? h("button", { class: "btn subtle", "data-k": `pk:${s.id}`,
      title: "Zip this folder and send it to the second scorer",
      onclick: () => act("/api/open", { what: "package", id: s.id }) }, "Open package folder") : null));
  return h("section", { class: "extra" },
    h("div", { class: "x-head" },
      h("div", {},
        h("div", { class: "s-title" }, "Second scorer", h("span", { class: "chip wait" }, "Optional")),
        h("div", { class: "s-desc" }, "Another person scores some of the coded images on their own, from a package with no key. "
          + "Unblinding compares their answers with yours; your results never use them.")),
      h("div", { class: "x-act" },
        h("button", { class: "btn", "data-k": "pk-make", disabled: busy, onclick: makePackage }, "Make a package..."),
        list.length ? h("button", { class: "btn", "data-k": "pk-import", disabled: busy, onclick: importScores }, "Import scores...") : null)),
    rows.length ? h("ul", { class: "pk-list" }, rows) : null);
}

async function makePackage() {
  const p = st.data.project;
  const name = h("input", { type: "text", id: "pkName", class: "f-input", maxlength: "40", autocomplete: "off", placeholder: "For example: Sam K" });
  const pct = h("input", { type: "range", id: "pkPct", min: "1", max: "100", value: "30" });
  const out = h("output", { for: "pkPct", class: "f-out" });
  const show = () => (out.textContent = `${pct.value}% · about ${plural(Math.max(1, Math.round(p.coded * pct.value / 100)), "image")}`);
  pct.oninput = show;
  show();
  const body = h("div", { class: "form" },
    h("label", { class: "f-label", for: "pkName" }, "Second scorer's name"), name,
    h("label", { class: "f-label", for: "pkPct" }, "Share of each genotype's images"), h("div", { class: "f-range" }, pct, out),
    h("p", { class: "f-help" }, "Images are picked at random within each genotype (at least one each) and shown in a new random order. "
      + "The package holds the coded images and the scoring form only: no key, file names or analysis plan. "
      + "Next, choose a new, empty folder outside this project."));
  const asked = ask("Make a package for a second scorer", body, [{ value: true, label: "Choose folder..." }], "Cancel", {
    check: () => SCORER_NAME.test(name.value.trim()) ? null
      : "Give the second scorer a short name: letters, digits, spaces, dots, dashes or underscores (up to 40)." });
  name.focus();
  name.onkeydown = e => { if (e.key === "Enter") $("#mFootChoices button")?.click(); };
  if (!(await asked)) return;
  st.log = []; st.since = 0; st.showLog = false;
  act("/api/second-scorer", { action: "export", scorer: name.value.trim(), percent: Number(pct.value) });
}

async function importScores() {
  const go = await ask("Import a second scorer's scores",
    h("span", {}, "Choose the package folder the second scorer sent back (unzip it first). wormscore checks that it is a package made here, "
      + "with the same images and scoring form, then keeps the scores in the scorers folder. Your own scores do not change. "
      + "Then run ", h("b", {}, "Unblind"), " (again) to get the agreement tables."),
    [{ value: true, label: "Choose folder..." }]);
  if (!go) return;
  st.log = []; st.since = 0; st.showLog = false;
  act("/api/second-scorer", { action: "import" });
}

// A second scorer's own view of a package: only Score, and how to send it back.
function packageView(p, job) {
  const busy = job.running;
  const all = p.coded > 0 && p.scored >= p.coded;
  return h("div", { class: "wrap" },
    h("div", { class: "p-head" },
      h("div", {}, h("h2", {}, p.name || "Package"), h("div", { class: "p-path" }, breakable(p.path))),
      h("div", { class: "p-tools" },
        h("button", { class: "btn", "data-k": "folder", disabled: !p.exists, onclick: () => act("/api/open", { what: "folder" }) }, "Open folder"))),
    h("div", { class: "note" }, h("b", {}, `Second-scorer package for ${p.package.scorer}`),
      h("span", {}, "Score every image on your own, without asking how anyone else scored it. When you have finished, "
        + "zip this whole folder and send it back to the person who gave it to you. Do not rename or change its files.")),
    p.config_error ? h("div", { class: "banner" }, "config.json needs a fix:\n" + p.config_error) : null,
    activity(job),
    h("ol", { class: "steps" },
      stepCard(1, all ? "done" : "next", "Score", "Score each coded image in the scoring window. This package has no file names or genotypes.",
        all ? `All ${p.coded} scored. Zip this folder and send it back.` : `Scored ${p.scored} of ${p.coded ?? 0}.`,
        [h("button", { class: all ? "btn" : "primary", "data-k": "step3", disabled: busy || !p.coded, onclick: score },
          p.scored ? "Continue scoring" : "Start scoring")],
        p.coded ? h("div", { class: "mini" }, h("div", { style: `width:${Math.min(100, 100 * p.scored / p.coded)}%` })) : null)));
}

// ---------------------------------------------------------------- scoring form (in Project settings)

// The form as the scoring page reads it (config.json "scoring"), from the builder's answers.
function formForPreview(m) {
  const asQ = (q, i) => ({
    id: `p${i}`, label: q.label || "Untitled question", type: q.type, required: q.required,
    skip_when_calls: q.ask === "animal" ? [] : q.skip_when,
    options: [...answersOf(q), ...(q.cant_tell ? [q.cant_tell] : [])],
  });
  return {
    neurons: m.neurons.length ? m.neurons.map((n, i) => n || `Neuron ${i + 1}`) : ["Neuron 1"],
    calls: m.calls.map(c => c.text || "(call)"),
    sides: m.sides ? ["left", "right"] : [],
    count_question: { label: m.count.label || "(count question)", options: m.count.options },
    neuron_questions: m.questions.map(asQ).filter((_, i) => m.questions[i].ask !== "animal"),
    animal_questions: m.questions.map(asQ).filter((_, i) => m.questions[i].ask === "animal"),
    rules: m.rules,
  };
}

const splitList = text => text.split(",").map(s => s.trim()).filter(Boolean);
const splitLines = text => text.split("\n").map(s => s.trim()).filter(Boolean);
// The answers a question offers (without "can't tell"): a count's are its numbers, the last may be "N+".
function countAnswers(c) {
  const lo = Number(c?.min), hi = Number(c?.max);
  if (!Number.isInteger(lo) || !Number.isInteger(hi) || lo < 0 || hi <= lo || hi - lo > 40) return [];
  return [...Array(hi - lo).keys()].map(k => String(lo + k)).concat(c.top ? `${hi}+` : String(hi));
}
const answersOf = q => q.type === "yesno" ? ["yes", "no"] : q.type === "count" ? countAnswers(q.count) : q.options;
const RANKED = ["scale", "count"];
const TYPE_NAMES = { yesno: "Yes / No", choice: "Choice", scale: "Scale", count: "Count" };
const ROLLUP_NAMES = {
  worst: "Worst neuron (the highest level of any neuron)", mean: "Mean level of the neurons that were assessed",
  sum: "Total over the neurons", mean_per_neuron: "Mean per assessed neuron",
};
const ROLLUPS = { scale: ["worst", "mean"], count: ["sum", "mean_per_neuron"] };

// The Scoring form section: an editor on the left, a live preview on the right. Returns the
// element and payload(), which is null when the form cannot be changed here.
function formSection(info) {
  const m = structuredClone(info.model);
  const off = !!(info.locked || info.readonly);
  const edit = h("div", { class: "sf-edit" });
  const prev = h("div", { class: "sf-prev" });
  const root = h("div", { class: "sf" });

  const move = (arr, j, d) => {
    const k = j + d;
    if (k < 0 || k >= arr.length) return;
    [arr[j], arr[k]] = [arr[k], arr[j]];
    drawAll();
  };
  // confirm(j): a question to ask before removing row j (a filled-in question), or null to remove at once.
  const tools = (arr, j, what, min = 0, confirm = null) => off ? null : h("span", { class: "sf-tools" },
    h("button", { class: "ghost icon sm", title: "Move up", "aria-label": `Move ${what} up`, disabled: j === 0, onclick: () => move(arr, j, -1) }, icon("up", 14)),
    h("button", { class: "ghost icon sm", title: "Move down", "aria-label": `Move ${what} down`, disabled: j === arr.length - 1, onclick: () => move(arr, j, 1) }, icon("down", 14)),
    h("button", { class: "ghost icon sm", title: "Remove", "aria-label": `Remove ${what}`, disabled: arr.length <= min,
      onclick: async () => {
        const q = confirm?.(j);
        if (q && !(await ask(q.title, q.body, [{ value: true, label: "Remove" }]))) return;
        arr.splice(j, 1);
        drawAll();
      } }, icon("close", 14)));
  const confirmQuestion = j => m.questions[j].label.trim() ? {
    title: `Remove question ${j + 1}?`,
    body: `“${m.questions[j].label.trim()}” and its answers and analysis settings are removed from the form. `
      + "Nothing is saved until you press Save; Cancel at the bottom keeps the form as it was.",
  } : null;
  const text = (value, set, attrs = {}) => h("input", { type: "text", class: "f-input", value: value ?? "", autocomplete: "off",
    disabled: off, ...attrs, oninput: e => { set(e.target.value); drawPreview(); } });
  const check = (checked, label, set, attrs = {}) => h("label", { class: "sf-check" },
    h("input", { type: "checkbox", checked: !!checked, disabled: off, ...attrs, onchange: e => { set(e.target.checked); drawAll(); } }), label);
  const outcomes = () => [...new Set(m.questions.map(q => q.outcome).filter(Boolean))];

  const neuronsCard = () => h("div", { class: "s-card sf-card" },
    h("div", { class: "f-label" }, "Neurons in each image"),
    h("div", { class: "f-help" }, "Each one gets its own call and questions, in this order."),
    m.neurons.map((n, j) => h("div", { class: "sf-row" },
      text(n, v => { m.neurons[j] = v; }, { "aria-label": `Neuron ${j + 1} name` }), tools(m.neurons, j, `neuron ${j + 1}`, 1))),
    off || m.neurons.length >= 8 ? null : h("button", { class: "btn subtle add",
      onclick: () => { m.neurons.push(`Neuron ${m.neurons.length + 1}`); drawAll(); } }, "Add neuron"),
    check(m.sides, "Also ask which side each neuron is on (left / right)", v => { m.sides = v; }));

  const callsCard = () => h("div", { class: "s-card sf-card" },
    h("div", { class: "f-label" }, "Call for each neuron"),
    h("div", { class: "f-help" }, "Tick the calls that count as a defect. Keep them in order from normal to not seen: "
      + "the last call means the neuron was not seen."),
    m.calls.map((c, j) => h("div", { class: "sf-row" },
      text(c.text, v => {
        for (const q of m.questions) q.skip_when = q.skip_when.map(s => s === c.text ? v : s);
        c.text = v;
      }, { "aria-label": `Call ${j + 1}`, onchange: () => drawAll() }),
      h("label", { class: "sf-check tight" }, h("input", { type: "checkbox", checked: c.defect, disabled: off,
        onchange: e => { c.defect = e.target.checked; drawAll(); } }), "defect"),
      tools(m.calls, j, `call ${j + 1}`, 2))),
    off ? null : h("button", { class: "btn subtle add", onclick: () => {
      m.calls.splice(Math.max(m.calls.length - 1, 0), 0, { text: "", defect: true });
      drawAll();
    } }, "Add call"));

  const radioRow = (j, key, label, choices, current, pick) => h("div", { class: "sf-opts", role: "radiogroup", "aria-label": `Question ${j + 1}: ${label}` },
    choices.map(([value, text]) => h("label", { class: "sf-check" },
      h("input", { type: "radio", name: `sf${key}${j}`, checked: current === value, disabled: off,
        onchange: () => { pick(value); drawAll(); } }), text)));

  const setType = (q, t) => {
    q.type = t;
    if ((t === "choice" || t === "scale") && !q.options.length) q.options = t === "scale" ? ["none", "mild", "severe"] : ["yes", "no"];
    if (t === "count" && !q.count) q.count = { min: 0, max: 5, top: true };
    if (RANKED.includes(t)) {
      q.defect = [];
      if (!ROLLUPS[t].includes(q.rollup)) q.rollup = ROLLUPS[t][0];
      q.cutoff = "";
    } else {
      q.defect = q.defect.filter(d => answersOf(q).includes(d));
    }
  };

  const countInputs = (q, j) => {
    const num = (key, label) => h("label", { class: "sf-inline" }, label,
      h("input", { type: "number", class: "f-input sf-number", min: 0, max: 999, step: 1, value: q.count[key], disabled: off,
        "aria-label": `Question ${j + 1}: ${label === "from" ? "lowest" : "highest"} count`,
        oninput: e => { q.count[key] = e.target.value === "" ? "" : Number(e.target.value); drawPreview(); },
        onchange: () => drawAll() }));
    return h("div", { class: "sf-count" }, num("min", "from"), num("max", "to"),
      check(q.count.top, `The last answer means that many or more (${q.count.max ?? "N"}+)`, v => { q.count.top = v; }));
  };

  // The cutoff, set before scoring: an animal at or past it is "affected", tested with Fisher's exact test.
  const cutoffSelect = (q, j) => {
    const answers = answersOf(q);
    const choices = q.type === "scale" ? answers.slice(1).map(a => [a, `${a} or worse`])
      : [...Array(Math.max(Number(q.count?.max) || 0, 1)).keys()].map(k => [String(k + 1), `${k + 1} or more`]);
    if (q.cutoff && !choices.some(([v]) => v === q.cutoff)) q.cutoff = "";
    return h("select", { class: "f-input sf-select", disabled: off, "aria-label": `Question ${j + 1}: cutoff`,
      onchange: e => { q.cutoff = e.target.value; } },
      [["", "No cutoff"], ...choices].map(([v, l]) => h("option", { value: v, selected: q.cutoff === v }, l)));
  };

  const questionCard = (q, j) => {
    const answers = answersOf(q);
    const ranked = RANKED.includes(q.type), perAnimal = q.ask === "animal";
    const row = (label, ...body) => [h("span", { class: "sf-k" }, label), h("div", { class: "sf-v" }, ...body)];
    return h("div", { class: "s-card sf-card sf-q" },
      h("div", { class: "sf-row sf-q-head" }, h("span", { class: "sf-num" }, `${j + 1}`),
        text(q.label, v => { q.label = v; }, { placeholder: "The question, e.g. Axon reaches the nerve ring?", "aria-label": `Question ${j + 1}` }),
        tools(m.questions, j, `question ${j + 1}`, 0, confirmQuestion)),
      h("div", { class: "sf-grid" },
        ...row("Asked", radioRow(j, "Ask", "asked", [["neuron", "For each neuron"], ["animal", "Once per animal"]], q.ask || "neuron",
          v => { q.ask = v; if (v === "animal") { q.skip_when = []; q.rollup = ""; } else if (ranked && !q.rollup) q.rollup = ROLLUPS[q.type][0]; })),
        ...row("Answers",
          radioRow(j, "Type", "answer type", Object.entries(TYPE_NAMES), q.type, t => setType(q, t)),
          q.type === "choice" ? text(q.options.join(", "), v => { q.options = splitList(v); },
            { placeholder: "Answers, separated by commas", "aria-label": `Question ${j + 1}: answers`,
              onchange: () => { q.defect = q.defect.filter(d => q.options.includes(d)); drawAll(); } }) : null,
          q.type === "scale" ? levelsEditor(q, j) : null,
          q.type === "count" ? countInputs(q, j) : null,
          check(!!q.cant_tell, `Add a “can't tell” answer (that ${perAnimal ? "animal" : "neuron"} is left out of the results)`,
            v => { q.cant_tell = v ? "can't tell" : ""; })),
        ...(perAnimal ? [] : row("Skip when", h("div", { class: "sf-opts" }, m.calls.map(c => check(q.skip_when.includes(c.text), `call is ${c.text || "(call)"}`,
          v => { q.skip_when = m.calls.map(x => x.text).filter(x => x === c.text ? v : q.skip_when.includes(x)); }))))),
        ...row("", check(q.required, "Required (Save waits until it is answered)", v => { q.required = v; })),
        h("div", { class: "sf-split", role: "presentation" }, "Analysis"),
        ...row("Outcome",
          text(q.outcome, v => { q.outcome = v; }, { placeholder: "Not tested (answers only recorded)", list: ranked ? null : "sfOutcomes",
            "aria-label": `Question ${j + 1}: outcome name`, onchange: () => drawAll() }),
          h("div", { class: "f-help" }, !q.outcome ? "Name an outcome to test this question at unblinding."
            : ranked ? "Tested on its own: one value per animal, Mann-Whitney test between genotypes."
              : "Questions with the same outcome name are combined; Fisher's exact test between genotypes.")),
        ...(ranked ? [] : row("Defect if", h("div", { class: "sf-opts" }, answers.map(a => check(q.defect.includes(a), a,
          v => { q.defect = answers.filter(x => x === a ? v : q.defect.includes(x)); }))))),
        ...(ranked && q.outcome && !perAnimal ? row("Per animal", h("select", { class: "f-input sf-select", disabled: off,
          "aria-label": `Question ${j + 1}: value per animal`, onchange: e => { q.rollup = e.target.value; } },
          ROLLUPS[q.type].map(r => h("option", { value: r, selected: (q.rollup || ROLLUPS[q.type][0]) === r }, ROLLUP_NAMES[r])))) : []),
        ...(ranked && q.outcome ? row("Cutoff", cutoffSelect(q, j),
          h("div", { class: "f-help" }, "Optional: the share of animals at or past it is also compared, with "
            + "Fisher's exact test.")) : [])));
  };

  // A scale's levels, one per row, best at the top and worst at the bottom: the order is what the test compares.
  const levelsEditor = (q, j) => h("div", { class: "sf-levels", role: "group", "aria-label": `Question ${j + 1}: levels, best first` },
    q.options.map((lv, k) => h("div", { class: "sf-row" },
      h("span", { class: "sf-end" }, k === 0 ? "best" : k === q.options.length - 1 ? "worst" : ""),
      text(lv, v => { q.options[k] = v.trim(); }, { "aria-label": `Question ${j + 1}: level ${k + 1}`, onchange: () => drawAll() }),
      tools(q.options, k, `level ${k + 1}`, 2))),
    off || q.options.length >= info.max_levels ? null : h("button", { class: "btn subtle add sm", onclick: () => {
      q.options.push("");
      drawAll();
      edit.querySelector(`[aria-label="Question ${j + 1}: level ${q.options.length}"]`)?.focus();
    } }, "Add level"));

  const questionsCard = () => {
    const nNeuron = m.questions.filter(q => q.ask !== "animal").length;
    const nAnimal = m.questions.length - nNeuron;
    return h("section", { class: "sf-section", "aria-label": "Extra questions" },
      h("div", { class: "sf-section-head" },
        h("div", { class: "f-label" }, "Extra questions"),
        h("div", { class: "f-help" }, "Asked for each neuron or once per animal. Yes / No and Choice answers can count as a "
          + "defect; a Scale (levels in order) or a Count gives each animal a value.")),
      h("datalist", { id: "sfOutcomes" }, outcomes().map(o => h("option", { value: o }))),
      m.questions.length ? m.questions.map(questionCard) : h("div", { class: "f-help sf-none" }, "None. The call is the only answer for each neuron."),
      nNeuron > info.warn_questions ? h("div", { class: "pv warn" },
        `${nNeuron} questions per neuron. More than ${info.warn_questions} makes scoring slower and less consistent.`) : null,
      off || (nNeuron >= info.max_questions && nAnimal >= info.max_questions) ? null : h("button", { class: "btn subtle add", onclick: () => {
        const last = m.calls.at(-1)?.text;
        m.questions.push({ id: null, label: "", type: "yesno", ask: nNeuron >= info.max_questions ? "animal" : "neuron",
          options: [], count: null, cant_tell: "can't tell", defect: ["no"], required: true,
          skip_when: last && nNeuron < info.max_questions ? [last] : [], outcome: "", rollup: "", cutoff: "" });
        drawAll();
        $$(".sf-q").at(-1)?.querySelector("input")?.focus();
      } }, "Add question"));
  };

  const resultsCard = () => {
    const names = outcomes();
    if (m.primary && m.primary !== "calls" && !names.includes(m.primary)) m.primary = "";
    return h("div", { class: "s-card sf-card" },
      h("label", { class: "f-label", for: "sfPrimary" }, "Primary outcome"),
      h("div", { class: "f-help" }, "Name the one result the study is about before scoring starts. The results "
        + "and Methods then label it primary and every other test secondary."),
      h("select", { id: "sfPrimary", class: "f-input sf-select", disabled: off, onchange: e => { m.primary = e.target.value; } },
        [["", "Not named"], ["calls", "Neuron calls (any defect call)"], ...names.map(n => [n, n])]
          .map(([v, l]) => h("option", { value: v, selected: m.primary === v }, l))));
  };

  const moreCard = () => h("details", { class: "s-card sf-card sf-more" },
    h("summary", {}, "Count question, rules and unusable reasons"),
    h("label", { class: "f-label", for: "sfCount" }, "Count question (one per image)"),
    text(m.count.label, v => { m.count.label = v; }, { id: "sfCount" }),
    text(m.count.options.join(", "), v => { m.count.options = splitList(v); }, { placeholder: "Answers, separated by commas", "aria-label": "Count answers" }),
    h("label", { class: "f-label", for: "sfRules" }, "Rules shown above the form, one per line"),
    h("textarea", { id: "sfRules", class: "f-input", rows: 4, disabled: off,
      oninput: e => { m.rules = splitLines(e.target.value); drawPreview(); } }, m.rules.join("\n")),
    h("label", { class: "f-label", for: "sfReasons" }, "Reasons an image is not usable, one per line"),
    h("textarea", { id: "sfReasons", class: "f-input", rows: 4, disabled: off,
      oninput: e => { m.unusable_reasons = splitLines(e.target.value); } }, m.unusable_reasons.join("\n")));

  const start = () => off ? null : h("div", { class: "sf-start" }, h("span", { class: "f-help" }, "Start from:"),
    info.presets.map(p => h("button", { class: "btn subtle", title: p.desc, onclick: async () => {
      const go = await ask(`Start from ${p.title}?`, `${p.desc}\nThis replaces the form below. Nothing is saved until you press Save.`,
        [{ value: true, label: "Replace the form" }]);
      if (!go) return;
      try { Object.assign(m, (await api("/api/project-settings", { action: "preset", preset: p.id })).model); drawAll(); }
      catch (e) { toast(e.message); }
    } }, p.title)));

  const drawPreview = () => {
    const sc = formForPreview(m);
    const f = h("form", { class: "sf-form", "aria-label": "Preview of the score form", onsubmit: e => e.preventDefault(),
      onchange: e => { if (/^n\d+_call$/.test(e.target.name)) wsForm.syncSkips(f, sc); } },
      sc.rules.length ? h("ul", { class: "sf-rules" }, sc.rules.map(r => h("li", {}, r))) : null,
      h("fieldset", {}, h("legend", {}, sc.count_question.label), h("div", {}, wsForm.radios(h, "cells_seen", sc.count_question.options))),
      ...wsForm.neurons(h, sc), ...wsForm.animal(h, sc));
    wsForm.syncSkips(f, sc);
    prev.replaceChildren(h("div", { class: "sf-prev-head" }, h("span", { class: "f-label" }, "Preview"),
      h("span", { class: "f-help" }, "What the scorer answers for each image. Click a call to see the skips.")), f);
  };
  // Redrawing removes the focused box, which can fire its change event again; that second call
  // is skipped, and the redraw already shows its value.
  let drawing = false;
  const drawAll = () => {
    if (drawing) return;
    drawing = true;
    try {
      const active = document.activeElement;
      const focus = active && edit.contains(active) ? active.getAttribute("aria-label") : null;
      edit.replaceChildren(...[start(), neuronsCard(), callsCard(), questionsCard(), resultsCard(), moreCard()].filter(Boolean));
      if (focus) edit.querySelector(`[aria-label="${CSS.escape(focus)}"]`)?.focus();
      drawPreview();
    } finally {
      drawing = false;
    }
  };
  root.replaceChildren(...[
    info.locked ? h("div", { class: "sf-lock" }, h("span", { class: "chip wait" }, "Locked"),
      h("span", {}, `${info.locked} It was recorded in log.md and is what unblinding checks. To change it, start a new blind set.`)) : null,
    info.readonly && !info.locked ? h("div", { class: "banner" }, `This form uses a setting the builder cannot show (${info.readonly}). `
      + "It is shown read-only here; change it in config.json.") : null,
    h("div", { class: "sf-cols" }, edit, prev)].filter(Boolean));
  drawAll();
  // A save error about one question ("Question 3: ..." or "Question "Axon": ...") also marks that
  // question's card and scrolls to it, so the scorer's form is fixed where the problem is.
  const showError = msg => {
    const byNumber = /^Question (\d+)\b/.exec(msg), byLabel = /^Question "(.+?)"/.exec(msg);
    const j = byNumber ? Number(byNumber[1]) - 1 : byLabel ? m.questions.findIndex(q => q.label.trim() === byLabel[1]) : -1;
    const card = j >= 0 ? edit.querySelectorAll(".sf-q")[j] : null;
    if (!card) return;
    card.classList.add("sf-bad");
    card.scrollIntoView({ block: "center" });
    const level = /level (\d+) is empty/.exec(msg);
    (level && card.querySelector(`[aria-label="Question ${j + 1}: level ${level[1]}"]`) || card.querySelector("input"))
      ?.focus({ preventScroll: true });
  };
  return { el: root, payload: () => off ? null : m, showError };
}

// ---------------------------------------------------------------- project settings

const blankSource = () => ({ name: "", path: "", original_path: "", genotypes: [{ match: "", genotype: "" }],
  default_genotype: "UNKNOWN", exclude: [], from: null });

function previewText(pv, scanned) {
  if (!pv) return "";
  if (!pv.folder_found) return ["warn", "Folder not found. Check the path or choose it with Browse."];
  if (!scanned || pv.images === null) return ["", "Not scanned with these settings yet. Save, then run Scan data to see how the rules sort the images."];
  const g = Object.entries(pv.genotypes).sort((a, b) => a[0].localeCompare(b[0]));
  const parts = g.map(([k, n]) => `${k} ${n}`);
  const unknown = pv.genotypes.UNKNOWN;
  return [unknown ? "warn" : "ok", `${plural(pv.images, "scanned image")}: ${parts.join(", ") || "none"}`
    + (pv.left_out ? `; ${pv.left_out} left out` : "") + (unknown ? `. ${unknown} match no rule.` : "")];
}

async function openProjectSettings(section) {
  let cur;
  try { cur = await api("/api/project-settings", { action: "load" }); }
  catch (e) {
    const go = await ask("config.json cannot be read", `${e.message}\nOpen it in a text editor to fix it?`, [{ value: true, label: "Edit config.json" }]);
    if (go) act("/api/open", { what: "config" });
    return;
  }
  const scanned = st.data.project?.scanned !== null && st.data.project?.scanned !== undefined;
  const model = { project_name: cur.project_name, sources: cur.sources.map((s, i) => ({ ...s, from: i })) };
  if (!model.sources.length) model.sources.push(blankSource());
  const locked = cur.locked;
  const form = formSection(cur.form);
  const payload = () => ({ project_name: model.project_name, sources: model.sources,
    ...(form.payload() ? { form: form.payload() } : {}) });
  const box = h("div", { class: "settings ps" });
  const err = h("div", { class: "m-err", role: "alert", hidden: true });
  let pvTimer = null, previews = [];

  const preview = () => {
    clearTimeout(pvTimer);
    err.hidden = true;           // the message was about the answers before this change
    pvTimer = setTimeout(async () => {
      try {
        const r = await api("/api/project-settings", { action: "preview", ...payload() });
        previews = r.sources;
        $$(".ps .pv").forEach((el, i) => {
          const [cls, text] = previewText(previews[i], r.scanned) || ["", ""];
          el.className = `pv ${cls}`;
          el.textContent = text;
        });
      } catch {     // a half-filled row: old counts would mislead, so say why there are none
        $$(".ps .pv").forEach(el => { el.className = "pv"; el.textContent = "Fill in every name, folder and rule to see the counts."; });
      }
    }, 300);
  };
  const field = (obj, k, attrs = {}) => h("input", { type: "text", class: "f-input", value: obj[k] ?? "", autocomplete: "off",
    spellcheck: "false", ...attrs, oninput: e => { obj[k] = e.target.value; preview(); } });
  const removeBtn = (label, fn) => h("button", { class: "ghost icon rm", title: label, "aria-label": label, onclick: fn }, icon("close", 14));

  const rules = (src, list, a, b, labels, off) => h("div", { class: "rules" },
    src[list].map((r, j) => h("div", { class: "rule" },
      field(r, a, { placeholder: labels[0], "aria-label": `${labels[0]}, rule ${j + 1}`, disabled: off }),
      h("span", { class: "arrow", "aria-hidden": "true" }, "\u2192"),
      field(r, b, { placeholder: labels[1], "aria-label": `${labels[1]}, rule ${j + 1}`, disabled: off }),
      off ? null : removeBtn(`Remove rule ${j + 1}`, () => { src[list].splice(j, 1); draw(); preview(); }))),
    off ? null : h("button", { class: "btn subtle add", onclick: () => { src[list].push({ [a]: "", [b]: "" }); draw(`${list}`); } }, labels[2]));

  const draw = focusLast => {
    const cards = model.sources.map((s, i) => {
      const off = i < locked;
      const [cls, text] = previewText(previews[i], scanned) || ["", ""];
      return h("div", { class: "s-card src" + (off ? " locked" : "") },
        h("div", { class: "src-head" },
          h("label", { class: "f-label", for: `srcName${i}` }, "Folder name"),
          off ? h("span", { class: "chip wait" }, "Locked: scoring has started") : null,
          off || model.sources.length < 2 ? null : removeBtn("Remove this image folder", () => { model.sources.splice(i, 1); draw(); preview(); })),
        field(s, "name", { id: `srcName${i}`, placeholder: "For example: confocal 2026", disabled: off }),
        h("label", { class: "f-label", for: `srcPath${i}` }, "Folder with the images"),
        h("div", { class: "f-row" }, field(s, "path", { id: `srcPath${i}`, placeholder: "C:\\data\\my images (ideally a read-only copy)", disabled: off }),
          off ? null : h("button", { class: "btn", onclick: async () => {
            const r = await api("/api/project-settings", { action: "browse" }).catch(e => (toast(e.message), null));
            if (r?.path) { s.path = r.path; if (!s.name) s.name = r.path.split(/[\\/]/).filter(Boolean).pop(); draw(); preview(); }
          } }, "Browse...")),
        h("div", { class: "f-label" }, "Genotype rules"),
        h("div", { class: "f-help" }, "A pattern is matched against each file's path inside the folder and its name. "
          + "* stands for any text, so *ctrl* matches every file with ctrl in it. The first rule that matches wins."),
        rules(s, "genotypes", "match", "genotype", ["Pattern, e.g. *ctrl*", "Genotype", "Add genotype rule"], off),
        h("div", { class: "f-row dflt" }, h("label", { class: "f-label", for: `srcDef${i}` }, "Genotype of files no rule matches"),
          field(s, "default_genotype", { id: `srcDef${i}`, disabled: off })),
        h("details", { class: "lo", open: s.exclude.length ? true : null },
          h("summary", {}, `Leave out images${s.exclude.length ? ` (${s.exclude.length})` : ""}`),
          h("div", { class: "f-help" }, "Matching images are not coded or scored. They are listed in outputs/excluded.csv with the reason."),
          rules(s, "exclude", "match", "reason", ["Pattern, e.g. *test*", "Reason", "Add a rule"], off)),
        h("div", { class: `pv ${cls}`, "aria-live": "polite" }, text));
    });
    box.replaceChildren(
      h("h4", {}, "Project"),
      h("div", { class: "s-card src" }, h("label", { class: "f-label", for: "psName" }, "Project name"),
        field(model, "project_name", { id: "psName" })),
      h("h4", {}, "Image folders"),
      ...cards,
      h("button", { class: "btn add-src", onclick: () => { model.sources.push(blankSource()); draw("src"); } }, "Add image folder"),
      h("h4", { id: "sfHead" }, "Scoring form"),
      form.el,
      h("div", { class: "f-help sf-config" }, "The marker, statistics and other settings are in config.json: ",
        h("button", { class: "linkish", onclick: () => leave("config") }, "edit config.json"), "."));
    if (focusLast === "src") $$(".ps .src").at(-1)?.querySelector("input")?.focus();
    else if (focusLast) [...document.querySelectorAll(".ps .rules")].find(r => r.contains(document.activeElement))?.querySelector(".rule:last-of-type input")?.focus();
  };
  draw();
  preview();
  // A page in the main area, not an overlay: the form is long, and the project list stays in view.
  const leave = what => {
    clearTimeout(pvTimer);
    st.settingsOpen = null;
    st.pageKey = "";
    if (what === "config") act("/api/open", { what: "config" });
    render();
  };
  const save = async () => {
    let r;
    try { r = await api("/api/project-settings", { action: "save", ...payload() }); }
    catch (e) {
      err.textContent = e.message;
      err.hidden = false;
      form.showError(e.message);
      return;
    }
    toast(r.form_changed ? "Saved. The score form is recorded in log.md and locks at the first score."
      : scanned ? "Saved. Run Scan data again so the new folders and rules are used." : "Saved. Next: Scan data.", "ok");
    leave();
    refresh(true);
  };
  const path = st.data.project.path;
  st.settingsOpen = path;
  const page = h("div", { class: "wrap ps-page", onkeydown: e => { if (e.key === "Escape") leave(); } },
    h("div", { class: "p-head" },
      h("div", {}, h("h2", { id: "psTitle", tabindex: "-1" }, "Project settings"), h("div", { class: "p-path" }, breakable(path)))),
    box,
    h("div", { class: "ps-foot" }, err, h("span", { class: "grow" }),
      h("button", { class: "btn subtle", onclick: () => leave() }, "Cancel"),
      h("button", { class: "primary", onclick: save }, "Save")));
  $("#main").replaceChildren(page);
  $("#main").scrollTop = 0;
  // Only a new project starts in its name box (cursor at the end). Otherwise the page opens
  // without typing into anything, so the name cannot be changed by accident.
  if (section === "form") $("#sfHead")?.scrollIntoView();
  else if (section === "new") {
    const name = $("#psName");
    name?.focus();
    name?.setSelectionRange(name.value.length, name.value.length);
  } else if (section === "folders") $("#srcPath0")?.focus();
  else $("#psTitle")?.focus({ preventScroll: true });
}

// ---------------------------------------------------------------- updates

// Release notes as a short list: "- " lines become bullets, other lines paragraphs.
function notesList(text) {
  const out = [];
  let ul = null;
  const lines = text.split("\n").map(l => l.trim()).filter(Boolean);
  if (/^what'?s new:?$/i.test(lines[0] || "")) lines.shift();     // the dialog already says it
  for (const line of lines) {
    if (/^[-*] /.test(line)) {
      if (!ul) out.push(ul = h("ul", {}));
      ul.append(h("li", {}, line.slice(2)));
    } else {
      ul = null;
      out.push(h("p", {}, line));
    }
  }
  return out;
}

// The update dialog: shown once when the app opens and a newer version exists, and again from
// the pill next to the version number. Rebuilt only when what it shows changes, so a poll does
// not reset the tick box or the scroll position.
function renderUpdate(d) {
  const u = d.update;
  const busy = u.phase === "downloading" || u.phase === "restarting";
  const open = busy || u.phase === "error" || (u.result && !u.result.ok) || ((u.show || st.updOpen) && u.newer);
  const pill = $("#updPill");
  pill.hidden = !(u.newer && !open && !busy);
  pill.textContent = `${u.latest} available`;
  if (u.result?.ok && !st.resultShown) {
    st.resultShown = true;
    toast(`wormscore is now ${d.version}.` + (u.previous ? ` Settings can go back to ${u.previous}.` : ""), "ok");
    api("/api/update", { action: "dismiss" }).catch(() => {});
  }
  const key = JSON.stringify([open, u.phase, u.latest, u.warning, u.kind, u.can_install, u.message,
    u.result, st.updAgree, busy && u.frac !== null ? Math.round(100 * u.frac) : null, st.closing]);
  if (key === st.updKey) return;
  st.updKey = key;
  const m = $("#updModal");
  m.hidden = !open;
  if (!open) return;

  const head = (title, sub) => h("div", { class: "ud-head" },
    h("img", { src: "/static/icon.svg", width: 40, height: 40, alt: "" }),
    h("div", {}, h("h2", { id: "udTitle" }, title), sub));
  const versions = h("div", { class: "ud-ver" }, h("span", { class: "vtag" }, d.version), " \u2192 ",
    h("span", { class: "vtag new" }, u.latest));
  let kids;
  if (busy) {
    const pct = u.frac !== null && u.frac !== undefined ? Math.round(100 * u.frac) : null;
    kids = [head(u.phase === "restarting" ? "Restarting wormscore" : `Updating to ${u.latest}`, versions),
      h("div", { class: "bar" + (u.phase === "downloading" && pct === null ? " indet" : "") },
        h("div", { style: `transform:scaleX(${(u.phase === "restarting" ? 100 : pct ?? 0) / 100})` })),
      h("p", { class: "ud-msg" }, st.closing ? "wormscore is restarting. This window reconnects by itself in a few seconds."
        : u.message || "Starting the download...")];
    // The new version answers on the same address, and refresh() reloads this page when it does.
    if (u.phase === "restarting") st.closing = true;
  } else if (u.phase === "error" || (u.result && !u.result.ok)) {
    kids = [head("The update did not finish", null), h("p", { class: "ud-msg" }, u.phase === "error" ? u.message : u.result.message),
      h("div", { class: "ud-foot" }, h("span", { class: "grow" }),
        h("button", { class: "primary", onclick: () => { st.updOpen = false; act("/api/update", { action: "dismiss" }); } }, "OK"))];
  } else {
    const warn = !!u.warning;
    const later = () => { st.updOpen = false; st.updAgree = false; act("/api/update", { action: "later" }); };
    const skip = () => { st.updOpen = false; act("/api/update", { action: "skip" }); };
    let main;
    if (u.can_install) {
      main = h("button", { class: warn ? "btn" : "primary", disabled: warn && !st.updAgree,
        onclick: () => act("/api/update", { action: "install", confirmed: warn }) }, warn ? "Update anyway" : "Update and restart");
    } else if (u.kind === "pip") {
      main = h("button", { class: "primary", onclick: () => copyText(u.pip_command) }, "Copy command");
    } else {
      main = h("button", { class: "primary", onclick: () => act("/api/update", { action: "releases" }) }, "Open download page");
    }
    kids = [head("Update available", versions),
      u.notes ? h("div", { class: "ud-label" }, "What's new") : null,
      u.notes ? h("div", { class: "ud-notes" }, notesList(u.notes)) : null,
      u.kind === "pip" ? h("p", { class: "ud-msg" }, "This copy was installed with Python. To update, run ",
        h("code", {}, u.pip_command), " in a terminal.") : null,
      warn ? h("div", { class: "ud-warn" }, h("b", {}, "A project is in progress"), h("span", {}, u.warning)) : null,
      warn && u.can_install ? h("label", { class: "check ud-agree" },
        h("input", { type: "checkbox", checked: st.updAgree, onchange: e => { st.updAgree = e.target.checked; render(); } }),
        "I understand the Methods would cite two versions") : null,
      h("div", { class: "ud-foot" },
        h("button", { class: "btn subtle", onclick: skip }, "Skip this version"),
        h("span", { class: "grow" }),
        h("button", { class: warn ? "primary" : "btn", onclick: later }, "Not now"),
        main)];
    m.onkeydown = e => { if (e.key === "Escape") later(); };
  }
  const box = $("#updBox");
  box.replaceChildren(...kids.filter(Boolean));
  (box.querySelector("button.primary:not(:disabled)") || box.querySelector("button"))?.focus();
}

async function copyText(text, done = "Copied. Paste it in a terminal.", failed = `Run this in a terminal: ${text}`) {
  try { await navigator.clipboard.writeText(text); toast(done, "ok"); }
  catch { toast(failed, "ok", true); }
}

// The same two formats GitHub's "Cite this repository" makes from CITATION.cff; keep the title,
// year and DOI in step with it. The DOI covers all versions; the version number says which one.
const CITE_TITLE = "wormscore: blind scoring and fluorescence measurement of neurons in confocal z-stacks";
const CITATIONS = {
  APA: v => `Bansal, A. (2026). ${CITE_TITLE} (Version ${v}) [Computer software]. https://doi.org/10.5281/zenodo.23520610`,
  BibTeX: v => `@software{Bansal_wormscore_2026,
  author = {Bansal, Arjun},
  doi = {10.5281/zenodo.23520610},
  title = {{${CITE_TITLE}}},
  url = {https://github.com/arjunkai/wormscore},
  version = {${v}},
  year = {2026}
}`,
};

// One settings row: title and a short description on the left, its control on the right.
const sRow = (title, desc, control, forId) => h("div", { class: "s-row" },
  h("div", { class: "s-text" }, forId ? h("label", { class: "s-title", for: forId }, title) : h("div", { class: "s-title" }, title),
    desc ? h("div", { class: "s-desc" }, desc) : null),
  control);

function citeBlock(v) {
  let fmt = "APA";
  const text = h("pre", { class: "cite-text", tabindex: "0", "aria-label": "Citation" });
  const copy = h("button", { class: "ghost icon cite-copy", title: "Copy", "aria-label": "Copy citation" }, icon("copy", 16));
  // Tabs as the ARIA pattern expects: one Tab stop (the selected tab), arrow keys move between them.
  const tabs = Object.keys(CITATIONS).map(f => h("button", { role: "tab", class: "cite-tab", id: `cite-tab-${f}`,
    "aria-controls": "cite-panel", onclick: () => show(f) }, f));
  const panel = h("div", { class: "cite-box", role: "tabpanel", id: "cite-panel" }, text, copy);
  const show = (f, focus = false) => {
    fmt = f;
    text.textContent = CITATIONS[f](v);
    text.classList.toggle("mono", f === "BibTeX");
    panel.setAttribute("aria-labelledby", `cite-tab-${f}`);
    for (const t of tabs) {
      const on = t.textContent === f;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      if (on && focus) t.focus();
    }
  };
  const onKey = e => {
    const names = Object.keys(CITATIONS), i = names.indexOf(fmt);
    const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: names.length - 1 }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    show(names[(to + names.length) % names.length], true);
  };
  copy.onclick = async () => {
    try { await navigator.clipboard.writeText(CITATIONS[fmt](v)); }
    catch { return toast(`Could not copy. Select the citation and press ${MOD}+C.`, "error"); }
    copy.replaceChildren(icon("check", 16));
    copy.setAttribute("aria-label", "Copied");
    setTimeout(() => { copy.replaceChildren(icon("copy", 16)); copy.setAttribute("aria-label", "Copy citation"); }, 1500);
  };
  show(fmt);
  return h("div", { class: "cite" },
    h("div", { class: "cite-tabs", role: "tablist", "aria-label": "Citation format", onkeydown: onKey }, tabs), panel);
}

async function openSettings() {
  const u = st.data.update, v = st.data.version;
  const statusText = x => x.checking ? "Checking..." : x.error ? x.error
    : x.newer ? `${x.latest} is available.` : x.latest ? "You have the latest version." : "Not checked yet.";
  // Buttons sit next to what they act on; picking one closes Settings and acts on it below.
  let pick = null;
  const choose = c => () => { pick = c; $("#mCancel").click(); };
  // Check now works inside Settings. If it finds a newer version, Settings closes and the update
  // dialog opens by itself; otherwise the status line just changes.
  const status = h("span", {}, statusText(u));
  const checkNow = async () => {
    verBtn.disabled = true;
    verBtn.textContent = status.textContent = "Checking...";
    try { await api("/api/update", { action: "check" }); } catch (e) { toast(e.message); }
    const now = await api("/api/state").catch(() => null);
    if (now?.update?.newer) {        // close first, so the update dialog keeps the focus
      pick = "found";
      return $("#mCancel").click();
    }
    await refresh(true);
    status.textContent = statusText(st.data.update);
    verBtn.disabled = false;
    verBtn.textContent = "Check now";
  };
  const verBtn = u.newer
    ? h("button", { class: "primary", onclick: choose("see") }, "See update")
    : h("button", { class: "btn", disabled: u.checking, onclick: checkNow }, "Check now");
  const box = h("div", { class: "settings" },
    h("h4", {}, "Updates"),
    h("div", { class: "s-card" },
      sRow("Check for updates at start", "Asks GitHub for the latest version. Nothing about you or your projects is sent.",
        h("input", { type: "checkbox", role: "switch", class: "switch", id: "updSwitch", checked: u.enabled,
          onchange: e => act("/api/settings", { update_check: e.target.checked }) }), "updSwitch"),
      sRow(`Version ${v}`, status, verBtn),
      u.kind === "pip" ? sRow("Installed with Python", h("span", {}, "Update with ", h("code", {}, u.pip_command)), null) : null,
      u.previous ? sRow("Previous version", `${u.previous} is still on this computer. Projects and scores are not affected.`,
        h("button", { class: "btn", onclick: choose("rollback") }, `Go back to ${u.previous}`)) : null),
    h("h4", {}, "About"),
    h("div", { class: "s-card" },
      sRow("License", "MIT. Free and open source, with no warranty.", null),
      h("div", { class: "s-row s-col" },
        h("div", { class: "s-text" }, h("div", { class: "s-title" }, "Cite wormscore"),
          h("div", { class: "s-desc" }, "Cite the version you scored with. log.md records it.")),
        citeBlock(v))));
  await ask("Settings", box, [], "Close", { wide: true });      // wide: rows and the citation wrap less
  if (pick === "found") return refresh(true);      // the update dialog opens from the new state
  if (pick === "see") {
    st.updOpen = true;
    st.updKey = "";
    return render();
  }
  if (pick === "rollback") {
    const go = await ask(`Go back to ${u.previous}?`,
      `wormscore closes and reopens as ${u.previous}. ${st.data.version} is kept, and projects and scores are not changed.`,
      [{ value: true, label: `Go back to ${u.previous}` }]);
    if (go) act("/api/update", { action: "rollback" });
  }
}

function render() {
  const d = st.data;
  if (!d) return;
  renderUpdate(d);
  document.documentElement.dataset.theme = d.theme;
  $("#themeBtn").replaceChildren(icon(d.theme === "dark" ? "sun" : "moon", 18));
  $("#themeBtn").title = d.theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
  $("#ver").textContent = d.version;
  // "I have finished scoring" is about one project: switching projects clears it.
  const path = d.project?.path ?? null;
  if (path !== st.tickedFor) { st.tickedFor = path; st.finished = false; }
  // The state is polled every few seconds. Rebuilding the page each time would throw keyboard
  // focus back to the top and make screen readers read it again, so only redraw on a change.
  const key = JSON.stringify([d.recent, d.project, d.job, st.finished, st.showLog, st.log.length]);
  if (key === st.pageKey) return;
  st.pageKey = key;
  const focused = document.activeElement;
  renderSide(d);
  // Project settings stay open while the page polls; picking another project closes them.
  if (st.settingsOpen && st.settingsOpen !== path) st.settingsOpen = null;
  if (!st.settingsOpen) renderMain(d);
  if (focused && !focused.isConnected) refocus(focused.dataset?.k);
}

// ---------------------------------------------------------------- actions

async function sideAction(which) {
  if (which === "demo") {
    const real = await ask("Try a demo", "Pick the kind of demo, then choose an empty folder for it.", [
      { value: "sim", label: "Simulated worms", desc: "20 heads with known normal, dim and missing cells. Instant, works offline." },
      { value: "real", label: "Real worm images", desc: "4 L1 heads from Sun and Hobert 2021 (Zenodo, CC BY 4.0). Downloads about 150 MB once." },
    ]);
    if (!real) return;
    st.log = []; st.since = 0;
    return act("/api/demo", { real: real === "real" });
  }
  const r = await act(`/api/${which}`, {});
  if (r?.settings) openProjectSettings("new");      // a new project starts with its name and image folders
}

async function scan() {
  const p = st.data.project;
  if (p.scanned !== null && p.scanned !== undefined) {
    const go = await ask("Scan again?", "Scanning checks every file again, which takes a while on large data. Do it if you added or removed files.",
      [{ value: true, label: "Scan again" }]);
    if (!go) return;
  }
  startStep({ step: "scan" });
}

async function build() {
  const p = st.data.project;
  let mode = "new";
  if (p.coded !== null && p.coded !== undefined) {
    if (p.scoring_started) {
      mode = await ask("Scoring has already started",
        "Existing codes never change once scoring has started. Do not score while this runs.", [
          { value: "add", label: "Add new images", desc: "Run Scan data first. Existing codes and images stay the same; new images get codes after the last one. They form a later batch, which the Methods text reports." },
          { value: "keep", label: "Rebuild, keeping the current codes", desc: "Only if settings changed. Takes a while." },
        ]);
    } else {
      mode = await ask("A blind set already exists",
        "You can go straight to scoring. Rebuild only if images or settings changed. Do not score while it runs.", [
          { value: "keep", label: "Rebuild, keeping the current codes", desc: "Only if settings changed." },
          { value: "reassign", label: "Rebuild with NEW codes", danger: true, desc: "Only if images were added or removed. Allowed only because nothing is scored yet." },
        ]);
    }
    if (!mode) return;
  }
  startStep({ step: "build", mode });
}

async function score() {
  const r = await act("/api/score", {});
  if (r?.url) toast("The scoring window is open. Keep this app open while you score.", "ok");
}

async function unblind() {
  const p = st.data.project;
  let anyway = false;
  if (p.scored < p.min_scored) {
    anyway = await ask("Fewer images scored than planned",
      `Only ${p.scored} images are scored; the project plan says at least ${p.min_scored}.\nUnblinding anyway is recorded in log.md.`,
      [{ value: true, label: "Unblind anyway", danger: true }]);
    if (!anyway) return;
  } else {
    const go = await ask("Unblind now?", "This reveals which genotype each code was. Do it only when scoring is finished; scores saved later are a separate, unblinded look.",
      [{ value: true, label: "Unblind" }]);
    if (!go) return;
  }
  startStep({ step: "unblind", finished: true, anyway: anyway === true });
}

async function startStep(body) {
  st.log = []; st.since = 0; st.showLog = false;
  await act("/api/run", body);
}

// ---------------------------------------------------------------- polling

// Connection to the wormscore program. Like chat and editor apps: a short "Reconnecting..."
// first, retrying every 2 s and at once when the computer wakes or the window gets focus; only
// if the program stays away does the window explain what happened, with a button to start it.
const GONE_AFTER_MS = 8000;
const OWN_WINDOW = new URLSearchParams(location.search).get("window") === "own";

function leftOverWindow() {
  clearTimeout(st.timer);
  $("#reconn").hidden = true;
  $("#goneModal").hidden = true;
  $("#movedModal").hidden = false;
}

function connectionLost() {
  if (st.closing) return;              // an update is restarting the app; its dialog says so
  st.downSince ??= Date.now();
  const long = Date.now() - st.downSince > GONE_AFTER_MS;
  $("#reconn").hidden = long;
  $("#goneModal").hidden = !long;
  $("#goneStart").hidden = !st.data?.can_start;
  $("#goneHow").textContent = st.data?.can_start
    ? "Click Start wormscore, then Open when your browser asks. This window reconnects by itself."
    : "Open wormscore again the way you usually do. This window reconnects by itself.";
}

function connectionBack() {
  if (st.downSince === null || st.downSince === undefined) return;
  const long = Date.now() - st.downSince > GONE_AFTER_MS;
  st.downSince = null;
  $("#reconn").hidden = true;
  $("#goneModal").hidden = true;
  if (long) toast("Connected to wormscore again.", "ok");
}

async function refresh(soon) {
  try {
    const res = await fetch(`/api/state?since=${st.since}`);
    const d = await res.json();
    if (!res.ok) throw Object.assign(new Error(d.error || `Request failed (${res.status})`), { fromApp: true });
    st.stateError = null;
    // Another version answers on this address (after an update or a go back): load its page.
    if (st.data && d.version !== st.data.version) return location.reload();
    // wormscore now shows its pages in its own window; this one is left over from before.
    if (d.own_window && !OWN_WINDOW) return leftOverWindow();
    connectionBack();
    if (d.job.next < st.since) { st.log = []; st.since = 0; }
    st.log.push(...d.job.lines);
    st.since = d.job.next;
    if (st.lastRunning && !d.job.running && d.job.outcome !== "done") st.showLog = true;
    st.lastRunning = d.job.running;
    st.data = d;
    render();
  } catch (e) {
    if (e.fromApp) {
      connectionBack();
      if (e.message !== st.stateError) toast((st.stateError = e.message));
    } else {
      connectionLost();       // polling goes on, so the window recovers by itself
    }
  }
  clearTimeout(st.timer);
  const fast = st.data?.job.running || st.data?.update.phase === "downloading" || st.data?.update.checking;
  st.timer = setTimeout(refresh, st.downSince ? 2000 : fast ? 700 : soon ? 1500 : 4000);
}

// Waking the computer, getting the network back or clicking into the window: check at once.
for (const ev of ["focus", "online"]) window.addEventListener(ev, () => refresh(true));
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(true); });
$("#goneStart").onclick = () => { location.href = "wormscore://start"; };

// Tab and Shift+Tab stay inside an open dialog.
document.addEventListener("keydown", e => {
  if (e.key !== "Tab") return;
  const m = $$(".modal").reverse().find(x => !x.hidden);
  if (!m) return;
  const els = [...m.querySelectorAll("button, input, a[href], [tabindex]")].filter(x => !x.disabled && x.offsetParent !== null);
  const i = els.indexOf(document.activeElement);
  if (!els.length) e.preventDefault();
  else if (e.shiftKey && i <= 0) { els.at(-1).focus(); e.preventDefault(); }
  else if (!e.shiftKey && (i === -1 || i === els.length - 1)) { els[0].focus(); e.preventDefault(); }
});

$$("[data-act]").forEach(b => (b.onclick = () => sideAction(b.dataset.act)));
$("#guideBtn").onclick = () => act("/api/guide", {});
$("#updPill").onclick = () => { st.updOpen = true; st.updKey = ""; render(); };
$("#settingsBtn").replaceChildren(icon("gear", 18));
$("#settingsBtn").onclick = () => st.data && openSettings();
$("#themeBtn").onclick = () => act("/api/theme", { theme: st.data?.theme === "dark" ? "light" : "dark" });
refresh();
