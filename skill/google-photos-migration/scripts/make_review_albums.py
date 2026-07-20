#!/usr/bin/env python3
"""
make_review_albums.py — Stage 4 (optional) of the Google Photos → Apple
Photos migration. Run ON YOUR MAC after the import has finished.

Reads output/date_fixes.csv (written by prepare_takeout.py
--fix-album-dates) and creates two albums in Apple Photos so the affected
photos are easy to review:

    "0 Review - Datum angepasst"        photos whose date was adjusted
    "0 Review - Datum NICHT angepasst"  photos where the EXIF rewrite did
                                        not stick (check dates manually)

The "0 " prefix sorts the albums to the top. Delete them (album only, NOT
"Delete from Library"!) once you're done reviewing.

Usage:
    ~/.photos-migration-venv/bin/python3 make_review_albums.py [date_fixes.csv]
"""

import csv
import json
import subprocess
import sys
from pathlib import Path

VENV_BIN = Path.home() / ".photos-migration-venv" / "bin"
ALBUM_FIXED = "0 Review - Datum angepasst"
ALBUM_FAILED = "0 Review - Datum NICHT angepasst"


def main():
    csv_path = (Path(sys.argv[1]) if len(sys.argv) > 1
                else Path(__file__).parent / "output" / "date_fixes.csv")
    if not csv_path.is_file():
        sys.exit(f"ERROR: {csv_path} not found. Run prepare_takeout.py "
                 "--fix-album-dates first.")

    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    # CSVs from before the status column was added: treat rows as fixed
    fixed = {r["file"] for r in rows if r.get("status", "fixed") == "fixed"}
    failed = {r["file"] for r in rows if r.get("status", "fixed") != "fixed"}
    if not (fixed or failed):
        print("No date fixes listed — nothing to do.")
        return

    print(f"{len(fixed)} adjusted, {len(failed)} failed — "
          "querying Photos library (takes a minute)...")
    r = subprocess.run([str(VENV_BIN / "osxphotos"), "query", "--json"],
                       capture_output=True, text=True)
    photos = json.loads(r.stdout or "[]")
    by_name = {}
    for p in photos:
        by_name.setdefault(p["original_filename"], []).append(p["uuid"])

    try:
        from photoscript import PhotosLibrary
    except ImportError:
        sys.exit("ERROR: run this with the migration venv:\n"
                 f"  {VENV_BIN}/python3 {Path(__file__).name}")

    lib = PhotosLibrary()
    for album_name, names in ((ALBUM_FIXED, fixed), (ALBUM_FAILED, failed)):
        if not names:
            continue
        uuids = [u for n in sorted(names) for u in by_name.get(n, [])]
        missing = sorted(n for n in names if n not in by_name)
        album = lib.album(album_name) or lib.create_album(album_name)
        if uuids:
            album.add(list(lib.photos(uuid=uuids)))
        print(f"'{album_name}': {len(uuids)} photos added"
              + (f", {len(missing)} not found in Photos:" if missing else ""))
        for n in missing[:20]:
            print(f"    {n}")
        if len(missing) > 20:
            print(f"    ... and {len(missing) - 20} more")

    print("\nDone. Review the albums in Photos.app. To dissolve a review "
          "album afterwards, right-click it in the sidebar and choose "
          "'Delete Album' — the photos themselves stay in the library.")


if __name__ == "__main__":
    main()
