# Roadmap

What is planned for wormscore, roughly in order. Plans change; the
[Releases page](https://github.com/arjunkai/wormscore/releases) lists what has shipped. Ideas and
requests are welcome as [GitHub issues](https://github.com/arjunkai/wormscore/issues).

New features are off by default, so a project that does not turn them on scores, measures and
reports exactly as before.

## Planned

- **Score form builder, part 2.** Ordered scales (for example a 0 to 6 degeneration score) and counts,
  each with a rank test (Mann-Whitney, Bonferroni-corrected) and Methods text; questions per left/right
  cell and per animal; presets for neurodegeneration, dendrite length, and counts and position; a short
  definition for each answer, shown while scoring. Answer types are offered only once their test ships,
  so nobody collects answers that cannot be analysed.
- **Reference images for "dim".** An optional panel of reference images the scorer can compare against.
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
each is off by default, so a project that does not turn it on works exactly as in 1.0.0.

## Recently shipped

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
