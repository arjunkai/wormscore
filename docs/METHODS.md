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

## 3. Inclusion rules

Decided before scoring and applied without regard to genotype. An image is used if its marker channel
was identified, its objective is at least `min_objective_magnification` (when the metadata names one),
it has no more than `max_time_points`, and its z-stack covers at least `min_z_extent_um`
((planes - 1) x step). Images can also be left out by file name with a per-source `exclude` rule
(`{"match": pattern, "reason": text}`), for example a plate found to carry a different reporter; the
reason is written to `excluded.csv`. Such rules should be set before scoring. The same image saved inside two different files (identical pixels, found by a checksum of
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

## 5. Images shown

For each image the marker channel is saved plane by plane and as a maximum-intensity projection
(each pixel takes its brightest value across planes), with exact recorded values. Display is linear
with two fixed ranges per microscope, the same for every image from that microscope:

- **Standard:** 0 to the detector maximum (for example 255 for 8-bit, 65535 for 16-bit).
- **Enhanced:** from the median, over images, of each image's median projection value (typical
  background) to the median, over images, of each image's 99.9th percentile (typical bright spot).
  Computed once over all included images of both genotypes.

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
found automatically: the filtered projection is smoothed with a Gaussian (1.5 um), local maxima at
least 6 um apart are taken brightest first, and each is grown to every connected pixel at or above half
of its height above the local background (median of a ring 6 to 10 um away), within 8 um, keeping
regions of at least 4 um^2 (full width at half maximum). Suggestions are measured too
(`outputs/auto_measurements.csv`); they only point at places to look.

## 7. Questions asked of the scorer

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
  gets one column per outcome with each neuron's state.
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
  mutant groups are small. `outcome` chooses the calls (default) or a `question_outcomes` name.
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
- **Scorer consistency** (when the project has hidden repeats): `reliability.csv` gives, over the pairs
  where both the repeat and its original were scored, percent agreement and Cohen's kappa for: usable or
  not; animal affected (primary, and secondary if set); each neuron's call (linear-weighted kappa over the
  ordered calls); cells seen (linear-weighted); and each `question_outcomes` measure. Neurons are matched
  by the recorded side when both images name each neuron's side, otherwise by number. Kappa is undefined
  when every answer is the same. With few pairs, kappa is imprecise; kappa below 0.60 is usually
  considered inadequate ([McHugh 2012](https://doi.org/10.11613/BM.2012.031)). The same scorer seeing an
  image twice measures consistency, not agreement between people.
- **Analysis plan lock.** When any of the settings above, `question_outcomes` or `interaction` is set,
  `prepare` writes the whole analysis plan (both definitions of affected, comparisons, direction,
  cross-microscope measures, interaction model, dim cutoff) to `log.md`. Unblinding compares the current settings with
  the last plan recorded before the first score was saved. If they differ, no test is run and every test
  file says why; descriptive tables are still written. If no plan was recorded before scoring, tests run
  but are marked `set_before_scoring = NO`.
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
- XML metadata is parsed with Python's standard library, which does not fetch external entities; only
  open files you trust.
