# Roadmap

What is planned for wormscore, roughly in order. Plans change; the
[Releases page](https://github.com/arjunkai/wormscore/releases) lists what has shipped. Ideas and
requests are welcome as [GitHub issues](https://github.com/arjunkai/wormscore/issues).

New features are off by default, so a project that does not turn them on scores, measures and
reports exactly as before.

## Planned

- **Scorer consistency.** Hidden repeat images (the same image shown again under a new code), a
  second-scorer mode, and agreement statistics: percent agreement and Cohen's kappa (weighted for
  ordered calls such as normal / dim / missing).
- **A clearer "dim".** An optional panel of reference images the scorer can compare against, a
  dim cutoff based on the local background noise and recorded before scoring, and a report at
  unblinding that compares each dim / normal call with the measured brightness. Calls stay with the
  scorer; numbers never decide them.
- **Second identity channel.** Find the cell on one marker and judge the reporter on another, as
  recommended for unbiased selection. Presets for common fluorescent proteins beyond GFP (for
  example mNeonGreen, YFP, mCherry, TagRFP, mScarlet, mKate2, BFP), matched by emission band.
- **More file formats.** Nikon `.nd2`, Olympus `.oib` / `.oif` / `.oir`, and ImageJ hyperstack TIFF.
- **Interim report.** Totals for the images scored so far, with no codes shown, recorded in the
  log as an interim look.
- **Imaging-session tools.** Optional flat-field or reference-slide correction per imaging session,
  and a check for gut autofluorescence.
- **Example data.** An optional NeuroPAL example download, if its license allows.
- **Interface polish.** Accessibility, keyboard use and edge cases in the app window and the
  scoring page.

## Version 1.0.0

1.0.0 comes after the list above is done and wormscore has been used from start to finish on a real
blind-scoring project.

## Recently shipped

- 0.8.0: in-app updates (the app offers new versions and can go back to the previous one).
- 0.7.x: the app window, a faster and lighter Build blind set, the macOS app.
- 0.5.0 to 0.6.x: an analysis plan locked before scoring, cross-microscope rules, test direction,
  source labels, a real-image demo.
