---
title: 'wormscore: blind scoring and fluorescence measurement of identified neurons in confocal stacks'
tags:
  - Python
  - Caenorhabditis elegans
  - neuroscience
  - fluorescence microscopy
  - blinding
  - image analysis
authors:
  - name: Arjun Bansal
    # TODO(author): replace with your real ORCID iD before submitting.
    orcid: 0000-0000-0000-0000
    affiliation: 1
affiliations:
  # TODO(author): replace with your institution (and department if wanted); add a ROR ID if it has one.
  - name: TODO, Institution, Country
    index: 1
date: 25 November 2026
bibliography: paper.bib
---

# Summary

Many *C. elegans* studies describe a mutant by looking at a fluorescent reporter in identified neurons
and deciding, animal by animal, whether each neuron is on, dim or off, whether its axon reaches its
target, or how much of its dendrite is left. These calls are judgements made by a person looking at
an image. wormscore is a desktop application for making them blind and keeping a record of how they
were made. It reads the microscope's own files, gives every image a random code, and keeps the
code-to-genotype key in a folder that the scoring page never reads. The scorer answers a set of
questions fixed before scoring starts, on images shown with the same brightness range, and can
outline cells to have them measured. When scoring is finished, unblinding joins the answers to the
genotypes and writes per-image tables, penetrance per genotype, the planned statistical tests and a
draft Methods paragraph.

wormscore is written in Python and runs on Windows, macOS and any system with Python 3.12 or newer. It
is distributed as a Windows app, a macOS app and a package on PyPI, under the MIT license. Nothing is
uploaded: the scoring page is served from the user's own computer.

# Statement of need

Worm cell-fate papers commonly score each identified neuron in every animal and compare genotypes with
Fisher's exact test [@Yemini2021; @Reilly2022]. Such calls are open to observer bias. In a
text-mining study of life-science papers, about 14% reported blind data recording, and in 83 matched
pairs of evolutionary-biology experiments the non-blind ones reported effects about 27% larger
[@Holman2015]. The ARRIVE 2.0 guidelines ask authors to state who knew group allocation at each stage,
including outcome assessment [@PercieduSert2020], and microscopy guidance asks for criteria fixed in
advance, blind analysis, and the same display settings for every image being compared [@Jost2019].
Quantitative comparisons also depend on knowing the detector and laser settings behind each number
and on noticing saturated pixels [@Waters2009]. Image-reporting checklists ask for software versions,
key parameters and the regions measured [@Schmied2024].

In practice these requirements are met by hand: renaming files, keeping a key in a spreadsheet, and
setting display ranges in a general-purpose viewer. Each step can leak the genotype or lose track of
which settings a number came from. wormscore is for biologists who score identified neurons in
confocal or widefield z-stacks and want blinding, fixed display, measurement and record keeping in one
place, without writing code. It is not tied to one neuron, marker or organism: the reporter (GFP by
default; mCherry, YFP and others are settings) is chosen from each file's metadata.

# State of the field

General tools such as Fiji [@Schindelin2012] and napari [@napari] can view and measure these images,
but have no blinding built in. Fiji's Blind Analysis Tools plugin and SIAL [@Tyrpak2020] copy files
under coded names with a key, then record one decision or score per image. Blinder [@Cothren2018]
shows images in random order, records one score per image and can re-show a share of them to test a
scorer's consistency. Tyche [@Schippers2024] lets several observers score images blind in a web
browser. QuPath [@Bankhead2017] can mask image names in a project, and the mask can be turned off at
any time. NeuroPAL ID [@Yemini2021] identifies many neurons in NeuroPAL animals and records each
neuron's reporter level, but describes no blinding. Segmentation tools such as Cellpose
[@Stringer2021], ilastik [@Berg2019] and CellProfiler [@Stirling2021] measure many cells
automatically, but do not know which cell is which neuron.

None of these records several calls per identified neuron per animal under blinding, fixes the
display range per microscope across both genotypes, and links every number to its file, series and
acquisition settings. The per-image blinding tools are built around one decision per image, and
blinding a general viewer from the outside leaves the original files, file headers and name masks
open to the scorer. wormscore is therefore a separate application whose scoring page is only ever
given coded images. The comparison, with the source for
each claim about other tools, is in the repository's `docs/COMPARISON.md`.

wormscore does less than these tools in several ways. It reads fewer file formats than Bio-Formats
[@Linkert2010]. It does not identify neurons or segment cells at scale; its suggested cells come from
a simple local-maximum rule. Its statistics are limited to Fisher's exact tests, Mann-Whitney tests
and a test of genetic interaction, with no regression or mixed models. Agreement is computed between
two scorers at a time.

# Software design

A project is a plain folder. `config.json` holds the settings, every step appends the parameters it
used and the software version to `log.md`, and results are CSV files. The `key/` folder holds the
random seed and the code-to-file key.

**Reading files.** Leica `.lif`, Zeiss `.czi`, Olympus `.oib`, `.oif` and `.oir`, OME-TIFF and ImageJ
hyperstack TIFF files are read with Christoph Gohlke's `liffile`, `czifile`, `oiffile`, `oirfile` and
`tifffile`, and Nikon `.nd2` files with `nd2`. wormscore reads each format's own metadata rather than a
converted form, because the brightness of a reporter depends on detector gain, laser power and
averaging, and these are not always carried through conversion. Images on one microscope with the
same settings form a settings group, and brightness is compared only within a group. The reporter
channel is chosen from excitation line, emission band and dye or channel name; if not exactly one
channel matches, the image is set aside as unresolved instead of guessed.

**Blinding.** The scoring page is served by Python's standard-library HTTP server, listening only on
the local machine, to a browser window (an embedded WebView2 window on Windows). The server sends coded
images, their plane count, pixel size and z-step, and the scoring questions. It never reads `key/`
and never sends file names, genotypes or brightness numbers; tests check this. Images can be browsed
with names before the blind set is built; each image viewed is recorded, and browsing is locked from
the build until unblinding. The scoring questions and the analysis plan are written to `log.md` before
scoring and locked at the first saved score, and unblinding refuses to run a planned test whose plan
was changed afterwards.

**Scoring criteria.** Each neuron gets a call and a side, plus yes/no, choice, scale or count
questions per neuron, per side or per animal. Templates follow published *C. elegans* scoring: fate
reporters [@Reilly2022], axon guidance [@Schmitz2007; @Xu2015], neurodegeneration [@Bijwadia2021],
dendrite length [@Heiman2009; @Low2019], and cell counts and position [@Chng2003]. Each template names
its source in the app and in the rules it writes.

**Display and measurement.** Each microscope gets two fixed linear display ranges, computed once over
all included images of both genotypes, with no per-image autoscale. Regions drawn by the scorer, and
suggested cells, are measured on median-filtered planes (peak, projection mean, sharpest-plane mean,
local ring background, a background-subtracted sum over all planes, and saturated pixels) with NumPy
[@Harris2020] and SciPy [@Virtanen2020]. The numbers are never shown while scoring.

**Statistics.** The animal is the unit of analysis [@Lazic2018]. Unblinding computes penetrance and
two-sided Fisher's exact tests within each microscope, with a Bonferroni correction. Scales and counts
are compared with the Mann-Whitney statistic, with p-values from permuting midranks so that ties are
handled exactly. An optional test of genetic interaction compares a double mutant with the share
expected from the two single mutants under a model chosen before scoring [@Mani2008]. Hidden repeat
images measure one scorer's consistency, and a second scorer can score a package of coded images on
another computer; both are reported as percent agreement and Cohen's kappa. The draft Methods text
includes a table of who knew the genotype at each stage.

**Testing the reading.** The automated test suite (786 tests at version 1.26.23) runs on GitHub for
every change and builds the Windows and macOS apps. A tool in the repository compares wormscore's
reading of each file with Bio-Formats' and prints every difference. The values shown by the "Check the
reading" command were compared field by field with the vendors' own software (LAS X, ZEN,
NIS-Elements Viewer and OlyVIA) on real files, and the reading of files from 33 public datasets was
compared with what their authors wrote about the acquisition; the differences found were fixed. These
checks are listed in `docs/METHODS.md`.

A demonstration project of simulated worm heads, with a file of true genotypes to check calls
against, is built by one command. A second demonstration downloads four real larval heads with the
BAG neurons marked (*flp-17::GFP*) from @Sun2021 [@SunData2021].

# Research impact statement

<!-- TODO(author): this section must show realized use, which JOSS requires. Add the citation of the
paper or preprint that used wormscore (or describe the project so the editors can verify it), and any
other group that uses it. Do not submit with only the text below. -->

wormscore was developed alongside, and used from start to finish on, the author's own blind-scoring
study in *C. elegans*: 210 scored images from a Leica SP8, a Zeiss LSM 800 and a widefield
microscope, scored and then unblinded. That project's results were later recomputed with version
1.26.21, including a new scan of the files and a new build, and every count, penetrance and p-value
was unchanged. TODO(author): name the study and its publication status, and cite it.

Each release is archived on Zenodo with its own DOI under a concept DOI that lists all versions
(10.5281/zenodo.23520610), and `log.md` records which version ran each step of a project, so a study
can cite the exact version it used.

# AI usage disclosure

<!-- TODO(author): confirm this wording, name the model versions you used, and say which design
decisions were yours. JOSS asks for the tools and versions, the scope of help, and how outputs were
checked. -->

The software, its documentation and this paper were developed with substantial help from Anthropic's
Claude models, used through Claude Code. Claude wrote much of the code, tests and documentation under
the author's direction, and drafted this paper from the repository's documentation. The author
decided what the software should do and how, reviewed and edited the code and text, and checked the
software against the vendors' microscope software, Bio-Formats and real image data as described
above. Every reference in this paper was checked against its DOI record.

# Acknowledgements

<!-- TODO(author): thank people who contributed data, testing or advice, and state funding, or write
that there was no specific funding. -->

TODO.

# References
