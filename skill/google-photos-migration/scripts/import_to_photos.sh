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
# osxphotos needs Python >= 3.10 (uses modern type-hint syntax at runtime);
# Apple's bundled /usr/bin/python3 may be 3.9, so pick the newest available.
find_python() {
  for v in python3.13 python3.12 python3.11 python3.10; do
    if command -v "$v" >/dev/null 2>&1; then command -v "$v"; return; fi
  done
  local minor=$(python3 -c 'import sys; print(sys.version_info[1])' 2>/dev/null)
  if [[ -n "$minor" && "$minor" -ge 10 ]]; then command -v python3; fi
}

# rebuild the venv if it was created with a too-old Python
if [[ -x "$VENV/bin/python" ]]; then
  vminor=$("$VENV/bin/python" -c 'import sys; print(sys.version_info[1])')
  if [[ "$vminor" -lt 10 ]]; then
    echo ">> Existing venv uses Python 3.$vminor (too old) — rebuilding..."
    rm -rf "$VENV"
  fi
fi

if [[ ! -x "$VENV/bin/osxphotos" ]]; then
  PY="$(find_python)"
  if [[ -z "$PY" ]]; then
    echo "ERROR: osxphotos requires Python 3.10+, but only an older python3 was found."
    echo "Install a current Python first:   brew install python"
    echo "(no Homebrew? get it at https://brew.sh)"
    exit 1
  fi
  echo ">> Installing osxphotos (one-time setup, using $PY)..."
  "$PY" -m venv "$VENV"
  "$VENV/bin/pip" -q install --upgrade pip osxphotos
fi
OSXPHOTOS="$VENV/bin/osxphotos"
echo ">> osxphotos $($OSXPHOTOS version 2>/dev/null | head -1)"

# --- helpers ---------------------------------------------------------------
PAUSE="${PAUSE:-5}"        # seconds to pause between batches (be nice to Photos.app)
BATCH="${BATCH:-500}"      # library files per osxphotos call

bounce_photos() {
  echo ">> Photos.app seems unhappy — restarting it..."
  killall Photos 2>/dev/null || true
  sleep 5
  killall -9 Photos 2>/dev/null || true
  sleep 3
  open -a Photos
  sleep 20
}

run_import() {
  # $1 = report file, remaining args passed to osxphotos import
  local report="$1"; shift
  "$OSXPHOTOS" import "$@" \
      --skip-dups \
      --sidecar --sidecar-ignore-date \
      --stop-on-error 50 \
      --report "$report" --append \
      --verbose
}

# rotate reports from previous (possibly crashed) runs
for r in "$REPORTS/albums_import.csv" "$REPORTS/library_import.csv"; do
  [[ -f "$r" ]] && mv "$r" "$r.$(date +%Y%m%d%H%M%S).bak"
done

failed_albums=()

# --- 2. import albums, one osxphotos call per album ------------------------
if [[ -d "$READY/Albums" ]]; then
  albumdirs=("$READY/Albums"/*(N/))
  echo ">> Importing ${#albumdirs[@]} albums, pausing ${PAUSE}s between albums..."
  n=0
  for albumdir in $albumdirs; do
    name="${albumdir:t}"
    n=$((n+1))
    echo ">> [$n/${#albumdirs[@]}] Album: $name"
    if ! run_import "$REPORTS/albums_import.csv" "$albumdir" --walk \
           --album "$name" --dup-albums; then
      bounce_photos
      echo ">> Retrying album: $name"
      if ! run_import "$REPORTS/albums_import.csv" "$albumdir" --walk \
             --album "$name" --dup-albums; then
        echo ">> Album '$name' failed twice — skipping, rerun the script later."
        failed_albums+=("$name")
      fi
    fi
    sleep "$PAUSE"
  done
fi

# --- 3. import remaining library in batches --------------------------------
if [[ -d "$READY/Library" ]]; then
  libfiles=()
  for f in "$READY/Library"/*(N.); do
    [[ "$f" == *.json ]] || libfiles+=("$f")
  done
  total=${#libfiles[@]}
  echo ">> Importing $total library photos in batches of $BATCH..."
  i=1
  while (( i <= total )); do
    j=$(( i + BATCH - 1 )); (( j > total )) && j=$total
    echo ">> Library batch $i-$j of $total"
    if ! run_import "$REPORTS/library_import.csv" ${libfiles[$i,$j]}; then
      bounce_photos
      echo ">> Retrying library batch $i-$j"
      run_import "$REPORTS/library_import.csv" ${libfiles[$i,$j]} || \
        echo ">> Batch $i-$j failed twice — skipping, rerun the script later."
    fi
    i=$(( j + 1 ))
    sleep "$PAUSE"
  done
fi

if (( ${#failed_albums[@]} > 0 )); then
  echo ""
  echo ">> WARNING: these albums had failures (rerun this script to retry):"
  printf '   %s\n' "${failed_albums[@]}"
fi

echo ""
echo ">> Done. Import reports: $REPORTS/albums_import.csv, $REPORTS/library_import.csv"
echo ">> Open Photos.app and check your albums. iCloud sync happens automatically."
