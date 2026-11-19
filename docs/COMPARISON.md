# How wormscore compares to other tools

For researchers deciding whether wormscore fits their project. Claims about other tools were checked
against each tool's own documentation, repository or paper in November 2026; statements marked
**(inference)** are our reading, not something a source says. wormscore is young, has one developer,
and has not yet been used in a published study; weigh that accordingly.

## Short answer

| If you need to... | Consider |
|---|---|
| Score named neurons per animal (on / dim / off, yes/no questions, scales, counts) blind, with brightness traceable to each file | wormscore |
| Blind whole images and record one decision per image, inside Fiji | Fiji Blind Analysis Tools; SIAL |
| Blind whole images with a built-in repeat-scoring check, on Windows | Blinder |
| Blind scoring by several people over the web | Tyche (Schippers et al. 2024) |
| Identify many neurons in NeuroPAL animals and read reporter levels per neuron | NeuroPAL ID |
| Segment thousands of cells or nuclei automatically | Cellpose, StarDist, ilastik, CellProfiler, QuPath |
| General viewing, measurement and scripting | Fiji / ImageJ, napari |

## The problem

Worm cell-fate papers commonly score each identified neuron as on / dim / off in every animal and
compare genotypes with Fisher's exact test ([Yemini et al. 2021](https://doi.org/10.1016/j.cell.2020.12.012);
[example](https://journals.plos.org/plosgenetics/article?id=10.1371/journal.pgen.1010372)). Such calls
are judgements, and unblinded judgements are biased. In a text-mining study of life-science papers,
about 14% reported blind scoring; in 83 matched pairs of evolutionary-biology experiments, the non-blind
ones reported effects about 27% larger ([Holman et al. 2015](https://doi.org/10.1371/journal.pbio.1002190)).
The NeuroPAL paper states that "Mutant analysis studies were not blinded" (Yemini et al. 2021), which
suggests blinding is not yet routine in this kind of work **(inference from one paper; we found no
survey of C. elegans papers)**. ARRIVE 2.0 item 5 asks authors to report who knew group allocation at
each stage, including outcome assessment ([Percie du Sert et al. 2020](https://doi.org/10.1371/journal.pbio.3000411)).

## Tools compared

| Tool | What it does for this task | Blinding | Per-neuron calls | Brightness measured | License |
|---|---|---|---|---|---|
| **wormscore** | Blind scoring of identified neurons in confocal stacks, with measurement and a key back to every file | Random codes, sealed key; the scorer never sees names, genotypes or numbers | Yes | Yes, per region, with local background and saturation | MIT |
| **Fiji Blind Analysis Tools** ([page](https://imagej.net/plugins/blind-analysis-tools)) | Copies randomly selected files under encrypted names with a mapping file; opens images in random order and records one decision per image; can make replicate copies | By file name. Handles files with one series only | No (one decision per image) | No | GPL-3.0 ([repository](https://github.com/ahtsaJ/Blind-Analysis-Tools)) |
| **SIAL** ([Tyrpak et al. 2020](https://joss.theoj.org/papers/10.21105/joss.02689)) | Fiji file randomizer with a key file, and a score keeper that asks for a whole-number score per image and saves a CSV | By file name | No | No | MIT |
| **Blinder** ([Cothren et al. 2018](https://doi.org/10.21769/BioProtoc.3103)) | Shows images in random order; the user picks a score from their own scale; optional re-showing of a chosen percentage of images to test consistency | Yes | No (one score per image) | No | Free, no license stated; Windows only |
| **Tyche** ([Schippers et al. 2024](https://doi.org/10.3390/jimaging10020033)) | Experts open images through temporary links and score them "blind in a random order inside a web browser"; several observers | Yes | Not described | Not described | No code or license published |
| **NeuroPAL ID** ([instructions](https://www.hobertlab.org/wp-content/uploads/2020/06/Software-Instructions.pdf), [repo](https://github.com/Yemini-Lab/NeuroPAL_ID)) | Semi-automatic identification of neurons in NeuroPAL images; saves each neuron's reporter (GFP) value with background, an Otsu threshold and a change-point threshold | Not described | Yes, many neurons at once | Yes | CC BY-NC-SA 4.0 (non-commercial) |
| **QuPath** ([docs](https://qupath.readthedocs.io/en/stable/docs/tutorials/projects.html)) | Whole-slide and tissue analysis, cell detection, classification | Image names can be masked in a project, and unmasked at any time | Cell classification | Yes | GPL-3.0 |
| **Fiji / ImageJ** ([repo](https://github.com/fiji/fiji)) | General viewer and measurement, Bio-Formats import, macros | Only with plugins or macros | Manual (ROI Manager, Cell Counter) | Yes, manual | GPL-3.0 |
| **napari** ([repo](https://github.com/napari/napari)) | n-dimensional viewer with plugins | None built in; we found no blinding plugin | Through plugins | Through plugins | BSD-3-Clause |
| **CellProfiler** ([repo](https://github.com/CellProfiler/CellProfiler)) | Batch segmentation and measurement pipelines | None described | Through segmentation, not identity | Yes | BSD-3-Clause |
| **ilastik** ([repo](https://github.com/ilastik/ilastik)) | Interactive machine-learning pixel and object classification | No | Through classification | Yes | GPL-2.0 or later |
| **Cellpose**, **StarDist** ([cellpose](https://github.com/MouseLand/cellpose), [stardist](https://github.com/stardist/stardist)) | Deep-learning segmentation of cells or nuclei | No | Segmentation only | Through other tools | BSD-3-Clause |
| **Vendor software** (Leica LAS X, Zeiss ZEN, Nikon NIS-Elements), **Imaris**, **arivis** | Acquisition and commercial analysis | We found no documented blinding feature **(inference)** | Varies | Yes | Commercial |

Smaller blinding helpers also exist: the [Filename Randomizer](https://wsr.imagej.net/macros/Filename_Randomizer.txt)
ImageJ macro (its copies keep the original name in the image header), the R packages
[blinded](https://github.com/jeffreypullin/blinded) and blaseRtools, and OMERO PathViewer's blind mode
([docs](https://omero.hms.harvard.edu/static/pathviewer/docs/blindworkflows.html); commercial, and it
hides names only in its own viewer).

## Where wormscore does more

| Need | What other tools do | What wormscore does |
|---|---|---|
| Blinding that does not leak | Renaming tools copy files and leave the originals openable; names can survive in file headers (Filename Randomizer); QuPath's masking can be switched off at any time | The scoring page receives the coded images with their plane count, pixel size and z-step, and the scoring criteria. Names, paths, genotypes and brightness numbers stay in `key/` and the outputs, which the scoring app never sends. Tests check this. |
| Calls per neuron, not per image | The blinding tools above record one decision per image | Each named neuron gets its own call and side, plus your own questions (yes/no, choice, scale, count) per neuron, per side or per animal. |
| The same display for every image | Judging autoscaled images "could lead to inaccurate conclusions" ([Jost & Waters 2019](https://doi.org/10.1083/jcb.201812109)) | Two fixed display ranges per microscope, computed once over all images of both genotypes. No per-image autoscale. |
| Knowing which settings each number came from | Bio-Formats reports laser settings for one laser only ([#3513](https://github.com/ome/bioformats/issues/3513)), mixes up LIF channels ([#3896](https://github.com/ome/bioformats/issues/3896)) and misreads CZI detector gain ([#4029](https://github.com/ome/bioformats/issues/4029)); all three were open in November 2026 | Reads each format's own metadata, records gain, laser power and averaging per image, flags images whose settings differ, and compares brightness only within a settings group. `wormscore check-reading` shows what it read from a file and where the microscope software shows the same value. |
| Channel identity from metadata | Channels can be read in the wrong order (Bio-Formats #3896) | The reporter is chosen from excitation line, emission band and dye or channel name. If not exactly one channel matches, the image is left out as UNRESOLVED, not guessed. |
| Saturation noticed early | Saturated images "cannot be used for quantitation" ([Waters 2009](https://doi.org/10.1083/jcb.200903097)) | Saturated pixels are counted per region, and the scorer is asked to confirm when a suggested cell is saturated. |
| Every number traceable | Reporting checklists ask for software versions, key parameters and manual ROIs ([Schmied et al. 2024](https://doi.org/10.1038/s41592-023-01987-9)) | Each row links to the original path, local copy, series and SHA-256; every run writes parameters and software versions to `log.md`; drawn regions are saved. |
| Looking first without breaking blinding later | Jost & Waters 2019 ask for criteria fixed ahead of time and blind analysis for the test | **Browse images** shows names and genotypes before the build, records every image viewed (log, CSV, Methods draft), and is locked from Build blind set until Unblind. The scoring criteria and the analysis plan are recorded before scoring and locked at the first score. |
| Reliability | Blind Analysis Tools and Blinder re-show images to one scorer | Hidden repeats (one scorer's consistency) and a second-scorer package (agreement between two people), with percent agreement and Cohen's kappa. |
| No scripting | | Point-and-click app; no code needed. |

## Where wormscore is not the right tool

- **File formats.** It reads Leica `.lif`, Zeiss `.czi`, Nikon `.nd2`, Olympus `.oib`, `.oif` and `.oir`,
  OME-TIFF and (when turned on) ImageJ hyperstack TIFF. It does not read Olympus `.vsi`, Imaris `.ims`
  and many others; Fiji (Bio-Formats) and napari read far more.
- **No neuron identification.** You must know which cell is which. For whole-brain identification use
  NeuroPAL and NeuroPAL ID. NeuroPAL colours can be shown to help find cells, but are never measured.
- **No automatic segmentation at scale.** Suggested cells come from a simple local-maximum rule. For
  many cells, use Cellpose, StarDist, ilastik, CellProfiler or QuPath.
- **Simple statistics.** Fisher's exact tests, Mann-Whitney tests for scales and counts, and a test of
  genetic interaction against a chosen model. No regression or mixed models (for example with animal
  as a random effect, or covariates), no Brunner-Munzel test, no plots.
- **Two scorers at a time.** Each second scorer is compared with the primary scorer on their own; there
  is no Krippendorff's alpha for three or more scorers, no consensus or adjudication step, and no
  agreement statistic for drawn regions.
- **Max projections in some measures.** `mean_mip` and `peak_filtered` use the maximum-intensity
  projection, which "shouldn't be used for intensity quantification"
  ([Culley et al., preprint](https://arxiv.org/abs/2302.01657); published in
  [J Microsc 295:61](https://doi.org/10.1111/jmi.13208)). wormscore also reports
  `stack_sum_minus_bg`, a background-subtracted sum over all planes, and the sharpest-plane mean; it
  has no full 3D segmentation.
- **Corrections it reports but does not apply.** Bleed-through between channels is measured on
  single-label controls, and gut autofluorescence is flagged after unblinding; neither is subtracted.
  There is no chromatic-shift correction. Flat-field correction is applied when you supply slide images.
- **Single computer.** Scoring is local. A second scorer elsewhere gets a package folder to score on
  their own computer; many scorers working at once are better served by a web-based tool such as Tyche.
- **Maturity.** Fiji, CellProfiler and QuPath have large user communities and years of fixes. wormscore
  does not. GitHub builds the Windows and Mac apps on every change and installs an update over them,
  but it has few users so far.

## Community threads

These forum threads describe the problems above in users' words. They were collected in October 2026;
in November 2026 the forums blocked automated access, so the quotes were not checked again.

- Renamed `.czi` files still show the original name in Fiji
  ([image.sc 107354](https://forum.image.sc/t/renaming-czi-files-impossible/107354),
  [image.sc 61555](https://forum.image.sc/t/how-to-fully-blind-an-image-hide-original-image-name/61555)).
- Cell Counter cannot attach several labels to one cell
  ([image.sc 98534](https://forum.image.sc/t/assigning-multiple-labels-to-one-cell/98534)).
- Display settings differ between images
  ([image.sc 117643](https://forum.image.sc/t/how-to-keep-source-brightness-setting-when-uploading-for-analysis/117643),
  [image.sc 120964](https://forum.image.sc/t/quantification-of-yeast-golgi-protein-colocalization-with-known-golgi-protein-sec7/120964)).
- LIF channel order and names read wrongly
  ([image.sc 73038](https://forum.image.sc/t/bad-reading-of-lif-metadata/73038)).
- Saturation discovered at analysis time
  ([image.sc 103263](https://forum.image.sc/t/overexposure-high-signal-intensity-in-roi-of-confocal-images/103263)).
- Writing a blind-scoring macro is harder than scoring by hand
  ([image.sc 23869](https://forum.image.sc/t/blind-scoring-macro-for-imagej/23869)).
- TIFF exports lose the z-step
  ([image.sc 42830](https://forum.image.sc/t/nd2-to-tiff-with-correct-voxel-depth/42830)); wormscore
  reads raw files but cannot recover it from TIFFs that lack it.
- Normalizing across days when settings had to change
  ([image.sc 117835](https://forum.image.sc/t/rescaleintensity-module/117835)).

We found almost no community discussion of rater reliability in worm scoring, and no reviews of the
Blind Analysis Tools plugin.
