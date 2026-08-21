"""One-shot migration: re-key committed replay fixtures under the current hash.

Every fixture stores its own raw ``request``, so a change to the hashing scheme
in ``shared/replay_client.py`` can be applied to the whole corpus *offline* —
no API credentials, no re-recording, and the recorded responses are never
touched, which is what keeps the ``call_id`` chain between consecutive turns
intact.

Written for the ``_normalize_for_hash`` change (issue #25), but it is scheme-
agnostic: it always recomputes with whatever ``_request_hash`` currently does,
so it is reusable the next time the key changes.

When two fixtures collapse onto one hash, they were the *same logical request*
recorded in different seed sessions — the exact duplication the normalization
was added to eliminate. Only one can survive, and *which* one matters: each
recorded a different model trajectory, and later turns were recorded against
one specific trajectory. Keep the wrong sibling and every fixture downstream of
the discarded one is stranded, which shows up much later as an unexplained
fixture miss rather than as an error here.

So the keeper is the sibling with the most consumers — other fixtures that
replay its response, either as a literal continuation of the conversation or,
for orchestrator chains, as specialist prose embedded in a tool result. mtime
is only the tiebreaker.

Usage::

    uv run python -m evals.rehash_fixtures --dry-run
    uv run python -m evals.rehash_fixtures
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from shared.replay_client import _request_hash

DEFAULT_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "replay"


def plan_rehash(fixtures_dir: Path) -> tuple[dict[str, list[Path]], list[Path]]:
    """Group every fixture by its recomputed hash.

    Returns ``(groups, unreadable)`` where ``groups`` maps the new hash to the
    files that now claim it, newest first.
    """
    groups: dict[str, list[Path]] = defaultdict(list)
    unreadable: list[Path] = []

    for path in sorted(fixtures_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            new_hash = _request_hash(data["request"])
        except (json.JSONDecodeError, KeyError, OSError):
            unreadable.append(path)
            continue
        groups[new_hash].append(path)

    consumers = _consumer_counts(fixtures_dir)
    for paths in groups.values():
        paths.sort(key=lambda p: (consumers.get(p.name, 0), p.stat().st_mtime), reverse=True)

    return dict(groups), unreadable


def _consumer_counts(fixtures_dir: Path) -> dict[str, int]:
    """How many other fixtures depend on each fixture's recorded response.

    A later turn embeds the earlier turn's response verbatim — as the assistant
    message it continues from, or (for the orchestrator) as the specialist prose
    that came back through ``call_specialist_agent`` and landed in a tool
    result. Either way it is a substring match against the raw request, which
    makes dependency computable offline with no database and no model.
    """
    responses: dict[str, list[str]] = {}
    requests: dict[str, str] = {}

    for path in sorted(fixtures_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        requests[path.name] = json.dumps(data.get("request", {}), sort_keys=True)
        texts: list[str] = []
        for message in data.get("response", {}).get("messages", []):
            for content in message.get("contents", []):
                text = content.get("text")
                # Short strings ("Sure!") would match everywhere; only a
                # substantial response identifies a trajectory.
                if isinstance(text, str) and len(text) >= 40:
                    texts.append(text)
        responses[path.name] = texts

    counts: dict[str, int] = {}
    for name, texts in responses.items():
        counts[name] = sum(
            1
            for other, blob in requests.items()
            if other != name and any(json.dumps(t)[1:-1] in blob for t in texts)
        )
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures-dir", type=Path, default=DEFAULT_FIXTURES_DIR)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without touching the filesystem.",
    )
    args = parser.parse_args(argv)

    fixtures_dir: Path = args.fixtures_dir
    if not fixtures_dir.is_dir():
        print(f"Not a directory: {fixtures_dir}", file=sys.stderr)
        return 1

    groups, unreadable = plan_rehash(fixtures_dir)
    total = sum(len(p) for p in groups.values()) + len(unreadable)

    renames: list[tuple[Path, Path]] = []
    drops: list[Path] = []
    for new_hash, paths in sorted(groups.items()):
        keeper, *duplicates = paths
        drops.extend(duplicates)
        if keeper.stem != new_hash:
            renames.append((keeper, fixtures_dir / f"{new_hash}.json"))

    print(f"Fixtures scanned:   {total}")
    print(f"Distinct requests:  {len(groups)}")
    print(f"To rename:          {len(renames)}")
    print(f"Duplicates to drop: {len(drops)}")
    if unreadable:
        print(f"Unreadable (left alone): {len(unreadable)}", file=sys.stderr)
        for path in unreadable:
            print(f"  ! {path.name}", file=sys.stderr)

    for old, new in renames:
        print(f"  {old.name} -> {new.name}")
    for path in drops:
        print(f"  drop {path.name} (duplicate of an existing request)")

    if args.dry_run:
        print("\nDry run — nothing written.")
        return 0

    for path in drops:
        path.unlink()

    # Rename via a temporary name first: a fixture's new hash can collide with
    # some *other* fixture's current filename, so renaming in place would
    # clobber a file this loop has not visited yet.
    staged: list[tuple[Path, Path]] = []
    for old, new in renames:
        tmp = old.with_suffix(".json.rehash-tmp")
        old.rename(tmp)
        staged.append((tmp, new))
    for tmp, new in staged:
        tmp.rename(new)

    print(f"\nRewrote {len(renames)} filename(s), removed {len(drops)} duplicate(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
