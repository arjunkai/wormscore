# Changelog

What changed in each version of wormscore, newest first. Where a change can alter what an existing
project shows after its next Scan data, build or unblinding, the entry has a **Who is affected** note.
Updating alone never changes a project's results. What is planned is in [ROADMAP.md](ROADMAP.md); every
version can be downloaded from the [Releases page](https://github.com/arjunkai/wormscore/releases).

## 1.26.19

**Check the reading, held against ZEN lite.**
- Check the reading names the fields of ZEN's Info view as ZEN 3.15 (ZEN lite) shows them, checked
  on a real Zeiss LSM 880 file: every value matched. ZEN names the objective by its model (for example
  C-Apochromat 40x/1.2 W Korr FCS M27, where wormscore writes 40x/1.2 Water), and its Lasers row lists
  the first scan track's lasers only; the reporter's laser and power are in the file with its channel.
- **Who is affected:** nobody's results change; only the text of Check the reading.

## 1.26.18

**Check the reading of a file against the microscope software; Micro-Manager z-stacks read whole; faster builds.**
- **Check the reading...** under Scan data (`wormscore check-reading <file>`) shows what wormscore reads
  from one microscope file (objective, pixel size, z-step, planes, bit depth, each channel's role, the
  reporter's detector, laser and averaging settings) and where LAS X, ZEN, NIS-Elements or the FluoView
  software shows each value. It reads the file's metadata only and changes nothing. A Leica TCS SP8
  gain is shown with the unit LAS X uses: percent for a HyD, volts for a PMT. For Leica files the
  places are the sections of LAS X's Properties window, as checked against LAS X Office on a real TCS
  SP8 file.
- Leica files have a date. Scan data records each image's start time, which LAS X shows as Start Time,
  in the computer's time zone (for example 2026-10-15 16:20:58 -04:00). Before, Leica images had none,
  so `imaging_date` was empty and flat-field correction could not tell their days apart. A scan on a
  computer in another time zone can move a Leica image to the day before or after; the Build card
  then says so.
- Clearer wording for the notices about two scoring windows, for example "Another window changed
  this image's score. Open the image again to see it, or save yours."
- The update box showed each line of the release notes as a paragraph of its own, breaking sentences
  in the middle. Lines of one paragraph or list item are now joined.
- The user guide has pictures of the main screens: the home page, the criteria editor, the scoring
  page, the Results card and Browse images, in dark or light to match the reader's GitHub.
- Light mode is less faded: cards, buttons and step circles have clearer edges (about as clear as in
  dark mode), and typing boxes have edges at 3:1 contrast in both themes, as WCAG asks.
- OME-TIFF from Micro-Manager whose planes are described only in the `.companion.ome` file next to it
  was read as its first plane, with the flag "illuminations (2)". The pages are now read as the planes,
  channels or time points that file gives them. Pages it does not place are flagged plainly.
- OME-TIFF written by a development build of Bio-Formats (schema name 2016-DEV0) is read. It was listed
  as not used.
- When two scoring windows save a score or outlines for the same image one after the other, the later
  save is kept, as before, and both windows now say so.
- Scan data reads dates written day first with AM or PM or with no time (for example 17/7/2009 4:01:38
  PM), so such
  images get their flat-field imaging day. Before, they used the average of the microscope's sessions.
- Build blind set is about 2.4 times faster on large stacks, and with an identity channel uses 2.5 GB
  instead of 6.1 GB at most (4 images of 2048 x 2048 x 50). Opening an image in Browse before the build
  is about 3 times faster. Every file a build writes is the same, byte for byte.
- Scan data flags an image whose reporter or identity channel the file names for a fluorophore of another colour,
  for example a channel named mCherry that was taken as GFP because it was excited at 488 nm (16 public
  Olympus files did this). The channel keeps its role; the flag asks you to check it.
- A Build-card line about a new scan could stay as it was when the key or the scan was written twice
  within a second or two (drives formatted FAT32 or exFAT keep file times to 2 seconds).
- The real-worm demo shows one reporter, flp-17::GFP, which marks the BAG pair, in two L1 and two L2
  heads, and the form asks about BAG 1 and BAG 2. It compared flp-17 with nlp-45, a reporter of other
  neurons, and asked for the two brightest GFP cells. It downloads about 230 MB (was 150 MB).
- The user guide is organised by step and feature; the app's Guide button opens it. What changed in
  each version is now in this file; ROADMAP.md lists what is planned.
- **Who is affected:**
  - Projects with Micro-Manager OME-TIFF z-stacks: after the next Scan data such images have all their
    planes. If any of them already has a code, the Build card says "the number of planes of N coded
    images changes from 1 to 2", and a rebuild keeping the codes records it in `log.md`. Images of a
    stack shallower than the minimum z extent stay left out.
  - Projects with 2016-DEV0 OME-TIFF files in an image folder: after the next Scan data they are listed
    as images, and join a blind set only through Add new images or a build with new codes.
  - Flat-field projects whose microscope writes such dates: nothing changes until Scan data runs again.
    After it, the Build card names the coded images whose day is now read. Before scoring, a rebuild
    uses the new day (choose the slide images again so theirs are read the same way); once scoring has
    begun, coded images keep the correction they were scored with.
  - Leica projects: nothing changes until Scan data runs again. After it, `inventory.csv` has each
    image's date, and after the next build `key.csv` and the `imaging_date` column have it too (a
    label; nothing is tested by it). With flat-field correction, slide images keep their undated
    session, so the correction stays the same, and the Build card says "the flat-field imaging day of
    N coded Leica images is now read from the file; choose the slide images again so theirs are read
    the same way". Before scoring, choosing them again gives each day its own slide images; once
    scoring has begun, coded images keep the correction they were scored with.
  - Projects whose files name a fluorophore of another colour for the reporter or identity channel: the
    next Scan data adds the flag; roles, codes and results do not change.
  - Everyone else: nothing changes in scores, measurements or reports. `inventory.csv` gains an
    `acquired_day` column at the next Scan data.

## 1.26.17

**Olympus `.oir`: a laser's power that the file records twice is settled from the file.**
- In 10 of 23 real files the first plane's record of a laser's share differs from the image's (1% against
  0.1%). 1.26.14 wrote both and flagged the image, which also put it in another settings group than
  images taken at the same settings.
- The file records the share a second time in both places, as the value set in the software. That
  record agreed between the image and the plane in all 23 files and fits the image's share, and in four
  series of one session the pixels follow the image's share (equally bright, where the plane's value
  would mean ten times brighter). The image's share is now used alone when that second record agrees.
- A file without the second record, or where it does not agree, still gets both values and the flag.
- **Who is affected:** projects with `.oir` files scanned with 1.26.14 to 1.26.16. After the next Scan data
  the settings text of such images loses "(first plane: ...)"; the Build card says if coded images
  change settings group.

## 1.26.16

**Fixes from an independent review of 1.26.13 to 1.26.15.**
- **Add new images** never gives a new image a code that already has a saved score or outlines. An Add new images that failed in an earlier version could leave a coded image without a key row;
  1.26.15 replaced it with another image under the same code, with its score still saved.
- **Rebuild with new codes:** if the new coded images could not be put in place (a file held by another
  program), the key, seed and drafts that had been set aside stayed aside. They are put back, and the
  message says to close other programs, not to check the free space.
- **Flat-field correction after scoring:** `key.csv` records how an image was corrected in words that
  can fit two sessions, so slide images added later could take a scored image under the same words.
  Each build now records the session of each code (`key/flat_field_sessions.json`), and a rebuild holds
  each coded image to it. For a key built before, slide images added after the image got its code are
  not used for it; if the words still fit two sessions, the build warns.
- **Settings worded anew (1.26.13):**
  - A PMT and a HyD at the same gain number, once both written "HyD gain", are two settings: the new
    scan is shown as a change and Add new images warns, where the two could share a group unnoticed.
  - Add slide images for a session that is only worded anew stored it as a new setting with its own
    brightness factor; it is now that session. A rebuild after scoring no longer warns of a flat-field
    change that was wording alone.
- **OME-TIFF in the 2008 schema:** the exposure is read (two exposures shared a settings group), and an
  image with two pixel sets is left unresolved, not read from the first.
- Text from a file that would be a spreadsheet formula gets its leading `'` in every reading of the file,
  so slide images and control images name a microscope as its images do.
- **Mac, app on another disk:** of two kept copies, Go back uses the one kept last (1.26.15 could delete
  the real previous version); a folder that cannot be written gives a plain message.
- The real-worm demo also tries again when a download ends early. The Methods draft names an objective
  once when the key holds it in two wordings.
- **Who is affected:** none of 1.26.13 to 1.26.15 was published as a release. A project with flat-field
  correction gets `key/flat_field_sessions.json` at its next build; nothing else changes for it.

## 1.26.15

**Rebuilds after scoring, builds that fail, outlines saved from two windows, and the Mac updater on another disk.**
- **Flat-field correction:** slide images added after scoring began, followed by "Rebuild, keeping the
  current codes", corrected every image again with all sessions. Scored images changed (planes, display
  ranges, `key.csv`) with no warning. Such a rebuild now corrects each coded image as `key.csv` records
  it; slide images added since are used only for images added after them, and `log.md` says how many
  images were kept. A correction that cannot be kept (the setting removed by hand, a new scan) is built
  as set now, with a recorded warning.
- **Build:** the coded images are made in `blind_building/` and replace `blind/` in one step when all are
  made. A rebuild that failed midway (a file that cannot be read, a full disk) used to leave only part of
  the coded images until the next rebuild, and a failed Add new images stopped every later one. Both now
  leave the blind set, key, scores and drafts as they were, and the Build log says so. A rebuild needs
  free space for a second copy of the blind set while it runs. A failed build is started again, not resumed.
- **Save outlines:** with two scoring windows on one image, the save clicked first could be written last.
  The later click now wins, and the other window says its outlines were not saved.
- **Mac update with the app on another disk than the home folder:** the swap copied the app between disks
  and could stop halfway, leaving part of an app while reporting "The previous version was kept". The kept
  copy and the download now stay in hidden folders next to the app, so every move is one rename. Windows
  was not affected.
- The real-worm demo tries a download that stalls three times, then says to check the connection. One
  timeout ended it with a Python error.
- Text from a microscope file (a channel or microscope name) that a spreadsheet would run as a formula is
  written to `inventory.csv` and the key with a leading `'`, as answers in `scores.csv` are.
- **Who is affected:**
  - Projects where flat-field slide images were added after scoring began and the blind set was then
    rebuilt with 1.21.1 to 1.26.12: images scored before that rebuild were scored on the earlier
    correction (`key.csv`, column `flat_field`, shows the later one). None of those versions was
    published as a release.
  - Mac users whose app is not on the home folder's disk.
  - Everyone else: nothing changes in scores, measurements or reports.

## 1.26.14

**Olympus `.oir` files are read (FV3000, FV4000, FVMPE-RS).**
- Read from the file's own metadata with [oirfile](https://github.com/cgohlke/oirfile) (BSD-3-Clause):
  the reporter, identity and brightfield channels (brightfield is the channel on the transmitted
  detector, TD), the reporter's detector voltage, gain, offset, laser share and averaging, bit depth,
  pixel size, z-step, objective and date. Checked against Bio-Formats 8.5.0 on 23 public files; the
  pixels of 11 of them are identical.
- Flagged instead of guessed: lambda scans, a tile of a tile scan (from `matl.omp2info`), a scan
  stopped inside its last plane (that plane is left out), images made by the Olympus software
  ("Modified"), brightness correction along z, and files that record two values for a laser's share
  (both are written; check in the Olympus software).
- Not read: `.poir` archives, line scans, and the stage position (the files do not record it).
- Still wanted: an FV4000 file with a transmitted-light channel, and a file whose laser power is known
  from the Olympus software. Tell us if `outputs/inventory.csv` differs from what the microscope shows.
- Text from a file that is not a date is never written to `per_neuron.csv` as a spreadsheet formula.
- **Who is affected:** labs with an FV3000 or FV4000, who had to export to OME-TIFF; and projects whose
  image folders already hold `.oir` files. After their next Scan data those files are listed as raw
  images (before: "reference (not an image)"). Codes, scores and results do not change; the new images
  join a blind set only through Add new images or a rebuild. No other reader changed.

## 1.26.13

**Imaging dates from Nikon files, OME-TIFF in the 2008 schema, and how settings are worded.**
- `per_neuron.csv`: the `imaging_date` column held the first 10 characters of the date, which for Nikon
  files is written as the acquiring computer shows it ("11/3/2009 1"). It is now YYYY-MM-DD when the date
  has one reading (17/7/2009), and the date as written, marked "day or month first: not known", when it
  has two (11/3/2009). Dates already written as 2026-01-02 give the same text as before.
- OME-TIFF files in the 2008 schema were read with no channels. Their channels are now read where the
  file says which is which, and are flagged and left unresolved where it does not.
- Leica: the settings text said "HyD gain" for every detector. A PMT is now named "PMT gain".
- OME-TIFF written by Bio-Formats: the objective read "40.0x/1.2000000000000002 Water" and now reads
  "40x/1.2 Water" (it is named in the Methods draft).
- Nikon colour-camera files (histology, brightfield pictures) are still not used; `manifest.csv` now says why.
- **Who is affected:**
  - Projects with Nikon `.nd2` images: the `imaging_date` column of `per_neuron.csv` changes the next
    time they unblind. It is a label; no count, test or other number changes.
  - Leica projects with the reporter on a PMT, and OME-TIFF projects from Bio-Formats: after the next Scan
    data the settings or objective text is worded anew. Wording alone moves no image to another settings
    group, also for images added later; nothing is warned, and flat-field slide images measured before
    still match. "Rebuild, keeping the current codes" writes the new wording into the key.

## 1.26.12

**OME-TIFF files converted with Bio-Formats, flat-field slide images after a new scan, and the Windows zip.**
- OME-TIFF: Bio-Formats writes the detector gain on the instrument's detector and has the channel point
  to it. Only the channel was read (1.26.7), so two real Zeiss files converted to OME-TIFF (gain 550 and
  800) still shared a settings group. The value is now followed to the detector or light source named.
- Flat-field correction: slide images measured before 1.26.7 carry the settings text of that time, so
  after a new scan an image no longer matched its own slide images. Slide images of the same objective
  and zoom now stand in first; before, the nearest day alone could pick another objective's taken that
  day. Build still warns "other settings" for such images. Measure the slide images again (before
  scoring) to have them match exactly.
- Zeiss camera files that give a channel only its dye list the dye as the channel name in `inventory.csv`.
- The Windows zip is built by GitHub's `win-app` check on every change (the same build it updates and
  goes back with) and kept with the run, with the versions of everything inside it. It was built by hand.
- **Who is affected:** projects with OME-TIFF files written by Bio-Formats, after their next Scan data
  (the Build card says which coded images would change group).

## 1.26.11

**Nikon A1 and AX confocal files: laser power and detector voltage are read.** These microscopes
write them in a form of their own, so every such image had empty settings and one settings group,
whatever its laser power or detector voltage. The reporter channel's laser power, detector voltage (HV)
and line averaging are now read. Checked against a real A1 file (Zenodo 10814759).
**Who is affected:** projects with `.nd2` files from an A1 or AX confocal. As with 1.26.7, nothing
changes until Scan data runs again; the Build card then says which coded images would change group.

## 1.26.10

**Fixes from an independent review of 1.26.7 to 1.26.9, and faster Build and Browse on large images.**
- **Go back** with a file of the kept copy open in another program (a virus scanner) could leave the
  kept copy with one file; the next Go back then put that folder in the app's place and said it had
  worked. The kept copy is now checked and moved in one step, or nothing is changed.
- **Settings-group letters** after a new scan (1.26.7): the rebuild after the warned one could swap the
  letters A and B with no warning, and an image added later could get a letter already in use, so two
  settings shared a group. A rebuild that keeps the codes now keeps the letters from the key, and a new
  group always gets a letter no group has.
- A channel named "td-Tomato" or "TD Tomato" was read as brightfield in 1.26.8. Only a detector named
  TD or TPMT (alone, or with a number or track) is; a brightfield name the project lists itself is
  matched as before.
- The Methods draft found the build of the current blind set from a file's date, which copying a
  project can reset; the version that built it could be left out. It now reads `log.md` and the key folder.
- On Windows, an update that worked was reported as failed if the note of the kept version could not be written.
- **Build and Browse on large images:** the 3 x 3 median filter took about 40 s per 2048 x 2048 x 50
  stack and now takes about 3 s, with exactly the same values (a test compares them).
- METHODS: Bonferroni correction is per definition of a defect and per outcome, as the files' `n_tests`
  column shows; it said "for the tests in the file".
- **Who is affected:** a project that rebuilt after a 1.26.7 to 1.26.9 scan and then rebuilt again, or
  added images, may have settings-group letters that differ from its `log.md` line or are shared. Run
  "Rebuild, keeping the current codes" once more if images were added; `key/settings_groups.csv` lists
  one letter per setting when it is right. None of these versions was published as a release.

## 1.26.9

**Update and Go back, the scoring page, and the Methods draft** (independent audit).
- **Update and Go back never leave the app folder incomplete.** With a file in the app folder open in
  another program (a virus scanner, a second copy of the app), a failed swap on Windows moved some files
  and left the folder without `wormscore.exe`. Each move is now all or nothing: a swap that fails changes
  nothing, Go back still restores the copy that is kept, and a failed Go back can be tried again.
- **Update and Go back are checked with the real app on every change.** GitHub's Windows and Mac machines
  build the app twice, install the newer build over the older through the app's own updater, go back, and
  repeat with the app folder held so the swap fails. No release is involved: for this check only, the
  updater reads the "latest release" from a local file, and the checksum check stays in force.
- **The scoring page warns when the browser changes the images.** Safari Private windows, Brave, and
  Firefox with fingerprinting protection change the image data a page reads back, which shows as speckle.
  The page checks for this when it opens and shows a warning above the image. Score in a normal window.
- Number keys answer the questions after a click on an answer in Safari; they did nothing until Tab was pressed.
- **Save outlines** shows "Saving..." at once and no longer holds up saving a score. It is also much
  faster on large stacks (two cells on a 2048 x 2048 x 50 stack: about 46 s before, 6 s now); the numbers
  are exactly the same (a test compares them with the earlier method).
- **Browse images** on a laptop screen at 125% scaling: Next image was cut off at the edge of the window
  and the measurements were wider than their panel. The measurements now show one column for each region.
- The Methods draft names every wormscore version `log.md` records from building the blind set to
  unblinding, and says which one computed the results. It used to name only the version that ran Unblind.
  A project built and unblinded with one version gets the same text as before.
- An OME-TIFF that names the file holding its metadata is followed only to a file in the same folder.
- Mac: the README and the note in the zip give the "Open Anyway" steps as current macOS shows them, and a
  one-line Terminal command that does the same (`xattr -dr com.apple.quarantine /Applications/wormscore.app`).

## 1.26.8

**Leica sequential scans from current LAS X, and files that are flagged instead of guessed** (independent audit).
- Sequential scans from LAS X 3.5 (SP8) and 4.4 (Stellaris) never gave a reporter channel, so none of
  their images could be used. Each step records only what differs from the master setting; its
  detection band, averaging and gain are now taken from the master where the step has none. Checked
  against real Stellaris and SP8 files.
- Tile scans, lambda scans and other extra dimensions were read as their first entry with no word. They
  are now flagged at Scan data, and Build warns. The same for a Nikon file with no pixel calibration
  (read as 1 um per pixel).
- OME-TIFF: pixel sizes in nm or mm are converted (they were read as micrometres); files in the 2010 to
  2015 schemas and sets whose metadata is in another file are read; one file of a plane-per-file set is flagged.
- A channel named "BFP" or "TLR-4" is no longer read as brightfield; "TD" and "TPMT" are.
- Nikon `.nd2` files from before 2010, and one with a damaged header, say why they cannot be read.
- Scan data's progress covers the whole scan. It used to reach 100% after the file checksums and stay there.
- `tools/bioformats_referee.py` compares what wormscore reads from a folder of files with Bio-Formats
  (gain, laser, bit depth, pixel size, z-step, channels). It is how these readers were checked.
- **Who is affected:** nothing changes until Scan data runs again. After it, a project with a channel
  named like "BFP" loses that channel as brightfield, and one with OME-TIFF sizes in nm gets the true
  pixel size; the Build card names a pixel size that would change for coded images.

## 1.26.7

**Detector gain, laser power and bit depth are read for Zeiss, Nikon and OME-TIFF** (independent audit).
- Images taken at different gain or laser power shared a settings group, so their brightness could be
  compared. Zeiss files now give the reporter channel's gain and laser share (and exposure and lamp
  intensity for cameras), Nikon files its laser power, OME-TIFF files its detector and light-source
  settings. The four real demo files were taken at gain 550 and 800 and are now two settings groups.
- Zeiss camera files were read as 16-bit. Their true bit depth (12 or 14) is now read, so saturation is
  flagged and Standard display covers the real range. A 16-bit Zeiss or OME-TIFF file that does not
  record it is flagged "bit depth not recorded"; state the level under `saturation_value` in `config.json`.
- **Who is affected:** projects with Zeiss `.czi`, Nikon `.nd2` or OME-TIFF images. Leica and Olympus
  projects were already read this way. Nothing changes until the project runs Scan data again: a built
  project keeps its settings groups and saturation level, and "Rebuild, keeping the current codes" gives
  the same blind set. **What to do:** run Scan data again. If the Build card then says that coded images
  would change settings group or saturation level, rebuild, keeping the current codes; the change is
  recorded in `log.md`. Scores do not change. Brightness comparisons at unblinding then use the new groups.

## 1.26.6

**Build blind set is much faster on large images with few cells.** Suggested cells are found
in a square around each bright point, not with passes over the whole image; the regions and numbers
are exactly the same (a test compares them with the earlier method). A 2048 x 2048 image with one or
two cells took about 3 minutes for this step and now takes about 2 seconds. Found in the audit's check
with large stacks.

## 1.26.5

**Fixes from an independent audit: files that cannot be read, and the Mac app.**
- An OME-TIFF cut short (a copy that did not finish, or a set with a file missing) was coded with black
  planes, so a cell that was there looked missing. It is now flagged at Scan data and left out at Build.
- One image that could not be read, or a file with no pixel size, stopped the whole Build with an error
  that named no file; a file open in another program stopped Scan data. Each is now left out with the reason.
- An objective written as `63.0x` was read as 0x, so those images were left out as below the minimum.
- Mac app: the update check and the real-worm demo use a certificate list carried in the app. They could
  fail on a Mac without the Python the app was built with. The Mac check on GitHub now hides that
  Python's certificates and makes real requests, reads the real Zeiss files, records what Gatekeeper says
  about a downloaded copy, and opens the pages in Safari itself.
- The home page no longer needs Safari 16.4: one expression kept older Safari on the loading screen.
- A demo is refused in a folder that already holds a project; it used to replace that project's `config.json`.

## 1.26.4

**Fixes from an independent audit: unblinding says when a result is not what it seems.**
- Scores saved or changed after the first unblinding (when the scorer knows the genotypes) were counted
  with no mark. The summary and the Methods draft now warn, and `merged.csv` marks each one. Tests still run.
- Folders or genotype rules changed after Scan data: Scan data and Build show a warning until they have
  run again (it was a message shown for a few seconds), and unblinding warns if the key still has the
  old genotypes.
- A genotype in the analysis settings that no image has (a typing mistake in `config.json`) is named at
  Build and at unblinding, instead of only "no comparison possible".
- **Calls or answers that start with `+`, `-`, `=` or `@`** (for example calls named "+" and "-") were never
  counted as a defect at unblinding, because `scores.csv` stores them with a leading `'`. They are now read
  back as chosen. **This changes the results of an existing project that uses such names: unblind it again.**
  Projects without such names report exactly as before.
- A project with fewer images than the planned minimum unblinds without the override once every image is scored.
- Fix: a failed Save in Project settings raised a script error after showing the message.

## 1.26.3

**Fixes from an independent audit: a build never changes what was scored.**
- With the key folder moved away (some labs keep it elsewhere while scoring), Build blind set gave every
  image a new code, so saved scores then belonged to other images. It now stops and asks for the key
  when scores or a second-scorer package exist.
- A rebuild checks that every image file can be found before it deletes the coded images. With the
  image drive not connected, it used to leave a project that was being scored without them.
- "Rebuild, keeping the current codes" after Add new images keeps each microscope's display ranges,
  the settings-group letters and which images were added when. It used to work the ranges out again
  over all images, so images already scored could look different, and the Methods draft lost the batches.
- If `scores.json` is lost while `scores.csv` is still there, the scores are read from `scores.csv`;
  the next save used to write `scores.csv` again with that one score only.

## 1.26.2

**Fixes from an independent review of 1.24 to 1.26.**
- Markers lock while a second-scorer package exists (no Change anyway...), as the criteria and analysis do:
  the package keeps the images it was made with.
- The joint bleed-through fit checks collinearity with the uncentred variance inflation factor, which suits a fit
  through the origin; the centred one missed channels that share an offset.
- The Markers card warns when the reporter and an identity channel emit at similar wavelengths.
- The Methods draft gives the real reason a joint fit was not measured.
- A hand-written `"marker": "GFP"` shows as Custom instead of stopping Project settings; channel name prefixes
  under 3 letters no longer affect brightfield detection.

## 1.26.1

**Fix: the "Unlocked for this save" notes in Project settings** pushed a one-line text to the right
edge (their class clashed with the top bar's). Found in a check of the new screens at 125% scaling.

## 1.26.0

**Bleed-through of each NeuroPAL channel.** With several identity channels, identity-only controls
also fit the reporter channel to all of them together (multiple least squares), giving each channel's share,
such as CyOFP1's at 488 nm. Images where the channels are too alike to separate (variance inflation factor
over 10) are left out and counted. Shown on the card, in `log.md` and the Methods draft.
- Fix: a channel named "BFP" was read as brightfield (it begins like "BF"). In a project that uses BFP it
  is now that channel; other projects read as before.

## 1.25.0

**Gut autofluorescence flags in Browse.** After the check has run, Browse images marks each
flagged code in the list (searchable as "gut autofluorescence") and names the flagged outlines and the
channel in the image's details. Codes checked and not flagged say so; nothing changes in projects without the check.

## 1.24.0

**Markers in Project settings.** Choose the reporter and the identity channel (none, a
preset, or NeuroPAL) and where suggested cells come from, without editing `config.json`.
- A channel with its own settings shows as Custom and is kept unless another choice is made.
- A change goes to `log.md`. Scan data and Build then show a warning until they have run again.
- Locked once scoring has started; **Change anyway...** with a reason, written to `log.md`.
- Disabled lists in Project settings show one arrow again, not a repeated pattern.

## 1.23.1

**A wording pass and an audit of 1.21 to 1.23.**
- Wording:
  - Warnings say what happened and what to do next, in at most two lines, with correct plurals ("3 images", not "image(s)").
  - Dialogs that unlock a setting have a plain Cancel button, as in the Apple and Microsoft guidelines.
  - A typed reason is quoted in the unlocked note.
- Fixes:
  - Flat-field sessions now read Nikon and Olympus dates written in the computer's locale.
  - The objective and zoom are now part of an imaging session.
  - A stand-in dark image now always comes from a session of the right size.
  - One unreadable slide image is skipped, not fatal.
  - A rebuild that keeps codes logs the spacing its hidden repeats really have.
  - Images left out by exclusion rules changed after scoring are marked in `merged.csv` (`left_out_after_scoring`), and the gut autofluorescence check leaves them out.
  - Its noise is measured without filtering, as the dim-call check measures it.
  - On Nikon files, which name no dyes, CyOFP1's emission settles its 488 nm tie with the GFP reporter.
  - Extra identity channels are checked like the first one (colour, label, settings).

## 1.23.0

**Several identity channels, for NeuroPAL.** `"identity"` may list up to four channels, and
`{"preset": "NeuroPAL"}` shows its four colours together (TagRFP-T white, mNeptune2.5 red, CyOFP1 green,
mTagBFP2 blue) in Projection, Planes and 3D, each scaled per image and never measured. The first channel
is the identity channel as before. New presets: CyOFP1 and mNeptune2.5. The real-worm demo (Sun and Hobert
2021, CC BY 4.0) is NeuroPAL with a GFP reporter, so it now shows the NeuroPAL colours too, which serves as
the NeuroPAL example. (The Yemini et al. 2021 NeuroPAL datasets, MIT License, hold no reporter to score.)
Projects with one identity channel build and show exactly as before.

## 1.22.0

**Gut autofluorescence check.** After unblinding, in projects whose images also recorded a
channel with no label (for example blue), an optional card measures each outline (or the first
suggested cell) in that channel, in the reporter's sharpest plane for the region and the planes either
side. Gut granules glow in every channel, brightest in blue, while the reporter does not (Pincus et al.
2016; Coburn et al. 2013), so a region at least 3 times the background noise there is flagged as
possibly gut autofluorescence. Flags are reported by genotype in `outputs/unblinded/autofluorescence.csv`,
`log.md` and a paragraph of the Methods draft, and never change a call or leave an image out. It runs
only after unblinding, since a flag per image could hint at a genotype while scoring.

## 1.21.2

**Warnings instead of blocks.** Steps that were refused only to prevent a mistake the researcher
can knowingly accept are now allowed with a warning, recorded in `log.md` and, where it matters, the
Methods draft. **Change anyway...** unlocks the analysis settings after scoring began (the tests then run
and are reported as exploratory, with the reason) and the image folders (a moved folder is followed, a
corrected genotype rule is applied, a new exclusion leaves images out at unblinding and says so). Hidden
repeats that do not fit at the planned distance are placed as far apart as they fit. A practice set may
have fewer than 5 images (up to 500), and a practice result can be recorded before every image is
scored. A package or practice set may go anywhere except wormscore's own folders. Every warning has
one look (an icon, the next step, beside what it is about), and a finished step lists its warnings
instead of leaving them in the log. Blocks that protect blinding, saved scores or data stay.

## 1.21.1

**Flat-field correction that never gets in the way.** Nothing that can be worked around stops
the build any more. An image from a day without slide images uses the nearest day's (the pattern of
light across the field changes little unless the microscope is realigned), an image without a date
uses the average of its microscope's sessions, and slide images taken at other settings stand in when
they are the same size; the light across the field is then corrected, the brightness between sessions
is not. An image with no usable slide images is used as recorded. Each case is a warning in the Build
log, on the card, in `log.md`, in `key.csv` (column `flat_field`) and in the Methods draft. Slide
images for later days can be added at any time, also after scoring began, without changing any coded
image. Unusable slide images (saturated, another size, a slide that did not fill the field) are left
out with the reason instead of stopping. Slide and control images may sit in any folder, also inside
the project; only the project's own image folders are refused.

## 1.21.0

**Flat-field correction.** An optional card corrects the reporter for uneven light across the
field and for brightness drift between imaging sessions, from images of a uniform fluorescent slide
taken at the project's settings in each session (one microscope, settings group and acquisition day),
with optional dark images. Build applies it before anything is shown, suggested or measured, so the
scorer's calls and the numbers rest on the same corrected image. Saturated pixels are found before
correction and still counted. It is set before scoring, recorded in `log.md` (with checksums) and the
Methods draft, and locked from the first score. An image whose session has no slide images stops the
build. Raw files are never changed, and projects without the correction build exactly as before.

## 1.20.0

**Bleed-through check.** In projects with an identity channel, an optional card measures each
marker's light in the other channel from a folder of single-label control images (animals with one
marker, imaged at the project's settings; Bolte and Cordelieres 2006). Identity-only controls matter
most: their light would add to the measured reporter. The result is the least-squares slope over
pixels with signal in every plane, saturated pixels left out, as a percentage (median over images).
It goes to `outputs/bleed_through_*.csv`, `log.md` and the Methods draft. Nothing in the blind set or
the scores is read or changed.

## 1.19.0

**The identity channel in 3D.** In projects with an identity channel, 3D turns and tilts it with
the reporter, and the reporter, identity and both buttons (keys 1, 2 and 3) now work in 3D too. Each
channel keeps its own brightest point along the line of sight. Projects without an identity channel
draw 3D exactly as before.

## 1.18.0

**More 3D.** The 3D view also tilts the stack about the horizontal axis: up and down keys,
dragging up or down, or the new Tilt slider (each -60 to +60 degrees). At no tilt it is drawn exactly
as before. In Projection and 3D, the keys line under the image shows which plane holds the brightest
point under the cursor (only where the image is lit), and a double-click there opens that plane in
Planes. It shows a plane number, never a brightness value. Turn and Tilt fit in the Plane slider's
place, so the toolbar keeps one row on a 1366-pixel screen; each slider's tooltip gives its angle.

## 1.17.0

**Interim totals.** **Interim totals...** on the Score step shows totals of the answers so
far: images scored and not usable (and why), the calls, the share of animals with a defect call,
confidence, the time per image and the time left, and a check for drift (the first half of the
scored images against the second; the codes are in random order, so both halves hold the same mix
of genotypes on average). All images are pooled: nothing by genotype or about hidden repeats, not
even counts, because a count that changes after a save would reveal that image's group. Each look
is recorded in `log.md` as an interim look, and the Methods draft says how many were taken.

## 1.16.0

**Analysis settings in the app, part 2.** The Analysis card in Project settings now sets the
whole analysis plan: **Only the pairs I list** with a test **direction**, and under **More planned
tests** a **second definition of affected** (reported, or also tested), a **genetic interaction**
test (three genotypes, what counts as affected, the model in plain words, the likelihood-ratio
test by default for a new test, and the expected result), the **check of dim calls** and
**comparing across microscopes** with the reason for the Methods. Like part 1 they are recorded in
`log.md` at each save, at Build and at the first score, and locked from the first score. A setting
is written to `config.json` only when it changes, so a recorded plan stays exactly as it was.

## 1.15.0

**Analysis settings in the app, part 1.** Project settings has an **Analysis** card:
**Compare each genotype with** (the reference genotype, or every pair) and **Hidden repeats** (the
share of each genotype's images shown a second time). They are recorded in `log.md` at each save, at
Build and at the first score, and locked from the first score, like the scoring criteria. The
reference genotype is now recorded too: if it is changed in `config.json` after scoring began,
unblinding runs no planned test (projects that scored before 1.15.0 have no record and are checked as
before). A new share of hidden repeats once the blind set is built rebuilds it with new codes (only
before scoring), so the Methods draft never states a share the blind set does not have.

## 1.14.1

text from a practice set made elsewhere is also cleaned of the less common line breaks
(U+0085, U+2028, U+2029) before it goes into `log.md`, so it cannot add a false log entry.

## 1.14.0

**Questions for each side.** An extra question can be asked for each neuron, **for each side
(left and right)** or once per animal. The scoring page shows it once, with a Left and a Right row
of answers (saved as `left_<id>` and `right_<id>`). For a Yes / No or Choice outcome each side is
assessed like a neuron; a scale or count is rolled up over the two sides (the worse side, or the
total over both sides) and tested with the Mann-Whitney test. A new **Counts and position** template
counts marker-positive cells on each side and calls each neuron's cell body in place, anterior or
posterior (after Ch'ng et al. 2003, *Genetics*). A form can now ask no count for each image.
Criteria made before this release record, hash and analyse exactly as before.

## 1.13.1

a practice set from someone else is checked before its result is recorded: its id must be
one wormscore made (it names the file the result is stored in, so an edited set cannot write
outside the project), and its name, dates and answer names go into `log.md` and the Methods draft
as one plain line each (a line break could otherwise add a false log entry).

## 1.13.0

**Practice sets.** In a finished (unblinded) project, **Make a practice set...** copies a
few coded images, taken in turn from each genotype, with your saved answers as the answer key (by
default only images you marked sure). The set has no genotypes, file names or analysis plan. A new
scorer opens it like a second-scorer package and scores it; once every image is scored, the set
shows the agreement with the key for each answer and each image where the answers differ, side by
side. In their own project, **Add a practice result...** computes the agreement again from the
set's files and writes it to `log.md` with the key's checksum (also in the log of the project the
set came from), and the Methods draft says the scorer practised before scoring. Published scales
come with training images and a key (Bijwadia, Morton and Meyer 2021), and agreement checks catch
drift in how categories are judged (Gibson-Corley, Olivier and Meyerholz 2013). Projects without a
practice result report exactly as before. Also: a finished step's "Done in" time no longer keeps
counting, and another project's last step is no longer shown.

## 1.12.3

Project settings cards look like the Scoring criteria cards (the panel colour, a stronger
edge, the same padding), so the boxes to type in stand out from the card instead of matching it.

## 1.12.2

a new or changed project name is kept to 80 characters (it appears in window titles, the
project list and package names, and is the default name of exported criteria, which allow 80); a
longer name already in `config.json` is kept, so older projects still open and save. A long name
takes at most two lines at the top of the project, with the full name as its tooltip.

## 1.12.1

the home screen's project list reads like the recent-projects lists of other desktop apps
(JetBrains, VS Code, GitHub Desktop): one row per project with a folder icon, its name and its
status, a fill on hover and a soft green fill for the open project; the full path is the row's
tooltip and stays under the project's name on the right. The list scrolls on its own when it is
longer than the window. In a long image list the scoring window keeps the open image in view, with
a row of context around it (Next image, the N key, Ctrl+Enter).

## 1.12.0

**A wording pass and a tidier score panel.** Each step card and its "?" help now say
different things instead of the same thing twice; the welcome screen no longer asks a new user about
`config.json`; the Build dialog no longer points to Project settings for the criteria; what you draw
around a cell is an **outline** everywhere (**Outline cells**, **Save outlines**). On the scoring
page each neuron's side list sits on its border line, clear of the answers; sections with a "?" are
the same height as the others; Notes is one line until you type more. The panel now fits without
scrolling when the page is at least 735 pixels tall (for example a maximized window on a 1920 x 1080
laptop at 125%); in a smaller window Save score stays in view and Notes is one short scroll away.
Results are unchanged.

## 1.11.1

**Fixes from a review of 1.9 to 1.11.** Criteria written by hand in `config.json` that the
builder cannot show (a very long rule or question, a reason listed twice) again build and score; in
1.10 and 1.11 they stopped Build and the first score. Answering with a number key now counts as a
change like a click (draft kept, "Unsaved changes", skipped questions hidden, the reason asked for an
unusable image). In projects with an identity channel, 1, 2 and 3 switch the channel again instead of
changing an answer. A criteria file changed after export, or without a checksum, is no longer called
"used as published"; nothing is written until the file is checked. The Methods draft gives the full
checksum when `log.md` does not have it, and mentions the checksum only for imported criteria, so
other projects' Methods read as before. A package says when a reference image is missing. The key
numbers no longer move the answers.

## 1.11.0

**Calmer screens and faster scoring,** after a design review of the whole app. The scoring
criteria have their own page (Project settings keeps the name and image folders), templates are one
menu with their descriptions, definition fields appear when you add one, and a question's analysis
settings appear when you choose to test it. Finished and waiting steps on the home screen are one
line each, and only the next step explains itself. On the scoring page every question is a row of
buttons answered with one click or one number key (the focus then moves to the next question), each
neuron's side sits on its title line, the definitions box starts closed, and the progress, keys and
toolbar read more clearly. Saved scores look exactly as before.

## 1.10.0

**Share criteria between projects and labs.** **Export criteria...** saves the scoring
criteria as one file (questions, answers, definitions and reference images, outcomes, primary
outcome, a name, a version and an optional citation); **Import criteria...** uses it in another
project. The checksum of the criteria in use goes into `log.md` with the form, and the Methods draft
names the source and says whether they were used as published or adapted. This follows REDCap's
data dictionary and Label Studio's labeling configs. **Preset wording from the literature, and a
warning for long scales.** New projects and the
Fate reporter preset call each neuron **on / dim / off**, with dim judged against wild type at the
same settings (Reilly et al. 2022, *PLoS Genet*). Dendrite length is **full length / short**, with
partial optional (Heiman and Shaham 2009, *Cell*; Low et al. 2019, *Development*). Axon guidance
asks around the target ("fails to reach the ventral cord?") and adds a midline-crossing question
(Schmitz, Kinge and Hutter 2007, *PNAS*; Xu et al. 2015, *PLoS Genet*). Each preset names its
source in the app and in its rules. Above 5 levels the builder warns that agreement between scorers
usually drops (Gibson-Corley et al. 2013) and points to the weighted kappa at unblinding. Clearer
greys in the dark theme. Existing projects keep their wording and give identical results.

## 1.9.0

**A definition and a reference image for every answer.** Each call, and each answer of a
Choice or Scale question, can have its own short definition and a reference image (PNG or JPEG)
to compare against, set in the scoring criteria. The scoring page shows them in a **What each answer
means** box above the neurons, with thumbnails that open larger beside the image being scored. Scoring guidelines ask for clear
boundaries between categories (Gibson-Corley, Olivier and Meyerholz 2013, *Vet Pathol*), and
published scales come with a reference image per score (Bijwadia, Morton and Meyer 2021, *JoVE*
62894). Definitions are wording: they can be fixed after the lock without stopping the planned tests.
They are written to `log.md` with each image's SHA-256, go into second-scorer packages, and the
Methods draft says they were shown. The presets now put each call's and level's definition with
it instead of in the rules list. Existing projects give identical results.

## 1.8.1

**Browse images fixes.** Browse stays off when the key is moved away during scoring.
After Unblind, images that are not in the blind set (for example newly scanned ones) are
recorded when viewed, like images viewed before the build. An image is recorded before it is
shown, so a failed write never hides a view. A window of another project no longer keeps
Browse open. In the criteria builder, the count question's answers have their own label.

## 1.8.0

**Interface pass.** A small ? next to each term shows a one-sentence definition. After
Unblind, a Results card shows penetrance per genotype and the planned tests. Build blind set and
Unblind ask first and say what happens (how many images are unscored). The scoring window has a
Next image button, and Save score stays in view on small screens. Browse images moved to the top
of the project and shows a lock while it is off. Neuron 1 is pink instead of red, so it does not
read as an error. Clearer wording throughout, and a home screen that fits narrow windows.

## 1.7.0

**Browse images.** See each image with its file name and genotype in the scoring viewer
(planes, 3D, channels, both display ranges, suggested cells), and measure a region on screen,
without scoring or saving anything. It works before the blind set is built (to check images and
set the scoring criteria) and after unblinding (to look up a code). It is off between Build blind
set and Unblind, and in a second scorer's package. Browsing before the build is recorded in
`log.md` and `outputs/browsed_images.csv`, and the Build log and Methods draft say so. Projects
that never browse give exactly the same results.

## 1.6.3

the home screen appears at once: the project list no longer waits for the image libraries
(about 2 s to 0.2 s here), a skeleton of the screen shows while it loads, and the version and theme
are there from the first frame.

## 1.6.2

the score form is now called **scoring criteria** (the term papers use), and step 2 is **Set
scoring criteria**. ROI, intra-rater and inter-rater named where they apply. Dropdown arrows with room
to breathe. The Neurodegeneration preset's level 4 matches the published scale (Bijwadia et al. 2021).

## 1.6.1

closing the Windows app's own window with unsaved Project settings asks first (WebView2
windows close without asking the page). The home screen re-reads the log only when it changes. README
checked claim by claim against the code.

## 1.6.0

a locked score form can be changed on purpose: **change it anyway...** asks for a reason and
writes the change and the reason to the log, and unblinding lists each change with its reason in the
summary and the Methods draft. Wording fixes keep the planned tests; other changes stop them, as a hand
edit always did. Project settings asks before closing with unsaved changes.

## 1.5.0

**Score form** is its own step on the home screen (Scan data, Score form, Build blind set,
Score, Unblind). The card says what the scorer answers and whether the form is recorded in the log
or locked; checking the form and keeping it as it is also records it. Fixes an occasional "cannot
import name 'measure'" message a few seconds after the app opened. Results are unchanged.

## 1.4.0

scale and count questions. The builder adds **Scale** (levels in order, best first) and
**Count** answers, and any question can be asked for each neuron or once per animal. Each animal gets
one value (worst neuron by default), compared between genotypes with a Mann-Whitney test with an
exact permutation p-value and an effect size; an optional cutoff is also tested with Fisher's exact
test. New presets: Neurodegeneration (0 to 6) and Dendrite length. The builder and scoring page are
roomier. Existing projects give identical results.

## 1.3.0

the score form builder. The **Scoring form** section of Project settings sets the neurons,
calls, extra yes/no and choice questions (defect answers, "can't tell", required, skipped for some
calls), outcomes and a primary outcome, with presets and a live preview of the score panel. The form
is written to the log and locks at the first score; a form changed by hand afterwards stops the tests
at unblinding. Existing projects give identical results.

## 1.2.0

a second scorer. **Make a package** gives another person a random share of each genotype's
coded images, with no key, file names or analysis plan, in a new order; **Import scores** checks the
package came back unchanged and keeps the answers apart from yours. Unblinding adds agreement
between scorers and a list of disagreements; results still use only your answers. Image folders,
genotype rules and leave-out rules are now set in the app (**Project settings**), with a live count
of how the rules sort the scanned images, instead of by editing `config.json`.

## 1.1.0

checks from a review of the measurement and statistics methods. An optional likelihood-ratio
test for genetic interaction (`interaction.test`), which allows for the sampling error of the single
mutants (the binomial test, still the default, finds an interaction too often with small groups);
optional extra agreement statistics for hidden repeats (kappa interval, PABAK, Gwet's AC1) and a
choice of kappa weights; the measured dim check now counts regions it cannot use, and the Methods
text says so when no region was outlined. Clearer docs on suggested-cell spacing and display ranges.

## 1.0.0

presets for fluorescent proteins beyond GFP (mNeonGreen, YFP, mCherry, TagRFP, mScarlet,
mKate2, BFP) and an optional identity channel: a second marker that shows where the cell is. The
scoring page shows the reporter, the identity marker or both (keys 1, 2, 3), and suggested cells are
found on the identity marker and measured on the reporter, so a dim or missing reporter cell is still
found. GFP projects build exactly the same blind set as before.

## 0.14.0

3D view on the scoring page: turn the stack -60 to +60 degrees, same fixed display ranges.

## 0.13.0

reads Nikon `.nd2`, Olympus FluoView `.oib` and `.oif`, and (when turned on for a source)
ImageJ hyperstack TIFF.

## 0.12.0

safer saving (a held Ctrl+Enter, a slow image or a results file open in Excel can no
longer save the wrong answers or half a save), images never mixed up between projects or builds,
clear messages instead of "lost connection" for errors, and finishing touches on both screens.

## 0.11.0

faster start and image switching; keyboard and screen-reader fixes, clearer status and
wording, safer dialogs, and Ctrl+Enter to save and open the next unscored image.

## 0.10.0

hidden repeat images and a scorer-consistency report (percent agreement, Cohen's kappa); a
measured background cutoff, recorded before scoring, for checking calls afterwards.

## 0.9.0

the Windows app has its own windows (one taskbar button, no lost connections).

## 0.8.x

in-app updates (the app offers new versions and can go back to the previous one), drafts of
unsaved scoring answers, reconnecting windows.

## 0.7.x

the app window, a faster and lighter Build blind set, the macOS app.

## 0.5.0 to 0.6.x

an analysis plan locked before scoring, cross-microscope rules, test direction,
source labels, a real-image demo.
