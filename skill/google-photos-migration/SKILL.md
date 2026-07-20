---
name: google-photos-migration
description: Migrate Google Photos to Apple Photos preserving albums and metadata. Use when the user mentions Google Takeout, moving/migrating photos from Google Photos to Apple Photos or iCloud Photos, rebuilding photo albums after export, or importing a Takeout archive into the Photos app.
---

# Google Photos → Apple Photos migration (albums preserved)

Two-stage workflow. Stage 1 runs in the Claude sandbox (pure-Python, stdlib
only). Stage 2 runs on the user's Mac in Terminal (osxphotos).

## Prerequisites
- User has downloaded Google Takeout zip(s) of Google Photos (zip format).
  If not: instruct them to go to takeout.google.com → Deselect all →
  Google Photos only → Export once, .zip, 50 GB parts.
- iCloud Photos enabled with enough free storage.
- Connect the folder containing the Takeout zips (usually ~/Downloads).

## Stage 1 — prepare (sandbox)
Run `scripts/prepare_takeout.py`:

    python3 prepare_takeout.py --source <dir with Takeout*.zip> \
                               --output <dir>/output --move

What it does:
- extracts all Takeout*.zip parts (idempotent; `.done_*` markers); also
  works on already-extracted `Takeout*/` folders (Safari auto-extracts)
- skips Trash/Bin/Papierkorb and failed-video folders (EN + DE locales)
- real album folders → `output/PhotosReady/Albums/<Name>/`
- "Photos from YYYY" / "Fotos von YYYY" folders →
  `output/PhotosReady/Library/`, minus photos already present in an album
  (content-hash dedupe)
- prefers the Google-edited version (`X-edited.jpg` / `X-bearbeitet.jpg`)
  over the untouched original, stored under the original filename so the
  sidecar pairs up
- pairs each photo with its Google JSON sidecar (handles
  `.supplemental-metadata` truncations and `X.jpg(1).json` → `X(1).jpg`)
  and normalizes sidecars to the key set osxphotos requires
- sets file mtime from photoTakenTime (fixes timeline for EXIF-less files)
- optional `--fix-album-dates` (needs exiftool on the Mac): clamps photo
  dates ≥2 years off their album/year-folder name's date to that date,
  verified and logged to `output/date_fixes.csv`
- writes `prepare_report.txt` with per-album counts

For large libraries (>20 GB) run it with nohup in the background and poll
the log, since single bash calls time out:

    nohup python3 prepare_takeout.py ... > prepare.log 2>&1 &

## Stage 2 — import (user's Mac, Terminal)
User runs `scripts/import_to_photos.sh [path-to-PhotosReady]`:
- needs Python 3.10+ (Apple's bundled 3.9 is too old for osxphotos —
  `brew install python`); creates a venv at ~/.photos-migration-venv and
  installs osxphotos (one-time)
- imports one osxphotos call per album, then Library/ in batches of 500,
  pausing between batches (a single huge import can hang Photos.app);
  on failure it restarts Photos.app and retries the batch once
- `--skip-dups --dup-albums --sidecar --sidecar-ignore-date
  --stop-on-error 50`; rerunning resumes safely
- writes CSV import reports next to PhotosReady
- afterwards, if --fix-album-dates was used:
  `~/.photos-migration-venv/bin/python3 scripts/make_review_albums.py`
  builds "0 Review ..." albums in Photos from date_fixes.csv for easy
  review of adjusted / failed-to-adjust photos

`--sidecar-ignore-date` is intentional: Google sidecars store UTC times;
EXIF inside the files (or the mtime set in Stage 1) is more accurate.

## Verification
- Compare album counts in prepare_report.txt vs albums_import.csv.
- Have the user spot-check 2–3 albums in Photos.app, including one photo's
  date and location.
- Remind the user: nothing was deleted from Google Photos; cancel Google One
  storage only after verifying.

## Edge cases
- RAW+JPEG pairs import as separate items (Photos may stack them).
- Motion Photos import as still JPG (the embedded video part is dropped).
- Shared albums appear in Takeout only if the user saved them to their
  library.
- If a transfer was interrupted, rerunning both stages is safe: Stage 1 is
  idempotent, Stage 2 skips duplicates.
