# Tools

Helpers for checking wormscore. They are not part of the app or of the test suite.

## bioformats_referee.py

Compares what wormscore reads from microscope files with what [Bio-Formats](https://www.openmicroscopy.org/bio-formats/)
reads from the same files: image size, bit depth, pixel size, z-step, channel names and, for the reporter
channel wormscore picked, detector gain or voltage, laser attenuation and exposure. It prints every
difference. No pixels are read and nothing is written next to the files.

It needs, once:

- Java on `PATH`: version 8 or later for Bio-Formats 8, 11 or later for Bio-Formats 9
  (free: [Temurin](https://adoptium.net)).
- The Bio-Formats command-line tools: download `bftools.zip` from the `artifacts` folder of a release at
  <https://downloads.openmicroscopy.org/bio-formats/> and unzip it anywhere outside this repository.

Run it from the repository folder:

```
python -I -X utf8 tools/bioformats_referee.py FOLDER [FOLDER ...] --bftools PATH\TO\bftools --out report.txt
```

- `FOLDER`: folders with real `.lif`, `.czi`, `.nd2`, `.oib`, `.oif`, `.oir` or `.ome.tif` files, searched in depth.
- `--bftools`: the unzipped folder (or set `BFTOOLS`, or put it on `PATH`). The script says what to install
  if Java or Bio-Formats is missing.
- `--src`: the wormscore source to check. Default: this checkout's `src`. (`-I` makes Python ignore
  `PYTHONPATH`, so nothing lying next to the sample files is imported.)
- `--project`: a project folder whose `config.json` sets the markers. Default: a GFP reporter. Gain, laser
  and exposure are compared for the reporter channel only, so use a project whose markers fit the files;
  for images without a reporter the report lists Bio-Formats' values per channel.
- `--cache`: where Bio-Formats' output is kept, so a second run takes seconds. Default:
  `~/.cache/wormscore-referee`. A file is read again when its size or date changes.

Reading the report: each image has one line with wormscore's reading, then `DIFF` lines (the two disagree,
or Bio-Formats reads a brightness setting whose number is not in wormscore's settings text) and `note`
lines (Bio-Formats has nothing to compare with, or what the file's own metadata says). The summary counts
differences per format.

Bio-Formats is a second opinion, not the truth. Known cases where it is the one that is off: Zeiss camera
files written as 12-bit are reported as 16 significant bits, and Leica channel names are detector or dye
names where wormscore lists the colour table. Check a difference against the microscope software.

Olympus `.oir`: Bio-Formats does not write the laser's share into its OME-XML, so the script takes it from
the file's own metadata as Bio-Formats lists it (`Laser LD488 transmissivity`). Bio-Formats keeps one value
per laser, the last phase's; for a scan with several phases the script checks wormscore's value against
every laser entry of the file and prints a note. It counts each wavelength of a lambda scan as a channel
(a note), and it leaves out a last plane that was not finished, as wormscore does. A gain of 1 and a
voltage of 0 are left out of wormscore's settings on purpose (a note).

Micro-Manager OME-TIFF: the metadata is in `<name>.companion.ome` next to the TIFF. Given only the TIFF,
Bio-Formats reads it as a plain TIFF (each page a time point, no channel names), so the script gives it
the companion file.

Pixels are not compared by this script. For `.oir` they were compared once with `bfconvert` (every
channel and plane of 11 files; the reading is the same), with a script kept outside the repository.

Real images are never committed to this repository; keep sample files and the cache outside it.

## demo_video.py

Records the one-minute screen recording on wormscore.org: the real-worm demo project taken from Scan data
to Unblind, with a drawn cursor and a caption for each step. Re-run it when the screens change.

```
python tools/demo_video.py OUT_DIR
```

`OUT_DIR` must not hold a project yet. It writes `demo.mp4` (1920x1080, no sound) and `demo-poster.webp`;
copy both into the site's `img/` folder and change the `?v=` after both names in its `index.html` to the
version shown, so browsers fetch the new recording instead of a cached one. It runs the app's home server inside the script with its own settings
file, so your projects list is not touched. Needs `pip install playwright pillow`,
`python -m playwright install chromium` and ffmpeg on `PATH`. The first run downloads the demo images
(about 230 MB, cached).
