#!/usr/bin/env python3
"""Compare two alias dictionaries and summarize what changed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalize_entry(value) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(v) for v in value)
    if isinstance(value, dict):
        if isinstance(value.get("law_id"), str):
            return (value["law_id"],)
        if isinstance(value.get("law_ids"), list):
            return tuple(str(v) for v in value["law_ids"])
    return (json.dumps(value, ensure_ascii=False, sort_keys=True),)


def compare_alias_dicts(old_path: str, new_path: str) -> dict:
    old = load_json(old_path)
    new = load_json(new_path)

    old_keys = set(old)
    new_keys = set(new)

    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)

    changed = []
    unchanged = 0
    for alias in sorted(old_keys & new_keys):
        old_value = normalize_entry(old[alias])
        new_value = normalize_entry(new[alias])
        if old_value == new_value:
            unchanged += 1
        else:
            changed.append(
                {
                    "alias": alias,
                    "old": old[alias],
                    "new": new[alias],
                }
            )

    return {
        "old_count": len(old),
        "new_count": len(new),
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
        "unchanged_count": unchanged,
        "added_sample": added[:50],
        "removed_sample": removed[:50],
        "changed_sample": changed[:50],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two alias dictionaries.")
    parser.add_argument("--old", required=True, help="Old alias dictionary JSON.")
    parser.add_argument("--new", required=True, help="New alias dictionary JSON.")
    parser.add_argument("--output", default=None, help="Optional JSON summary output path.")
    args = parser.parse_args()

    summary = compare_alias_dicts(args.old, args.new)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved summary to {args.output}")


if __name__ == "__main__":
    main()
