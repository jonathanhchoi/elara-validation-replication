# Diversity-pilot expansion proposal v0.01: 33 → 300 items

> **Status: drafted by the model on 2026-08-11. Everything below is a proposal awaiting the
> researcher's item-level audit and stage approval. No model call has been made, no frozen
> artifact has been modified, and the confirmatory pool is untouched.**

## 1. What is proposed

Expand the route-agreement benchmark from the 33-item intensive panel to a **300-item
benchmark** by authoring **267 new development-phase items**, and code the full panel in the
staged "diversity pilot" that `design_v004.md` Section 11 and `budget_plan_v003.json` already
contemplate (the frozen staging lists "~160 disposable items × ≥2 codings per route"; this
proposal enlarges that stage and asks the researcher to approve the deviation).

The new items are **already drafted, built, and mechanically validated** — the deliverable of
this proposal is the panel itself, for the researcher to audit and approve:

| Artifact | Contents |
|---|---|
| `diversity_items_source_{presence,move}_v002.json` + `..._{extract,contract,role,relation,statute,interp}_v001.json` | The 267 new items, one source file per family (presence/move rebuilt on real opinions in r2), with authoring notes and difficulty tags |
| `diversity_objective_panel_v003.jsonl` | 255 rows: 21 objective intensive-panel rows byte-preserved + 234 new objective items |
| `diversity_interpretive_panel_v001.jsonl` | 45 rows: 12 interpretive intensive-panel rows byte-preserved + 33 new interpretive items |
| `diversity_panel_review_v003.md` | The researcher audit sheet rendering all 267 new items with per-item checklists |
| `build_diversity_panels_v003.py` | Deterministic builder; `--check` verifies panels and sheet are current |
| `build_diversity_envelopes_v002.py` | Envelope builder reusing the frozen v0.03 functions; asserts the 33 carried-over envelopes are byte-identical to `intensive_prompt_envelopes_v001.jsonl` |
| `diversity_objective_envelopes_v003.jsonl`, `diversity_interpretive_envelopes_v002.jsonl` | 255 + 45 frozen v0.4 prompt envelopes (codebook_v004; 42 free-text) |
| `budget_plan_v004.json` | Draft budget stages for the two diversity runs (pending approval) |

Pinned versions (editing any item invalidates these and requires a rebuild + re-audit):

- Objective panel SHA-256 (v003): `9c6a55e864a76fbc821a042b714666bb4282393f28a0a236c1498c9120b7ae86`
- Interpretive panel SHA-256 (v001): `26d0ddca53758c8eabf9b7992e49c47f2ac185a794399d405fe508e6e954570d`

Both panels pass `validate_tasks.py` against `task_schema_v003.json` and
`task_family_spec_v004.json` (255 items / 0 twin sets; 45 items / 21 twin sets).

### Revision r2 (2026-08-11): opinion-passage families rebuilt on real opinions

Per the researcher's direction (revision round of 2026-08-11: real documents, not
synthetic; CourtListener for opinions), the two opinion-passage families were rebuilt from
scratch on **verbatim excerpts of real published opinions filed on or after 2026-06-01**,
so every source document postdates the study models' training cutoffs (the same
low-contamination rationale as the Clark County materials):

- **Acquisition route.** The anonymous CourtListener v4 search API supplied metadata for 20
  construct-targeted queries; the original opinion PDFs (106 distinct opinions, June 1 -
  August 11, 2026, state and federal appellate plus select trial courts) were downloaded
  from CourtListener's public document store with the issuing court's site as fallback — no
  authenticated endpoint, no credentials. Everything is archived kit-style under
  `sources/courtlistener_opinions_2026-08/` (raw PDFs, extracted text, SHA-256 manifest,
  retrieval receipt); `fetch_cl_opinions_v001.py` and `segment_cl_paragraphs_v001.py`
  reproduce the acquisition, and `build_cl_selections_v001.py` pins every curated selection
  (paragraph hash, excerpt offsets, gold, rationale) and regenerates the v002 source files.
- **Item shape.** 84 items (42 presence, 42 move) drawn from 54 distinct opinions, at most
  two items per opinion, with items from the same opinion sharing a dependence cluster.
  Excerpts are contiguous verbatim substrings trimmed only at sentence boundaries (median
  1,117 characters; three move items run 352-399 characters, flagged for the audit);
  `source_tier: public_exact`, provenance carries case name, court, filing date, and
  CourtListener URL. Extraction normalizes only whitespace, line-break hyphenation, and
  page furniture, as recorded in the manifest.
- **Realized compositions (documented deviations from the v001 targets).** Presence: 20
  PRESENT / 20 ABSENT / 2 UNCERTAIN across the same 14 judicial acts, with real hard
  negatives (a reported lower-court distinction, a post-Loper Bright no-deference
  statement, 'overruled' as an evidentiary ruling, 'waiver' and 'severable' in contractual
  and tax senses) and real edge positives (lenity and harmlessness as alternative holdings,
  unnamed avoidance, a severance-unavailable-for-want-of-formation analysis). Move: 8
  TEXT / 6 PURPOSE / 6 LEGISLATIVE HISTORY / 8 REPORTING / 10 NO_CODED / 4 UNCERTAIN, where
  the UNCERTAIN items are method-recitation passages fusing multiple coded moves and the
  NO_CODED pool includes real hard cases (precedent-only analysis, a concurrence's
  interpretive philosophy, regulatory background).
- **What stayed synthetic, and why.** Statutory entailment and interpretive application
  keep their authored v001 items because the frozen family spec prescribes synthetic
  construction there (executable rule templates; newly written scenarios, with famous
  structures only as sentinels). Extraction, contract, role, and relation keep their v001
  synthetic materials for this stage; re-sourcing them from real post-cutoff documents
  (RECAP filings via the same CourtListener store, EDGAR exhibits for clauses, or
  additional Clark County packets via the researcher's portal session) is available as a
  further revision on the researcher's decision, and is recommended for the confirmatory
  pool in any case.
- The six carried-over intensive presence/move items remain byte-preserved synthetic
  fixtures (frozen instrument); the audit sheet notes the mixed provenance.

### Revision r3 (2026-08-11): free-text extraction and harder chunk materials

Per the researcher's further direction (no forced multiple choice; equivalence checked on
the back end; chunks mixing relevant and irrelevant information), revision r3 changes the
instrument and three families:

- **Instrument v0.4.** `task_schema_v004.json` adds a `free_text` response mode with
  `answer_key` golds; `codebook_v004.md` revises the extraction, contract, and entailment
  sections; `build_prompt_envelopes_v004.py` emits envelopes whose free-text items carry a
  bounded string response schema instead of an enumerated label set (prompt contract
  `intercoder-visible-prompt-v002`); `answer_equivalence_v001.py` (22 self-tests) supplies
  frozen normalizers (money, number, percent, date, duration, string, citation); and
  `normalize_ratings_v001.py` canonicalizes free-text ratings and derives a realized label
  space so the frozen `agreement_analysis_v004.py` and bootstrap run unchanged. Agreement
  keeps its form: g(x, y) = 1 only when both codings are valid and their (canonical) labels
  match. Because the embedded codebook changed, diversity-run envelopes are no longer
  byte-identical to the intensive envelopes; the drift bridge compares labels, not bytes,
  and the carried families' codebook sections are unchanged (with a legacy note covering
  the three carried closed-candidate extraction items).
- **Extraction (42, rebuilt).** Free-text questions over verbatim multi-paragraph chunks of
  real post-cutoff documents: 35 RECAP federal filings (fee and default-judgment motions,
  settlement papers, orders, complaints, bankruptcy schedules) fetched anonymously with
  receipts (`sources/recap_filings_2026-08/`), plus three archived-opinion chunks. Chunks
  average ~1,700 characters and deliberately contain multiple values of the target's kind
  (near-twin figures such as a $76,155.27 award beside $76,118.32 in costs; twin
  $2,500,000 appropriations for different purposes; scheduled-versus-concluded dates).
  Golds: 39 as-stated values (22 money, 14 date, 1 percent, 1 duration, 1 string) verified
  verbatim-in-chunk at build, plus 3 UNCERTAIN absent-value items.
- **Contracts (27, rebuilt).** Multi-section verbatim chunks of real post-cutoff EDGAR
  EX-10 exhibits (45 exhibits fetched with receipts, `sources/edgar_exhibits_2026-08/`),
  each mixing the target attribute with neighboring and irrelevant provisions. Real edges
  include a formula governing-law clause, a split federal/state election, an arbitration
  section that is all definitions with the mandate elsewhere, 'at any time ... for Cause'
  termination, non-exclusive-license and asset-allocation false friends, and a
  redacted-term assignment condition. Golds 13 PRESENT / 14 ABSENT.
- **Statutory entailment (27, rewritten).** Every rule is now a multi-subsection scheme
  (definitions, governing provision, inapplicable neighbors, exceptions) so coders must
  locate the governing subsection; constructs and golds match v001 (11/11/5), and the
  family stays synthetic per the frozen specification's executable-template design.
- Presence/move (r2 real-opinion rebuilds), role, relation, and interpretive application
  are unchanged; role and relation remain flagged for optional real-document rebuilds.

## 2. Composition: the union hits the frozen target mixture exactly

The intensive panel's known limitation (recorded in the manuscript) is that its realized
composition put 36% of items in the one family that carries all the variance, against a 15%
target. The expansion sizes each family so the **300-item union lands exactly on the frozen
task mixture** of `task_family_spec_v004.json` (weights unchanged; the estimand is untouched):

| Family | Weight | Carried | New | Total | Share |
|---|---|---|---|---|---|
| opinion_feature_presence | 0.15 | 3 | 42 | 45 | 15% |
| interpretive_move_coding | 0.15 | 3 | 42 | 45 | 15% |
| structured_value_extraction | 0.15 | 3 | 42 | 45 | 15% |
| contract_clause_coding | 0.10 | 3 | 27 | 30 | 10% |
| judicial_document_role | 0.10 | 3 | 27 | 30 | 10% |
| cross_document_relation_coding | 0.10 | 3 | 27 | 30 | 10% |
| statutory_entailment | 0.10 | 3 | 27 | 30 | 10% |
| interpretive_application | 0.15 | 12 | 33 | 45 | 15% |
| **Total** | 1.00 | **33** | **267** | **300** | 100% |

Dependence-cluster counts (the unit the bootstrap resamples) rise from 21 to 243 overall and
from 6 to 24 in the interpretive family (r2: presence/move items from the same source
opinion share that opinion's cluster).

### What the new items add, family by family

- **Opinion-feature presence (42).** From 2 features to 14 judicial acts (legislative history,
  enacted text, distinguishing precedent, dictionary reliance, purpose reasoning, canons,
  constitutional avoidance, agency deference, harmless error, waiver/forfeiture, judicial
  notice, severability, lenity, overruling), each as a clear positive / hard negative
  (attribution, string cite, refusal, bypass) / edge triplet. Golds 20 PRESENT / 18 ABSENT / 4
  UNCERTAIN.
- **Interpretive-move coding (42).** All six codebook labels populated: 8 TEXT (grammar,
  punctuation, definitions, whole-act, interpretation clauses, tense, placement, shall/may),
  7 PURPOSE, 7 LEGISLATIVE HISTORY (reports, sponsor statements, rejected amendments,
  conference resolution, hearings, drafting evolution, colloquy), 8 REPORTING-ONLY hard
  negatives, 8 NO-CODED-MOVE, 4 UNCERTAIN (co-equal moves; unsupplied decisive passage).
- **Structured-value extraction (42).** New document genres (leases, notes, purchase
  agreements, bylaws, settlement motions, probate petitions, entered orders, affidavits,
  ordinances) and field types (amounts, dates, durations, rates, percentages, counts, actors,
  defined terms, rule citations, thresholds), each with same-type distractors planted in the
  text. 38 value golds + 4 UNCERTAIN (value absent, unresolved duplicate, value defined by
  reference, count stated without a numeral).
- **Contract-clause coding (27).** From 2 attributes to 9 (governing law, arbitration,
  indemnification, liability cap, termination for convenience, automatic renewal, assignment
  restriction, force majeure, exclusivity), each as positive / adjacent-attribute hard
  negative (forum≠law, mediation≠arbitration, insurance≠indemnity, damages-exclusion≠cap,
  cause-only≠convenience, mutual-renewal≠automatic, subcontracting≠assignment,
  definition-only≠operative excuse, first-refusal≠exclusivity) / edge. 14 PRESENT / 10 ABSENT
  / 3 UNCERTAIN.
- **Judicial-document role (27).** Five items per role plus two UNCERTAIN (combined
  stipulation-and-order; caption-only excerpt). Includes the codebook's own edge rules as
  items: proposed order in judicial voice, affidavit of service as a service record.
- **Cross-document relations (27).** Fresh two-document packets: 9 CONSISTENT (incl.
  request-granted and $1.875M/$1,875,000 notation variants), 8 INCONSISTENT (amount, date,
  count-transposition, prejudice-term, and factual conflicts), 7 NO_RELATION, 3 UNCERTAIN
  (figure in an unsupplied exhibit; partial excerpt; unlinked list-vs-count).
- **Statutory entailment (27).** New executable constructs: inclusive vs. exclusive endpoints
  at the boundary, day-count windows, conjunction/disjunction, full vs. partial exception
  satisfaction, nested definitions, definitional exclusion, tiered schedules, more-than
  counting at the boundary, negative conditions, scope limits, rate arithmetic, written-form
  requirements. 11 ENTAILED / 11 CONTRADICTED / 5 UNCERTAIN (missing occupancy, vague date,
  unstated registration, boundary measurement, unstated writing).
- **Interpretive application (33).** Fifteen new familiar/novel twin pairs instantiating
  canonical structures not yet in the panel — trading-as-use (Smith), residual-object (Yates),
  ordinary-vs-technical (Nix), general-words-vs-spirit (Holy Trinity), enumerated-catchall
  (McBoyle), grand-term-petty-facts (Bond), public-duty conflict (Kirby), deadline literalism
  (Locke), clear-text-disproportionate-consequence (TVA v. Hill), assistive instance (guide
  dog), verb scope (Muscarello), old-term-new-technology (Edison Telephone), category boundary
  (burrito-as-sandwich), but-for-through-proxy (Bostock), scrivener's over-broad repealer
  (Cernauskas) — plus three untwinned originals with no famous analogue, spanning intended
  difficulty. Familiar twins are contamination sentinels (`public_paraphrase`,
  contamination_risk high) and are never usable as confirmatory items; the sentinel contrast
  grows from 6 to 21 twin sets.

### Two deliberate design choices to flag for the audit

1. **Deterministic UNCERTAIN golds (25 of 234 objective items, ~11%).** The pilot found
   *perfect* agreement on every objective item — a ceiling that leaves the route contrast no
   room to show. Each objective family now includes edge items whose deterministic right
   answer under the frozen codebook is UNCERTAIN (insufficient excerpt, unresolved duplicate,
   missing required fact), plus harder-but-decidable edges (difficulty tags `basic` /
   `hard_negative` / `edge` are recorded in every `authoring_note` for stratified analysis).
   If routes ever diverge on objective work, these are the items that will show it first.
2. **Carried-over items are re-coded.** The 33 intensive items ride along in the new runs.
   Their August 6–7 results at R=12 are already archived; re-coding them (~372 codings/pair)
   gives a same-run bridge between the intensive and diversity stages — a direct check on
   model/product drift between run dates, for trivial cost. Stage results are never pooled
   with intensive results; the bridge is a comparison, not a top-up (the no-additions stop
   rule binds within runs, and each diversity run is a new seeded, sealed run).

## 3. Run plan: two runs per vendor pair, tiered repeats

The frozen launcher takes uniform repeats per run, so tiered repeats are implemented as two
runs per vendor pair — no launcher changes:

| Run | Panel | Items | Repeats/route | Codings/pair | API codings/pair |
|---|---|---|---|---|---|
| `diversity_objective_<vendor>_v001` | objective panel | 255 | 2 | 1,020 | 510 |
| `diversity_interpretive_<vendor>_v001` | interpretive panel | 45 | 12 | 1,080 | 540 |
| Total per vendor pair | | 300 | | 2,100 | 1,050 |

Rationale for the tiering: the pilot located **all** variance in the interpretive family, so
depth (R=12, matching the intensive design exactly, so the family pools cleanly across all 45
items) goes where the variance is, and breadth (R=2, the design minimum) goes where variance
was zero and the question is coverage. Total: **4,200 codings across both pairs** (vs. 1,584
in the intensive pilot).

Everything else is inherited unchanged from the intensive configuration: matched HIGH
reasoning effort on both arms (Amendment A1), seeded interleaved schedules, hash-sealed
configuration, operational-only status output, the 25% block-failure stop, model-drift stop,
single linked retry, parent-side serial merging, and per-run `analysis_v00N.json` written by
the gated analyze subcommand. Runbooks mirroring `intensive_runbook_{anthropic,openai}_v001.md`
will be issued for the four runs upon approval, each pinning its panel and envelope hashes.

### Cost and load projection

Using realized intensive-pilot unit costs (Claude API arm ≈ $0.0254/coding; GPT ≈ $0.0093):

| | Claude pair | GPT pair |
|---|---|---|
| API-route metered cost (1,050 codings) | ≈ $27 | ≈ $10 |
| Sub-agent route | subscription allowance only | subscription allowance only |
| Wall-clock (sub-agent arm, vs. 396-coding intensive arm) | ≈ 2.5× intensive | ≈ 2.5× intensive |

Both fit comfortably inside the draft stage ceilings in `budget_plan_v004.json` ($60 per
diversity stage; cumulative ceiling path $150 → $210 → $270, confirmatory envelope intact at
$1,000). Human-adjudication exposure stays far under the D6 cap of 300: even if every one of
the 45 interpretive items became a route disagreement and objective disagreements ran at ten
times the pilot rate, the load would be ≈ 70 items.

## 4. Analysis plan (frozen tools, no new estimators)

Per vendor pair, on the union of the two runs' rating records (the estimators are per-item
and handle unequal repeats; the bootstrap resamples dependence clusters within family):

1. **Primary:** `Δ_overall = Σ w_f D_f` (SS − AA), computed by `agreement_analysis_v004.py`
   with `cluster_bootstrap_v001.py` (B = 2,000, BCa + percentile, TOST at ±0.05), exactly as
   in the intensive analysis. Report alongside, never pooled with, the intensive estimates.
2. **Secondaries (all prespecified in design v0.04):** AS − AA; route-shift term; per-family
   agreement; validity decomposition.
3. **Sentinel contrast:** familiar vs. novel twins across the 21 twin sets (paired,
   cluster-bootstrapped) — the contamination check the twin design exists for.
4. **Difficulty stratification (descriptive):** agreement by the `basic` / `hard_negative` /
   `edge` authoring tags, to locate where objective agreement first degrades.
5. **Cross-stage drift bridge (descriptive):** for the 33 carried items, diversity-run vs.
   intensive-run agreement rates per route.
6. **Variance components for the confirmatory power simulation:** feed the realized per-family
   modal-mass and heterogeneity estimates into `power_simulation_v001.py` (its designed role
   under design v0.04 Section 11) before the confirmatory freeze.

## 5. What this stage is and is not

- All 300 items are **development-phase materials** (`benchmark_phase: development_only`),
  permanently excluded from the confirmatory pool, like all pilot materials before them. The
  manuscript can report the expanded benchmark as the diversity stage of the same study.
- No frozen artifact changes: `design_v004.md`, `budget_plan_v003.json`,
  `task_family_spec_v004.json`, schema, codebook, and the intensive panel/envelopes are
  untouched. The panels byte-preserve the intensive rows, and the carried-over envelopes are
  asserted byte-identical on every build.
- Upon approval, record the decision in `project/DECISIONS.md` and append an Amendment A2 to
  `design_v004.md` (drafted below), and `budget_plan_v004.json` supersedes v003.

## 6. Approval mechanism

1. Audit the 267 new items in `diversity_panel_review_v001.md` (checklists per item; the
   gold-label line for every objective item states the intended deterministic answer and its
   rationale — flag any item where you'd code differently).
2. Request revisions by item id; I will issue `_v002` source files and rebuild (new hashes).
3. Approve by recording the decision and pasting the diversity handoff prompt (issued with
   the runbooks), which pins the two panel SHA-256 values above.

## 7. Draft Amendment A2 text (for design_v004.md upon approval)

> **Amendment A2 (researcher decision, 2026-MM-DD): diversity-pilot configuration.** The
> diversity pilot enlarges the staged ~160-item design to a 300-item panel: the 33 intensive
> items (byte-preserved, re-coded as a cross-stage bridge) plus 267 new development items
> whose union realizes the frozen eight-family target mixture exactly. Two runs per vendor
> pair implement tiered repeats: the 255-item objective panel at R_A = R_S = 2 and the
> 45-item interpretive panel at R_A = R_S = 12, both at matched HIGH reasoning effort under
> the Amendment A1 configuration. Panels `diversity_objective_panel_v001.jsonl`
> (sha256 d4448c82…) and `diversity_interpretive_panel_v001.jsonl` (sha256 26d0ddca…) are
> approved as audited via `diversity_panel_review_v001.md`; stage ceilings per
> `budget_plan_v004.json`. Diversity-stage results are reported alongside, never pooled with,
> intensive-stage results; all panel items remain permanently excluded from the confirmatory
> pool.
