#!/usr/bin/env bash
# OCR a sample of frames from each clip and fail on anything that looks like account data: a GUID, an email address
# outside the published demo accounts, an IPv4 address, a home directory path, or a word in REDACT_WORDS.
#
#   REDACT_WORDS="Contoso Prod,jane" scripts/demo/check-frames.sh docs/images/*.gif docs/media/*.mp4
#
# Needs ffmpeg and tesseract. A clean result is a floor, not a proof: OCR misses small or styled text, so look at
# the clips too.
set -euo pipefail
command -v tesseract >/dev/null || { echo "tesseract is required (brew install tesseract)" >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
# Seeded demo accounts and documentation domains are public; anything else is not.
ALLOWED_EMAIL='@(gmail\.com|example\.com|example\.org)$'
fail=0
for clip in "$@"; do
  rm -f "$TMP"/*.png
  ffmpeg -loglevel error -i "$clip" -vf "fps=1/2,scale=2560:-1" "$TMP/f%04d.png"
  text="$(for f in "$TMP"/*.png; do tesseract "$f" - 2>/dev/null; done)"
  hits=()
  # A GUID already in the committed demo recordings is seeded demo data (deterministic, public); any other is not.
  while IFS= read -r m; do
    [ -n "$m" ] && ! grep -rqF -- "$m" "$ROOT/agents/python/demo/fixtures" && hits+=("guid: $m")
  done < <(grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' <<< "$text" | sort -u)
  while IFS= read -r m; do [ -n "$m" ] && ! grep -qE "$ALLOWED_EMAIL" <<< "$m" && hits+=("email: $m"); done < <(grep -oE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' <<< "$text" | sort -u)
  while IFS= read -r m; do [ -n "$m" ] && hits+=("ip: $m"); done < <(grep -oE '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' <<< "$text" | sort -u)
  while IFS= read -r m; do [ -n "$m" ] && hits+=("path: $m"); done < <(grep -oE '/(Users|home)/[A-Za-z0-9._-]+' <<< "$text" | sort -u)
  if [ -n "${REDACT_WORDS:-}" ]; then
    IFS=',' read -r -a words <<< "$REDACT_WORDS"
    for w in "${words[@]}"; do [ -n "$w" ] && grep -qiF -- "$w" <<< "$text" && hits+=("word: $w"); done
  fi
  if [ ${#hits[@]} -gt 0 ]; then
    fail=1; echo "LEAK? $clip"; printf '  %s\n' "${hits[@]}"
  else
    echo "clean  $clip"
  fi
done
exit $fail
