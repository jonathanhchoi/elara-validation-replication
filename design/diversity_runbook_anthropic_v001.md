# Diversity-pilot runbook: Anthropic pair (v0.01)

This runbook governs the Anthropic-pair diversity pilot: **two sealed runs** on the r3
panels under instrument v0.4 (design: `benchmarks/intercoder-route-agreement/design_v004.md`
Sections 2, 9, 10, Amendment A1; proposal: `diversity_expansion_proposal_v001.md`, revisions
r2-r3; budget: `budget_plan_v004.json`, stage `diversity_pilot`). The researcher's handoff
prompt naming this runbook and its SHA-256 constitutes approval of exactly this version,
of `budget_plan_v004.json`, and of the pinned panels and instrument files below.

**Completion condition.** Both run directories show all scheduled slots terminal with zero
invalid and zero pending across both arms; each run's `rating_records.jsonl` is merged with
a reconciled `merge_receipt.json`; `analysis_v001.json` exists for each run (objective run
analyzed via `analyze_normalized_v001.py`, interpretive run via the launcher's gated
`analyze`); and a full disclosure report is delivered.

Use `/elr-code-observations` and follow `workflow/shared/observation-fanout.md` throughout.
This is the kit's route-agreement validation benchmark, an explicit kit validation run: do
not route it through `project/PROJECT_STATE.md`, and change nothing outside the two run
directories.

## The two runs

| | Run A (objective) | Run B (interpretive) |
|---|---|---|
| Run directory | `runs/diversity_objective_anthropic_v001` | `runs/diversity_interpretive_anthropic_v001` |
| Items file | `diversity_objective_panel_v003.jsonl` | `diversity_interpretive_panel_v001.jsonl` |
| Envelopes file | `diversity_objective_envelopes_v003.jsonl` | `diversity_interpretive_envelopes_v002.jsonl` |
| Items | 255 (42 free-text) | 45 (all closed) |
| Repeats per route | 2 | 12 |
| Slots (both arms) | 1,020 | 1,080 |
| Blocks | 10 | 8 |
| Seed | 20260812 | 20260813 |
| Analysis tool | `analyze_normalized_v001.py` | launcher `analyze` |

Run A runs to terminal completion (all blocks, both arms) before Run B's prepare. **No
analysis, merge, or label inspection happens until both runs' generation is complete**;
this keeps the no-additions stop rule and blinding intact across the stage.

## Preconditions

Verify each before any model call; stop and report if any fails:

1. The working directory is the kit root (the folder containing `scripts/` and
   `benchmarks/`).
2. This session runs on Claude Opus 5 at High effort; report the session model you believe
   you are. If it is not Opus 5, stop. Sub-agents inherit this session's configuration, and
   every workflow invocation below also pins model `opus` and effort `high` explicitly.
3. `PILOT_ANTHROPIC_API_KEY` is set in the environment. Never print it, write it to a file,
   or pass it on a command line.
4. No other session is operating on either run directory, and Claude Code supports saved
   dynamic workflows (v2.1.154+).
5. The approved artifacts are intact (SHA-256; on any mismatch, stop):
   - `diversity_objective_panel_v003.jsonl` = `9c6a55e864a76fbc821a042b714666bb4282393f28a0a236c1498c9120b7ae86`
   - `diversity_interpretive_panel_v001.jsonl` = `26d0ddca53758c8eabf9b7992e49c47f2ac185a794399d405fe508e6e954570d`
   - `diversity_objective_envelopes_v003.jsonl` = `262ce4433f2a6c7d32c10b9b91a4252724cc0600bb725c031af4707210fe7c5b`
   - `diversity_interpretive_envelopes_v002.jsonl` = `b8b54b28d4a791141db0c4c60e9e8093c22d90168a836bbda9e467bb9cf2f3ec`
   - `budget_plan_v004.json` = `9962c20d73ac2b27f0340ce2f7e83e5bedddf99c8c8bc0f981d81e14a15d0988`
   - `answer_equivalence_v001.py` = `d3e60cae51b498d05b55b878f013f4629c36d02f473bc68db01e7e25644a4567`
   - `normalize_ratings_v001.py` = `175690050435eca900bd4b8acad9f81f4fa7c2d0403a37f2d38d876cdbfa660f`
   - `analyze_normalized_v001.py` = `be1acaa5a9bb47e898fbb7705138e93f23aa887b05740c63c722987b458a8896`
6. `python scripts/doctor.py` passes.

## Run A: prepare the sealed objective run

One time; if the run directory already exists from an interrupted session, skip prepare and
resume at the first incomplete block:

```text
python benchmarks/intercoder-route-agreement/run_route_comparison.py prepare --run-dir benchmarks/intercoder-route-agreement/runs/diversity_objective_anthropic_v001 --comparison anthropic --stage diversity_pilot --envelopes benchmarks/intercoder-route-agreement/diversity_objective_envelopes_v003.jsonl --items benchmarks/intercoder-route-agreement/diversity_objective_panel_v003.jsonl --reps-api 2 --reps-subagent 2 --blocks 10 --seed 20260812 --effort high --budget-plan benchmarks/intercoder-route-agreement/budget_plan_v004.json --approve-stage-ceiling
```

Confirm the prepare summary shows 1,020 slots, 10 blocks, stage `diversity_pilot`, and
reasoning effort `high`.

## Run A: the ten temporal blocks

Strictly in order, 0 through 9. For each block N, complete BOTH arms within the same
working window before opening block N+1:

**(a) API arm.**

```text
python benchmarks/intercoder-route-agreement/run_route_comparison.py run-api --run-dir benchmarks/intercoder-route-agreement/runs/diversity_objective_anthropic_v001 --block N
```

The launcher sets `output_config` effort `high`, enforces the $120 stage ceiling before and
during every block, archives every raw response, retries transport and parse failures once
as linked attempts, and stops on model drift or a failing block. If it writes any stopfile,
stop everything and report.

**(b) Sub-agent arm.** Invoke the saved workflow `/elr-observation-fanout` (from
`.claude/workflows/elr-observation-fanout.js`) with exactly these arguments, substituting N
and the absolute run-directory path:

```json
{"run_dir": "<absolute path>\\subagent_run", "block": N, "model": "opus", "effort": "high", "fixture": true}
```

It runs one discovery agent, one fresh sub-agent per pending block-N assignment (about 51;
the workflow bounds concurrency itself), and one operational verifier. Workers read only
their own assignment, verify its frozen hashes, write only their allowed return path with
the full return contract and honest provenance, and never report labels. Free-text
assignments are answered with the value itself (or UNCERTAIN) per the envelope's response
schema; workers copy values as the document states them and never normalize.

**(c) Validate serially.**

```text
python scripts/unit_fanout.py status --run-dir benchmarks/intercoder-route-agreement/runs/diversity_objective_anthropic_v001/subagent_run --include-pending
```

The block is complete when all of its assignments are terminal with zero invalid. If a
return is invalid, or is a terminal failure on attempt 1, run
`python scripts/unit_fanout.py retry --run-dir <subagent_run> --assignment-id <id>` and
re-invoke the workflow for that block (it dispatches only pending assignments). Never
hand-edit or overwrite a return; never retry past attempt 2; a still-failing slot stays in
the denominator as a typed failure.

## Run B: prepare and run the sealed interpretive run

Only after Run A shows 1,020 expected, 1,020 terminal, 0 invalid, 0 pending across both
arms. Same discipline, 8 blocks (0 through 7), about 68 sub-agent assignments per block:

```text
python benchmarks/intercoder-route-agreement/run_route_comparison.py prepare --run-dir benchmarks/intercoder-route-agreement/runs/diversity_interpretive_anthropic_v001 --comparison anthropic --stage diversity_pilot --envelopes benchmarks/intercoder-route-agreement/diversity_interpretive_envelopes_v002.jsonl --items benchmarks/intercoder-route-agreement/diversity_interpretive_panel_v001.jsonl --reps-api 12 --reps-subagent 12 --blocks 8 --seed 20260813 --effort high --budget-plan benchmarks/intercoder-route-agreement/budget_plan_v004.json --approve-stage-ceiling
```

Confirm 1,080 slots, 8 blocks; then run blocks 0-7 exactly as in Run A ((a) `run-api
--block N`, (b) `/elr-observation-fanout` on this run's `subagent_run` with the same pinned
model and effort, (c) serial validation), substituting the Run B directory throughout.

## Blinding

Until the closing sequence below, report only operational information: counts, terminal
statuses, latency, cost, retries. Never state, summarize, or tally any label, answer,
agreement rate, or distribution, and do not allow receipts to include them.

## Closing sequence

Only when BOTH runs show all slots terminal (1,020 and 1,080), 0 invalid, 0 pending across
both arms, run serially:

```text
python benchmarks/intercoder-route-agreement/run_route_comparison.py collect-subagent --run-dir benchmarks/intercoder-route-agreement/runs/diversity_objective_anthropic_v001
python benchmarks/intercoder-route-agreement/run_route_comparison.py merge --run-dir benchmarks/intercoder-route-agreement/runs/diversity_objective_anthropic_v001 --output benchmarks/intercoder-route-agreement/runs/diversity_objective_anthropic_v001/rating_records.jsonl
python benchmarks/intercoder-route-agreement/run_route_comparison.py collect-subagent --run-dir benchmarks/intercoder-route-agreement/runs/diversity_interpretive_anthropic_v001
python benchmarks/intercoder-route-agreement/run_route_comparison.py merge --run-dir benchmarks/intercoder-route-agreement/runs/diversity_interpretive_anthropic_v001 --output benchmarks/intercoder-route-agreement/runs/diversity_interpretive_anthropic_v001/rating_records.jsonl
python benchmarks/intercoder-route-agreement/analyze_normalized_v001.py --run-dir benchmarks/intercoder-route-agreement/runs/diversity_objective_anthropic_v001 --out benchmarks/intercoder-route-agreement/runs/diversity_objective_anthropic_v001/analysis_v001.json
python benchmarks/intercoder-route-agreement/run_route_comparison.py analyze --run-dir benchmarks/intercoder-route-agreement/runs/diversity_interpretive_anthropic_v001 --out benchmarks/intercoder-route-agreement/runs/diversity_interpretive_anthropic_v001/analysis_v001.json
```

The objective run's analysis MUST use `analyze_normalized_v001.py` (it canonicalizes the 42
free-text items' answers with the frozen equivalence module, then runs the same frozen
v0.04 analysis); using the launcher's `analyze` on the objective run would mis-score
free-text responses and is prohibited. The interpretive run has no free-text items and uses
the launcher's gated `analyze` unchanged.

## Report

Take your time, and err on the side of being too careful. After finishing, conduct a round
of review to confirm you followed this runbook exactly, then report: the final status
output for both runs; both merge reconciliation counts; realized cost against the $120
stage ceiling; retries on each arm of each run; both analysis file paths; the
`normalization` block hashes from the objective analysis; and a complete, accurate list of
every file you created or modified. Disclose everything; make no changes you have not
disclosed.
