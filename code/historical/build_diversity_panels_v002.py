#!/usr/bin/env python3
"""Build the diversity-pilot panels (v0.02) and their researcher audit sheet.

Revision r2: presence and move source files are the v002 real-opinion rebuilds;
the other six families are unchanged v001 sources.

Two panels are produced for the tiered-repeats design proposed in
`diversity_expansion_proposal_v001.md`:

- `diversity_objective_panel_v001.jsonl` (255 rows): the 21 objective-family
  rows of the frozen intensive panel, byte-preserved, followed by the 234 new
  objective items from the seven-family diversity sources in canonical family
  order (presence, move, extract, contract, role, relation, statute).
- `diversity_interpretive_panel_v001.jsonl` (45 rows): the 12
  interpretive-application rows of the frozen intensive panel, byte-preserved,
  followed by the 33 new interpretive items in source-file order.

The audit sheet renders only the 267 new items and pins both panels' SHA-256.
This builder performs structural sanity checks only; schema and family
validation belong to `validate_tasks.py`, run against each panel with
`task_schema_v003.json` and `task_family_spec_v004.json`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
DEFAULT_BASE = HERE / "intensive_panel_items_v001.jsonl"
DEFAULT_OBJECTIVE_OUTPUT = HERE / "diversity_objective_panel_v002.jsonl"
DEFAULT_INTERP_OUTPUT = HERE / "diversity_interpretive_panel_v001.jsonl"
DEFAULT_REVIEW = HERE / "diversity_panel_review_v002.md"

INTERP_FAMILY = "interpretive_application"
EXPECTED_BASE_ITEMS = 33
EXPECTED_BASE_OBJECTIVE = 21
EXPECTED_BASE_INTERP = 12

# Canonical family order and per-family expectations for the new items.
SOURCE_FILES: list[tuple[str, str, int]] = [
    ("diversity_items_source_presence_v002.json", "opinion_feature_presence", 42),
    ("diversity_items_source_move_v002.json", "interpretive_move_coding", 42),
    ("diversity_items_source_extract_v001.json", "structured_value_extraction", 42),
    ("diversity_items_source_contract_v001.json", "contract_clause_coding", 27),
    ("diversity_items_source_role_v001.json", "judicial_document_role", 27),
    ("diversity_items_source_relation_v001.json", "cross_document_relation_coding", 27),
    ("diversity_items_source_statute_v001.json", "statutory_entailment", 27),
    ("diversity_items_source_interp_v001.json", INTERP_FAMILY, 33),
]

TIER_LABELS = {
    "public_paraphrase": "familiar twin (public_paraphrase)",
    "synthetic_structural_twin": "novel twin (synthetic_structural_twin)",
    "synthetic_original": "original synthetic (synthetic_original)",
    "public_recent_minimized": "minimized public record (public_recent_minimized)",
}

OBJECTIVE_CHECKLIST = [
    "- [ ] Gold defensible: the recorded gold label follows deterministically from the materials under the frozen codebook.",
    "- [ ] Distractors fair: no unintended second defensible answer; candidates and hard negatives are planted in the text.",
    "- [ ] Wording clean: no typos, leaked answer cues, or unintended ambiguity in instruction, materials, question, or labels.",
]

INTERP_CHECKLIST = [
    "- [ ] Structure fidelity: the scenario instantiates the intended text-purpose tension (twins) or the intended difficulty (originals).",
    "- [ ] Novel twin genuinely unfamiliar: the synthetic surface domain is not itself a well-known hypothetical.",
    "- [ ] No unintended gold answer: rule, purpose, and facts leave the interpretive question genuinely contestable.",
    "- [ ] Wording clean: no typos, leaked answer cues, or unintended ambiguity in materials, question, or labels.",
]


class PanelBuildError(RuntimeError):
    pass


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically publish `text` at `path` via a same-directory temp file and os.replace."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_base_lines(path: Path) -> tuple[list[str], list[str]]:
    """Split the frozen intensive panel into byte-preserved objective and interpretive lines."""
    text = path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        raise PanelBuildError(f"{path}: base panel must end with a newline")
    lines = [piece + "\n" for piece in text.split("\n")[:-1]]
    if len(lines) != EXPECTED_BASE_ITEMS:
        raise PanelBuildError(
            f"{path}: expected {EXPECTED_BASE_ITEMS} base rows, found {len(lines)}"
        )
    objective: list[str] = []
    interp: list[str] = []
    for line_number, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PanelBuildError(f"{path}:{line_number}: {exc}") from exc
        if not isinstance(row, dict) or "item_id" not in row or "family_id" not in row:
            raise PanelBuildError(f"{path}:{line_number}: expected a task object")
        (interp if row["family_id"] == INTERP_FAMILY else objective).append(line)
    if len(objective) != EXPECTED_BASE_OBJECTIVE or len(interp) != EXPECTED_BASE_INTERP:
        raise PanelBuildError(
            f"{path}: expected {EXPECTED_BASE_OBJECTIVE} objective and "
            f"{EXPECTED_BASE_INTERP} interpretive base rows, found "
            f"{len(objective)} and {len(interp)}"
        )
    return objective, interp


def load_source(path: Path, family_id: str, expected: int) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        source = json.load(handle)
    items = source.get("items")
    if not isinstance(items, list) or len(items) != expected:
        raise PanelBuildError(f"{path}: expected exactly {expected} source items")
    for item in items:
        if not isinstance(item, dict) or "item_id" not in item:
            raise PanelBuildError(f"{path}: every source item needs an item_id")
        if item.get("family_id") != family_id:
            raise PanelBuildError(
                f"{path}: item {item.get('item_id')} has family "
                f"{item.get('family_id')!r}, expected {family_id!r}"
            )
    return source


def render_rows(items: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in items
    )


def check_unique_ids(*panels: str) -> None:
    seen: set[str] = set()
    for panel in panels:
        for line in panel.split("\n")[:-1]:
            item_id = json.loads(line)["item_id"]
            if item_id in seen:
                raise PanelBuildError(f"duplicate item_id across panels: {item_id}")
            seen.add(item_id)


def _material_lines(item: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for material in item["prompt"]["materials"]:
        kind = str(material["kind"]).replace("_", " ").capitalize()
        lines.append(f"- **{kind} ({material['id']}):** {material['text']}")
    return lines


def _item_section(number: int, item: dict[str, Any]) -> list[str]:
    tier = TIER_LABELS.get(item["source_tier"], item["source_tier"])
    labels = ", ".join(f"`{label}`" for label in item["prompt"]["allowed_labels"])
    adjudication = item["adjudication"]
    gold = adjudication.get("gold_label")
    gold_line = (
        f"- **Gold label:** `{gold}` - {adjudication['rationale']}"
        if gold is not None
        else "- **Gold label:** none (subjective family)"
    )
    lines = [
        "",
        f"### Item {number} - `{item['item_id']}`",
        "",
        f"- **Tier:** {tier}",
        f"- **Authoring note:** {item['provenance']['authoring_note']}",
    ]
    if item.get("twin_set_id"):
        lines.append(f"- **Twin set:** `{item['twin_set_id']}`")
    if item["provenance"].get("source_locator"):
        lines.append(f"- **Source locator:** {item['provenance']['source_locator']}")
    lines.append(f"- **Instruction:** {item['prompt']['instruction']}")
    lines.extend(_material_lines(item))
    lines.extend(
        [
            f"- **Question:** {item['prompt']['question']}",
            f"- **Allowed labels:** {labels}",
            gold_line,
            "",
            "Audit checklist (leave unchecked until the researcher has verified each point):",
            "",
        ]
    )
    lines.extend(
        INTERP_CHECKLIST if item["family_id"] == INTERP_FAMILY else OBJECTIVE_CHECKLIST
    )
    return lines


def render_review(
    sources: list[dict[str, Any]],
    objective_sha256: str,
    interp_sha256: str,
    objective_name: str,
    interp_name: str,
) -> str:
    total_new = sum(len(source["items"]) for source in sources)
    lines = [
        "# Diversity pilot panels v0.02: researcher audit sheet for the 267 new items",
        "",
        "> **Status: revision r2, drafted by the model on 2026-08-11; the opinion-passage families (presence, move) are rebuilt on verbatim excerpts from real published opinions filed on or after 2026-06-01, retrieved from CourtListener (see sources/courtlistener_opinions_2026-08/). Researcher audit of every new item below is required before any live call.**",
        "",
        f"- **Objective panel:** `{objective_name}` (255 items: the 21 objective intensive-panel rows byte-preserved, then the 234 new objective items in canonical family order).",
        f"- **Objective panel sha256:** `{objective_sha256}`",
        f"- **Interpretive panel:** `{interp_name}` (45 items: the 12 interpretive intensive-panel rows byte-preserved, then the 33 new interpretive items in source-file order).",
        f"- **Interpretive panel sha256:** `{interp_sha256}`",
        f"- **New items rendered below:** {total_new}, drawn from the eight `diversity_items_source_*_v001.json` files.",
        "- **Approval mechanism:** pasting the diversity handoff prompt (to be issued with the runbooks) constitutes the researcher's approval of exactly these panel versions, identified by the SHA-256 values pinned above. Do not paste the handoff prompt until the audit is complete.",
        "- Editing any item invalidates this sheet and the pinned hashes; rebuild with `build_diversity_panels_v001.py` and re-audit.",
        "",
        "## Panel composition",
        "",
        "| Family | Carried from intensive | New | Total |",
        "|---|---|---|---|",
    ]
    carried = {
        "opinion_feature_presence": 3,
        "interpretive_move_coding": 3,
        "structured_value_extraction": 3,
        "contract_clause_coding": 3,
        "judicial_document_role": 3,
        "cross_document_relation_coding": 3,
        "statutory_entailment": 3,
        INTERP_FAMILY: 12,
    }
    for source in sources:
        family = source["family_id"]
        new = len(source["items"])
        lines.append(f"| {family} | {carried[family]} | {new} | {carried[family] + new} |")
    lines.append(f"| **Total** | **33** | **{total_new}** | **{33 + total_new}** |")
    item_number = 0
    for source in sources:
        family = source["family_id"]
        lines.extend(
            [
                "",
                f"## Family: `{family}` ({len(source['items'])} new items)",
                "",
                f"> {source['purpose']}",
            ]
        )
        for item in source["items"]:
            item_number += 1
            lines.extend(_item_section(item_number, item))
    lines.extend(
        [
            "",
            "## Audit record",
            "",
            "- **Researcher:**",
            "- **Audit date:**",
            "- **Decision:** approve (paste the diversity handoff prompt) / revise (new source versions required)",
            "- **Notes:**",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--objective-output", type=Path, default=DEFAULT_OBJECTIVE_OUTPUT)
    parser.add_argument("--interp-output", type=Path, default=DEFAULT_INTERP_OUTPUT)
    parser.add_argument("--review-output", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        base_objective, base_interp = load_base_lines(args.base)
        sources: list[dict[str, Any]] = []
        new_objective: list[dict[str, Any]] = []
        new_interp: list[dict[str, Any]] = []
        for filename, family_id, expected in SOURCE_FILES:
            source = load_source(HERE / filename, family_id, expected)
            sources.append(source)
            (new_interp if family_id == INTERP_FAMILY else new_objective).extend(
                source["items"]
            )
        objective_panel = "".join(base_objective) + render_rows(new_objective)
        interp_panel = "".join(base_interp) + render_rows(new_interp)
        check_unique_ids(objective_panel, interp_panel)
        review = render_review(
            sources,
            sha256_text(objective_panel),
            sha256_text(interp_panel),
            args.objective_output.name,
            args.interp_output.name,
        )
        objective_rows = len(objective_panel.split("\n")) - 1
        interp_rows = len(interp_panel.split("\n")) - 1
        if args.check:
            if args.objective_output.read_text(encoding="utf-8") != objective_panel:
                raise PanelBuildError(f"stale generated panel: {args.objective_output}")
            if args.interp_output.read_text(encoding="utf-8") != interp_panel:
                raise PanelBuildError(f"stale generated panel: {args.interp_output}")
            if args.review_output.read_text(encoding="utf-8") != review:
                raise PanelBuildError(f"stale generated review sheet: {args.review_output}")
            print(
                f"diversity panels v0.01 PASS: {objective_rows} objective rows, "
                f"{interp_rows} interpretive rows; review sheet current"
            )
        else:
            atomic_write_text(args.objective_output, objective_panel)
            atomic_write_text(args.interp_output, interp_panel)
            atomic_write_text(args.review_output, review)
            print(
                f"wrote {objective_rows} rows to {args.objective_output.name}, "
                f"{interp_rows} rows to {args.interp_output.name}, and the audit sheet "
                f"to {args.review_output.name}"
            )
    except (OSError, ValueError, KeyError, PanelBuildError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
