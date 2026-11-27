#!/usr/bin/env bash
# Encode one screencast take (a directory of PNG frames plus frames.txt, written by web/e2e/media/screencast.ts)
# into the documentation formats:
#
#   scripts/demo/encode.sh <frames-dir> <name> [--gif-width 1280] [--gif-fps 15] [--quality 95] [--speed 1.0]
#                          [--mp4-width 1920] [--max-mb 8] [--no-mp4]
#
#   docs/images/<name>.gif    gifski, for README and any page GitHub renders (GitHub does not play repo video files)
#   docs/media/<name>.mp4     H.264, for the docs site, release notes and uploading to the README as a GitHub asset
#
# Settings and why they are the defaults are in scripts/demo/README.md. The frames are captured at device scale 2, so
# both outputs are a downscale of a sharp source, never an upscale.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
[ $# -ge 2 ] || { sed -n '2,12p' "$0" >&2; exit 2; }
SRC="$1"; NAME="$2"; shift 2
GIF_WIDTH=1280 GIF_FPS=15 QUALITY=95 SPEED=1.0 MP4_WIDTH=1920 MAX_MB=8 MP4=true
while [ $# -gt 0 ]; do
  case "$1" in
    --gif-width) GIF_WIDTH="$2"; shift ;;
    --gif-fps)   GIF_FPS="$2"; shift ;;
    --quality)   QUALITY="$2"; shift ;;
    --speed)     SPEED="$2"; shift ;;
    --mp4-width) MP4_WIDTH="$2"; shift ;;
    --max-mb)    MAX_MB="$2"; shift ;;
    --no-mp4)    MP4=false ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

for tool in ffmpeg gifski; do
  command -v "$tool" >/dev/null || { echo "$tool is required (brew install $tool)" >&2; exit 1; }
done
[ -f "$SRC/frames.txt" ] || { echo "no frames.txt in $SRC: record first (scripts/record-media.sh)" >&2; exit 1; }

mkdir -p "$ROOT/docs/images" "$ROOT/docs/media"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
# The concat demuxer replays each frame for its recorded duration; setpts applies the speed-up.
INPUT=(-f concat -safe 0 -i "$SRC/frames.txt")
TIMING="setpts=PTS/${SPEED}"

if [ "$MP4" = true ]; then
  ffmpeg -y -loglevel error "${INPUT[@]}" \
    -vf "${TIMING},fps=30,scale=${MP4_WIDTH}:-2:flags=lanczos,format=yuv420p" \
    -c:v libx264 -preset slow -crf 18 -profile:v high -movflags +faststart -an "$ROOT/docs/media/$NAME.mp4"
  echo "wrote docs/media/$NAME.mp4 ($(du -h "$ROOT/docs/media/$NAME.mp4" | cut -f1))"
fi

# Frames at the GIF rate and width first, then gifski: its per-frame palettes and temporal dithering keep text and
# UI gradients clean where a single ffmpeg palette bands.
ffmpeg -y -loglevel error "${INPUT[@]}" -vf "${TIMING},fps=${GIF_FPS},scale=${GIF_WIDTH}:-1:flags=lanczos" \
  "$TMP/g%05d.png"
gifski --quiet --fps "$GIF_FPS" --quality "$QUALITY" --motion-quality 100 --lossy-quality 100 --width "$GIF_WIDTH" -o "$ROOT/docs/images/$NAME.gif" "$TMP"/g*.png
BYTES=$(wc -c <"$ROOT/docs/images/$NAME.gif")
echo "wrote docs/images/$NAME.gif ($((BYTES / 1024)) KB, ${GIF_WIDTH}px, ${GIF_FPS} fps, quality ${QUALITY})"
if [ "$BYTES" -gt $((MAX_MB * 1024 * 1024)) ]; then
  echo "over ${MAX_MB} MB: lower --quality (80), --gif-fps (12) or --gif-width (1120), or raise --speed" >&2
  exit 1
fi
