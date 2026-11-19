# Roadmap

What is planned for wormscore, roughly in order. Plans change. What shipped in each version is in
[CHANGELOG.md](CHANGELOG.md). Ideas and requests are welcome as
[GitHub issues](https://github.com/arjunkai/wormscore/issues).

Updating never changes a project's results: new analysis options only run if you set them, so a
project scores, measures and reports exactly as before. Screens and buttons may change.

## Planned

### Features

- **Open (non-blind) scoring, under consideration.** An optional project type for pilots or
  phenotypes that cannot be hidden, chosen when the project is created. Every output and the
  Methods draft would state that scoring was not blind to genotype. Blind scoring stays the default.
  A review of other tools and of image.sc (November 2026) found no request for it: every ordinary
  viewer already scores openly, and what people ask for is stronger blinding. It will be built if
  users ask for it.
- **Open a single image, under consideration.** View one file in wormscore's viewer (both display
  ranges, suggested cells, 3D, measurements) without making a project, as Fiji, QuPath and napari
  allow. It would refuse, or warn and record the view, for a file inside a project that is being
  scored blind, so it cannot get around the Browse lock. Fiji already covers quick looks, so it will
  be built if users ask for it.

### Readers

- **The acquisition date of Leica files.** A .lif file holds each image's start time (LAS X shows it
  in Properties; Check the reading shows it), but Scan data does not record it, so Leica images have
  no imaging_date and flat-field correction cannot tell their days apart. Recording it would change
  flat-field sessions of Leica projects that use slide images, so it needs a rule that keeps existing
  projects as they are until they are scanned again.

## Version 1.0.0

1.0.0 is the version used from start to finish on a real blind-scoring project (0.14.0, then
unblinded) plus the identity channel and marker presets. The list above continues as 1.x releases;
new analysis options only run if a project sets them, so its results stay as in 1.0.0.
