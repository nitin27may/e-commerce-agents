#!/usr/bin/env bash
# Replay a timestamped log (one "<epoch seconds><TAB><line>" per line) at a speed-up, for terminal recordings of
# commands too slow to film in real time (scripts/demo/tapes/azure-up.tape). Gaps are divided by SPEED and capped
# at MAX_GAP seconds, so a ten-minute run plays in well under a minute.
#
#   scripts/demo/replay-log.sh azure-up.log [SPEED=40] [MAX_GAP=1.2]
set -euo pipefail
log="$1"; speed="${2:-40}"; max_gap="${3:-1.2}"
perl -e '
  use Time::HiRes qw(sleep); $| = 1;
  my ($speed, $max) = @ARGV[0, 1]; my $prev;
  while (my $l = <STDIN>) {
    my ($t, $text) = split(/\t/, $l, 2); next unless defined $text;
    if (defined $prev) { my $gap = ($t - $prev) / $speed; $gap = $max if $gap > $max; sleep($gap) if $gap > 0; }
    $prev = $t; print $text;
  }' "$speed" "$max_gap" < "$log"
