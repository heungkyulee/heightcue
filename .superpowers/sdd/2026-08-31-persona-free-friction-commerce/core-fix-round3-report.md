# Core Fix Round 3 Report — Persona-Free Friction Commerce

## Outcome

All four blocking re-review findings are fixed on active paths. Fresh verification is green: the complete pytest suite passes, ops safety passes, and a KR/US `publish=false` rehearsal appended six valid preview records (discovery, bridge, verdict per market) with zero `published.jsonl` delta, no fixture leakage, and no retired-persona tokens.

## Fixes

1. **KR product identity / single SSOT**
   - KR queue selection now attaches `queue_product:<product_key>:<sha256>` for the exact audited queue packet selected.
   - The authoritative worker re-reads `autopilot/state/browser-queue/results.json` by product key, verifies the immutable packet digest, audit status/owner alias, and low-consideration score, and never calls Company OS for this KR identity.
   - Master, hooks, and verdict generation all preserve the same `_generation_input_id`; US continues to use Company OS.
   - A mutation after selection fails closed with a digest mismatch.

2. **Persona removal**
   - Removed the live critic sentence that trusted the 26-year-old / 167cm / 5'6 persona.
   - Expanded the active static contract scan to include `generation_worker.py` and `26-year-old`.

3. **Stage-aware analytics**
   - Discovery requires `friction_id`, `stage`, `market`, and `source_pointers`.
   - Bridge requires those fields plus `mechanism`.
   - Verdict requires those fields plus `mechanism`, `price_band`, and `affiliate_destination`.
   - Collector deterministically maps `affiliate_destination <- attributable_route <- link_mode`.
   - KR/US verdict publication metadata now carries the actual price band and final attributable route/destination. Bridge generation and publication carry the resolved mechanism.
   - Existing commission > orders > clicks > progression > qualified engagement > views hierarchy is preserved.

4. **Fixture boundary**
   - Sourcing fixtures require all of `_rehearsal is True`, `publish is False`, and the fixture marker.
   - Ordinary `dry_run` returns no product fixture and does not call live product sources.
   - Fixture-marked dry-run publication is blocked before `published.jsonl` write.
   - Removed the duplicate unreachable KR dry-run branch.

## TDD evidence

Initial focused RED:

```text
../.venv/bin/python -m pytest test_fix_round3.py -q
7 failed
```

The failures reproduced all four blockers: missing immutable KR queue identity, persona text in the worker, stage-inappropriate attribution gaps, absent route mapping/price metadata, dry-run fixture access, and fixture ledger leakage.

Focused GREEN:

```text
../.venv/bin/python -m pytest test_fix_round3.py test_fix_round2.py \
  test_persona_free_contract.py test_authoritative_generation.py \
  test_execution_contract.py test_analytics.py test_ops.py -q
90 passed, 26 subtests passed
```

## Full verification

```text
../.venv/bin/python -m pytest . -q
1256 passed, 77 subtests passed in 29.92s

../.venv/bin/python test_ops.py
ops safety tests: PASS
```

## Fresh KR/US publish=false E2E

The run used an in-memory `cfg['mode']['publish'] = False` override and called `run.rehearsal(cfg)`.

Read-back assertions and result:

```json
{"return_code": 0, "preview_offset": 69, "preview_rows": 6, "published_before": 69, "published_after": 69, "markets_stages": [["KR", "bridge"], ["KR", "discovery"], ["KR", "verdict"], ["US", "bridge"], ["US", "discovery"], ["US", "verdict"]]}
```

Verified for only the six newly appended rows:

- Every row has `friction_id`, `stage`, `market`, and `source_pointers`.
- Bridge rows have `mechanism`.
- Verdict rows have `mechanism`, `price_band`, `affiliate_destination`, and `attributable_route`.
- Discovery/bridge rows contain no URL, `#ad`, or Coupang disclosure.
- No row contains `167cm`, `5'6`, `26-year-old`, `growth mindset`, or `atom:`.
- `published.jsonl` remained at 69 rows; delta was exactly zero.
- Rehearsal returned 0 after credential checks and all six preview writes.

## Files changed in round 3

- `autopilot/analytics.py`
- `autopilot/execution_contract.py`
- `autopilot/generate.py`
- `autopilot/generation_ssot.py`
- `autopilot/generation_worker.py`
- `autopilot/publish.py`
- `autopilot/run.py`
- `autopilot/sourcing.py`
- `autopilot/test_fix_round3.py`
- `autopilot/test_fix_round2.py`
- `autopilot/test_persona_free_contract.py`
- `autopilot/test_ops.py`
- `autopilot/test_supabase_products.py`
- `autopilot/test_video_handoff.py`
- `autopilot/test_viral_intelligence.py`

No stash, reset, checkout-overwrite, `git add -A`, live publication, or subagent was used.
