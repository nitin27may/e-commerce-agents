#!/usr/bin/env bash
# Record the documentation clips and encode them: docs/images/<name>.gif (README, GitHub) and docs/media/<name>.mp4.
#
#   ./scripts/dev.sh --no-key            # first: the no-key stack (no model key is used)
#   scripts/record-media.sh              # every app scenario in web/e2e/demo-media.spec.ts
#   scripts/record-media.sh hero return  # only scenarios whose test title starts with these names
#   scripts/record-media.sh evals        # the terminal clip (scripts/demo/tapes/evals.tape, needs vhs)
#
# Needs ffmpeg and gifski (brew install ffmpeg gifski) and, for terminal clips, vhs. Raw frames go to a temp
# directory and are deleted. Settings, and why they are what they are: scripts/demo/README.md.
#
# Environment: E2E_BASE_URL (frontend, default http://localhost:3000), E2E_API_URL (orchestrator, default
# http://localhost:8080), DATABASE_URL / REDIS_URL for the evals clip, KEEP_FRAMES=1 to keep the raw frames.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API="${E2E_API_URL:-http://localhost:8080}"
for tool in ffmpeg gifski; do
  command -v "$tool" >/dev/null || { echo "$tool is required (brew install $tool)" >&2; exit 1; }
done

FRAMES="$(mktemp -d)"
if [ "${KEEP_FRAMES:-0}" = 1 ]; then
  echo "frames kept in $FRAMES"
else
  trap 'rm -rf "$FRAMES"' EXIT
fi

# Terminal clips are vhs tapes; everything else is a Playwright scenario.
APP=()
for name in "$@"; do
  if [ -f "$ROOT/scripts/demo/tapes/$name.tape" ]; then
    command -v vhs >/dev/null || { echo "vhs is required for $name (brew install vhs)" >&2; exit 1; }
    (cd "$ROOT" && vhs "scripts/demo/tapes/$name.tape")
    mkdir -p "$FRAMES/$name"
    ffmpeg -y -loglevel error -i "$ROOT/docs/media/$name.mp4" -vf "fps=30" "$FRAMES/$name/f%06d.png"
    # A constant-rate frame list, the shape scripts/demo/encode.sh reads.
    (cd "$FRAMES/$name" && for f in f*.png; do printf "file '%s'\nduration 0.0333\n" "$f"; done > frames.txt)
    "$ROOT/scripts/demo/encode.sh" "$FRAMES/$name" "$name" --no-mp4 --gif-fps 12
  else
    APP+=("$name")
  fi
done
[ $# -gt 0 ] && [ ${#APP[@]} -eq 0 ] && exit 0

curl -sf "$API/health" >/dev/null || { echo "the stack is not up at $API: ./scripts/dev.sh --no-key" >&2; exit 1; }
# The clips are made from recorded answers only. A stack with a live model would film answers nobody can reproduce
# (and spend tokens), and one without the tour has none of the accounts the scenarios sign in as.
curl -sf "$API/api/demo/tour" >/dev/null || {
  echo "$API is not the no-key stack (no /api/demo/tour). Start it with ./scripts/dev.sh --no-key" >&2
  exit 1
}

GREP=()
if [ ${#APP[@]} -gt 0 ]; then
  # Playwright matches -g against the whole title path ("demo-media.spec.ts > hero: ..."), so no ^ anchor.
  pattern="$(printf '%s|' "${APP[@]}")"
  GREP=(-g " (${pattern%|}): ")
fi
(cd "$ROOT/web" && RECORD_MEDIA=1 RECORD_FRAMES_DIR="$FRAMES" E2E_API_URL="$API" \
  pnpm exec playwright test e2e/demo-media.spec.ts --project=chromium-python --trace off ${GREP[@]+"${GREP[@]}"})

# Each take directory becomes one clip. Multi-part takes (name-1, name-2) are joined first.
for dir in "$FRAMES"/*/; do
  name="$(basename "$dir")"
  case "$name" in
    *-[0-9]) continue ;;
    stills)
      # Docs stills: 2x PNGs from the spec, written as JPEG (a fraction of the size, no visible loss at q85).
      for png in "$dir"*.png; do
        [ -f "$png" ] || continue
        out="$ROOT/docs/images/$(basename "$png" .png).jpg"
        ffmpeg -y -loglevel error -i "$png" -q:v 3 "$out"
        echo "wrote docs/images/$(basename "$out") ($(du -h "$out" | cut -f1))"
      done
      continue ;;
  esac
  "$ROOT/scripts/demo/encode.sh" "$dir" "$name" --gif-fps 13 --mp4-width 2560
done
for first in "$FRAMES"/*-1/; do
  [ -d "$first" ] || continue
  base="$(basename "$first")"; base="${base%-1}"
  joined="$FRAMES/$base"
  mkdir -p "$joined"
  : > "$joined/frames.txt"
  last=""
  for part in "$FRAMES/$base"-[0-9]/; do
    p="$(basename "$part")"
    # Each part's list ends with its last frame repeated without a duration (the concat demuxer's way of holding
    # it). In the middle of a joined list that entry is misread as a long still, so it is dropped here and only
    # the final part's is added back after the loop.
    while IFS= read -r line; do
      case "$line" in
        file\ *) f="${line#file \'}"; f="${f%\'}"; cp "$part$f" "$joined/$p-$f"; echo "file '$p-$f'" >> "$joined/frames.txt"; last="$p-$f" ;;
        *) echo "$line" >> "$joined/frames.txt" ;;
      esac
    done < <(sed '$d' "$part/frames.txt")
  done
  echo "file '$last'" >> "$joined/frames.txt"
  "$ROOT/scripts/demo/encode.sh" "$joined" "$base" --gif-fps 13 --mp4-width 2560
done
