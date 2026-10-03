#!/usr/bin/env python3
"""Build diversity-panel prompt envelopes by reusing the frozen v0.03 functions.

Every envelope is produced by `build_prompt_envelopes_v003.py` (loaded from its
file path): same canonical JSON, envelope construction, item response schema,
hashing, codebook, base response schema, and prompt contract version. The 33
rows carried over from the intensive panel therefore yield records
byte-identical to their `intensive_prompt_envelopes_v001.jsonl` counterparts;
this builder asserts that identity on every run.

Outputs:
- `diversity_objective_envelopes_v002.jsonl` (255 records)
- `diversity_interpretive_envelopes_v001.jsonl` (45 records)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType


HERE = Path(__file__).resolve().parent
V003_BUILDER = HERE / "build_prompt_envelopes_v003.py"
V003_MODULE_NAME = "build_prompt_envelopes_v003_frozen_for_diversity_v002"
INTENSIVE_ENVELOPES = HERE / "intensive_prompt_envelopes_v001.jsonl"

PANELS: list[tuple[Path, Path]] = [
    (HERE / "diversity_objective_panel_v002.jsonl", HERE / "diversity_objective_envelopes_v002.jsonl"),
    (HERE / "diversity_interpretive_panel_v001.jsonl", HERE / "diversity_interpretive_envelopes_v001.jsonl"),
]


class DiversityEnvelopeError(RuntimeError):
    pass


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


def load_v003_builder(path: Path = V003_BUILDER) -> ModuleType:
    """Load the frozen v0.03 envelope builder module from its file path."""
    cached = sys.modules.get(V003_MODULE_NAME)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(V003_MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise DiversityEnvelopeError(f"cannot load the v0.03 envelope builder: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[V003_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def assert_carryover_identity(rendered_by_id: dict[str, str]) -> int:
    """Assert carried-over intensive items rendered byte-identically here."""
    if not INTENSIVE_ENVELOPES.exists():
        raise DiversityEnvelopeError(
            f"missing intensive envelopes for the carry-over identity check: {INTENSIVE_ENVELOPES}"
        )
    matched = 0
    with INTENSIVE_ENVELOPES.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            item_id = json.loads(line)["item_id"]
            ours = rendered_by_id.get(item_id)
            if ours is None:
                raise DiversityEnvelopeError(
                    f"intensive item {item_id} is missing from the diversity envelopes"
                )
            if ours != line:
                raise DiversityEnvelopeError(
                    f"{INTENSIVE_ENVELOPES.name}:{line_number}: envelope for {item_id} "
                    "is not byte-identical to the diversity rendering"
                )
            matched += 1
    return matched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codebook", type=Path, default=None)
    parser.add_argument("--response-schema", type=Path, default=None)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        v003 = load_v003_builder()
        codebook_path = args.codebook if args.codebook is not None else v003.DEFAULT_CODEBOOK
        schema_path = (
            args.response_schema
            if args.response_schema is not None
            else v003.DEFAULT_RESPONSE_SCHEMA
        )
        codebook = codebook_path.read_text(encoding="utf-8")
        response_schema = v003.load_json(schema_path)
        rendered_by_id: dict[str, str] = {}
        summaries: list[str] = []
        for tasks_path, output_path in PANELS:
            rendered = v003.render(
                v003.build_records(v003.load_jsonl(tasks_path), codebook, response_schema)
            )
            for line in rendered.split("\n")[:-1]:
                rendered_by_id[json.loads(line)["item_id"]] = line + "\n"
            items = len(rendered.split("\n")) - 1
            if args.check:
                if output_path.read_text(encoding="utf-8") != rendered:
                    raise DiversityEnvelopeError(f"prompt envelope file is stale: {output_path}")
            else:
                atomic_write_text(output_path, rendered)
            summaries.append(f"{items} -> {output_path.name}")
        matched = assert_carryover_identity(rendered_by_id)
        verb = "PASS" if args.check else "wrote"
        print(
            f"diversity prompt envelopes {verb}: {'; '.join(summaries)}; "
            f"{matched} carried-over envelopes byte-identical to the intensive file"
        )
    except (OSError, ValueError, KeyError, DiversityEnvelopeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # v003.PromptBuildError is only importable after load
        if type(exc).__name__ == "PromptBuildError":
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
