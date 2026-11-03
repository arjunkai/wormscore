# What wormscore does, step by step

Written so it can be adapted into a Methods section. Every number named here is a setting in the
project's `config.json`, and the values actually used are copied into `log.md` on every run.

## 1. Inventory

Every file in every source folder is listed in `outputs/manifest.csv` with its SHA-256 checksum and a
role: raw image; duplicate (byte-identical to another file; the copy kept is set by
`duplicate_preference`, otherwise the shortest path); derived (projections, channel splits and exports
recognized by Fiji-style names such as `MAX_`, `Projections of`, `C1-`); metadata sidecar; other image
without microscope metadata; reference document; or hidden system file (for example macOS `._` files).

Each raw image (each series of a `.lif`, each scene of a `.czi`, each series of an OME-TIFF, each stage
position of a Nikon `.nd2`, each Olympus FluoView `.oib` or `.oif`, and each ImageJ hyperstack TIFF in a
source that turns ImageJ reading on) is listed
in `outputs/inventory.csv` with objective, planes, pixel size, z-step, bit depth, channels and the
acquisition settings that affect brightness (detector gain or voltage, laser power, averaging, exposure).
Images whose settings differ from the most common value on the same microscope are flagged.

## 2. Which channel is the marker

From metadata only:

- **Leica:** the active detectors of each sequential-scan step, in order, are the image channels.
  The marker is the channel whose detector had a configured excitation laser on (default 488 nm), an
  emission band starting in the configured range (default 490 to 530 nm), and, if set, the configured
  lookup-table color. Brightfield is the transmitted-light detector.
- **Zeiss and OME-TIFF:** the marker is the channel whose fluorophore, channel name or excitation
  wavelength matches the configuration; brightfield is the channel recorded with transmitted light
  (or named BF, Brightfield, DIC, Trans, T-PMT, T PMT). If several channels match, for example a
  second fluorophore excited by the same 488 nm laser, and exactly one of them names a configured dye
  (EGFP, GFP), that one is the marker. If the file gives no objective name, the objective is written
  from its magnification, NA and immersion.
- **Nikon and Olympus FluoView:** the same rules, using the channel name, dye name and excitation
  wavelength the file records; brightfield is the channel recorded as brightfield or transmitted light
  (Olympus detectors named TD). These files also record an emission wavelength, which counts only if
  `marker.emission_nm` gives a range (for example `[500, 550]` for GFP). Olympus numbers written with a
  decimal comma (`0,207`) are read as decimals. Colour (RGB) camera pictures are not used.
- **ImageJ hyperstack TIFF:** these files record pixel size and z-step but not which channel is which.
  They are used only if their source in `config.json` has `imagej_tiff` with the channel order, for
  example `{"channels": ["marker", "brightfield"]}`, and optionally the objective; that setting is
  copied into `log.md` when the blind set is built. If the file has a different number of channels the
  image is UNRESOLVED. Projections made later (names starting `MAX_`, `SUM_` and so on) are never used.

If not exactly one channel matches, the marker is UNRESOLVED and the image is excluded.

**Other fluorescent proteins** (since 1.0.0). `"marker": {"preset": "mCherry"}` replaces the GFP
settings with those of another protein: GFP, mNeonGreen, YFP, mCherry, TagRFP, mScarlet, mKate2 or BFP.
Each preset gives the laser lines that excite it, where a Leica detection band starts, an emission range
for files that record one (Nikon, Olympus), the dye and channel names microscopes write for it, and its
display color, from FPbase peaks (for example mCherry 587/610 nm: lasers 561, 587 or 594 nm, band
starting 575 to 625 nm, emission 595 to 645 nm). Any field given next to the preset overrides it. Without
a preset the GFP settings above are used exactly as before, so GFP projects build the same blind set.

**Identity channel** (optional, since 1.0.0). `"identity": {"preset": "mCherry"}` names a second
marker that labels the cells independently of the reporter being scored, such as a pan-neuronal or
cell-identity marker. It is found by the same rules, among the channels that are not the marker (the
reporter is tested first, so a channel is never both); if two channels match, the one naming a
configured dye is used, otherwise it is UNRESOLVED. One identity channel is supported; a multi-color
identity system such as NeuroPAL is not.

## 3. Inclusion rules

Decided before scoring and applied without regard to genotype. An image is used if its marker channel
was identified, its objective is at least `min_objective_magnification` (when the metadata names one),
it has no more than `max_time_points`, and its z-stack covers at least `min_z_extent_um`
((planes - 1) x step). Images can also be left out by file name with a per-source `exclude` rule
(`{"match": pattern, "reason": text}`), for example a plate found to carry a different reporter; the
reason is written to `excluded.csv`. Such rules should be set before scoring. In a project with an
identity channel, an image without one is excluded too (`identity.required`, default true), so every
scored image had its cells found the same way; with `required: false` it is kept, scored on the reporter
alone, and marked in the key (`identity_channel_used`). The same image saved inside two different files (identical pixels, found by a checksum of
every pixel recorded at inventory) is coded only once; the other copy is listed in `excluded.csv`. An image is never excluded for having no fluorescent signal: a missing cell is
a result. Images taken at the same stage position in the same file are treated as the same animal
imaged again; only one is counted (by default the one with more planes; the scorer can change this
while still blind).

## 4. Blinding

Included images are shuffled with a random seed and numbered W0001, W0002, ... The seed and the
code-to-file key are written to `key/`, which the scoring app never reads. The scoring page receives
images, plane counts, pixel size, z step and quality questions, but no genotype, file name, path or brightness
number. Re-running `prepare` keeps the same codes; new codes are refused once any score is saved.
Unsaved answers are kept per code in `scoring_drafts/` (the same fields as a score, no genotype or
file name) and deleted when the score is saved; drafts are never read as scores and do not lock the
codes. New codes set any drafts aside (`scoring_drafts/superseded_<time>.json`), so answers typed for an
old code never open on the image that now has that code. Images are fetched at addresses that name the
project and the build, so a picture cached from another project or an earlier build is never shown. Each scoring window sends its project's ID with every request, so a window left open from
another project cannot save into the one being scored.

Images imaged later can be added with `prepare --add-new-images` (or **Add new images** in the app's
build step once scoring has started). Only images not yet in the key are coded: their codes continue after the
highest existing code, shuffled among themselves with a new seed (kept in `key/`). Existing codes,
blind images, settings-group letters and display ranges are not changed, so images already scored look
exactly the same; a new microscope gets its own ranges. Adding refuses to run if any coded image would be
dropped. Which codes were added, and when, is recorded only in the key (`added_on`), and the draft Methods
text reports each added batch. Limit: the scorer can tell which images arrived later. If a later batch is
all one genotype, blinding is weaker for it, so mix genotypes in each batch where possible, or add all
images before scoring starts.

**Hidden repeats** (optional, `reliability.repeat_fraction`, off by default). When coding, this share
of each genotype's images (at least one per genotype) is chosen at random and coded a second time, mixed
in with the rest. Each repeat is placed at least 15% of the set away from its original in code order (the
order of the scoring list). Only images that are counted by default and have no other image of the same
field are chosen, so a repeat shows no "same stage position" note or different default; its blind files
are byte-identical copies of the original's apart from the code in `meta.json`. Which codes are repeats is
recorded only in the key (`repeat_of`); the log gives only their number. Display ranges are computed from
originals only. Re-running `prepare` keeps the same repeats; adding images adds none. Repeats are never
counted in any result; at unblinding, `reliability.csv` compares each scored repeat with its original
(see section 8). Limit: someone who opens `blind/` and compares files can find the repeats, as they can
find any other information there; score only through the app.

**Second scorer** (optional, made from the app or with `export-scorer`). The project owner makes a
package for another person after the blind set is built. A share of each genotype's included images
(chosen by the owner from 1 to 100%, at least one per genotype) is picked at random with a seed that is
not stored, together with the hidden repeats of the picked images, and put in a new random order with
each repeat kept apart from its original (the same 15% rule). The package is an ordinary project folder
with only those `blind/` folders and a `config.json` holding the scoring form, the marker and display
settings and the region measurement settings. It has no `key/`, `outputs/`, `log.md`, `scoring/`,
image sources, analysis plan, file names or checksums (repeats are byte copies of their originals, so a
checksum list would show which codes are repeats). Its scoring list follows the package's own order.
Scanning, building and unblinding are refused inside a package. The owner's project keeps the
selection, the order and a SHA-256 checksum of every file of every packaged code in
`scorers/<id>/package.json`, and `log.md` records the scorer's name, the share and the number of
images. When the package comes back, import refuses it unless the package ID was made in this
project, the scoring form (its answer fields and allowed answers) is unchanged on both sides, the
packaged images and the project's own images match the checksums, no codes were added or removed,
every column and answer of `scores.csv` comes from the form, and nothing in the package is a link.
Only the scoring files are copied, to `scorers/<id>/scoring/`; the owner's `scoring/` is never
written. Limits: the owner knows the results if the package is made after unblinding (the images are
still picked at random, and the Methods text says when the second scorer scored); and the second
scorer is trusted to score alone and not to compare raw files.

## 5. Images shown

For each image the marker channel is saved plane by plane and as a maximum-intensity projection
(each pixel takes its brightest value across planes), with exact recorded values. Display is linear
with two fixed ranges per microscope, the same for every image from that microscope:

- **Standard:** 0 to the detector maximum (for example 255 for 8-bit, 65535 for 16-bit).
- **Enhanced:** from the median, over images, of each image's median projection value (typical
  background) to the median, over images, of each image's 99.9th percentile (typical bright spot).
  Computed once over all included images of both genotypes from that microscope, so it depends on which
  images are in the set; images added later to an existing key keep their microscope's range.

Images are drawn at a common physical scale (`display_um_per_px`) so microscope zoom does not change
apparent cell size. A **3D** view turns the stack about the vertical axis (-60 to +60 degrees in
5-degree steps; 0 is the maximum projection): each screen pixel is the brightest value along the
turned line of sight, with z stretched by z step / pixel size and up to 6 planes blended in between
neighbouring planes (the same as Fiji's 3D Project, brightest point, with interpolation). It uses the
same fixed display ranges, has no depth cueing (which would change apparent brightness), is drawn in
the browser from the planes already shown, and is for viewing only: nothing is measured or saved from
it, and regions are drawn on the projection or a plane. Images over 1.5 million pixels are turned at
half size (each pixel the brightest of 2 x 2) to stay quick. Brightfield (middle plane, contrast-stretched per image) can be overlaid; it is
never measured.

**Identity channel.** Saved plane by plane and as a projection for display only, as 8-bit images,
each image scaled on its own from the median of its projection (background) to its 99.9th percentile
(`display.identity_contrast_percentiles`). It is scaled per image on purpose: it is only for finding
the cell, and if its brightness differed between genotypes a fixed range could hint at the genotype;
the scoring page receives no identity brightness numbers. The scorer can show the reporter, the identity
channel or both (keys 1, 2 and 3), each in its own color, added together: the reporter in its preset's
color (green for GFP) and the identity channel in magenta (or green when the reporter is magenta), so a
cell with both looks white. The 3D view turns the reporter only.

## 6. Measurements

Before measuring, each plane is median-filtered (3 x 3 pixels by default), so a single hot pixel or a
one-pixel scan streak cannot become a peak. For each region:

| Measure | Definition |
|---|---|
| peak_filtered | brightest filtered pixel in the region across all planes |
| mean_mip | mean raw value in the region in the projection |
| best_z | plane where the region's filtered mean is highest (sharpest plane) |
| mean_best_plane | mean raw value in the region in that plane |
| background | median of a ring starting 2 um outside the region, 4 um wide, excluding other regions (projection and sharpest plane) |
| *_minus_bg | the value minus its background |
| integrated_best_minus_bg | sum over the region of (pixel - background) in the sharpest plane |
| stack_sum_minus_bg | sum over every plane and every region pixel of (pixel - that plane's own ring background): a background-subtracted sum projection. Unlike the maximum projection it keeps the light from all planes. It grows with the number of planes covering the cell and with the z-step, so compare it only between stacks of the same shape; the unblinded summary warns when a settings group mixes shapes |
| saturated_px_mip | pixels at the detector maximum (peak is then an underestimate) |
| best_z_at_stack_edge | sharpest plane is the first or last (cell may be cut off) |
| background_sd_best_plane | only when `unblinding.dim_cutoff_sd` is set: background noise, the robust SD (1.4826 x median absolute deviation) of the ring pixels in the sharpest plane |

Regions come from the scorer (drawn circle or outline, or an accepted suggestion). Suggestions are
found automatically: the filtered projection is smoothed with a Gaussian (1.5 um), local maxima (each
the brightest point of the 6 um square around it, so maxima about 3 um or more apart are kept) are
taken brightest first, and each is grown to every connected pixel at or above half
of its height above the local background (median of a ring 6 to 10 um away), within 8 um, keeping
regions of at least 4 um^2 (full width at half maximum). Suggestions are measured too
(`outputs/auto_measurements.csv`); they only point at places to look.

With an identity channel (`identity.spots_from`, default `identity`), suggestions are found the same way
on the identity channel's median-filtered projection, and the reporter is measured in those regions
(column `spots_found_on`). Cells are then located without using the reporter being scored, so a dim or
missing reporter cell is still found and measured, as recommended by Jost & Waters (2019): select regions
on "a fluorescence channel or transmitted light image from which content of the experimental image cannot
be deduced". The sharpest plane is still the plane where the reporter is brightest in the region.
`spots_from: "reporter"` finds them on the reporter, as without an identity channel.

## 7. Questions asked of the scorer

The score form (set on the Project settings page or in `config.json`) asks, for each image: whether it
is usable (and if not, why), the count question, and for each neuron its call, optionally its side, and
any extra questions; since 1.4.0 a question can instead be asked once per animal (`scoring.animal_questions`,
saved as `a_<id>`, shown under "This animal"). Extra questions are yes/no, a choice, an ordered scale (levels
listed best first, worst last; `"type": "scale"`) or a count (whole numbers going up by one, the last
optionally "N+" for N or more; `"type": "count"`). A scale or count may have a "can't tell" answer
(`cant_tell`), which is never a level. A required question must be answered before the score can be saved,
and a question for each neuron can be skipped (hidden, saved empty) when the neuron's call is one the
question's outcome leaves out, such as "not seen". Answers are stored as their text.

The form is recorded in `log.md` ("Score form set before scoring") when it is saved in the app, when the
blind set is built and when the first score is saved. From the first score, or once a second-scorer
package exists, the app does not let it change. The record covers what decides the stored answers and
their analysis: neurons, calls, defect calls, sides, count answers, each question's id, answers, whether it
is required and when it is skipped, the question outcomes and the primary outcome; since 1.4.0 also each
question's type and "can't tell" answer, the questions asked once per animal and the rank outcomes (these
appear only when used, so forms recorded by 1.3.0 still match). Wording (question text,
rules shown to the scorer, unusable reasons) is not part of it and may still be corrected.

The app asks for confirmation when: an image shares a stage position with another (repeat); the
brightest suggested spot is sharpest in the first or last plane; a suggested cell is saturated; or a
horizontal scan-line artifact is present. Answers are saved with the score. Every save is also appended
to `scoring/score_history.csv`.

## 8. Unblinding

Allowed only after the scorer confirms scoring is finished and at least `min_scored_worms` images are
scored (an override is possible and is recorded in `log.md`). `outputs/unblinded/merged.csv` holds, for
every image, the key (original path, local copy, location in file, checksum, genotype, microscope,
settings group), the score, and the measurements. The summary counts calls per microscope and genotype
and lists drawn-region brightness by call within each settings group only.

Results tables use counted, usable images only, with the animal as the unit (one image per animal):

- `per_neuron.csv`: one row per animal and neuron (genotype, microscope, settings group, imaging date,
  call, affected yes/no, region measurements), ready for plots that show every animal, such as SuperPlots.
- `penetrance.csv`: per microscope and genotype, the number of animals with 0, 1, 2, ... affected
  neurons (a neuron is affected when its call is one of `defect_calls`), the share with at least one
  (penetrance), and the share affected for each neuron. Animals with a neuron left uncalled are counted
  separately and left out of the percentages.
- `fisher_tests.csv`: two-sided Fisher's exact test on animals with versus without an affected neuron,
  comparing each genotype with `reference_genotype` (or every pair of genotypes if none is set). Tests
  are run only between genotypes imaged on the same microscope, because calls made on different
  microscopes are not interchangeable. p-values are Bonferroni-corrected for the number of tests in the
  file. Other tests (for example per neuron) can be run from `per_neuron.csv`; correct them too.
- `question_outcomes.csv` and `question_outcome_tests.csv` (only if `unblinding.question_outcomes` is
  set): the same penetrance and Fisher's exact tables, built from the per-neuron questions instead of the
  calls. For each outcome the config names the questions, the answers that count as a defect (for
  example `no`) and the answers that count as unclear (for example `can't tell`). A neuron is defective
  if any listed question has a defect answer. A neuron whose call is in `not_visible_calls` (default
  `missing`) is not assessed, because its processes cannot be judged. An unclear answer leaves out that
  neuron (`"unclear": "exclude_neuron"`, the default) or the whole animal (`"exclude_animal"`). An
  animal is defective if any assessed neuron is defective; animals with no assessed neuron are left out
  and counted in their own columns, as are animals with a question left unanswered. `per_neuron.csv`
  gets one column per outcome with each neuron's state. An outcome may instead combine questions asked
  once per animal (not mixed with neuron questions): the animal is defective if any answer is a defect.
- `rank_outcomes.csv` and `rank_outcome_tests.csv` (only if `unblinding.rank_outcomes` is set, 1.4.0):
  ordered scale and count questions, each tested on its own. Each entry names the outcome, the question,
  and for a question asked for each neuron how the neurons give one value per animal (the animal is the
  unit; neurons are subsamples, [Lazic et al. 2018](https://doi.org/10.1371/journal.pbio.2005282)):
  - scale: `worst` (default; the highest level of any neuron) or `mean` (mean level of the assessed
    neurons, which treats levels as equally spaced);
  - count: `sum` (default; the total over the neurons) or `mean_per_neuron`.

  Levels are scored 0, 1, 2 ... in order; counts by their number, with "N+" counted as N (a lower
  bound, said in the summary). `worst` and `sum` need every neuron assessed, because the worst of four
  neurons tends to be higher than the worst of three; otherwise the animal is left out, except that an
  animal whose assessed neuron already has the worst level counts under `worst`. "Can't tell" is never
  a level: that neuron is unclear (under `mean`, `"unclear": "exclude_animal"` leaves out the animal
  instead). Neurons skipped by their call are "not assessed", kept apart from unclear. An unanswered
  question leaves the animal out as incomplete. `rank_outcomes.csv` gives, per microscope and genotype,
  the animals with a value, the animals left out by reason, the median and quartiles (linear
  interpolation, as Excel QUARTILE.INC), the % at each level (scales whose value is a level) or the mean
  (counts), and the level key; the summary warns when the share left out differs by more than 20
  points between groups. Genotypes are compared within each microscope, for the same pairs as Fisher's
  test, with the Mann-Whitney (Wilcoxon rank-sum) statistic on the animal values. The p-value comes from
  permuting the pooled midranks, so ties are handled correctly: exact (every rearrangement) when there
  are at most 100,000 rearrangements, otherwise 100,000 random ones with seed 1404 (recorded in the
  table). scipy's default for tied data is a normal approximation, which gave p-values about half the
  correct size on short scales with many ties in checks made for this feature; GraphPad Prism also
  reports exact p-values with ties. If every animal has the same value, p = 1. Bonferroni within each
  outcome. Effect size: `prob_a_worse` = U / (n_a n_b), the chance that a random genotype_a animal scores
  worse than a random genotype_b animal, ties counted half (Vargha and Delaney's A), and Cliff's delta
  = 2A - 1. The test is about that probability, not about medians, which are often equal on short
  scales. Normality is not tested because a rank test is used (ARRIVE 2.0 item 7b). An optional
  `cutoff` (a level after the first for a scale, a number of 1 or more for a count), set before
  scoring, also gives the share of animals at or past it, tested with Fisher's exact test (rows marked
  with the cutoff in `definition`). `per_neuron.csv` gets each neuron's answer and the animal's value.
  Rank outcomes cannot be used by `interaction` or `allow_cross_microscope`. Left out on purpose:
  Brunner-Munzel (its p-values were far too small at 5 animals per group, and scipy has no permuted
  version), t-tests on counts, and regression or mixed models.
- `interaction_test.csv` (only if `unblinding.interaction` is set): a test of genetic interaction
  between two single mutants (P1, P2 = share of affected animals) and their double. The expected share
  in the double comes from one model, chosen before scoring (Mani et al. 2008, PNAS 105:3461, applied to
  the share of unaffected animals): `product` (default) 1 - (1 - P1)(1 - P2), the share expected if the
  two genes act independently; `additive` P1 + P2 (at most 1); `min` the larger of P1 and P2. The
  observed count in the double is compared with the expected share by a two-sided one-sample binomial
  test (`scipy.stats.binomtest`), only within a microscope where all three genotypes were scored
  (Bonferroni if more than one). A double above the expected share is consistent with the genes acting
  redundantly; this is a statistical statement, not proof of mechanism. The expected value treats P1
  and P2 as exact and ignores their sampling error, so the test is anti-conservative when the single
  mutant groups are small: in simulations with 30 animals per genotype and no interaction, it was
  significant at 0.05 in about 7 to 19% of runs. `interaction.test: "likelihood_ratio"` (recommended
  for new projects; part of the analysis plan) instead fits the three genotypes together: under the
  model the double's share is the model's function of P1 and P2, fitted with them by maximum likelihood,
  and the likelihood ratio G is compared with chi-square on 1 degree of freedom (for a one-sided
  alternative, the signed square root of G with the normal distribution). It allows for the singles'
  sampling error and stayed near 5% in the same simulations. The table then has `likelihood_ratio_g`
  and `p_likelihood_ratio_*`. `outcome` chooses the calls (default) or a `question_outcomes` name.
  The model is part of the analysis plan that `prepare` records and unblinding checks (below), so
  nobody can try all three models after seeing results.
- **Optional analysis settings** (all in `unblinding`, all off by default):
  - `secondary_defect_calls`: a second definition of "affected" (for example dim or missing, when the
    primary `defect_calls` is missing only). It is reported per microscope with 95% Wilson intervals and
    is never pooled over microscopes. It is tested (Fisher's exact, within one microscope) only if
    `test_secondary` is true. Use this when one call (such as dim) depends on brightness, which is not
    comparable between microscopes, while another (present or missing) is.
  - `allow_cross_microscope`: the measures (`calls` or a `question_outcomes` name) whose tests may compare
    genotypes imaged on different microscopes. For those measures each genotype's animals are pooled over
    microscopes, every table lists the microscopes and the number of animals from each, and every test
    whose genotypes come from different microscopes carries the warning "microscopes differ between
    genotypes". Pooling hides differences in sensitivity between instruments (for example a faint cell is
    more likely to be called missing on a less sensitive system); state this as a limitation.
  - `comparisons`: the planned `[genotype_a, genotype_b]` pairs. Only these are tested, so the Bonferroni
    correction counts only the planned tests.
  - `alternative`: `two-sided` (default), `greater` or `less`, for Fisher's exact and the binomial test.
    A one-sided test needs `comparisons` (the direction is genotype_a compared with genotype_b; for the
    binomial test, the double compared with the expected value). `interaction.expected_direction`
    (`higher` or `lower`) states the hypothesis; the interaction table then says whether the result
    supports it (significant after correction and in that direction).
  - Source `note`: a label such as "collected by others" on a source in `sources`; tables list the
    sources of each group with an asterisk and the summary explains it.
  - `dim_cutoff_sd` (k): a measured line, set before scoring, for checking calls afterwards. A measured
    neuron (drawn region or accepted suggestion) is below the cutoff when its background-subtracted mean
    in the sharpest plane is less than k times `background_sd_best_plane`. `dim_check.csv` lists, per
    settings group and call, how many measured neurons fall below the cutoff and the range of signal /
    noise. A "normal" or "dim" call below the cutoff is a cell that could not be told from background
    by this measure; review it, but calls are never changed by numbers. Neurons called missing have no
    region and are not in this table. Compare only within a settings group. Part of the analysis plan.
    It is a contrast-to-noise visibility check (is the cell distinguishable from its own surroundings),
    not a comparison with control brightness. It needs drawn regions: outline every visible neuron if you
    set it. Regions whose ring has no measurable noise (robust SD 0, common in 8-bit or low-count images)
    cannot be checked; the summary counts them.
- **Scorer consistency** (when the project has hidden repeats): `reliability.csv` gives, over the pairs
  where both the repeat and its original were scored, percent agreement and Cohen's kappa for: usable or
  not; animal affected (primary, and secondary if set); each neuron's call (linear-weighted kappa over the
  ordered calls); cells seen (linear-weighted); each `question_outcomes` measure; and each scale rank
  outcome whose animal value is a level (linear-weighted). Neurons are matched
  by the recorded side when both images name each neuron's side, otherwise by number. Kappa is undefined
  when every answer is the same. With few pairs, kappa is imprecise; kappa below 0.60 is usually
  considered inadequate ([McHugh 2012](https://doi.org/10.11613/BM.2012.031)). The same scorer seeing an
  image twice measures consistency, not agreement between people. When most animals get the same answer
  (for example few affected animals), kappa can be low even with high agreement ([Feinstein & Cicchetti
  1990](https://doi.org/10.1016/0895-4356(90)90158-L)); report percent agreement with it. Optional, in
  `reliability`: `extra_statistics: true` adds a 95% bootstrap interval for kappa (pairs resampled 2,000
  times, fixed seed), PABAK and Gwet's AC1 (both from exact agreement); `call_weights` sets the kappa
  weights for neuron calls: `linear` (default, for ordered calls such as normal, dim, missing),
  `quadratic`, or `none` (for calls that are not ordered).
- **Agreement between scorers** (only when a second scorer's scores were imported). `between_scorers.csv`
  compares the primary scorer's and each second scorer's answers on the original images both scored
  (repeats are left out, so no image counts twice), with the same measures and statistics as for
  hidden repeats (percent agreement, Cohen's kappa, weighted for ordered calls, and the optional extra
  statistics). `scorer_disagreements.csv` lists each code and answer (usable, cells seen, each neuron's
  call) where the two differ, with no genotype. Every results table uses the primary scorer's answers
  only; the second scorer's answers are never merged in or used to settle disagreements. The Methods
  draft says how many images the second scorer scored, the share, when, and that they scored blind,
  without the key, in a different order and without seeing the first scorer's answers (and, if they
  scored after the primary analysis was unblinded, says so).
- **Analysis plan lock.** When any of the settings above, `question_outcomes`, `rank_outcomes` or `interaction` is set,
  `prepare` writes the whole analysis plan (both definitions of affected, comparisons, direction,
  cross-microscope measures, interaction model, dim cutoff) to `log.md`. Unblinding compares the current settings with
  the last plan recorded before the first score was saved. If they differ, no test is run and every test
  file says why; descriptive tables are still written. If no plan was recorded before scoring, tests run
  but are marked `set_before_scoring = NO`.
- **Score form lock.** Unblinding compares the current form with the last one recorded before the first
  score (section 7). If it differs, no test is run, every test file says the form changed, and the
  Methods draft says only descriptive results are reported. Projects built before 1.3.0 have no form
  line; for them the full settings that Build wrote to `log.md` before the first score are used. If those
  differ or are missing, tests run but are marked `form_recorded_before_scoring = NO` and the summary
  warns.
- **Primary outcome.** `unblinding.primary_outcome` (`calls`, a `question_outcomes` name or a `rank_outcomes` name, part of the
  analysis plan) adds an `outcome_role` column (primary or secondary) to every test file, a line in the
  summary and a Methods sentence. Bonferroni correction is still within each file.
- `methods_text.md`: a draft Methods paragraph built from the project files: inclusion counts, the date
  codes were assigned (date of `key/seed.txt`), the scoring dates (`scoring/score_history.csv`), the
  unblinding date, measurement parameters and the tests run, plus a table of who knew the genotype at
  each stage (ARRIVE 2.0 item 5). Names and whether imaging was blind come from the `blinding` section
  of `config.json` (`coded_by`, `scored_by`, `imager_knew_genotype`); anything not set is left in
  [brackets] to fill in. Check every sentence before use.

## Limits to state

- Brightness depends on detector gain, laser power and the microscope. Numbers are comparable only
  within a settings group (same microscope and settings). HyD and PMT gain are not simple multipliers,
  so no correction between settings is attempted.
- Calls are human judgements made on fixed display ranges; the software records but does not make them.
- Filter size and background ring are common choices, not values from a published protocol; they are
  settings and are logged.
- One version per project: `log.md` records the wormscore version at every step. Updating between
  Build blind set and Unblind would put two versions in the Methods; the app warns before such an
  update and defaults to Not now.
- Identity channel: no correction is made for bleed-through between channels or for the small depth
  shift between colors (chromatic aberration). Acquire channels sequentially, image single-label
  controls, and remember that gut autofluorescence shows in red too, so a suggestion on the identity
  channel can still land on gut granules.
- XML metadata is parsed with Python's standard library, which does not fetch external entities; only
  open files you trust.
