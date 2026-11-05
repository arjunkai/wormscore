# Roadmap

What is planned for wormscore, roughly in order. Plans change; the
[Releases page](https://github.com/arjunkai/wormscore/releases) lists what has shipped. Ideas and
requests are welcome as [GitHub issues](https://github.com/arjunkai/wormscore/issues).

Updating never changes a project's results: new analysis options only run if you set them, so a
project scores, measures and reports exactly as before. Screens and buttons may change.

## Planned

### Scoring criteria (from a review of published scoring schemes and tools, November 2026)

- **A definition for every category, with an optional example image, shown while scoring.** Each
  call and scale level gets its own short definition (today they share one free-text rules list),
  and can carry a reference image the scorer can compare against, for example for "dim". Scoring
  guidelines ask for clear boundaries between categories (Gibson-Corley, Olivier and Meyerholz 2013,
  *Vet Pathol*), and published scales come with one reference image per score (Bijwadia, Morton and
  Meyer 2021, *JoVE* 62894).
- **Share criteria between projects and labs.** Export the scoring criteria as one file (questions,
  answers, definitions, outcomes, primary outcome, a name, a version and an optional citation) and
  import it into another project. Its checksum goes into `log.md` and the Methods draft, so a paper
  can say exactly which criteria were used. This follows REDCap's data dictionary and Label Studio's
  labeling configs.
- **Preset wording that matches the literature** (new projects only; existing projects keep theirs):
  fate reporter **on / dim / off**, with dim defined against wild type at the same settings (Reilly
  et al. 2022, *PLoS Genet*); dendrite length **full length / short**, with "partial" optional
  (Heiman and Shaham 2009, *Cell*; Low et al. 2019, *Development*); axon guidance questions phrased
  around the target ("fails to reach the ventral cord?") plus a midline-crossing question (Schmitz,
  Kinge and Hutter 2007, *PNAS*; Xu et al. 2015, *PLoS Genet*). Each preset names its source in the
  app.
- **A practice set.** Score a small set of images against an answer key before the real scoring
  and see the agreement, as published scales provide (Bijwadia et al. 2021), to catch drift in how
  categories are judged (Gibson-Corley et al. 2013).
- **A warning for long scales.** Above 5 levels the builder warns that agreement between scorers
  usually drops and points to the weighted kappa at unblinding (Gibson-Corley et al. 2013 suggest
  about 4 to 5 levels).
- **Also:** questions per left/right cell, and a counts-and-position preset.

### Other planned features

- **More identity-channel tools.** The identity channel in the 3D view, a bleed-through check, and
  several identity channels (for multi-color systems such as NeuroPAL).
- **More 3D.** Depth of the brightest point under the cursor, and turning about the horizontal axis
  too (both keep brightness unchanged; no depth cueing in the scoring view).
- **Olympus `.oir`.** Planned once its channel information can be checked against enough sample files.
- **Interim report.** Totals for the images scored so far, with no codes shown, recorded in the
  log as an interim look.
- **Imaging-session tools.** Optional flat-field or reference-slide correction per imaging session,
  and a check for gut autofluorescence.
- **Example data.** An optional NeuroPAL example download, if its license allows.

## Version 1.0.0

1.0.0 is the version used from start to finish on a real blind-scoring project (0.14.0, then
unblinded) plus the identity channel and marker presets. The list above continues as 1.x releases;
new analysis options only run if a project sets them, so its results stay as in 1.0.0.

## Recently shipped

- 1.6.2: the score form is now called **scoring criteria** (the term papers use), and step 2 is **Set
  scoring criteria**. ROI, intra-rater and inter-rater named where they apply. Dropdown arrows with room
  to breathe. The Neurodegeneration preset's level 4 matches the published scale (Bijwadia et al. 2021).
- 1.6.1: closing the Windows app's own window with unsaved Project settings asks first (WebView2
  windows close without asking the page). The home screen re-reads the log only when it changes. README
  checked claim by claim against the code.
- 1.6.0: a locked score form can be changed on purpose: **change it anyway...** asks for a reason and
  writes the change and the reason to the log, and unblinding lists each change with its reason in the
  summary and the Methods draft. Wording fixes keep the planned tests; other changes stop them, as a hand
  edit always did. Project settings asks before closing with unsaved changes.
- 1.5.0: **Score form** is its own step on the home screen (Scan data, Score form, Build blind set,
  Score, Unblind). The card says what the scorer answers and whether the form is recorded in the log
  or locked; checking the form and keeping it as it is also records it. Fixes an occasional "cannot
  import name 'measure'" message a few seconds after the app opened. Results are unchanged.
- 1.4.0: scale and count questions. The builder adds **Scale** (levels in order, best first) and
  **Count** answers, and any question can be asked for each neuron or once per animal. Each animal gets
  one value (worst neuron by default), compared between genotypes with a Mann-Whitney test with an
  exact permutation p-value and an effect size; an optional cutoff is also tested with Fisher's exact
  test. New presets: Neurodegeneration (0 to 6) and Dendrite length. The builder and scoring page are
  roomier. Existing projects give identical results.
- 1.3.0: the score form builder. The **Scoring form** section of Project settings sets the neurons,
  calls, extra yes/no and choice questions (defect answers, "can't tell", required, skipped for some
  calls), outcomes and a primary outcome, with presets and a live preview of the score panel. The form
  is written to the log and locks at the first score; a form changed by hand afterwards stops the tests
  at unblinding. Existing projects give identical results.
- 1.2.0: a second scorer. **Make a package** gives another person a random share of each genotype's
  coded images, with no key, file names or analysis plan, in a new order; **Import scores** checks the
  package came back unchanged and keeps the answers apart from yours. Unblinding adds agreement
  between scorers and a list of disagreements; results still use only your answers. Image folders,
  genotype rules and leave-out rules are now set in the app (**Project settings**), with a live count
  of how the rules sort the scanned images, instead of by editing `config.json`.
- 1.1.0: checks from a review of the measurement and statistics methods. An optional likelihood-ratio
  test for genetic interaction (`interaction.test`), which allows for the sampling error of the single
  mutants (the binomial test, still the default, finds an interaction too often with small groups);
  optional extra agreement statistics for hidden repeats (kappa interval, PABAK, Gwet's AC1) and a
  choice of kappa weights; the measured dim check now counts regions it cannot use, and the Methods
  text says so when no region was outlined. Clearer docs on suggested-cell spacing and display ranges.
- 1.0.0: presets for fluorescent proteins beyond GFP (mNeonGreen, YFP, mCherry, TagRFP, mScarlet,
  mKate2, BFP) and an optional identity channel: a second marker that shows where the cell is. The
  scoring page shows the reporter, the identity marker or both (keys 1, 2, 3), and suggested cells are
  found on the identity marker and measured on the reporter, so a dim or missing reporter cell is still
  found. GFP projects build exactly the same blind set as before.
- 0.14.0: 3D view on the scoring page: turn the stack -60 to +60 degrees, same fixed display ranges.

- 0.13.0: reads Nikon `.nd2`, Olympus FluoView `.oib` and `.oif`, and (when turned on for a source)
  ImageJ hyperstack TIFF.
- 0.12.0: safer saving (a held Ctrl+Enter, a slow image or a results file open in Excel can no
  longer save the wrong answers or half a save), images never mixed up between projects or builds,
  clear messages instead of "lost connection" for errors, and finishing touches on both screens.
- 0.11.0: faster start and image switching; keyboard and screen-reader fixes, clearer status and
  wording, safer dialogs, and Ctrl+Enter to save and open the next unscored image.
- 0.10.0: hidden repeat images and a scorer-consistency report (percent agreement, Cohen's kappa); a
  measured background cutoff, recorded before scoring, for checking calls afterwards.
- 0.9.0: the Windows app has its own windows (one taskbar button, no lost connections).
- 0.8.x: in-app updates (the app offers new versions and can go back to the previous one), drafts of
  unsaved scoring answers, reconnecting windows.
- 0.7.x: the app window, a faster and lighter Build blind set, the macOS app.
- 0.5.0 to 0.6.x: an analysis plan locked before scoring, cross-microscope rules, test direction,
  source labels, a real-image demo.
