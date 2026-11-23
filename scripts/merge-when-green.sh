#!/usr/bin/env bash
# Squash-merge a pull request only when it is provably green and unchanged.
#
# Usage: scripts/merge-when-green.sh <pr-number>
#
# Why the SHA check exists: a watcher once merged a PR at an old head, before a late push
# registered, and shipped the PR without its final fix. So the head SHA is recorded when the
# script starts, every poll must see that same SHA, and the merge itself is pinned to it with
# --match-head-commit (GitHub refuses the merge if the head moved after our last look).
#
# Merges only when, on two polls MWG_POLL_INTERVAL seconds apart, all of these hold:
#   * the head SHA equals the one recorded at start
#   * at least MWG_MIN_CHECKS checks exist, all complete, none failing (skipped and neutral are fine)
#   * the PR is mergeable (no conflicts)
# Otherwise it exits non-zero and says why. It never uses --admin and never retries a refusal.
#
# Environment (all optional):
#   MWG_GH             gh binary (default: gh)
#   MWG_POLL_INTERVAL  seconds between the two polls (default: 45)
#   MWG_MIN_CHECKS     minimum number of checks that must exist (default: 20)
#   MWG_POLLS          number of polls before merging (default: 2)
set -euo pipefail

GH="${MWG_GH:-gh}"
INTERVAL="${MWG_POLL_INTERVAL:-45}"
MIN_CHECKS="${MWG_MIN_CHECKS:-20}"
POLLS="${MWG_POLLS:-2}"

die() { echo "merge-when-green: REFUSED: $*" >&2; exit 1; }

[ "$#" -eq 1 ] || { echo "usage: $0 <pr-number>" >&2; exit 2; }
pr="$1"
[[ "$pr" =~ ^[0-9]+$ ]] || { echo "merge-when-green: not a PR number: $pr" >&2; exit 2; }
[[ "$MIN_CHECKS" =~ ^[0-9]+$ && "$POLLS" =~ ^[0-9]+$ && "$POLLS" -ge 1 ]] || { echo "merge-when-green: bad MWG_MIN_CHECKS or MWG_POLLS" >&2; exit 2; }

# Prints "<sha> <verdict>" where verdict is OK or a one-line reason. Reads gh's JSON on stdin.
evaluate() {
    python3 -c '
import json, sys
min_checks = int(sys.argv[1])
d = json.load(sys.stdin)
sha = d.get("headRefOid") or "unknown"
rollup = d.get("statusCheckRollup") or []
problems = []
if d.get("mergeable") != "MERGEABLE":
    problems.append("PR is not mergeable (state: %s); conflicts or unknown" % d.get("mergeable"))
if len(rollup) < min_checks:
    problems.append("only %d checks reported, need at least %d" % (len(rollup), min_checks))
ok_conclusions = {"SUCCESS", "SKIPPED", "NEUTRAL"}
for c in rollup:
    name = c.get("name") or c.get("context") or "?"
    if "status" in c:  # CheckRun
        if c.get("status") != "COMPLETED":
            problems.append("check %s is not complete (%s)" % (name, c.get("status")))
        elif c.get("conclusion") not in ok_conclusions:
            problems.append("check %s concluded %s" % (name, c.get("conclusion")))
    elif c.get("state") != "SUCCESS":  # StatusContext
        problems.append("status %s is %s" % (name, c.get("state")))
print(sha, "; ".join(problems) if problems else "OK")
' "$MIN_CHECKS"
}

fetch() { "$GH" pr view "$pr" --json headRefOid,mergeable,statusCheckRollup; }

start_sha="$("$GH" pr view "$pr" --json headRefOid --jq .headRefOid)" || die "could not read PR #$pr"
[ -n "$start_sha" ] || die "empty head SHA for PR #$pr"
echo "merge-when-green: PR #$pr head at start: $start_sha"

for ((i = 1; i <= POLLS; i++)); do
    out="$(fetch | evaluate)" || die "could not read checks for PR #$pr"
    sha="${out%% *}"
    verdict="${out#* }"
    [ "$sha" = "$start_sha" ] || die "head SHA changed during the watch ($start_sha -> $sha); checks watched are not the checks to merge"
    [ "$verdict" = "OK" ] || die "$verdict"
    echo "merge-when-green: poll $i/$POLLS ok at $sha"
    if [ "$i" -lt "$POLLS" ]; then sleep "$INTERVAL"; fi
done

"$GH" pr merge "$pr" --squash --match-head-commit "$start_sha"
echo "merge-when-green: merged PR #$pr at $start_sha"
