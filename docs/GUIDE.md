# wormscore user guide

Everything the app does, step by step. For a first look, the [README](../README.md) is shorter. How each
number is computed, and what to write in a paper, is in [METHODS.md](METHODS.md).

- [Using it on your own images](#using-it-on-your-own-images): the five steps, and the optional checks
- [Markers and identity channels](#markers-and-identity-channels)
- [Scoring criteria](#scoring-criteria)
- [Results](#results)
- [Project folder](#project-folder)
- [Updates](#updates)
- [The demo, file formats and other ways to start](#the-demo-file-formats-and-other-ways-to-start)

## Using it on your own images

The app's home screen walks you through five steps in order, with progress and time left. Every step
also has a command (in brackets) if you prefer the command line or want to script it. A small **?** next
to a term (blind set, ROI, settings group, primary outcome and others) shows a one-sentence definition:
click it or press Enter, and Escape closes it.

**Before you start**

- **Copy your data** to a local folder (recommended): `wormscore copy D:\from\drive C:\data\my_copy`
  copies every file, checks each one by SHA-256 and makes the copies read-only.
- **Make a project:** **New project...** opens **Project settings**. Choose each folder of images and
  write the genotype rules (a pattern such as `*ctrl*` and the genotype it means). The page counts how
  the rules sort the scanned images, so a typo shows before you build anything. Images can be left out
  by pattern there too. Images do not need converting: wormscore reads the microscope files as they are.
  (`wormscore init C:\projects\my_project`, then edit `config.json`.) The markers and the main analysis
  settings are in Project settings too; the rest are in `config.json`; see
  [`examples/config_example.json`](https://github.com/arjunkai/wormscore/blob/main/examples/config_example.json).

**The five steps**

1. **Scan data** (`wormscore inventory my_project`): lists every file (`outputs/manifest.csv`: raw
   image, duplicate, derived projection, reference, hidden system file) and every image with its
   metadata and channel identity (`outputs/inventory.csv`). Check the flags column. A file that cannot be
   read (damaged, cut short, or open in the microscope software) is listed with the reason and left out;
   it never stops the scan or the build.
2. **Set scoring criteria:** choose what the scorer answers for each image (see
   [Scoring criteria](#scoring-criteria) below). **Set criteria...** opens them; pressing Save records
   them in `log.md`, even if you keep the defaults. They lock at the first score. On the command line
   this step is optional: the criteria in `config.json` (the defaults unless you changed them) are
   recorded when you build the blind set.
3. **Build blind set** (`wormscore prepare my_project`): applies the inclusion rules, gives every image a
   random code, finds and measures suggested cells, and writes the sealed key. The first build asks
   before it starts, and offers to set the criteria first if you have not saved them. Excluded images and the
   reasons are in `outputs/excluded.csv`. To leave out images by name (for example a plate with the
   wrong reporter), add `"exclude": [{"match": "*pattern*", "reason": "why"}]` to that source. Images
   taken after scoring started can be added with **Add new images** (`wormscore prepare my_project
   --add-new-images`; scan first): existing codes stay the same and new images get codes after the last
   one. Once scoring has started, the folders and rules that built the blind set are locked, but you can
   still add a folder for new images. **Change anyway...** unlocks them with a reason (written to `log.md`):
   a moved or renamed folder is followed by the coded images, a corrected genotype rule is applied to them
   (listed in `key/changes_after_scoring.csv`), and a new exclusion rule leaves matching images out at
   unblinding, which the summary and the Methods draft say.
4. **Score** (`wormscore app my_project`): for each image, answer any yellow questions, score each
   neuron, save, then outline each cell body (or accept a suggestion) and save the outlines (**Save outlines**).
   Outlining is optional. Every question is a row of buttons, so the whole image can be scored from
   the keyboard: Tab to a group, press its number (1 for the first answer) and the focus moves to the
   next group; the numbers show on the group that has the focus. In a project with an identity
   channel, 1, 2 and 3 keep switching the channel, so answer with the mouse or the arrow keys there.
   **Ctrl+Enter** saves and opens the
   next unscored image. **Next image** (or the N key) moves on without saving, and **Save score** stays
   in view at the bottom of the panel on small screens. Notes is one line until you type more. Answers you have
   not saved yet are kept as a draft (`scoring_drafts/`, never counted as scores), so a crash or restart
   loses nothing: open the image again and click Save. If `scores.json` is lost while `scores.csv` is
   still there, the scores are read from `scores.csv`. If the connection to wormscore is lost, the
   window shows "Reconnecting..." and reconnects by itself; if wormscore is not running it says so, with
   a Start wormscore button (Windows app).
   **Interim totals...** on the Score step shows totals of your answers so far: images scored and not
   usable (and why), the calls, the share of animals with a defect call, confidence, and the time per
   image and time left. It also compares the first and second half of the images you scored: the codes
   are in random order, so a large difference suggests the criteria are being applied differently over
   time. All images are pooled. Nothing is shown by genotype or about hidden repeats, not even counts,
   since a count that changes after a save would reveal that image's genotype. Each look is recorded in
   `log.md`, and the Methods draft says how many were taken.
5. **Unblind** (`wormscore unblind my_project --i-have-finished-scoring`), only when scoring is
   finished: reveals the genotypes and writes the results tables and a draft Methods paragraph (see
   [Results](#results) below). A score saved or changed after the first unblinding is no longer blind: the
   summary, `merged.csv` and the Methods draft mark it. The confirmation says how many images are scored and that unscored ones
   are left out. Afterwards a **Results** card at the top of the project shows the penetrance per
   genotype and the planned Fisher tests, with **Open results**.

**Bleed-through check** (projects with an identity channel). Image a few animals that carry one marker
only, at the project's settings, and choose their folder on the **Bleed-through check** card. wormscore
measures how much of that marker's light lands in the other channel, as a percentage, and records it in
`log.md` and the Methods draft. Identity-only controls matter most, since that light would add to the
measured reporter; reporter-only controls show whether bright cells look brighter in the identity
channel, which would make them easier to find. With several identity channels (NeuroPAL), the
identity-only controls also fit the reporter channel to all of them together, so each one's share is
separate from the others' (for example CyOFP1, excited at 488 nm like GFP).

**Gut autofluorescence check** (optional, after unblinding, for images that also recorded a channel with
no label, such as blue). Gut granules glow in every channel; the reporter does not. **Check outlines** on
the card measures each outline in that channel and flags those that are bright there too, by genotype, in
`outputs/unblinded/autofluorescence.csv`, `log.md` and the Methods draft. **Browse images** then marks
flagged codes and names their outlines. Flags never change a call.

**Flat-field correction** (optional, before scoring). In each imaging session, image a uniform
fluorescent slide at the project's settings (and, if you can, a few dark images with the light off).
Choose the folder on the **Flat-field correction** card. Build then evens out the reporter's light
across the field and between sessions before anything is shown or measured, so a cell at the edge of the
field is not judged dimmer than one in the middle. It is recorded in `log.md` and the Methods draft; a
blind set already built is rebuilt with the same codes. A day without slide images uses the nearest
day's, with a warning (the build never stops for it), and slide images for later days can be added with
**Sessions... > Add slide images** at any time, also after scoring began, without changing images
already coded: once scoring has begun they are used only for images you add later, and a rebuild keeps
every coded image as it was scored.

**Looking at images: Browse images** (`wormscore browse my_project`). After scanning, **Browse images**
(next to Open folder and Project settings, at the top of the project) opens the scoring viewer with each image's file name and genotype, for checking
images and setting the scoring criteria. It has the same views as scoring (planes, 3D, channels, both
display ranges, suggested cells), and you can outline a cell to see its measurements on screen. Nothing
is scored or saved. Before the blind set exists, Enhanced is set from each image alone, so compare
brightness between images with Standard. Each image you open before the build is recorded in `log.md`
and `outputs/browsed_images.csv`, and the Build log and the Methods draft say how many images were seen
(ARRIVE 2.0 item 5 asks who knew the groups at each stage). Browse is off from Build blind set until
Unblind (the button shows a lock and says why), and in a second scorer's package. It also stays off if
the key folder is moved away during scoring (some labs keep the key elsewhere to keep it sealed); put
the key back before you build again or unblind, since a build without it is refused once scores are saved. After
unblinding it opens again, to look up a code (for example W0010) with its genotype, file and saved score.

**Optional: a second scorer** (inter-rater reliability). After building the blind set, use **Make a package...** in the
**Second scorer** card on the home page (`wormscore export-scorer my_project D:\for_sam --scorer "Sam K"
--percent 30`). Choose a name and a share of each genotype's images (1 to 100%, at least one image per
genotype, picked at random). The package is a folder with those coded images in a new random order
(plus their hidden repeats, if you turned repeats on), the scoring criteria and the display settings. It has no key, no file names, no analysis plan and no checksum list. Zip it and send it.
The second scorer opens the folder in wormscore, which then shows only **Score**, and sends the folder
back when finished. **Import scores...** (`wormscore import-scorer my_project D:\from_sam`) checks that
it is the package made from this project with the same images and scoring criteria, and stores the answers in
`scorers/`. Your own scores are never changed. At unblinding, `between_scorers.csv` gives percent
agreement and Cohen's kappa for each answer, `scorer_disagreements.csv` lists each image where the
answers differ (codes only), and the Methods draft gets a sentence about it. All results tables still
use only your answers.

**Optional: a practice set** (training against an answer key). Before scoring for real, a new
scorer can practise on images someone has already scored. In a finished (unblinded) project, **Make a
practice set...** in the **Practice set** card (`wormscore make-practice my_project D:\practice --n 20`)
copies that many coded images, taken in turn from each genotype, with your saved answers as the
answer key (only images you marked sure, unless you untick it or add `--any-confidence`). The set has
no genotypes, file names or analysis plan; the key is in `practice_key.json`. The new scorer opens the
folder in wormscore and scores every image; then the set shows the agreement with the key for each
answer (percent agreement and Cohen's kappa) and lists each image where the answers differ, with both
answers. In their own project, before scoring, **Add a practice result...** (`wormscore add-practice
my_project D:\practice`) checks the set, computes the agreement again from its files and writes it to
`log.md` with the key's SHA-256 (the same value is in the log of the project the set was made from),
and the Methods draft says the scorer practised. Projects without a practice result report exactly as
before.

## Markers and identity channels

**Not GFP?** Choose the reporter under **Markers** in **Project settings** (or set
`"marker": {"preset": "mCherry"}`; also mNeonGreen, YFP, TagRFP, mScarlet, mKate2, BFP). **Have a second
marker that labels the cell,** such as a pan-neuronal mCherry? Choose it as the identity channel there (or
add `"identity": {"preset": "mCherry"}`): the scoring page can then show the reporter, the identity
marker or both (keys 1, 2, 3), and suggested cells are found on the identity marker and the reporter is
measured there, so a dim or missing reporter cell is still found. **NeuroPAL?** Choose NeuroPAL as the
identity channel (`"identity": {"preset": "NeuroPAL"}`): its four colours (TagRFP-T white, mNeptune2.5 red, CyOFP1 green,
mTagBFP2 blue) are shown together in 2D and 3D, each scaled per image and never measured; or list up to four
identity channels yourself, `"identity": [{"preset": "mCherry"}, {"preset": "BFP"}]`, the first one finding
the cells. GFP and CyOFP1 are both green, so use keys 1 and 2 to see each on its own. Markers are found
at Scan data, so after a change run Scan data again and rebuild; once scoring has started they are locked,
and **Change anyway...** records the change and your reason in `log.md` (not while a second-scorer
package exists). See
[METHODS.md](METHODS.md) sections 2 and 6.

## Scoring criteria

**Edit criteria...** on step 2 opens the scoring criteria on their own page (Project settings has the
project name and image folders), with a live preview of the score panel. (Older versions and `log.md`
call this the score form.)

- **Start from a template...:** **Fate reporter** (each neuron on / dim / off, with dim judged against wild
  type at the same settings; what a new project starts with), **Axon guidance** (yes/no questions about
  the axon, phrased around its target, and whether it crosses the ventral midline),
  **Neurodegeneration** (each dendrite scored 0 to 6), **Dendrite length** (full length / short; add a
  partial level if you need one), **Counts and position** (marker-positive cells counted on the left
  and on the right side, and each neuron's cell body in place, anterior or posterior) or **Blank**.
  Each preset follows published scoring and names its
  source in the list and in its rules. Projects made before 1.10.0 keep normal / dim / missing.
- **Long scales:** above 5 levels the builder warns that scorers usually agree less, and points to the
  weighted kappa that unblinding reports when there are hidden repeats or a second scorer.
- **Neurons and calls:** name the neurons and the calls, and tick which calls count as a defect.
- **What each answer means:** each call, and each answer of a Choice or Scale question, can have a
  short definition and a **reference image** (PNG or JPEG) to compare against, for example what "dim"
  looks like. The scoring page shows them in a **What each answer means** box above the neurons (click
  a thumbnail to see it larger, beside the image being scored), and a definition is also the answer's tooltip. Take reference
  images from outside the blind set, such as a pilot or a published figure. Images are copied into the
  project's `criteria/` folder and go into second-scorer packages; the definitions and each image's
  SHA-256 are written to `log.md` with the criteria, and the Methods draft says they were shown.
  The general **rules** list stays for instructions that are not about one answer.
- **Extra questions,** each asked **for each neuron**, **for each side** (left and right, for example
  the cells counted on each side) or **once per animal**, with an optional "can't tell" answer (never
  counted as an answer: that neuron, side or animal is left out), whether it is required, and calls
  for which it is skipped (for example "not seen"; only neurons have a call). A question for each side
  is shown once with a Left and a Right row of answers, saved as `left_<id>` and `right_<id>`; for a
  Yes / No or Choice outcome each side is assessed like a neuron. Four answer types:
  - **Yes / No** and **Choice:** tick the answers that count as a defect. Questions with the same
    outcome name are combined (the animal has that defect if any answer is a defect), and each outcome
    gets Fisher's exact tests at unblinding.
  - **Scale:** levels in order, best first and worst last (for example 0 to 6, or full length /
    partial / short).
  - **Count:** whole numbers from a lowest to a highest, the last optionally "that many or more" (for
    example 0 to 5+).
- **How scales and counts are tested:** a scale or count with an outcome name is tested on its own.
  Each animal gets one value: for a question asked for each neuron, the **worst neuron** (default) or
  the mean of the assessed neurons for a scale, and the total (default) or the mean per neuron for a
  count; a question asked for each side is rolled up the same way over the two sides (the worse side,
  or the total over both sides). Genotypes are compared with the **Mann-Whitney test** on those values, with a permutation
  p-value (correct with the many ties short scales have): exact when there are up to 100,000
  rearrangements, otherwise from 100,000 random ones with a fixed seed. An optional **cutoff** (for
  example "4 or worse") also gives the share of animals at or past it, tested with Fisher's exact test.
- **Primary outcome:** name one; the results and Methods then call every other test secondary.
- **Count for each image:** under **Per-image questions and general rules**, untick **Ask a count for
  each image** when cells are counted another way (the Counts and position template counts each side
  instead). The `cells_seen` column then stays empty.

**Share criteria between projects and labs.** **Export criteria...** saves the criteria, with their
definitions and reference images, as one `.wormscore-criteria.json` file with a name, an optional
version and an optional citation. **Import criteria...** in another project reads it (nothing is saved
until you press Save), and says so if the file was changed after it was exported or has no checksum.
`log.md` records a SHA-256 checksum of the criteria in use, the same in every project for the same
criteria. For imported criteria the Methods draft names the source and says whether they were used as
published or adapted; for criteria made in the project it reads as before.

**The criteria lock at the first score** (or once a second-scorer package exists), so the questions
cannot change while you score. Every save is written to `log.md`. If you really must change them
afterwards, **change them anyway...** on the locked criteria asks for a reason and writes the change and the
reason to `log.md`; unblinding lists each change with its reason in the summary and the Methods draft.
Fixing wording (question text, rules, an answer's definition or reference image) keeps the planned tests. Any other change, in the app or by
editing `config.json` by hand, means unblinding runs no planned test and says why (descriptive tables
are still written, and changing the criteria back runs the tests again). It is not offered once a
second-scorer package exists. Leaving the criteria or Project settings with unsaved changes asks
whether to save them.

## Results

Unblinding writes `outputs/unblinded/merged.csv` (scores + numbers + key, one row per image), a
summary, and results tables: `per_neuron.csv` (one row per animal and neuron, for plotting),
`penetrance.csv` (animals with 0, 1, 2 ... affected neurons) and `fisher_tests.csv` (Fisher's exact
within each microscope, Bonferroni-corrected). To test against a control, choose it under **Analysis**
in Project settings (**Compare each genotype with**; `unblinding.reference_genotype` in `config.json`).

`methods_text.md` is a draft Methods paragraph on blinding, measurement and statistics; fill in the
`blinding` section of `config.json` (who coded, who scored) or edit the [brackets] it leaves.

Optional, off unless set. The outcomes come from the scoring criteria; the rest is under **Analysis**
in Project settings (or the `unblinding` section of `config.json`):

- `question_outcomes` turns the per-neuron questions into the same penetrance and Fisher tables (for
  example "process defect" when a dendrite question is answered "no").
- `rank_outcomes` (set by the builder for scale and count questions) writes `rank_outcomes.csv` (per
  genotype: animals, animals left out and why, median and quartiles, % at each level or the mean, the
  share at the cutoff) and `rank_outcome_tests.csv` (Mann-Whitney with the effect size: the chance a
  genotype_a animal scores worse than a genotype_b animal, and Cliff's delta).
- **Genetic interaction** (`interaction`) tests a double mutant against the value two single mutants
  predict (independent effects, the product model, by default; also additive, or same pathway). The
  card starts a new test with the likelihood-ratio test, which allows for the single mutants' own
  sampling error. The binomial test (the default in `config.json`, kept for older projects) treats
  their rates as exact and so finds an interaction too often with small groups.
- **Only the pairs I list** (`comparisons`) tests only those pairs, with an optional **direction**
  (`alternative`: one-sided tests need listed pairs); Bonferroni counts only them.
- **Second definition of affected** (`secondary_defect_calls`, descriptive within one microscope unless
  **Test it too** is ticked), **Check of dim calls** (`dim_cutoff_sd`, a measured background cutoff for
  checking calls against numbers afterwards) and **Compare across microscopes** for chosen results
  (`allow_cross_microscope`, with warnings and the reason in the Methods). Labelled sources stay in
  `config.json`.
Hidden repeats measure intra-rater reliability (how consistently one scorer scores the same image).
**Hidden repeats** under **Analysis** in Project settings (`"reliability": {"repeat_fraction": 0.1}` in
`config.json`) shows that share of images a second time under new codes. It decides the codes, so changing
it once the blind set is built rebuilds the blind set with new codes (only before scoring);
unblinding then reports how consistently you scored them (`reliability.csv`).

All of these are recorded as an analysis plan before scoring and locked once scoring starts.
**Change anyway...** on the locked card asks for a reason: the tests then run on the new settings and are
marked as not set before scoring, and the summary and the Methods draft call them exploratory and give the
reason. The share of hidden repeats stays as it is (it decides the codes). See
[docs/METHODS.md](METHODS.md#8-unblinding).

## Project folder

| Path | Contents | Open while scoring? |
|---|---|---|
| `config.json` | every parameter | yes |
| `log.md` | every run: parameters, software versions, counts | yes |
| `outputs/manifest.csv`, `inventory.csv`, `excluded.csv` | what was found and what was left out | yes |
| `outputs/browsed_images.csv` | images viewed with Browse images before the build (only if you browsed) | yes |
| `outputs/auto_measurements.csv` | numbers for suggested spots | avoid until done |
| `blind/` | images for scoring, named by code | used by the app |
| `scoring/` | your scores, regions and their measurements | scores yes; avoid `roi_measurements.csv` |
| `scorers/` | second scorers' packages (which images, checksums) and their returned scores | not needed |
| `key/` | code to file, genotype, settings | **no, not until scoring is finished** |

## Updates

wormscore is young and new versions come out often. Updating does not change your results: new
analysis options (such as a new test or hidden repeat images) only run if you set them, so an
existing project scores, measures and reports exactly as before. Screens and buttons may change. Every
version stays downloadable from the [Releases page](https://github.com/arjunkai/wormscore/releases),
so an analysis can be rerun on the version it used. For a study, keep one version from building the blind
set to unblinding (the app reminds you), and cite that version.

The downloaded app (Windows and Mac) checks for a new version when it starts. If there is one, a
window shows what is new with **Update and restart**, **Not now** and **Skip this version**.
Update downloads the zip for your computer, checks it against the SHA-256 checksum GitHub lists for
it, runs the new copy once to confirm its version, and swaps it in after the app closes. The previous
version is kept: **Settings > Go back to ...** swaps it back. If the swap cannot be done (for example a
file in the app folder is open in another program), nothing is changed: the app and the kept copy
stay as they were, and you can try again. On a Mac whose app is on another disk than your home folder
(an external disk, for example), the kept copy is in a hidden folder next to the app
(`.wormscore-previous`), so the swap never copies an app between disks. Projects, codes and scores are never touched. From the command line: `wormscore update`, `wormscore update --install`,
`wormscore update --rollback`.

If a project has a blind set built but is not unblinded yet, the update window warns that updating
would put two wormscore versions in that project's Methods: **Not now** becomes the default, and
**Update anyway** works only after you tick "I understand the Methods would cite two versions".
Update before you build a blind set or after you unblind.

Copies installed with pip are told to run `pip install -U wormscore` instead. Turn the check off in
Settings (the gear at the top). Versions before 0.8.0 need one manual download.

## The demo, file formats and other ways to start

```
wormscore demo my_demo
wormscore inventory my_demo
wormscore prepare my_demo
wormscore app my_demo
```

`demo` writes 20 simulated worm heads, 10 per genotype (`--n` changes that): controls with two
normal cells, and mutants with cells that are normal, dim or missing at random, and a `demo_truth.csv` you can check your calls against later.
`wormscore demo my_demo --identity` adds an mCherry identity channel that marks every cell.
In the app window, use **Try a demo...**, then the steps in order: **Scan data > Set scoring criteria > Build blind set > Score > Unblind**.

**Real worms:** `wormscore demo my_demo --real` (or **Real worm images** in the app's demo dialog) downloads four
L1 heads with an endogenous GFP reporter, about 150 MB, from
[Sun and Hobert 2021](https://doi.org/10.1038/s41586-021-04071-4)
([Zenodo 5602160](https://doi.org/10.5281/zenodo.5602160), CC BY 4.0). Each file is checked against its
MD5 from the Zenodo record and cached in `%LOCALAPPDATA%\wormscore\demo_cache` (`~/.cache` elsewhere).
The two reporters stand in for two genotypes; both are normal animals, so this is for practice. The
animals carry NeuroPAL, so since 1.23.0 the demo also shows its four colours (keys 1, 2, 3 switch between
GFP, NeuroPAL and both).
The images are not part of this repository.

Supported raw formats: Leica `.lif`, Zeiss `.czi`, OME-TIFF (including Micro-Manager style files with
a `.companion.ome`), Nikon `.nd2`, Olympus FluoView `.oib` and `.oif`, Olympus `.oir` (FV3000, FV4000), and ImageJ hyperstack TIFF (only
where you turn it on, because ImageJ files do not record which channel is which). Channel identity
(marker vs brightfield) is read from metadata, never guessed.

In a browser, score in a normal window. Safari Private windows, Brave, and Firefox with fingerprinting
protection change the image data a page reads back, which would show as speckle in the images. The scoring
page checks for this when it opens and shows a warning above the image if the window does it.

- The Windows app draws its own window with WebView2, the web engine built into Windows 10 and 11. The
  older launcher window is still available: `wormscore.exe --classic`.
- The newest code from GitHub: `pip install git+https://github.com/arjunkai/wormscore.git`. pip installs a
  `wormscore` command and `wormscore-launcher`, which opens the same app window (in your default browser
  on macOS and Linux).
- In the viewer you can page through z-planes, turn and tilt the stack in 3D, see which plane the
  brightest point under the cursor is in (double-click opens it), and overlay brightfield.
