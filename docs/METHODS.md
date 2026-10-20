# What wormscore does, step by step

Written so it can be adapted into a Methods section. Every number named here is a setting in the
project's `config.json`, and the values actually used are copied into `log.md` on every run.

## 1. Inventory

Every file in every source folder is listed in `outputs/manifest.csv` with its SHA-256 checksum and a
role: raw image; duplicate (byte-identical to another file; the copy kept is set by
`duplicate_preference`, otherwise the shortest path); derived (projections, channel splits and exports
recognized by Fiji-style names such as `MAX_`, `Projections of`, `C1-`); metadata sidecar; other image
without microscope metadata; reference document; or hidden system file (for example macOS `._` files).

Each raw image (each series of a `.lif`, each scene of a `.czi`, each series of an OME-TIFF) is listed
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
  (or named BF, Brightfield, DIC, Trans, T-PMT).

If not exactly one channel matches, the marker is UNRESOLVED and the image is excluded.

## 3. Inclusion rules

Decided before scoring and applied without regard to genotype. An image is used if its marker channel
was identified, its objective is at least `min_objective_magnification` (when the metadata names one),
it has no more than `max_time_points`, and its z-stack covers at least `min_z_extent_um`
((planes - 1) x step). An image is never excluded for having no fluorescent signal: a missing cell is
a result. Images taken at the same stage position in the same file are treated as the same animal
imaged again; only one is counted (by default the one with more planes; the scorer can change this
while still blind).

## 4. Blinding

Included images are shuffled with a random seed and numbered W0001, W0002, ... The seed and the
code-to-file key are written to `key/`, which the scoring app never reads. The scoring page receives
images, plane counts, pixel size and quality questions, but no genotype, file name, path or brightness
number. Re-running `prepare` keeps the same codes; new codes are refused once any score is saved.

## 5. Images shown

For each image the marker channel is saved plane by plane and as a maximum-intensity projection
(each pixel takes its brightest value across planes), with exact recorded values. Display is linear
with two fixed ranges per microscope, the same for every image from that microscope:

- **Standard:** 0 to the detector maximum (for example 255 for 8-bit, 65535 for 16-bit).
- **Enhanced:** from the median, over images, of each image's median projection value (typical
  background) to the median, over images, of each image's 99.9th percentile (typical bright spot).
  Computed once over all included images of both genotypes.

Images are drawn at a common physical scale (`display_um_per_px`) so microscope zoom does not change
apparent cell size. Brightfield (middle plane, contrast-stretched per image) can be overlaid; it is
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
| saturated_px_mip | pixels at the detector maximum (peak is then an underestimate) |
| best_z_at_stack_edge | sharpest plane is the first or last (cell may be cut off) |

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

## Limits to state

- Brightness depends on detector gain, laser power and the microscope. Numbers are comparable only
  within a settings group (same microscope and settings). HyD and PMT gain are not simple multipliers,
  so no correction between settings is attempted.
- Calls are human judgements made on fixed display ranges; the software records but does not make them.
- Filter size and background ring are common choices, not values from a published protocol; they are
  settings and are logged.
- XML metadata is parsed with Python's standard library, which does not fetch external entities; only
  open files you trust.
