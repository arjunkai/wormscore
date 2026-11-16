// The per-neuron and per-animal questions of the score form. Shared by the scoring page (app.js) and the live preview
// in Project settings (home.js), so the preview shows exactly what the scorer will see.
// h is the page's element helper: h(tag, attrs, ...children).
"use strict";

const wsForm = (() => {
  // defs (optional): {answer: {text, image}}; an answer's definition is also its tooltip.
  const radios = (h, name, options, defs = {}) =>
    options.map(o => h("label", { title: defs?.[o]?.text || null }, h("input", { type: "radio", name, value: o }), ` ${o}`));

  // One extra question: its text, then the answers. "choose" stays until it is answered. A scale
  // lists its levels best first, worst last; a count lists its numbers.
  const question = (h, name, q) => h("label", { class: "nq", "data-q": q.id },
    h("span", { class: "nq-text" }, q.label, q.required ? h("span", { class: "nq-req", title: "Required" }, " *") : null),
    h("select", { name, ...(q.type === "scale" ? { title: "Levels from best (top) to worst (bottom)" } : {}) },
      h("option", { value: "" }, "choose"), q.options.map(o => h("option", { title: q.definitions?.[o]?.text || null }, o))));

  const neurons = (h, sc) => sc.neurons.map((name, i) => {
    const n = i + 1;
    return h("fieldset", { class: `neuron n${n}` },
      h("legend", {}, name),
      h("div", { class: "calls" }, radios(h, `n${n}_call`, sc.calls, sc.call_definitions)),
      sc.sides.length ? h("select", { name: `n${n}_side`, "aria-label": `${name}: side` },
        h("option", { value: "unknown" }, "side unknown"), sc.sides.map(s => h("option", {}, s))) : null,
      sc.neuron_questions.map(q => question(h, `n${n}_${q.id}`, q)));
  });

  // Questions asked once per image (wormscore 1.4.0), after the neurons. [] if there are none.
  const animal = (h, sc) => (sc.animal_questions || []).length
    ? [h("fieldset", { class: "animal-qs" }, h("legend", {}, "This animal"),
        sc.animal_questions.map(q => question(h, `a_${q.id}`, q)))]
    : [];

  // What each answer means (wormscore 1.9.0): the definition and reference image of each call and
  // each Choice or Scale answer, in one box above the neurons. [] if no answer has one. Open by
  // default; closing it is remembered on this computer.
  const OPEN_KEY = "wormscore.meaningsOpen";
  const has = d => d && (d.text || d.image);
  function meanings(h, sc) {
    const groups = [];
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
      if (rows.length) groups.push(h("div", { class: "m-group" }, h("div", { class: "m-head" }, title), ...rows));
    };
    group("Call", sc.calls, sc.call_definitions);
    for (const q of [...(sc.neuron_questions || []), ...(sc.animal_questions || [])])
      group(q.label, q.options, q.definitions);
    if (!groups.length) return [];
    let open = true;
    try { open = localStorage.getItem(OPEN_KEY) !== "0"; } catch { /* storage blocked: stay open */ }
    return [h("details", { class: "meanings", open, ontoggle: e => {
      try { localStorage.setItem(OPEN_KEY, e.target.open ? "1" : "0"); } catch { /* not remembered */ }
    } }, h("summary", {}, "What each answer means"), ...groups)];
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
        const sel = form.querySelector(`[name="n${i + 1}_${q.id}"]`);
        if (!sel) continue;
        const skip = skipped(q, call);
        sel.closest(".nq").hidden = skip;
        if (skip) sel.value = "";
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

  return { radios, neurons, animal, meanings, zoom, syncSkips, missingRequired, missingAnimal };
})();
