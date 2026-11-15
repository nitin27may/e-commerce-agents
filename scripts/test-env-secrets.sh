#!/usr/bin/env bash
# Shell-level check for scripts/lib/env-secrets.sh. No Docker, no network.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/lib/env-secrets.sh

fail() { echo "FAIL: $*" >&2; exit 1; }
get() { grep -E "^$2=" "$1" | tail -n 1 | cut -d= -f2-; }
dir="$(mktemp -d)"; trap 'rm -rf "$dir"' EXIT
HEX64='^[0-9a-f]{64}$'

# 1. Fresh copy of .env.example: all three replaced, other lines untouched.
cp .env.example "$dir/a.env"
out="$(ensure_local_secrets "$dir/a.env")"
for v in "${SECRET_VARS[@]}"; do
    val="$(get "$dir/a.env" "$v")"
    [[ "$val" =~ $HEX64 ]] || fail "$v not generated: '$val'"
    grep -qx "$v" <<<"$out" || fail "$v not reported"
done
[ "$(get "$dir/a.env" JWT_SECRET)" != "$(get "$dir/a.env" AGENT_SHARED_SECRET)" ] || fail "secrets identical"
diff <(grep -vE '^(JWT_SECRET|AGENT_SHARED_SECRET|OAUTH_SEED_KEY)=' .env.example) \
     <(grep -vE '^(JWT_SECRET|AGENT_SHARED_SECRET|OAUTH_SEED_KEY)=' "$dir/a.env") >/dev/null || fail "other lines changed"

# 2. Idempotent: a second run changes nothing and reports nothing.
cp "$dir/a.env" "$dir/a.before"
out="$(ensure_local_secrets "$dir/a.env")"
[ -z "$out" ] || fail "second run reported: $out"
cmp -s "$dir/a.env" "$dir/a.before" || fail "second run rewrote the file"

# 3. User-set values survive; a placeholder next to them is still replaced.
printf 'JWT_SECRET=my-own-jwt-secret-that-is-long-enough-1234\nAGENT_SHARED_SECRET=agent-internal-shared-secret\nOAUTH_SEED_KEY="user-seed"\n' > "$dir/b.env"
ensure_local_secrets "$dir/b.env" >/dev/null
[ "$(get "$dir/b.env" JWT_SECRET)" = "my-own-jwt-secret-that-is-long-enough-1234" ] || fail "user JWT_SECRET rewritten"
[ "$(get "$dir/b.env" OAUTH_SEED_KEY)" = '"user-seed"' ] || fail "user OAUTH_SEED_KEY rewritten"
[[ "$(get "$dir/b.env" AGENT_SHARED_SECRET)" =~ $HEX64 ]] || fail "placeholder not replaced"

# 4. Compose placeholder, empty value and CRLF endings count as placeholders;
#    a missing variable is appended (even with no trailing newline).
printf 'JWT_SECRET=change-me-in-production-use-random-256-bit-key\r\nAGENT_SHARED_SECRET=' > "$dir/c.env"
ensure_local_secrets "$dir/c.env" >/dev/null
for v in "${SECRET_VARS[@]}"; do
    [[ "$(get "$dir/c.env" "$v" | tr -d '\r')" =~ $HEX64 ]] || fail "$v not generated in c.env"
done
[ "$(grep -c '^OAUTH_SEED_KEY=' "$dir/c.env")" = 1 ] || fail "OAUTH_SEED_KEY duplicated"

# 5. Python fallback when openssl is absent.
mkdir "$dir/bin"
for t in python3 grep tail awk mktemp cat rm tr; do ln -s "$(command -v $t)" "$dir/bin/$t"; done
cp .env.example "$dir/d.env"
PATH="$dir/bin:/nonexistent" "$BASH" -c 'source scripts/lib/env-secrets.sh; ensure_local_secrets "$1" >/dev/null' _ "$dir/d.env" || fail "python fallback failed"
[[ "$(get "$dir/d.env" JWT_SECRET)" =~ $HEX64 ]] || fail "python fallback produced no secret"
[ "$(grep -c "^JWT_SECRET=" "$dir/d.env")" = 1 ] || fail "python fallback duplicated JWT_SECRET"

echo "env-secrets: all checks passed"
