#!/usr/bin/env bash
# Record the README hero GIF and the documentation stills from the NO-KEY stack. No model key, no model call.
#
#   ./scripts/dev.sh --no-key        # first: the stack must be up (ports 3000 and 8080)
#   scripts/record-demo.sh           # hero GIF -> docs/images/demo.gif
#   scripts/record-demo.sh --stills  # stills only -> docs/images/
#
# Needs ffmpeg and a Playwright browser (PW_CHROMIUM_PATH overrides the pinned one). The raw video is written
# to a temp directory and deleted: only the GIF (kept under 5 MB, 960 px wide) and the stills are committed.
# GIF_START trims leading seconds, GIF_SPEED speeds the take up (setpts divisor), GIF_FPS sets the frame rate.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STILLS_ONLY=false
[ "${1:-}" = "--stills" ] && STILLS_ONLY=true

command -v ffmpeg >/dev/null || { echo "ffmpeg is required" >&2; exit 1; }
curl -sf http://localhost:8080/health >/dev/null || { echo "the stack is not up: ./scripts/dev.sh --no-key" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export RECORD_MEDIA=1 RECORD_VIDEO_DIR="$WORK"

cd "$ROOT/web"
if [ "$STILLS_ONLY" = true ]; then
  pnpm exec playwright test e2e/readme-gif.spec.ts --project=chromium-python -g "stills"
  # The spec writes PNGs; the repo keeps JPGs (each well under 400 KB).
  for name in compare-dialog orchestration-graph-live runs-approval aspire-trace; do
    ffmpeg -y -loglevel error -i "$ROOT/docs/images/$name.png" -q:v 4 "$ROOT/docs/images/$name.jpg"
    rm -f "$ROOT/docs/images/$name.png"
  done
  exit 0
fi
pnpm exec playwright test e2e/readme-gif.spec.ts --project=chromium-python -g "hero"

START="${GIF_START:-0.5}" SPEED="${GIF_SPEED:-1.7}" FPS="${GIF_FPS:-8}" WIDTH="${GIF_WIDTH:-960}"
OUT="$ROOT/docs/images/demo.gif"
ffmpeg -y -loglevel error -ss "$START" -i "$WORK/hero.webm" \
  -vf "setpts=PTS/${SPEED},fps=${FPS},scale=${WIDTH}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
  "$OUT"
SIZE=$(wc -c <"$OUT")
echo "wrote $OUT ($SIZE bytes)"
[ "$SIZE" -lt 5242880 ] || { echo "GIF is over 5 MB: raise GIF_SPEED, lower GIF_FPS or GIF_WIDTH" >&2; exit 1; }
