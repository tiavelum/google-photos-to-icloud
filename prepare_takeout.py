#!/usr/bin/env python3
"""
prepare_takeout.py — Stage 1 of the Google Photos → Apple Photos migration.

Takes one or more Google Takeout zip files (or an already-extracted Takeout
folder) and produces a clean, import-ready folder structure:

    <output>/PhotosReady/
        Albums/<Album Name>/...   photos belonging to user-created albums
                                  (with their Google JSON sidecars)
        Library/...               photos NOT in any album, deduplicated
                                  against the album copies

Also sets each file's modification time from Google's photoTakenTime so that
photos lacking EXIF dates (screenshots, WhatsApp images, ...) still land at
the right spot in the Apple Photos timeline.

Pure standard library — no third-party packages required.

Usage:
    python3 prepare_takeout.py --source <dir with Takeout*.zip or Takeout/> \
                               --output <output dir> [--move] [--dry-run]
"""

import argparse
import csv
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path

YEAR_FOLDER_RE = re.compile(r"^(Photos from|Fotos von|Fotos aus) \d{4}$")
# Google exports edited photos twice: X.jpg (original) plus X-edited.jpg
# (German: X-bearbeitet.jpg) — the version as seen in Google Photos.
EDITED_RE = re.compile(
    r"^(.*)-(edited|bearbeitet|modifié|editado)(\.[A-Za-z0-9]{2,4})$",
    re.IGNORECASE,
)
SKIP_FOLDERS = {
    "Trash", "Bin", "Failed Videos",
    "Papierkorb", "Fehlgeschlagene Videos",
    "Videos, die nicht verarbeitet werden konnten",
}
ALBUM_META_FILES = {"metadata.json", "metadata(1).json"}
MEDIA_EXT = {
    ".jpg", ".jpeg", ".heic", ".heif", ".png", ".gif", ".tiff", ".tif",
    ".bmp", ".webp", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf",
    ".mp4", ".mov", ".m4v", ".avi", ".mpg", ".mpeg", ".3gp", ".mkv", ".mts",
}

log_lines = []


def log(msg):
    print(msg, flush=True)
    log_lines.append(msg)


def norm(name: str) -> str:
    """Normalize unicode so 'Zürich' matches across zip parts."""
    return unicodedata.normalize("NFC", name)


# ---------------------------------------------------------------- extraction

def extract_zips(source: Path, workdir: Path) -> None:
    zips = sorted(source.glob("[Tt]akeout*.zip"))
    if not zips:
        return
    workdir.mkdir(parents=True, exist_ok=True)
    for z in zips:
        marker = workdir / f".done_{z.name}"
        if marker.exists():
            log(f"[extract] {z.name} already extracted, skipping")
            continue
        log(f"[extract] {z.name} ...")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(workdir)
        marker.touch()


def find_google_photos_roots(base: Path):
    """Find every '.../Google Photos' (or 'Google Fotos') directory under base."""
    roots = []
    for dirpath, dirnames, _ in os.walk(base):
        for d in dirnames:
            if d in ("Google Photos", "Google Fotos"):
                roots.append(Path(dirpath) / d)
        # don't descend into found roots' siblings unnecessarily deep
    return roots


# ---------------------------------------------------------------- sidecars

def sidecar_media_name(json_name: str):
    """Given a sidecar filename, return the media filename it belongs to.

    Handles:  X.jpg.json
              X.jpg.supplemental-metadata.json  (and truncated variants)
              X.jpg(1).json  ->  X(1).jpg
    Returns None for album metadata files.
    """
    if json_name in ALBUM_META_FILES or not json_name.endswith(".json"):
        return None
    stem = json_name[:-5]  # strip .json
    # strip supplemental-metadata suffix (Google truncates it arbitrarily)
    m = re.match(r"^(.*\.[A-Za-z0-9]{2,4})\.(s|su|sup|supp|suppl|supplemental[-a-z]*)$", stem)
    if m:
        stem = m.group(1)
    # relocate duplicate counter:  X.jpg(1) -> X(1).jpg
    m = re.match(r"^(.*)(\.[A-Za-z0-9]{2,4})(\(\d+\))$", stem)
    if m:
        stem = f"{m.group(1)}{m.group(3)}{m.group(2)}"
    return stem


def build_sidecar_map(folder: Path):
    """Map media filename -> sidecar Path for one folder."""
    smap = {}
    for f in folder.iterdir():
        if f.is_file() and f.suffix == ".json":
            media = sidecar_media_name(f.name)
            if media:
                smap[norm(media)] = f
    return smap


def normalize_sidecar(path: Path) -> bool:
    """Make a Takeout sidecar recognizable by osxphotos.

    osxphotos identifies a Google Takeout sidecar only if ALL of these keys
    exist: title, description, imageViews, creationTime, photoTakenTime,
    geoData, geoDataExif, url. Older Takeout exports omit some (typically
    geoDataExif), which makes the import crash with 'Unknown sidecar type'.
    Add harmless defaults for missing keys.

    Rewrites via temp file + os.replace so that if the sidecar is hardlinked
    to the original Takeout copy, the original stays untouched.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict) or "photoTakenTime" not in data:
        return False
    no_geo = {"latitude": 0.0, "longitude": 0.0, "altitude": 0.0,
              "latitudeSpan": 0.0, "longitudeSpan": 0.0}
    defaults = {
        "title": "",
        "description": "",
        "imageViews": "0",
        "creationTime": data.get("photoTakenTime", {}),
        "geoData": no_geo,
        "url": "",
    }
    missing = {k: v for k, v in defaults.items() if k not in data}
    if "geoDataExif" not in data:
        missing["geoDataExif"] = data.get("geoData", no_geo)
    if not missing:
        return False
    data.update(missing)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)
    return True


def taken_timestamp(sidecar: Path):
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        ts = int(data.get("photoTakenTime", {}).get("timestamp", 0))
        return ts if ts > 0 else None
    except Exception:
        return None


# ------------------------------------------------------- album date fixing

ALBUM_YEAR_RE = re.compile(r"^\s*(19\d{2}|20[0-3]\d)(?:\s+(\d{1,2}))?(?:\s|$)")


def album_target_date(album_name: str):
    """Parse '2004 03 Igloo...' -> (2004, 3); '2002 South Africa' -> (2002, None).
    Returns None for albums without a leading plausible year (e.g. '9999 ...')."""
    m = ALBUM_YEAR_RE.match(album_name)
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else None
    if month is not None and not 1 <= month <= 12:
        month = None
    return year, month


def exiftool_read_dates(files):
    """Batch-read DateTimeOriginal/CreateDate. Returns {filename: year or None}."""
    out = {}
    if not files:
        return out
    try:
        r = subprocess.run(
            ["exiftool", "-j", "-fast2", "-DateTimeOriginal", "-CreateDate"]
            + [str(f) for f in files],
            capture_output=True, text=True)
        for entry in json.loads(r.stdout or "[]"):
            d = entry.get("DateTimeOriginal") or entry.get("CreateDate") or ""
            year = None
            m = re.match(r"^(\d{4})", str(d))
            if m and int(m.group(1)) > 1900:   # exiftool uses 0000 for unset
                year = int(m.group(1))
            out[Path(entry["SourceFile"]).name] = year
    except Exception as e:
        log(f"[dates] WARNING: exiftool read failed: {e}")
    return out


def fix_dates_for_files(label: str, year: int, month, media: list,
                        threshold: int, fixes: list, stats):
    """Clamp photo dates that are >= threshold years away from the target
    date implied by `label` (album or year-folder name), or missing
    entirely, to that date. Rewrites EXIF via exiftool (breaks the
    hardlink, so Takeout originals stay untouched) and sets the file
    mtime. Appends (label, file, old, new, reason) rows to fixes."""
    if not media:
        return
    tstr = f"{year}:{month:02d}:15 12:00:00" if month else f"{year}:07:01 12:00:00"
    ts = datetime.datetime(year, month or 7, 15 if month else 1, 12).timestamp()

    exif_years = exiftool_read_dates(media)
    sidecar_maps = {}   # parent dir -> sidecar map (cached)

    to_fix = []
    for f in media:
        exif_year = exif_years.get(f.name)
        if f.parent not in sidecar_maps:
            sidecar_maps[f.parent] = build_sidecar_map(f.parent)
        sc = sidecar_maps[f.parent].get(norm(f.name))
        sc_ts = taken_timestamp(sc) if sc else None
        sc_year = datetime.datetime.fromtimestamp(sc_ts).year if sc_ts else None
        current = exif_year or sc_year
        if current is None:
            reason, old = "no-date", ""
        elif abs(current - year) >= threshold:
            reason, old = f"off-by-{abs(current - year)}y", str(current)
        else:
            continue
        to_fix.append((f, old, reason))

    if not to_fix:
        return
    try:
        subprocess.run(
            ["exiftool", "-m", "-overwrite_original", f"-AllDates={tstr}",
             f"-FileModifyDate={tstr}"] + [str(f) for f, _, _ in to_fix],
            capture_output=True, text=True)
    except Exception as e:
        log(f"[dates] WARNING: exiftool write failed for {label}: {e}")
    for f, _, _ in to_fix:
        try:
            os.utime(f, (ts, ts))
        except OSError:
            pass
    # verify: re-read EXIF; None means the format has no EXIF date at all,
    # in which case the mtime we just set governs the date in Photos -> ok
    new_years = exiftool_read_dates([f for f, _, _ in to_fix])
    failed = 0
    for f, old, reason in to_fix:
        ny = new_years.get(f.name)
        status = "fixed" if (ny == year or ny is None) else "fix-failed"
        failed += status == "fix-failed"
        fixes.append([label, f.name, old, tstr, reason, status])
    stats["dates_fixed"] += len(to_fix) - failed
    stats["dates_fix_failed"] += failed
    log(f"[dates] {label}: adjusted {len(to_fix)} file(s) -> {tstr[:10]}"
        + (f" ({failed} FAILED verification)" if failed else ""))


def fix_album_dates(album: str, dest: Path, threshold: int, fixes: list, stats):
    """Date fix for an album folder; target date parsed from the album name."""
    target = album_target_date(album)
    if target is None:
        return
    media = [f for f in sorted(dest.iterdir())
             if f.is_file() and f.suffix.lower() in MEDIA_EXT]
    fix_dates_for_files(album, target[0], target[1], media,
                        threshold, fixes, stats)


# ---------------------------------------------------------------- hashing

def content_key(path: Path):
    """Fast content fingerprint: size + md5 of first 1 MiB."""
    h = hashlib.md5()
    size = path.stat().st_size
    with open(path, "rb") as f:
        h.update(f.read(1024 * 1024))
    return (size, h.hexdigest())


# ---------------------------------------------------------------- transfer

def transfer(src: Path, dst: Path, move: bool, dry: bool):
    if dry:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    if move:
        shutil.move(str(src), str(dst))
    else:
        try:
            os.link(src, dst)          # cheap hardlink when possible
        except OSError:
            shutil.copy2(src, dst)


def folder_media_items(d: Path, stats):
    """Yield (dest_name, src_file) for one Takeout folder.

    When Google exported both an original (X.jpg) and its edited version
    (X-edited.jpg / X-bearbeitet.jpg), only the edited content is kept,
    stored under the ORIGINAL filename so the JSON sidecar (always named
    after the original) still pairs up and dedupe stays consistent.
    """
    files = {}
    for f in sorted(d.iterdir()):
        if not f.is_file() or f.suffix == ".json":
            continue
        if f.suffix.lower() not in MEDIA_EXT:
            stats["unknown_type_skipped"] += 1
            continue
        files[norm(f.name)] = f
    edited = {}
    for name, f in files.items():
        if m := EDITED_RE.match(name):
            edited[norm(m.group(1) + m.group(3))] = f
    for name, f in files.items():
        if m := EDITED_RE.match(name):
            orig = norm(m.group(1) + m.group(3))
            if orig in files:
                continue              # emitted when the original comes up
            yield orig, f             # orphan edited file: use original name
        elif name in edited:
            stats["edited_replaced_original"] += 1
            yield name, edited[name]  # edited content under original name
        else:
            yield name, f


def place(media: Path, sidecar, dest_dir: Path, move, dry, stats, dest_name=None):
    dst = dest_dir / (dest_name or norm(media.name))
    transfer(media, dst, move, dry)
    ts = None
    if sidecar is not None:
        sc_dst = dest_dir / norm(sidecar.name)
        transfer(sidecar, sc_dst, move, dry)
        if not dry and normalize_sidecar(sc_dst):
            stats["sidecar_normalized"] += 1
        ts = taken_timestamp(sc_dst) if not dry else None
        stats["with_sidecar"] += 1
    else:
        stats["without_sidecar"] += 1
    if ts and not dry:
        try:
            os.utime(dst, (ts, ts))
        except OSError:
            pass


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True,
                    help="dir containing Takeout*.zip and/or extracted Takeout/")
    ap.add_argument("--output", required=True, help="output base dir")
    ap.add_argument("--move", action="store_true",
                    help="move files instead of hardlink/copy (saves disk; "
                         "zips remain your backup)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fix-album-dates", action="store_true",
                    help="if a photo's date is far off the year in its album "
                         "name, set EXIF+file date to the album date "
                         "(requires exiftool); changes listed in "
                         "output/date_fixes.csv")
    ap.add_argument("--date-threshold", type=int, default=2,
                    help="years of difference that triggers --fix-album-dates "
                         "(default: 2)")
    args = ap.parse_args()

    if args.fix_album_dates and not shutil.which("exiftool"):
        log("ERROR: --fix-album-dates requires exiftool. "
            "Install it with:  brew install exiftool")
        sys.exit(1)

    source, output = Path(args.source), Path(args.output)
    ready = output / "PhotosReady"
    albums_out, library_out = ready / "Albums", ready / "Library"
    workdir = output / "_extracted"

    extract_zips(source, workdir)

    roots = find_google_photos_roots(workdir)
    if not roots:
        roots = find_google_photos_roots(source)
    if not roots:
        log("ERROR: no 'Google Photos' folder found. Extract zips or check --source.")
        sys.exit(1)
    log(f"[scan] found {len(roots)} Google Photos root(s)")

    # collect folders across all zip parts (same album can span parts)
    album_dirs = defaultdict(list)   # album name -> [dirs]
    year_dirs = []
    for root in roots:
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            name = norm(d.name)
            if name in SKIP_FOLDERS:
                log(f"[skip] folder '{name}'")
            elif YEAR_FOLDER_RE.match(name):
                year_dirs.append(d)
            else:
                album_dirs[name].append(d)

    log(f"[scan] {len(album_dirs)} albums, {len(year_dirs)} year folders")

    stats = defaultdict(int)
    album_index = {}   # content key -> album name (for dedup)
    date_fixes = []    # rows for date_fixes.csv

    # ---- pass 1: albums
    for album, dirs in sorted(album_dirs.items()):
        dest = albums_out / album
        count = 0
        for d in dirs:
            smap = build_sidecar_map(d)
            for dest_name, f in folder_media_items(d, stats):
                try:
                    album_index[content_key(f)] = album
                except OSError:
                    pass
                place(f, smap.get(dest_name), dest, args.move, args.dry_run,
                      stats, dest_name)
                count += 1
        stats["album_photos"] += count
        log(f"[album] {album}: {count} items")
        if args.fix_album_dates and not args.dry_run:
            fix_album_dates(album, dest, args.date_threshold, date_fixes, stats)

    # ---- pass 2: year folders -> Library, minus items already in an album
    for d in sorted(year_dirs, key=lambda p: p.name):
        smap = build_sidecar_map(d)
        kept = deduped = 0
        placed_files = []
        for dest_name, f in folder_media_items(d, stats):
            try:
                key = content_key(f)
            except OSError:
                key = None
            if key is not None and key in album_index:
                deduped += 1
                continue
            place(f, smap.get(dest_name), library_out, args.move, args.dry_run,
                  stats, dest_name)
            placed_files.append(library_out / dest_name)
            kept += 1
        stats["library_photos"] += kept
        stats["deduplicated"] += deduped
        log(f"[year] {d.name}: kept {kept}, removed {deduped} album duplicates")
        if args.fix_album_dates and not args.dry_run:
            if ym := re.search(r"(19|20)\d{2}", d.name):
                fix_dates_for_files(d.name, int(ym.group(0)), None,
                                    [p for p in placed_files if p.exists()],
                                    args.date_threshold, date_fixes, stats)

    # ---- report
    log("\n========== SUMMARY ==========")
    log(f"Albums:                    {len(album_dirs)}")
    log(f"Photos in albums:          {stats['album_photos']}")
    log(f"Photos in library only:    {stats['library_photos']}")
    log(f"Duplicates removed:        {stats['deduplicated']}")
    log(f"With JSON sidecar:         {stats['with_sidecar']}")
    log(f"Sidecars normalized:       {stats['sidecar_normalized']}")
    log(f"Edited replaced original:  {stats['edited_replaced_original']}")
    if args.fix_album_dates:
        log(f"Dates fixed from album:    {stats['dates_fixed']}")
        log(f"Date fixes FAILED:         {stats['dates_fix_failed']}")
        with open(output / "date_fixes.csv", "w", newline="",
                  encoding="utf-8") as fp:
            w = csv.writer(fp)
            w.writerow(["album", "file", "old_date", "new_date", "reason",
                        "status"])
            w.writerows(date_fixes)
        log(f"Date change list: {output / 'date_fixes.csv'}")
    log(f"Without sidecar:           {stats['without_sidecar']}")
    log(f"Skipped non-media files:   {stats['unknown_type_skipped']}")
    log(f"Output: {ready}")
    (output / "prepare_report.txt").write_text("\n".join(log_lines), encoding="utf-8")


if __name__ == "__main__":
    main()
