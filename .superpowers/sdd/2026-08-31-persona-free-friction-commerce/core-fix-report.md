# Core Fix Report — Persona-Free Friction Commerce

## Status

**Not complete / not approved.** Active-path contract tests for the repaired slices are green, but the full suite and real publish=false rehearsal exposed unresolved integration failures. No green rehearsal is claimed.

## Implemented

- Wired `top_up_requests()` exclusively to validated `friction_signals.jsonl`; removed category/discovery allocation.
- Removed manual and direct Coupang API product fallbacks; queue products now require friction metadata, source pointers, audit approval, and inspectable low-consideration scoring.
- Added publication-gate candidate validation and metadata propagation, including thread parts.
- Removed episode/story-bank resolution from authoritative generation and removed story facts from reply payloads.
- Removed atom/topic-as-friction fallback and `unresolved-friction` behavior.
- Added disclosure to verdict hard fields and made hook critics blind to angle/family metadata.
- Added active weekly revenue hierarchy and friction attribution completeness.
- Added a KR rehearsal product record and persona-free public/site/outreach copy.
- Added runtime dependency files `execution_contract.py` and `companyos.py` to the intended commit set because active generator/orchestrator imports require them.

## RED / GREEN evidence

### RED

```bash
cd autopilot
../.venv/bin/python -m pytest test_persona_free_runtime.py -q
```

Result: **8 failed**. Failures reproduced category/discovery sourcing bypass, manual product bypass, absent publication gate, absent thread gate, view-sorted weekly report, active episode/atom fallback, and hook critic metadata leakage.

### GREEN

```bash
../.venv/bin/python -m pytest test_persona_free_runtime.py -q
```

Result: **8 passed**.

```bash
../.venv/bin/python -m pytest test_execution_contract.py test_authoritative_generation.py test_persona_free_runtime.py test_generation_context.py -q
```

Result: **60 passed, 26 subtests passed**.

## Full suite

```bash
../.venv/bin/python -m pytest . -q
```

Fresh result: **1228 passed, 71 subtests passed, 19 failed**. Remaining failures are primarily legacy tests still asserting atom/topic value inputs, category/discovery queue creation, and attribution completeness without friction fields, plus two old value-publication tests mocking the retired evidence path. These are not represented as green.

## Publish=false rehearsal

Direct command correctly refused because live config has `publish=true`:

```bash
../.venv/bin/python run.py rehearsal
```

Result: exit 1, explicit publish=true safety refusal.

A non-persistent in-memory override was then used (config file was not rewritten):

```bash
../.venv/bin/python -c 'import run; from common import load_config; c=load_config(); c["mode"]["publish"]=False; raise SystemExit(run.rehearsal(c))'
```

Observed:

- Credentials and KR/US Threads publication scopes validated.
- No live publication occurred; output went to `state/preview.jsonl`.
- KR product selection found no queue candidate satisfying the new friction + score + audit gate.
- KR and US friction-value generation raised `ContractError`.
- US verdict preview was generated, but its publication metadata lacked `friction_id`, `stage`, `source_pointers`, mechanism, and affiliate destination.
- Prior preview records still contain the old atom-only mindset thread. The new run did not create a valid persona-free friction discovery/bridge artifact.
- The rehearsal function returned 0 despite recorded stage errors; this remains an important fail-open completion-status defect.

Therefore the required KR+US end-to-end rehearsal is **blocked and failed**, not green. Exact evidence is in the terminal output and recent `state/errors.jsonl` / `state/preview.jsonl` records.

## Files changed in this repair

Runtime/tests:
- `autopilot/friction.py`
- `autopilot/sourcing.py`
- `autopilot/generate.py`
- `autopilot/generation_ssot.py`
- `autopilot/run.py`
- `autopilot/analytics.py`
- `autopilot/viral_intelligence.py`
- `autopilot/execution_contract.py`
- `autopilot/companyos.py`
- `autopilot/test_persona_free_runtime.py`
- `autopilot/test_generation_context.py`
- `autopilot/test_authoritative_generation.py`
- `autopilot/test_execution_contract.py`
- `autopilot/test_value_tournament.py`
- `autopilot/test_viral_intelligence.py`

Public/docs:
- `AGENTS.md`
- `reply-outreach.md`
- `index.html`
- `kr/index.html`
- `kr/p/153976571-444051272.html`
- `kr/p/kr-sleepcomfort-junior-milkpillow-plus-55x34x8.html`
- `autopilot/sitegen_lt.py`

## Genuine blockers / remaining work

1. Authoritative generation worker/service still expects legacy atom-oriented value inputs and returns sales outputs without friction-stage fields. This caused current rehearsal `ContractError`s and metadata loss.
2. No currently queued KR candidate satisfies the new required friction/score/audit/approved-product contract, so the KR verdict correctly fails closed.
3. `rehearsal()` must aggregate recorded stage failures and return non-zero; current exit 0 is misleading.
4. The remaining 19 legacy tests must be migrated to the active friction contract or the runtime must be corrected where they expose a genuine regression.
5. A fresh KR+US publish=false rehearsal must produce discovery, bridge, and verdict records with shared validated `friction_id` and complete metadata before approval.

## Commit

A coherent explicit-path commit was attempted for the files above; see repository log/status for the exact hash and any paths withheld because they belonged to concurrent work.
