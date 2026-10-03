#!/usr/bin/env python3
"""Canonicalize free-text ratings so the frozen v0.04 agreement analysis runs unchanged.

The frozen estimators (`agreement_analysis_v004.py`) compare labels literally
and require every succeeded record's label to sit in the item's closed label
set. Instrument v0.4 free-text items violate both premises, so this
deterministic preprocessing derives, from a run's items file and rating
records:

- `rating_records_normalized.jsonl`: every free-text record's `label` replaced
  by its canonical form under the item's declared normalizer
  (`answer_equivalence_v001.canonicalize`); the raw label is preserved as
  `label_raw`. Closed-mode records pass through unchanged.
- `items_normalized.jsonl`: every free-text item's `prompt.allowed_labels`
  replaced by the realized canonical label space (all canonical labels observed
  for that item, plus the canonical gold and UNCERTAIN), so the frozen
  validity rule "label in allowed labels" is satisfied by construction while
  failures and malformed records remain failures.

Agreement semantics are unchanged in form: g(x, y) = 1 only when both codings
are valid terminal successes whose (now canonical) labels match. This file is
part of the frozen instrument; hash it into the run configuration alongside
`answer_equivalence_v001.py`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
EQUIV_PATH = HERE / "answer_equivalence_v001.py"


def load_equivalence():
    spec = importlib.util.spec_from_file_location("answer_equivalence_v001_frozen", EQUIV_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=Path, required=True)
    parser.add_argument("--ratings", type=Path, required=True)
    parser.add_argument("--out-items", type=Path, required=True)
    parser.add_argument("--out-ratings", type=Path, required=True)
    args = parser.parse_args(argv)
    equiv = load_equivalence()

    items = load_jsonl(args.items)
    ratings = load_jsonl(args.ratings)
    free_text: dict[str, dict[str, Any]] = {}
    for item in items:
        if item.get("prompt", {}).get("response_mode") == "free_text":
            key = item.get("adjudication", {}).get("answer_key") or {}
            normalizer = key.get("normalizer")
            if normalizer is None:
                print(f"ERROR: {item['item_id']}: free_text item without answer_key", file=sys.stderr)
                return 1
            free_text[item["item_id"]] = {"normalizer": normalizer, "key": key}

    observed: dict[str, set[str]] = {item_id: set() for item_id in free_text}
    out_ratings: list[dict[str, Any]] = []
    changed = 0
    for record in ratings:
        record = dict(record)
        item_id = record.get("item_id")
        if item_id in free_text and record.get("label") is not None:
            normalizer = free_text[item_id]["normalizer"]
            raw = str(record["label"])
            canonical = equiv.canonicalize(raw, normalizer)
            record["label_raw"] = raw
            record["label"] = canonical
            record["label_normalizer"] = normalizer
            if record.get("terminal_status") == "succeeded":
                observed[item_id].add(canonical)
            changed += 1
        out_ratings.append(record)

    out_items: list[dict[str, Any]] = []
    for item in items:
        item_id = item["item_id"]
        if item_id in free_text:
            item = json.loads(json.dumps(item, ensure_ascii=False))
            normalizer = free_text[item_id]["normalizer"]
            key = free_text[item_id]["key"]
            space = set(observed[item_id])
            space.add("UNCERTAIN")
            if key.get("gold_answer"):
                space.add(equiv.canonicalize(key["gold_answer"], normalizer))
            for alias in key.get("aliases") or []:
                space.add(equiv.canonicalize(alias, normalizer))
            item["prompt"]["allowed_labels"] = sorted(space)
            item["prompt"]["allowed_labels_provenance"] = (
                "realized canonical label space derived by normalize_ratings_v001.py; "
                "not part of the frozen visible prompt"
            )
        out_items.append(item)

    dump_jsonl(args.out_ratings, out_ratings)
    dump_jsonl(args.out_items, out_items)
    print(
        f"normalized {changed} free-text ratings across {len(free_text)} items; "
        f"wrote {args.out_ratings.name} and {args.out_items.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
