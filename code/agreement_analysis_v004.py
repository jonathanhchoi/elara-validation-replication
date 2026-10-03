#!/usr/bin/env python3
"""Repeated-coder route-agreement analysis for design v0.04 (SS - AA primary).

Implements `design_v004.md` Sections 3-6 for one vendor comparison at a time:

- Section 3: per-item exact-agreement rates over all independent within-item
  coder pairs (AA over C(R_A, 2) API pairs, SS over C(R_S, 2) sub-agent pairs,
  AS over R_A * R_S cross pairs), where g(x, y) = 1 only when both terminal
  codings succeeded and their substantive labels match; d_i = SS_i - AA_i; and
  the primary Delta_overall = sum_f w_f * mean_f(d_i) under the frozen target
  mix of `task_family_spec_v004.json`.
- Section 4: the leading secondaries AS - AA and the route-shift term
  S_i = AA_i + SS_i - 2 * AS_i, aggregated with the same frozen weights.
- Section 5: the validity/agreement decomposition per route, per pair type,
  per family, and per phase: P(valid) for single codings, P(both valid) for
  pairs, and P(agree | both valid), all from pooled counts (null when a
  denominator is zero).
- Section 6: emitted primary blocks state the point null "Delta_overall = 0",
  the TOST equivalence null at the approved margin, and that this module
  produces point estimates only; intervals come from `cluster_bootstrap_v001.py`.

Input contract: rating records are terminal draw-slot outcomes (exactly one
record per (comparison, item, route, coder_rep)) conforming to
`rating_record_schema_v002.json`; items follow the `development_items_v003.jsonl`
shape (item_id, family_id, dependence_cluster_id, benchmark_phase, and allowed
labels either top-level or under `prompt.allowed_labels`). Records for the
other vendor comparison are schema-validated, then excluded and counted.
Items with no records for the analyzed comparison are skipped and listed in
`items_without_ratings`; items with some records must meet the spec's
`minimum_ratings_per_item` per route.

Bootstrap contract (consumed by `cluster_bootstrap_v001.py`):

    delta_overall_from_items(item_rows, weights, contrast="SS_minus_AA") -> float

`item_rows` is a list of per-item dicts each carrying at least `family_id`,
`dependence_cluster_id`, `d`, `as_minus_aa`, and `shift`; the rows in the
`item_rows` list of every phase block returned by `analyze` qualify, as do
resampled multisets of them. `weights` maps family_id -> weight (the frozen
`primary_target_mix`). `contrast` is one of "SS_minus_AA", "AS_minus_AA", or
"route_shift". The value is the sum over weighted families of the weight times
the unweighted mean of the contrast field over that family's rows. Rows whose
family is not in `weights` are ignored; a weighted family with no rows raises
AgreementAnalysisError. The function is pure, dependency-light, and touches no
files, so the bootstrap can call it on every resampled cluster set.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
DEFAULT_ITEMS = HERE / "development_items_v003.jsonl"
DEFAULT_SPEC = HERE / "task_family_spec_v004.json"
DEFAULT_RATING_SCHEMA = HERE / "rating_record_schema_v002.json"

ANALYSIS_VERSION = "0.4"
COMPARISONS = ("anthropic", "openai")
PAIR_TYPES = ("AA", "AS", "SS")
CONTRAST_FIELDS = {
    "SS_minus_AA": "d",
    "AS_minus_AA": "as_minus_aa",
    "route_shift": "shift",
}
INFERENCE_NOTE = "point estimates; intervals via cluster_bootstrap_v001.py"


class AgreementAnalysisError(ValueError):
    """A fail-closed input, contract, or estimand-construction error."""


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgreementAnalysisError(f"cannot read valid JSON from {path}: {exc}") from exc


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise AgreementAnalysisError(f"{path}:{line_number}: {exc}") from exc
    except OSError as exc:
        raise AgreementAnalysisError(f"cannot read {path}: {exc}") from exc
    return rows


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


def item_allowed_labels(item: dict[str, Any]) -> list[str]:
    """Return the item's closed label set (top-level or `prompt.allowed_labels`)."""
    labels = item.get("allowed_labels")
    if labels is None and isinstance(item.get("prompt"), dict):
        labels = item["prompt"].get("allowed_labels")
    if (
        not isinstance(labels, list)
        or not labels
        or not all(isinstance(label, str) and label for label in labels)
    ):
        raise AgreementAnalysisError(
            f"{item.get('item_id')!r}: item has no usable allowed labels"
        )
    return list(labels)


def index_items(items: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index and validate the item roster (design_v004.md Sections 3 and 8).

    Every item must carry item_id, family_id, dependence_cluster_id,
    benchmark_phase, and a usable allowed-label set; family_id must be one of
    the frozen spec families; item_id must be unique.
    """
    families = {family.get("family_id") for family in spec.get("families", [])}
    families.discard(None)
    if not families:
        raise AgreementAnalysisError("spec declares no families")
    if not items:
        raise AgreementAnalysisError("items file is empty")
    by_id: dict[str, dict[str, Any]] = {}
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            raise AgreementAnalysisError(f"item {position} must be an object")
        for field in ("item_id", "family_id", "dependence_cluster_id", "benchmark_phase"):
            value = item.get(field)
            if not isinstance(value, str) or not value:
                raise AgreementAnalysisError(f"item {position}: missing or invalid {field}")
        item_id = item["item_id"]
        if item_id in by_id:
            raise AgreementAnalysisError(f"duplicate item_id: {item_id}")
        if item["family_id"] not in families:
            raise AgreementAnalysisError(
                f"{item_id}: family_id {item['family_id']!r} is not in the frozen spec"
            )
        item_allowed_labels(item)
        by_id[item_id] = item
    return by_id


def substantive_agreement(left: dict[str, Any], right: dict[str, Any]) -> float:
    """Section 3 g(x, y): 1.0 only when both codings succeeded with equal labels."""
    if left["terminal_status"] != "succeeded" or right["terminal_status"] != "succeeded":
        return 0.0
    return float(left["label"] == right["label"])


def _pair_stats(pairs: Iterable[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, int]:
    """Count pairs, both-valid pairs, and agreements among both-valid pairs (Sections 3 and 5)."""
    total = both_valid = agreements = 0
    for left, right in pairs:
        total += 1
        if left["terminal_status"] == "succeeded" and right["terminal_status"] == "succeeded":
            both_valid += 1
            if left["label"] == right["label"]:
                agreements += 1
    return {
        "pairs": total,
        "both_valid": both_valid,
        "agreements_given_both_valid": agreements,
    }


def item_scores(records_for_item: list[dict[str, Any]]) -> dict[str, Any]:
    """Score one item's terminal codings (design_v004.md Section 3).

    Pure function over the terminal records of a single (comparison, item):
    returns the AA/AS/SS exact-agreement rates over all independent coder
    pairs, d = SS - AA, as_minus_aa = AS - AA, shift = AA + SS - 2*AS,
    per-route validity counts, and per-pair-type counts for the Section 5
    decomposition. Invalid, refused, malformed, and missing codings stay in
    every denominator: a pair containing a failed coding disagrees.
    """
    by_route: dict[str, dict[int, dict[str, Any]]] = {"api": {}, "subagent": {}}
    for record in records_for_item:
        route = record.get("route")
        if route not in by_route:
            raise AgreementAnalysisError(f"unknown route: {route!r}")
        rep = record.get("coder_rep")
        if not isinstance(rep, int):
            raise AgreementAnalysisError(f"coder_rep must be an integer: {rep!r}")
        if rep in by_route[route]:
            raise AgreementAnalysisError(
                f"duplicate (route, coder_rep) slot: ({route}, {rep})"
            )
        by_route[route][rep] = record
    api = [by_route["api"][rep] for rep in sorted(by_route["api"])]
    subagent = [by_route["subagent"][rep] for rep in sorted(by_route["subagent"])]
    if len(api) < 2 or len(subagent) < 2:
        raise AgreementAnalysisError(
            "Section 3 requires R_A >= 2 and R_S >= 2 terminal codings; "
            f"found api={len(api)}, subagent={len(subagent)}"
        )
    pair_stats = {
        "AA": _pair_stats(itertools.combinations(api, 2)),
        "AS": _pair_stats((a, s) for a in api for s in subagent),
        "SS": _pair_stats(itertools.combinations(subagent, 2)),
    }
    aa = pair_stats["AA"]["agreements_given_both_valid"] / pair_stats["AA"]["pairs"]
    as_rate = pair_stats["AS"]["agreements_given_both_valid"] / pair_stats["AS"]["pairs"]
    ss = pair_stats["SS"]["agreements_given_both_valid"] / pair_stats["SS"]["pairs"]
    return {
        "r_api": len(api),
        "r_subagent": len(subagent),
        "valid_api": sum(1 for record in api if record["terminal_status"] == "succeeded"),
        "valid_subagent": sum(
            1 for record in subagent if record["terminal_status"] == "succeeded"
        ),
        "aa": aa,
        "as": as_rate,
        "ss": ss,
        "d": ss - aa,
        "as_minus_aa": as_rate - aa,
        "shift": aa + ss - 2.0 * as_rate,
        "pair_stats": pair_stats,
    }


def delta_overall_from_items(
    item_rows: list[dict[str, Any]],
    weights: dict[str, float],
    contrast: str = "SS_minus_AA",
) -> float:
    """Weighted family aggregate of one contrast (design_v004.md Sections 3-4).

    This is the bootstrap contract documented in the module docstring:
    `cluster_bootstrap_v001.py` calls it on resampled cluster sets. Each row
    must carry `family_id`, `dependence_cluster_id` (unused here, required so
    resampling stays cluster-aware), and the fields `d`, `as_minus_aa`, and
    `shift`. Rows whose family is outside `weights` are ignored; a weighted
    family with no rows is an error; family means are unweighted item means.
    """
    field = CONTRAST_FIELDS.get(contrast)
    if field is None:
        raise AgreementAnalysisError(
            f"unknown contrast {contrast!r}; expected one of {sorted(CONTRAST_FIELDS)}"
        )
    if not isinstance(weights, dict) or not weights:
        raise AgreementAnalysisError("weights must be a nonempty mapping")
    by_family: dict[str, list[float]] = defaultdict(list)
    for row in item_rows:
        family_id = row["family_id"]
        if family_id in weights:
            by_family[family_id].append(float(row[field]))
    missing = sorted(set(weights) - set(by_family))
    if missing:
        raise AgreementAnalysisError(f"no items for weighted families: {missing}")
    return sum(
        weights[family_id] * (sum(values) / len(values))
        for family_id, values in sorted(by_family.items())
    )


def validity_decomposition(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pooled-count validity/agreement decomposition (design_v004.md Section 5).

    Per route: P(valid) over single codings. Per pair type (AA/AS/SS):
    P(both valid) and P(agree | both valid). Rates come from counts pooled over
    the given item rows; a rate with a zero denominator is null.
    """
    per_route: dict[str, Any] = {}
    for route, count_key, valid_key in (
        ("api", "r_api", "valid_api"),
        ("subagent", "r_subagent", "valid_subagent"),
    ):
        codings = sum(row[count_key] for row in rows)
        valid = sum(row[valid_key] for row in rows)
        per_route[route] = {
            "codings": codings,
            "valid": valid,
            "p_valid": (valid / codings) if codings else None,
        }
    per_pair_type: dict[str, Any] = {}
    for pair_type in PAIR_TYPES:
        pairs = sum(row["pair_stats"][pair_type]["pairs"] for row in rows)
        both_valid = sum(row["pair_stats"][pair_type]["both_valid"] for row in rows)
        agreements = sum(
            row["pair_stats"][pair_type]["agreements_given_both_valid"] for row in rows
        )
        per_pair_type[pair_type] = {
            "pairs": pairs,
            "both_valid": both_valid,
            "agreements_given_both_valid": agreements,
            "p_both_valid": (both_valid / pairs) if pairs else None,
            "p_agree_given_both_valid": (agreements / both_valid) if both_valid else None,
        }
    return {"per_route": per_route, "per_pair_type": per_pair_type}


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _spec_minimums(spec: dict[str, Any]) -> dict[str, int]:
    minimums = spec.get("minimum_ratings_per_item")
    if not isinstance(minimums, dict):
        raise AgreementAnalysisError("spec is missing minimum_ratings_per_item")
    out: dict[str, int] = {}
    for route in ("api", "subagent"):
        value = minimums.get(route)
        if not isinstance(value, int) or isinstance(value, bool) or value < 2:
            raise AgreementAnalysisError(
                f"spec minimum_ratings_per_item.{route} must be an integer >= 2"
            )
        out[route] = value
    return out


def _target_mix(spec: dict[str, Any]) -> dict[str, float]:
    weights = spec.get("primary_target_mix")
    if not isinstance(weights, dict) or not weights:
        raise AgreementAnalysisError("spec is missing primary_target_mix")
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-9:
        raise AgreementAnalysisError(
            f"primary target-mix weights must sum to one, found {total!r}"
        )
    return {family_id: float(weight) for family_id, weight in weights.items()}


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row[key]
        for key in (
            "item_id",
            "family_id",
            "dependence_cluster_id",
            "benchmark_phase",
            "r_api",
            "r_subagent",
            "valid_api",
            "valid_subagent",
            "aa",
            "as",
            "ss",
            "d",
            "as_minus_aa",
            "shift",
        )
    }


def _phase_block(rows: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    """Build one labeled phase block (design_v004.md Sections 3-6)."""
    weights = _target_mix(spec)
    margin = spec.get("equivalence_margin_absolute_agreement")
    if not isinstance(margin, (int, float)) or isinstance(margin, bool) or margin <= 0:
        raise AgreementAnalysisError(
            "spec is missing a positive equivalence_margin_absolute_agreement"
        )
    family_ids = sorted({row["family_id"] for row in rows})
    families: dict[str, Any] = {}
    for family_id in family_ids:
        family_rows = [row for row in rows if row["family_id"] == family_id]
        summary: dict[str, Any] = {"items": len(family_rows)}
        for key in ("aa", "as", "ss", "d", "as_minus_aa", "shift"):
            summary[key] = _mean([row[key] for row in family_rows])
        summary["validity"] = validity_decomposition(family_rows)
        families[family_id] = summary

    missing = sorted(set(weights) - set(family_ids))
    if missing:
        primary = None
        reason = (
            "primary requires every target-mix family in this phase; missing: "
            + ", ".join(missing)
        )
        as_minus_aa_overall: float | None = None
        route_shift_overall: float | None = None
    else:
        primary = {
            "contrast": "SS_minus_AA",
            "estimand": spec.get("primary_estimand"),
            "delta_overall": delta_overall_from_items(rows, weights, "SS_minus_AA"),
            "family_deltas": {
                family_id: families[family_id]["d"] for family_id in sorted(weights)
            },
            "target_mix": dict(sorted(weights.items())),
            "realized_family_shares": {
                family_id: families[family_id]["items"] / len(rows)
                for family_id in family_ids
            },
            "null": "Delta_overall = 0",
            "equivalence_margin": margin,
            "equivalence_null": f"Delta_overall <= -{margin} or Delta_overall >= {margin}",
            "inference": INFERENCE_NOTE,
        }
        reason = None
        as_minus_aa_overall = delta_overall_from_items(rows, weights, "AS_minus_AA")
        route_shift_overall = delta_overall_from_items(rows, weights, "route_shift")

    secondary = {
        "estimands": list(spec.get("secondary_estimands", [])),
        "as_minus_aa_overall": as_minus_aa_overall,
        "route_shift_overall": route_shift_overall,
        "unavailable_reason": reason,
        "inference": INFERENCE_NOTE,
    }
    return {
        "items": len(rows),
        "families": families,
        "item_rows": [_public_row(row) for row in rows],
        "validity_decomposition": validity_decomposition(rows),
        "primary": primary,
        "primary_unavailable_reason": reason,
        "secondary": secondary,
    }


def analyze(
    items: list[dict[str, Any]],
    ratings: list[dict[str, Any]],
    rating_schema: dict[str, Any],
    spec: dict[str, Any],
    comparison: str,
    phase: str | None = None,
) -> dict[str, Any]:
    """Validate inputs and compute the v0.04 agreement report (Sections 3-6).

    One vendor comparison per call (Section 2: comparisons are never pooled).
    Every rating record in the file must conform to
    `rating_record_schema_v002.json`; records for the other comparison are then
    excluded and counted. For the analyzed comparison: unknown item_ids,
    duplicate (item, route, coder_rep) slots, succeeded labels outside the
    item's allowed set, and per-route counts below the spec minimum are
    errors. `phase=None` reports every benchmark phase present among scored
    items; a named phase restricts the report to that phase.
    """
    if comparison not in spec.get("vendor_pairs", {}):
        raise AgreementAnalysisError(
            f"comparison {comparison!r} is not declared in the spec vendor_pairs"
        )
    if spec.get("primary_contrast") != "SS_minus_AA":
        raise AgreementAnalysisError(
            "spec primary_contrast must be SS_minus_AA for the v0.04 analysis"
        )
    minimums = _spec_minimums(spec)
    _target_mix(spec)
    items_by_id = index_items(items, spec)

    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - environment-dependent guard
        raise AgreementAnalysisError("install jsonschema to validate rating records") from exc
    validator = Draft202012Validator(rating_schema)
    selected: list[dict[str, Any]] = []
    other_comparison = 0
    for position, record in enumerate(ratings):
        errors = sorted(validator.iter_errors(record), key=str)
        if errors:
            raise AgreementAnalysisError(
                f"rating record {position} violates rating_record_schema_v002: "
                f"{errors[0].message}"
            )
        if record["comparison"] == comparison:
            selected.append(record)
        else:
            other_comparison += 1
    if not selected:
        raise AgreementAnalysisError(f"no rating records for comparison {comparison!r}")

    selected.sort(key=lambda record: (record["item_id"], record["route"], record["coder_rep"]))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_slots: set[tuple[str, str, int]] = set()
    status_counts: Counter[str] = Counter()
    for record in selected:
        item_id = record["item_id"]
        item = items_by_id.get(item_id)
        if item is None:
            raise AgreementAnalysisError(f"rating references unknown item_id: {item_id}")
        slot = (item_id, record["route"], record["coder_rep"])
        if slot in seen_slots:
            raise AgreementAnalysisError(
                "duplicate terminal record for slot (item="
                f"{item_id}, route={record['route']}, coder_rep={record['coder_rep']})"
            )
        seen_slots.add(slot)
        if record["terminal_status"] == "succeeded" and record["label"] not in item_allowed_labels(item):
            raise AgreementAnalysisError(
                f"{item_id}: succeeded label {record['label']!r} is not in the item's allowed labels"
            )
        status_counts[f"{record['route']}:{record['terminal_status']}"] += 1
        grouped[item_id].append(record)

    scored_rows: list[dict[str, Any]] = []
    for item_id in sorted(grouped):
        item = items_by_id[item_id]
        item_records = grouped[item_id]
        for route in ("api", "subagent"):
            observed = sum(1 for record in item_records if record["route"] == route)
            if observed < minimums[route]:
                raise AgreementAnalysisError(
                    f"{item_id}: route {route} has {observed} terminal records; "
                    f"spec minimum is {minimums[route]}"
                )
        scored_rows.append(
            {
                "item_id": item_id,
                "family_id": item["family_id"],
                "dependence_cluster_id": item["dependence_cluster_id"],
                "benchmark_phase": item["benchmark_phase"],
                **item_scores(item_records),
            }
        )

    phases_present = sorted({row["benchmark_phase"] for row in scored_rows})
    if phase is not None:
        if phase not in phases_present:
            raise AgreementAnalysisError(
                f"no scored items in requested phase {phase!r}; phases present: {phases_present}"
            )
        reported_phases = [phase]
    else:
        reported_phases = phases_present

    phases: dict[str, Any] = {}
    for phase_name in reported_phases:
        phase_rows = [row for row in scored_rows if row["benchmark_phase"] == phase_name]
        phases[phase_name] = _phase_block(phase_rows, spec)

    return {
        "analysis_version": ANALYSIS_VERSION,
        "design_document": "design_v004.md",
        "spec_version": spec.get("spec_version"),
        "comparison": comparison,
        "phase_filter": phase,
        "minimum_ratings_per_item": minimums,
        "records_total": len(ratings),
        "records_analyzed": len(selected),
        "records_other_comparison": other_comparison,
        "items_in_file": len(items_by_id),
        "items_scored": len(scored_rows),
        "items_without_ratings": sorted(set(items_by_id) - set(grouped)),
        "terminal_status_counts": dict(sorted(status_counts.items())),
        "phases": phases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=Path, default=DEFAULT_ITEMS)
    parser.add_argument("--ratings", type=Path, required=True)
    parser.add_argument("--rating-schema", type=Path, default=DEFAULT_RATING_SCHEMA)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--comparison", required=True, choices=list(COMPARISONS))
    parser.add_argument("--phase", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        result = analyze(
            load_jsonl(args.items),
            load_jsonl(args.ratings),
            load_json(args.rating_schema),
            load_json(args.spec),
            comparison=args.comparison,
            phase=args.phase,
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        atomic_write_text(args.output, text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
