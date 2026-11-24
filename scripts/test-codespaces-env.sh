#!/usr/bin/env bash
# Shell-level check for .devcontainer/provider.sh and the way scripts/dev.sh reads injected variables.
# No Docker, no network. Uses fake values only.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=/dev/null
source .devcontainer/provider.sh

fail() { echo "FAIL: $*" >&2; exit 1; }
dir="$(mktemp -d)"; trap 'rm -rf "$dir"' EXIT
FAKE=fake-value-not-a-real-key
clear_vars() { unset AZURE_OPENAI_ENDPOINT AZURE_OPENAI_KEY LLM_BASE_URL OPENAI_API_KEY; }

# 1. Mode selection and precedence.
clear_vars
[ "$(codespaces_mode)" = replay ] || fail "nothing set should be replay"
OPENAI_API_KEY="" ; export OPENAI_API_KEY
[ "$(codespaces_mode)" = replay ] || fail "empty key should be replay"
OPENAI_API_KEY="sk-your-openai-api-key-here"
[ "$(codespaces_mode)" = replay ] || fail "placeholder key should be replay"
OPENAI_API_KEY=$FAKE
[ "$(codespaces_mode)" = openai ] || fail "key should be openai"
LLM_BASE_URL=http://example.invalid/v1; export LLM_BASE_URL
[ "$(codespaces_mode)" = openai-compatible ] || fail "base url should be openai-compatible"
AZURE_OPENAI_ENDPOINT=https://example.invalid; export AZURE_OPENAI_ENDPOINT
[ "$(codespaces_mode)" = openai-compatible ] || fail "azure endpoint without a key must not select azure"
AZURE_OPENAI_KEY=$FAKE; export AZURE_OPENAI_KEY
[ "$(codespaces_mode)" = azure ] || fail "endpoint and key should be azure"
[ "$(codespaces_llm_provider azure)" = azure ] || fail "azure provider"
[ "$(codespaces_llm_provider openai-compatible)" = openai ] || fail "compatible maps to openai"
[ "$(codespaces_llm_provider replay)" = replay ] || fail "replay provider"

# 2. .env is written without any secret value, and has the right provider lines.
mkdir "$dir/r1"; cp .env.minimal "$dir/r1/.env.minimal"
write_codespaces_env "$dir/r1" >"$dir/r1.out"
grep -qx 'LLM_PROVIDER=azure' "$dir/r1/.env" || fail "azure provider not written"
! grep -q 'EMBEDDING_PROVIDER=' "$dir/r1/.env" || fail "live provider must not pin embeddings to replay"
for v in "$FAKE" "$LLM_BASE_URL" "$AZURE_OPENAI_ENDPOINT"; do
    ! grep -qF -- "$v" "$dir/r1/.env" "$dir/r1.out" || fail "a value reached .env or the output"
done
! grep -q '^OPENAI_API_KEY=' "$dir/r1/.env" || fail "placeholder key line left active"

# 3. Rebuild with the secrets gone: the managed file flips back to replay, exactly one provider line.
clear_vars
write_codespaces_env "$dir/r1" >/dev/null
grep -qx 'LLM_PROVIDER=replay' "$dir/r1/.env" || fail "did not return to replay"
grep -qx 'EMBEDDING_PROVIDER=replay' "$dir/r1/.env" || fail "replay needs replay embeddings"
[ "$(grep -c '^LLM_PROVIDER=' "$dir/r1/.env")" = 1 ] || fail "LLM_PROVIDER duplicated"

# 4. A file without the marker is the user's: untouched even when a key appears.
mkdir "$dir/r2"; cp .env.minimal "$dir/r2/.env.minimal"
printf 'LLM_PROVIDER=anthropic\n' > "$dir/r2/.env"
OPENAI_API_KEY=$FAKE write_codespaces_env "$dir/r2" >/dev/null
[ "$(cat "$dir/r2/.env")" = "LLM_PROVIDER=anthropic" ] || fail "user .env was rewritten"

# 5. dev.sh reads an injected variable when .env lacks it, and the shell beats .env.
sed -n '/^env_value() {/,/^}/p' scripts/dev.sh > "$dir/env_value.sh"
[ -s "$dir/env_value.sh" ] || fail "could not extract env_value from dev.sh"
mkdir "$dir/r3"; printf 'LLM_PROVIDER=openai\nOPENAI_API_KEY=from-file\n' > "$dir/r3/.env"
got="$(cd "$dir/r3" && AZURE_OPENAI_KEY=$FAKE bash -c 'source "$1"; env_value AZURE_OPENAI_KEY' _ "$dir/env_value.sh")"
[ "$got" = "$FAKE" ] || fail "injected variable not read"
got="$(cd "$dir/r3" && OPENAI_API_KEY=from-shell bash -c 'source "$1"; env_value OPENAI_API_KEY' _ "$dir/env_value.sh")"
[ "$got" = "from-shell" ] || fail "shell value should beat .env"
# shellcheck disable=SC2016
got="$(cd "$dir/r3" && env -u OPENAI_API_KEY bash -c 'source "$1"; env_value OPENAI_API_KEY' _ "$dir/env_value.sh")"
[ "$got" = "from-file" ] || fail ".env fallback broken"

echo "codespaces-env: all checks passed"
