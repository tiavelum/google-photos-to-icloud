#!/bin/zsh
# import_to_photos.sh — Stage 2 of the Google Photos → Apple Photos migration.
# Run this ON YOUR MAC (Terminal). It installs osxphotos into a private
# virtual environment, then imports the prepared PhotosReady folder:
#   - Albums/<Name>/*  -> imported into an Apple Photos album called <Name>
#   - Library/*        -> imported with no album
# Metadata (descriptions, GPS) is read from Google's JSON sidecars.
# Duplicates are skipped; if a duplicate belongs in an album, it is added
# to the album without re-importing the file.
#
# Usage:  ./import_to_photos.sh [path-to-PhotosReady]
set -e

READY="${1:-$HOME/Downloads/PhotosMigration/output/PhotosReady}"
VENV="$HOME/.photos-migration-venv"
REPORTS="$(dirname "$READY")"

if [[ ! -d "$READY/Albums" && ! -d "$READY/Library" ]]; then
  echo "ERROR: $READY does not contain Albums/ or Library/. Run prepare_takeout.py first."
  exit 1
fi

# --- 1. install osxphotos (one-time) ---------------------------------------
if [[ ! -x "$VENV/bin/osxphotos" ]]; then
  echo ">> Installing osxphotos (one-time setup)..."
  python3 -m venv "$VENV"
  "$VENV/bin/pip" -q install --upgrade pip osxphotos
fi
OSXPHOTOS="$VENV/bin/osxphotos"
echo ">> osxphotos $($OSXPHOTOS version 2>/dev/null | head -1)"

# --- 2. import albums -------------------------------------------------------
if [[ -d "$READY/Albums" ]]; then
  echo ">> Importing albums (this may take a while)..."
  "$OSXPHOTOS" import "$READY/Albums" \
      --walk \
      --album "{filepath.parent.name}" \
      --skip-dups --dup-albums \
      --sidecar --sidecar-ignore-date \
      --report "$REPORTS/albums_import.csv" \
      --verbose
fi

# --- 3. import remaining library -------------------------------------------
if [[ -d "$READY/Library" ]]; then
  echo ">> Importing library photos (no album)..."
  "$OSXPHOTOS" import "$READY/Library" \
      --walk \
      --skip-dups \
      --sidecar --sidecar-ignore-date \
      --report "$REPORTS/library_import.csv" \
      --verbose
fi

echo ""
echo ">> Done. Import reports: $REPORTS/albums_import.csv, $REPORTS/library_import.csv"
echo ">> Open Photos.app and check your albums. iCloud sync happens automatically."
