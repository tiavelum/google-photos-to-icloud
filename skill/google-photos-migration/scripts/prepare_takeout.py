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
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path

YEAR_FOLDER_RE = re.compile(r"^(Photos from|Fotos von|Fotos aus) \d{4}$")
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


def place(media: Path, sidecar, dest_dir: Path, move, dry, stats):
    dst = dest_dir / norm(media.name)
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
    args = ap.parse_args()

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

    # ---- pass 1: albums
    for album, dirs in sorted(album_dirs.items()):
        dest = albums_out / album
        count = 0
        for d in dirs:
            smap = build_sidecar_map(d)
            for f in sorted(d.iterdir()):
                if not f.is_file() or f.suffix == ".json":
                    continue
                if f.suffix.lower() not in MEDIA_EXT:
                    stats["unknown_type_skipped"] += 1
                    continue
                try:
                    album_index[content_key(f)] = album
                except OSError:
                    pass
                place(f, smap.get(norm(f.name)), dest, args.move, args.dry_run, stats)
                count += 1
        stats["album_photos"] += count
        log(f"[album] {album}: {count} items")

    # ---- pass 2: year folders -> Library, minus items already in an album
    for d in sorted(year_dirs, key=lambda p: p.name):
        smap = build_sidecar_map(d)
        kept = deduped = 0
        for f in sorted(d.iterdir()):
            if not f.is_file() or f.suffix == ".json":
                continue
            if f.suffix.lower() not in MEDIA_EXT:
                stats["unknown_type_skipped"] += 1
                continue
            try:
                key = content_key(f)
            except OSError:
                key = None
            if key is not None and key in album_index:
                deduped += 1
                continue
            place(f, smap.get(norm(f.name)), library_out, args.move, args.dry_run, stats)
            kept += 1
        stats["library_photos"] += kept
        stats["deduplicated"] += deduped
        log(f"[year] {d.name}: kept {kept}, removed {deduped} album duplicates")

    # ---- report
    log("\n========== SUMMARY ==========")
    log(f"Albums:                    {len(album_dirs)}")
    log(f"Photos in albums:          {stats['album_photos']}")
    log(f"Photos in library only:    {stats['library_photos']}")
    log(f"Duplicates removed:        {stats['deduplicated']}")
    log(f"With JSON sidecar:         {stats['with_sidecar']}")
    log(f"Sidecars normalized:       {stats['sidecar_normalized']}")
    log(f"Without sidecar:           {stats['without_sidecar']}")
    log(f"Skipped non-media files:   {stats['unknown_type_skipped']}")
    log(f"Output: {ready}")
    (output / "prepare_report.txt").write_text("\n".join(log_lines), encoding="utf-8")


if __name__ == "__main__":
    main()
