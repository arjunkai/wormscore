# wormscore

Blind scoring and fluorescence measurement of identified neurons in confocal z-stacks.
Built for scoring *C. elegans* reporter neurons (normal / dim / missing), but nothing is tied
to one neuron, marker or microscope: those are settings in each project's `config.json`.

- **Blind:** every image gets a random code. Genotypes and file names stay in a sealed key
  until you finish scoring.
- **Fixed display:** every image from the same microscope is shown with the same brightness range.
  You can page through z-planes and overlay brightfield.
- **Measured, not decided:** suggested cell bodies and your drawn regions are measured (peak, mean,
  local background, sharpest plane, saturation). The numbers are written to files and never shown
  while you score. The software never labels a cell normal, dim or missing.
- **Traceable:** every number links to the exact file, path, series and SHA-256 checksum it came from.
- **Reproducible:** every parameter and software version is appended to `log.md` on each run.
- **Local:** runs on your computer only. Nothing is uploaded.

Supported raw formats: Leica `.lif`, Zeiss `.czi`, OME-TIFF (including Micro-Manager style files with
a `.companion.ome`). Channel identity (marker vs brightfield) is read from metadata, never guessed.

## Install

**Windows, no Python needed:** download `wormscore-windows.zip` from the Releases page, unzip it,
and double-click `wormscore.exe`.

**With Python 3.12 or newer:**

```
pip install git+https://github.com/arjunkai/wormscore.git
```

This installs a `wormscore` command and a `wormscore-launcher` window.

## Try it in two minutes (simulated data)

```
wormscore demo my_demo
wormscore inventory my_demo
wormscore prepare my_demo
wormscore app my_demo
```

`demo` writes 20 simulated worm heads (controls with two normal cells; mutants with cells that are
normal, dim or missing at random) and a `demo_truth.csv` you can check your calls against later.
In the launcher window the same steps are buttons: **Demo... > 1. Scan data > 2. Build blind set > 3. Score**.

## Using it on your own images

1. **Copy your data** to a local folder (not a synced cloud folder if the data is not yours to share):
   `wormscore copy D:\from\drive C:\data\my_copy` copies every file, checks each one by SHA-256 and
   makes the copies read-only.
2. **Make a project:** `wormscore init C:\projects\my_project`, then edit `config.json`:
   point `sources` at your images, write genotype rules, and name your neurons. See
   [`examples/config_example.json`](examples/config_example.json).
3. **Scan:** `wormscore inventory my_project` lists every file (`outputs/manifest.csv`: raw image,
   duplicate, derived projection, reference, hidden system file) and every image with its metadata
   and channel identity (`outputs/inventory.csv`). Check the flags column.
4. **Build the blind set:** `wormscore prepare my_project` applies the inclusion rules, assigns codes,
   finds and measures suggested cells, and writes the sealed key. Excluded images and the reasons are
   in `outputs/excluded.csv`. To leave out images by name (for example a plate with the wrong
   reporter), add `"exclude": [{"match": "*pattern*", "reason": "why"}]` to that source.
5. **Score:** `wormscore app my_project`. For each image: answer any yellow questions, score each
   neuron, save, then outline each cell body (or accept a suggestion) and save regions.
6. **Unblind after scoring:** `wormscore unblind my_project --i-have-finished-scoring`. Writes
   `outputs/unblinded/merged.csv` (scores + numbers + key, one row per image), a summary, and results
   tables: `per_neuron.csv` (one row per animal and neuron, for plotting), `penetrance.csv` (animals with
   0, 1, 2 ... affected neurons) and `fisher_tests.csv` (Fisher's exact within each microscope,
   Bonferroni-corrected). Set `unblinding.reference_genotype` in `config.json` to test against a control.
   Optional, off unless set in `unblinding`: `question_outcomes` turns the per-neuron questions into
   the same penetrance and Fisher tables (for example "process defect" when a dendrite question is
   answered "no"), and `interaction` tests a double mutant against the value two single mutants predict
   (Product model by default) with a binomial test. See [docs/METHODS.md](docs/METHODS.md#8-unblinding).
   `methods_text.md` is a draft Methods paragraph on blinding, measurement and statistics; fill in the
   `blinding` section of `config.json` (who coded, who scored) or edit the [brackets] it leaves.

## Project folder

| Path | Contents | Open while scoring? |
|---|---|---|
| `config.json` | every parameter | yes |
| `log.md` | every run: parameters, software versions, counts | yes |
| `outputs/manifest.csv`, `inventory.csv`, `excluded.csv` | what was found and what was left out | yes |
| `outputs/auto_measurements.csv` | numbers for suggested spots | avoid until done |
| `blind/` | images for scoring, named by code | used by the app |
| `scoring/` | your scores, regions and their measurements | scores yes; avoid `roi_measurements.csv` |
| `key/` | code to file, genotype, settings | **no, not until scoring is finished** |

## How it works

See [docs/METHODS.md](docs/METHODS.md) for every step in plain language, suitable for adapting into
a Methods section, including the measurement definitions and their limits.

## Development

```
pip install -e .[dev]
pytest
```

The tests build a simulated project and check the measurements, the blinding (no genotype or file
name reaches the scoring page), code stability, the unblinding locks and traceability. Leica and
Zeiss readers are checked against real files locally; real images are never committed.

Build the Windows app: `packaging\build_windows.bat`.

## Privacy

This repository contains code only. `.gitignore` excludes image formats, keys, scores and outputs.
Do not commit or upload data that belongs to someone else.

## License and citation

MIT License (see [LICENSE](LICENSE)). If you use wormscore in research, please cite it: GitHub's
"Cite this repository" button (from [CITATION.cff](CITATION.cff)) gives the reference.

wormscore reads Leica files with [liffile](https://github.com/cgohlke/liffile) and Zeiss files with
[czifile](https://github.com/cgohlke/czifile) (both BSD-3). The Windows app bundles these and other
libraries; their licenses are in `THIRD_PARTY_LICENSES.txt` next to `wormscore.exe`.
