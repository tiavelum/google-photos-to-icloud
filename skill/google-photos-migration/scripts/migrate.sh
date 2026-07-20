#!/bin/zsh
# migrate.sh — interactive orchestrator for the Google Photos → Apple Photos
# migration. Detects how far you've come and guides you through each step.
#
# Usage:  ./migrate.sh [workdir]     (default workdir: folder of this script)
set -e

DIR="${1:-$(cd "$(dirname "$0")" && pwd)}"
SRC="$DIR"                     # where Takeout*.zip are expected
OUT="$DIR/output"
READY="$OUT/PhotosReady"

bar()  { echo "\n────────────────────────────────────────────"; }
ask()  { echo ""; read "?Press Enter when done (or Ctrl-C to quit)... "; }

bar
echo "Google Photos → Apple Photos migration"
echo "Working directory: $DIR"
bar

# ── Step 1: Takeout export ──────────────────────────────────────────────────
if ! ls "$SRC"/[Tt]akeout*.zip >/dev/null 2>&1 && [ ! -d "$READY" ]; then
  echo "STEP 1 — Export your photos from Google (manual, ~2 min + wait)"
  echo ""
  echo "  1. Open   https://takeout.google.com"
  echo "  2. 'Deselect all', then tick ONLY 'Google Photos'"
  echo "  3. Next step → Export once → .zip → 50 GB"
  echo "  4. 'Create export' — Google emails you when ready (hours)"
  echo "  5. Download ALL takeout-*.zip files into:"
  echo "         $SRC"
  echo ""
  echo "Re-run this script once the zip files are in place."
  exit 0
fi

# ── Step 2: prepare ─────────────────────────────────────────────────────────
if [ ! -d "$READY" ]; then
  echo "STEP 2 — Preparing your library (automatic)"
  echo "Found Takeout archives:"
  ls -lh "$SRC"/[Tt]akeout*.zip | awk '{print "   " $9 "  (" $5 ")"}'
  echo ""
  read "?Start processing now? [Enter=yes, Ctrl-C=abort] "
  python3 "$DIR/prepare_takeout.py" --source "$SRC" --output "$OUT" --move
  bar
  echo "Prepared. Review the summary above (also in output/prepare_report.txt)."
fi

# ── Step 3: import into Apple Photos ────────────────────────────────────────
if [ -d "$READY" ]; then
  bar
  echo "STEP 3 — Import into Apple Photos (automatic, on this Mac)"
  echo "Before continuing, please check MANUALLY:"
  echo "  • iCloud Photos is ON  (System Settings → Apple ID → iCloud → Photos)"
  echo "  • Your iCloud plan has enough free storage"
  echo "  • Photos.app has been opened at least once"
  ask
  "$DIR/import_to_photos.sh" "$READY"
  bar
  echo "STEP 4 — Verify (manual)"
  echo "  • Open Photos.app → Albums: spot-check 2–3 albums"
  echo "  • Check one photo's date & location"
  echo "  • iCloud upload runs in background (keep Mac awake/powered)"
  echo ""
  echo "Nothing was deleted from Google Photos. Cancel Google One storage"
  echo "only after everything is verified."
fi
