#!/usr/bin/env python3
"""Stratified whole-cluster bootstrap inference for the route-agreement benchmark.

Implements design_v004.md Section 6 (nulls, decision rules, and inference): inference
resamples whole dependence clusters (``dependence_cluster_id``) stratified by task
family and never resamples items or coder pairs; draws come from a seeded numpy
Generator with B = 5,000 by default; bias-corrected and accelerated (BCa) intervals are
primary, with acceleration estimated by a leave-one-cluster-out jackknife and
percentile intervals reported as a sensitivity; the point-null p-value uses the
centered (translation) bootstrap; and TOST practical equivalence is evaluated against
the researcher-approved margin of 0.05 absolute agreement (decision D2).

Per design Section 12, the per-item estimand rows
(``family_id``, ``dependence_cluster_id``, ``d``, ``as_minus_aa``, ``shift``) and the
``delta_overall_from_items(item_rows, weights, contrast)`` statistic are consumed from
``agreement_analysis_v004.py``, loaded from this file's directory via
``importlib.util.spec_from_file_location``. Contrasts are accepted in both the field
vocabulary (``d`` / ``as_minus_aa`` / ``shift``) and the estimand vocabulary of the
analysis module (``SS_minus_AA`` / ``AS_minus_AA`` / ``route_shift``); the delegated
helper is probe-verified against the frozen Section 3 formula on the observed rows
before it is trusted, and an internal implementation of the same formula is the
documented fallback (with an explicit note) so precomputed per-item rows remain
analyzable on their own.

Stratification detail: some real dependence clusters span several families (the Clark
County packets). A whole cluster is never split across strata, so clusters are
stratified by their observed family signature: family-pure clusters resample within
their family exactly as Section 6 states, and each cross-family signature forms its
own stratum whose clusters are resampled with replacement holding the observed
cluster count. Every family observed in the data therefore appears in every
replicate, and joint cross-family dependence is preserved.

``--coverage-check`` is a clearly labeled diagnostic mode, not part of the frozen
inference path: design Section 6 requires interval coverage to be simulation-checked
before the confirmatory freeze, and this mode provides that check on a simple
known-truth data-generating process.
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from statistics import NormalDist
from typing import Any, Callable

import numpy as np


HERE = Path(__file__).resolve().parent
DEFAULT_SPEC = HERE / "task_family_spec_v004.json"
DEFAULT_RATING_SCHEMA = HERE / "rating_record_schema_v002.json"
DEFAULT_AGREEMENT_MODULE = HERE / "agreement_analysis_v004.py"
DEFAULT_B = 5000
DEFAULT_ALPHA = 0.05
CONTRASTS = ("d", "as_minus_aa", "shift")
FIELD_TO_ESTIMAND = {"d": "SS_minus_AA", "as_minus_aa": "AS_minus_AA", "shift": "route_shift"}
ESTIMAND_TO_FIELD = {estimand: field for field, estimand in FIELD_TO_ESTIMAND.items()}
COMPARISONS = ("anthropic", "openai")
CI_LEVELS = (0.95, 0.90)
_NORMAL = NormalDist()

# Callables probed on agreement_analysis_v004: the delta helper name is part of that
# module's documented bootstrap contract; the row builders include its analyze()
# entry point plus defensively probed flat-builder spellings.
DELTA_HELPER_CANDIDATES = ("delta_overall_from_items",)
ROW_BUILDER_CANDIDATES = (
    "per_item_rows",
    "compute_per_item_rows",
    "build_per_item_rows",
    "item_rows",
    "build_item_rows",
    "analyze",
)
INTERNAL_STATISTIC_SOURCE = "internal:design_v004_section_3_weighted_family_mean"


class ClusterBootstrapError(RuntimeError):
    """A fail-closed input, contract, or inference error."""


# ---------------------------------------------------------------------------
# IO helpers (house style: UTF-8 everywhere, atomic writes via os.replace).
# ---------------------------------------------------------------------------


def load_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClusterBootstrapError(f"cannot read valid JSON from {path}: {exc}") from exc


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ClusterBootstrapError(f"{path}:{line_number}: {exc}") from exc
    except OSError as exc:
        raise ClusterBootstrapError(f"cannot read {path}: {exc}") from exc
    return rows


def atomic_write_json(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


# ---------------------------------------------------------------------------
# Loading the frozen per-item estimand module (design Sections 6 and 12).
# ---------------------------------------------------------------------------


def load_agreement_analysis(path: Path | None = None) -> Any:
    """Load ``agreement_analysis_v004.py`` beside this file (design Section 12)."""
    module_path = Path(path) if path is not None else DEFAULT_AGREEMENT_MODULE
    if not module_path.is_file():
        raise ClusterBootstrapError(f"agreement analysis module not found: {module_path}")
    name = f"_cluster_bootstrap_loaded_{module_path.stem}"
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise ClusterBootstrapError(f"cannot build an import spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # the loaded file is data, so fail closed on any error
        raise ClusterBootstrapError(f"failed to execute {module_path}: {exc}") from exc
    return module


def _bind_call(
    func: Callable[..., Any],
    semantic_values: dict[str, Any],
    alias_map: dict[str, tuple[str, ...]],
    required: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Bind semantic inputs to a callable's parameters by alias-name matching.

    Supports design Section 12 integration with ``agreement_analysis_v004`` without
    hard-coding that module's exact keyword spelling. Returns the keyword arguments and
    a map from semantic name to the bound parameter name; raises when a required
    semantic input cannot be placed or a mandatory parameter cannot be satisfied.
    """
    label = getattr(func, "__name__", repr(func))
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError) as exc:
        raise ClusterBootstrapError(f"cannot inspect {label!r}: {exc}") from exc
    kwargs: dict[str, Any] = {}
    bound: dict[str, str] = {}
    for name, parameter in signature.parameters.items():
        if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        matched = None
        for semantic, aliases in alias_map.items():
            if semantic in semantic_values and semantic not in bound and name in aliases:
                matched = semantic
                break
        if matched is not None:
            if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
                raise ClusterBootstrapError(
                    f"{label!r} uses a positional-only parameter {name!r}; cannot bind safely"
                )
            kwargs[name] = semantic_values[matched]
            bound[matched] = name
        elif parameter.default is inspect.Parameter.empty:
            raise ClusterBootstrapError(
                f"cannot satisfy required parameter {name!r} of {label!r}"
            )
    missing = [semantic for semantic in required if semantic not in bound]
    if missing:
        raise ClusterBootstrapError(f"{label!r} does not accept required inputs: {missing}")
    return kwargs, bound


def make_internal_statistic(
    weights: dict[str, float], value_key: str
) -> Callable[[list[dict[str, Any]]], float]:
    """Frozen fallback statistic: design Section 3, ``Delta = sum_f w_f D_f``.

    ``D_f`` is the mean of ``value_key`` over the items of family ``f`` and ``w_f`` the
    frozen target-mix weights. When some spec families carry no items (tiny pilots),
    the sum runs over observed families with the frozen weights renormalized to those
    families, which reduces to the exact Section 3 formula whenever every family is
    present; the renormalization keeps the estimate on the agreement scale instead of
    silently shrinking it toward zero.
    """

    def statistic(items: list[dict[str, Any]]) -> float:
        if not items:
            raise ClusterBootstrapError("cannot compute the statistic on zero items")
        sums: dict[str, float] = {}
        counts: dict[str, int] = {}
        for row in items:
            family = row.get("family_id")
            if family not in weights:
                raise ClusterBootstrapError(f"item family is not in the weights: {family!r}")
            value = _finite_value(row, value_key)
            sums[family] = sums.get(family, 0.0) + value
            counts[family] = counts.get(family, 0) + 1
        total_weight = sum(weights[family] for family in counts)
        if total_weight <= 0.0:
            raise ClusterBootstrapError("observed families carry zero total weight")
        weighted = sum(
            weights[family] * (sums[family] / counts[family]) for family in sorted(counts)
        )
        return weighted / total_weight

    return statistic


def make_module_statistic(
    module: Any,
    weights: dict[str, float],
    spec: dict[str, Any] | None,
    value_key: str,
    probe_items: list[dict[str, Any]],
) -> tuple[Callable[[list[dict[str, Any]]], float] | None, str | None, str | None]:
    """Wrap ``delta_overall_from_items`` from the loaded module (design Section 12).

    The helper takes the contrast in its own estimand vocabulary
    (``SS_minus_AA`` / ``AS_minus_AA`` / ``route_shift``), so candidate spellings are
    derived from the module's ``CONTRAST_FIELDS`` map (falling back to the frozen
    field-to-estimand map, then the raw field name) and each candidate is
    probe-verified on the observed rows against the frozen Section 3 formula before it
    is trusted. Returns ``(statistic, description, None)`` on success or
    ``(None, None, reason)`` when the module cannot serve the requested contrast.
    """
    func = None
    helper_name = None
    for candidate in DELTA_HELPER_CANDIDATES:
        attribute = getattr(module, candidate, None)
        if callable(attribute):
            func = attribute
            helper_name = candidate
            break
    if func is None:
        return None, None, (
            "agreement module exposes none of the delta helper candidates: "
            + ", ".join(DELTA_HELPER_CANDIDATES)
        )
    semantic_values: dict[str, Any] = {
        "items": [],
        "weights": weights,
        "value_key": value_key,
    }
    if spec is not None:
        semantic_values["spec"] = spec
    alias_map = {
        "items": ("items", "rows", "item_rows", "scored_items", "per_item_rows"),
        "weights": ("weights", "target_mix", "family_weights", "mix"),
        "spec": ("spec", "family_spec", "task_family_spec"),
        "value_key": ("value_key", "contrast", "contrast_key", "key", "field", "column"),
    }
    try:
        kwargs_template, bound = _bind_call(
            func, semantic_values, alias_map, required=("items",)
        )
    except ClusterBootstrapError as exc:
        return None, None, str(exc)
    items_parameter = bound["items"]
    contrast_parameter = bound.get("value_key")

    try:
        reference = make_internal_statistic(weights, value_key)(probe_items)
    except ClusterBootstrapError as exc:
        return None, None, f"cannot evaluate the probe rows: {exc}"

    if contrast_parameter is None:
        if value_key != "d":
            return None, None, (
                f"{helper_name!r} accepts no contrast parameter, so only the primary "
                f"contrast can be delegated; requested {value_key!r}"
            )
        contrast_candidates: list[str | None] = [None]
    else:
        contrast_candidates = []
        fields_map = getattr(module, "CONTRAST_FIELDS", None)
        if isinstance(fields_map, dict):
            for name, field in sorted(fields_map.items()):
                if field == value_key and name not in contrast_candidates:
                    contrast_candidates.append(name)
        mapped = FIELD_TO_ESTIMAND.get(value_key)
        if mapped is not None and mapped not in contrast_candidates:
            contrast_candidates.append(mapped)
        if value_key not in contrast_candidates:
            contrast_candidates.append(value_key)

    def build_statistic(contrast_argument: str | None):
        def statistic(items: list[dict[str, Any]]) -> float:
            call_kwargs = dict(kwargs_template)
            call_kwargs[items_parameter] = items
            if contrast_argument is not None:
                call_kwargs[contrast_parameter] = contrast_argument
            try:
                value = func(**call_kwargs)
            except ClusterBootstrapError:
                raise
            except Exception as exc:
                raise ClusterBootstrapError(f"{helper_name} failed: {exc}") from exc
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise ClusterBootstrapError(
                    f"{helper_name} returned a non-numeric value: {value!r}"
                ) from exc
            if not math.isfinite(value):
                raise ClusterBootstrapError(f"{helper_name} returned a non-finite value")
            return value

        return statistic

    failures: list[str] = []
    for contrast_argument in contrast_candidates:
        statistic = build_statistic(contrast_argument)
        spelling = "default" if contrast_argument is None else contrast_argument
        try:
            probe = statistic(probe_items)
        except ClusterBootstrapError as exc:
            failures.append(f"{spelling}: {exc}")
            continue
        if abs(probe - reference) > 1e-6:
            failures.append(
                f"{spelling}: probe value {probe!r} does not match the frozen "
                f"Section 3 formula {reference!r}"
            )
            continue
        return statistic, f"{helper_name}[{spelling}]", None
    return None, None, (
        f"no contrast spelling for {helper_name!r} passed the probe: "
        + "; ".join(failures)
    )


def select_statistic(
    module: Any | None,
    weights: dict[str, float],
    spec: dict[str, Any] | None,
    value_key: str,
    probe_items: list[dict[str, Any]],
) -> tuple[Callable[[list[dict[str, Any]]], float], str, str | None]:
    """Choose the statistic per design Section 12: the frozen module helper when it
    passes the probe, otherwise the documented internal Section 3 fallback.

    Returns ``(statistic, source_label, note)``.
    """
    if module is not None:
        statistic, description, reason = make_module_statistic(
            module, weights, spec, value_key, probe_items
        )
        if statistic is not None:
            module_name = Path(getattr(module, "__file__", "agreement_analysis_v004")).name
            return statistic, f"{module_name}:{description}", None
        note = f"fell back to the internal Section 3 formula: {reason}"
        return make_internal_statistic(weights, value_key), INTERNAL_STATISTIC_SOURCE, note
    return make_internal_statistic(weights, value_key), INTERNAL_STATISTIC_SOURCE, None


def build_rows_from_ratings(
    module: Any,
    ratings: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    spec: dict[str, Any],
    comparison: str,
    rating_schema: dict[str, Any] | None = None,
    phase: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Ask the loaded agreement module for its per-item rows (design Section 12).

    Ratings are pre-filtered to the requested vendor comparison because the two
    comparisons are analyzed separately and never pooled (design Section 2). The
    module's ``analyze`` entry point is supported directly: its report's phase blocks
    carry ``item_rows``; when more than one benchmark phase is present the caller must
    name one (``--phase``) because phases are analyzed separately.
    """
    filtered = [row for row in ratings if row.get("comparison", comparison) == comparison]
    if not filtered:
        raise ClusterBootstrapError(f"no ratings remain for comparison {comparison!r}")
    semantic_values: dict[str, Any] = {
        "ratings": filtered,
        "tasks": tasks,
        "spec": spec,
        "comparison": comparison,
        "phase": phase,
    }
    if rating_schema is not None:
        semantic_values["rating_schema"] = rating_schema
    alias_map = {
        "ratings": ("ratings", "rating_rows", "records", "rating_records"),
        "tasks": ("tasks", "items", "task_rows", "manifest", "item_manifest"),
        "spec": ("spec", "family_spec", "task_family_spec"),
        "comparison": ("comparison", "vendor_pair", "pair", "vendor"),
        "rating_schema": ("rating_schema", "ratings_schema", "record_schema", "schema"),
        "phase": ("phase", "benchmark_phase", "phase_filter"),
    }
    failures: list[str] = []
    for candidate in ROW_BUILDER_CANDIDATES:
        func = getattr(module, candidate, None)
        if not callable(func):
            continue
        try:
            kwargs, _bound = _bind_call(func, semantic_values, alias_map, required=("ratings",))
        except ClusterBootstrapError as exc:
            failures.append(f"{candidate}: {exc}")
            continue
        try:
            rows = func(**kwargs)
        except ClusterBootstrapError:
            raise
        except Exception as exc:
            raise ClusterBootstrapError(f"{candidate} failed: {exc}") from exc
        rows = _extract_item_rows(rows, candidate)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ClusterBootstrapError(
                f"{candidate} did not return a list of per-item row objects"
            )
        return rows, candidate
    raise ClusterBootstrapError(
        "agreement module exposes no usable per-item row builder; tried "
        + ", ".join(ROW_BUILDER_CANDIDATES)
        + (f"; binding failures: {failures}" if failures else "")
    )


def _extract_item_rows(rows: Any, candidate: str) -> Any:
    """Unwrap per-item rows from an analyze-style report when necessary."""
    if not isinstance(rows, dict):
        return rows
    phases = rows.get("phases")
    if isinstance(phases, dict):
        blocks = {
            name: block["item_rows"]
            for name, block in phases.items()
            if isinstance(block, dict) and isinstance(block.get("item_rows"), list)
        }
        if not blocks:
            raise ClusterBootstrapError(
                f"{candidate} returned phases without item_rows lists"
            )
        if len(blocks) > 1:
            raise ClusterBootstrapError(
                f"{candidate} returned multiple benchmark phases {sorted(blocks)}; "
                "phases are analyzed separately - pass --phase to choose one"
            )
        return next(iter(blocks.values()))
    for key in ("per_item_rows", "item_rows", "items", "rows"):
        if isinstance(rows.get(key), list):
            return rows[key]
    return rows


# ---------------------------------------------------------------------------
# Stratified whole-cluster resampling (design Section 6).
# ---------------------------------------------------------------------------


def _finite_value(row: dict[str, Any], value_key: str) -> float:
    if value_key not in row:
        raise ClusterBootstrapError(
            f"per-item row is missing the contrast key {value_key!r}: "
            f"{sorted(row.keys())}"
        )
    raw = row[value_key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ClusterBootstrapError(f"contrast {value_key!r} is not numeric: {raw!r}")
    value = float(raw)
    if not math.isfinite(value):
        raise ClusterBootstrapError(f"contrast {value_key!r} is not finite: {raw!r}")
    return value


def build_cluster_index(
    items: list[dict[str, Any]],
    value_key: str,
    weights: dict[str, float] | None = None,
) -> list[tuple[tuple[str, ...], list[tuple[str, list[dict[str, Any]]]]]]:
    """Group per-item rows into whole-cluster strata (design Section 6).

    The resampling unit is the whole ``dependence_cluster_id``. Strata are the
    observed family signatures of the clusters: a family-pure cluster sits in its
    family's stratum (the plain Section 6 case), and a cluster spanning several
    families (the Clark packets) forms a stratum with the other clusters that share
    exactly that family set, so it is resampled whole and never split. Ordering is
    deterministic (sorted signatures, sorted cluster ids, input row order within a
    cluster). Every row must carry a finite value for the requested contrast so a
    malformed row cannot silently enter a replicate.
    """
    if not isinstance(items, list) or not items:
        raise ClusterBootstrapError("per-item rows must be a nonempty list")
    cluster_rows: dict[str, list[dict[str, Any]]] = {}
    cluster_families: dict[str, set[str]] = {}
    for position, row in enumerate(items):
        if not isinstance(row, dict):
            raise ClusterBootstrapError(f"per-item row {position} is not an object")
        family = row.get("family_id")
        if not isinstance(family, str) or not family:
            raise ClusterBootstrapError(f"per-item row {position} has no family_id")
        if weights is not None and family not in weights:
            raise ClusterBootstrapError(
                f"per-item row {position} names a family outside the frozen weights: "
                f"{family!r}"
            )
        cluster = row.get("dependence_cluster_id")
        if isinstance(cluster, int) and not isinstance(cluster, bool):
            cluster = str(cluster)
        if not isinstance(cluster, str) or not cluster:
            raise ClusterBootstrapError(
                f"per-item row {position} has no dependence_cluster_id"
            )
        _finite_value(row, value_key)
        cluster_rows.setdefault(cluster, []).append(row)
        cluster_families.setdefault(cluster, set()).add(family)
    strata: dict[tuple[str, ...], list[str]] = {}
    for cluster in sorted(cluster_rows):
        signature = tuple(sorted(cluster_families[cluster]))
        strata.setdefault(signature, []).append(cluster)
    return [
        (
            signature,
            [(cluster, cluster_rows[cluster]) for cluster in strata[signature]],
        )
        for signature in sorted(strata)
    ]


def resample_replicate(
    family_clusters: list[tuple[tuple[str, ...], list[tuple[str, list[dict[str, Any]]]]]],
    rng: np.random.Generator,
) -> list[dict[str, Any]]:
    """Draw one stratified whole-cluster bootstrap replicate (design Section 6).

    Within each stratum (sorted signature order, one ``rng.integers`` call per
    stratum) the observed clusters are resampled with replacement, keeping the
    observed number of clusters; an item enters the replicate as many times as its
    cluster is drawn. Items and coder pairs are never resampled directly.
    """
    replicate: list[dict[str, Any]] = []
    for _signature, clusters in family_clusters:
        count = len(clusters)
        draws = rng.integers(0, count, size=count)
        for index in draws:
            replicate.extend(clusters[int(index)][1])
    return replicate


def bootstrap_distribution(
    family_clusters: list[tuple[tuple[str, ...], list[tuple[str, list[dict[str, Any]]]]]],
    statistic: Callable[[list[dict[str, Any]]], float],
    b: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Return the B seeded stratified whole-cluster bootstrap statistics."""
    if b < 1:
        raise ClusterBootstrapError(f"B must be a positive integer, got {b}")
    values = np.empty(b, dtype=np.float64)
    for replicate_index in range(b):
        replicate = resample_replicate(family_clusters, rng)
        try:
            values[replicate_index] = statistic(replicate)
        except ClusterBootstrapError as exc:
            raise ClusterBootstrapError(
                f"bootstrap replicate {replicate_index} failed: {exc}"
            ) from exc
    return values


def jackknife_values(
    family_clusters: list[tuple[tuple[str, ...], list[tuple[str, list[dict[str, Any]]]]]],
    statistic: Callable[[list[dict[str, Any]]], float],
) -> list[float]:
    """Leave-one-cluster-out jackknife statistics for the BCa acceleration.

    Design Section 6: each jackknife replicate drops one whole cluster from its
    stratum while every other stratum stays intact.
    """
    flattened: list[list[dict[str, Any]]] = [
        rows for _signature, clusters in family_clusters for _cluster, rows in clusters
    ]
    if len(flattened) < 2:
        raise ClusterBootstrapError(
            "the jackknife needs at least two dependence clusters overall"
        )
    values: list[float] = []
    for drop_index in range(len(flattened)):
        replicate: list[dict[str, Any]] = []
        for cluster_index, rows in enumerate(flattened):
            if cluster_index != drop_index:
                replicate.extend(rows)
        values.append(statistic(replicate))
    return values


def _family_and_strata_metadata(
    family_clusters: list[tuple[tuple[str, ...], list[tuple[str, list[dict[str, Any]]]]]],
) -> tuple[dict[str, dict[str, int]], list[dict[str, Any]], int]:
    families: dict[str, dict[str, int]] = {}
    strata_meta: list[dict[str, Any]] = []
    total_clusters = 0
    for signature, clusters in family_clusters:
        total_clusters += len(clusters)
        strata_meta.append({"families": list(signature), "clusters": len(clusters)})
        for _cluster, rows in clusters:
            present = sorted({row["family_id"] for row in rows})
            for family in present:
                families.setdefault(family, {"clusters": 0, "items": 0})
                families[family]["clusters"] += 1
            for row in rows:
                families[row["family_id"]]["items"] += 1
    return dict(sorted(families.items())), strata_meta, total_clusters


# ---------------------------------------------------------------------------
# Interval and p-value machinery (design Section 6).
# ---------------------------------------------------------------------------


def percentile_interval(boot: np.ndarray, level: float) -> list[float]:
    """Equal-tailed percentile interval (design Section 6 sensitivity interval)."""
    if not 0.0 < level < 1.0:
        raise ClusterBootstrapError(f"confidence level must be in (0, 1), got {level}")
    tail = (1.0 - level) / 2.0
    low, high = np.quantile(boot, [tail, 1.0 - tail])
    return [float(low), float(high)]


def bca_bias_and_acceleration(
    boot: np.ndarray,
    theta_hat: float,
    jackknife: list[float] | None,
) -> tuple[float, float, list[str]]:
    """Estimate the BCa bias correction ``z0`` and acceleration ``a``.

    ``z0`` comes from the bootstrap distribution with the midpoint tie convention and
    is clipped away from 0/1 by a +1-style guard; ``a`` comes from the
    leave-one-cluster-out jackknife (design Section 6). Failures degrade to ``a = 0``
    (bias-corrected-only interval) with an explicit note rather than crashing.
    """
    notes: list[str] = []
    b = int(boot.size)
    below = int(np.count_nonzero(boot < theta_hat))
    ties = int(np.count_nonzero(boot == theta_hat))
    proportion = (below + 0.5 * ties) / b
    lower_guard = 1.0 / (b + 1.0)
    upper_guard = b / (b + 1.0)
    if proportion < lower_guard or proportion > upper_guard:
        notes.append("z0_proportion_clipped_extreme_bias")
        proportion = min(max(proportion, lower_guard), upper_guard)
    z0 = _NORMAL.inv_cdf(proportion)
    acceleration = 0.0
    if jackknife is None:
        notes.append("acceleration_fallback_zero_no_jackknife")
    elif len(jackknife) < 2:
        notes.append("acceleration_fallback_zero_single_jackknife_value")
    else:
        jack = np.asarray(jackknife, dtype=np.float64)
        deviations = float(np.mean(jack)) - jack
        denominator = 6.0 * float(np.sum(deviations**2)) ** 1.5
        if denominator == 0.0:
            notes.append("acceleration_fallback_zero_variance_jackknife")
        else:
            acceleration = float(np.sum(deviations**3)) / denominator
    return float(z0), float(acceleration), notes


def bca_interval(
    boot: np.ndarray,
    z0: float,
    acceleration: float,
    level: float,
) -> tuple[list[float], dict[str, Any]]:
    """BCa interval endpoints at the given level (design Section 6 primary interval)."""
    if not 0.0 < level < 1.0:
        raise ClusterBootstrapError(f"confidence level must be in (0, 1), got {level}")
    notes: list[str] = []
    tail = (1.0 - level) / 2.0
    z_low = _NORMAL.inv_cdf(tail)
    z_high = -z_low
    adjusted: list[float] = []
    for z_tail, default_tail in ((z_low, tail), (z_high, 1.0 - tail)):
        denominator = 1.0 - acceleration * (z0 + z_tail)
        if denominator <= 0.0:
            notes.append("bca_denominator_nonpositive_used_percentile_tail")
            adjusted.append(default_tail)
            continue
        adjusted.append(_NORMAL.cdf(z0 + (z0 + z_tail) / denominator))
    alpha_low, alpha_high = adjusted
    if alpha_low > alpha_high:
        notes.append("bca_adjusted_tails_crossed_and_were_sorted")
        alpha_low, alpha_high = alpha_high, alpha_low
    low, high = np.quantile(boot, [alpha_low, alpha_high])
    diagnostics = {
        "adjusted_lower_tail": float(alpha_low),
        "adjusted_upper_tail": float(alpha_high),
        "notes": notes,
    }
    return [float(low), float(high)], diagnostics


def centered_null_pvalue(boot: np.ndarray, theta_hat: float) -> float:
    """Centered (translation) bootstrap two-sided p-value for ``H0: theta = 0``.

    Design Section 6: shift the bootstrap distribution to be centered at zero
    (``T* = theta_b - theta_hat``) and compare the observed estimate against it with
    the +1 finite-sample correction:
    ``p = min(1, 2 * min((1 + #{T* >= theta_hat}) / (B + 1),
    (1 + #{T* <= theta_hat}) / (B + 1)))``.
    """
    b = int(boot.size)
    centered = boot - theta_hat
    count_upper = int(np.count_nonzero(centered >= theta_hat))
    count_lower = int(np.count_nonzero(centered <= theta_hat))
    p_value = 2.0 * min((1 + count_upper) / (b + 1.0), (1 + count_lower) / (b + 1.0))
    return float(min(1.0, p_value))


def interpret(
    contrast: str,
    theta_hat: float,
    point_rejected: bool,
    practically_equivalent: bool,
) -> dict[str, Any]:
    """Prespecified interpretation map (design Section 6).

    Reject the point null with a negative primary contrast = sub-agent route less
    reproducible; positive = more reproducible; TOST rejects = practically equivalent;
    neither = inconclusive; when both reject, both outcomes are reported verbatim as a
    small but nonzero difference. The route-direction language is defined for the
    primary contrast ``d`` (SS - AA); the secondary contrasts get neutral direction
    wording.
    """
    if contrast == "d":
        if theta_hat < 0.0:
            direction_token = "subagent_route_less_reproducible"
            direction_reading = (
                "the sub-agent route is less internally reproducible than the API route"
            )
        elif theta_hat > 0.0:
            direction_token = "subagent_route_more_reproducible"
            direction_reading = (
                "the sub-agent route is more internally reproducible than the API route"
            )
        else:
            direction_token = "no_direction"
            direction_reading = "the estimated contrast is exactly zero"
    else:
        if theta_hat < 0.0:
            direction_token = f"{contrast}_below_zero"
            direction_reading = f"the {contrast} contrast is below zero"
        elif theta_hat > 0.0:
            direction_token = f"{contrast}_above_zero"
            direction_reading = f"the {contrast} contrast is above zero"
        else:
            direction_token = "no_direction"
            direction_reading = "the estimated contrast is exactly zero"
    if point_rejected and practically_equivalent:
        verdict = "practically_equivalent_and_point_null_rejected"
        reading = (
            "point null and TOST both rejected: a small but nonzero difference "
            f"({direction_reading}) inside the practical-equivalence margin"
        )
    elif point_rejected:
        verdict = direction_token
        reading = f"point null rejected: {direction_reading}"
    elif practically_equivalent:
        verdict = "practically_equivalent"
        reading = (
            "TOST rejected: the contrast is practically equivalent to zero within the margin"
        )
    else:
        verdict = "inconclusive"
        reading = "neither the point null nor the equivalence null was rejected"
    return {
        "verdict": verdict,
        "point_null_rejected": bool(point_rejected),
        "practically_equivalent": bool(practically_equivalent),
        "reading": reading,
    }


# ---------------------------------------------------------------------------
# The frozen inference entry point (design Section 6).
# ---------------------------------------------------------------------------


def _validate_weights(weights: dict[str, float]) -> dict[str, float]:
    if not isinstance(weights, dict) or not weights:
        raise ClusterBootstrapError("family weights must be a nonempty object")
    cleaned: dict[str, float] = {}
    for family, weight in weights.items():
        if not isinstance(family, str) or not family:
            raise ClusterBootstrapError(f"weight key is not a family id: {family!r}")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise ClusterBootstrapError(f"weight for {family!r} is not numeric: {weight!r}")
        value = float(weight)
        if not math.isfinite(value) or value <= 0.0:
            raise ClusterBootstrapError(f"weight for {family!r} must be positive: {weight!r}")
        cleaned[family] = value
    if abs(sum(cleaned.values()) - 1.0) > 1e-9:
        raise ClusterBootstrapError("family weights must sum to one")
    return cleaned


def run_inference(
    items: list[dict[str, Any]],
    *,
    weights: dict[str, float],
    contrast: str,
    seed: int,
    b: int = DEFAULT_B,
    margin: float = 0.05,
    alpha: float = DEFAULT_ALPHA,
    statistic: Callable[[list[dict[str, Any]]], float] | None = None,
    statistic_source: str | None = None,
    comparison: str | None = None,
) -> dict[str, Any]:
    """Run the frozen stratified whole-cluster bootstrap inference (design Section 6).

    Point estimate, percentile and BCa intervals at 95% (point null, alpha 0.05) and
    90% (TOST at alpha 0.05 with the decision-D2 margin), the centered-bootstrap
    point-null p-value, the practical-equivalence boolean on the BCa 90% interval, and
    the prespecified interpretation-map verdict. Degenerate zero-variance bootstrap
    distributions return the documented collapsed conventions instead of crashing.
    """
    contrast = ESTIMAND_TO_FIELD.get(contrast, contrast)
    if contrast not in CONTRASTS:
        raise ClusterBootstrapError(f"contrast must be one of {CONTRASTS}, got {contrast!r}")
    if not isinstance(b, int) or isinstance(b, bool) or b < 1:
        raise ClusterBootstrapError(f"B must be a positive integer, got {b!r}")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ClusterBootstrapError(f"seed must be an integer, got {seed!r}")
    if not (isinstance(margin, (int, float)) and math.isfinite(margin) and margin > 0.0):
        raise ClusterBootstrapError(f"margin must be a positive number, got {margin!r}")
    if not (isinstance(alpha, float) and 0.0 < alpha < 1.0):
        raise ClusterBootstrapError(f"alpha must be in (0, 1), got {alpha!r}")
    weights = _validate_weights(weights)
    family_clusters = build_cluster_index(items, contrast, weights)
    if statistic is None:
        statistic = make_internal_statistic(weights, contrast)
        statistic_source = statistic_source or INTERNAL_STATISTIC_SOURCE
    else:
        statistic_source = statistic_source or "caller_supplied"
    internal_statistic = make_internal_statistic(weights, contrast)

    theta_hat = float(statistic(items))
    internal_delta = float(internal_statistic(items))
    rng = np.random.default_rng(seed)
    boot = bootstrap_distribution(family_clusters, statistic, b, rng)

    families_meta, strata_meta, n_clusters = _family_and_strata_metadata(family_clusters)
    estimand = FIELD_TO_ESTIMAND[contrast]
    result: dict[str, Any] = {
        "tool": "cluster_bootstrap_v001",
        "design_reference": "design_v004.md Section 6",
        "freeze_status": (
            "pilot implementation; coverage must be simulation-checked before the "
            "confirmatory freeze and the implementation frozen before unblinding"
        ),
        "comparison": comparison,
        "contrast": contrast,
        "contrast_estimand": estimand,
        "b": int(b),
        "seed": int(seed),
        "alpha": float(alpha),
        "equivalence_margin": float(margin),
        "statistic_source": statistic_source,
        "n_items": len(items),
        "n_clusters": int(n_clusters),
        "n_families": len(families_meta),
        "families": families_meta,
        "strata": strata_meta,
        "point_estimate": theta_hat,
        "internal_check_delta": internal_delta,
        "internal_check_matches": bool(abs(internal_delta - theta_hat) <= 1e-9),
        "bootstrap_mean": float(np.mean(boot)),
        "bootstrap_sd": float(np.std(boot, ddof=1)) if b > 1 else 0.0,
    }

    degenerate = bool(np.all(boot == boot[0]))
    if degenerate:
        collapsed = [theta_hat, theta_hat]
        equivalent = bool(-margin <= collapsed[0] and collapsed[1] <= margin)
        result.update(
            {
                "degenerate": True,
                "degenerate_note": (
                    "all bootstrap replicates are identical (zero resampling "
                    "variance); by convention the intervals collapse to the point "
                    "estimate, the centered point-null p-value is 1.0 (a "
                    "zero-variance bootstrap carries no evidence against the null), "
                    "and every downstream boolean is computed mechanically from the "
                    "collapsed interval"
                ),
                "bootstrap_constant_value": float(boot[0]),
                "percentile_ci": {"0.90": list(collapsed), "0.95": list(collapsed)},
                "bca_ci": {"0.90": list(collapsed), "0.95": list(collapsed)},
                "bca_diagnostics": {
                    "z0": 0.0,
                    "acceleration": 0.0,
                    "jackknife_clusters": 0,
                    "notes": ["degenerate_zero_bootstrap_variance"],
                },
                "p_value_centered_bootstrap": 1.0,
                "point_null": {
                    "h0": f"{estimand} = 0",
                    "alpha": float(alpha),
                    "rejected": False,
                },
                "tost": {
                    "alpha": float(alpha),
                    "margin": float(margin),
                    "ci_level": 0.90,
                    "bca_90": list(collapsed),
                    "percentile_90": list(collapsed),
                    "equivalent_at_margin": equivalent,
                    "equivalent_at_margin_percentile": equivalent,
                },
                "interpretation": interpret(contrast, theta_hat, False, equivalent),
            }
        )
        return result

    jack: list[float] | None
    jackknife_note: str | None = None
    try:
        jack = jackknife_values(family_clusters, statistic)
    except ClusterBootstrapError as exc:
        jack = None
        jackknife_note = str(exc)
    z0, acceleration, bca_notes = bca_bias_and_acceleration(boot, theta_hat, jack)
    if jackknife_note is not None:
        bca_notes.append(f"jackknife_failed: {jackknife_note}")

    percentile_by_level: dict[str, list[float]] = {}
    bca_by_level: dict[str, list[float]] = {}
    for level in CI_LEVELS:
        key = f"{level:.2f}"
        percentile_by_level[key] = percentile_interval(boot, level)
        endpoints, diagnostics = bca_interval(boot, z0, acceleration, level)
        bca_by_level[key] = endpoints
        bca_notes.extend(f"level_{key}:{note}" for note in diagnostics["notes"])

    p_value = centered_null_pvalue(boot, theta_hat)
    point_rejected = bool(p_value < alpha)
    bca_90 = bca_by_level["0.90"]
    percentile_90 = percentile_by_level["0.90"]
    equivalent = bool(-margin <= bca_90[0] and bca_90[1] <= margin)
    equivalent_percentile = bool(
        -margin <= percentile_90[0] and percentile_90[1] <= margin
    )

    result.update(
        {
            "degenerate": False,
            "degenerate_note": None,
            "percentile_ci": percentile_by_level,
            "bca_ci": bca_by_level,
            "bca_diagnostics": {
                "z0": z0,
                "acceleration": acceleration,
                "jackknife_clusters": len(jack) if jack is not None else 0,
                "notes": bca_notes,
            },
            "p_value_centered_bootstrap": p_value,
            "point_null": {
                "h0": f"{estimand} = 0",
                "alpha": float(alpha),
                "rejected": point_rejected,
            },
            "tost": {
                "alpha": float(alpha),
                "margin": float(margin),
                "ci_level": 0.90,
                "bca_90": bca_90,
                "percentile_90": percentile_90,
                "equivalent_at_margin": equivalent,
                "equivalent_at_margin_percentile": equivalent_percentile,
            },
            "interpretation": interpret(contrast, theta_hat, point_rejected, equivalent),
        }
    )
    return result


# ---------------------------------------------------------------------------
# Coverage diagnostic (design Section 6; labeled not-frozen).
# ---------------------------------------------------------------------------


def coverage_check(
    *,
    sims: int = 200,
    b: int = 500,
    seed: int,
    weights: dict[str, float],
    clusters_per_family: int = 8,
    items_per_cluster: int = 3,
    cluster_sd: float = 0.08,
    item_sd: float = 0.05,
    true_delta: float = 0.0,
    margin: float = 0.05,
    alpha: float = DEFAULT_ALPHA,
) -> dict[str, Any]:
    """Monte Carlo coverage diagnostic for the BCa intervals (design Section 6).

    Simulates a simple known-truth DGP: within every family, ``clusters_per_family``
    dependence clusters receive independent normal cluster effects on ``d``
    (``N(0, cluster_sd^2)``) and each of the ``items_per_cluster`` items adds
    ``N(0, item_sd^2)`` item noise around ``true_delta``, so the estimand truly equals
    ``true_delta``. Reports empirical coverage of the nominal 90% and 95% BCa
    intervals (percentile coverage included as context). This mode is a diagnostic
    tool only and is NOT part of the frozen inference path.
    """
    if not isinstance(sims, int) or isinstance(sims, bool) or sims < 1:
        raise ClusterBootstrapError(f"sims must be a positive integer, got {sims!r}")
    if clusters_per_family < 1 or items_per_cluster < 1:
        raise ClusterBootstrapError("clusters_per_family and items_per_cluster must be >= 1")
    if cluster_sd < 0.0 or item_sd < 0.0:
        raise ClusterBootstrapError("cluster_sd and item_sd must be nonnegative")
    weights = _validate_weights(weights)
    master = np.random.default_rng(seed)
    covered = {"bca_0.90": 0, "bca_0.95": 0, "percentile_0.90": 0, "percentile_0.95": 0}
    point_rejections = 0
    degenerate_sims = 0
    for _sim in range(sims):
        rows: list[dict[str, Any]] = []
        for family in sorted(weights):
            for cluster_index in range(clusters_per_family):
                effect = float(master.normal(0.0, cluster_sd))
                cluster_id = f"{family}_cluster_{cluster_index:03d}"
                for _item in range(items_per_cluster):
                    rows.append(
                        {
                            "family_id": family,
                            "dependence_cluster_id": cluster_id,
                            "d": float(true_delta + effect + master.normal(0.0, item_sd)),
                        }
                    )
        replicate_seed = int(master.integers(0, 2**63 - 1))
        result = run_inference(
            rows,
            weights=weights,
            contrast="d",
            seed=replicate_seed,
            b=b,
            margin=margin,
            alpha=alpha,
        )
        if result["degenerate"]:
            degenerate_sims += 1
        for method in ("bca", "percentile"):
            for level_key in ("0.90", "0.95"):
                low, high = result[f"{method}_ci"][level_key]
                if low <= true_delta <= high:
                    covered[f"{method}_{level_key}"] += 1
        if result["point_null"]["rejected"]:
            point_rejections += 1
    return {
        "tool": "cluster_bootstrap_v001",
        "mode": "coverage_check",
        "design_reference": "design_v004.md Section 6 (pre-freeze coverage check)",
        "not_frozen": True,
        "diagnostic_note": (
            "Monte Carlo diagnostic on a simple known-truth DGP; it informs the "
            "confirmatory freeze decision and is not itself frozen inference output"
        ),
        "parameters": {
            "sims": int(sims),
            "b": int(b),
            "seed": int(seed),
            "families": sorted(weights),
            "clusters_per_family": int(clusters_per_family),
            "items_per_cluster": int(items_per_cluster),
            "cluster_sd": float(cluster_sd),
            "item_sd": float(item_sd),
            "true_delta": float(true_delta),
            "margin": float(margin),
            "alpha": float(alpha),
        },
        "empirical_coverage": {key: covered[key] / sims for key in sorted(covered)},
        "point_null_rejection_rate": point_rejections / sims,
        "degenerate_sims": int(degenerate_sims),
    }


# ---------------------------------------------------------------------------
# CLI (design Sections 6 and 12).
# ---------------------------------------------------------------------------


def _load_rows_for_cli(args: argparse.Namespace, spec: dict[str, Any]) -> tuple[
    list[dict[str, Any]], Any | None, dict[str, Any]
]:
    """Resolve per-item rows for the CLI: via the frozen agreement module when ratings
    are supplied, otherwise directly from a precomputed per-item rows JSONL."""
    inputs: dict[str, Any] = {
        "items": str(args.items) if args.items else None,
        "ratings": str(args.ratings) if args.ratings else None,
        "spec": str(args.spec),
        "rating_schema": None,
        "phase": args.phase,
        "agreement_module": None,
        "rows_builder": None,
    }
    module = None
    if args.items is None:
        raise ClusterBootstrapError("--items is required outside --coverage-check mode")
    if args.ratings is not None:
        if args.comparison is None:
            raise ClusterBootstrapError(
                "--comparison is required with --ratings (the two vendor pairs are "
                "analyzed separately and never pooled)"
            )
        module_path = args.agreement_module or DEFAULT_AGREEMENT_MODULE
        module = load_agreement_analysis(module_path)
        inputs["agreement_module"] = str(module_path)
        rating_schema = load_json(args.rating_schema)
        inputs["rating_schema"] = str(args.rating_schema)
        ratings = load_jsonl(args.ratings)
        tasks = load_jsonl(args.items)
        rows, builder = build_rows_from_ratings(
            module,
            ratings,
            tasks,
            spec,
            args.comparison,
            rating_schema=rating_schema,
            phase=args.phase,
        )
        inputs["rows_builder"] = builder
        return rows, module, inputs
    rows = load_jsonl(args.items)
    if args.agreement_module is not None:
        module = load_agreement_analysis(args.agreement_module)
        inputs["agreement_module"] = str(args.agreement_module)
    elif DEFAULT_AGREEMENT_MODULE.is_file():
        module = load_agreement_analysis(DEFAULT_AGREEMENT_MODULE)
        inputs["agreement_module"] = str(DEFAULT_AGREEMENT_MODULE)
    return rows, module, inputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ratings", type=Path, default=None,
                        help="rating-record JSONL; requires the agreement module")
    parser.add_argument("--items", type=Path, default=None,
                        help="item manifest JSONL (with --ratings) or precomputed "
                             "per-item rows JSONL (without --ratings)")
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC,
                        help="task family spec (weights and equivalence margin)")
    parser.add_argument("--rating-schema", type=Path, default=DEFAULT_RATING_SCHEMA,
                        help="rating record schema passed through to the agreement "
                             "module in --ratings mode")
    parser.add_argument("--comparison", choices=COMPARISONS, default=None)
    parser.add_argument("--contrast", default="d",
                        choices=CONTRASTS + tuple(ESTIMAND_TO_FIELD),
                        help="field vocabulary (d | as_minus_aa | shift) or estimand "
                             "vocabulary (SS_minus_AA | AS_minus_AA | route_shift)")
    parser.add_argument("--phase", default=None,
                        help="benchmark phase to analyze when the ratings cover more "
                             "than one phase")
    parser.add_argument("--b", type=int, default=DEFAULT_B)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--agreement-module", type=Path, default=None,
                        help="explicit path to agreement_analysis_v004.py "
                             "(defaults to the copy beside this file)")
    parser.add_argument("--coverage-check", action="store_true",
                        help="run the labeled not-frozen Monte Carlo coverage "
                             "diagnostic instead of data inference")
    parser.add_argument("--coverage-sims", type=int, default=200)
    parser.add_argument("--coverage-b", type=int, default=500)
    parser.add_argument("--coverage-clusters-per-family", type=int, default=8)
    parser.add_argument("--coverage-items-per-cluster", type=int, default=3)
    parser.add_argument("--coverage-cluster-sd", type=float, default=0.08)
    parser.add_argument("--coverage-item-sd", type=float, default=0.05)
    parser.add_argument("--coverage-true-delta", type=float, default=0.0)
    args = parser.parse_args(argv)

    try:
        spec = load_json(args.spec)
        if not isinstance(spec, dict):
            raise ClusterBootstrapError("the task family spec must be a JSON object")
        weights = spec.get("primary_target_mix")
        if not isinstance(weights, dict):
            raise ClusterBootstrapError("the spec has no primary_target_mix weights")
        margin = spec.get("equivalence_margin_absolute_agreement")
        if not isinstance(margin, (int, float)) or isinstance(margin, bool):
            raise ClusterBootstrapError(
                "the spec has no equivalence_margin_absolute_agreement"
            )
        if args.coverage_check:
            if args.ratings is not None or args.items is not None:
                raise ClusterBootstrapError(
                    "--coverage-check simulates its own data; do not pass "
                    "--ratings or --items"
                )
            result = coverage_check(
                sims=args.coverage_sims,
                b=args.coverage_b,
                seed=args.seed,
                weights=weights,
                clusters_per_family=args.coverage_clusters_per_family,
                items_per_cluster=args.coverage_items_per_cluster,
                cluster_sd=args.coverage_cluster_sd,
                item_sd=args.coverage_item_sd,
                true_delta=args.coverage_true_delta,
                margin=float(margin),
            )
        else:
            contrast = ESTIMAND_TO_FIELD.get(args.contrast, args.contrast)
            rows, module, inputs = _load_rows_for_cli(args, spec)
            statistic, source, note = select_statistic(
                module, weights, spec, contrast, rows
            )
            result = run_inference(
                rows,
                weights=weights,
                contrast=contrast,
                seed=args.seed,
                b=args.b,
                margin=float(margin),
                statistic=statistic,
                statistic_source=source,
                comparison=args.comparison,
            )
            result["statistic_note"] = note
            result["inputs"] = inputs
        if args.out is not None:
            atomic_write_json(args.out, result)
    except (OSError, ValueError, ClusterBootstrapError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
