# wormscore

<p>
  <a href="https://github.com/arjunkai/wormscore/releases/latest/download/wormscore-windows.zip"><img alt="Download for Windows" src="https://img.shields.io/badge/Download_for-Windows-0078D4?style=for-the-badge&logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI+PHBhdGggZmlsbD0iI2ZmZiIgZD0iTTAgMy40IDkuOCAydjkuNUgwek0xMC45IDEuOSAyNCAwdjExLjVIMTAuOXpNMCAxMi42aDkuOHY5LjVMMCAyMC42ek0xMC45IDEyLjZIMjRWMjRsLTEzLjEtMS45eiIvPjwvc3ZnPg=="></a>
  <a href="https://github.com/arjunkai/wormscore/releases/latest/download/wormscore-mac.zip"><img alt="Download for macOS" src="https://img.shields.io/badge/Download_for-macOS-111111?style=for-the-badge&logo=apple&logoColor=white"></a>
</p>

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23520610.svg)](https://doi.org/10.5281/zenodo.23520610)
[![PyPI](https://img.shields.io/pypi/v/wormscore?label=PyPI)](https://pypi.org/project/wormscore/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/arjunkai/wormscore/blob/main/LICENSE)
[![Tests](https://github.com/arjunkai/wormscore/actions/workflows/tests.yml/badge.svg)](https://github.com/arjunkai/wormscore/actions/workflows/tests.yml)

Blind scoring of fluorescent neurons in *C. elegans*, with measurement and genetics statistics.
Built for scoring reporter neurons (normal / dim / missing) in confocal z-stacks. Nothing is tied
to one neuron, marker or microscope: those are settings in each project's `config.json`, so other
organisms work the same way.

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

**Windows, no Python needed:** click **Download for Windows** above (or get `wormscore-windows.zip`
from the [Releases page](https://github.com/arjunkai/wormscore/releases)), unzip it, and double-click `wormscore.exe`. It opens the wormscore app window: your projects on the left and the
four steps (Scan data, Build blind set, Score, Unblind) as a guided list with progress and time left.
The older launcher window is still there for one version: `wormscore.exe --classic`.

**Mac (Apple silicon, M1 and newer), no Python needed:** click **Download for macOS** above, unzip,
and drag `wormscore.app` into Applications. The app is not signed with a paid Apple developer
account, so the first time macOS blocks it: close the warning, then open **System Settings > Privacy &
Security** and click **Open Anyway**. It opens in your web browser. On an Intel Mac, use Python below.

**With Python 3.12 or newer:**

```
pip install wormscore
```

The newest code from GitHub: `pip install git+https://github.com/arjunkai/wormscore.git`

This installs a `wormscore` command and `wormscore-launcher`, which opens the same app window (in your
default browser on macOS and Linux).

## Try it in two minutes (simulated data)

```
wormscore demo my_demo
wormscore inventory my_demo
wormscore prepare my_demo
wormscore app my_demo
```

`demo` writes 20 simulated worm heads (controls with two normal cells; mutants with cells that are
normal, dim or missing at random) and a `demo_truth.csv` you can check your calls against later.
In the app window, use **Try a demo...**, then the steps in order: **Scan data > Build blind set > Score**.

**Real worms:** `wormscore demo my_demo --real` (or **Real worm images** in the app's demo dialog) downloads four
L1 heads with an endogenous GFP reporter, about 150 MB, from
[Sun and Hobert 2021](https://doi.org/10.1038/s41586-021-04071-4)
([Zenodo 5602160](https://doi.org/10.5281/zenodo.5602160), CC BY 4.0). Each file is checked against its
MD5 from the Zenodo record and cached in `%LOCALAPPDATA%\wormscore\demo_cache` (`~/.cache` elsewhere).
The two reporters stand in for two genotypes; both are normal animals, so this is for practice.
The images are not part of this repository.

## Using it on your own images

1. **Copy your data** to a local folder (not a synced cloud folder if the data is not yours to share):
   `wormscore copy D:\from\drive C:\data\my_copy` copies every file, checks each one by SHA-256 and
   makes the copies read-only.
2. **Make a project:** `wormscore init C:\projects\my_project`, then edit `config.json`:
   point `sources` at your images, write genotype rules, and name your neurons. See
   [`examples/config_example.json`](https://github.com/arjunkai/wormscore/blob/main/examples/config_example.json).
3. **Scan:** `wormscore inventory my_project` lists every file (`outputs/manifest.csv`: raw image,
   duplicate, derived projection, reference, hidden system file) and every image with its metadata
   and channel identity (`outputs/inventory.csv`). Check the flags column.
4. **Build the blind set:** `wormscore prepare my_project` applies the inclusion rules, assigns codes,
   finds and measures suggested cells, and writes the sealed key. Excluded images and the reasons are
   in `outputs/excluded.csv`. To leave out images by name (for example a plate with the wrong
   reporter), add `"exclude": [{"match": "*pattern*", "reason": "why"}]` to that source.
   Images taken after scoring started can be added with `wormscore prepare my_project --add-new-images`
   (scan first): existing codes stay the same and new images get codes after the last one.
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
   (Product model by default) with a binomial test. Also optional: a secondary definition of "affected"
   (descriptive, within one microscope), planned comparisons, one-sided tests, labelled sources, and
   comparisons across microscopes for chosen measures (with warnings). All of these are recorded as an
   analysis plan before scoring and locked once scoring starts. See [docs/METHODS.md](https://github.com/arjunkai/wormscore/blob/main/docs/METHODS.md#8-unblinding).
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

See [docs/METHODS.md](https://github.com/arjunkai/wormscore/blob/main/docs/METHODS.md) for every step in plain language, suitable for adapting into
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

MIT License (see [LICENSE](https://github.com/arjunkai/wormscore/blob/main/LICENSE)). If you use wormscore in research, please cite the version you used.
Each release has its own DOI on Zenodo; [doi:10.5281/zenodo.23520610](https://doi.org/10.5281/zenodo.23520610)
lists all versions and resolves to the latest. GitHub's "Cite this repository" button (from
[CITATION.cff](https://github.com/arjunkai/wormscore/blob/main/CITATION.cff)) gives the reference, and `log.md`
in your project records which version ran each step.

wormscore reads Leica files with [liffile](https://github.com/cgohlke/liffile) and Zeiss files with
[czifile](https://github.com/cgohlke/czifile) (both BSD-3). The Windows app bundles these and other
libraries; their licenses are in `THIRD_PARTY_LICENSES.txt` next to `wormscore.exe`.
