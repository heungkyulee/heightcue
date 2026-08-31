# Core Fix Round 2 Report — Persona-Free Friction Commerce

## Outcome

Fix round 2 is complete for the core repository/runtime scope. The authoritative generation boundary now binds friction-stage metadata from resolved trusted inputs, rehearsal fails closed on newly recorded stage errors, the legacy regression expectations were migrated to the approved friction contract, and a fresh KR+US `publish=false` rehearsal produced six new preview rows (three stages per market) without live publication.

## RED evidence and classification

The required baseline command was run first:

```bash
cd autopilot
../.venv/bin/python -m pytest . -q
```

The shared working tree had already moved since the first-fix report: the previously reported 19 failures reproduced as **8 remaining failures** (`1237 passed, 77 subtests passed, 8 failed`). Classification:

- **6 obsolete contract tests**: legacy attribution rows omitted friction fields; legacy category/discovery queue creation; legacy demand provenance; atom-based value publication/thread fixtures.
- **2 runtime-facing obsolete value tests**: mocked the retired evidence-atom path rather than a validated friction signal and therefore never reached the active publication path.
- Safety/compliance assertions were not weakened. Category-only sourcing is now asserted to fail closed, malformed friction source pointers are explicitly rejected, and friction attribution remains mandatory.

New focused RED command:

```bash
../.venv/bin/python -m pytest test_fix_round2.py -q
```

Initial result: **4 failed**. It proved missing worker stage input/output metadata, incomplete verdict metadata, fail-open rehearsal exit status, and absent explicit complete KR/US rehearsal products.

## Implementation

- Added an explicit optional `stage` to the authoritative request protocol and worker payload.
- Added worker-side trusted binding from resolved friction/product records to:
  - `friction_id`, `stage`, `market`, `source_pointers`
  - verdict `mechanism`, `failure_mode`, `skip_if`, `attributable_route`, `disclosure`
- Added those fields to signed worker results/attestations; model-supplied metadata is overwritten by resolved authoritative data.
- Added explicit `rehearsal_fixture=true` KR and US storage products with inspectable scores, source pointers, failure modes, skip conditions, and attributable routes. Production product resolution remains Company OS fail-closed.
- Rehearsal generation is deterministic/no-paid-generation and still traverses candidate validation, post checks, and publication preview gates. The fixture bypass is accepted only when all three hold: fixture marker, `_rehearsal=true`, and `publish is False`.
- `daily()` now generates discovery + bridge + verdict for KR and US; each market shares one friction ID across all stages.
- Rehearsal counts newly appended errors and exits nonzero if any stage records an error. Preview gate failures now record an error rather than silently returning success.
- Removed current-run atom/topic generation from the active orchestration tests and updated attribution/queue tests to the friction contract.

## GREEN evidence

Focused:

```bash
../.venv/bin/python -m pytest test_fix_round2.py -q
# 4 passed
```

Full required verification:

```bash
../.venv/bin/python -m pytest . -q
# 1249 passed, 77 subtests passed in 28.61s

../.venv/bin/python test_ops.py
# ops safety tests: PASS
```

## Fresh KR+US publish=false E2E

In-memory override only; `config.json` was not rewritten:

```bash
../.venv/bin/python - <<'PY'
import run
from common import load_config
cfg = load_config()
cfg['mode']['publish'] = False
raise SystemExit(run.rehearsal(cfg))
PY
```

A programmatic marker/readback selected only newly appended preview rows: **offset 57, 6 rows, return code 0**.

Verified:

- KR: `discovery`, `bridge`, `verdict`; shared `fr-rehearsal-storage`
- US: `discovery`, `bridge`, `verdict`; shared `fr-rehearsal-storage-us`
- Every row: non-empty `friction_id`, `stage`, `market`, `source_pointers`
- Verdict rows: non-empty `mechanism`, `failure_mode`, `skip_if`, `attributable_route`, `disclosure`
- Discovery/bridge: no link, `#ad`, or commercial coupling
- New rows contain none of `167cm`, `5'6`, `growth mindset`, or `atom:`
- All six rows have `publish_status=preview` and `PREVIEW-*` media IDs
- `preview.jsonl` mtime advanced to `1788137780646810064`; `published.jsonl` remained older at `1788136357983873497`
- No Threads publish call occurred; no live post was created
- Rehearsal content generation used deterministic fixtures, so no paid generation occurred (credential validation still performed its existing model health probe)

## Files in this round

Core runtime:
- `autopilot/execution_contract.py`
- `autopilot/generation_worker.py`
- `autopilot/generation_ssot.py`
- `autopilot/generate.py`
- `autopilot/run.py`
- `autopilot/sourcing.py`
- `autopilot/publish.py`
- `context/execution-contract.json`

Tests/fixtures:
- `autopilot/generation_test_fixture.py`
- `autopilot/test_fix_round2.py`
- `autopilot/test_authoritative_generation.py`
- `autopilot/test_persona_free_runtime.py`
- `autopilot/test_value_tournament.py`
- `autopilot/test_viral_intelligence.py`
- `autopilot/test_ops.py`
- `autopilot/test_video_handoff.py`
- `autopilot/test_supabase_products.py` (shared-tree untracked test updated locally; not staged unless explicitly included)

## Safety notes

- No stash, reset, checkout-overwrite, `git add -A`, profile edits, live Threads changes, or subagents were used.
- Historical preview/published rows were not rewritten; verification selected only newly appended rows by marker offset.
