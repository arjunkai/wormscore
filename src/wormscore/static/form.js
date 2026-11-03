// The per-neuron part of the score form. Shared by the scoring page (app.js) and the live preview
// in Project settings (home.js), so the preview shows exactly what the scorer will see.
// h is the page's element helper: h(tag, attrs, ...children).
"use strict";

const wsForm = (() => {
  const radios = (h, name, options) =>
    options.map(o => h("label", {}, h("input", { type: "radio", name, value: o }), ` ${o}`));

  // One extra question: its text, then the answers. "choose" stays until it is answered.
  const question = (h, n, q) => h("label", { class: "nq", "data-q": q.id },
    h("span", { class: "nq-text" }, q.label, q.required ? h("span", { class: "nq-req", title: "Required" }, " *") : null),
    h("select", { name: `n${n}_${q.id}` }, h("option", { value: "" }, "choose"), q.options.map(o => h("option", {}, o))));

  const neurons = (h, sc) => sc.neurons.map((name, i) => {
    const n = i + 1;
    return h("fieldset", { class: `neuron n${n}` },
      h("legend", {}, name),
      h("div", { class: "calls" }, radios(h, `n${n}_call`, sc.calls)),
      sc.sides.length ? h("select", { name: `n${n}_side`, "aria-label": `${name}: side` },
        h("option", { value: "unknown" }, "side unknown"), sc.sides.map(s => h("option", {}, s))) : null,
      sc.neuron_questions.map(q => question(h, n, q)));
  });

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

  return { radios, neurons, syncSkips, missingRequired };
})();
