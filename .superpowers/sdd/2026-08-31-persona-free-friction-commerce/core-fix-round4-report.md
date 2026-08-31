# Core Fix Round 4 Report

## Outcome

Both Round 4 blockers are fixed. KR queue selection and authoritative resolution now use one canonical packet constructor and digest, including request-derived `formfactor_id`/`ux_grade` and default `country=KR`. The Company OS US claim adapter now preserves explicit `price_band` or derives it from typed USD `price_info` through one mapping, and verdict binding fails closed when `price_band` is empty.

## Strict TDD evidence

Initial focused RED:

```text
../.venv/bin/python -m pytest test_fix_round4.py -q
3 failed in 0.18s
- audited queue packet digest mismatch
- KeyError: 'price_band'
- verdict without price_band did not fail closed
```

Focused GREEN after implementation:

```text
../.venv/bin/python -m pytest test_fix_round4.py -q
3 passed in 0.09s
```

Expanded focused regression:

```text
../.venv/bin/python -m pytest test_fix_round4.py test_fix_round3.py test_fix_round2.py \
  test_supabase_products.py test_authoritative_generation.py \
  test_execution_contract.py test_analytics.py test_ops.py -q
104 passed, 26 subtests passed in 4.11s
```

## KR canonical queue packet

- Added `sourcing.canonical_queue_product()` as the sole construction rule.
- Selection and resolver both load the matching request and apply the same request fallbacks plus KR country default before hashing or returning the trusted packet.
- The digest still covers the complete canonical non-runtime packet, so mutation after selection fails closed.
- Audit owner, score, status, blacklist, provenance and candidate gates remain intact.
- Selection constructs the authoritative canonical identity before recording queue consumption/sourced history.
- Production-shaped regression starts with a result missing `country`, `formfactor_id`, and `ux_grade`; the matching request supplies the two tags. The selected `_generation_input_id` resolves, and a later `price_info` mutation is rejected.

## US production price band

- Added one Company OS SSOT mapping: `USD_PRICE_BANDS`.
- Explicit non-empty bands are preserved.
- Otherwise only a finite, non-negative typed numeric amount with `currency=USD` is accepted and mapped; malformed/untyped claims fail closed.
- The claim adapter now carries the authoritative friction/mechanism fields needed by verdict generation along with `price_info` and `price_band`.
- Verdict binding now requires non-empty `price_band`.
- Production-shaped regression proves USD `{amount: 19.99, currency: USD}` maps to `US_15_30`, survives authoritative verdict binding and US publication metadata, and yields complete analytics attribution.
- No price amount was invented. The legacy rehearsal product with no price uses the explicit categorical marker `US_PRICE_UNAVAILABLE`.

## Fresh verification

Full suite:

```text
../.venv/bin/python -m pytest . -q
1259 passed, 77 subtests passed in 25.74s
```

Ops suite:

```text
../.venv/bin/python -m pytest test_ops.py -q
27 passed in 0.27s
```

`git diff --check` exited 0.

## Fresh KR/US publish=false 6-row E2E

The rehearsal used an in-memory `publish=False` override; `config.json` was not rewritten. Programmatic marker/readback asserted exactly the newly appended rows and unchanged publication count.

```json
{"market_stages":[["KR","bridge"],["KR","discovery"],["KR","verdict"],["US","bridge"],["US","discovery"],["US","verdict"]],"new_rows":6,"preview_offset":75,"published_after":69,"published_before":69,"return_code":0,"verdict_price_bands":{"KR":"KR_10_30K","US":"US_15_30"}}
```

Every row had `PREVIEW-*`, `publish_status=preview`, and non-empty friction/stage/market/source provenance. Both verdict rows had non-empty mechanism, failure mode, skip condition, price band, attributable route, and disclosure. Discovery/bridge rows contained no URL or `#ad`. No row was appended to `published.jsonl`.

## Files changed in Round 4

- `autopilot/sourcing.py`
- `autopilot/generation_ssot.py`
- `autopilot/companyos.py`
- `autopilot/generation_worker.py`
- `autopilot/test_fix_round4.py`
- `autopilot/test_authoritative_generation.py`
- `.superpowers/sdd/2026-08-31-persona-free-friction-commerce/core-fix-round4-report.md`

No stash, reset, checkout-overwrite, broad staging, live publication, or subagent was used. Unrelated dirty-tree files were not staged.
