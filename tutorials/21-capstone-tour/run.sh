#!/usr/bin/env bash
# Chapter 21 — start the capstone stack with no API key and open the tour.
#
# A thin wrapper: everything real happens in scripts/dev.sh, which pulls the
# prebuilt images and layers docker-compose.nokey.yml (LLM_PROVIDER=replay, the
# demo-tour settings). Model answers are recorded, so only the suggested prompts
# in the chat answer; anything else says it was not recorded.
#
#   ./tutorials/21-capstone-tour/run.sh              # latest release images
#   IMAGE_TAG=main ./tutorials/21-capstone-tour/run.sh
#   ./tutorials/21-capstone-tour/run.sh --clean      # fresh volumes (recommended after a re-run)
#
# Extra arguments go to dev.sh unchanged. Docker with Compose v2 is required.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "$repo_root/scripts/dev.sh" --demo --no-key "$@"
