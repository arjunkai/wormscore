// Home screen: projects, the four steps, progress and the safety questions.
// Every action calls the same code as the command line (see home.py).
"use strict";

const $ = s => document.querySelector(s);
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
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
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
  }, icon("close", 14)) : null);
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

function ask(title, body, choices, cancel = "Cancel") {
  const opener = document.activeElement, openerKey = opener?.dataset?.k;
  return new Promise(resolve => {
    $("#mTitle").textContent = title;
    if (body instanceof Node) $("#mBody").replaceChildren(body);
    else $("#mBody").textContent = body;
    $("#mCancel").textContent = cancel;
    // A plain yes/no question gets a small box with its button next to Cancel; choices that
    // need explaining are listed as cards.
    const simple = !(body instanceof Node) && choices.every(c => !c.desc);
    $("#modal .dialog").classList.toggle("sm", simple);
    const box = simple ? $("#mFootChoices") : $("#mChoices");
    (simple ? $("#mChoices") : $("#mFootChoices")).replaceChildren();
    box.replaceChildren(...choices.map(c => simple
      ? h("button", { class: c.danger ? "btn danger" : "primary", onclick: () => close(c.value) }, c.label)
      : h("button", { class: "choice" + (c.danger ? " danger" : ""), onclick: () => close(c.value) },
        h("b", {}, c.label), c.desc ? h("span", {}, c.desc) : null)));
    const m = $("#modal");
    const close = v => {
      m.hidden = true;
      document.removeEventListener("keydown", esc);
      refocus(openerKey, opener);
      resolve(v);
    };
    const esc = e => { if (e.key === "Escape") close(null); };
    $("#mCancel").onclick = () => close(null);
    document.addEventListener("keydown", esc);
    m.hidden = false;
    // A choice that cannot be undone never gets the focus: one Enter press must not pick it.
    (choices.some(c => c.danger) ? $("#mCancel") : box.querySelector("button"))?.focus();
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
  if (p.unblinded) return "Unblinded";
  if (p.coded !== null && p.coded !== undefined) return `Scored ${p.scored} of ${p.coded}`;
  if (p.scanned !== null && p.scanned !== undefined) return "Scanned; blind set not built";
  return "Not scanned yet";
}

// ---------------------------------------------------------------- render

// The X only takes a project off this list; people reasonably fear it deletes the folder.
async function forgetProject(name, path) {
  const go = await ask(`Remove "${name}" from the list?`,
    "The folder stays on your computer. Use Open project to add it back.",
    [{ value: true, label: "Remove" }]);
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
  const job = d.job;
  const busy = job.running;
  const runningHere = busy && job.project === p.path;
  const stepRunning = name => runningHere && job.title.toLowerCase().includes(name);

  const scanned = p.scanned !== null && p.scanned !== undefined;
  const built = p.coded !== null && p.coded !== undefined;
  const allScored = built && p.coded > 0 && p.scored >= p.coded;
  const ok = p.has_config && !p.config_error;

  const building = stepRunning("build") || stepRunning("rebuild") || stepRunning("add new");
  const s1 = stepRunning("scan") ? "running" : scanned ? "done" : "next";
  const s2 = building ? "running" : built ? "done" : scanned ? "next" : "wait";
  const s3 = building ? "wait" : built ? (allScored ? "done" : "next") : "wait";
  const s4 = stepRunning("unblind") ? "running" : p.unblinded ? "done" : allScored ? "next" : "wait";

  const steps = h("ol", { class: "steps" },
    stepCard(1, s1, "Scan data", "List every file and image and identify channels from metadata.",
      scanned ? `${plural(p.scanned, "raw image")} found. Scan again if you added files.` : null,
      [h("button", { class: s1 === "next" ? "primary" : "btn", "data-k": "step1", disabled: busy || !ok, onclick: scan },
        scanned ? "Scan again" : "Scan data")]),
    stepCard(2, s2, "Build blind set", "Apply the inclusion rules, give every image a random code, and record the analysis plan in log.md.",
      building ? "Building..." : built ? `${plural(p.coded, "image")} coded.` : null,
      [h("button", { class: s2 === "next" ? "primary" : "btn", "data-k": "step2", disabled: busy || !ok || !scanned, onclick: build },
        !built ? "Build blind set" : p.scoring_started ? "Add or rebuild..." : "Rebuild...")]),
    stepCard(3, s3, "Score", "Score each coded image in the scoring window. File names and genotypes stay hidden.",
      building ? "Available when the blind set is built." : built ? `Scored ${p.scored} of ${p.coded}.` : null,
      [h("button", { class: s3 === "next" ? "primary" : "btn", "data-k": "step3", disabled: busy || !built, onclick: score },
        p.scored ? "Continue scoring" : "Start scoring")],
      built && p.coded && !building ? h("div", { class: "mini" }, h("div", { style: `width:${Math.min(100, 100 * p.scored / p.coded)}%` })) : null),
    stepCard(4, s4, "Unblind", "Reveal which genotype each code was, then write the results tables and a draft Methods text. Do this only when scoring is finished.",
      p.unblinded ? "Results are in the outputs\\unblinded folder." : null,
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
        h("button", { class: "btn", "data-k": "config", disabled: !p.has_config, onclick: () => act("/api/open", { what: "config" }) }, "Edit config.json"))),
    !p.has_config ? h("div", { class: "banner" }, "This folder has no config.json. Open another project or make a new one.") : null,
    p.config_error ? h("div", { class: "banner" }, "config.json needs a fix before the next step:\n" + p.config_error) : null,
    busy && !runningHere ? h("div", { class: "banner" }, `"${job.title}" is running for another project.`) : null,
    activity(job),
    steps));
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
    h("div", { class: "bar" + (job.running && pct === null ? " indet" : "") }, h("div", { style: `width:${width}%` })),
    h("div", { class: "a-last", title: last }, last),
    h("button", { class: "btn subtle log-toggle", "data-k": "log", onclick: () => { st.showLog = !st.showLog; render(); } },
      st.showLog ? "Hide log" : "Show log"),
    st.showLog ? h("pre", { class: "log", id: "log" }, st.log.join("\n")) : null);
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
    h("div", {}, h("h3", { id: "udTitle" }, title), sub));
  const versions = h("div", { class: "ud-ver" }, h("span", { class: "vtag" }, d.version), " \u2192 ",
    h("span", { class: "vtag new" }, u.latest));
  let kids;
  if (busy) {
    const pct = u.frac !== null && u.frac !== undefined ? Math.round(100 * u.frac) : null;
    kids = [head(u.phase === "restarting" ? "Restarting wormscore" : `Updating to ${u.latest}`, versions),
      h("div", { class: "bar" + (u.phase === "downloading" && pct === null ? " indet" : "") },
        h("div", { style: `width:${u.phase === "restarting" ? 100 : pct ?? 0}%` })),
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

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied. Paste it in a terminal.", "ok"); }
  catch { toast(`Run this in a terminal: ${text}`, "ok", true); }
}

async function openSettings() {
  const u = st.data.update;
  const status = u.checking ? "Checking..." : u.error ? u.error
    : u.newer ? `wormscore ${u.latest} is available. You have ${st.data.version}.`
    : u.latest ? `You have the latest version (${st.data.version}).` : "Not checked yet.";
  const box = h("div", { class: "settings" },
    h("label", { class: "check" },
      h("input", { type: "checkbox", checked: u.enabled, onchange: e => act("/api/settings", { update_check: e.target.checked }) }),
      "Check for updates when wormscore starts"),
    h("p", { class: "fine" }, "The check asks GitHub for the latest release. Nothing about you or your projects is sent."),
    h("p", { class: "fine" }, status),
    u.kind === "pip" ? h("p", { class: "fine" }, "Installed with Python. Update with: ", h("code", {}, u.pip_command)) : null);
  const choices = [{ value: "check", label: "Check now" }];
  if (u.previous) choices.push({ value: "rollback", label: `Go back to ${u.previous}`, danger: true,
    desc: `Closes wormscore, swaps the kept copy of ${u.previous} back in and reopens it. ${st.data.version} is kept in its place.` });
  const pick = await ask("Settings", box, choices, "Close");
  if (pick === "check") {
    await act("/api/update", { action: "check" });
    return openSettings();
  }
  if (pick === "rollback") {
    const go = await ask(`Go back to ${u.previous}?`, "wormscore closes and reopens as the older version. Projects and scores are not changed.",
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
  renderMain(d);
  if (focused && !focused.isConnected) refocus(focused.dataset?.k);
}

// ---------------------------------------------------------------- actions

async function sideAction(which) {
  if (which === "demo") {
    const real = await ask("Try a demo", "Pick the kind of demo, then choose an empty folder for it (outside OneDrive is best).", [
      { value: "sim", label: "Simulated worms", desc: "20 heads with known normal, dim and missing cells. Instant, works offline." },
      { value: "real", label: "Real worm images", desc: "4 L1 heads from Sun and Hobert 2021 (Zenodo, CC BY 4.0). Downloads about 150 MB once." },
    ]);
    if (!real) return;
    st.log = []; st.since = 0;
    return act("/api/demo", { real: real === "real" });
  }
  return act(`/api/${which}`, {});
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
