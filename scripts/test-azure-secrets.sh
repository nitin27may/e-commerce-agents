#!/usr/bin/env bash
# Offline check for scripts/azure/lib/secrets.sh and the preprovision --secrets-only path.
# No Azure, no azd, no network: the azd environment is replaced by a dotenv file.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/azure/lib/common.sh
source scripts/azure/lib/common.sh
# shellcheck source=scripts/azure/lib/secrets.sh
source scripts/azure/lib/secrets.sh

fail() { echo "FAIL: $*" >&2; exit 1; }
dir="$(mktemp -d)"; trap 'rm -rf "$dir"' EXIT
export AZURE_SECRET_STORE_FILE="$dir/azd.env"
HEX64='^[0-9a-f]{64}$'
ALL=(POSTGRES_ADMIN_PASSWORD JWT_SECRET AGENT_SHARED_SECRET A2A_IDENTITY_KEY OAUTH_SEED_KEY AUTH_SIGNING_KEY_ENCRYPTION_KEY SEED_STAFF_PASSWORD)

# 1. A fresh environment: everything is generated and reported.
out="$(ensure_azure_secrets)"
for v in "${ALL[@]}"; do grep -qx "$v" <<<"$out" || fail "$v not reported"; done

pg="$(env_get POSTGRES_ADMIN_PASSWORD)"
[ "${#pg}" -eq 32 ] || fail "postgres password length ${#pg}"
[[ "$pg" =~ ^[A-Za-z0-9]+$ ]] || fail "postgres password is not alphanumeric: $pg"
[[ "$pg" =~ [A-Z] && "$pg" =~ [a-z] && "$pg" =~ [0-9] ]] || fail "postgres password lacks a character class"

staff="$(env_get SEED_STAFF_PASSWORD)"
[ "${#staff}" -eq 20 ] || fail "staff password length ${#staff}"
[[ "$staff" =~ ^[A-Za-z0-9]+$ ]] || fail "staff password is not alphanumeric"
[[ "$staff" =~ [A-Z] && "$staff" =~ [a-z] && "$staff" =~ [0-9] ]] || fail "staff password lacks a character class"

for v in "${HEX_SECRET_VARS[@]}"; do
    [[ "$(env_get "$v")" =~ $HEX64 ]] || fail "$v is not 64 hex characters"
done
[ "$(env_get A2A_IDENTITY_KEY)" != "$(env_get AGENT_SHARED_SECRET)" ] || fail "A2A_IDENTITY_KEY equals AGENT_SHARED_SECRET"
[ "$(env_get JWT_SECRET)" != "$(env_get AGENT_SHARED_SECRET)" ] || fail "JWT_SECRET equals AGENT_SHARED_SECRET"

# 2. Idempotent: a second run reports nothing and rewrites nothing.
cp "$AZURE_SECRET_STORE_FILE" "$dir/before"
out="$(ensure_azure_secrets)"
[ -z "$out" ] || fail "second run reported: $out"
cmp -s "$AZURE_SECRET_STORE_FILE" "$dir/before" || fail "second run changed the store"

# 3. Never overwrite a value that is already set; fill only what is missing.
printf 'JWT_SECRET="my-own-jwt-secret"\nPOSTGRES_ADMIN_PASSWORD="HandPickedPw1"\n' >"$AZURE_SECRET_STORE_FILE"
out="$(ensure_azure_secrets)"
[ "$(env_get JWT_SECRET)" = "my-own-jwt-secret" ] || fail "JWT_SECRET overwritten"
[ "$(env_get POSTGRES_ADMIN_PASSWORD)" = "HandPickedPw1" ] || fail "POSTGRES_ADMIN_PASSWORD overwritten"
! grep -qx JWT_SECRET <<<"$out" || fail "JWT_SECRET reported as generated"
[[ "$(env_get AGENT_SHARED_SECRET)" =~ $HEX64 ]] || fail "missing secret not generated"

# 4. Many draws: classes and alphabet hold every time (the generator retries until all three appear).
for _ in $(seq 1 200); do
    p="$(generate_alnum 20)"
    [[ "${#p}" -eq 20 && "$p" =~ ^[A-Za-z0-9]+$ && "$p" =~ [A-Z] && "$p" =~ [a-z] && "$p" =~ [0-9] ]] || fail "bad draw: $p"
done

# 5. The Python fallback when openssl is absent.
mkdir "$dir/bin"
for t in python3 tr cat rm mv mktemp grep tail cut sed touch; do ln -s "$(command -v "$t")" "$dir/bin/$t"; done
: >"$AZURE_SECRET_STORE_FILE"
PATH="$dir/bin" "$BASH" -c '
    source scripts/azure/lib/common.sh; source scripts/azure/lib/secrets.sh
    ensure_azure_secrets >/dev/null' || fail "python fallback failed"
[[ "$(env_get JWT_SECRET)" =~ $HEX64 ]] || fail "python fallback produced no hex secret"
pg="$(env_get POSTGRES_ADMIN_PASSWORD)"
[[ "${#pg}" -eq 32 && "$pg" =~ ^[A-Za-z0-9]+$ ]] || fail "python fallback password malformed"

# 6. The hook itself, offline: secrets only, then again (nothing changes).
: >"$AZURE_SECRET_STORE_FILE"
scripts/azure/preprovision.sh --secrets-only 2>/dev/null
cp "$AZURE_SECRET_STORE_FILE" "$dir/hook-before"
scripts/azure/preprovision.sh --secrets-only 2>/dev/null
cmp -s "$AZURE_SECRET_STORE_FILE" "$dir/hook-before" || fail "preprovision rewrote existing secrets"
for v in "${ALL[@]}"; do [ -n "$(env_get "$v")" ] || fail "preprovision left $v unset"; done

# 7. preprovision refuses a floating image tag, with no Azure call.
: >"$AZURE_SECRET_STORE_FILE"
if scripts/azure/preprovision.sh --dry-run >/dev/null 2>&1; then fail "preprovision accepted an unset IMAGE_TAG"; fi
env_set IMAGE_TAG latest
if scripts/azure/preprovision.sh --dry-run >/dev/null 2>&1; then fail "preprovision accepted IMAGE_TAG=latest"; fi

echo "azure-secrets: all checks passed"
