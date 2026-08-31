# HeightCue active generation skills — persona-free friction commerce

Every output is grounded in supplied source pointers. Never invent a narrator biography, family, DM, ownership, use, expertise, review, price, number, or comparison. Required identity fields on content: `friction_id`, `stage`, `market`, `source_pointers`; add `evidence_pointers` when factual claims require evidence.

## SKILL A2 — product research note
```
Build a source-grounded research note for one low-consideration product. Return JSON with friction_id, market, mechanism, verified_points, repeated_failure_modes, skip_if, source_pointers, evidence_pointers, hooks, missing_data, and risk_notes. Reject health-outcome promises and creator testimony.
```

## SKILL A3-KR — product verdict
```
Return JSON with text, friction_id, stage="verdict", market="KR", source_pointers, evidence_pointers, and self_check. Sequence: friction micro-hook; exact required Korean affiliate disclosure in its approved location; mechanism; one or two verified facts; repeated bad-review failure mode and why the selection reduces it; explicit "비추천" or "skip if"; attributable route. No narrator sentence.
```

## SKILL A3-US — product verdict
```
Return JSON with text, friction_id, stage="verdict", market="US", source_pointers, evidence_pointers, and self_check. Sequence: friction micro-hook; approved Associates disclosure; mechanism; one or two verified facts; repeated bad-review failure mode and why the selection reduces it; explicit "Skip if"; attributable route. No narrator sentence.
```

## SKILL V1 — friction discovery or mechanism bridge
```
Return JSON with text, friction_id, stage, market, source_pointers, evidence_pointers, angle_used, and self_check. stage=discovery: one repeated household scene and its cost; no product, brand, affiliate link, recommendation, or forced solution. stage=bridge: explain one generic form-factor mechanism in one screen; no specific product, brand, affiliate link, or direct coupling to an ad. Allowed angle families: scene, mechanism, bad_review, before_after, price_math. No creator-centered narrative.
```

## SKILL V2 — non-commercial friction thread
```
Return JSON with parts, friction_id, stage, market, source_pointers, evidence_pointers, and self_check. Use discovery or bridge rules for every part. Do not reply-chain or link to a commercial verdict. State the conclusion early; preserve evidence limits.
```

## SKILL A5 — comment classification and reply
```
Return JSON {"category":"...","action":"reply|hold|skip","reason":"...","text":"..."}. Use supplied conversation context only. Hold medical, disputed, or context-missing questions. Never claim personal or family experience.
```

## SKILL A4 — review
```
Return JSON with verdict, violations, and corrections. Fail candidates missing friction_id/stage/market/source pointers, violating stage separation, inventing testimony, exceeding evidence, weakening disclosure, or omitting verdict mechanism/failure mode/skip-if/route.
```
