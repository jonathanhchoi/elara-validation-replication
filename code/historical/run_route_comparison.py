#!/usr/bin/env python3
"""Route-comparison launcher: sealed schedule, gates, budget stops, dispatch, merge, analysis.

Implements `design_v004.md` Section 2 (routes and provider-default model configuration),
Section 9 (the route-neutral, authorization-based transmission gate, enforced at prepare and
re-verified by hash before every dispatch), Section 10 (seeded interleaved block scheduling,
hash sealing before any call, linked retries, blinded operational output, and the four stop
rules), and Section 11 staging with the stage ceilings of `budget_plan_v003.json`.
Section 12 names this file as the "Route launcher, scheduler, gates, budget enforcement"
component.

Byte-identical prompts are obtained only through
`build_prompt_envelopes_v003.route_visible_payload` with the stored `prompt_sha256`
re-verified at dispatch (design Section 2). The sub-agent arm is materialized as a
`unit_fanout` run of one-unit assignments (design Section 2; `workflow/shared/`
observation-fanout discipline).

Matched reasoning effort (researcher decision of 2026-08-05 for the intensive pilot):
`prepare --effort` pins one explicit reasoning-effort level for BOTH arms of the run and
seals it into `config.json` (already part of the manifest hash chain). When set, the
Anthropic call adds `output_config={"effort": <value>}` and the OpenAI Responses call adds
`reasoning={"effort": <value>}` (both shapes verified against anthropic 0.109.1
`OutputConfigParam.effort` and openai 2.21.0 `Reasoning.effort`); nothing else changes.
When null (every instrument-pilot run), requests are byte-identical to the pre-effort
behavior, so existing runs remain replayable and analyzable unchanged. Every rating
record carries `requested_effort` (value or null); sub-agent records inherit the config
value because the orchestrating session pins it, while `reported_model_verified` stays
false there.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
KIT_ROOT = HERE.parents[1]

DEFAULT_ENVELOPES = HERE / "prompt_envelopes_v003.jsonl"
DEFAULT_ITEMS = HERE / "development_items_v003.jsonl"
DEFAULT_SPEC = HERE / "task_family_spec_v004.json"
DEFAULT_BUDGET_PLAN = HERE / "budget_plan_v003.json"
DEFAULT_RATING_SCHEMA = HERE / "rating_record_schema_v002.json"
DEFAULT_SOURCES_ROOT = HERE / "sources"
AGREEMENT_MODULE_PATH = HERE / "agreement_analysis_v004.py"
BOOTSTRAP_MODULE_PATH = HERE / "cluster_bootstrap_v001.py"

RECORD_VERSION = "0.2"
COMPARISONS = ("anthropic", "openai")
STAGES = ("instrument_pilot", "intensive_pilot", "diversity_pilot", "confirmatory")
ROUTES = ("api", "subagent")
# Matched reasoning-effort levels (researcher decision 2026-08-05). Every level is a
# valid literal for both anthropic 0.109.1 output_config.effort and openai 2.21.0
# reasoning.effort; None means provider default and byte-identical requests.
REASONING_EFFORTS = ("high", "medium", "low", "xhigh")
MAX_LINKED_ATTEMPTS = 2
RETRY_SLEEP_SECONDS = 2.0
EST_INPUT_TOKENS = 3000
EST_OUTPUT_TOKENS = 600
MIN_REALIZED_FOR_ESTIMATE = 5
BLOCK_FAILURE_FRACTION = 0.25
DEFAULT_SMOKE_CALLS = 3
DEFAULT_BOOTSTRAP_B = 2000
MAX_TOKENS_PER_CALL = 2048
ERROR_DISPLAY_CHARS = 120

# Terminal-status map for collect-subagent (design Sections 3 and 10: every non-success
# terminal slot stays in the denominator as a failure).
SUBAGENT_STATUS_MAP = {
    "succeeded": "succeeded",
    "schema_failed": "parse_failure",
    "quote_failed": "parse_failure",
    "refused": "refused",
    "unreadable": "terminal_failure",
    "wrong_document": "terminal_failure",
    "exhausted_retry": "terminal_failure",
    "worker_error": "terminal_failure",
}


class LauncherError(RuntimeError):
    """A fail-closed gate, integrity, budget, or reconciliation error."""


def _import_by_path(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    if not path.is_file():
        raise LauncherError(f"required module is missing: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise LauncherError(f"cannot build an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_ENVELOPE_MODULE = _import_by_path(
    "build_prompt_envelopes_v003", HERE / "build_prompt_envelopes_v003.py"
)
PromptBuildError = _ENVELOPE_MODULE.PromptBuildError
route_visible_payload = _ENVELOPE_MODULE.route_visible_payload
canonical_json = _ENVELOPE_MODULE.canonical_json
sha256_text = _ENVELOPE_MODULE.sha256_text

_UNIT_FANOUT = _import_by_path("unit_fanout", KIT_ROOT / "scripts" / "unit_fanout.py")
FanoutError = _UNIT_FANOUT.FanoutError
atomic_json = _UNIT_FANOUT.atomic_json
sha256_file = _UNIT_FANOUT.sha256_file


# ---------------------------------------------------------------------------
# Shared helpers


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LauncherError(f"cannot read valid JSON from {path}: {exc}") from exc


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LauncherError(f"cannot read {path}: {exc}") from exc
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise LauncherError(f"{path}:{line_number}: {exc}") from exc
    if not rows:
        raise LauncherError(f"file has no records: {path}")
    return rows


def _write_jsonl_exclusive(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def _validator(schema: dict[str, Any]) -> Any:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - environment-dependent guard
        raise LauncherError("install jsonschema to run the launcher") from exc
    return Draft202012Validator(schema)


def _sanitized_schema_errors(schema: dict[str, Any], instance: Any) -> list[str]:
    """Value-free validator messages (design Section 10: label values must not leak)."""
    messages = []
    for error in sorted(_validator(schema).iter_errors(instance), key=str):
        messages.append(f"{error.json_path or '$'}: violates '{error.validator}'")
    return messages


def _sanitize_error(kind: str, detail: str = "") -> str:
    detail = " ".join(str(detail).split())
    return f"{kind}: {detail[:200]}" if detail else kind


def extract_first_json_object(text: str) -> Any:
    """Return json.loads of the whole text, else of the first balanced {...} substring."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    if not isinstance(text, str):
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


# ---------------------------------------------------------------------------
# Prepare (design Sections 9 and 10)


def _load_stage_row(budget_plan: dict[str, Any], stage: str) -> dict[str, Any]:
    for row in budget_plan.get("stages", []):
        if row.get("stage_id") == stage:
            return row
    raise LauncherError(f"budget plan has no stage row for {stage!r}")


def _load_authorizations(sources_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Load every sources/*/authorization_v001.json (design Section 9)."""
    records: list[tuple[Path, dict[str, Any]]] = []
    if sources_root.is_dir():
        for path in sorted(sources_root.glob("*/authorization_v001.json")):
            record = load_json(path)
            if not isinstance(record, dict):
                raise LauncherError(f"authorization record must be an object: {path}")
            records.append((path.resolve(), record))
    return records


def _privacy_gate(
    envelopes: list[dict[str, Any]],
    items_by_id: dict[str, dict[str, Any]],
    authorizations: list[tuple[Path, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Route-neutral transmission gate at prepare (design Section 9, decision D5).

    Every envelope whose provenance carries `external_api_eligible` not True requires an
    authorization record whose source_id matches the item's provenance.source_id and whose
    covered_routes include every route the schedule will dispatch (both routes, because
    every block interleaves both). Fails closed before anything is created.
    """
    authorized: dict[str, dict[str, Any]] = {}
    for record in envelopes:
        if record.get("external_api_eligible") is True:
            continue
        item_id = record["item_id"]
        provenance = items_by_id[item_id].get("provenance") or {}
        source_id = provenance.get("source_id")
        if not source_id:
            raise LauncherError(
                f"privacy gate: restricted item {item_id} has no provenance.source_id "
                "to match an authorization record"
            )
        covering: tuple[Path, dict[str, Any]] | None = None
        for path, auth in authorizations:
            covered_routes = auth.get("covered_routes") or []
            if auth.get("source_id") == source_id and all(
                route in covered_routes for route in ROUTES
            ):
                covering = (path, auth)
                break
        if covering is None:
            raise LauncherError(
                f"privacy gate: no authorization record covers source {source_id!r} for "
                f"routes {list(ROUTES)} (item {item_id}); refusing to prepare"
            )
        authorized[item_id] = {
            "source_id": source_id,
            "covered_routes": list(covering[1].get("covered_routes") or []),
            "authorization_path": str(covering[0]),
        }
    return authorized


def _build_schedule(
    comparison: str,
    item_ids: list[str],
    reps_api: int,
    reps_subagent: int,
    blocks: int,
    seed: int,
) -> list[dict[str, Any]]:
    """Seeded schedule: round-robin by item, dealt to blocks, shuffled within block.

    Design Section 10: every block interleaves both routes; blocks are balanced to within
    one slot. Deterministic for a fixed seed (no timestamps in the schedule).
    """

    def route_slots(route: str, reps: int) -> list[tuple[str, str, int]]:
        return [
            (item_id, route, rep)
            for rep in range(1, reps + 1)
            for item_id in item_ids
        ]

    api_slots = route_slots("api", reps_api)
    subagent_slots = route_slots("subagent", reps_subagent)
    if len(api_slots) < blocks or len(subagent_slots) < blocks:
        raise LauncherError(
            "more blocks than slots on a route; every block must interleave both routes"
        )
    rows_by_block: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, (item_id, route, rep) in enumerate(api_slots):
        rows_by_block[index % blocks].append(
            {"item_id": item_id, "route": route, "coder_rep": rep}
        )
    offset = len(api_slots) % blocks
    for index, (item_id, route, rep) in enumerate(subagent_slots):
        rows_by_block[(index + offset) % blocks].append(
            {"item_id": item_id, "route": route, "coder_rep": rep}
        )

    rng = random.Random(seed)
    schedule: list[dict[str, Any]] = []
    position = 0
    for block in range(blocks):
        block_rows = rows_by_block[block]
        rng.shuffle(block_rows)
        for row in block_rows:
            schedule.append(
                {
                    "slot_id": (
                        f"{comparison}-{row['item_id']}-{row['route']}"
                        f"-r{row['coder_rep']:02d}"
                    ),
                    "comparison": comparison,
                    "item_id": row["item_id"],
                    "route": row["route"],
                    "coder_rep": row["coder_rep"],
                    "block": block,
                    "position": position,
                }
            )
            position += 1

    sizes = Counter(row["block"] for row in schedule)
    if max(sizes.values()) - min(sizes.values()) > 1:
        raise LauncherError("internal error: blocks are not balanced to within one slot")
    for block in range(blocks):
        block_routes = {row["route"] for row in schedule if row["block"] == block}
        if block_routes != set(ROUTES):
            raise LauncherError(f"internal error: block {block} does not contain both routes")
    return schedule


def prepare_run(
    run_dir: Path,
    *,
    comparison: str,
    stage: str,
    envelopes_path: Path,
    items_path: Path,
    reps_api: int,
    reps_subagent: int,
    blocks: int,
    seed: int,
    approve_stage_ceiling: bool = False,
    reasoning_effort: str | None = None,
    spec_path: Path = DEFAULT_SPEC,
    budget_plan_path: Path = DEFAULT_BUDGET_PLAN,
    sources_root: Path = DEFAULT_SOURCES_ROOT,
) -> dict[str, Any]:
    """Build the sealed run: schedule, approvals, privacy gate, sub-agent arm, manifest.

    Design Section 10 (schedule hash-sealed before any call), Section 9 (route-neutral
    gate at prepare; nothing is created when the gate fails), Section 11 stage ceilings
    from `budget_plan_v003.json`. `reasoning_effort` (researcher decision 2026-08-05)
    pins one matched explicit effort level for both arms; it is stored in `config.json`,
    which is hash-sealed into the run manifest, so the sealed inputs cover it. None
    keeps provider defaults and byte-identical requests.
    """
    run_dir = run_dir.resolve()
    envelopes_path = envelopes_path.resolve()
    items_path = items_path.resolve()
    spec_path = spec_path.resolve()
    budget_plan_path = budget_plan_path.resolve()
    sources_root = sources_root.resolve()
    rating_schema_path = DEFAULT_RATING_SCHEMA.resolve()

    if comparison not in COMPARISONS:
        raise LauncherError(f"comparison must be one of {COMPARISONS}")
    if stage not in STAGES:
        raise LauncherError(f"stage must be one of {STAGES}")
    if blocks < 1:
        raise LauncherError("blocks must be a positive integer")
    if seed < 0:
        raise LauncherError("seed must be a nonnegative integer")
    if reasoning_effort is not None and reasoning_effort not in REASONING_EFFORTS:
        raise LauncherError(
            f"reasoning effort must be omitted or one of {REASONING_EFFORTS}"
        )

    spec = load_json(spec_path)
    minimum = spec.get("minimum_ratings_per_item") or {}
    if reps_api < int(minimum.get("api", 2)) or reps_subagent < int(
        minimum.get("subagent", 2)
    ):
        raise LauncherError(
            "repeats per route must meet the frozen minimum_ratings_per_item"
        )
    vendor = (spec.get("vendor_pairs") or {}).get(comparison)
    if not isinstance(vendor, dict):
        raise LauncherError(f"spec has no vendor pair for {comparison!r}")
    budget_plan = load_json(budget_plan_path)
    stage_row = _load_stage_row(budget_plan, stage)
    price_key = f"{comparison}_{vendor['api_model']}"
    if price_key not in (budget_plan.get("price_assumptions") or {}):
        raise LauncherError(f"budget plan has no price assumptions for {price_key!r}")

    envelopes = load_jsonl(envelopes_path)
    items = load_jsonl(items_path)
    envelopes_by_id = {record["item_id"]: record for record in envelopes}
    items_by_id = {row["item_id"]: row for row in items}
    if len(envelopes_by_id) != len(envelopes) or len(items_by_id) != len(items):
        raise LauncherError("duplicate item_id in envelopes or items")
    if set(envelopes_by_id) != set(items_by_id):
        raise LauncherError("envelopes and items files do not cover the same item_ids")
    for record in envelopes:
        item = items_by_id[record["item_id"]]
        flag = (item.get("provenance") or {}).get("external_api_eligible")
        if record.get("external_api_eligible") != flag:
            raise LauncherError(
                f"external_api_eligible mismatch between envelope and item "
                f"{record['item_id']}; the envelope was built from different items"
            )
        if sha256_text(record["prompt_text"]) != record["prompt_sha256"]:
            raise LauncherError(f"prompt_sha256 mismatch for item {record['item_id']}")
        if sha256_text(canonical_json(record["response_schema"])) != record[
            "response_schema_sha256"
        ]:
            raise LauncherError(
                f"response_schema_sha256 mismatch for item {record['item_id']}"
            )

    # Privacy gate before anything is created (design Section 9; fail closed).
    authorizations = _load_authorizations(sources_root)
    authorized_restricted = _privacy_gate(envelopes, items_by_id, authorizations)

    item_ids = [row["item_id"] for row in items]
    schedule = _build_schedule(comparison, item_ids, reps_api, reps_subagent, blocks, seed)

    if run_dir.exists() and any(run_dir.iterdir()):
        raise LauncherError(f"run directory is not empty: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)

    schedule_path = run_dir / "schedule.jsonl"
    _write_jsonl_exclusive(schedule_path, schedule)

    approvals = {
        "stage": stage,
        "stage_ceiling_usd": float(stage_row["stage_ceiling_usd"]),
        "cumulative_ceiling_usd": float(stage_row["maximum_cumulative_spend_usd"]),
        "status": "researcher_approved" if approve_stage_ceiling else "pending",
        "approved_on": utc_now() if approve_stage_ceiling else None,
    }
    approvals_path = run_dir / "approvals.json"
    atomic_json(approvals_path, approvals)

    config = {
        "comparison": comparison,
        "stage": stage,
        "seed": seed,
        "blocks": blocks,
        "reps": {"api": reps_api, "subagent": reps_subagent},
        "models": {
            "api_model": vendor["api_model"],
            "subagent_requested_model": vendor["subagent_requested_model"],
            "subagent_route": vendor.get("subagent_route"),
        },
        "reasoning_effort": reasoning_effort,
        "price_key": price_key,
        "envelopes": {"path": str(envelopes_path), "sha256": sha256_file(envelopes_path)},
        "items": {"path": str(items_path), "sha256": sha256_file(items_path)},
        "design_spec": {"path": str(spec_path), "sha256": sha256_file(spec_path)},
        "budget_plan": {
            "path": str(budget_plan_path),
            "sha256": sha256_file(budget_plan_path),
        },
        "rating_schema": {
            "path": str(rating_schema_path),
            "sha256": sha256_file(rating_schema_path),
        },
        "sources_root": str(sources_root),
        "authorized_restricted_items": authorized_restricted,
        "est_input_tokens": EST_INPUT_TOKENS,
        "est_output_tokens": EST_OUTPUT_TOKENS,
    }
    config_path = run_dir / "config.json"
    atomic_json(config_path, config)

    # Sub-agent arm: one unit per subagent slot (design Section 2; observation-fanout).
    result_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["item_id", "label"],
        "properties": {
            "item_id": {"enum": sorted(item_ids)},
            "label": {"type": "string", "minLength": 1, "maxLength": 200},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"},
        },
        "additionalProperties": True,
    }
    result_schema_path = run_dir / "subagent_result_schema.json"
    atomic_json(result_schema_path, result_schema)

    units = []
    for row in schedule:
        if row["route"] != "subagent":
            continue
        payload = route_visible_payload(envelopes_by_id[row["item_id"]], "subagent")
        units.append(
            {
                "unit_id": row["slot_id"],
                "assignment_id": row["slot_id"],
                "attempt": 1,
                "payload": {
                    "slot_id": row["slot_id"],
                    "item_id": row["item_id"],
                    "block": row["block"],
                    "prompt_sha256": payload["prompt_sha256"],
                    "response_schema_sha256": payload["response_schema_sha256"],
                    "prompt_text": payload["prompt_text"],
                    "response_schema": payload["response_schema"],
                    "instructions": (
                        "read the payload prompt_text, produce ONLY the JSON result "
                        "object matching response_schema"
                    ),
                },
            }
        )
    fanout_spec = {
        "contract_version": _UNIT_FANOUT.CONTRACT_VERSION,
        "run_id": f"{comparison}-{stage}-s{seed}",
        "assignment_kind": "route_comparison_coding",
        "frozen_inputs": [
            {"role": "envelopes", "path": str(envelopes_path), "sha256": sha256_file(envelopes_path)},
            {"role": "design_spec", "path": str(spec_path), "sha256": sha256_file(spec_path)},
            {"role": "schedule", "path": str(schedule_path.resolve()), "sha256": sha256_file(schedule_path)},
            {
                "role": "result_schema",
                "path": str(result_schema_path.resolve()),
                "sha256": sha256_file(result_schema_path),
            },
        ],
        "units": units,
    }
    fanout_spec_path = run_dir / "subagent_fanout_spec.json"
    atomic_json(fanout_spec_path, fanout_spec)
    subagent_run_dir = run_dir / "subagent_run"
    try:
        _UNIT_FANOUT.prepare(fanout_spec_path, subagent_run_dir)
    except FanoutError as exc:
        raise LauncherError(f"sub-agent arm preparation failed: {exc}") from exc

    (run_dir / "api_archive").mkdir()
    (run_dir / "api_records").mkdir()
    (run_dir / "subagent_records").mkdir()

    driver_path = Path(__file__).resolve()
    sealed_files: dict[str, dict[str, str]] = {}

    def seal(role: str, path: Path) -> None:
        sealed_files[role] = {"path": str(path.resolve()), "sha256": sha256_file(path)}

    seal("driver", driver_path)
    seal("envelopes", envelopes_path)
    seal("items", items_path)
    seal("design_spec", spec_path)
    seal("budget_plan", budget_plan_path)
    seal("rating_schema", rating_schema_path)
    seal("schedule", schedule_path)
    seal("approvals", approvals_path)
    seal("config", config_path)
    seal("subagent_result_schema", result_schema_path)
    seal("subagent_fanout_spec", fanout_spec_path)
    seal("subagent_run_manifest", subagent_run_dir / "run_manifest.json")
    for index, (path, _record) in enumerate(authorizations):
        seal(f"authorization_{index:02d}_{path.parent.name}", path)

    manifest = {
        "launcher_design": "design_v004.md",
        "comparison": comparison,
        "stage": stage,
        "created_utc": utc_now(),
        "expected_slots": len(schedule),
        "expected_by_route": dict(Counter(row["route"] for row in schedule)),
        "subagent_run_dir": str(subagent_run_dir.resolve()),
        "sealed_files": sealed_files,
    }
    manifest_path = run_dir / "run_manifest.json"
    atomic_json(manifest_path, manifest)
    atomic_json(run_dir / "seal.json", {"run_manifest_sha256": sha256_file(manifest_path)})
    return {
        "run_dir": str(run_dir),
        "slots": len(schedule),
        "by_route": manifest["expected_by_route"],
        "blocks": blocks,
        "approval_status": approvals["status"],
        "reasoning_effort": reasoning_effort,
        "authorized_restricted_items": len(authorized_restricted),
    }


# ---------------------------------------------------------------------------
# Integrity (design Section 10: the schedule is hash-sealed; drift fails closed)


def verify_integrity(run_dir: Path) -> dict[str, Any]:
    """Recheck every sealed control file; called first by every other subcommand."""
    run_dir = run_dir.resolve()
    seal = load_json(run_dir / "seal.json")
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.is_file() or sha256_file(manifest_path) != seal.get(
        "run_manifest_sha256"
    ):
        raise LauncherError("run manifest changed after preparation")
    manifest = load_json(manifest_path)
    for role, entry in sorted(manifest["sealed_files"].items()):
        path = Path(entry["path"])
        if not path.is_file() or sha256_file(path) != entry["sha256"]:
            raise LauncherError(f"sealed control file drifted: {role} ({path})")
    try:
        _UNIT_FANOUT.verify_run_integrity(Path(manifest["subagent_run_dir"]))
    except FanoutError as exc:
        raise LauncherError(f"sub-agent run integrity failed: {exc}") from exc
    return manifest


def _load_run_state(run_dir: Path) -> dict[str, Any]:
    manifest = verify_integrity(run_dir)
    sealed = manifest["sealed_files"]
    config = load_json(Path(sealed["config"]["path"]))
    approvals = load_json(Path(sealed["approvals"]["path"]))
    schedule = load_jsonl(Path(sealed["schedule"]["path"]))
    return {
        "manifest": manifest,
        "config": config,
        "approvals": approvals,
        "schedule": schedule,
    }


def _record_path(run_dir: Path, route: str, slot_id: str) -> Path:
    folder = "api_records" if route == "api" else "subagent_records"
    return run_dir / folder / f"{slot_id}.json"


def _stopfiles(run_dir: Path) -> list[str]:
    return sorted(path.name for path in run_dir.glob("stopfile_*.json"))


def _write_stopfile(run_dir: Path, reason: str, payload: dict[str, Any]) -> Path:
    path = run_dir / f"stopfile_{reason}.json"
    atomic_json(path, {"reason": reason, "written_utc": utc_now(), **payload})
    return path


# ---------------------------------------------------------------------------
# Cost model (design Section 10 stop rule b; budget_plan_v003 automatic stop rules)


def _prices(config: dict[str, Any]) -> dict[str, float]:
    budget_plan = load_json(Path(config["budget_plan"]["path"]))
    return budget_plan["price_assumptions"][config["price_key"]]


def _usage_cost_usd(prices: dict[str, Any], usage: dict[str, Any]) -> float:
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    details = usage.get("input_tokens_details") or {}
    cached = int(details.get("cached_tokens") or 0)
    cached = min(cached, input_tokens)
    input_price = float(prices["input_usd_per_million_tokens"])
    cached_price = float(prices.get("cached_input_usd_per_million_tokens", input_price))
    output_price = float(prices["output_usd_per_million_tokens"])
    return (
        (input_tokens - cached) * input_price
        + cached * cached_price
        + output_tokens * output_price
    ) / 1_000_000


def _realized_costs(run_dir: Path, prices: dict[str, Any]) -> dict[str, Any]:
    """Realized spend over every archived attempt (attempts cost money even when failed)."""
    total = 0.0
    with_usage: list[float] = []
    attempts = 0
    for path in sorted((run_dir / "api_archive").glob("*.json")):
        attempts += 1
        raw = load_json(path)
        usage = (raw.get("response") or {}).get("usage")
        if isinstance(usage, dict):
            cost = _usage_cost_usd(prices, usage)
            total += cost
            with_usage.append(cost)
    if len(with_usage) >= MIN_REALIZED_FOR_ESTIMATE:
        per_call = sum(with_usage) / len(with_usage)
    else:
        per_call = (
            EST_INPUT_TOKENS * float(prices["input_usd_per_million_tokens"])
            + EST_OUTPUT_TOKENS * float(prices["output_usd_per_million_tokens"])
        ) / 1_000_000
    return {
        "realized_usd": total,
        "per_call_estimate_usd": per_call,
        "archived_attempts": attempts,
        "attempts_with_usage": len(with_usage),
    }


def _projected_total(run_dir: Path, prices: dict[str, Any], remaining_slots: int) -> dict[str, Any]:
    costs = _realized_costs(run_dir, prices)
    costs["remaining_api_slots"] = remaining_slots
    costs["projected_usd"] = costs["realized_usd"] + remaining_slots * costs[
        "per_call_estimate_usd"
    ]
    return costs


# ---------------------------------------------------------------------------
# Vendor clients (design Section 2: provider defaults, no sampling overrides)


def _config_effort(config: dict[str, Any]) -> str | None:
    """Sealed reasoning effort for this run; absent key (pre-effort runs) means None."""
    effort = config.get("reasoning_effort")
    if effort is not None and effort not in REASONING_EFFORTS:
        raise LauncherError(
            f"config reasoning_effort must be null or one of {REASONING_EFFORTS}"
        )
    return effort


def _make_anthropic_client() -> Any:
    """Real Anthropic client factory; tests monkeypatch this module attribute."""
    try:
        import anthropic
    except ImportError as exc:
        raise LauncherError("install the anthropic package before a live run") from exc
    if "PILOT_ANTHROPIC_API_KEY" not in os.environ:
        raise LauncherError("PILOT_ANTHROPIC_API_KEY is not set in this environment")
    return anthropic.Anthropic(api_key=os.environ["PILOT_ANTHROPIC_API_KEY"])


def _make_openai_client() -> Any:
    """Real OpenAI client factory; tests monkeypatch this module attribute."""
    try:
        import openai
    except ImportError as exc:
        raise LauncherError("install the openai package before a live run") from exc
    if "PILOT_OPENAI_API_KEY" not in os.environ:
        raise LauncherError("PILOT_OPENAI_API_KEY is not set in this environment")
    return openai.OpenAI(api_key=os.environ["PILOT_OPENAI_API_KEY"])


def _call_vendor(
    comparison: str,
    client: Any,
    model: str,
    prompt_text: str,
    reasoning_effort: str | None = None,
) -> Any:
    """One stateless call; no sampling overrides ever (design Section 2, decision D4).

    Claude Opus 5 and GPT-5.6 Sol reject sampling overrides, so no temperature or top_p
    is ever sent. With `reasoning_effort` None (every instrument-pilot run) no reasoning
    or output configuration is sent either and the request is byte-identical to the
    pre-effort launcher. With an explicit effort (researcher decision 2026-08-05,
    matched across both arms) exactly one extra field is sent: Anthropic
    `output_config={"effort": ...}` (no thinking parameter), OpenAI Responses
    `reasoning={"effort": ...}`. Shapes verified against the installed SDKs
    (anthropic 0.109.1 OutputConfigParam, openai 2.21.0 Reasoning).
    """
    if comparison == "anthropic":
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": MAX_TOKENS_PER_CALL,
            "messages": [{"role": "user", "content": prompt_text}],
        }
        if reasoning_effort is not None:
            kwargs["output_config"] = {"effort": reasoning_effort}
        return client.messages.create(**kwargs)
    kwargs = {
        "model": model,
        "max_output_tokens": MAX_TOKENS_PER_CALL,
        "store": False,
        "input": prompt_text,
    }
    if reasoning_effort is not None:
        kwargs["reasoning"] = {"effort": reasoning_effort}
    return client.responses.create(**kwargs)


def _response_dump(response: Any) -> Any:
    for method in ("model_dump", "to_dict"):
        dump = getattr(response, method, None)
        if callable(dump):
            try:
                return dump(mode="json") if method == "model_dump" else dump()
            except TypeError:
                try:
                    return dump()
                except Exception:  # pragma: no cover - defensive
                    break
            except Exception:  # pragma: no cover - defensive
                break
    return {"repr": repr(response)[:4000]}


def _usage_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    result: dict[str, Any] = {}
    for field in ("input_tokens", "output_tokens"):
        value = getattr(usage, field, None)
        if value is not None:
            result[field] = int(value)
    details = getattr(usage, "input_tokens_details", None)
    if details is not None:
        cached = details.get("cached_tokens") if isinstance(details, dict) else getattr(
            details, "cached_tokens", None
        )
        if cached is not None:
            result["input_tokens_details"] = {"cached_tokens": int(cached)}
    return result or None


def _normalize_anthropic_response(response: Any) -> dict[str, Any]:
    """Pure normalization of an Anthropic Messages response (testable without the SDK)."""
    parts: list[str] = []
    for block in getattr(response, "content", None) or []:
        block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if block_type == "text":
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", "")
            parts.append(text or "")
    return {
        "reported_model": getattr(response, "model", None),
        "refusal": getattr(response, "stop_reason", None) == "refusal",
        "text": "".join(parts),
        "usage": _usage_dict(getattr(response, "usage", None)),
        "raw": _response_dump(response),
    }


def _normalize_openai_response(response: Any) -> dict[str, Any]:
    """Pure normalization of an OpenAI Responses API response."""
    text = getattr(response, "output_text", None)
    refusal = False
    if text is None:
        parts: list[str] = []
        for item in getattr(response, "output", None) or []:
            for piece in getattr(item, "content", None) or []:
                piece_type = piece.get("type") if isinstance(piece, dict) else getattr(piece, "type", None)
                if piece_type == "refusal":
                    refusal = True
                elif piece_type == "output_text":
                    value = piece.get("text") if isinstance(piece, dict) else getattr(piece, "text", "")
                    parts.append(value or "")
        text = "".join(parts)
    return {
        "reported_model": getattr(response, "model", None),
        "refusal": refusal,
        "text": text or "",
        "usage": _usage_dict(getattr(response, "usage", None)),
        "raw": _response_dump(response),
    }


def _normalize_response(comparison: str, response: Any) -> dict[str, Any]:
    if comparison == "anthropic":
        return _normalize_anthropic_response(response)
    return _normalize_openai_response(response)


# ---------------------------------------------------------------------------
# Rating records (rating_record_schema_v002.json)


def _build_rating_record(
    *,
    comparison: str,
    item_id: str,
    route: str,
    coder_rep: int,
    attempt: int,
    terminal_status: str,
    label: str | None,
    evidence_ids: list[str] | None,
    rationale: str | None,
    prompt_sha256: str,
    response_schema_sha256: str,
    requested_model: str,
    requested_effort: str | None,
    reported_model: str | None,
    reported_model_verified: bool,
    block: int,
    latency_ms: float | None,
    usage: dict[str, Any] | None,
    error: str | None,
    raw_archive_path: str,
    rating_schema: dict[str, Any],
) -> dict[str, Any]:
    record = {
        "record_version": RECORD_VERSION,
        "comparison": comparison,
        "item_id": item_id,
        "route": route,
        "coder_rep": coder_rep,
        "attempt": attempt,
        "terminal_status": terminal_status,
        "label": label,
        "evidence_ids": evidence_ids,
        "rationale": rationale,
        "prompt_sha256": prompt_sha256,
        "response_schema_sha256": response_schema_sha256,
        "requested_model": requested_model,
        "requested_effort": requested_effort,
        "reported_model": reported_model,
        "reported_model_verified": reported_model_verified,
        "timestamp_utc": utc_now(),
        "block": block,
        "latency_ms": latency_ms,
        "usage": usage,
        "error": error,
        "raw_archive_path": raw_archive_path,
    }
    problems = _sanitized_schema_errors(rating_schema, record)
    if problems:
        raise LauncherError(
            f"internal error: rating record for {record['item_id']} slot violates "
            f"rating_record_schema_v002: {'; '.join(problems[:3])}"
        )
    return record


# ---------------------------------------------------------------------------
# run-api and smoke-test (design Sections 2 and 10)


def _next_attempt(run_dir: Path, slot_id: str) -> int:
    numbers = [
        int(path.stem.rsplit("attempt", 1)[1])
        for path in (run_dir / "api_archive").glob(f"{slot_id}.attempt*.json")
    ]
    return max(numbers, default=0) + 1


def _archive_attempt(
    run_dir: Path, slot_id: str, attempt: int, payload: dict[str, Any]
) -> Path:
    path = run_dir / "api_archive" / f"{slot_id}.attempt{attempt}.json"
    atomic_json(path, payload)
    return path


def _api_payload(
    record: dict[str, Any], authorized_restricted: dict[str, Any]
) -> dict[str, Any]:
    """Obtain the API-route payload strictly through `route_visible_payload`.

    The v0.03 accessor closes the API branch whenever `external_api_eligible` is not
    True; design v0.04 Section 9 (decision D5) supersedes that flag-only rule with the
    route-neutral authorization gate, which `prepare_run` enforced and whose records are
    hash-sealed. For an authorized restricted item the byte-identical payload is obtained
    through the accessor's route-neutral branch; unauthorized items stay closed.
    """
    try:
        return route_visible_payload(record, "api")
    except PromptBuildError as exc:
        if record.get("item_id") in authorized_restricted:
            return route_visible_payload(record, "subagent")
        raise LauncherError(f"transmission gate closed at dispatch: {exc}") from exc


def _dispatch_api_slot(
    run_dir: Path,
    *,
    comparison: str,
    row: dict[str, Any],
    envelope: dict[str, Any],
    client: Any,
    requested_model: str,
    reasoning_effort: str | None,
    prices: dict[str, Any],
    stage_ceiling: float,
    rating_schema: dict[str, Any],
    authorized_restricted: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch one pending api-route slot with one linked retry (design Section 10).

    Retry policy: at most 2 attempts per slot for transport or parse failure; the
    terminal attempt fills the slot. Every attempt is archived raw before it is
    interpreted. Realized spend is re-checked after every attempt and the run aborts
    the moment it crosses the approved stage ceiling.
    """
    slot_id = row["slot_id"]
    payload = _api_payload(envelope, authorized_restricted)
    if payload["prompt_sha256"] != envelope["prompt_sha256"] or (
        sha256_text(payload["prompt_text"]) != payload["prompt_sha256"]
    ):
        raise LauncherError(f"prompt hash re-verification failed for slot {slot_id}")

    def finalize(
        *,
        attempt: int,
        terminal_status: str,
        label: str | None = None,
        evidence_ids: list[str] | None = None,
        rationale: str | None = None,
        reported_model: str | None = None,
        latency_ms: float | None = None,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
        raw_archive_path: str = "",
    ) -> dict[str, Any]:
        record = _build_rating_record(
            comparison=comparison,
            item_id=row["item_id"],
            route="api",
            coder_rep=row["coder_rep"],
            attempt=min(attempt, MAX_LINKED_ATTEMPTS),
            terminal_status=terminal_status,
            label=label,
            evidence_ids=evidence_ids,
            rationale=rationale,
            prompt_sha256=payload["prompt_sha256"],
            response_schema_sha256=payload["response_schema_sha256"],
            requested_model=requested_model,
            requested_effort=reasoning_effort,
            reported_model=reported_model,
            reported_model_verified=True,
            block=row["block"],
            latency_ms=latency_ms,
            usage=usage,
            error=error,
            raw_archive_path=raw_archive_path,
            rating_schema=rating_schema,
        )
        atomic_json(_record_path(run_dir, "api", slot_id), record)
        return record

    def ceiling_crossed() -> bool:
        return _realized_costs(run_dir, prices)["realized_usd"] > stage_ceiling

    def abort_midblock() -> LauncherError:
        return LauncherError(
            "realized spend crossed the approved stage ceiling mid-block; run aborted "
            "(budget_plan automatic stop rule)"
        )

    while True:
        attempt = _next_attempt(run_dir, slot_id)
        if attempt > MAX_LINKED_ATTEMPTS:
            previous = sorted(
                (run_dir / "api_archive").glob(f"{slot_id}.attempt*.json")
            )
            record = finalize(
                attempt=MAX_LINKED_ATTEMPTS,
                terminal_status="transport_error",
                error=_sanitize_error(
                    "transport_error",
                    "linked attempts exhausted in a previous aborted invocation",
                ),
                raw_archive_path=str(previous[-1]) if previous else "unavailable",
            )
            return record
        meta = {
            "slot_id": slot_id,
            "comparison": comparison,
            "item_id": row["item_id"],
            "route": "api",
            "coder_rep": row["coder_rep"],
            "block": row["block"],
            "attempt": attempt,
            "requested_model": requested_model,
            "prompt_sha256": payload["prompt_sha256"],
            "response_schema_sha256": payload["response_schema_sha256"],
            "started_utc": utc_now(),
        }
        start = time.perf_counter()
        try:
            response = _call_vendor(
                comparison,
                client,
                requested_model,
                payload["prompt_text"],
                reasoning_effort,
            )
        except Exception as exc:  # transport/SDK failure: one linked retry after 2s
            latency_ms = (time.perf_counter() - start) * 1000.0
            archive_path = _archive_attempt(
                run_dir,
                slot_id,
                attempt,
                {
                    "request": meta,
                    "transport_error": {
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:2000],
                        "latency_ms": latency_ms,
                    },
                },
            )
            if attempt >= MAX_LINKED_ATTEMPTS:
                record = finalize(
                    attempt=attempt,
                    terminal_status="transport_error",
                    latency_ms=latency_ms,
                    error=_sanitize_error(type(exc).__name__, str(exc)),
                    raw_archive_path=str(archive_path),
                )
                if ceiling_crossed():
                    raise abort_midblock()
                return record
            if ceiling_crossed():
                raise abort_midblock()
            time.sleep(RETRY_SLEEP_SECONDS)
            continue

        latency_ms = (time.perf_counter() - start) * 1000.0
        normalized = _normalize_response(comparison, response)
        archive_path = _archive_attempt(
            run_dir,
            slot_id,
            attempt,
            {"request": meta, "response": normalized["raw"], "latency_ms": latency_ms},
        )

        reported = normalized["reported_model"]
        if not (isinstance(reported, str) and reported.startswith(requested_model)):
            _write_stopfile(
                run_dir,
                "model_drift",
                {
                    "slot_id": slot_id,
                    "requested_model": requested_model,
                    "reported_model": reported,
                    "archive_path": str(archive_path),
                },
            )
            raise LauncherError(
                "reported model drifted from the frozen identifier; stop-file written "
                "(design Section 10 stop rule a)"
            )

        crossed = ceiling_crossed()
        if normalized["refusal"]:
            record = finalize(
                attempt=attempt,
                terminal_status="refused",
                reported_model=reported,
                latency_ms=latency_ms,
                usage=normalized["usage"],
                error=_sanitize_error("refused", "provider refusal stop_reason"),
                raw_archive_path=str(archive_path),
            )
            if crossed:
                raise abort_midblock()
            return record

        parsed = extract_first_json_object(normalized["text"])
        problems = (
            ["response text did not contain a valid JSON object"]
            if parsed is None
            else _sanitized_schema_errors(envelope["response_schema"], parsed)
        )
        if not problems:
            record = finalize(
                attempt=attempt,
                terminal_status="succeeded",
                label=parsed["label"],
                evidence_ids=parsed.get("evidence_ids"),
                rationale=parsed.get("rationale"),
                reported_model=reported,
                latency_ms=latency_ms,
                usage=normalized["usage"],
                error=None,
                raw_archive_path=str(archive_path),
            )
            if crossed:
                raise abort_midblock()
            return record
        if attempt >= MAX_LINKED_ATTEMPTS:
            record = finalize(
                attempt=attempt,
                terminal_status="parse_failure",
                reported_model=reported,
                latency_ms=latency_ms,
                usage=normalized["usage"],
                error=_sanitize_error("parse_failure", "; ".join(problems[:3])),
                raw_archive_path=str(archive_path),
            )
            if crossed:
                raise abort_midblock()
            return record
        if crossed:
            raise abort_midblock()
        time.sleep(RETRY_SLEEP_SECONDS)


def _require_researcher_approval(approvals: dict[str, Any]) -> None:
    if approvals.get("status") != "researcher_approved":
        raise LauncherError(
            "the stage ceiling is not researcher-approved for this run; "
            "re-prepare with --approve-stage-ceiling after the researcher decision"
        )


def _refuse_on_stopfiles(run_dir: Path) -> None:
    stopfiles = _stopfiles(run_dir)
    if stopfiles:
        raise LauncherError(
            f"stop-file present ({', '.join(stopfiles)}); resolve and remove it "
            "before generating again"
        )


def _pending_api_rows(
    run_dir: Path, schedule: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return [
        row
        for row in schedule
        if row["route"] == "api" and not _record_path(run_dir, "api", row["slot_id"]).is_file()
    ]


def _load_envelopes_by_id(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        record["item_id"]: record
        for record in load_jsonl(Path(config["envelopes"]["path"]))
    }


def _evaluate_block_stop(
    run_dir: Path, schedule: list[dict[str, Any]], block: int
) -> None:
    """Stop rule c: more than 25% of a block's attempts terminally failing stops the run."""
    block_rows = [
        row for row in schedule if row["route"] == "api" and row["block"] == block
    ]
    statuses = []
    for row in block_rows:
        path = _record_path(run_dir, "api", row["slot_id"])
        if not path.is_file():
            return  # block not yet fully terminal; evaluated at its completion
        statuses.append(load_json(path)["terminal_status"])
    if not statuses:
        return
    failed = sum(1 for status in statuses if status != "succeeded")
    if failed / len(statuses) > BLOCK_FAILURE_FRACTION:
        _write_stopfile(
            run_dir,
            "block_failure",
            {
                "block": block,
                "terminal": len(statuses),
                "failed": failed,
                "threshold": BLOCK_FAILURE_FRACTION,
            },
        )
        raise LauncherError(
            f"more than {BLOCK_FAILURE_FRACTION:.0%} of block {block} attempts "
            "terminally failed; stop-file written for diagnosis (design Section 10 "
            "stop rule c)"
        )


def run_api(
    run_dir: Path, *, block: int | None = None, limit: int | None = None
) -> dict[str, Any]:
    """Dispatch pending api-route slots serially by block (design Sections 2 and 10)."""
    run_dir = run_dir.resolve()
    state = _load_run_state(run_dir)
    config, approvals, schedule = state["config"], state["approvals"], state["schedule"]
    _require_researcher_approval(approvals)
    _refuse_on_stopfiles(run_dir)
    comparison = config["comparison"]
    requested_model = config["models"]["api_model"]
    reasoning_effort = _config_effort(config)
    prices = _prices(config)
    rating_schema = load_json(Path(config["rating_schema"]["path"]))
    envelopes_by_id = _load_envelopes_by_id(config)
    authorized_restricted = config.get("authorized_restricted_items") or {}
    stage_ceiling = float(approvals["stage_ceiling_usd"])

    client = (
        _make_anthropic_client() if comparison == "anthropic" else _make_openai_client()
    )
    blocks_present = sorted(
        {row["block"] for row in schedule if row["route"] == "api"}
    )
    if block is not None:
        if block not in blocks_present:
            raise LauncherError(f"block {block} is not in the schedule")
        blocks_present = [block]

    dispatched = 0
    outcomes: Counter[str] = Counter()
    limit_reached = False
    for current_block in blocks_present:
        pending_block = [
            row
            for row in _pending_api_rows(run_dir, schedule)
            if row["block"] == current_block
        ]
        if not pending_block:
            continue
        remaining = len(_pending_api_rows(run_dir, schedule))
        projection = _projected_total(run_dir, prices, remaining)
        if projection["projected_usd"] > stage_ceiling:
            raise LauncherError(
                "projected cumulative spend "
                f"${projection['projected_usd']:.4f} exceeds the approved stage "
                f"ceiling ${stage_ceiling:.4f}; refusing to start block {current_block} "
                "(design Section 10 stop rule b)"
            )
        for row in sorted(pending_block, key=lambda item: item["position"]):
            if limit is not None and dispatched >= limit:
                limit_reached = True
                break
            record = _dispatch_api_slot(
                run_dir,
                comparison=comparison,
                row=row,
                envelope=envelopes_by_id[row["item_id"]],
                client=client,
                requested_model=requested_model,
                reasoning_effort=reasoning_effort,
                prices=prices,
                stage_ceiling=stage_ceiling,
                rating_schema=rating_schema,
                authorized_restricted=authorized_restricted,
            )
            outcomes[record["terminal_status"]] += 1
            dispatched += 1
        _evaluate_block_stop(run_dir, schedule, current_block)
        if limit_reached:
            break

    remaining = len(_pending_api_rows(run_dir, schedule))
    projection = _projected_total(run_dir, prices, remaining)
    return {
        "dispatched": dispatched,
        "terminal_status_counts": dict(sorted(outcomes.items())),
        "pending_api_slots": remaining,
        "realized_cost_usd": projection["realized_usd"],
        "projected_cost_usd": projection["projected_usd"],
        "stage_ceiling_usd": stage_ceiling,
    }


def smoke_test(run_dir: Path, *, calls: int = DEFAULT_SMOKE_CALLS) -> dict[str, Any]:
    """OpenAI adapter smoke test on the first pending api slots (design Section 11 stage 2).

    Operational output only: statuses, latency, cost; never label values.
    """
    run_dir = run_dir.resolve()
    if calls < 1:
        raise LauncherError("--calls must be a positive integer")
    state = _load_run_state(run_dir)
    config, approvals, schedule = state["config"], state["approvals"], state["schedule"]
    if config["comparison"] != "openai":
        raise LauncherError("smoke-test applies to the openai comparison only")
    _require_researcher_approval(approvals)
    _refuse_on_stopfiles(run_dir)
    prices = _prices(config)
    rating_schema = load_json(Path(config["rating_schema"]["path"]))
    envelopes_by_id = _load_envelopes_by_id(config)
    stage_ceiling = float(approvals["stage_ceiling_usd"])
    pending = sorted(
        _pending_api_rows(run_dir, schedule), key=lambda row: row["position"]
    )[:calls]
    if not pending:
        raise LauncherError("no pending api slots to smoke-test")
    projection = _projected_total(
        run_dir, prices, len(_pending_api_rows(run_dir, schedule))
    )
    if projection["projected_usd"] > stage_ceiling:
        raise LauncherError(
            "projected cumulative spend exceeds the approved stage ceiling; "
            "refusing the smoke test"
        )
    client = _make_openai_client()
    outcomes: Counter[str] = Counter()
    latencies: list[float] = []
    for row in pending:
        record = _dispatch_api_slot(
            run_dir,
            comparison="openai",
            row=row,
            envelope=envelopes_by_id[row["item_id"]],
            client=client,
            requested_model=config["models"]["api_model"],
            reasoning_effort=_config_effort(config),
            prices=prices,
            stage_ceiling=stage_ceiling,
            rating_schema=rating_schema,
            authorized_restricted=config.get("authorized_restricted_items") or {},
        )
        outcomes[record["terminal_status"]] += 1
        if isinstance(record.get("latency_ms"), (int, float)):
            latencies.append(float(record["latency_ms"]))
    costs = _realized_costs(run_dir, prices)
    return {
        "smoke_calls": len(pending),
        "terminal_status_counts": dict(sorted(outcomes.items())),
        "latency_ms": {
            "mean": sum(latencies) / len(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "realized_cost_usd": costs["realized_usd"],
        "stage_ceiling_usd": stage_ceiling,
    }


# ---------------------------------------------------------------------------
# status (design Section 10: operational only; labels never shown)


def status_report(run_dir: Path) -> dict[str, Any]:
    """Operational status: counts, blocks, cost, retries. Never label values."""
    run_dir = run_dir.resolve()
    state = _load_run_state(run_dir)
    config, approvals, schedule = state["config"], state["approvals"], state["schedule"]
    prices = _prices(config)
    by_route: dict[str, Any] = {}
    recent_errors: list[str] = []
    for route in ROUTES:
        rows = [row for row in schedule if row["route"] == route]
        counts: Counter[str] = Counter()
        for row in rows:
            path = _record_path(run_dir, route, row["slot_id"])
            if not path.is_file():
                counts["pending"] += 1
                continue
            record = load_json(path)
            counts[record["terminal_status"]] += 1
            error = record.get("error")
            if error and len(recent_errors) < 10:
                recent_errors.append(
                    f"{route}:{record['terminal_status']}: {str(error)[:ERROR_DISPLAY_CHARS]}"
                )
        by_route[route] = {
            "expected": len(rows),
            "pending": counts.pop("pending", 0),
            "terminal_status_counts": dict(sorted(counts.items())),
        }
    blocks = sorted({row["block"] for row in schedule})
    completed_blocks = [
        block
        for block in blocks
        if all(
            _record_path(run_dir, row["route"], row["slot_id"]).is_file()
            for row in schedule
            if row["block"] == block
        )
    ]
    retries = sum(
        1
        for row in schedule
        if row["route"] == "api"
        and (run_dir / "api_archive" / f"{row['slot_id']}.attempt2.json").is_file()
    )
    projection = _projected_total(
        run_dir, prices, len(_pending_api_rows(run_dir, schedule))
    )
    return {
        "comparison": config["comparison"],
        "stage": config["stage"],
        "reasoning_effort": _config_effort(config),
        "expected_slots": len(schedule),
        "by_route": by_route,
        "blocks": {"total": len(blocks), "completed": completed_blocks},
        "retries": retries,
        "cost": {
            "realized_usd": projection["realized_usd"],
            "projected_usd": projection["projected_usd"],
            "stage_ceiling_usd": float(approvals["stage_ceiling_usd"]),
            "approval_status": approvals["status"],
        },
        "stopfiles": _stopfiles(run_dir),
        "recent_errors": recent_errors,
    }


# ---------------------------------------------------------------------------
# collect-subagent (design Sections 2, 3, and 10)


def collect_subagent(run_dir: Path) -> dict[str, Any]:
    """Convert terminal unit_fanout worker returns into rating records.

    Requires the sub-agent run to be all-terminal and valid; maps worker terminal
    statuses onto rating terminal statuses (failures stay in the denominator, design
    Section 3); re-verifies the slot's prompt hash against the frozen envelope; marks
    `reported_model_verified` false because sub-agent identity is self-reported
    (design Section 2). `requested_effort` is the sealed config value: the
    orchestrating session pins the sub-agent effort level, so the config records what
    was requested for both arms, while verification stays false.
    """
    run_dir = run_dir.resolve()
    state = _load_run_state(run_dir)
    config, schedule = state["config"], state["schedule"]
    manifest = state["manifest"]
    subagent_run_dir = Path(manifest["subagent_run_dir"])
    try:
        fanout_report = _UNIT_FANOUT.status(subagent_run_dir)
    except FanoutError as exc:
        raise LauncherError(f"sub-agent run status failed: {exc}") from exc
    if fanout_report["pending"] or fanout_report["invalid"]:
        raise LauncherError(
            "sub-agent run is not all-terminal and valid: "
            f"pending={fanout_report['pending']}, invalid={fanout_report['invalid']}"
        )
    fanout_manifest = load_json(subagent_run_dir / "run_manifest.json")
    assignments = {row["assignment_id"]: row for row in fanout_manifest["assignments"]}
    # AMENDMENT_002 (researcher-approved 2026-08-16): a linked retry issued by
    # `unit_fanout.py retry` renames assignment_id to "<slot_id>-attemptNNN" while
    # preserving unit_id as the original slot id. Index by unit_id as a fallback so a
    # superseded slot still resolves here. No-op for runs with no retry.
    assignments_by_unit = {
        row["unit_id"]: row
        for row in fanout_manifest["assignments"]
        if row.get("unit_id")
    }
    envelopes_by_id = _load_envelopes_by_id(config)
    rating_schema = load_json(Path(config["rating_schema"]["path"]))
    requested_model = config["models"]["subagent_requested_model"]
    requested_effort = _config_effort(config)
    written = 0
    skipped = 0
    counts: Counter[str] = Counter()
    for row in schedule:
        if row["route"] != "subagent":
            continue
        slot_id = row["slot_id"]
        assignment_row = assignments.get(slot_id) or assignments_by_unit.get(slot_id)
        if assignment_row is None:
            raise LauncherError(f"sub-agent run has no assignment for slot {slot_id}")
        assignment = load_json(Path(assignment_row["assignment_path"]))
        payload = assignment["payload"]
        envelope = envelopes_by_id[row["item_id"]]
        if (
            payload["prompt_sha256"] != envelope["prompt_sha256"]
            or sha256_text(payload["prompt_text"]) != envelope["prompt_sha256"]
        ):
            raise LauncherError(f"prompt hash re-verification failed for slot {slot_id}")
        returned = load_json(Path(assignment_row["return_path"]))
        worker_status = returned["status"]
        mapped = SUBAGENT_STATUS_MAP.get(worker_status)
        if mapped is None:
            raise LauncherError(
                f"unmapped sub-agent terminal status {worker_status!r} for {slot_id}"
            )
        provenance = returned.get("provenance") or {}
        label = evidence_ids = rationale = None
        error: str | None = None
        if mapped == "succeeded":
            result = returned.get("result") or {}
            problems = _sanitized_schema_errors(envelope["response_schema"], result)
            if problems:
                mapped = "parse_failure"
                error = _sanitize_error(
                    "parse_failure",
                    "worker result failed the frozen per-item response schema: "
                    + "; ".join(problems[:3]),
                )
            else:
                label = result["label"]
                evidence_ids = result.get("evidence_ids")
                rationale = result.get("rationale")
        else:
            error = _sanitize_error(
                f"subagent worker status {worker_status}", str(returned.get("error") or "")
            )
        reported_model = provenance.get("reported_model") or provenance.get("model")
        latency = provenance.get("latency_ms")
        record = _build_rating_record(
            comparison=config["comparison"],
            item_id=row["item_id"],
            route="subagent",
            coder_rep=row["coder_rep"],
            attempt=int(returned.get("attempt") or 1),
            terminal_status=mapped,
            label=label,
            evidence_ids=evidence_ids,
            rationale=rationale,
            prompt_sha256=envelope["prompt_sha256"],
            response_schema_sha256=envelope["response_schema_sha256"],
            requested_model=requested_model,
            requested_effort=requested_effort,
            reported_model=reported_model if isinstance(reported_model, str) else None,
            reported_model_verified=False,
            block=row["block"],
            latency_ms=float(latency) if isinstance(latency, (int, float)) else None,
            usage=None,
            error=error,
            raw_archive_path=str(assignment_row["return_path"]),
            rating_schema=rating_schema,
        )
        target = _record_path(run_dir, "subagent", slot_id)
        if target.is_file():
            skipped += 1
        else:
            atomic_json(target, record)
            written += 1
        counts[mapped] += 1
    return {
        "written": written,
        "already_collected": skipped,
        "terminal_status_counts": dict(sorted(counts.items())),
    }


# ---------------------------------------------------------------------------
# merge (design Section 10: unblinding only after counts reconcile)


def merge_records(run_dir: Path, output: Path) -> dict[str, Any]:
    """Write rating_records.jsonl in schedule order once every slot is terminal."""
    run_dir = run_dir.resolve()
    output = output.resolve()
    state = _load_run_state(run_dir)
    config, schedule = state["config"], state["schedule"]
    receipt_path = run_dir / "merge_receipt.json"
    if receipt_path.exists():
        raise LauncherError(f"merge receipt already exists: {receipt_path}")
    if output.exists():
        raise LauncherError(f"refusing to overwrite merged output: {output}")
    rating_schema = load_json(Path(config["rating_schema"]["path"]))
    missing = [
        row["slot_id"]
        for row in schedule
        if not _record_path(run_dir, row["route"], row["slot_id"]).is_file()
    ]
    if missing:
        raise LauncherError(
            f"{len(missing)} schedule slots have no terminal record "
            f"(first: {', '.join(missing[:5])})"
        )
    records: list[dict[str, Any]] = []
    record_hashes: dict[str, str] = {}
    counts: dict[str, Counter[str]] = {route: Counter() for route in ROUTES}
    for row in schedule:
        path = _record_path(run_dir, row["route"], row["slot_id"])
        record = load_json(path)
        for field in ("comparison", "item_id", "route", "coder_rep", "block"):
            if record.get(field) != row[field]:
                raise LauncherError(
                    f"record/schedule mismatch for slot {row['slot_id']} field {field}"
                )
        problems = _sanitized_schema_errors(rating_schema, record)
        if problems:
            raise LauncherError(
                f"record for slot {row['slot_id']} violates the rating schema: "
                + "; ".join(problems[:3])
            )
        records.append(record)
        record_hashes[row["slot_id"]] = sha256_file(path)
        counts[row["route"]][record["terminal_status"]] += 1
    expected = Counter(row["route"] for row in schedule)
    for route in ROUTES:
        if sum(counts[route].values()) != expected[route]:
            raise LauncherError(f"merge counts do not reconcile for route {route}")
    _write_jsonl_exclusive(output, records)
    receipt = {
        "created_utc": utc_now(),
        "output_path": str(output),
        "output_sha256": sha256_file(output),
        "expected_by_route": dict(expected),
        "counts_by_route_status": {
            route: dict(sorted(counts[route].items())) for route in ROUTES
        },
        "record_file_sha256": record_hashes,
        "reconciled": True,
    }
    atomic_json(receipt_path, receipt)
    return {
        "merged": len(records),
        "output": str(output),
        "counts_by_route_status": receipt["counts_by_route_status"],
    }


# ---------------------------------------------------------------------------
# analyze (design Sections 3, 4, and 6; post-run, unblinded)


def _pair_agreement(labels_a: list[str | None], labels_b: list[str | None] | None) -> float:
    """Mean of g over pairs; failures (None) stay in the denominator (design Section 3)."""

    def g(left: str | None, right: str | None) -> float:
        return 1.0 if left is not None and left == right else 0.0

    if labels_b is None:
        pairs = [
            g(labels_a[i], labels_a[j])
            for i in range(len(labels_a))
            for j in range(i + 1, len(labels_a))
        ]
    else:
        pairs = [g(left, right) for left in labels_a for right in labels_b]
    if not pairs:
        raise LauncherError("agreement requires at least two codings per route")
    return sum(pairs) / len(pairs)


def _item_contrasts(
    schedule: list[dict[str, Any]], ratings: list[dict[str, Any]]
) -> dict[str, dict[str, float]]:
    by_slot = {record["item_id"] + "|" + record["route"] + "|" + str(record["coder_rep"]): record for record in ratings}
    labels: dict[str, dict[str, list[str | None]]] = defaultdict(
        lambda: {"api": [], "subagent": []}
    )
    for row in schedule:
        key = row["item_id"] + "|" + row["route"] + "|" + str(row["coder_rep"])
        record = by_slot.get(key)
        if record is None:
            raise LauncherError(f"merged output is missing a record for {row['slot_id']}")
        label = record["label"] if record["terminal_status"] == "succeeded" else None
        labels[row["item_id"]][row["route"]].append(label)
    contrasts: dict[str, dict[str, float]] = {}
    for item_id, routes in labels.items():
        aa = _pair_agreement(routes["api"], None)
        ss = _pair_agreement(routes["subagent"], None)
        as_ = _pair_agreement(routes["api"], routes["subagent"])
        contrasts[item_id] = {
            "AA": aa,
            "SS": ss,
            "AS": as_,
            "d": ss - aa,
            "shift": aa + ss - 2.0 * as_,
        }
    return contrasts


def analyze_run(
    run_dir: Path, out_path: Path, *, bootstrap_b: int = DEFAULT_BOOTSTRAP_B
) -> dict[str, Any]:
    """Post-run analysis: SS-AA primary and shift term with the frozen cluster bootstrap.

    Gates (fail closed): sealed-file integrity, the researcher-approved equivalence
    margin (design decision D2; spec `equivalence_margin_status`), and a reconciled
    merge receipt (design Section 10 unblinding rule). This is the only subcommand
    whose output may show labels or agreement values.
    """
    run_dir = run_dir.resolve()
    out_path = out_path.resolve()
    state = _load_run_state(run_dir)
    config, schedule = state["config"], state["schedule"]
    spec = load_json(Path(config["design_spec"]["path"]))
    margin_status = str(spec.get("equivalence_margin_status") or "")
    if not margin_status.startswith("researcher_approved"):
        raise LauncherError(
            "the equivalence margin is not researcher-approved in the frozen spec "
            f"(equivalence_margin_status={margin_status!r})"
        )
    receipt_path = run_dir / "merge_receipt.json"
    if not receipt_path.is_file():
        raise LauncherError("analyze requires a merge receipt; run merge first")
    receipt = load_json(receipt_path)
    merged_path = Path(receipt["output_path"])
    if not merged_path.is_file() or sha256_file(merged_path) != receipt["output_sha256"]:
        raise LauncherError("merged output drifted from the merge receipt")
    if out_path.exists():
        raise LauncherError(f"refusing to overwrite analysis output: {out_path}")

    ratings = load_jsonl(merged_path)
    items = load_jsonl(Path(config["items"]["path"]))
    items_by_id = {row["item_id"]: row for row in items}
    rating_schema = load_json(Path(config["rating_schema"]["path"]))

    comparison = config["comparison"]
    agreement_module = _import_by_path("agreement_analysis_v004", AGREEMENT_MODULE_PATH)
    try:
        delegated = agreement_module.analyze(
            items, ratings, rating_schema, spec, comparison
        )
    except Exception as exc:
        raise LauncherError(
            f"agreement_analysis_v004.analyze failed: {type(exc).__name__}: {exc}"
        ) from exc

    contrasts = _item_contrasts(schedule, ratings)
    weights = spec.get("primary_target_mix") or {}
    if not weights:
        raise LauncherError("spec has no primary_target_mix weights")
    families: dict[str, list[str]] = defaultdict(list)
    for item_id in contrasts:
        families[items_by_id[item_id]["family_id"]].append(item_id)
    family_means = {
        family: {
            metric: sum(contrasts[item_id][metric] for item_id in ids) / len(ids)
            for metric in ("AA", "SS", "AS", "d", "shift")
        }
        for family, ids in sorted(families.items())
    }
    weighted: dict[str, float | None] = {}
    for metric in ("d", "shift"):
        if set(weights) <= set(family_means) and weights:
            weighted[metric] = sum(
                weights[family] * family_means[family][metric] for family in weights
            )
        else:
            weighted[metric] = None

    bootstrap_module = _import_by_path("cluster_bootstrap_v001", BOOTSTRAP_MODULE_PATH)
    seed = int(config["seed"])
    margin = spec.get("equivalence_margin_absolute_agreement")
    if not isinstance(margin, (int, float)) or margin <= 0:
        raise LauncherError(
            "spec has no positive equivalence_margin_absolute_agreement"
        )
    try:
        rows, row_source = bootstrap_module.build_rows_from_ratings(
            agreement_module,
            ratings,
            items,
            spec,
            comparison,
            rating_schema=rating_schema,
        )
        bootstrap_results = {
            contrast: bootstrap_module.run_inference(
                rows,
                weights=weights,
                contrast=contrast,
                seed=seed,
                b=bootstrap_b,
                margin=float(margin),
                comparison=comparison,
            )
            for contrast in ("d", "shift")
        }
    except LauncherError:
        raise
    except Exception as exc:
        raise LauncherError(
            f"cluster_bootstrap_v001 inference failed: {type(exc).__name__}: {exc}"
        ) from exc

    analysis = {
        "generated_utc": utc_now(),
        "comparison": comparison,
        "stage": config["stage"],
        "estimand": "SS_minus_AA (design_v004 Section 3, decision D1)",
        "equivalence_margin_absolute_agreement": margin,
        "equivalence_margin_status": margin_status,
        "bootstrap": {
            "b": bootstrap_b,
            "seed": seed,
            "row_source": row_source,
            "results": bootstrap_results,
        },
        "launcher_cross_check": {
            "weighted_overall": weighted,
            "family_means": family_means,
            "item_contrasts": {
                item_id: contrasts[item_id] for item_id in sorted(contrasts)
            },
        },
        "delegated_agreement_analysis": delegated,
        "merge_receipt_sha256": sha256_file(receipt_path),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(analysis, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {
        "out": str(out_path),
        "d_overall_weighted": weighted["d"],
        "shift_overall_weighted": weighted["shift"],
        "items": len(contrasts),
    }


# ---------------------------------------------------------------------------
# CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--run-dir", type=Path, required=True)
    prepare_parser.add_argument("--comparison", choices=COMPARISONS, required=True)
    prepare_parser.add_argument("--stage", choices=STAGES, required=True)
    prepare_parser.add_argument("--envelopes", type=Path, default=DEFAULT_ENVELOPES)
    prepare_parser.add_argument("--items", type=Path, default=DEFAULT_ITEMS)
    prepare_parser.add_argument("--reps-api", type=int, required=True)
    prepare_parser.add_argument("--reps-subagent", type=int, required=True)
    prepare_parser.add_argument("--blocks", type=int, required=True)
    prepare_parser.add_argument("--seed", type=int, required=True)
    prepare_parser.add_argument("--approve-stage-ceiling", action="store_true")
    prepare_parser.add_argument(
        "--effort",
        choices=REASONING_EFFORTS,
        default=None,
        help=(
            "matched reasoning effort for BOTH arms (researcher decision 2026-08-05); "
            "omit for provider defaults and byte-identical pre-effort requests"
        ),
    )
    prepare_parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    prepare_parser.add_argument("--budget-plan", type=Path, default=DEFAULT_BUDGET_PLAN)
    prepare_parser.add_argument("--sources-root", type=Path, default=DEFAULT_SOURCES_ROOT)

    run_parser = subparsers.add_parser("run-api")
    run_parser.add_argument("--run-dir", type=Path, required=True)
    run_parser.add_argument("--block", type=int)
    run_parser.add_argument("--limit", type=int)

    smoke_parser = subparsers.add_parser("smoke-test")
    smoke_parser.add_argument("--run-dir", type=Path, required=True)
    smoke_parser.add_argument("--calls", type=int, default=DEFAULT_SMOKE_CALLS)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--run-dir", type=Path, required=True)

    collect_parser = subparsers.add_parser("collect-subagent")
    collect_parser.add_argument("--run-dir", type=Path, required=True)

    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("--run-dir", type=Path, required=True)
    merge_parser.add_argument("--output", type=Path, required=True)

    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--run-dir", type=Path, required=True)
    analyze_parser.add_argument("--out", type=Path, required=True)
    analyze_parser.add_argument("--bootstrap-b", type=int, default=DEFAULT_BOOTSTRAP_B)

    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_run(
                args.run_dir,
                comparison=args.comparison,
                stage=args.stage,
                envelopes_path=args.envelopes,
                items_path=args.items,
                reps_api=args.reps_api,
                reps_subagent=args.reps_subagent,
                blocks=args.blocks,
                seed=args.seed,
                approve_stage_ceiling=args.approve_stage_ceiling,
                reasoning_effort=args.effort,
                spec_path=args.spec,
                budget_plan_path=args.budget_plan,
                sources_root=args.sources_root,
            )
        elif args.command == "run-api":
            result = run_api(args.run_dir, block=args.block, limit=args.limit)
        elif args.command == "smoke-test":
            result = smoke_test(args.run_dir, calls=args.calls)
        elif args.command == "status":
            result = status_report(args.run_dir)
        elif args.command == "collect-subagent":
            result = collect_subagent(args.run_dir)
        elif args.command == "merge":
            result = merge_records(args.run_dir, args.output)
        else:
            result = analyze_run(args.run_dir, args.out, bootstrap_b=args.bootstrap_b)
    except (LauncherError, PromptBuildError, FanoutError, OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
