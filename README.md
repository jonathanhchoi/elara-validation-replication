# ELARA validation study replication materials

This repository accompanies Jonathan H. Choi's *ELARA: A Framework for Empirical Legal Research with AI Agents*. It contains the materials and archived codings for the comparison of direct API calls and scaffolded sub-agents reported in Appendix C and Table 4.

The study used 255 objective items from seven task families and 45 subjective interpretation items. Each objective item was coded twice per route and vendor; each subjective item was coded twelve times. The four runs contain 4,200 terminal coding records. Failed codings remain in the agreement denominators.

The reusable ELARA software is in [jonathanhchoi/ELARA](https://github.com/jonathanhchoi/ELARA). This repository preserves the study materials separately from the evolving software.

## Reproduce the results

Use Python 3.12 and the package versions verified for this release:

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python reproduce.py
```

The reproduction runs entirely offline after installing the two dependencies. It does not call a model or incur API charges. It takes the archived terminal codings, repeats the original free-text normalization, computes agreement, and reruns the stratified whole-cluster bootstrap with 2,000 resamples and the original seeds. It checks every field of the resulting agreement and inference reports against the saved analyses, allowing only a floating-point tolerance of 1e-12.

The specified environment matters. A Python 3.11/NumPy 1.26.4 check reproduced all agreement estimates but gave slightly different bias-corrected interval endpoints for the OpenAI objective panel. Python 3.12/NumPy 2.3.5 reproduces the archived reports within the stated tolerance. The original analysis modules are preserved unchanged.

Results are written to `build/`:

- `table4.csv` and `table4.json` contain the agreement estimates, confidence intervals, p-values, and coding counts.
- `api_costs.json` reconstructs API costs from archived attempt usage and the study's historical prices.
- `verification.json` records whether the reproduction succeeded, the file and coding counts, and the software versions used.
- Each run's subdirectory contains the reproduced analysis and, for objective items, the normalized inputs.

The checked output from preparing this release is in [`results/`](results/). The manuscript's subscription-cost comparison also depends on external estimates of plan capacity, full subscription utilization, and a character-based usage proxy. The offline command reconstructs recorded API costs; it does not reconstruct the character-consumption totals or establish the subscription-capacity assumptions.

## What is included

| Location | Contents |
|---|---|
| `data/` | All 300 item definitions, supplied materials, questions, visible prompts, and item-specific response schemas |
| `runs/` | The 4,200 terminal coding records, original schedules and configurations, saved analyses, and execution archives |
| `code/` | Original agreement, bootstrap, and free-text normalization code used by the offline reproduction |
| `code/historical/` | Archived generation and orchestration scripts for methodological inspection |
| `design/` | Design, codebook, schemas, task mixture, budget assumptions, expansion proposal, and runbooks |
| `preregistration/` | The local August 5, 2026 preregistration document, in PDF and editable LaTeX |
| `provenance/` | Original and publication-file hashes, prompt hashes, export inventory, and Clark County authorization record |

Each `execution_archive.zip` preserves the recorded API attempts, terminal records, sub-agent assignments and returns, manifests, approvals, and amendments from that run. `archive_manifest.json` gives the original and publication SHA-256 hash of every member. Files can be extracted with an ordinary ZIP utility. Historical absolute paths inside the archived records describe the original workspace; the reproduction command does not use those paths.

The registration link supplied with the manuscript is [OSF kqxw4](https://osf.io/kqxw4/). The document included here is the local dated copy; this release does not independently certify its byte identity with an OSF deposit.

## Study history and interpretation

The reported runs retain their historical names `diversity_objective_*_v001` and `diversity_interpretive_*_v001`, and the original `diversity_pilot` and `development_only` fields. The panels contain retained development items as well as later additions. These fields have not been relabeled as a new confirmatory sample. The broader preregistration and design describe additional phases and human adjudication; this archive establishes the four reported runs, not completion of every proposed phase.

Some frozen instrument fields still say `pending_researcher`. Read the separately recorded authorizations, run approvals, and amendments alongside those historical fields. In particular, the Clark County authorization covers minimized development materials and hosted processing; it does not authorize publication of the original court-document scans.

The objective panel contains 153 items tagged `public_exact`, 94 tagged `synthetic_original`, and eight based on minimized Clark County materials. The source texts include court opinions, court filings, and contract excerpts; provenance remains in each item. Subjective items include familiar scenarios, newly worded structural twins, and original scenarios. Agreement is a measure of consistency, not a direct measure of correctness.

Archived model names, reported-model verification flags, errors, failed outputs, and retry records are retained. Model services and agent products change, so calling them again is a new experiment and is not expected to recreate these labels exactly. The historical generation scripts are documentation of the original environment, not the portable entry point for reproducing the paper's results.

## Publication redactions and source files

The public copy removes one incidental personal phone number and one residential street address. The address occurred in two tasks, so these changes affect `div_extract_018`, `div_extract_019`, and `div_extract_039`. The same replacements are applied wherever those details occur in the exported inputs and execution archives. No target answer, coding label, numerical result, inclusion decision, or denominator was changed.

The original run hashes remain recorded. For the affected prompts, the historical `prompt_sha256` and other original envelope hashes identify what the models actually received, not the redacted publication text. [`provenance/prompt_hashes.json`](provenance/prompt_hashes.json) supplies separate hashes for the published prompt text. The export and archive manifests identify every changed file. The unredacted originals remain in the author's research archive.

Raw Clark County PDFs, complete OCR files, unrelated project files, credentials, caches, and terminal logs are not part of this release. The minimized passages actually supplied to the coders are included. See [data rights and source notes](DATA_RIGHTS.md).

## Citation and contact

Choi, Jonathan H. (2026). *ELARA: A Framework for Empirical Legal Research with AI Agents*. Validation-study replication materials, initial release, October 3, 2026. https://github.com/jonathanhchoi/elara-validation-replication

Correspondence: jonathanc@wustl.edu.
