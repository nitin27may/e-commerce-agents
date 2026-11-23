#!/usr/bin/env bash
# Shell-level check for scripts/lib/profile.sh and the --profile flags of dev.sh / replay-evals.sh.
# No Docker, no network. The Python side of the same rules is agents/python/tests/test_run_profiles.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/lib/profile.sh

# Start from a clean slate: a caller (a pytest run, CI) may already export some of these.
unset LLM_PROVIDER LLM_MODEL LLM_BASE_URL OPENAI_API_KEY EMBEDDING_BASE_URL LLM_TEMPERATURE NEEDS_YOU RUN_PROFILE || true

fail() { echo "FAIL: $*" >&2; exit 1; }
dir="$(mktemp -d)"; trap 'rm -rf "$dir"' EXIT
mkdir "$dir/profiles"
cat > "$dir/profiles/demo.env" <<'EOF'
# profile: demo
# @summary: test profile
LLM_PROVIDER=openai
LLM_MODEL=qwen2.5:14b
LLM_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
EMBEDDING_BASE_URL=https://example.test/v1
LLM_TEMPERATURE=
NEEDS_YOU=<set-me>
EOF
export PROFILE_DIR="$dir/profiles"
export ENV_FILE="$dir/.env"
printf 'LLM_MODEL=from-dotenv\nOPENAI_API_KEY=sk-real-key-in-dotenv\nLLM_PROVIDER=azure\nNEEDS_YOU=supplied\n' > "$ENV_FILE"

# 1. list and validity; a name that is not a profile never reaches the filesystem.
[ "$(list_profiles)" = "demo" ] || fail "list_profiles: $(list_profiles)"
profile_valid demo || fail "demo not valid"
for bad in "../x" "demo/../demo" "DEMO" "" "demo.env" "/etc/passwd" "nope"; do
    ! profile_valid "$bad" || fail "'$bad' accepted"
done
msg="$(profile_refuse nope 2>&1)"
[[ "$msg" == *"valid profiles: demo"* ]] || fail "refusal does not list the names: $msg"

# 2. precedence: shell > profile > .env. Run in a subshell so exports do not leak.
out="$(
    export LLM_MODEL=from-shell
    apply_profile demo containers >/dev/null
    echo "model=$LLM_MODEL provider=$LLM_PROVIDER key=$OPENAI_API_KEY url=$LLM_BASE_URL emb=$EMBEDDING_BASE_URL run=$RUN_PROFILE"
    echo "temp_set=${LLM_TEMPERATURE+yes}"
    echo "kept=${PROFILE_KEPT[*]-} applied_has_provider=$([[ " ${PROFILE_APPLIED[*]-} " == *" LLM_PROVIDER "* ]] && echo y)"
    echo "overrides=${PROFILE_OVERRIDES[*]-}"
)"
grep -q 'model=from-shell' <<<"$out" || fail "shell value overridden: $out"
grep -q 'provider=openai' <<<"$out" || fail "profile did not beat .env: $out"
grep -q 'key=ollama' <<<"$out" || fail "real key not replaced by the local placeholder: $out"
grep -q 'url=http://host.docker.internal:11434/v1' <<<"$out" || fail "container rewrite missing: $out"
grep -q 'emb=https://example.test/v1' <<<"$out" || fail "remote host was rewritten: $out"
grep -q 'run=demo' <<<"$out" || fail "RUN_PROFILE not set: $out"
grep -q 'temp_set=yes' <<<"$out" || fail "an empty profile value must still be exported"
grep -q 'kept=LLM_MODEL' <<<"$out" || fail "kept list wrong: $out"
grep -q 'LLM_PROVIDER (.env: azure -> openai)' <<<"$out" || fail "override not reported: $out"
grep -q 'sk-real' <<<"$out" && fail "a secret value was printed"
grep -q 'OPENAI_API_KEY' <<<"$(grep '^overrides=' <<<"$out")" || fail "secret override should be named"
! grep -q 'key (.env' <<<"$out" || fail "secret override printed a value"

# 3. without the container flag a host URL stays put.
out="$(apply_profile demo >/dev/null; echo "$LLM_BASE_URL")"
[ "$out" = "http://localhost:11434/v1" ] || fail "host url rewritten without the flag: $out"

# 4. <set-me>: skipped when .env or the shell has it, refused when neither does.
out="$(apply_profile demo >/dev/null; echo "${NEEDS_YOU+set}")"
[ -z "$out" ] || fail "<set-me> was exported"
printf 'LLM_MODEL=x\n' > "$ENV_FILE"
if ( apply_profile demo >/dev/null 2>"$dir/err" ); then fail "unresolved <set-me> accepted"; fi
grep -q 'NEEDS_YOU' "$dir/err" || fail "unresolved name not reported"
( export NEEDS_YOU=1; apply_profile demo >/dev/null ) || fail "shell value should satisfy <set-me>"

# 5. the flags: unknown profile is refused by both scripts before any Docker call, listing the valid names.
unset PROFILE_DIR ENV_FILE
for script in scripts/dev.sh scripts/replay-evals.sh; do
    if "$script" --profile no-such-profile >"$dir/o" 2>&1; then fail "$script accepted an unknown profile"; fi
    grep -q 'ollama' "$dir/o" || fail "$script did not list valid profiles: $(cat "$dir/o")"
done
# replay-evals is the keyless gate: any real profile would be silently ignored, so it is refused.
if scripts/replay-evals.sh --profile ollama >"$dir/o" 2>&1; then fail "replay-evals accepted --profile ollama"; fi
grep -q 'would ignore' "$dir/o" || fail "replay-evals refusal does not say why: $(cat "$dir/o")"
# dev.sh: --no-key with a non-keyless profile is a contradiction.
if scripts/dev.sh --no-key --profile ollama >"$dir/o" 2>&1; then fail "dev.sh accepted --no-key with --profile ollama"; fi
grep -q 'cannot be combined' "$dir/o" || fail "dev.sh contradiction not explained: $(cat "$dir/o")"
# --help lists the profiles and exits 0.
help_text="$(scripts/dev.sh --help)"
[[ "$help_text" == *"--profile"* && "$help_text" == *"ollama"* ]] || fail "dev.sh --help does not mention --profile and the names"

# 6. every shipped profile applies cleanly on an empty environment (placeholders aside).
for name in $(list_profiles); do
    ( apply_profile "$name" >/dev/null 2>&1 ) || echo "note: $name has unresolved <set-me> values (expected for templates)"
done

echo "profiles: all checks passed"
