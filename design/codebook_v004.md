# Coding codebook v0.04

Status: development-only; supersedes v0.03 for the diversity-pilot panels (v0.03 remains the codebook of record for the instrument and intensive pilots). Changes: structured-value extraction is now a free-text task with a back-end equivalence check; contract-clause items present multi-clause chunks; statutory-entailment items present multi-subsection schemes. Audit and freeze this codebook and every source material before confirmatory generation.

## Common rules

Return exactly one JSON object matching `response_schema_v001.json`. Copy the supplied `item_id`. Choose exactly one of the item's `allowed_labels`. Cite only material IDs supplied in the item. Do not use outside sources, browse, inspect sibling assignments, or infer a gold answer from filenames or provenance. Base the label only on the frozen materials and question.

Use `UNCERTAIN` only when the supplied material is genuinely insufficient or the frozen rule does not resolve the case. Do not use it merely because the task is difficult. Use the shortest rationale needed to expose the decisive reading. The rationale is an audit trail, not a second label.

## Opinion-feature presence

Choose `PRESENT` only if the opinion itself substantively performs the feature named in the question. A bare reference, quotation without analysis, string citation, or report of a party's or dissent's argument is `ABSENT` unless the opinion adopts or evaluates it. Cite the passage that performs the feature, or the passage showing the hard negative. Do not infer from the case outcome.

## Interpretive-move coding

Code the principal move made by the supplied passage:

- `TEXT_ANALYSIS`: analyzes enacted words, grammar, syntax, punctuation, definitions, placement, or whole-act fit;
- `PURPOSE_ANALYSIS`: uses an identified statutory aim or problem addressed to interpret the law;
- `LEGISLATIVE_HISTORY_ANALYSIS`: evaluates committee reports, sponsor statements, hearings, or other legislative materials;
- `REPORTING_ONLY`: reports an argument or source without adopting or evaluating it;
- `NO_CODED_MOVE`: contains none of the listed moves; and
- `UNCERTAIN`: the passage genuinely combines equally central coded moves or lacks enough context to select one.

Do not count the formula "we begin with the text" as text analysis unless the passage analyzes enacted language. When multiple moves appear, code the one doing the principal inferential work.

## Structured-value extraction

Answer with the requested value itself, as a short free-text response copied from the supplied material: report the value exactly as the document expresses it (for example `$1,875,000`, `June 12, 2026`, `thirty (30) days`, `Marcus Fenwick`). Do not reformat, convert units, round, or add words; equivalent formattings are reconciled mechanically after coding. The field may be a defined term, date, threshold, actor, amount, count, rate, or another expressly stated value. Distinguish the target from nearby values of the same kind: other amounts, other dates, superseded figures, values quoted from other documents, cross-references, and values appearing only inside definitions. Answer `UNCERTAIN` when the requested value is not stated in the supplied material or two different values are equally supported without reconciliation. A few legacy items instead present enumerated candidate labels; for those, choose exactly one candidate, preserving its spelling.

## Contract-clause coding

The supplied material is an excerpt of one or more contract provisions, which may span several numbered sections and mix subjects. Choose `PRESENT` only if some provision within the supplied excerpt itself states the specified attribute or legal effect. Choose `ABSENT` when the excerpt's provisions address other subjects, merely use a defined term without establishing the attribute, or leave the specified matter to a provision outside the excerpt. Irrelevant neighboring provisions do not make an excerpt incomplete. Do not assume omitted contract language. Use `UNCERTAIN` only when the excerpt is internally incomplete in a way that prevents either code, such as an operative sentence that breaks off or a governing election left to an unsupplied document.

## Judicial-document role

Code the role the supplied material performs in the record:

- `PARTY_REQUEST`: a party asks the court for relief or a ruling, including a motion or petition;
- `EVIDENTIARY_SUPPORT`: a declaration, affidavit, exhibit, or similar material supplies factual support rather than itself requesting or entering relief;
- `PROPOSED_DISPOSITION`: a party-submitted proposed order, judgment, or findings states requested disposition but is not itself an entered judicial act;
- `ENTERED_DISPOSITION`: the court actually enters an order, judgment, findings, or other disposition;
- `ADMINISTRATIVE_RECORD`: a notice, filing receipt, service record, appearance, scheduling entry, or other non-adjudicative record; and
- `UNCERTAIN`: the supplied material combines roles or omits the feature needed to distinguish them.

Use the material's operative function, not merely a stray word such as "order." A document expressly labeled proposed remains `PROPOSED_DISPOSITION` even if written in judicial voice.

## Cross-document relation coding

Evaluate only the proposition named in the question:

- `CONSISTENT`: both materials address the proposition and their relevant statements can be true together, including a request later granted or a factual assertion directly supported;
- `INCONSISTENT`: both address the proposition and their relevant statements materially conflict;
- `NO_RELATION`: one or both materials do not address the proposition, so no cross-document consistency judgment is available; and
- `UNCERTAIN`: both appear relevant, but the minimized excerpts omit what is needed to decide consistency.

Do not treat silence as inconsistency. Do not infer that an affidavit supports an unrelated proposition merely because it was filed in the same case. Compare exact amounts and counts as written.

## Statutory entailment

The supplied rule may be a scheme of several numbered subsections, not all of which bear on the question; identify and apply the provisions that govern the assertion. Choose `ENTAILED` when the assertion follows from the supplied rule and facts. Choose `CONTRADICTED` when they make the assertion false. Apply thresholds, inclusive endpoints, conjunctions, disjunctions, definitions, cross-references among the supplied subsections, and express exceptions as written. Do not import unstated legal rules, and do not let an inapplicable subsection displace the one that governs. Use `UNCERTAIN` only if a fact required by the governing provisions is missing.

## Interpretive application

Choose `APPLIES` when the best reading of the stated rule covers the facts, `DOES_NOT_APPLY` when it does not, and `GENUINELY_AMBIGUOUS` only when ordinary meaning, stated purpose, or their interaction leaves materially balanced substantive readings. The enacted rule remains authoritative; purpose is interpretive context rather than a free-standing prohibition. Use `UNCERTAIN` only for a defect in the supplied materials or codebook, not as a substitute for `GENUINELY_AMBIGUOUS`.

## Failure treatment

A schema-invalid response, disallowed label, missing item ID, or terminal execution failure becomes `PARSE_FAILURE` in analysis and remains in the planned denominator. Agreement between two failures is scored zero, not as substantive agreement.

## Free-text responses (instrument v0.4)

For items whose envelope marks `response_mode: free_text`, the `label` field of the response carries the free-text answer itself (or `UNCERTAIN`). Copy values as the document states them; equivalence across formattings is adjudicated mechanically by the frozen normalizers in `answer_equivalence_v001.py`, which also treat any casing of `UNCERTAIN` as the escape token. All other response requirements (item ID, evidence IDs, rationale) are unchanged.
