# What wormscore does, step by step

Written so it can be adapted into a Methods section. Every number named here is a setting in the
project's `config.json`, and the values actually used are copied into `log.md` on every run.

1. [Inventory](#1-inventory): which files and images are found, and their acquisition settings
2. [Which channel is the marker](#2-which-channel-is-the-marker)
3. [Inclusion rules](#3-inclusion-rules)
4. [Blinding](#4-blinding): codes, the key, added images, Browse, hidden repeats, a second scorer
5. [Images shown](#5-images-shown): display ranges, 3D, and the optional corrections and checks
6. [Measurements](#6-measurements)
7. [Questions asked of the scorer](#7-questions-asked-of-the-scorer)
8. [Unblinding](#8-unblinding): the results tables, the tests and the locks
9. [Limits to state](#9-limits-to-state)
10. [Projects made with earlier versions](#projects-made-with-earlier-versions)

## 1. Inventory

### Files

Every file in every source folder is listed in `outputs/manifest.csv` with its SHA-256 checksum and a
role:

- raw image;
- duplicate: byte-identical to another file. The copy kept is set by `duplicate_preference`,
  otherwise the shortest path;
- derived: projections, channel splits and exports recognized by Fiji-style names such as `MAX_`,
  `Projections of`, `C1-`;
- metadata sidecar;
- other image without microscope metadata;
- reference document;
- hidden system file, for example macOS `._` files.

### Images

Each raw image is listed in `outputs/inventory.csv`. One image is each series of a `.lif`, each scene
of a `.czi`, each series of an OME-TIFF, each stage position of a Nikon `.nd2`, each Olympus FluoView
`.oib` or `.oif`, each Olympus `.oir`, and each ImageJ hyperstack TIFF in a source that turns ImageJ
reading on.

The row gives the objective, planes, pixel size, z-step, bit depth, channels and the acquisition
settings that affect brightness (detector gain or voltage, laser power, averaging, exposure). Images
whose settings differ from the most common value on the same microscope are flagged. Images from one
microscope with the same settings form a **settings group** (A, B, ...); brightness is compared only
within a settings group.

### Acquisition settings

The settings are those of the reporter channel, read from that channel's own part of the metadata.

| Format | Settings read |
|---|---|
| Leica | Detector gain, each laser line in use with its intensity, and line averaging. The detector is named as the file names it: "HyD gain" for hybrid detectors, "PMT gain" for a PMT. |
| Zeiss | Detector gain (or voltage), each laser line with the share of its power let through (1 - attenuation), lamp intensity and exposure for cameras, and averaging. |
| Nikon | Camera exposure, gain and multiplier, each laser line that is on with its power, and the fluorescence lamp. For the A1 and AX point-scanning confocals: the laser power and detector voltage (HV) of the detector channel with the reporter's excitation wavelength, and line averaging. |
| OME-TIFF | Detector gain and voltage, each light source's wavelength and attenuation as the file states them, and exposure. A value the channel does not give itself is taken from the instrument's detector or light source that the channel names, as Bio-Formats writes converted files. |
| Olympus FluoView | Detector voltage, each laser with its transmissivity, and integration. |
| Olympus `.oir` (FV3000, FV4000, FVMPE-RS) | The reporter channel's detector voltage (HV), its gain and offset when they are not 1 and 0, its own laser line with the share let through, a neutral density filter in the laser path, and line or frame averaging. The FV4000's detectors have no voltage, so its settings are the laser and the averaging. |

With brightness correction along z in an `.oir` file, voltage and laser power change with depth: the
settings give the range over the planes and the image is flagged.

### Bit depth

Bit depth is the number of bits the detector used, which sets the saturation level (a 12-bit camera
saved in a 16-bit file saturates at 4095). A 16-bit Zeiss or OME-TIFF file that does not record it is
flagged "bit depth not recorded" and its saturation level is taken as 65535; state the true level per
microscope under `saturation_value` in `config.json`.

## 2. Which channel is the marker

The marker is the reporter channel that is scored and measured (`marker` in `config.json`). It is found
from metadata only. If not exactly one channel matches, the marker is UNRESOLVED and the image is
excluded.

### Leica

The active detectors of each sequential-scan step, in order, are the image channels. The marker is the
channel whose detector had:

- a configured excitation laser on (default 488 nm);
- an emission band starting in the configured range (default 490 to 530 nm);
- if set, the configured lookup-table color.

Brightfield is the transmitted-light detector. Current LAS X (SP8 3.5, Stellaris 4.4) writes for each
step only what differs from the image's master setting, so a step's detection band, averaging and
(where the step gives none) gain are taken from the master.

### Zeiss and OME-TIFF

The marker is the channel whose fluorophore, channel name or excitation wavelength matches the
configuration.

Brightfield is the channel recorded with transmitted light, or named BF, Brightfield, DIC, Trans,
T-PMT, T PMT, TL, or a transmitted detector named TD or TPMT, alone or with a number or track. "BF" and
"TL" must be the whole word: "BFP" and "TLR-4" are not brightfield, and neither is "td-Tomato".

If several channels match, for example a second fluorophore excited by the same 488 nm laser, and
exactly one of them names a configured dye (EGFP, GFP), that one is the marker.

If the file gives no objective name, the objective is written from its magnification, NA and
immersion.

### Nikon and Olympus FluoView

The same rules, using the channel name, dye name and excitation wavelength the file records.
Brightfield is the channel recorded as brightfield or transmitted light (Olympus detectors named TD).

- These files also record an emission wavelength, which counts only if `marker.emission_nm` gives a
  range (for example `[500, 550]` for GFP).
- Olympus numbers written with a decimal comma (`0,207`) are read as decimals.
- Colour (RGB) camera pictures are not used; `manifest.csv` says so in the file's note.

### Olympus `.oir`

Files of the FV3000, FV4000 and FVMPE-RS, read with [oirfile](https://github.com/cgohlke/oirfile). One
image per file. Each channel that was scanned has its dye name, its laser line and its detector in the
file.

- The marker is found by the same rules from the dye name (or the channel name if no dye was set), the
  wavelength of the channel's one laser line, and the dye's emission peak if `marker.emission_nm`
  gives a range.
- Brightfield is the channel whose detector is the transmitted-light detector (device `TD`), whatever
  its position.
- A multiphoton line (for example 940 nm) matches only if `marker.excitation_nm` lists it.
- The bit depth is the one the file states (10 or 12 in the FV3000 and FVMPE-RS files checked, 16 in
  the FV4000's).
- The date is the file's local time. The file does not record the stage position.

### ImageJ hyperstack TIFF

These files record pixel size and z-step but not which channel is which. They are used only if their
source in `config.json` has `imagej_tiff` with the channel order, for example
`{"channels": ["marker", "brightfield"]}`, and optionally the objective. That setting is copied into
`log.md` when the blind set is built. If the file has a different number of channels the image is
UNRESOLVED. Projections made later (names starting `MAX_`, `SUM_` and so on) are never used.

### What is flagged instead of guessed

Most of these are a flag in `outputs/inventory.csv`; files that cannot be read are listed in
`outputs/manifest.csv` with the reason.

**Any format**

- A dimension wormscore does not use (the tiles of a tile scan, the wavelengths of a lambda scan,
  Airyscan phases): only its first entry is read, and Build warns how many such images are in the
  blind set.
- A Micro-Manager style OME-TIFF keeps its metadata in a `.companion.ome` file. Its pages are read as
  the planes, channels or time points that file gives them. Pages it does not place are flagged, and
  only the first is read.

**Nikon**

- A file with no pixel calibration reads as 1 um per pixel, which sets the display scale and the
  search for suggested cells. Build warns.
- `.nd2` files from before 2010 are listed as unreadable, with the way to convert them.

**OME-TIFF**

- A pixel size or z-step in another unit (nm, mm) is converted to micrometres. One in a unit that is
  not a length is not used, and the image is left out for having no pixel size.
- One file of a set that keeps each plane in its own file is flagged ("the metadata lists 5 planes,
  this file holds 1").
- Files written to the 2010 to 2015 schemas, and to `2016-DEV0` (a development build of Bio-Formats,
  whose names match 2016-06 for everything read here), are read like current ones.
- Files written to the 2008 schema describe their channels apart from the pixels. The channels are
  read where the file says which pixel channel each description belongs to. Where it does not, they
  are UNRESOLVED and flagged "the file does not say which channel is which"; the order of the
  descriptions is not taken as the order of the channels.

**Olympus `.oir`**

- A time series and a lambda scan are read at the first time point and the first wavelength. The
  lambda scan is flagged.
- A file that the list `matl.omp2info` next to it names as one area of several is flagged "one tile
  of a tile scan"; tiles are not joined.
- A scan stopped by hand ends inside a plane: that plane is left out and flagged, and so is a z-stack
  with fewer planes than were set.
- An image the Olympus software made from another (system name ending in "(Modified)", for example a
  projection) is flagged, and so is one whose size is not the scanned size, because its pixel size may
  not apply.
- A file with planes missing elsewhere is flagged incomplete and left out at Build.
- A file cut short, a line scan and an overview map without pixels are listed as unreadable with the
  reason. `.poir` archives are not read.

**Olympus `.oir`: a laser share recorded twice.** Some files record two values for a laser's share,
one in the image's settings and another in the first plane's record. The file records the share a
second time in both places (the value set in the software, before a neutral density filter).

- Where that second record agrees between the image and the plane, the image's share is used alone. It
  is then backed by three of the four records, it is the one Bio-Formats reports, and in series of
  one session the pixels follow it. This held in all 23 files checked.
- Where a file has no second record or it does not agree, both shares are written in the settings and
  the image is flagged so it can be checked in the Olympus software.

### Other fluorescent proteins

`"marker": {"preset": "mCherry"}` replaces the GFP settings with those of another protein: GFP,
mNeonGreen, YFP, mCherry, TagRFP, mScarlet, mKate2 or BFP.

Each preset gives the laser lines that excite it, where a Leica detection band starts, an emission
range for files that record one (Nikon, Olympus), the dye and channel names microscopes write for it,
and its display color, from FPbase peaks. For example mCherry 587/610 nm: lasers 561, 587 or 594 nm,
band starting 575 to 625 nm, emission 595 to 645 nm. Any field given next to the preset overrides it.
Without a preset the GFP settings above are used, as before presets existed, so GFP projects build the
same blind set.

The reporter and identity channel can be chosen from these presets in Project settings, which writes
the same `preset` entries; a channel with its own settings is kept as it is. A change is written to
`log.md`. Once scoring has started, a change needs a reason, which is written with it. While a
second-scorer package exists the markers cannot change, since its images would no longer match the
project's.

### Identity channel

Optional. `"identity": {"preset": "mCherry"}` names a second marker that labels the cells
independently of the reporter being scored, such as a pan-neuronal or cell-identity marker. It is
found by the same rules, among the channels that are not the marker (the reporter is tested first, so
a channel is never both). If two channels match, the one naming a configured dye is used, otherwise it
is UNRESOLVED.

### Several identity channels

For multi-colour systems such as NeuroPAL (Yemini et al. 2021, Cell 184:272). `"identity"` may list up
to four channels. The first is the identity channel above (it may find the suggested cells); the
others are shown with it only.

`{"preset": "NeuroPAL"}` stands for TagRFP-T (pan-neuronal, shown white, the first), mNeptune2.5
(red), CyOFP1 (green) and mTagBFP2 (blue).

The other channels are found by the same rules among channels still unassigned after the reporter and
the identity channel, in the order listed. So CyOFP1, excited at 488 nm like GFP, takes the 488 nm
channel that does not name the reporter's dye. Each is saved for display only (`identity2/` to
`identity4/`), scaled per image like the identity channel, and is never measured. Projects with one
identity channel are built and shown as they were before several were possible.

## 3. Inclusion rules

Decided before scoring and applied without regard to genotype.

**An image is used if all of these hold:**

- its marker channel was identified;
- its objective is at least `min_objective_magnification` (when the metadata names one; a
  magnification written with a decimal, `63.0x`, is read whole);
- it has no more than `max_time_points`;
- its z-stack covers at least `min_z_extent_um` ((planes - 1) x step);
- its pixels could be read at Scan data;
- its file is complete. An OME-TIFF whose planes lie past the end of the file, or a set with a file
  missing, is incomplete: such planes would otherwise read as black;
- the file records a pixel size.

Each image left out is listed in `excluded.csv` with the reason. A file that cannot be opened at Scan
data (in use by another program) is listed as unreadable and the scan goes on.

**Other rules**

- **By name.** Images can be left out by file name with a per-source `exclude` rule
  (`{"match": pattern, "reason": text}`), for example a plate found to carry a different reporter. The
  reason is written to `excluded.csv`. Such rules should be set before scoring.
- **Identity channel.** In a project with an identity channel, an image without one is excluded too
  (`identity.required`, default true), so every scored image had its cells found the same way. With
  `required: false` it is kept, scored on the reporter alone, and marked in the key
  (`identity_channel_used`).
- **The same image in two files.** The same image saved inside two different files (identical pixels,
  found by a checksum of every pixel recorded at inventory) is coded only once; the other copy is
  listed in `excluded.csv`.
- **The same animal imaged again.** Images taken at the same stage position in the same file are
  treated as the same animal imaged again. Only one is counted: by default the one with more planes;
  the scorer can change this while still blind.
- **No signal is not a reason.** An image is never excluded for having no fluorescent signal: a
  missing cell is a result.

## 4. Blinding

### Codes and the key

Included images are shuffled with a random seed and numbered W0001, W0002, ... The seed and the
code-to-file key are written to `key/`, which the scoring app never reads. The scoring page receives
images, plane counts, pixel size, z step and quality questions, but no genotype, file name, path or
brightness number.

Re-running `prepare` keeps the same codes; new codes are refused once any score is saved.

A build also stops, before anything is changed, when:

- scores or a second-scorer package exist but `key.csv` is not in the key folder. Without the key it
  would assign new codes, and the saved scores would belong to other images;
- an image file it has to read cannot be found.

### A build that fails

A build makes its coded images in `blind_building/` and puts them in the place of `blind/` in one step
when all are made; the key and the tables are written after that.

- **It fails midway** (an image that cannot be read, a full disk): the blind set, the key, the scores
  and the drafts are left as they were, the Build log says so, and the build can be run again. The
  same holds for **Add new images**, which then adds nothing.
- **It is cut off by the computer shutting down:** it leaves `blind_building/`, which the next build
  removes.
- **It is cut off in the moment of the exchange:** it can leave the blind set as `blind_previous/`
  (the next build or the scoring page puts it back), or, for Add new images, codes in the key whose
  images are not yet in `blind/` (Add new images then says so; "Rebuild, keeping the current codes"
  makes them).

A new image never gets a code that has a saved score or outlines. There is no resume: a build that
failed is run again from the start. A rebuild therefore needs free space for a second copy of the
blind set while it runs.

### Drafts, cached pictures and other windows

- Unsaved answers are kept per code in `scoring_drafts/` (the same fields as a score, no genotype or
  file name) and deleted when the score is saved. Drafts are never read as scores and do not lock the
  codes.
- New codes set any drafts aside (`scoring_drafts/superseded_<time>.json`), so answers typed for an
  old code never open on the image that now has that code.
- Images are fetched at addresses that name the project and the build, so a picture cached from
  another project or an earlier build is never shown.
- Each scoring window sends its project's ID with every request, so a window left open from another
  project cannot save into the one being scored.

### Images added later

Images imaged later can be added with `prepare --add-new-images` (or **Add new images** in the app's
build step once scoring has started).

- Only images not yet in the key are coded. Their codes continue after the highest existing code,
  shuffled among themselves with a new seed (kept in `key/`).
- Existing codes, blind images, settings-group letters and display ranges are not changed, so images
  already scored look exactly the same. A new microscope gets its own ranges. A later rebuild that
  keeps the codes keeps those ranges, the settings-group letters and `added_on` too.
- Adding refuses to run if any coded image would be dropped.
- Which codes were added, and when, is recorded only in the key (`added_on`), and the draft Methods
  text reports each added batch.

Limit: the scorer can tell which images arrived later. If a later batch is all one genotype, blinding
is weaker for it, so mix genotypes in each batch where possible, or add all images before scoring
starts.

### Browse images

Optional, from the home screen or `wormscore browse`. A separate viewer, served by its own local
server, shows images with their file name, genotype and acquisition settings, and measures regions on
screen without saving them.

**Before the blind set is built** it reads each scanned image when it is opened, with the same code
that builds the blind set, and keeps the last three in memory only (nothing is written to disk). The
Standard range is the same as in scoring. Enhanced uses the build's formula (median to 99.9th
percentile of the projection) on that image alone, because the per-microscope range does not exist
yet.

Each image opened before the build is recorded once per session in `outputs/browsed_images.csv` (time,
session, genotype, file, location), with one `log.md` entry per session. The next build's log entry
and the Methods draft (a sentence and a "Before coding" row of the ARRIVE table) give the number of
images viewed.

**From Build blind set until Unblind** it refuses to start, and refuses every request: that is, while
the project has a key that is newer than its last unblinding (also again after a rebuild or added
images), and in a second scorer's package.

**After unblinding** it lists the coded images from the key with their saved scores, and the images
that were left out.

The scoring server is unchanged and still never receives genotypes or file names. Projects that are
never browsed produce the same files as before.

### Hidden repeats

Optional (`reliability.repeat_fraction`, off by default). When coding, this share of each genotype's
images (at least one per genotype) is chosen at random and coded a second time, mixed in with the
rest.

- Each repeat is placed at least 15% of the set away from its original in code order (the order of
  the scoring list). If a small set cannot fit them that far apart, Build places them as far apart as
  they fit and warns with the distance reached, which `log.md` records.
- Only images that are counted by default and have no other image of the same field are chosen, so a
  repeat shows no "same stage position" note or different default. Its blind files are byte-identical
  copies of the original's apart from the code in `meta.json`.
- Which codes are repeats is recorded only in the key (`repeat_of`); the log gives only their number.
- Display ranges are computed from originals only.
- Re-running `prepare` keeps the same repeats; adding images adds none.
- Repeats are never counted in any result. At unblinding, `reliability.csv` compares each scored
  repeat with its original (see section 8).

Limit: someone who opens `blind/` and compares files can find the repeats, as they can find any other
information there; score only through the app.

### Second scorer

Optional, made from the app or with `export-scorer`. The project owner makes a package for another
person after the blind set is built.

**What is picked.** A share of each genotype's included images (chosen by the owner from 1 to 100%, at
least one per genotype) is picked at random with a seed that is not stored, together with the hidden
repeats of the picked images. They are put in a new random order, with each repeat kept apart from its
original (the same 15% rule).

**What the package holds.** It is an ordinary project folder with only those `blind/` folders and a
`config.json` holding the scoring criteria, the marker and display settings and the region measurement
settings. It has no `key/`, `outputs/`, `log.md`, `scoring/`, image sources, analysis plan, file
names or checksums (repeats are byte copies of their originals, so a checksum list would show which
codes are repeats). Its scoring list follows the package's own order. Scanning, building and
unblinding are refused inside a package.

**What the owner keeps.** The owner's project keeps the selection, the order and a SHA-256 checksum of
every file of every packaged code in `scorers/<id>/package.json`, and `log.md` records the scorer's
name, the share and the number of images.

**Import.** When the package comes back, import refuses it unless:

- the package ID was made in this project;
- the scoring criteria (their answer fields and allowed answers) are unchanged on both sides;
- the packaged images and the project's own images match the checksums;
- no codes were added or removed;
- every column and answer of `scores.csv` comes from the form;
- nothing in the package is a link.

Only the scoring files are copied, to `scorers/<id>/scoring/`; the owner's `scoring/` is never
written.

Limits: the owner knows the results if the package is made after unblinding (the images are still
picked at random, and the Methods text says when the second scorer scored); and the second scorer is
trusted to score alone and not to compare raw files.

## 5. Images shown

### Display ranges

For each image the marker channel is saved plane by plane and as a maximum-intensity projection (each
pixel takes its brightest value across planes), with exact recorded values. Display is linear with two
fixed ranges per microscope, the same for every image from that microscope:

- **Standard:** 0 to the detector maximum (for example 255 for 8-bit, 65535 for 16-bit).
- **Enhanced:** from the median, over images, of each image's median projection value (typical
  background) to the median, over images, of each image's 99.9th percentile (typical bright spot).
  Computed once over all included images of both genotypes from that microscope, so it depends on
  which images are in the set; images added later to an existing key keep their microscope's range.

Images are drawn at a common physical scale (`display_um_per_px`) so microscope zoom does not change
apparent cell size.

Brightfield (middle plane, contrast-stretched per image) can be overlaid; it is never measured.

### 3D view

The 3D view turns the stack about the vertical axis and tilts it about the horizontal axis (each -60
to +60 degrees in 5-degree steps; 0 and 0 is the maximum projection).

- Each screen pixel is the brightest value along the turned line of sight, with z stretched by z step
  / pixel size and up to 6 planes blended in between neighbouring planes (the same as Fiji's 3D
  Project, brightest point, with interpolation).
- It uses the same fixed display ranges and has no depth cueing, which would change apparent
  brightness.
- It is drawn in the browser from the planes already shown, and is for viewing only: nothing is
  measured or saved from it, and regions are drawn on the projection or a plane.
- Images over 1.5 million pixels are turned at half size (each pixel the brightest of 2 x 2) to stay
  quick.

In the projection and in 3D, the keys line under the image shows the plane that holds the brightest
point under the cursor (only where the image is lit), and a double-click there opens that plane. It
shows a plane number, never a brightness value.

### Identity channel

Saved plane by plane and as a projection for display only, as 8-bit images, each image scaled on its
own from the median of its projection (background) to its 99.9th percentile
(`display.identity_contrast_percentiles`).

It is scaled per image on purpose. It is only for finding the cell, and if its brightness differed
between genotypes a fixed range could hint at the genotype. The scoring page receives no identity
brightness numbers.

The scorer can show the reporter, the identity channel or both (keys 1, 2 and 3), each in its own
color, added together: the reporter in its preset's color (green for GFP) and the identity channel in
magenta (or green when the reporter is magenta), so a cell with both looks white. The 3D view turns
and tilts the identity channel with the reporter, each channel keeping its own brightest point along
the line of sight.

### Bleed-through check

Optional, for projects with an identity channel. Light from one marker recorded in the other channel
cannot be told apart from real signal in two-marker images, so it is measured on single-label controls
imaged at the same settings (Bolte and Cordelieres 2006, J Microsc 224:213).

For each control image, over every plane:

1. The background of each channel is its median.
2. Pixels with signal are those in the labelled channel above background plus half the range to its
   99.9th percentile. Pixels saturated in either channel are left out.
3. The bleed-through is the least-squares slope, through the origin, of the other channel above its
   background against the labelled channel above its background, as a percentage.

The project's value is the median over control images (`outputs/bleed_through_identity-only.csv`,
`..._reporter-only.csv`). Identity-only controls give the share of identity light added to the
measured reporter; reporter-only controls give the share of reporter light in the identity channel.
Values are reported, never subtracted.

**With several identity channels** (NeuroPAL), identity-only controls are also fitted jointly. All the
identity channels label the same cells, so a slope against one alone also picks up the others' light.

- Over pixels with signal in any identity channel (saturated pixels in any channel left out), the
  reporter channel above its background is fitted to every identity channel above its background
  together, by multiple least squares through the origin. Each coefficient is that channel's
  bleed-through.
- The channels can only be told apart where their patterns differ (NeuroPAL's color code differs
  between neurons). An image whose largest uncentred variance inflation factor is above 10 is left out
  of the joint fit and counted. The factor is computed from X'X scaled to a unit diagonal, as suited
  to a fit through the origin (Belsley 1991, Conditioning Diagnostics); 10 is the usual limit for
  collinear predictors (Kutner et al. 2005, Applied Linear Statistical Models).
- The project's values are the medians over the remaining images (columns `joint_<channel>_pct` and
  `joint_max_vif` in the same file). The single slope above is still reported.

### Gut autofluorescence check

Optional, after unblinding. C. elegans gut granules fluoresce across the spectrum, brightest with a
DAPI-type filter set and also in green, where they overlap GFP (Pincus et al. 2016, Aging 8:889;
Coburn et al. 2013, PLoS Biol 11:e1001613).

It applies to images that recorded a channel with no label (a channel named DAPI, Hoechst, blue, BFP,
CFP, 405 or UV is preferred). Each counted image's saved outlines (or, without outlines, its first
suggested cell) are measured in that channel:

- without filtering, so the noise is not shrunk;
- in the reporter's sharpest plane for the region and the planes either side (a small depth shift
  between colours);
- as signal to noise = (mean in the region - median of the background ring) / robust SD of the ring
  (1.4826 x median absolute deviation), the ring as in section 6;
- keeping the highest of the three planes.

A region at 3 or more is flagged "possibly gut autofluorescence". Flags are reported
(`outputs/unblinded/autofluorescence.csv`, `log.md`, the Methods draft, by genotype; and in Browse
images, per code), never used to change a call or leave an image out.

The check runs only after unblinding, because autofluorescence differs between strains and ages, so a
flag per image could hint at a genotype while scoring.

### Flat-field correction

Optional, set before scoring. Most microscopes light the middle of the field more than the edges, and
lamp or laser power changes from day to day. With the correction on, Build blind set corrects the
reporter channel of every image before anything is shown, suggested or measured, using images of a
uniform fluorescent reference slide taken at the project's settings in each imaging session.

An imaging session is one microscope, settings, objective and zoom, and one acquisition day from the
metadata. Nikon and Olympus dates, written in the computer's locale, are read in the common forms, US
month/day first; a date that can only be day first (a day above 12) and is written with AM or PM, or
with no time, is read day first when Scan data was run with 1.26.18 or later (the day is then in `inventory.csv`, column
`acquired_day`). Slide images are read by the same rule as the project's images.

    corrected = (raw - dark) / flat x factor + mean(dark)

- **flat:** the session's slide images, each averaged over its planes, then averaged together, less
  the dark image, smoothed with a Gaussian (sigma 2% of the shorter side) and divided by its mean.
- **factor** (only with "between sessions"): the median slide brightness over that microscope's
  sessions with the same settings, divided by this session's.
- **dark** (optional): images taken with the light off at the same settings, median over images.
  Without them the camera offset is taken as 0, and the Methods draft says so.

**Slide images that cannot be used** are left out with the reason: more than 0.1% of pixels at the
detector maximum, another size than the session's other slide images, or a flat below 20% of its mean
anywhere (the slide did not fill the field).

**Nothing that can be worked around stops the build.** Each fallback is a warning in the Build log and
`log.md`, the `flat_field` column of `key.csv` and the Methods draft. The pattern of light across the
field changes little unless the microscope is realigned (UCSF Center for Advanced Light Microscopy,
flat-field notes), so an image without slide images from its own session uses slide images of the same
size from the same microscope:

- from sessions at the same settings if there are any; otherwise from sessions with the image's
  objective and zoom; otherwise from any session of that microscope;
- among those, the nearest day's if the image has a date; for an image without a date, the average of
  those sessions (or the one session, if there is only one).

Its light across the field is then corrected, its brightness between sessions is not. An image with
no usable slide images at all is used as recorded. Formats with no acquisition date (Leica LIF, ImageJ
TIFF) use the microscope's only session directly.

**What is recorded.** Pixels at the detector maximum are found on the raw stack and stored per image
(`saturated.png`), so `saturated_px_mip` still counts them. Raw files are never changed. Each
session's light range and factor are in `outputs/flat_field/sessions.csv`, `log.md` (at each setting
and each Build, with checksums of the slide images and the measured correction) and the Methods draft.
`key.csv` gets `imaging_session`, `flat_field` and `session_factor`.

**Slide images added later.** Slide images for later sessions can be added at any time, also after
scoring began. Sessions in use are not changed, and a new session's factor brings it to the level its
microscope's sessions were brought to, so every coded image keeps the planes it was scored on.
Starting again or turning the correction off would change images already scored, so those wait for
nothing to be scored (and no second-scorer package to exist). Projects without the correction are built
as they were before it existed.

**A rebuild after scoring began** (or once a second-scorer package exists) that keeps the codes
corrects each coded image as it was corrected when it got its code: with the slide images the
project had then. Each build records the session of each code in `key/flat_field_sessions.json`, and
`key.csv` records the correction in words (`imaging_session`, `flat_field`, `session_factor`). Slide images added
since are used only for images added after them (**Add new images**), so an image scored with the
nearest day's slide images stays so, and `log.md` says how many images were kept this way. Before
scoring, the same rebuild uses every session.

If a coded image cannot be corrected as recorded (the setting was removed from `config.json` by hand,
`key.csv` has no record, or a new scan reads the image's settings or day differently), the build uses
the setting as it now is and writes a warning with the number of images to the Build log and
`log.md`; it is not stopped.

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

### Regions and suggestions

Regions come from the scorer (drawn circle or outline, or an accepted suggestion). Suggestions are
found automatically:

1. The filtered projection is smoothed with a Gaussian (1.5 um).
2. Local maxima are taken brightest first. Each is the brightest point of the 6 um square around it,
   so maxima about 3 um or more apart are kept.
3. Each is grown to every connected pixel at or above half of its height above the local background
   (median of a ring 6 to 10 um away), within 8 um (full width at half maximum).
4. Regions of at least 4 um^2 are kept.

Suggestions are measured too (`outputs/auto_measurements.csv`); they only point at places to look.

With flat-field correction on (section 5), every measurement is made on the corrected reporter.

### Saving outlines

Saving outlines replaces the image's saved outlines and their measurements. If two scoring windows of
one project save outlines for the same image at nearly the same time, the click that reached the app
later is the one kept, whichever is measured first; the other window says its outlines were not saved.
If they save one after the other, the later save is kept too, and both windows say so. The same holds
for scores.

### With an identity channel

With an identity channel (`identity.spots_from`, default `identity`), suggestions are found the same
way on the identity channel's median-filtered projection, and the reporter is measured in those
regions (column `spots_found_on`). Cells are then located without using the reporter being scored, so
a dim or missing reporter cell is still found and measured, as recommended by Jost & Waters (2019):
select regions on "a fluorescence channel or transmitted light image from which content of the
experimental image cannot be deduced". The sharpest plane is still the plane where the reporter is
brightest in the region. `spots_from: "reporter"` finds them on the reporter, as without an identity
channel.

## 7. Questions asked of the scorer

### What is asked

The scoring criteria (the score form; set on the Project settings page or in `config.json`) ask, for
each image:

- whether it is usable, and if not, why;
- the count question;
- for each neuron its call, optionally its side, and any extra questions;
- any questions asked once per animal (`scoring.animal_questions`, saved as `a_<id>`, shown under
  "This animal");
- any questions asked for each side (`scoring.side_questions`, saved as `left_<id>` and
  `right_<id>`). In `question_outcomes` each side is assessed like a neuron (not mixed with other
  questions); in `rank_outcomes` the two sides are rolled up into one value per animal.

Extra questions are of four types:

- yes/no;
- a choice;
- an ordered scale: levels listed best first, worst last (`"type": "scale"`);
- a count: whole numbers going up by one, the last optionally "N+" for N or more (`"type": "count"`).

A scale or count may have a "can't tell" answer (`cant_tell`), which is never a level. A required
question must be answered before the score can be saved. A question for each neuron can be skipped
(hidden, saved empty) when the neuron's call is one the question's outcome leaves out, such as "not
seen".

Answers are stored as their text. In `scores.csv`, text that starts with `=`, `+`, `-` or `@` is
written with a leading `'` so a spreadsheet does not run it as a formula; unblinding reads such
answers back without it.

### When the criteria are recorded

The criteria are recorded in `log.md` ("Score form set before scoring"):

- when they are saved in the app, or checked and kept as they are from the home screen's Set scoring
  criteria step;
- when the blind set is built;
- when the first score is saved.

From the first score, or once a second-scorer package exists, the app does not let them change.

The record covers what decides the stored answers and their analysis: neurons, calls, defect calls,
sides, count answers, each question's id, answers, whether it is required and when it is skipped, the
question outcomes and the primary outcome; and each question's type and "can't tell" answer, the
questions asked once per animal and the rank outcomes. These last four were added in 1.4.0 and appear
only when used, so forms recorded by 1.3.0 still match.

Wording (question text, rules shown to the scorer, unusable reasons) is not part of the record and may
still be corrected.

### Confirmations

The app asks for confirmation when:

- an image shares a stage position with another (repeat);
- the brightest suggested spot is sharpest in the first or last plane;
- a suggested cell is saturated;
- a horizontal scan-line artifact is present.

Answers are saved with the score. Every save is also appended to `scoring/score_history.csv`.

## 8. Unblinding

### When it is allowed

Only after the scorer confirms scoring is finished and at least `min_scored_worms` images are scored.
An override is possible and is recorded in `log.md`; none is needed once every coded image is scored.

### Warnings

The summary also warns in these cases; tests still run:

- An image was scored or changed after the project was first unblinded. Those scores were not blind;
  `merged.csv` marks each one (`score_saved_after_unblinding`).
- Coded images have another genotype under the rules now in `config.json` than in the key (rules
  changed after Scan data).
- A genotype named in the analysis settings has no counted image.

### merged.csv and the summary

`outputs/unblinded/merged.csv` holds, for every image, the key (original path, local copy, location in
file, checksum, genotype, microscope, settings group), the score, and the measurements. The summary
counts calls per microscope and genotype and lists drawn-region brightness by call within each
settings group only.

### The results tables

Results tables use counted, usable images only, with the animal as the unit (one image per animal).

#### per_neuron.csv

One row per animal and neuron (genotype, microscope, settings group, imaging date, call, affected
yes/no, region measurements), ready for plots that show every animal, such as SuperPlots.

The imaging date is a label; nothing is grouped or tested by it. It is written as YYYY-MM-DD. Nikon
files give the date as the acquiring computer shows it: a date with only one reading (17/7/2009,
7/17/2009) is converted, and one with two (11/3/2009) is given as written and marked "day or month
first: not known", not guessed.

#### penetrance.csv

Per microscope and genotype: the number of animals with 0, 1, 2, ... affected neurons (a neuron is
affected when its call is one of `defect_calls`), the share with at least one (penetrance), and the
share affected for each neuron. Animals with a neuron left uncalled are counted separately and left
out of the percentages.

#### fisher_tests.csv

Two-sided Fisher's exact test on animals with versus without an affected neuron, comparing each
genotype with `reference_genotype` (or every pair of genotypes if none is set).

- **Within one microscope.** Tests are run only between genotypes imaged on the same microscope,
  because calls made on different microscopes are not interchangeable.
- **Correction.** p-values are Bonferroni-corrected for the number of tests made for one definition
  of a defect and one outcome (the `n_tests` column), not across every row of a file that holds
  several definitions or outcomes. Other tests (for example per neuron) can be run from
  `per_neuron.csv`; correct them too.
- **The reference genotype is part of the plan.** It is recorded in `log.md` with the analysis plan
  (at each save in Project settings, at Build and at the first score). If it differs at unblinding
  from the one recorded before scoring, no planned test runs. Projects that scored before 1.15.0
  have no such record.

#### question_outcomes.csv and question_outcome_tests.csv

Only if `unblinding.question_outcomes` is set. The same penetrance and Fisher's exact tables, built
from the per-neuron questions instead of the calls. For each outcome the config names the questions,
the answers that count as a defect (for example `no`) and the answers that count as unclear (for
example `can't tell`).

- A neuron is defective if any listed question has a defect answer.
- A neuron whose call is in `not_visible_calls` (default `missing`) is not assessed, because its
  processes cannot be judged.
- An unclear answer leaves out that neuron (`"unclear": "exclude_neuron"`, the default) or the whole
  animal (`"exclude_animal"`).
- An animal is defective if any assessed neuron is defective. Animals with no assessed neuron are
  left out and counted in their own columns, as are animals with a question left unanswered.
- `per_neuron.csv` gets one column per outcome with each neuron's state.
- An outcome may instead combine questions asked once per animal (not mixed with neuron questions):
  the animal is defective if any answer is a defect.

#### rank_outcomes.csv and rank_outcome_tests.csv

Only if `unblinding.rank_outcomes` is set. Ordered scale and count questions, each tested on its own.

**One value per animal.** Each entry names the outcome, the question, and for a question asked for
each neuron how the neurons give one value per animal (the animal is the unit; neurons are subsamples,
[Lazic et al. 2018](https://doi.org/10.1371/journal.pbio.2005282)):

- scale: `worst` (default; the highest level of any neuron) or `mean` (mean level of the assessed
  neurons, which treats levels as equally spaced);
- count: `sum` (default; the total over the neurons) or `mean_per_neuron`.

Levels are scored 0, 1, 2 ... in order; counts by their number, with "N+" counted as N (a lower bound,
said in the summary).

**Who is left out.**

- `worst` and `sum` need every neuron assessed, because the worst of four neurons tends to be higher
  than the worst of three. Otherwise the animal is left out, except that an animal whose assessed
  neuron already has the worst level counts under `worst`.
- "Can't tell" is never a level: that neuron is unclear (under `mean`, `"unclear": "exclude_animal"`
  leaves out the animal instead).
- Neurons skipped by their call are "not assessed", kept apart from unclear.
- An unanswered question leaves the animal out as incomplete.

**The descriptive table.** `rank_outcomes.csv` gives, per microscope and genotype, the animals with a
value, the animals left out by reason, the median and quartiles (linear interpolation, as Excel
QUARTILE.INC), the % at each level (scales whose value is a level) or the mean (counts), and the level
key. The summary warns when the share left out differs by more than 20 points between groups.

**The test.** Genotypes are compared within each microscope, for the same pairs as Fisher's test, with
the Mann-Whitney (Wilcoxon rank-sum) statistic on the animal values.

- The p-value comes from permuting the pooled midranks, so ties are handled correctly: exact (every
  rearrangement) when there are at most 100,000 rearrangements, otherwise 100,000 random ones with
  seed 1404 (recorded in the table).
- scipy's default for tied data is a normal approximation, which gave p-values about half the correct
  size on short scales with many ties in checks made for this feature; GraphPad Prism also reports
  exact p-values with ties.
- If every animal has the same value, p = 1.
- Bonferroni within each outcome.
- Normality is not tested because a rank test is used (ARRIVE 2.0 item 7b).

**Effect size.** `prob_a_worse` = U / (n_a n_b), the chance that a random genotype_a animal scores
worse than a random genotype_b animal, ties counted half (Vargha and Delaney's A), and Cliff's delta =
2A - 1. The test is about that probability, not about medians, which are often equal on short scales.

**Cutoff.** An optional `cutoff` (a level after the first for a scale, a number of 1 or more for a
count), set before scoring, also gives the share of animals at or past it, tested with Fisher's exact
test (rows marked with the cutoff in `definition`).

`per_neuron.csv` gets each neuron's answer and the animal's value. Rank outcomes cannot be used by
`interaction` or `allow_cross_microscope`.

Left out on purpose: Brunner-Munzel (its p-values were far too small at 5 animals per group, and scipy
has no permuted version), t-tests on counts, and regression or mixed models.

#### interaction_test.csv

Only if `unblinding.interaction` is set. A test of genetic interaction between two single mutants (P1,
P2 = share of affected animals) and their double.

**The model.** The expected share in the double comes from one model, chosen before scoring (Mani et
al. 2008, PNAS 105:3461, applied to the share of unaffected animals):

| Model | Expected share in the double |
|---|---|
| `product` (default) | 1 - (1 - P1)(1 - P2), the share expected if the two genes act independently |
| `additive` | P1 + P2 (at most 1) |
| `min` | the larger of P1 and P2 |

A double above the expected share is consistent with the genes acting redundantly; this is a
statistical statement, not proof of mechanism. The model is part of the analysis plan that `prepare`
records and unblinding checks (below), so nobody can try all three models after seeing results.
`outcome` chooses the calls (default) or a `question_outcomes` name.

**The binomial test** (the default in `config.json`; a test set up in the app starts with the
likelihood-ratio test below). The observed count in the double is compared with the expected
share by a two-sided one-sample binomial test (`scipy.stats.binomtest`), only within a microscope
where all three genotypes were scored (Bonferroni if more than one). The expected value treats P1 and
P2 as exact and ignores their sampling error, so the test is anti-conservative when the single mutant
groups are small: in simulations with 30 animals per genotype and no interaction, it was significant
at 0.05 in about 7 to 19% of runs.

**The likelihood-ratio test** (`interaction.test: "likelihood_ratio"`, recommended for new projects;
part of the analysis plan) instead fits the three genotypes together. Under the model the double's
share is the model's function of P1 and P2, fitted with them by maximum likelihood, and the likelihood
ratio G is compared with chi-square on 1 degree of freedom (for a one-sided alternative, the signed
square root of G with the normal distribution). It allows for the singles' sampling error and stayed
near 5% in the same simulations. The table then has `likelihood_ratio_g` and
`p_likelihood_ratio_*`.

### Optional analysis settings

All in `unblinding`, all off by default.

**`secondary_defect_calls`:** a second definition of "affected" (for example dim or missing, when the
primary `defect_calls` is missing only). It is reported per microscope with 95% Wilson intervals and
is never pooled over microscopes. It is tested (Fisher's exact, within one microscope) only if
`test_secondary` is true. Use this when one call (such as dim) depends on brightness, which is not
comparable between microscopes, while another (present or missing) is.

**`allow_cross_microscope`:** the measures (`calls` or a `question_outcomes` name) whose tests may
compare genotypes imaged on different microscopes. For those measures each genotype's animals are
pooled over microscopes, every table lists the microscopes and the number of animals from each, and
every test whose genotypes come from different microscopes carries the warning "microscopes differ
between genotypes". Pooling hides differences in sensitivity between instruments (for example a faint
cell is more likely to be called missing on a less sensitive system); state this as a limitation.

**`comparisons`:** the planned `[genotype_a, genotype_b]` pairs. Only these are tested, so the
Bonferroni correction counts only the planned tests.

**`alternative`:** `two-sided` (default), `greater` or `less`, for Fisher's exact and the binomial
test. A one-sided test needs `comparisons` (the direction is genotype_a compared with genotype_b; for
the binomial test, the double compared with the expected value). `interaction.expected_direction`
(`higher` or `lower`) states the hypothesis; the interaction table then says whether the result
supports it (significant after correction and in that direction).

**Source `note`:** a label such as "collected by others" on a source in `sources`. Tables list the
sources of each group with an asterisk and the summary explains it.

**`dim_cutoff_sd` (k):** a measured line, set before scoring, for checking calls afterwards. Part of
the analysis plan.

- A measured neuron (drawn region or accepted suggestion) is below the cutoff when its
  background-subtracted mean in the sharpest plane is less than k times `background_sd_best_plane`.
- `dim_check.csv` lists, per settings group and call, how many measured neurons fall below the cutoff
  and the range of signal / noise. Compare only within a settings group.
- A "normal" or "dim" call below the cutoff is a cell that could not be told from background by this
  measure; review it, but calls are never changed by numbers.
- Neurons called missing have no region and are not in this table.
- It is a contrast-to-noise visibility check (is the cell distinguishable from its own surroundings),
  not a comparison with control brightness.
- It needs drawn regions: outline every visible neuron if you set it.
- Regions whose ring has no measurable noise (robust SD 0, common in 8-bit or low-count images) cannot
  be checked; the summary counts them.

**`primary_outcome`** (`calls`, a `question_outcomes` name or a `rank_outcomes` name; part of the
analysis plan): adds an `outcome_role` column (primary or secondary) to every test file, a line in the
summary and a Methods sentence. Bonferroni correction is still within each file.

### Scorer consistency

When the project has hidden repeats. `reliability.csv` gives, over the pairs where both the repeat and
its original were scored, percent agreement and Cohen's kappa for:

- usable or not;
- animal affected (primary, and secondary if set);
- each neuron's call (linear-weighted kappa over the ordered calls);
- cells seen (linear-weighted);
- each `question_outcomes` measure;
- each scale rank outcome whose animal value is a level (linear-weighted).

Neurons are matched by the recorded side when both images name each neuron's side, otherwise by
number.

**Reading kappa.**

- Kappa is undefined when every answer is the same.
- With few pairs, kappa is imprecise; kappa below 0.60 is usually considered inadequate
  ([McHugh 2012](https://doi.org/10.11613/BM.2012.031)).
- The same scorer seeing an image twice measures consistency, not agreement between people.
- When most animals get the same answer (for example few affected animals), kappa can be low even with
  high agreement ([Feinstein & Cicchetti 1990](https://doi.org/10.1016/0895-4356(90)90158-L)); report
  percent agreement with it.

**Optional, in `reliability`:** `extra_statistics: true` adds a 95% bootstrap interval for kappa
(pairs resampled 2,000 times, fixed seed), PABAK and Gwet's AC1 (both from exact agreement).
`call_weights` sets the kappa weights for neuron calls: `linear` (default, for ordered calls such as
normal, dim, missing), `quadratic`, or `none` (for calls that are not ordered).

### Agreement between scorers

Only when a second scorer's scores were imported.

- `between_scorers.csv` compares the primary scorer's and each second scorer's answers on the original
  images both scored (repeats are left out, so no image counts twice), with the same measures and
  statistics as for hidden repeats (percent agreement, Cohen's kappa, weighted for ordered calls, and
  the optional extra statistics).
- `scorer_disagreements.csv` lists each code and answer (usable, cells seen, each neuron's call) where
  the two differ, with no genotype.
- Every results table uses the primary scorer's answers only; the second scorer's answers are never
  merged in or used to settle disagreements.
- The Methods draft says how many images the second scorer scored, the share, when, and that they
  scored blind, without the key, in a different order and without seeing the first scorer's answers
  (and, if they scored after the primary analysis was unblinded, says so).

### The analysis plan lock

When any of `secondary_defect_calls`, `allow_cross_microscope`, `comparisons`, a one-sided
`alternative`, `dim_cutoff_sd`, `primary_outcome`, `question_outcomes`, `rank_outcomes` or `interaction`
is set, `prepare` writes the whole analysis plan (both definitions of affected, comparisons, direction,
cross-microscope measures, interaction model, dim cutoff) to `log.md`. Unblinding compares the current
settings with the last plan recorded before the first score was saved.

| At unblinding | What happens |
|---|---|
| The settings match the plan | Tests run. |
| The settings differ | No test is run and every test file says why. Descriptive tables are still written. |
| No plan was recorded before scoring | Tests run but are marked `set_before_scoring = NO`. |
| The plan was changed in the app after scoring began, with a reason, and `config.json` still holds that plan | Tests run on it and are marked `set_before_scoring = no: changed after scoring began`. The summary and the Methods draft call them exploratory and give the reason (a deviation from the pre-registered plan, reported as such; Willroth & Atherton, 2024). |
| `config.json` was edited by hand after that | No test is run. |

The app changes the plan after scoring began only once a reason is typed; the reason and the new plan
are written to `log.md`.

### The scoring criteria lock

Unblinding compares the current form with the last one recorded before the first score (section 7). If
it differs, no test is run, every test file says the scoring criteria changed, and the Methods draft
says only descriptive results are reported.

The app can change a locked form only after the scorer types a reason. The change, the reason and
whether it went beyond wording are written to `log.md`, the summary and the Methods draft list each
change with its reason (as recommended for reporting deviations from a pre-registered plan; Willroth &
Atherton, 2024), and the rule above still decides whether tests run.

Projects built before 1.3.0 have no form line. For them the full settings that Build wrote to `log.md`
before the first score are used. If those differ or are missing, tests run but are marked
`form_recorded_before_scoring = NO` and the summary warns.

### Image folders changed after scoring began

With a reason typed in the app:

- a moved or renamed folder is followed by `key.csv` and `inventory.csv` (backups in `key/backups/`);
- a changed genotype rule is applied to the coded images, each change listed in
  `key/changes_after_scoring.csv` (not in `log.md`, which never names a code's genotype);
- a new exclusion rule leaves matching coded images out at unblinding, and the summary and the Methods
  draft give the number and the reason.

Folders with coded images cannot be removed.

### methods_text.md

A draft Methods paragraph built from the project files: inclusion counts, the date codes were assigned
(date of `key/seed.txt`), the scoring dates (`scoring/score_history.csv`), the unblinding date,
measurement parameters and the tests run, plus a table of who knew the genotype at each stage (ARRIVE
2.0 item 5). Names and whether imaging was blind come from the `blinding` section of `config.json`
(`coded_by`, `scored_by`, `imager_knew_genotype`); anything not set is left in [brackets] to fill in.
Check every sentence before use.

## 9. Limits to state

- **Brightness** depends on detector gain, laser power and the microscope. Numbers are comparable only
  within a settings group (same microscope and settings). HyD and PMT gain are not simple multipliers,
  so no correction between settings is attempted.
- **Calls** are human judgements made on fixed display ranges; the software records but does not make
  them.
- **Filter size and background ring** are common choices, not values from a published protocol; they
  are settings and are logged.
- **One version per project.** `log.md` records the wormscore version at every step. Updating between
  Build blind set and Unblind would put two versions in the Methods; the app warns before such an
  update and defaults to Not now. If it happens, the Methods draft names every version `log.md`
  records from the build of the current blind set to unblinding (Scan data, Build and Add new images
  each record one), and says which one computed the results.
- **Identity channel.** No correction is made for bleed-through between channels or for the small
  depth shift between colors (chromatic aberration). Acquire channels sequentially, image
  single-label controls, and remember that gut autofluorescence shows in red too, so a suggestion on
  the identity channel can still land on gut granules.
- **Files you open.** XML metadata is parsed with Python's standard library, which does not fetch
  external entities; only open files you trust.

## Projects made with earlier versions

Updating alone never changes a project's results. What an older version read or wrote differently
matters when you interpret a project it built, or when you run Scan data again. The
[changelog](../CHANGELOG.md) has the full list, with a "Who is affected" note wherever a change can alter
what an existing project shows.

**Settings and bit depth**

- **Before 1.26.7,** gain and laser power were not read for Zeiss, Nikon and OME-TIFF, and Zeiss
  camera files were read as 16-bit. A project built earlier keeps its settings groups and saturation
  level until it runs Scan data again. If the new scan would change them for images that already have
  a code, the Build card says what would change, and the rebuild that applies it records the same in
  `log.md` (for example "4 coded images move from settings group A to B"). Scores never change.
- **Before 1.26.11,** laser power and detector voltage were not read for Nikon A1 and AX confocals.
- **Before 1.26.12,** an OME-TIFF value given on the instrument's detector or light source, not on the
  channel, was not read.
- **Before 1.26.13,** the settings text said "HyD gain" for every Leica detector, and the objective of
  an OME-TIFF written by Bio-Formats read "40.0x/1.2000000000000002 Water" where it now reads
  "40x/1.2 Water". After a new scan the key and the Methods draft carry the new wording. Wording alone
  moves no image: such settings keep their group and letter (also for images added later), nothing is
  warned, and flat-field slide images measured before still match their images.
- **1.26.14 to 1.26.16** wrote both values of an Olympus `.oir` laser share that the file records
  twice, and flagged the image, which also put it in another settings group than images taken at the
  same settings. Since 1.26.17 the image's share is used alone where the file's second record agrees.

**Which files and channels were read**

- **Before 1.26.5,** a magnification written with a decimal (`63.0x`) was read as 0x and the image
  left out. An OME-TIFF cut short, or a set with a file missing, was coded with black planes. An image that could not be read, or a
  file with no pixel size, stopped the whole build, and a file open in another program stopped Scan
  data.
- **Before 1.26.8,** OME-TIFF pixel sizes in nm or mm were read as micrometres (wrong by a factor of
  1,000, which affects the z-extent rule, the display scale and region sizes); OME-TIFF files in the
  2010 to 2015 schemas, and sets whose metadata is in another file, were not read; a Leica sequential
  scan from current LAS X gave no reporter channel; "TLR-4", and "BFP" in projects that do not use BFP,
  were read as brightfield ("BFP" in projects that use it was fixed in 1.26.0), and "TD" and "TPMT"
  were not; and tile scans, other extra dimensions and uncalibrated Nikon pixel sizes were not flagged.
- **Before 1.26.13,** OME-TIFF files in the 2008 schema were read with no channels, and
  `manifest.csv` gave no reason for a Nikon colour picture.
- **Before 1.26.14,** Olympus `.oir` files were not read.

**Results**

- **Before 1.26.4,** a call named `+` or `-` matched no call at unblinding and was never counted as a
  defect; an override was needed to unblind below `min_scored_worms` even with every image scored; and
  the three warnings under "Warnings" in section 8 were not given.
- **Before 1.26.13,** the `imaging_date` column of `per_neuron.csv` held the first 10 characters of
  the date as the file wrote it.

**Builds**

- **Before 1.26.3,** a build did not stop for a missing `key.csv` or a missing image file. A rebuild
  that kept the codes after Add new images worked the display ranges out again over all images, so
  images already scored could look different, and it did not keep the settings-group letters or
  `added_on`.
- **Before 1.26.15,** a rebuild deleted `blind/` first, so a failure left only part of the coded
  images until the next rebuild, and a failed Add new images left folders that stopped every later
  one.
- **Flat-field correction, before 1.26.15:** a rebuild after scoring began corrected every image again
  with all sessions and recorded no warning. In a project where slide images were added after scoring
  began and the blind set was then rebuilt, images scored before the rebuild were scored on the
  earlier correction, while the measurements use the later one. Before 1.26.16, `key.csv` alone recorded
  the correction, in words that can fit two sessions; keys built then still warn when they do.
- **Joint bleed-through fit, 1.26.0 and 1.26.1:** collinearity was checked with the centred variance
  inflation factor, which missed channels that share an offset.
- **Before 1.26.18,** a Nikon or Olympus date written day first with AM or PM (17/7/2009 4:01:38 PM) gave no
  flat-field day, so such images used the average of the microscope's sessions; projects scanned
  before keep that reading until they run Scan data again.
- **Before 1.26.18,** OME-TIFF files in the `2016-DEV0` schema were not read, and a Micro-Manager style OME-TIFF whose planes are described only in its
  `.companion.ome` file was read as its first plane, flagged "illuminations".

**When a feature arrived.** A project scored before a feature arrived has no record of it, except
for the checks that run on finished projects (bleed-through, gut autofluorescence).

| Version | Feature |
|---|---|
| 1.0.0 | marker presets, the identity channel |
| 1.3.0 | the recorded score form |
| 1.4.0 | scales, counts, rank outcomes, questions once per animal |
| 1.5.0 | criteria recorded from the home screen |
| 1.6.0 | changing locked criteria with a reason |
| 1.14.0 | questions for each side |
| 1.15.0 | the reference genotype in the plan |
| 1.18.0, 1.19.0 | tilt in 3D; the identity channel in 3D |
| 1.20.0 | bleed-through check |
| 1.21.0, 1.21.1 | flat-field correction; fallbacks instead of stops |
| 1.21.2 | changing the plan or the image folders with a reason; repeats placed closer in small sets |
| 1.22.0, 1.25.0 | gut autofluorescence check; its flags in Browse |
| 1.23.0 | several identity channels |
| 1.24.0 | markers in Project settings |
| 1.26.0 | joint bleed-through fit |
