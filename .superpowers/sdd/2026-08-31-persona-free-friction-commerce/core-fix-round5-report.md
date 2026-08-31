# Core Fix Round 5 Report

## Outcome

The final Company OS authoritative `get_product()` gap is closed. Both live claim acquisition and authoritative `product:<key>` resolution now use the same `normalize_product()` function. It preserves an explicit non-empty `price_band`; otherwise it derives a band only from a finite, non-negative typed numeric USD `price_info.amount` through `USD_PRICE_BANDS`. It never invents an amount and malformed/untyped/no-band products fail closed.

## Strict TDD evidence

Focused RED before production changes:

```text
../.venv/bin/python -m pytest test_fix_round5.py -q
2 failed in 0.20s
- KeyError: 'price_band' after real product:<key> resolution
- malformed authoritative price did not fail closed
```

Focused GREEN:

```text
../.venv/bin/python -m pytest test_fix_round5.py -q
2 passed in 0.17s
```

Expanded focused regression:

```text
../.venv/bin/python -m pytest test_fix_round5.py test_fix_round4.py test_fix_round3.py \
  test_fix_round2.py test_supabase_products.py test_authoritative_generation.py \
  test_execution_contract.py test_analytics.py test_ops.py -q
106 passed, 26 subtests passed in 6.14s
```

## Authoritative production-shaped proof

`test_fix_round5.py` feeds an approved Company OS product with:

```json
{"price_info":{"amount":19.99,"currency":"USD"}}
```

and no stored `price_band`, then traverses the real path:

```text
generation_ssot.resolve_inputs(["product:us-authoritative-priced-product"])
→ companyos.get_product()
→ companyos.normalize_product()
→ generation_worker.bind_friction_contract()
```

The test proves `US_15_30` reaches the resolved authoritative packet, verdict output, US publication metadata, and a metric for which `analytics.attribution_gaps()` returns `[]`. A separate real-resolver regression proves string amount `"19.99"` fails closed with `CompanyOSError`.

## Fresh verification

Full suite:

```text
../.venv/bin/python -m pytest . -q
1261 passed, 77 subtests passed in 30.70s
```

Ops suite:

```text
../.venv/bin/python -m pytest test_ops.py -q
27 passed in 0.71s
```

`git diff --check` exited 0.

## Fresh KR/US publish=false 6-row E2E

The rehearsal used an in-memory `publish=False` override; `config.json` remained unchanged and no live publication occurred. It returned 0, appended exactly six preview rows, and left `published.jsonl` unchanged at 69 rows.

```json
{"all_preview":true,"all_provenance":true,"market_stages":[["KR","bridge"],["KR","discovery"],["KR","verdict"],["US","bridge"],["US","discovery"],["US","verdict"]],"new_rows":6,"noncommercial_clean":true,"published_after":69,"published_before":69,"return_code":0,"verdict_complete":true,"verdict_price_bands":{"KR":"KR_10_30K","US":"US_15_30"}}
```

Every row has `PREVIEW-*`, `publish_status=preview`, and complete friction/stage/market/source provenance. Verdict rows have mechanism, failure mode, skip condition, price band, attributable route, and disclosure. Discovery/bridge rows have no link, URL, or `#ad`.

## Files changed in Round 5

- `autopilot/companyos.py`
- `autopilot/test_fix_round5.py`
- `.superpowers/sdd/2026-08-31-persona-free-friction-commerce/core-fix-round5-report.md`

No stash, reset, checkout-overwrite, broad staging, live publication, or subagent was used. Unrelated dirty-tree files were not staged.
