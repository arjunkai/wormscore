// Short definitions of the terms wormscore uses, shown by a small "?" button next to the term
// (a toggletip: click or Enter shows it, Escape or a click elsewhere hides it; screen readers
// hear it through a live region). Shared by the home screen and the scoring window.
// One sentence or two each, plain words, no em dashes.
const WS_TERMS = {
  scan: "Scan data reads each image file's metadata (microscope, channels, planes) to list every image. It never changes your files.",
  criteria: "What the scorer answers for each image, and which answers count as a defect. They are written to log.md before scoring and locked at the first score, so they cannot change after you have seen data.",
  blind_set: "Copies of your images under random codes (W0001, W0002, ...), so you score without knowing genotype or file name. The key linking codes to files stays sealed until Unblind.",
  code: "The random name an image gets in the blind set, such as W0012. It says nothing about genotype or the order images were taken.",
  score: "You answer the questions for one coded image at a time. Each score is saved when you click Save score, so you can stop and come back at any time.",
  unblind: "Reveals which genotype each code was, then writes the results tables and a draft Methods text. Do it once, when scoring is finished.",
  display: "Standard shows the detector's full range. Enhanced runs from background to a typical bright spot, so dim cells are easier to see. Both stay the same for every image from one microscope, so images are always shown alike.",
  roi: "ROI (region of interest): an outline you draw around a cell body so wormscore can measure its brightness. Optional: skip it if there is nothing to outline.",
  suggested: "Spots wormscore found automatically, brightest first (S1). Use one as a neuron's outline or draw your own.",
  settings_group: "Images taken on the same microscope with the same settings. Brightness is compared only within one group.",
  hidden_repeats: "A few images shown a second time under new codes, so your own consistency can be measured (intra-rater agreement). They never count in the results.",
  primary_outcome: "The one result you choose in advance as the main test. Every other test is reported as secondary.",
  penetrance: "The share of animals with at least one affected neuron.",
  defect: "The answers that count as affected when results are calculated, for example dim or missing.",
  kappa: "Cohen's kappa measures agreement between two scorers beyond what chance alone would give: 1 is perfect, 0 is no better than chance.",
  second_scorer: "Another person scores some of the same coded images on their own, so agreement between scorers (inter-rater reliability) can be reported. Your results use only your answers.",
  browse: "See images with their file names and genotypes, without scoring. It is off from Build blind set until Unblind, so it can never break blinding.",
  log: "log.md is the project's record: every step, setting and software version, with the time. Reviewers can check the Methods against it.",
  usable: "Say No if the image cannot be judged (out of focus, wrong animal, damaged). Unusable images are counted separately, never as normal or affected.",
  confidence: "How sure you are about this image. It is saved with the score (scores.csv, and merged.csv after Unblind), so uncertain calls can be looked at again.",
};

function wsHelp(h, key, name) {
  const text = WS_TERMS[key];
  if (!text) return null;
  const tip = h("span", { class: "tip-bubble", role: "status" });
  const btn = h("button", {
    type: "button", class: "tip-btn", "aria-label": `What is ${name || key.replace(/_/g, " ")}?`, "aria-expanded": "false",
    onclick: e => {
      e.stopPropagation();
      e.preventDefault();
      const open = btn.getAttribute("aria-expanded") === "true";
      wsHelp.closeAll();
      if (!open) {
        btn.setAttribute("aria-expanded", "true");
        tip.textContent = "";
        // set after a tick, so screen readers announce the new text
        setTimeout(() => {
          tip.textContent = text;
          tip.classList.add("on");
          // fixed to the window, so a scrolling panel never clips it; kept inside the window
          const b = btn.getBoundingClientRect(), w = tip.offsetWidth, ht = tip.offsetHeight;
          const left = Math.max(8, Math.min(b.left + b.width / 2 - w / 2, innerWidth - w - 8));
          const below = b.bottom + 6 + ht < innerHeight - 8;
          tip.style.left = `${left}px`;
          tip.style.top = `${below ? b.bottom + 6 : Math.max(8, b.top - 6 - ht)}px`;
        }, 30);
      }
    },
  }, "?");
  return h("span", { class: "tip" }, btn, tip);
}

wsHelp.closeAll = () => {
  for (const b of document.querySelectorAll(".tip-btn[aria-expanded=true]")) {
    b.setAttribute("aria-expanded", "false");
    const t = b.nextElementSibling;
    t.classList.remove("on");
    t.textContent = "";
  }
};
document.addEventListener("click", e => { if (!e.target.closest?.(".tip")) wsHelp.closeAll(); });
window.addEventListener("scroll", () => wsHelp.closeAll(), true);
window.addEventListener("resize", () => wsHelp.closeAll());
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && document.querySelector(".tip-btn[aria-expanded=true]")) {
    const b = document.querySelector(".tip-btn[aria-expanded=true]");
    wsHelp.closeAll();
    b.focus();
    e.stopPropagation();
  }
}, true);
