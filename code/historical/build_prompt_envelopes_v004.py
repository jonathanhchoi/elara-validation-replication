#!/usr/bin/env python3
"""Build v0.4 prompt envelopes: the v0.03 layout plus a free-text response mode.

Instrument v0.4 (researcher direction, 2026-08-11) removes forced multiple
choice for extraction items: a free-text item's visible payload carries
`response_mode: "free_text"` instead of `allowed_labels`, its response schema
types `label` as a bounded free string (the answer itself, or UNCERTAIN), and
answer equivalence is adjudicated after coding by `answer_equivalence_v001.py`.
Closed items keep the exact v0.03 payload shape (an `allowed_labels` list and
an enum-constrained response schema).

The record layout, canonical JSON, hashing, and rendering are the v0.03
functions, loaded from `build_prompt_envelopes_v003.py` by file path; this
module rebuilds `build_record` natively so every hash matches the emitted
bytes. All v0.4 envelopes stamp prompt contract `intercoder-visible-prompt-v002`
and embed `codebook_v004.md` by default.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
V003_BUILDER = HERE / "build_prompt_envelopes_v003.py"
V003_MODULE_NAME = "build_prompt_envelopes_v003_frozen_for_v004"
DEFAULT_CODEBOOK = HERE / "codebook_v004.md"
CONTRACT_VERSION = "intercoder-visible-prompt-v002"
FREE_TEXT_LABEL_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 120}
FREE_TEXT_MODE_NOTE = (
    "free_text: the label field carries the requested value itself, copied as the "
    "document states it, or UNCERTAIN"
)


class EnvelopeV4Error(RuntimeError):
    pass


def atomic_write_text(path: Path, text: str) -> None:
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


def load_v003(path: Path = V003_BUILDER) -> ModuleType:
    cached = sys.modules.get(V003_MODULE_NAME)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(V003_MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise EnvelopeV4Error(f"cannot load the v0.03 envelope builder: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[V003_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def is_free_text(task: dict[str, Any]) -> bool:
    return task["prompt"].get("response_mode") == "free_text"


def item_response_schema_v004(
    v003: ModuleType, task: dict[str, Any], base_schema: dict[str, Any]
) -> dict[str, Any]:
    schema = copy.deepcopy(base_schema)
    item_id = task["item_id"]
    material_ids = [row["id"] for row in task["prompt"]["materials"]]
    if not material_ids:
        raise EnvelopeV4Error(f"{item_id}: no prompt materials")
    schema["$id"] = f"https://example.invalid/elr/intercoder-response-v004/{item_id}.schema.json"
    schema["properties"]["item_id"] = {"const": item_id}
    if is_free_text(task):
        schema["properties"]["label"] = dict(FREE_TEXT_LABEL_SCHEMA)
    else:
        schema["properties"]["label"] = {"enum": task["prompt"]["allowed_labels"]}
    schema["properties"]["evidence_ids"]["minItems"] = 1
    schema["properties"]["evidence_ids"]["items"] = {"enum": material_ids}
    return schema


def build_record_v004(
    v003: ModuleType, task: dict[str, Any], codebook: str, base_schema: dict[str, Any]
) -> dict[str, Any]:
    item_id = task["item_id"]
    response_schema = item_response_schema_v004(v003, task, base_schema)
    task_payload: dict[str, Any] = {
        "item_id": item_id,
        "instruction": task["prompt"]["instruction"],
        "materials": task["prompt"]["materials"],
        "question": task["prompt"]["question"],
    }
    if is_free_text(task):
        task_payload["response_mode"] = FREE_TEXT_MODE_NOTE
    else:
        task_payload["allowed_labels"] = task["prompt"]["allowed_labels"]
    envelope = {
        "prompt_contract_version": CONTRACT_VERSION,
        "common_instruction": v003.COMMON_VISIBLE_INSTRUCTION,
        "codebook": codebook,
        "task": task_payload,
        "response_schema": response_schema,
    }
    canonical_envelope = v003.canonical_json(envelope)
    prompt_text = (
        "Process the following canonical single-item coding envelope exactly as instructed.\n"
        + canonical_envelope
    )
    provenance = task["provenance"]
    return {
        "item_id": item_id,
        "prompt_contract_version": CONTRACT_VERSION,
        "response_mode": "free_text" if is_free_text(task) else "closed",
        "source_tier": task["source_tier"],
        "external_api_eligible": provenance["external_api_eligible"],
        "human_review_status": provenance["human_review_status"],
        "task_payload_sha256": v003.sha256_text(v003.canonical_json(task_payload)),
        "response_schema_sha256": v003.sha256_text(v003.canonical_json(response_schema)),
        "canonical_envelope_sha256": v003.sha256_text(canonical_envelope),
        "prompt_sha256": v003.sha256_text(prompt_text),
        "response_schema": response_schema,
        "prompt_text": prompt_text,
    }


def build_records_v004(
    v003: ModuleType,
    tasks: list[dict[str, Any]],
    codebook: str,
    base_schema: dict[str, Any],
) -> list[dict[str, Any]]:
    item_ids = [task["item_id"] for task in tasks]
    if len(item_ids) != len(set(item_ids)):
        raise EnvelopeV4Error("duplicate item_id in the task file")
    return [build_record_v004(v003, task, codebook, base_schema) for task in tasks]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--codebook", type=Path, default=DEFAULT_CODEBOOK)
    parser.add_argument("--response-schema", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        v003 = load_v003()
        base_schema_path = (
            args.response_schema if args.response_schema is not None else v003.DEFAULT_RESPONSE_SCHEMA
        )
        records = build_records_v004(
            v003,
            v003.load_jsonl(args.tasks),
            args.codebook.read_text(encoding="utf-8"),
            v003.load_json(base_schema_path),
        )
        rendered = v003.render(records)
        count = len(rendered.split("\n")) - 1
        free = sum(1 for r in records if r["response_mode"] == "free_text")
        if args.check:
            if args.output.read_text(encoding="utf-8") != rendered:
                raise EnvelopeV4Error(f"prompt envelope file is stale: {args.output}")
            print(f"v004 envelopes PASS: {count} records ({free} free-text) in {args.output.name}")
        else:
            atomic_write_text(args.output, rendered)
            print(f"wrote {count} v004 envelopes ({free} free-text) to {args.output.name}")
    except Exception as exc:  # noqa: BLE001 - single CLI error surface
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
