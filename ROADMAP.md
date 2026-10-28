# Roadmap

What is planned for wormscore, roughly in order. Plans change; the
[Releases page](https://github.com/arjunkai/wormscore/releases) lists what has shipped. Ideas and
requests are welcome as [GitHub issues](https://github.com/arjunkai/wormscore/issues).

New features are off by default, so a project that does not turn them on scores, measures and
reports exactly as before.

## Planned

- **Second scorer.** A second person scores the same blind set; agreement between scorers.
- **Reference images for "dim".** An optional panel of reference images the scorer can compare against.
- **Second identity channel.** Find the cell on one marker and judge the reporter on another, as
  recommended for unbiased selection. Presets for common fluorescent proteins beyond GFP (for
  example mNeonGreen, YFP, mCherry, TagRFP, mScarlet, mKate2, BFP), matched by emission band.
- **Score form builder.** Make your own set of scoring questions in the app instead of editing
  `config.json`: start from a preset (cell fate, axon guidance, neurodegeneration, dendrite
  length, counts and position) or a blank form; choose each question's answer type (choice,
  yes/no, ordered scale, count), whether it is asked per neuron, per left/right cell or per
  animal, which answers count as a defect, and simple conditions ("only if the axon is
  visible"); pick one main outcome; see the score panel as the scorer will. The form is locked
  and recorded in the log at Build blind set, and each answer type gets its test at unblinding
  (Fisher's exact for choices, a rank test for scales and counts).
- **Olympus `.oir`.** Planned once its channel information can be checked against enough sample files.
- **Interim report.** Totals for the images scored so far, with no codes shown, recorded in the
  log as an interim look.
- **Imaging-session tools.** Optional flat-field or reference-slide correction per imaging session,
  and a check for gut autofluorescence.
- **Example data.** An optional NeuroPAL example download, if its license allows.

## Version 1.0.0

1.0.0 comes after the list above is done and wormscore has been used from start to finish on a real
blind-scoring project.

## Recently shipped

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
