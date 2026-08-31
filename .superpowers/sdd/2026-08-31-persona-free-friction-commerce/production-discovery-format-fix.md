# Production discovery format fix — 2026-08-31

## Outcome

Production discovery generation now fails closed unless every candidate is a raw 4–6-nonblank-line micro-hook whose final line is an experience question. The contract is stage-aware, so bridge and verdict behavior is unchanged.

## TDD evidence

### RED

Command:

```bash
.venv/bin/python3 -m pytest autopilot/test_generation_context.py autopilot/test_authoritative_generation.py -q
```

Observed before implementation: `3 failed, 25 passed, 4 subtests passed`.

The failures proved that:

- legacy two-paragraph discovery copy was accepted;
- the authoritative KR/US prompt did not require the shape;
- the worker candidate gate had no stage-aware discovery validation.

### GREEN

Targeted contract suite:

```text
28 passed, 4 subtests passed in 3.70s
```

Related publication/tournament suite after updating fixtures to the approved contract:

```text
25 passed in 0.15s
```

Fresh full suite:

```text
1267 passed, 81 subtests passed in 33.46s
```

Additional gates:

- `autopilot/post_check.py autopilot/test_posts.json --test`: `39/39` matched
- `autopilot/test_ops.py`: `ops safety tests: PASS`
- `git diff --check`: clean

## Implementation

- `autopilot/generation_worker.py`
  - normalizes omitted non-commercial stage to `discovery` before generation;
  - validates every generated discovery candidate before critic selection;
  - requires 4–6 nonblank lines and a final `?` question;
  - retries once with an explicit shape-repair prompt, then fails closed.
- `autopilot/generate.py`
  - applies the same hard contract to the legacy/publication candidate validator;
  - updates deterministic KR/US discovery fixtures to the valid shape.
- `autopilot/generation_ssot.py`, `heightcue-gemini-skills.md`, `context/voice-kr.md`, `context/voice-us.md`
  - explicitly request the same raw micro-hook shape for both KR and US;
  - prohibit essay paragraphs and require a final reader-experience question.
- Regression tests prove 3-line, 7-line, paragraph, and non-question discovery drafts are rejected while bridge behavior remains unchanged.

## Real production US publish=false proof

Executed the normal production generation path with a deep-copied in-memory config override:

```json
{"publish": false, "dry_run": false}
```

No config file was rewritten and no live publish was attempted. The run used validated production friction `fr-us-toy-small-pieces-20260831` and created preview `PREVIEW-1788143218` with `publish_status=preview`:

```text
Small craft pieces and miniature toys covering the room.
Picking up plastic food sets piece by piece off the rug.
Stashing whole bins out of sight for a temporary reset.
Have you found yourself hiding toy sets to avoid the cleanup?
```

Read-back assertions:

- market: `US`
- stage: `discovery`
- nonblank lines: `4`
- final line question: `true`
- product/affiliate link: `false`
- live publication: `false`

The previously observed two-paragraph output can no longer pass either generated-candidate validation or the publication candidate gate.
