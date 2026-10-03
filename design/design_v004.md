# Route-agreement benchmark, design v0.04

Status: authoritative design, frozen for the disposable pilots on August 4, 2026.
Supersedes `design_v001.md`, `design_v002.md`, and `design_v003.md`, and resolves the
estimand inconsistency between the v0.03 package and the companion manuscript in favor of the
manuscript. Confirmatory freeze and preregistration remain future steps that incorporate pilot
results into Sections 10-11.

## 1. Researcher decisions recorded in this version

All decisions below were made by the researcher on August 4, 2026 and recorded in
`project/DECISIONS.md`.

| # | Decision |
|---|---|
| D1 | The primary estimand is `SS - AA` (within-sub-agent agreement minus within-API agreement). The manuscript's definition controls; the v0.01-v0.03 `AS - AA` primary is superseded. |
| D2 | The practical-equivalence margin is approved at five percentage points of exact agreement. |
| D3 | The quality criterion is blind human adjudication of route-level disagreements (Section 7), plus gold-label accuracy for objective families. |
| D4 | Models are matched within vendor family at provider-default sampling: Claude Code sub-agents vs. the Anthropic API on `claude-opus-5`, and Codex sub-agents vs. the OpenAI API on `gpt-5.6-sol`. Neither model accepts temperature or other sampling overrides, so provider defaults are the only configuration. |
| D5 | The Clark County minimized materials are authorized for de-identification, task fidelity, and hosted transmission on both routes (`sources/clark_county_pilot_v001/authorization_v001.json`). The transmission gate becomes route-neutral and authorization-based. |
| D6 | The researcher will blind-adjudicate at most 300 disagreement items in the confirmatory run. This is a hard design constraint on the power simulation. |
| D7 | Stage budget ceilings are set in `budget_plan_v003.json` within the $1,000 envelope. |
| D8 | Invalid or missing codings remain in the primary agreement denominators; the validity/agreement decomposition of Section 5 is a required companion report. |
| D9 | The eight-family target mixture and weights are confirmed; `research_basis_v003.md` records the rationale. |

## 2. Routes and model configuration

The study compares two coding routes within each of two vendor families. A "coding" is one
model judgment of one item in a fresh context.

| Comparison | Sub-agent route | API route |
|---|---|---|
| Anthropic pair | One fresh Claude Code sub-agent per coding, requested model `opus` (Claude Opus 5) | One stateless Anthropic Messages API call, model `claude-opus-5` |
| OpenAI pair | One fresh Codex sub-agent per coding, model GPT-5.6 Sol | One stateless OpenAI Responses API call, model `gpt-5.6-sol` |

Rules:

- The two comparisons are analyzed separately and never pooled. Each has its own
  `Delta_overall`, tested at alpha 0.05; the study reports both.
- Sampling configuration is provider defaults on both routes. Claude Opus 5 and GPT-5.6 Sol
  reject explicit temperature/top-p overrides, so no sampling parameter is sent anywhere.
  Reasoning configuration is likewise the provider default (no effort or reasoning override).
- Every attempt records `requested_model`; `reported_model` is recorded where the transport
  returns it (API responses) and recorded as self-reported-and-unverified for sub-agents.
- Both routes receive byte-identical `prompt_text` from `prompt_envelopes_v003.jsonl`,
  obtained only through `build_prompt_envelopes_v003.route_visible_payload` (or with the
  stored `prompt_sha256` re-verified at dispatch). Wrappers add transport mechanics only.

## 3. Primary estimand

For item `i`, collect `R_A >= 2` independent API codings and `R_S >= 2` independent sub-agent
codings within one vendor pair. `g(x, y) = 1` only when both codings are valid terminal
successes whose substantive labels match; otherwise 0. Invalid, refused, malformed, and
missing codings stay in the denominator.

```text
AA_i = mean g over all C(R_A, 2) distinct API/API pairs
AS_i = mean g over all R_A * R_S API/sub-agent pairs
SS_i = mean g over all C(R_S, 2) distinct sub-agent/sub-agent pairs
d_i  = SS_i - AA_i
```

`D_f` is the mean of `d_i` over family `f` items; with the frozen weights `w_f` of
`task_family_spec_v004.json`:

```text
Delta_overall = sum_f w_f D_f
```

A negative `Delta_overall` means the sub-agent route is less internally reproducible than the
API route on the target workload.

## 4. Secondary agreement outcomes

1. `AS - AA`, overall and by family (the v0.03 primary, retained as the leading secondary).
2. The route-shift term `S_i = AA_i + SS_i - 2 * AS_i`, aggregated like `d_i`. In expectation
   `S_i` equals the squared Euclidean distance between the two routes' conditional label
   distributions for item `i`, so it is nonnegative and zero only when the routes draw labels
   from the same distribution. Equal reproducibility with a large shift term means the routes
   are equally consistent but systematically different, which defeats substitutability; the
   shift term is therefore reported alongside the primary in every summary.
3. Route-specific label distributions; total-variation and Jensen-Shannon distances on the
   intensive panel; Gwet's AC1 and nominal Krippendorff's alpha with cluster-resampled
   intervals; evidence-set exact agreement and Jaccard similarity.

## 5. Validity decomposition

Because failures count as disagreement, the primary confounds label reproducibility with
operational reliability. Every analysis therefore also reports, per route and per family:
`P(valid)` for single codings, `P(both valid)` for pairs, and `P(agree | both valid)`. The
primary decision uses the all-attempts definition of Section 3; the decomposition attributes
any deficit to failures versus label disagreement.

## 6. Nulls, decision rules, and inference

- Point null: `H0: Delta_overall = 0`, two-sided, alpha 0.05, per vendor pair.
- Equivalence: TOST at alpha 0.05 with the approved margin `delta = 0.05`; equivalently the
  90% confidence interval must lie wholly inside `[-0.05, +0.05]`. A nonsignificant point
  test is never evidence of equivalence.
- Interpretation map (prespecified): reject point null with `Delta < 0` = sub-agent route
  less reproducible; reject with `Delta > 0` = more reproducible; TOST rejects = practically
  equivalent; neither rejects = inconclusive. TOST and the point test can both reject
  (a small but nonzero difference); report both outcomes verbatim.
- Inference resamples whole dependence clusters (`dependence_cluster_id`) stratified by task
  family; coder pairs are never resampled. Implementation: `cluster_bootstrap_v001.py` with
  seeded draws, B = 5,000, bias-corrected and accelerated (BCa) intervals as primary (with
  acceleration estimated by a cluster-level jackknife) and percentile intervals as a
  sensitivity; the point-null p-value uses the centered (translation) bootstrap. Coverage
  must be simulation-checked before the confirmatory freeze and the implementation frozen
  before unblinding.

## 7. Quality criterion (blind disagreement adjudication)

- Define each route's item-level label as the modal label among its valid codings; ties keep
  all tied labels. An item is a "route disagreement" when the modal label sets differ or
  either route has no valid coding.
- All route-disagreement items (capped by design at 300; the power simulation enforces the
  cap in expectation) go to blind human adjudication. Presentation is normalized: label
  candidates and their supporting evidence are re-rendered in a uniform template with route
  identity, coding counts, formatting, and rationale style stripped; order randomized;
  provenance key sealed until all decisions are final.
- The adjudicator selects the correct label or `indeterminate` under the frozen codebook.
- Quality estimands: per-route accuracy against adjudicated labels among disagreement-item
  codings, and per-route accuracy against audited gold labels for objective families.
  The claim "quality at least as good" requires the sub-agent route's accuracy deficit to be
  bounded by a margin frozen at confirmatory registration (candidate: five percentage
  points, mirroring the agreement margin).
- Gold labels are audited by an independent second reader before the confirmatory run;
  membership-only validation is not sufficient for accuracy claims.

## 8. Task population

Eight families with confirmed weights 0.15/0.15/0.15/0.10/0.10/0.10/0.10/0.15
(`task_family_spec_v004.json`, identical weights to v0.03). Confirmatory sources follow the
per-family strategy of v0.03 Section "family table": post-cutoff or newly authored materials,
executable rule templates, and newly written subjective scenarios; public benchmarks and
famous hypotheticals serve only as contamination sentinels. The 27 development items
(`development_items_v003.jsonl`) and all pilot items are permanently excluded from the
confirmatory pool.

## 9. Privacy and the route-neutral transmission gate

- The gate is route-neutral and authorization-based. A payload whose provenance carries
  `external_api_eligible: false` may be dispatched on ANY hosted route only if an
  authorization record covers its `source_id` and that route.
- Authorization records live beside the source
  (`sources/<source>/authorization_v001.json`) and carry: source id, covered material ids,
  covered routes, scope (de-identification, task fidelity, transmission), authorizer,
  decision date, and decision provenance.
- The Clark County pilot materials are authorized for both routes as of August 4, 2026
  (researcher decision D5). `minimized_materials_v002.jsonl` records the approved review
  statuses and supersedes v001, which remains immutable. `validate_clark_materials.py`
  validates v001 under the original pending-only rules and v002 under approved-plus-
  authorization rules; the hash chain to the OCR receipt is unchanged.
- Raw documents, OCR, and previews remain local and gitignored. The minimization discipline
  (pseudonymized passages, no direct identifiers, source-page hashes) is unchanged.

## 10. Scheduling, blinding, retries, and stopping

- The launcher (`run_route_comparison.py`) generates a seeded schedule that randomizes item
  and route order within short temporal blocks, interleaving both routes of a vendor pair in
  every block; the schedule is hash-sealed before any call.
- Retry policy: each planned draw slot allows at most 2 attempts (one retry) for transport
  or parse failure, as linked attempts; the terminal attempt fills the slot; a slot whose
  terminal attempt is not a valid success stays in the denominator as a failure.
- During generation, status output is operational only (counts, terminal statuses, latency,
  tokens, projected cost); it never shows labels, agreement, or distributions, and validator
  error strings are sanitized so label values do not leak.
- Stop rules, enforced by the launcher: (a) requested model unavailable or reported model
  drifts from the frozen identifier - stop at the next checkpoint; (b) projected cumulative
  spend exceeds the approved stage ceiling - refuse to start the next block, and abort
  mid-block if realized spend crosses the ceiling; (c) systematic failure - more than 25% of
  a block's attempts terminally failing stops the run for diagnosis; (d) no items or repeats
  are added after any route contrast has been observed.
- Unblinding occurs only after all scheduled attempts are terminal, integrity checks pass,
  and counts reconcile.

## 11. Power and staging

Power is chosen by joint simulation (`power_simulation_v001.py`) over
`J in {80, 160, 320, 640}` unique items and balanced repeats `R in {2, 4, 8, 16}` per route,
simulating family prevalence, route-specific shifts, failure rates, item heterogeneity,
cluster and twin dependence, and temporal blocks, evaluated for the point null and TOST via
the frozen cluster bootstrap. Two hard constraints: expected route disagreements routed to
human review must not exceed 300 (decision D6), and the item count must not exceed what the
researcher can author and audit. Parameters are pilot-based with a preregistered conservative
scenario. The v0.02 analytic check (`overall_power.py`) is retained for reference only; its
800-1,300-item scenarios were derived for `AS - AA` and do not transfer.

Stages (ceilings in `budget_plan_v003.json`):

1. Offline validation - no model calls.
2. Instrument pilot: 27 development items x 2 codings per route per vendor pair. The
   Anthropic pair runs first from Claude Code; the OpenAI pair requires Codex and runs when
   the researcher invokes the Codex adapter. A 3-call OpenAI adapter smoke test on
   disposable items is part of this stage.
3. Intensive pilot: ~32 disposable items x 8-12 codings per route.
4. Diversity pilot: ~160 disposable items x >= 2 codings per route.
5. Confirmatory run: allocation frozen by simulation, preregistered, then executed.

## 12. Implementation map (v0.04 components)

| Component | File |
|---|---|
| Machine-readable spec (weights, estimand, margins, models) | `task_family_spec_v004.json` |
| Rating record contract (general R, provenance, hashes) | `rating_record_schema_v002.json` |
| Repeated-coder agreement analysis (SS-AA primary) | `agreement_analysis_v004.py` |
| Stratified whole-cluster bootstrap + TOST | `cluster_bootstrap_v001.py` |
| Joint item/repeat power simulation | `power_simulation_v001.py` |
| Route launcher, scheduler, gates, budget enforcement | `run_route_comparison.py` |
| Budget stages and ceilings | `budget_plan_v003.json` |
| Clark authorization record | `sources/clark_county_pilot_v001/authorization_v001.json` |
| Approved Clark materials | `sources/clark_county_pilot_v001/minimized_materials_v002.jsonl` |

Items, schema, codebook, and envelopes are unchanged from v0.03
(`development_items_v003.jsonl`, `task_schema_v003.json`, `codebook_v003.md`,
`prompt_envelopes_v003.jsonl`); item-level `external_api_eligible` flags record build-time
state, and the authorization record of Section 9 governs dispatch.

## Amendment A1 (August 5, 2026): instrument-pilot acceptance and intensive-pilot configuration

Researcher decisions recorded August 5, 2026 (`project/DECISIONS.md`, DEC-...-002):

1. **Instrument-pilot gate: accepted for both vendor pairs.** Anthropic pair (August 4) and
   OpenAI pair (August 5, executed from Codex): 108/108 terminal valid codings each, no drift,
   no stop-files, ceilings enforced; runs `instrument_pilot_anthropic_v001` and
   `instrument_pilot_openai_v001`.
2. **Intensive-pilot panel: 33 disposable items** - the 27 development items plus six newly
   authored interpretive-application items (three familiar/novel twin pairs covering the
   drawing-blood, sleeping-in-the-station, and murdering-heir structures), built
   deterministically into `intensive_panel_items_v001.jsonl` with envelopes in
   `intensive_prompt_envelopes_v001.jsonl`. The new items require researcher audit via
   `intensive_panel_review_v001.md`; pasting the intensive handoff prompt constitutes
   approval of the pinned panel version. Panel items remain development-phase and are
   permanently excluded from the confirmatory pool.
3. **Repeats: R_A = R_S = 12** per item per route for both vendor pairs.
4. **Matched reasoning effort (refines decision D4).** Both arms of both pairs run at
   explicit HIGH reasoning effort: the sub-agent arms execute from dedicated orchestrating
   sessions on the study model at High effort (Claude Code on Claude Opus 5 High; Codex on
   GPT-5.6 Sol High), so workers inherit it; the API arms set the vendor's explicit
   reasoning-effort parameter to high (`output_config.effort` for Anthropic;
   `reasoning.effort` for OpenAI). Sampling parameters remain untunable provider defaults on
   these models; the "provider defaults" descriptor in `task_family_spec_v004.json`
   `vendor_pairs.*.sampling` is superseded for reasoning effort by this amendment. The
   launcher records `reasoning_effort` in the sealed run configuration and in every rating
   record.
5. **Configuration disclosure for the record.** The instrument-pilot runs predate this
   amendment: their API arms used vendor defaults with no explicit effort parameter (for
   Claude Opus 5 the default effort is high; for GPT-5.6 Sol the Responses default applied),
   and their sub-agent workers inherited the orchestrating session's effort (the Anthropic
   pair was orchestrated from a Claude Fable 5 session with workers pinned to requested
   model `opus`; the OpenAI pair from the researcher's Codex session). Reasoning effort was
   therefore recorded but not matched at the instrument stage. Stage-to-stage comparisons
   must treat effort configuration as changed between the instrument and intensive stages.

