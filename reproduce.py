#!/usr/bin/env python3
"""Reproduce the published agreement results offline from archived codings.

This entry point does not call a model, use an API key, or download data.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "code"))
import agreement_analysis_v004 as agreement
import cluster_bootstrap_v001 as bootstrap
import normalize_ratings_v001 as normalize


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def compare(actual, expected, path="result"):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and set(actual) == set(expected), f"Keys differ at {path}")
        for key in expected:
            compare(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), f"List differs at {path}")
        for i, (a, b) in enumerate(zip(actual, expected)):
            compare(a, b, f"{path}[{i}]")
    elif isinstance(expected, float):
        require(math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12), f"Number differs at {path}: {actual} vs {expected}")
    else:
        require(actual == expected, f"Value differs at {path}: {actual!r} vs {expected!r}")


def verify_files():
    entries = read_json(ROOT / "provenance/export_manifest.json")
    for row in entries:
        require(digest((ROOT / row["path"]).read_bytes()) == row["published_sha256"], f"Export hash mismatch: {row['path']}")
    release = ROOT / "release_manifest.json"
    if release.exists():
        for row in read_json(release)["files"]:
            require(digest((ROOT / row["path"]).read_bytes()) == row["sha256"], f"Release hash mismatch: {row['path']}")
    prompt_hashes = {(r["panel"], r["item_id"]): r for r in read_json(ROOT / "provenance/prompt_hashes.json")}
    for panel in ["objective", "interpretive"]:
        for prompt in read_jsonl(ROOT / f"data/{panel}_prompts.jsonl"):
            row = prompt_hashes[(panel, prompt["item_id"])]
            require(prompt["prompt_sha256"] == row["original_prompt_sha256"], "Original prompt hash changed")
            require(digest(prompt["prompt_text"].encode("utf-8")) == row["published_prompt_sha256"], "Publication prompt hash mismatch")
    return len(entries)


def archive_checks_and_cost(run, prices):
    folder = ROOT / "runs" / run["run_id"]
    members = read_json(folder / "archive_manifest.json")
    cost = 0.0
    usage_count = 0
    attempts = 0
    with zipfile.ZipFile(folder / "execution_archive.zip") as archive:
        require(set(archive.namelist()) == {r["path"] for r in members}, "Archive membership mismatch")
        for row in members:
            data = archive.read(row["path"])
            require(digest(data) == row["published_sha256"], f"Archive hash mismatch: {run['run_id']}/{row['path']}")
            if row["path"].startswith("api_archive/") and row["path"].endswith(".json"):
                attempts += 1
                payload = json.loads(data)
                usage = (payload.get("response") or {}).get("usage")
                if usage:
                    usage_count += 1
                    input_tokens = int(usage.get("input_tokens") or 0)
                    output_tokens = int(usage.get("output_tokens") or 0)
                    cached = min(input_tokens, int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0))
                    cost += ((input_tokens - cached) * prices["input_usd_per_million_tokens"]
                             + cached * prices.get("cached_input_usd_per_million_tokens", prices["input_usd_per_million_tokens"])
                             + output_tokens * prices["output_usd_per_million_tokens"]) / 1_000_000
        receipt = read_json(folder / "original_merge_receipt.json")
        original = next(row for row in members if row["path"] == "rating_records.jsonl")
        require(original["source_sha256"] == receipt["output_sha256"], "Original merged-file hash does not match receipt")
        for slot, expected in receipt["record_file_sha256"].items():
            route = "api" if "-api-r" in slot else "subagent"
            member = next((r for r in members if r["path"] == f"{route}_records/{slot}.json"), None)
            require(member is not None and member["source_sha256"] == expected, f"Original terminal-record hash mismatch: {slot}")
    return {"archived_files": len(members), "api_attempts": attempts, "api_attempts_with_usage": usage_count,
            "api_attempts_without_usage": attempts - usage_count, "api_cost_usd": cost}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build")
    args = parser.parse_args()
    require(sys.version_info[:2] == (3, 12), "Use Python 3.12, the version verified for this release")
    for package, version in [("numpy", "2.3.5"), ("jsonschema", "4.24.0")]:
        require(importlib.metadata.version(package) == version, "Install the versions in requirements.txt")
    output = args.output.resolve()
    protected = [ROOT / name for name in ["data", "runs", "code", "design", "preregistration", "provenance", "results"]]
    require(output != ROOT and not any(output == p or p in output.parents for p in protected), "Choose a separate output directory")
    output.mkdir(parents=True, exist_ok=True)
    exports = verify_files()
    study = read_json(ROOT / "study.json")
    spec = read_json(ROOT / "design/task_family_spec_v004.json")
    schema = read_json(ROOT / "design/rating_record_schema_v002.json")
    prices = read_json(ROOT / "design/budget_plan_v004.json")["price_assumptions"]
    table = []
    costs = []
    total = 0
    unique_items = set()
    for run in study["runs"]:
        name = run["run_id"]
        print(f"Verifying and reproducing {name}", flush=True)
        folder = ROOT / "runs" / name
        saved = read_json(folder / "reported_analysis.json")
        raw_items = ROOT / f"data/{run['panel']}_items.jsonl"
        raw_ratings = folder / "rating_records.jsonl"
        items = read_jsonl(raw_items)
        ratings = read_jsonl(raw_ratings)
        require(len({r["item_id"] for r in items}) == len(items), "Duplicate item")
        unique_items.update(r["item_id"] for r in items)
        slots = {(r["item_id"], r["route"], r["coder_rep"]) for r in ratings}
        require(len(slots) == len(ratings), "Duplicate coding slot")
        expected_slots = {(item["item_id"], route, rep) for item in items
                          for route in ["api", "subagent"] for rep in range(1, run["reps"][route] + 1)}
        require(slots == expected_slots, "Missing or unexpected coding slot")
        counts = {route: dict(Counter(r["terminal_status"] for r in ratings if r["route"] == route)) for route in ["api", "subagent"]}
        compare(counts, run["expected_status_counts"], name + ".status_counts")
        require(Counter(r["route"] for r in ratings) == Counter(run["expected_by_route"]), "Incorrect denominator")
        prompts = {p["item_id"]: p for p in read_jsonl(ROOT / f"data/{run['panel']}_prompts.jsonl")}
        for record in ratings:
            require(record["prompt_sha256"] == prompts[record["item_id"]]["prompt_sha256"], "Coding refers to a different prompt")
        if run["panel"] == "objective":
            target = output / name
            target.mkdir(exist_ok=True)
            normalize.main(["--items", str(raw_items), "--ratings", str(raw_ratings), "--out-items", str(target / "items_normalized.jsonl"), "--out-ratings", str(target / "rating_records_normalized.jsonl")])
            items = read_jsonl(target / "items_normalized.jsonl")
            ratings = read_jsonl(target / "rating_records_normalized.jsonl")
            require(digest((ROOT / "code/answer_equivalence_v001.py").read_bytes()) == saved["normalization"]["normalizer_module_sha256"], "Normalizer code differs from original analysis")
            require(digest((ROOT / "code/normalize_ratings_v001.py").read_bytes()) == saved["normalization"]["normalize_module_sha256"], "Normalization driver differs from original analysis")
            with zipfile.ZipFile(folder / "execution_archive.zip") as archive:
                for filename, actual in [("items_normalized.jsonl", items), ("rating_records_normalized.jsonl", ratings)]:
                    archived = [json.loads(line) for line in archive.read(filename).decode("utf-8").splitlines() if line.strip()]
                    compare(actual, archived, name + ".normalization." + filename)
        result = agreement.analyze(items, ratings, schema, spec, run["comparison"])
        compare(result, saved["delegated_agreement_analysis"], name + ".agreement")
        rows, _ = bootstrap.build_rows_from_ratings(agreement, ratings, items, spec, run["comparison"], rating_schema=schema)
        inferred = {}
        for contrast in ["d", "shift"]:
            inferred[contrast] = bootstrap.run_inference(rows, weights=spec["primary_target_mix"], contrast=contrast,
                seed=run["seed"], b=saved["bootstrap"]["b"], margin=float(spec["equivalence_margin_absolute_agreement"]), comparison=run["comparison"])
            compare(inferred[contrast], saved["bootstrap"]["results"][contrast], name + ".bootstrap." + contrast)
        families = defaultdict(list)
        for row in rows:
            families[row["family_id"]].append(row)
        weight_sum = sum(spec["primary_target_mix"][f] for f in families)
        def weighted(key):
            return sum(spec["primary_target_mix"][f] * sum(row[key] for row in group) / len(group) for f, group in families.items()) / weight_sum
        d = inferred["d"]
        table.append({"panel": run["panel"], "vendor": run["comparison"], "items": len(items),
            "codings_per_item_per_route": run["reps"]["api"], "api_succeeded": counts["api"].get("succeeded", 0),
            "api_scheduled": run["expected_by_route"]["api"], "subagent_succeeded": counts["subagent"].get("succeeded", 0),
            "subagent_scheduled": run["expected_by_route"]["subagent"], "AA": weighted("aa"), "AS": weighted("as"), "SS": weighted("ss"),
            "SS_minus_AA": d["point_estimate"], "ci95_lower": d["bca_ci"]["0.95"][0], "ci95_upper": d["bca_ci"]["0.95"][1],
            "p_value": d["p_value_centered_bootstrap"], "AS_minus_AA": weighted("as_minus_aa")})
        cost = archive_checks_and_cost(run, prices[run["price_key"]])
        costs.append({"run_id": name, "vendor": run["comparison"], **cost})
        write_json(output / name / "reproduced_analysis.json", {"agreement": result, "bootstrap": inferred})
        total += len(ratings)
        print(f"  Matches saved analysis; {len(ratings)} codings; {cost['archived_files']} archive files verified", flush=True)
    require(total == 4200, "Expected 4,200 terminal coding records")
    require(len(unique_items) == 300, "Expected 300 unique items")
    table.sort(key=lambda row: (row["panel"] != "objective", row["vendor"]))
    with (output / "table4.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    write_json(output / "table4.json", table)
    write_json(output / "api_costs.json", costs)
    report = {"success": True, "terminal_codings": total, "unique_items": len(unique_items), "runs": len(table),
              "bootstrap_resamples_per_contrast": 2000, "bootstrap_contrasts_per_run": 2,
              "export_files_verified": exports, "all_archived_agreement_and_inference_fields_match": True,
              "python": platform.python_version(), "numpy": importlib.metadata.version("numpy"),
              "jsonschema": importlib.metadata.version("jsonschema"), "api_calls_made": 0,
              "archive_files_verified": sum(r["archived_files"] for r in costs),
              "api_cost_usd_by_vendor": {v: sum(r["api_cost_usd"] for r in costs if r["vendor"] == v) for v in ["anthropic", "openai"]}}
    write_json(output / "verification.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"REPRODUCTION FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
