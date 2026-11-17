# Roadmap

What is planned for wormscore, roughly in order. Plans change; the
[Releases page](https://github.com/arjunkai/wormscore/releases) lists what has shipped. Ideas and
requests are welcome as [GitHub issues](https://github.com/arjunkai/wormscore/issues).

Updating never changes a project's results: new analysis options only run if you set them, so a
project scores, measures and reports exactly as before. Screens and buttons may change.

## Planned

### Features

- **Olympus `.oir`.** Planned once its channel information can be checked against enough sample files.
- **Open (non-blind) scoring, under consideration.** An optional project type for pilots or
  phenotypes that cannot be hidden, chosen when the project is created. Every output and the
  Methods draft would state that scoring was not blind to genotype. Blind scoring stays the default.
  A review of other tools and of image.sc (November 2026) found no request for it: every ordinary
  viewer already scores openly, and what people ask for is stronger blinding. It will be built if
  users ask for it.
- **Open a single image, under consideration.** View one file in wormscore's viewer (both display
  ranges, suggested cells, 3D, measurements) without making a project, as Fiji, QuPath and napari
  allow. It would refuse, or warn and record the view, for a file inside a project that is being
  scored blind, so it cannot get around the Browse lock. Fiji already covers quick looks, so it will
  be built if users ask for it.

## Version 1.0.0

1.0.0 is the version used from start to finish on a real blind-scoring project (0.14.0, then
unblinded) plus the identity channel and marker presets. The list above continues as 1.x releases;
new analysis options only run if a project sets them, so its results stay as in 1.0.0.

## Recently shipped

- 1.23.0: **Several identity channels, for NeuroPAL.** `"identity"` may list up to four channels, and
  `{"preset": "NeuroPAL"}` shows its four colours together (TagRFP-T white, mNeptune2.5 red, CyOFP1 green,
  mTagBFP2 blue) in Projection, Planes and 3D, each scaled per image and never measured. The first channel
  is the identity channel as before. New presets: CyOFP1 and mNeptune2.5. The real-worm demo (Sun and Hobert
  2021, CC BY 4.0) is NeuroPAL with a GFP reporter, so it now shows the NeuroPAL colours too, which serves as
  the NeuroPAL example. (The Yemini et al. 2021 NeuroPAL datasets, MIT License, hold no reporter to score.)
  Projects with one identity channel build and show exactly as before.
- 1.22.0: **Gut autofluorescence check.** After unblinding, in projects whose images also recorded a
  channel with no label (for example blue), an optional card measures each outline (or the first
  suggested cell) in that channel, in the reporter's sharpest plane for the region and the planes either
  side. Gut granules glow in every channel, brightest in blue, while the reporter does not (Pincus et al.
  2016; Coburn et al. 2013), so a region at least 3 times the background noise there is flagged as
  possibly gut autofluorescence. Flags are reported by genotype in `outputs/unblinded/autofluorescence.csv`,
  `log.md` and a paragraph of the Methods draft, and never change a call or leave an image out. It runs
  only after unblinding, since a flag per image could hint at a genotype while scoring.
- 1.21.2: **Warnings instead of blocks.** Steps that were refused only to prevent a mistake the researcher
  can knowingly accept are now allowed with a warning, recorded in `log.md` and, where it matters, the
  Methods draft. **Change anyway...** unlocks the analysis settings after scoring began (the tests then run
  and are reported as exploratory, with the reason) and the image folders (a moved folder is followed, a
  corrected genotype rule is applied, a new exclusion leaves images out at unblinding and says so). Hidden
  repeats that do not fit at the planned distance are placed as far apart as they fit. A practice set may
  have fewer than 5 images (up to 500), and a practice result can be recorded before every image is
  scored. A package or practice set may go anywhere except wormscore's own folders. Every warning has
  one look (an icon, the next step, beside what it is about), and a finished step lists its warnings
  instead of leaving them in the log. Blocks that protect blinding, saved scores or data stay.
- 1.21.1: **Flat-field correction that never gets in the way.** Nothing that can be worked around stops
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
- 1.21.0: **Flat-field correction.** An optional card corrects the reporter for uneven light across the
  field and for brightness drift between imaging sessions, from images of a uniform fluorescent slide
  taken at the project's settings in each session (one microscope, settings group and acquisition day),
  with optional dark images. Build applies it before anything is shown, suggested or measured, so the
  scorer's calls and the numbers rest on the same corrected image. Saturated pixels are found before
  correction and still counted. It is set before scoring, recorded in `log.md` (with checksums) and the
  Methods draft, and locked from the first score. An image whose session has no slide images stops the
  build. Raw files are never changed, and projects without the correction build exactly as before.
- 1.20.0: **Bleed-through check.** In projects with an identity channel, an optional card measures each
  marker's light in the other channel from a folder of single-label control images (animals with one
  marker, imaged at the project's settings; Bolte and Cordelieres 2006). Identity-only controls matter
  most: their light would add to the measured reporter. The result is the least-squares slope over
  pixels with signal in every plane, saturated pixels left out, as a percentage (median over images).
  It goes to `outputs/bleed_through_*.csv`, `log.md` and the Methods draft. Nothing in the blind set or
  the scores is read or changed.
- 1.19.0: **The identity channel in 3D.** In projects with an identity channel, 3D turns and tilts it with
  the reporter, and the reporter, identity and both buttons (keys 1, 2 and 3) now work in 3D too. Each
  channel keeps its own brightest point along the line of sight. Projects without an identity channel
  draw 3D exactly as before.
- 1.18.0: **More 3D.** The 3D view also tilts the stack about the horizontal axis: up and down keys,
  dragging up or down, or the new Tilt slider (each -60 to +60 degrees). At no tilt it is drawn exactly
  as before. In Projection and 3D, the keys line under the image shows which plane holds the brightest
  point under the cursor (only where the image is lit), and a double-click there opens that plane in
  Planes. It shows a plane number, never a brightness value. Turn and Tilt fit in the Plane slider's
  place, so the toolbar keeps one row on a 1366-pixel screen; each slider's tooltip gives its angle.
- 1.17.0: **Interim totals.** **Interim totals...** on the Score step shows totals of the answers so
  far: images scored and not usable (and why), the calls, the share of animals with a defect call,
  confidence, the time per image and the time left, and a check for drift (the first half of the
  scored images against the second; the codes are in random order, so both halves hold the same mix
  of genotypes on average). All images are pooled: nothing by genotype or about hidden repeats, not
  even counts, because a count that changes after a save would reveal that image's group. Each look
  is recorded in `log.md` as an interim look, and the Methods draft says how many were taken.
- 1.16.0: **Analysis settings in the app, part 2.** The Analysis card in Project settings now sets the
  whole analysis plan: **Only the pairs I list** with a test **direction**, and under **More planned
  tests** a **second definition of affected** (reported, or also tested), a **genetic interaction**
  test (three genotypes, what counts as affected, the model in plain words, the likelihood-ratio
  test by default for a new test, and the expected result), the **check of dim calls** and
  **comparing across microscopes** with the reason for the Methods. Like part 1 they are recorded in
  `log.md` at each save, at Build and at the first score, and locked from the first score. A setting
  is written to `config.json` only when it changes, so a recorded plan stays exactly as it was.
- 1.15.0: **Analysis settings in the app, part 1.** Project settings has an **Analysis** card:
  **Compare each genotype with** (the reference genotype, or every pair) and **Hidden repeats** (the
  share of each genotype's images shown a second time). They are recorded in `log.md` at each save, at
  Build and at the first score, and locked from the first score, like the scoring criteria. The
  reference genotype is now recorded too: if it is changed in `config.json` after scoring began,
  unblinding runs no planned test (projects that scored before 1.15.0 have no record and are checked as
  before). A new share of hidden repeats once the blind set is built rebuilds it with new codes (only
  before scoring), so the Methods draft never states a share the blind set does not have.
- 1.14.1: text from a practice set made elsewhere is also cleaned of the less common line breaks
  (U+0085, U+2028, U+2029) before it goes into `log.md`, so it cannot add a false log entry.
- 1.14.0: **Questions for each side.** An extra question can be asked for each neuron, **for each side
  (left and right)** or once per animal. The scoring page shows it once, with a Left and a Right row
  of answers (saved as `left_<id>` and `right_<id>`). For a Yes / No or Choice outcome each side is
  assessed like a neuron; a scale or count is rolled up over the two sides (the worse side, or the
  total over both sides) and tested with the Mann-Whitney test. A new **Counts and position** template
  counts marker-positive cells on each side and calls each neuron's cell body in place, anterior or
  posterior (after Ch'ng et al. 2003, *Genetics*). A form can now ask no count for each image.
  Criteria made before this release record, hash and analyse exactly as before.
- 1.13.1: a practice set from someone else is checked before its result is recorded: its id must be
  one wormscore made (it names the file the result is stored in, so an edited set cannot write
  outside the project), and its name, dates and answer names go into `log.md` and the Methods draft
  as one plain line each (a line break could otherwise add a false log entry).
- 1.13.0: **Practice sets.** In a finished (unblinded) project, **Make a practice set...** copies a
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
- 1.12.3: Project settings cards look like the Scoring criteria cards (the panel colour, a stronger
  edge, the same padding), so the boxes to type in stand out from the card instead of matching it.
- 1.12.2: a new or changed project name is kept to 80 characters (it appears in window titles, the
  project list and package names, and is the default name of exported criteria, which allow 80); a
  longer name already in `config.json` is kept, so older projects still open and save. A long name
  takes at most two lines at the top of the project, with the full name as its tooltip.
- 1.12.1: the home screen's project list reads like the recent-projects lists of other desktop apps
  (JetBrains, VS Code, GitHub Desktop): one row per project with a folder icon, its name and its
  status, a fill on hover and a soft green fill for the open project; the full path is the row's
  tooltip and stays under the project's name on the right. The list scrolls on its own when it is
  longer than the window. In a long image list the scoring window keeps the open image in view, with
  a row of context around it (Next image, the N key, Ctrl+Enter).
- 1.12.0: **A wording pass and a tidier score panel.** Each step card and its "?" help now say
  different things instead of the same thing twice; the welcome screen no longer asks a new user about
  `config.json`; the Build dialog no longer points to Project settings for the criteria; what you draw
  around a cell is an **outline** everywhere (**Outline cells**, **Save outlines**). On the scoring
  page each neuron's side list sits on its border line, clear of the answers; sections with a "?" are
  the same height as the others; Notes is one line until you type more. The panel now fits without
  scrolling when the page is at least 735 pixels tall (for example a maximized window on a 1920 x 1080
  laptop at 125%); in a smaller window Save score stays in view and Notes is one short scroll away.
  Results are unchanged.
- 1.11.1: **Fixes from a review of 1.9 to 1.11.** Criteria written by hand in `config.json` that the
  builder cannot show (a very long rule or question, a reason listed twice) again build and score; in
  1.10 and 1.11 they stopped Build and the first score. Answering with a number key now counts as a
  change like a click (draft kept, "Unsaved changes", skipped questions hidden, the reason asked for an
  unusable image). In projects with an identity channel, 1, 2 and 3 switch the channel again instead of
  changing an answer. A criteria file changed after export, or without a checksum, is no longer called
  "used as published"; nothing is written until the file is checked. The Methods draft gives the full
  checksum when `log.md` does not have it, and mentions the checksum only for imported criteria, so
  other projects' Methods read as before. A package says when a reference image is missing. The key
  numbers no longer move the answers.
- 1.11.0: **Calmer screens and faster scoring,** after a design review of the whole app. The scoring
  criteria have their own page (Project settings keeps the name and image folders), templates are one
  menu with their descriptions, definition fields appear when you add one, and a question's analysis
  settings appear when you choose to test it. Finished and waiting steps on the home screen are one
  line each, and only the next step explains itself. On the scoring page every question is a row of
  buttons answered with one click or one number key (the focus then moves to the next question), each
  neuron's side sits on its title line, the definitions box starts closed, and the progress, keys and
  toolbar read more clearly. Saved scores look exactly as before.
- 1.10.0: **Share criteria between projects and labs.** **Export criteria...** saves the scoring
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
- 1.9.0: **A definition and a reference image for every answer.** Each call, and each answer of a
  Choice or Scale question, can have its own short definition and a reference image (PNG or JPEG)
  to compare against, set in the scoring criteria. The scoring page shows them in a **What each answer
  means** box above the neurons, with thumbnails that open larger beside the image being scored. Scoring guidelines ask for clear
  boundaries between categories (Gibson-Corley, Olivier and Meyerholz 2013, *Vet Pathol*), and
  published scales come with a reference image per score (Bijwadia, Morton and Meyer 2021, *JoVE*
  62894). Definitions are wording: they can be fixed after the lock without stopping the planned tests.
  They are written to `log.md` with each image's SHA-256, go into second-scorer packages, and the
  Methods draft says they were shown. The presets now put each call's and level's definition with
  it instead of in the rules list. Existing projects give identical results.
- 1.8.1: **Browse images fixes.** Browse stays off when the key is moved away during scoring.
  After Unblind, images that are not in the blind set (for example newly scanned ones) are
  recorded when viewed, like images viewed before the build. An image is recorded before it is
  shown, so a failed write never hides a view. A window of another project no longer keeps
  Browse open. In the criteria builder, the count question's answers have their own label.
- 1.8.0: **Interface pass.** A small ? next to each term shows a one-sentence definition. After
  Unblind, a Results card shows penetrance per genotype and the planned tests. Build blind set and
  Unblind ask first and say what happens (how many images are unscored). The scoring window has a
  Next image button, and Save score stays in view on small screens. Browse images moved to the top
  of the project and shows a lock while it is off. Neuron 1 is pink instead of red, so it does not
  read as an error. Clearer wording throughout, and a home screen that fits narrow windows.
- 1.7.0: **Browse images.** See each image with its file name and genotype in the scoring viewer
  (planes, 3D, channels, both display ranges, suggested cells), and measure a region on screen,
  without scoring or saving anything. It works before the blind set is built (to check images and
  set the scoring criteria) and after unblinding (to look up a code). It is off between Build blind
  set and Unblind, and in a second scorer's package. Browsing before the build is recorded in
  `log.md` and `outputs/browsed_images.csv`, and the Build log and Methods draft say so. Projects
  that never browse give exactly the same results.
- 1.6.3: the home screen appears at once: the project list no longer waits for the image libraries
  (about 2 s to 0.2 s here), a skeleton of the screen shows while it loads, and the version and theme
  are there from the first frame.
- 1.6.2: the score form is now called **scoring criteria** (the term papers use), and step 2 is **Set
  scoring criteria**. ROI, intra-rater and inter-rater named where they apply. Dropdown arrows with room
  to breathe. The Neurodegeneration preset's level 4 matches the published scale (Bijwadia et al. 2021).
- 1.6.1: closing the Windows app's own window with unsaved Project settings asks first (WebView2
  windows close without asking the page). The home screen re-reads the log only when it changes. README
  checked claim by claim against the code.
- 1.6.0: a locked score form can be changed on purpose: **change it anyway...** asks for a reason and
  writes the change and the reason to the log, and unblinding lists each change with its reason in the
  summary and the Methods draft. Wording fixes keep the planned tests; other changes stop them, as a hand
  edit always did. Project settings asks before closing with unsaved changes.
- 1.5.0: **Score form** is its own step on the home screen (Scan data, Score form, Build blind set,
  Score, Unblind). The card says what the scorer answers and whether the form is recorded in the log
  or locked; checking the form and keeping it as it is also records it. Fixes an occasional "cannot
  import name 'measure'" message a few seconds after the app opened. Results are unchanged.
- 1.4.0: scale and count questions. The builder adds **Scale** (levels in order, best first) and
  **Count** answers, and any question can be asked for each neuron or once per animal. Each animal gets
  one value (worst neuron by default), compared between genotypes with a Mann-Whitney test with an
  exact permutation p-value and an effect size; an optional cutoff is also tested with Fisher's exact
  test. New presets: Neurodegeneration (0 to 6) and Dendrite length. The builder and scoring page are
  roomier. Existing projects give identical results.
- 1.3.0: the score form builder. The **Scoring form** section of Project settings sets the neurons,
  calls, extra yes/no and choice questions (defect answers, "can't tell", required, skipped for some
  calls), outcomes and a primary outcome, with presets and a live preview of the score panel. The form
  is written to the log and locks at the first score; a form changed by hand afterwards stops the tests
  at unblinding. Existing projects give identical results.
- 1.2.0: a second scorer. **Make a package** gives another person a random share of each genotype's
  coded images, with no key, file names or analysis plan, in a new order; **Import scores** checks the
  package came back unchanged and keeps the answers apart from yours. Unblinding adds agreement
  between scorers and a list of disagreements; results still use only your answers. Image folders,
  genotype rules and leave-out rules are now set in the app (**Project settings**), with a live count
  of how the rules sort the scanned images, instead of by editing `config.json`.
- 1.1.0: checks from a review of the measurement and statistics methods. An optional likelihood-ratio
  test for genetic interaction (`interaction.test`), which allows for the sampling error of the single
  mutants (the binomial test, still the default, finds an interaction too often with small groups);
  optional extra agreement statistics for hidden repeats (kappa interval, PABAK, Gwet's AC1) and a
  choice of kappa weights; the measured dim check now counts regions it cannot use, and the Methods
  text says so when no region was outlined. Clearer docs on suggested-cell spacing and display ranges.
- 1.0.0: presets for fluorescent proteins beyond GFP (mNeonGreen, YFP, mCherry, TagRFP, mScarlet,
  mKate2, BFP) and an optional identity channel: a second marker that shows where the cell is. The
  scoring page shows the reporter, the identity marker or both (keys 1, 2, 3), and suggested cells are
  found on the identity marker and measured on the reporter, so a dim or missing reporter cell is still
  found. GFP projects build exactly the same blind set as before.
- 0.14.0: 3D view on the scoring page: turn the stack -60 to +60 degrees, same fixed display ranges.

- 0.13.0: reads Nikon `.nd2`, Olympus FluoView `.oib` and `.oif`, and (when turned on for a source)
  ImageJ hyperstack TIFF.
- 0.12.0: safer saving (a held Ctrl+Enter, a slow image or a results file open in Excel can no
  longer save the wrong answers or half a save), images never mixed up between projects or builds,
  clear messages instead of "lost connection" for errors, and finishing touches on both screens.
- 0.11.0: faster start and image switching; keyboard and screen-reader fixes, clearer status and
  wording, safer dialogs, and Ctrl+Enter to save and open the next unscored image.
- 0.10.0: hidden repeat images and a scorer-consistency report (percent agreement, Cohen's kappa); a
  measured background cutoff, recorded before scoring, for checking calls afterwards.
- 0.9.0: the Windows app has its own windows (one taskbar button, no lost connections).
- 0.8.x: in-app updates (the app offers new versions and can go back to the previous one), drafts of
  unsaved scoring answers, reconnecting windows.
- 0.7.x: the app window, a faster and lighter Build blind set, the macOS app.
- 0.5.0 to 0.6.x: an analysis plan locked before scoring, cross-microscope rules, test direction,
  source labels, a real-image demo.
