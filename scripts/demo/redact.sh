#!/usr/bin/env bash
# Mask account-identifying values in terminal output before it is recorded or saved: stdin -> stdout.
#
#   scripts/azure-up.sh --env eca-demo 2>&1 | scripts/demo/redact.sh
#   REDACT_WORDS="Contoso Prod,jane" scripts/demo/redact.sh < log
#
# Masks GUIDs (subscription, tenant, object and principal ids), email addresses other than the published demo
# accounts (docs/demo-guide.md: the *.demo@gmail.com staff and the seeded customers), IPv4 addresses, the variable part
# of Azure hostnames, and any extra words in REDACT_WORDS (comma separated: a subscription name, a user name).
# Line-buffered, so it works on a live stream. A recording is still checked frame by frame before it is committed.
set -euo pipefail

extra=""
if [ -n "${REDACT_WORDS:-}" ]; then
  IFS=',' read -r -a words <<< "$REDACT_WORDS"
  for w in "${words[@]}"; do
    [ -n "$w" ] || continue
    # Escape regex metacharacters in the word.
    esc="$(printf '%s' "$w" | sed -e 's/[][\.*^$/+?(){}|]/\\&/g')"
    extra="$extra;s/$esc/<redacted>/g"
  done
fi

# Emails first, in perl for the lookahead: keep the seeded demo accounts, mask any other address.
DEMO='(?:[a-z]+\.demo|alice\.johnson|bob\.smith|carol\.davis|dave\.wilson|emma\.brown)@gmail\.com'
DEMO="$DEMO" perl -pe 'BEGIN { $| = 1 } s{([A-Za-z0-9._%+-]+\@[A-Za-z0-9.-]+\.[A-Za-z]{2,})}{ my $e = $1; $e =~ /\A$ENV{DEMO}\z/ ? $e : "user\@example.com" }ge' |
  sed -l -E \
  -e 's/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/g' \
  -e 's/(^|[^0-9.])([0-9]{1,3}\.){3}[0-9]{1,3}([^0-9.]|$)/\1x.x.x.x\3/g' \
  -e 's/[a-z0-9-]+(\.[a-z0-9-]+)*\.azurecontainerapps\.io/<app>.azurecontainerapps.io/g' \
  -e 's/[a-z0-9-]+\.(postgres\.database\.azure\.com|vault\.azure\.net|openai\.azure\.com|cognitiveservices\.azure\.com|services\.ai\.azure\.com|azurecr\.io|onmicrosoft\.com)/<name>.\1/g' \
  -e "s/^//$extra"
