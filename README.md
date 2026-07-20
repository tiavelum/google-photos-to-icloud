# Google Photos → Apple Photos Migration

Migrates a Google Photos library into Apple Photos **preserving albums and
metadata** (dates, GPS, descriptions), using Google Takeout as the export
path — Google offers no API for full-library access, and the official
Apple/Google direct transfer drops all album structure.

Works with English and German Takeout exports.

## Prerequisites

- macOS with Photos.app set up (opened at least once), iCloud Photos
  enabled with enough free storage
- [Homebrew](https://brew.sh)
- Python 3.10+ — Apple's bundled `python3` is too old for osxphotos:

  ```bash
  brew install python
  ```

- Only if you use `--fix-album-dates` (see below):

  ```bash
  brew install exiftool
  ```

## Quick start

```bash
./migrate.sh
```

The orchestrator detects where you are in the process and guides you through
the manual steps (Takeout export, iCloud checks) at the right moment. Run it
repeatedly; each stage is idempotent and safe to rerun.

If your browser auto-extracted the Takeout zips (Safari does this by
default), run the stages directly instead — pass the folder that contains
the extracted `Takeout*/` folders as `--source`:

```bash
python3 prepare_takeout.py --source ~/Downloads --output ./output [--fix-album-dates]
./import_to_photos.sh
```

## What it does

| Stage | Script | What happens |
|---|---|---|
| 1 (manual) | guided by `migrate.sh` | You create a Google Takeout export (.zip, 50 GB parts) of Google Photos and download all parts |
| 2 (auto) | `prepare_takeout.py` | Builds a clean, import-ready staging folder (details below) |
| 3 (auto) | `import_to_photos.sh` | Imports into Apple Photos, album by album (details below) |
| 4 (manual) | `make_review_albums.py` (optional) | Builds review albums in Photos for date-adjusted photos; spot-check albums, dates, locations; let iCloud sync finish |

### Stage 2 — prepare (`prepare_takeout.py`, pure Python stdlib)

- Extracts all `Takeout*.zip` parts (idempotent, `.done_*` markers), or works
  directly on already-extracted `Takeout*/` folders
- Separates user albums from "Photos from YYYY" / "Fotos von YYYY" year
  folders; skips Trash/Papierkorb and failed-video folders
- **Prefers the Google-edited version**: when both `X.jpg` and `X-edited.jpg`
  (German: `X-bearbeitet.jpg`) exist, only the edited content is kept, stored
  under the original filename so its metadata sidecar still pairs up. The
  untouched originals remain in the Takeout folders and are not imported
- Content-hash dedupes photos that exist in both an album and a year folder
  (album copy wins; each photo imports exactly once, album membership intact)
- Pairs each photo with its Google JSON sidecar (handles truncated
  `.supplemental-metadata` names and `X.jpg(1).json` renames) and normalizes
  sidecars to the key set osxphotos requires
- Sets file mtimes from `photoTakenTime` so EXIF-less files (scans,
  screenshots) land correctly on the timeline
- Writes `output/prepare_report.txt`

Output layout:

```
output/PhotosReady/
  Albums/<Album Name>/   photos + sidecars per album
  Library/               everything not in an album, deduplicated
```

### Stage 3 — import (`import_to_photos.sh`)

- One-time: creates a venv at `~/.photos-migration-venv` and installs
  [osxphotos](https://github.com/RhetTbull/osxphotos)
- Imports album folders **one osxphotos call per album** (album = folder
  name), then the library in batches of 500, with a pause between batches —
  a single huge import can make Photos.app hang
- If a batch fails, Photos.app is automatically restarted and the batch
  retried once; after 50 errors a batch aborts rather than spraying failures
- Duplicates are skipped, so rerunning after any interruption is safe and
  resumes where it stopped
- Metadata (description, GPS, favorite) comes from the sidecars; CSV import
  reports are written next to `PhotosReady`
- Tunables: `PAUSE=10 ./import_to_photos.sh` (seconds between batches),
  `BATCH=200` (library files per call)

## Optional: fix dates from album names

If album names carry the real date ("2004 03 Skitour...") but the photos
inside are scans with wrong or missing EXIF dates, add `--fix-album-dates`
to the `prepare_takeout.py` call (requires exiftool). Any photo dated ≥2
years away from its album's year — or the year in its "Photos from YYYY"
folder — gets its EXIF and file date corrected (threshold configurable via
`--date-threshold`). The target date is chosen in this order:

1. a plausible date in the **filename** (`Simon1990-03.JPG`,
   `20160726_221738.jpg`; camera sequence numbers like `IMG_2025.jpg` are
   ignored) — and if the filename *confirms* the photo's existing date,
   the photo is left untouched instead
2. otherwise the **album / year-folder date** (15th of the month, or
   July 1 if no month is known)

Every change is verified and listed in `output/date_fixes.csv` with old
date, new date, reason, and status.

After the import, `make_review_albums.py` turns that list into two albums
in Photos — "0 Review - Datum angepasst" (adjusted, spot-check them) and
"0 Review - Datum NICHT angepasst" (rewrite didn't stick, set dates
manually) — so reviewing is a scroll, not a search:

```bash
~/.photos-migration-venv/bin/python3 make_review_albums.py
```

Delete the review albums afterwards via right-click → "Delete Album" (the
photos stay in the library).

## Design notes

- **Copy, never destroy:** nothing is deleted from Google Photos; the
  Takeout zips/folders remain as backup. Staging uses hardlinks (near-zero
  disk cost); any file the pipeline modifies (sidecar normalization, date
  fixes) is rewritten through a temp file first, so the Takeout originals
  are never touched. `--move` moves files instead, saving disk
- **`--sidecar-ignore-date`** on import is intentional: Google sidecars
  store UTC without timezone; in-file EXIF (or the mtime set in stage 2) is
  more accurate
- Pixel Motion Photos import as stills; the standalone `.MP` video halves
  are skipped (Apple Photos cannot reattach them as Live Photos)
- RAW+JPEG pairs import as separate items
- Cancel your Google One storage only after verifying the import

## Files

- `migrate.sh` — interactive orchestrator (start here)
- `prepare_takeout.py` — stage 2, pure Python stdlib
- `import_to_photos.sh` — stage 3, wraps osxphotos
- `make_review_albums.py` — stage 4 (optional), review albums in Photos
- `skill/` — the same workflow packaged as a Claude skill
  (`google-photos-migration.skill`) so a Claude session can run stage 2 in
  its sandbox and guide the rest
