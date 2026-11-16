// The per-neuron and per-animal questions of the score form. Shared by the scoring page (app.js) and the live preview
// in Project settings (home.js), so the preview shows exactly what the scorer will see.
// h is the page's element helper: h(tag, attrs, ...children).
"use strict";

const wsForm = (() => {
  // defs (optional): {answer: {text, image}}; an answer's definition is also its tooltip.
  const radios = (h, name, options, defs = {}) =>
    options.map((o, i) => h("label", { title: defs?.[o]?.text || null }, h("input", { type: "radio", name, value: o }), ` ${o}`,
      i < 9 ? h("span", { class: "opt-k", "aria-hidden": "true" }, String(i + 1)) : null));
  const MAX_BUTTONS = 7;       // more answers than this stay a list

  // One extra question: its text, then its answers as buttons, like the call (wormscore 1.11.0), so
  // it is answered with one click or one number key. A long list (a count to 20, a long scale)
  // stays a list, where "choose" stays until it is answered.
  const question = (h, name, q) => {
    const text = h("span", { class: "nq-text", id: `${name}_t` }, q.label,
      q.required ? h("span", { class: "nq-req", title: "Required" }, " *") : null);
    if (q.options.length <= MAX_BUTTONS)
      return h("div", { class: "nq", "data-q": q.id, role: "radiogroup", "aria-labelledby": `${name}_t` },
        text, h("div", { class: "nq-opts" }, radios(h, name, q.options, q.definitions)));
    return h("label", { class: "nq", "data-q": q.id }, text,
      h("select", { name, ...(q.type === "scale" ? { title: "Levels from best (top) to worst (bottom)" } : {}) },
        h("option", { value: "" }, "choose"), q.options.map(o => h("option", { title: q.definitions?.[o]?.text || null }, o))));
  };

  const neurons = (h, sc) => sc.neurons.map((name, i) => {
    const n = i + 1;
    return h("fieldset", { class: `neuron n${n}` },
      h("legend", {}, name),
      h("div", { class: "calls" }, radios(h, `n${n}_call`, sc.calls, sc.call_definitions)),
      // the side sits on the neuron's title line, so it costs no row of its own
      sc.sides.length ? h("select", { name: `n${n}_side`, class: "side", "aria-label": `${name}: side` },
        h("option", { value: "unknown" }, "side ?"), sc.sides.map(s => h("option", {}, s))) : null,
      sc.neuron_questions.map(q => question(h, `n${n}_${q.id}`, q)));
  });

  // Questions asked for each side (wormscore 1.14.0), after the neurons: the question once, then a
  // Left and a Right row of answers, saved as left_<id> and right_<id>. [] if there are none.
  const SIDES = [["left", "Left"], ["right", "Right"]];
  const sideQuestion = (h, q) => {
    const tid = `side_${q.id}_t`;
    return h("div", { class: "nq nq-side", "data-q": q.id },
      h("span", { class: "nq-text", id: tid }, q.label, q.required ? h("span", { class: "nq-req", title: "Required" }, " *") : null),
      SIDES.map(([s, label]) => {
        const name = `${s}_${q.id}`, sid = `${name}_s`;
        const tag = h("span", { class: "nq-sidek", id: sid }, label);
        return q.options.length <= MAX_BUTTONS
          ? h("div", { class: "nq-row", role: "radiogroup", "aria-labelledby": `${tid} ${sid}` },
            tag, h("div", { class: "nq-opts" }, radios(h, name, q.options, q.definitions)))
          : h("label", { class: "nq-row" }, tag, h("select", { name, "aria-labelledby": `${tid} ${sid}` },
            h("option", { value: "" }, "choose"), q.options.map(o => h("option", { title: q.definitions?.[o]?.text || null }, o))));
      }));
  };
  const sides = (h, sc) => (sc.side_questions || []).length
    ? [h("fieldset", { class: "side-qs" }, h("legend", {}, "Left and right"), sc.side_questions.map(q => sideQuestion(h, q)))]
    : [];

  // Questions asked once per image (wormscore 1.4.0), after the neurons. [] if there are none.
  const animal = (h, sc) => (sc.animal_questions || []).length
    ? [h("fieldset", { class: "animal-qs" }, h("legend", {}, "This animal"),
        sc.animal_questions.map(q => question(h, `a_${q.id}`, q)))]
    : [];

  // What each answer means (wormscore 1.9.0): the definition and reference image of each call and
  // each Choice or Scale answer, in one box above the neurons. [] if no answer has one. On the
  // scoring page it starts closed, so the questions fit without scrolling (each answer's
  // definition is also its tooltip); opening it is remembered. The criteria preview shows it open.
  const OPEN_KEY = "wormscore.meaningsOpen";
  const has = d => d && (d.text || d.image);
  function meanings(h, sc, opts = {}) {
    const groups = [];
    let n = 0;
    const group = (title, answers, defs) => {
      const rows = answers.filter(a => has(defs?.[a])).map(a => {
        const d = defs[a];
        return h("div", { class: "m-row" },
          h("span", { class: "m-term" }, a),
          h("span", { class: "m-text" }, d.text || ""),
          d.image ? h("button", { type: "button", class: "m-thumb", title: `Reference image: ${a}`,
            "aria-label": `Show the reference image for ${a}`, onclick: () => zoom(h, d.image, `${title}: ${a}`, d.text) },
            h("img", { src: "/" + d.image, alt: "", loading: "lazy" })) : null);
      });
      n += rows.length;
      if (rows.length) groups.push(h("div", { class: "m-group" }, h("div", { class: "m-head" }, title), ...rows));
    };
    group("Call", sc.calls, sc.call_definitions);
    for (const q of [...(sc.neuron_questions || []), ...(sc.side_questions || []), ...(sc.animal_questions || [])])
      group(q.label, q.options, q.definitions);
    if (!groups.length) return [];
    let open = !!opts.open;
    if (!opts.open) try { open = localStorage.getItem(OPEN_KEY) === "1"; } catch { /* storage blocked: closed */ }
    return [h("details", { class: "meanings", open, ontoggle: e => {
      if (!opts.open) try { localStorage.setItem(OPEN_KEY, e.target.open ? "1" : "0"); } catch { /* not remembered */ }
    } }, h("summary", {}, `What each answer means (${n})`), ...groups)];
  }

  // An X that closes, drawn as a line icon so it matches the text colour in both themes.
  function closeX(h, onclick) {
    const b = h("button", { type: "button", class: "m-close", title: "Close (Esc)", "aria-label": "Close", onclick });
    b.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" '
      + 'stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>';
    return b;
  }

  // A reference image, larger, in a panel in the bottom-left corner. It is not modal and has no
  // backdrop, so the image being scored stays in view to compare with, and its keys keep working.
  // One at a time: opening another replaces it. Escape or the X closes it.
  function zoom(h, image, title, text) {
    document.querySelector("dialog.m-zoom")?.close();
    const opener = document.activeElement;
    const onKey = e => { if (e.key === "Escape") { e.preventDefault(); dlg.close(); } };
    const dlg = h("dialog", { class: "m-zoom", "aria-label": `Reference image: ${title}` },
      h("div", { class: "m-zoom-head" }, h("b", {}, title), closeX(h, () => dlg.close())),
      h("img", { src: "/" + image, alt: `Reference image: ${title}` }),
      text ? h("p", {}, text) : null,
      h("p", { class: "m-zoom-note" }, "Compare shape and position. Its brightness depends on how it was saved, "
        + "not on this project's display range."));
    dlg.addEventListener("close", () => {
      document.removeEventListener("keydown", onKey, true);
      dlg.remove();
      if (dlg.contains(document.activeElement) || document.activeElement === document.body) opener?.focus?.();
    });
    document.addEventListener("keydown", onKey, true);
    document.body.append(dlg);
    dlg.show();
    dlg.querySelector(".m-close").focus();
  }

  const callOf = (form, n) => form.querySelector(`input[name="n${n}_call"]:checked`)?.value;
  const skipped = (q, call) => !!call && (q.skip_when_calls || []).includes(call);

  // A question skipped for this neuron's call is hidden and its answer cleared (saved empty).
  function syncSkips(form, sc) {
    sc.neurons.forEach((_, i) => {
      const call = callOf(form, i + 1);
      for (const q of sc.neuron_questions) {
        const els = [...form.querySelectorAll(`[name="n${i + 1}_${q.id}"]`)];
        if (!els.length) continue;
        const skip = skipped(q, call);
        els[0].closest(".nq").hidden = skip;
        if (skip) for (const el of els) { if (el.type === "radio") el.checked = false; else el.value = ""; }
      }
    });
  }

  // Neuron numbers with a required question left unanswered (skipped questions do not count).
  function missingRequired(values, sc) {
    const out = [];
    sc.neurons.forEach((_, i) => {
      const n = i + 1, call = values[`n${n}_call`];
      if (sc.neuron_questions.some(q => q.required && !skipped(q, call) && !values[`n${n}_${q.id}`])) out.push(n);
    });
    return out;
  }

  // True if a required question about the whole animal is unanswered.
  const missingAnimal = (values, sc) => (sc.animal_questions || []).some(q => q.required && !values[`a_${q.id}`]);

  // True if a required question asked for each side is unanswered for either side.
  const missingSide = (values, sc) => (sc.side_questions || []).some(q => q.required && SIDES.some(([s]) => !values[`${s}_${q.id}`]));

  return { radios, neurons, sides, animal, meanings, zoom, syncSkips, missingRequired, missingSide, missingAnimal };
})();
