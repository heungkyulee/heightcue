# HeightCue Gemini Viral Intelligence Design

**Date:** 2026-08-27
**Status:** Approved in chat

## Goal

Make Gemini 3.7 Flash the judgment engine for HeightCue product sourcing, KR/US content creation, review, and performance learning while Aside CLI remains the authenticated browser execution layer and deterministic Python gates remain the final factual/compliance boundary.

## Ground truth

The design is grounded in the 66-turn AI Studio conversation at `https://aistudio.google.com/prompts/1QNIpujCLuyjsLd8iRGespi9Sv6vVe8kE`, collected through Aside CLI account `u0`, and independently analyzed by OpenRouter `google/gemini-3.7-flash`. The raw conversation is not replayed on every run. Its durable requirements and regression examples are stored locally.

## Architecture

1. **Aside execution layer** reads authenticated pages and submits structured evidence. It does not make final product or content decisions.
2. **Gemini judgment layer** ranks sourcing candidates, generates hook/body variants, performs blind viral critique, and proposes playbook changes.
3. **Deterministic gate layer** validates source evidence, disclosure, prohibited claims, language, deduplication, and metrics attribution.
4. **Learning layer** promotes only evidence-backed tactics into the playbook and rolls them back when they regress.

## User intent contract

Create `context/user-intent-contract.md` and `autopilot/state/viral-goldens.json` from the approved conversation analysis. The contract must preserve the user's negative constraints: no generic roundup, no medical-claim framing, no AI-report prose, no numbered corporate summary, no first-plausible-product selection, no safety/compliance copy dominating the creative brief, and no unsupported viral claims.

## Sourcing

Use two independent lanes:

- `demand`: product ideas grounded in audience questions, comments, click behavior, or repeated problems.
- `discovery`: novel form factors that reduce parental friction even when no demand signal exists yet.

Each sourcing run evaluates at least five candidate archetypes: branded anchor, marketplace bestseller, budget commodity, UX-novel candidate, and alternate-form-factor substitute. It must save five or more evidence-backed candidates, compare at least three, record explicit rejection reasons for at least two, and nominate exactly one winner. Discovery can run when demand signals are empty; demand lane cannot invent a signal.

## Model policy

Use `openrouter/google/gemini-3.7-flash` for:

- `kong-coupang`
- `maple-amazon`
- `loop-affiliate`
- `tori-threads-kr`
- `milo-threads-us`
- `mungchi-proof`
- `pip-publisher`
- HeightCue's internal generation and viral-critic calls

Deterministic metrics collection remains no-agent/script-first. Browser work uses `aside --account u0` only.

## Content tournament

For each eligible product:

1. Generate six hooks across configured hook families.
2. Blind-score all hooks in a separate Gemini call without the generator rationale.
3. Expand the top two into full drafts.
4. Blind-score drafts on scroll-stop strength, natural parent language, contrast specificity, brevity, fair point, and explicit skip-if.
5. Send the winner through deterministic post checks and Gemini evidence/compliance review.
6. Publish only the final passing candidate.

Writer prompts receive verified facts and prohibited claims but not verbose policy prose. Compliance never becomes the content angle.

## Metrics contract

Every publication record must include:

- `hook_family`
- `angle_id`
- `product_id`
- `formfactor_id`
- `ux_grade`
- `country`
- `post_type`
- `writer_variant`
- `views_24h`, `views_72h`
- `likes`, `replies`, `reposts`, `saves`
- `link_clicks`, `conversions`, `commission`

Dry runs and compliance replacements do not enter learning samples.

## Learning guardrails

Gemini may propose and automatically promote a playbook rule only when:

- each compared arm meets the configured minimum sample;
- attribution data exists;
- the candidate beats the baseline on normalized performance;
- all viral-golden and compliance regressions pass.

Only `playbook.md` is automatically changed. Persona, SSOT, compliance policy, source evidence, code, and base prompts are immutable to the learning loop. Every promotion stores the prior playbook and supports automatic rollback.

## Verification

Completion requires:

- config and cron readback showing Gemini 3.7 Flash on all intended profiles/jobs;
- unit tests for sourcing lane fallback, candidate comparison contract, tournament ranking, metrics attribution, promotion gating, and rollback;
- full existing test suite passing;
- one real Gemini smoke call;
- one real Aside sourcing rehearsal producing an auditable queue result;
- one KR and one US generation rehearsal with no publish side effect;
- readback of generated artifacts and state.

## Explicit non-goals

- No autonomous editing of source code or compliance rules.
- No speculative multi-armed-bandit infrastructure before attribution data exists.
- No live publishing during verification.
- No replacement of Aside with built-in browser tools.
