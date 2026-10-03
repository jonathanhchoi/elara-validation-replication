#!/usr/bin/env python3
"""Free-text-aware run analysis: canonicalize, then run the frozen v0.04 analysis.

Instrument v0.4 introduces free-text extraction items. The launcher's `analyze`
subcommand compares labels literally and treats any label outside an item's
closed label set as invalid, which mis-scores free-text responses. This tool is
the analysis path for runs whose panel contains free-text items. It reproduces
`run_route_comparison.analyze_run` exactly (same gates, same frozen
`agreement_analysis_v004` and `cluster_bootstrap_v001` modules, same output
shape) with one inserted, deterministic step:

    merge receipt -> normalize_ratings_v001 (canonical labels + realized
    label spaces) -> frozen analysis on the normalized files.

Agreement keeps its preregistered form: g(x, y) = 1 only when both codings are
valid terminal successes whose (canonical) labels match. The output JSON adds a
`normalization` block recording the derived files, their hashes, and the hashes
of the frozen normalizer modules. For closed-only panels the normalization is a
no-op and results match the launcher's `analyze`.

Usage:
  python analyze_normalized_v001.py --run-dir <run> --out <analysis.json> [--bootstrap-b 2000]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RRC_PATH = HERE / "run_route_comparison.py"
NORMALIZE_PATH = HERE / "normalize_ratings_v001.py"
EQUIV_PATH = HERE / "answer_equivalence_v001.py"


def import_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--bootstrap-b", type=int, default=None)
    args = parser.parse_args(argv)

    rrc = import_by_path("run_route_comparison_frozen_for_normalized_analysis", RRC_PATH)
    bootstrap_b = args.bootstrap_b if args.bootstrap_b is not None else rrc.DEFAULT_BOOTSTRAP_B
    run_dir = args.run_dir.resolve()
    out_path = args.out.resolve()

    try:
        state = rrc._load_run_state(run_dir)
        config, schedule = state["config"], state["schedule"]
        spec = rrc.load_json(Path(config["design_spec"]["path"]))
        margin_status = str(spec.get("equivalence_margin_status") or "")
        if not margin_status.startswith("researcher_approved"):
            raise rrc.LauncherError(
                "the equivalence margin is not researcher-approved in the frozen spec "
                f"(equivalence_margin_status={margin_status!r})"
            )
        receipt_path = run_dir / "merge_receipt.json"
        if not receipt_path.is_file():
            raise rrc.LauncherError("analysis requires a merge receipt; run merge first")
        receipt = rrc.load_json(receipt_path)
        merged_path = Path(receipt["output_path"])
        if not merged_path.is_file() or rrc.sha256_file(merged_path) != receipt["output_sha256"]:
            raise rrc.LauncherError("merged output drifted from the merge receipt")
        if out_path.exists():
            raise rrc.LauncherError(f"refusing to overwrite analysis output: {out_path}")

        items_path = Path(config["items"]["path"])
        norm_items_path = run_dir / "items_normalized.jsonl"
        norm_ratings_path = run_dir / "rating_records_normalized.jsonl"
        normalize = import_by_path("normalize_ratings_v001_frozen", NORMALIZE_PATH)
        rc = normalize.main([
            "--items", str(items_path),
            "--ratings", str(merged_path),
            "--out-items", str(norm_items_path),
            "--out-ratings", str(norm_ratings_path),
        ])
        if rc != 0:
            raise rrc.LauncherError("normalize_ratings_v001 failed")

        ratings = rrc.load_jsonl(norm_ratings_path)
        items = rrc.load_jsonl(norm_items_path)
        items_by_id = {row["item_id"]: row for row in items}
        rating_schema = rrc.load_json(Path(config["rating_schema"]["path"]))
        comparison = config["comparison"]

        agreement_module = rrc._import_by_path("agreement_analysis_v004", rrc.AGREEMENT_MODULE_PATH)
        delegated = agreement_module.analyze(items, ratings, rating_schema, spec, comparison)

        contrasts = rrc._item_contrasts(schedule, ratings)
        weights = spec.get("primary_target_mix") or {}
        if not weights:
            raise rrc.LauncherError("spec has no primary_target_mix weights")
        families: dict[str, list[str]] = defaultdict(list)
        for item_id in contrasts:
            families[items_by_id[item_id]["family_id"]].append(item_id)
        family_means = {
            family: {
                metric: sum(contrasts[i][metric] for i in ids) / len(ids)
                for metric in ("AA", "SS", "AS", "d", "shift")
            }
            for family, ids in sorted(families.items())
        }
        weighted: dict[str, float | None] = {}
        for metric in ("d", "shift"):
            if set(weights) <= set(family_means) and weights:
                weighted[metric] = sum(weights[f] * family_means[f][metric] for f in weights)
            else:
                weighted[metric] = None

        bootstrap_module = rrc._import_by_path("cluster_bootstrap_v001", rrc.BOOTSTRAP_MODULE_PATH)
        seed = int(config["seed"])
        margin = spec.get("equivalence_margin_absolute_agreement")
        if not isinstance(margin, (int, float)) or margin <= 0:
            raise rrc.LauncherError("spec has no positive equivalence_margin_absolute_agreement")
        rows, row_source = bootstrap_module.build_rows_from_ratings(
            agreement_module, ratings, items, spec, comparison, rating_schema=rating_schema
        )
        bootstrap_results = {
            contrast: bootstrap_module.run_inference(
                rows, weights=weights, contrast=contrast, seed=seed,
                b=bootstrap_b, margin=float(margin), comparison=comparison,
            )
            for contrast in ("d", "shift")
        }

        analysis = {
            "generated_utc": rrc.utc_now(),
            "comparison": comparison,
            "stage": config["stage"],
            "estimand": "SS_minus_AA (design_v004 Section 3, decision D1)",
            "equivalence_margin_absolute_agreement": margin,
            "equivalence_margin_status": margin_status,
            "normalization": {
                "tool": "analyze_normalized_v001.py",
                "reason": "instrument v0.4 free-text items; canonical-equality agreement",
                "normalized_ratings": {"path": str(norm_ratings_path), "sha256": rrc.sha256_file(norm_ratings_path)},
                "normalized_items": {"path": str(norm_items_path), "sha256": rrc.sha256_file(norm_items_path)},
                "normalizer_module_sha256": rrc.sha256_file(EQUIV_PATH),
                "normalize_module_sha256": rrc.sha256_file(NORMALIZE_PATH),
                "source_ratings_sha256": receipt["output_sha256"],
            },
            "bootstrap": {
                "b": bootstrap_b,
                "seed": seed,
                "row_source": row_source,
                "results": bootstrap_results,
            },
            "launcher_cross_check": {
                "weighted_overall": weighted,
                "family_means": family_means,
                "item_contrasts": {i: contrasts[i] for i in sorted(contrasts)},
            },
            "delegated_agreement_analysis": delegated,
            "merge_receipt_sha256": rrc.sha256_file(receipt_path),
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(analysis, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        print(json.dumps({
            "out": str(out_path),
            "d_overall_weighted": weighted["d"],
            "shift_overall_weighted": weighted["shift"],
            "items": len(contrasts),
        }, indent=2))
    except Exception as exc:  # noqa: BLE001 - single CLI error surface
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
