# Production generation fix — US discovery multi-candidate

Date: 2026-08-31

## Failure reproduced

The real authoritative US discovery request for `fr-us-toy-small-pieces-20260831` reached the OpenRouter writer, but the `value_post` directive only said “Return only JSON candidates.” Gemini returned a direct/single draft shape, and the unchanged fail-closed validator rejected it with `writer must return at least two candidates`.

## Fix

- Made the code-owned `value_post` directive specify the exact object schema with at least two uniquely identified candidates.
- Added one bounded schema-repair retry for tournament writers. The retry repeats the exact required shape without embedding or trusting the malformed response.
- Kept `validate_candidates()` unchanged: fewer than two candidates, duplicate IDs, extra fields, empty values, or wrong shapes still fail closed.
- Added explicit grounding boundaries to keep candidates eligible for the existing critic: no invented numbers, prevalence/frequency, first-person experience, family history, dialogue, or facts absent from the resolved friction payload.

## TDD evidence

RED:

- Exact `value_post` multi-candidate schema test failed against the vague directive.
- Semantic retry test errored because no retry boundary existed.
- Grounding-boundary directive test failed for all required fabrication categories.

GREEN:

- New focused tests pass.
- `test_authoritative_generation`: 20 tests pass.
- Full suite: `1264 passed, 81 subtests passed`.

## Real production-path verification

Ran `run.make_and_publish_value` with the real runtime config, `publish=False`, no `_rehearsal`, `country="US"`, `stage="discovery"`.

Result:

```text
[autopilot] 검사(US/value): 포맷 84점, 리스크 메모 0건
[autopilot] 리허설(발행 안 함, US): A bedroom floor covered in miniature toy food, s... → state/preview.jsonl
('PREVIEW-1788142515', 'published')
```

The authoritative writer produced a valid multi-candidate bundle, the grounded critic selected an eligible candidate, the friction/persona-free contract validated it, and the preview completed without publishing.
