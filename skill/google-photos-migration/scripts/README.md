# Google Photos → Apple Photos Migration

Migrates a Google Photos library into Apple Photos **preserving albums and
metadata** (dates, GPS, descriptions), using Google Takeout as the export
path — Google offers no API for full-library access, and the official
Apple/Google direct transfer drops all album structure.

Works fully standalone on a Mac; no dependencies beyond Python 3 (ships with
macOS command line tools) and [osxphotos](https://github.com/RhetTbull/osxphotos)
(installed automatically into a private venv).

## Quick start

```bash
./migrate.sh
```

The orchestrator detects where you are in the process and guides you through
the manual steps (Takeout export, iCloud checks) at the right moment. Run it
repeatedly; each stage is idempotent and safe to rerun.

## What it does

| Stage | Script | What happens |
|---|---|---|
| 1 (manual) | guided by `migrate.sh` | You create a Google Takeout export (.zip) of Google Photos and download the parts |
| 2 (auto) | `prepare_takeout.py` | Extracts all zip parts; separates user albums from "Photos from YYYY" folders; dedupes photos that exist in both; pairs each photo with its Google JSON sidecar (handles truncated `.supplemental-metadata` names and `X.jpg(1).json` renames); sets file mtimes from `photoTakenTime`; skips Trash; writes a report |
| 3 (auto) | `import_to_photos.sh` | Imports `PhotosReady/Albums/<Name>/` into Apple Photos albums (album = folder name) and `PhotosReady/Library/` without albums, reading metadata from sidecars, skipping duplicates; writes CSV import reports |
| 4 (manual) | guided by `migrate.sh` | Spot-check albums, dates, locations; let iCloud sync finish |

Output layout after stage 2:

```
output/PhotosReady/
  Albums/<Album Name>/   photos + sidecars per album
  Library/               everything not in an album, deduplicated
output/prepare_report.txt
```

## Optional: fix dates from album names

If album names carry the real date ("2004 03 Skitour...") but the photos
inside are scans with wrong or missing EXIF dates, add `--fix-album-dates`
to the `prepare_takeout.py` call (requires `exiftool`: `brew install
exiftool`). Any photo dated ≥2 years away from its album's year (threshold:
`--date-threshold`) gets its EXIF + file date set to the album date (15th of
the month, or July 1 if the name has no month). Every change is listed in
`output/date_fixes.csv`; Takeout originals are never modified.

## Design notes

- **Copy, never destroy:** nothing is deleted from Google Photos; the zips
  remain as backup. `--move` only moves files out of the *extracted* copy.
- **`--sidecar-ignore-date`** on import is intentional: Google sidecars store
  UTC without timezone; in-file EXIF (or the mtime set in stage 2) is better.
- Motion Photos import as stills; RAW+JPEG pairs import as separate items.
- Rerunning after an interruption is safe: extraction uses `.done_*` markers,
  import uses `--skip-dups`.

## Files

- `migrate.sh` — interactive orchestrator (start here)
- `prepare_takeout.py` — stage 2, pure Python stdlib
- `import_to_photos.sh` — stage 3, wraps osxphotos
- `make_review_albums.py` — stage 4 (optional): builds "0 Review …" albums
  in Photos from `date_fixes.csv` so adjusted / failed-to-adjust photos are
  easy to inspect
- `skill/` — the same workflow packaged as a Claude skill
  (`google-photos-migration.skill`) so a Claude session can run stage 2 in
  its sandbox and guide the rest
