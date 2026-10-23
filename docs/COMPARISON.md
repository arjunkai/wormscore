# How wormscore compares to other tools

Written October 2026 for researchers deciding whether wormscore fits their project. Every factual
claim links to a source. Statements marked **(inference)** are our own reading, not something a
source says. wormscore is young (version 0.1.x, one developer) and has not yet been used in a
published study; weigh that accordingly.

## Short answer

| If you need to... | Consider |
|---|---|
| Score named neurons as present / dim / missing per animal, blinded, with brightness numbers traceable to each file | wormscore |
| Blind whole images and record one call per image, inside Fiji | Fiji Blind Analysis Tools |
| Blind whole images with a built-in repeat-scoring check, on Windows | Blinder |
| Blind scoring by several people over the web | the browser-based tool of Schippers et al. 2024 |
| Identify many neurons in NeuroPAL animals and read reporter levels per neuron | NeuroPAL ID |
| Segment thousands of cells or nuclei automatically | Cellpose, StarDist, ilastik, CellProfiler, QuPath |
| General viewing, measurement and scripting | Fiji / ImageJ, napari |

## What problem wormscore addresses

Worm cell-fate papers commonly score each identified neuron as on / dim / off in every animal and
compare genotypes with Fisher's exact test ([Yemini et al. 2021](https://doi.org/10.1016/j.cell.2020.12.012);
[example](https://journals.plos.org/plosgenetics/article?id=10.1371/journal.pgen.1010372)). Such calls
are judgements, and judgements made without blinding are biased: across life-science papers only about
14% were scored as blind, and non-blind studies reported effects about 27% larger
([Holman et al. 2015](https://doi.org/10.1371/journal.pbio.1002190)). The NeuroPAL paper states plainly
that "Mutant analysis studies were not blinded"
([Yemini et al. 2021](https://doi.org/10.1016/j.cell.2020.12.012)), which suggests blinding is not yet
routine in this kind of work **(inference from one paper; we found no survey of C. elegans papers)**.
ARRIVE 2.0 asks authors to report who knew group allocation at each stage, including outcome
assessment ([Percie du Sert et al. 2020](https://doi.org/10.1371/journal.pbio.3000411)).

## Tools compared

| Tool | What it does for this task | Blinding | Per-neuron calls | Brightness measured | License |
|---|---|---|---|---|---|
| **wormscore** | Blinded scoring of identified neurons in confocal stacks, with measurement and a key back to every file | Yes: random codes, sealed key, scorer never sees names, genotype or numbers | Yes | Yes, per region, with local background and saturation | MIT |
| **Fiji Blind Analysis Tools** ([page](https://imagej.net/plugins/blind-analysis-tools)) | Copies randomly selected files under encrypted names with a mapping file; opens images in random order and records one decision per image; can make replicate copies of files | Yes, by file name | No (one decision per image) | No | GPL-3 (stated on the page) |
| **Blinder** ([Cothren et al. 2018](https://doi.org/10.21769/BioProtoc.3103)) | Shows images in random order; user picks a score from their own scale; optional "quality control" re-shows a chosen percentage of images to test consistency | Yes | No (one score per image) | No | Free; "available for Windows operating system only" (protocol PDF) |
| **Browser-based blinded tool** ([Schippers et al. 2024](https://doi.org/10.3390/jimaging10020033)) | Experts open images through temporary links and score them "blind in a random order inside a web browser" | Yes | Not described | Not described | See paper |
| **NeuroPAL ID** ([instructions](https://www.hobertlab.org/wp-content/uploads/2020/06/Software-Instructions.pdf), [repo](https://github.com/Yemini-Lab/NeuroPAL_ID)) | Semi-automatic identification of neurons in NeuroPAL images; saves each neuron's reporter (GFP) value with background, an Otsu threshold and a change-point threshold | Not described; the paper's mutant analyses were not blinded | Yes, many neurons at once | Yes | CC BY-NC-SA 4.0 (non-commercial; repository LICENSE.txt) |
| **Fiji / ImageJ** ([Fiji repo](https://github.com/fiji/fiji)) | General viewer and measurement, Bio-Formats import, macros | Only with plugins or macros | Manual (ROI Manager, Cell Counter) | Yes, manual | GPL-3 (Fiji) |
| **CellProfiler** ([repo](https://github.com/CellProfiler/CellProfiler)) | Batch segmentation and measurement pipelines | No built-in blinded scoring **(inference: none described)** | Through segmentation, not identity | Yes | BSD-3 |
| **ilastik** ([repo](https://github.com/ilastik/ilastik)) | Interactive machine-learning pixel and object classification | No | Through classification | Yes | GPL-2/3 |
| **Cellpose**, **StarDist** ([cellpose](https://github.com/MouseLand/cellpose), [stardist](https://github.com/stardist/stardist)) | Deep-learning segmentation of cells or nuclei | No | Segmentation only | Through other tools | BSD-3 |
| **napari** ([repo](https://github.com/napari/napari)) | n-dimensional viewer with plugins | No built-in | Through plugins | Through plugins | BSD-3 |
| **QuPath** ([repo](https://github.com/qupath/qupath)) | Whole-slide and tissue analysis, cell detection, classification | No built-in | Cell classification | Yes | GPL-3 |
| **Vendor software** (Leica LAS X, Zeiss ZEN, Nikon NIS-Elements), **Imaris**, **arivis** | Acquisition and commercial analysis | Not a documented feature **(inference: we found none)** | Varies | Yes | Commercial |

## Where wormscore is genuinely better

These are the specific needs of blinded per-neuron reporter scoring. Each row names the problem, the
evidence that people struggle with it, and what wormscore does.

| Need | Evidence that it is a real problem | What wormscore does |
|---|---|---|
| Blinding that does not leak | Renamed `.czi` files still show the original name in Fiji's header ([image.sc 107354](https://forum.image.sc/t/renaming-czi-files-impossible/107354), [image.sc 61555](https://forum.image.sc/t/how-to-fully-blind-an-image-hide-original-image-name/61555)); people ask how to keep the "cipher" ([r/labrats](https://www.reddit.com/r/labrats/comments/3sgi3s/i_want_a_program_that_blinds_my_filenames_for_me/)) | The scoring page receives only pixels, plane count and pixel size. Names, paths, genotypes and numbers stay in `key/`, which the app never reads. Tests check this. |
| Calls per neuron, not per image | Existing blinding tools record one decision per image (Blind Analysis Tools page; Blinder protocol). Cell Counter cannot attach several labels to one cell ([image.sc 98534](https://forum.image.sc/t/assigning-multiple-labels-to-one-cell/98534)) | Each named neuron gets its own call, side and notes, plus your own yes/no questions. |
| The same display for every image | Contrast differs between wild type and mutant ([image.sc 120964](https://forum.image.sc/t/quantification-of-yeast-golgi-protein-colocalization-with-known-golgi-protein-sec7/120964)); "Clicking reset for each image will almost guarantee that you have different display settings" ([image.sc 117643](https://forum.image.sc/t/how-to-keep-source-brightness-setting-when-uploading-for-analysis/117643)); judging autoscaled images "could lead to inaccurate conclusions" ([Jost & Waters 2019](https://doi.org/10.1083/jcb.201812109)) | Two fixed display ranges per microscope, computed once over all images of both genotypes. No per-image autoscale. Pixel values are never changed. |
| Knowing which settings each number came from | Bio-Formats reports laser settings for one laser only ([bioformats #3513](https://github.com/ome/bioformats/issues/3513)), mixes up LIF channels ([#3896](https://github.com/ome/bioformats/issues/3896)) and misreads CZI detector gain ([#4029](https://github.com/ome/bioformats/issues/4029)); comparing images taken with different settings is "making up data" ([r/labrats](https://www.reddit.com/r/labrats/comments/1gx7rdy/need_some_help_comparing_fluorescent_photos_with/)) | Reads Leica and Zeiss XML directly, records gain, laser power and averaging per image, flags images whose settings differ, and reports brightness only within a settings group. |
| Channel identity from metadata | LIF channel order and names read wrongly ([image.sc 73038](https://forum.image.sc/t/bad-reading-of-lif-metadata/73038)) | The marker channel is chosen from excitation line, emission band and LUT or dye name. If not exactly one channel matches, the image is excluded as UNRESOLVED instead of guessed. |
| Saturation noticed early | Saturated images "cannot be used for quantitation" ([Waters 2009](https://doi.org/10.1083/jcb.200903097)); people discover it at analysis time ([image.sc 103263](https://forum.image.sc/t/overexposure-high-signal-intensity-in-roi-of-confocal-images/103263)) | Saturated pixels are counted per region, and the scorer is asked to confirm when a suggested cell is saturated. |
| Every number traceable | Reporting checklists ask for software versions, key parameters and manual ROIs ([Schmied et al. 2024](https://doi.org/10.1038/s41592-023-01987-9)) | Each row links to original path, local copy, series and SHA-256; every run appends parameters and software versions to `log.md`; drawn regions are saved. |
| No scripting | Users find writing scoring macros harder than scoring by hand ([image.sc 23869](https://forum.image.sc/t/blind-scoring-macro-for-imagej/23869)) | Point-and-click launcher and scoring page; no code needed. |

## Where wormscore is worse or not the right tool

- **File formats.** Reads Leica `.lif`, Zeiss `.czi` and OME-TIFF only. It does not read Nikon `.nd2`,
  Olympus `.oir`/`.oib`/`.vsi`, Imaris `.ims` or plain ImageJ TIFF stacks; Fiji (Bio-Formats) and napari
  read far more. Openly licensed readers exist for several of these (for example
  [nd2](https://github.com/tlambert03/nd2), [oiffile](https://github.com/cgohlke/oiffile)).
- **One marker channel.** Labs often find the cell on a second marker and judge the reporter, which is
  also what [Jost & Waters 2019](https://doi.org/10.1083/jcb.201812109) recommend: select regions on "a
  fluorescence channel or transmitted light image from which content of the experimental image cannot
  be deduced". wormscore shows one fluorescent marker plus brightfield.
- **No second-scorer mode yet.** Since 0.10.0 wormscore can hide repeat images and reports one scorer's
  consistency (percent agreement and Cohen's kappa) at unblinding, as Blind Analysis Tools (replicate
  copies) and Blinder (re-shown images) do. Agreement between two scorers is not supported yet. Kappa
  below 0.60 is considered inadequate ([McHugh 2012](https://doi.org/10.11613/BM.2012.031)).
- **Max projections in some measures.** `mean_mip` and `peak_filtered` use the maximum-intensity
  projection, which "shouldn't be used for intensity quantification" according to
  [Culley et al., "Made to measure" (preprint)](https://arxiv.org/abs/2302.01657) (also
  [image.sc 83431](https://forum.image.sc/t/use-avg-intensity-or-max-intensity-for-data-analysis/83431)).
  Since 0.2.0 wormscore also reports `stack_sum_minus_bg`, a background-subtracted sum over all planes,
  and the sharpest-plane mean; it has no full 3D segmentation.
- **No session correction.** No flat-field or reference-slide correction between imaging days, which
  [Waters 2009](https://doi.org/10.1083/jcb.200903097) recommends collecting "at each imaging session".
  wormscore only keeps groups apart.
- **No autofluorescence check.** Gut autofluorescence overlaps GFP
  ([Rodrigues et al. 2022, SAIBR](https://doi.org/10.1242/dev.200545)); wormscore does not measure or
  correct it.
- **Limited statistics.** Since 0.2.0 it writes penetrance tables and Fisher's exact tests (within each
  microscope, Bonferroni-corrected) and a per-neuron table for plotting; since 0.3.0 also the same tables
  for yes/no questions (for example process defects) and a binomial test of a double mutant against a
  pre-chosen interaction model; since 0.5.0 an analysis plan (definitions, comparisons, direction) that is
  locked once scoring starts. It does not fit regression models, account for uncertainty in the single
  mutant estimates, compare brightness statistically or make plots.
- **No automatic segmentation at scale.** Suggested cells come from a simple local-maximum rule. For
  many cells, use Cellpose, StarDist, ilastik, CellProfiler or QuPath.
- **No neuron identification.** You must know which cell is which. For whole-brain identification use
  NeuroPAL and NeuroPAL ID.
- **Single computer.** Scoring is local. Several scorers in different places are better served by a
  web-based tool such as [Schippers et al. 2024](https://doi.org/10.3390/jimaging10020033).
- **Platforms.** The Windows app (its own WebView2 windows since 0.9.0) is tested by hand; the Python package is tested on Windows and Linux
  in CI. The Mac app is built, tested and smoke-tested on an Apple silicon runner for each release,
  but not tested by hand.
- **Maturity.** Fiji, CellProfiler and QuPath have large user communities and years of fixes.
  wormscore does not.

## Community pain points wormscore does not address yet

From image.sc, Reddit and GitHub (collected October 2026):

- Normalizing across days when settings had to change: replies say "you need some kind of reference
  point in your data" ([image.sc 117835](https://forum.image.sc/t/rescaleintensity-module/117835)).
- TIFF exports lose the z-step ([image.sc 42830](https://forum.image.sc/t/nd2-to-tiff-with-correct-voxel-depth/42830));
  wormscore avoids this by reading raw files but cannot recover it from TIFFs that lack it.
- Faint cells are hard to call yes/no ([image.sc 120964](https://forum.image.sc/t/quantification-of-yeast-golgi-protein-colocalization-with-known-golgi-protein-sec7/120964));
  wormscore offers "dim", but no "uncertain" by default (it can be added in `config.json`).

We found almost no community discussion of rater reliability in worm scoring, and no direct reviews
of the Blind Analysis Tools plugin; ResearchGate, Bluesky and X could not be searched.
