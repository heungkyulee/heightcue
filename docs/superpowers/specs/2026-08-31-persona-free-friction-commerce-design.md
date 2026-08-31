# HeightCue Persona-Free Friction Commerce Redesign

**Status:** Proposed design approved in chat; awaiting written-spec review  
**Date:** 2026-08-31  
**Business objective:** Maximize attributable affiliate revenue from low-consideration products. Brand recall and creator recognition are not goals.

## 1. Decision

HeightCue will remove the fixed `167cm에서 성장이 끝난 26살 남성` / `The 5'6" Uncle` persona from all active generation, profile, sourcing, analysis, and publishing logic.

The account will not replace that person with another fictional or demographic persona. It will operate as a persona-free commerce editor whose only stable identity is an observable selection rule:

> Find recurring friction in homes with growing children and show the lowest-friction, low-consideration product that removes it.

The previous personal story may remain only in historical posts and archives. It is not an active content source, hook, authority claim, or product-selling mechanism.

This design supersedes the identity-dependent parts of:

- `heightcue-SSOT-v2.md`
- `context/persona.md`
- `story-bank.md` as an active generation input
- `docs/superpowers/specs/2026-08-27-heightcue-raw-identity-design.md`
- Persona-dependent sections of `heightcue-gemini-skills.md`
- KR `167cm` and US `5'6" Uncle` profile positioning

It does not weaken evidence, disclosure, product-fidelity, medical-claim, publication-readback, or revenue-attribution gates.

## 2. Scope

### Included market

The active product universe expands from narrowly defined height-adjacent nutrition, sleep, exercise, and posture products to:

> Low-consideration products that remove recurring friction in households with children.

Eligible friction domains include:

- mess and cleaning
- storage and space
- schoolwork and desk ergonomics
- meal preparation and feeding logistics
- sleep environment
- bathing and dressing
- travel and outings
- safe participation in household tasks
- noise and indoor play
- routine reminders and organization
- age-appropriate nutrition convenience, subject to the existing stricter evidence and compliance gates

The audience remains parents and caregivers. The account is not widened to general household gadgets in this cycle because that would discard the existing audience signal and make product selection too broad.

### Low-consideration definition

A product is eligible only if the sourcing packet can demonstrate all of the following:

1. The problem is understood in one mobile screen without medical education.
2. The product's mechanism is visually or verbally obvious.
3. The purchase does not normally require professional advice, fit consultation, or extensive comparison.
4. The downside of a wrong purchase is limited and reversible.
5. The item can be sold through convenience, time, mess, noise, space, durability, or argument reduction rather than health-outcome promises.
6. The product has enough observed review evidence to identify both a repeated friction and repeated failure modes.

Price is a market-specific scoring input, not a single hard threshold. KR and US sourcing must learn conversion by price band from attributable sales instead of assuming one universal impulse-buy ceiling.

### Explicit exclusions

- Products whose primary promise is height increase or accelerated growth
- Products requiring diagnosis or professional fitting
- High-risk child safety products where a bad recommendation can cause serious injury
- Products sold primarily through unapproved health, treatment, hormonal, developmental, or deficiency claims
- Products whose value cannot be explained without creator testimony
- Generic novelty gadgets with no repeated household friction evidence
- High-consideration products requiring long comparison journeys in this cycle

## 3. Positioning and Voice

### No narrator biography

Generated content must not mention or imply:

- 167cm, 5'6", or stopped growing
- the operator's age or gender
- basketball, taekwondo, dance, busking, locker-room, dating, or childhood-height memories
- being an uncle, parent, caregiver, expert, or product user unless factually established for that exact claim
- a fictional child, family member, DM, ownership period, or product experience

First-person language is permitted only for verifiable editorial actions recorded in the execution packet, such as:

- `리뷰를 확인했습니다`
- `5개를 비교했습니다`
- `라벨에서 확인했습니다`

It must not create a creator-centered narrative.

### Stable editorial character

The account retains a strong voice without a person:

- short, dry, and specific
- starts from a recognizable scene, not a category name
- identifies the physical or procedural source of friction
- shows the product mechanism rather than praising the product
- states a concrete skip condition
- checks bad reviews and rejection reasons before positive claims
- uses no lesson, inspiration, warm summary, or generic engagement bait
- optimizes for purchase clarity, not brand lore

### Working positioning

KR:

> 아이 키우는 집에서 매일 참던 불편을, 가장 단순한 물건으로 줄입니다.

US:

> Small products that remove recurring parenting friction.

Final public profile copy must include the market's required affiliate disclosure and must be verified after publication.

## 4. Content Architecture

The old identity-led `value post versus sales post` split is replaced by a friction-led three-stage system. The legal separation between non-commercial information and affiliate recommendations remains intact.

### Stage A — Friction discovery

Purpose: Reach people who recognize the scene.

Rules:

- No product, brand, affiliate link, or disguised recommendation
- Lead with one repeated household moment
- Name the cost in time, mess, noise, space, waste, or arguments
- Do not force a solution or moral
- Store the validated friction as a demand signal

Example shape:

> 책 읽을 때마다 “고개 들어”라고 말하고 있다면  
> 아이 의지보다 책이 놓인 높이부터 볼 일입니다.

### Stage B — Mechanism bridge

Purpose: Teach what kind of physical or procedural change removes the friction.

Rules:

- May discuss a generic form factor but not a specific commercial product
- Explain the mechanism in one screen
- Produce or strengthen a demand signal for sourcing
- No affiliate link
- No direct thread coupling to a later ad

Example shape:

> 비싼 의자보다 먼저 볼 건 하나입니다.  
> 책이 눈높이까지 올라오는 구조인지.

### Stage C — Product verdict

Purpose: Convert existing demand into an attributable purchase.

Required sequence:

1. Friction-first micro-hook
2. Required market disclosure in the legally approved location
3. Physical/procedural mechanism
4. One or two verified selection facts
5. Repeated bad-review failure mode and why the winner avoids or reduces it
6. Explicit `skip if` condition
7. Attributable link or approved landing route

No creator story or identity sentence is allowed.

### Topic coherence

Friction discovery, mechanism bridge, and product verdict must share a `friction_id`, but non-commercial posts must not directly link or reply-chain into an affiliate post. This preserves legal separation while allowing analytics to evaluate whether coherent topic clusters improve profile visits, clicks, and sales.

## 5. Sourcing and Ranking

### Friction-first sourcing

Product sourcing may begin only from one of these inputs:

- validated internal engagement signal
- observed parent/caregiver complaint from an attributable external source
- repeated marketplace review friction
- a measured conversion pattern from prior HeightCue products

Category inventory rotation alone is not demand.

### Candidate score

Every candidate receives separately recorded scores for:

- `friction_frequency`
- `friction_intensity`
- `mechanism_clarity`
- `mobile_demo_clarity`
- `consideration_cost`
- `price_resistance`
- `review_evidence_strength`
- `failure_mode_severity`
- `compliance_cost`
- `expected_commission_value`
- `attribution_readiness`

The system must not collapse these into an uninspectable LLM opinion. The final score and every component must be stored with source pointers.

### Ranking objective

The highest-level optimization target is verified commission revenue. Before enough conversions exist, the learning hierarchy is:

1. verified orders and commission
2. verified outbound affiliate clicks
3. profile or guide-page progression tied to a `friction_id`
4. qualified engagement on the matching friction
5. views

Views alone may identify reach patterns but cannot promote a product or content pattern as a revenue winner.

## 6. Automation Changes

### SSOT and contexts

- Rewrite the brand/persona sections of `heightcue-SSOT-v2.md` around persona-free friction commerce.
- Replace `context/persona.md` with an editorial operating context or rename it while maintaining a temporary compatibility loader.
- Remove `story-bank.md` from active prompt assembly. Preserve the file as an archive.
- Update KR and US voice files to ban narrator biography and creator-centered hooks.
- Update `AGENTS.md` so future agents do not restore the retired persona from older documents.

### Generation

- Remove all mandatory `167 참견`, `5'6"`, childhood-memory, and persona-story fields.
- Replace identity-based angles such as `raw_memory` with friction-native angles such as `scene`, `mechanism`, `bad_review`, `before_after`, and `price_math`.
- Require a `friction_id` and stage for every content candidate.
- Blind critics receive only the publishable text plus hard stage requirements; they must not see angle labels or generator rationale.
- Candidate scoring prioritizes scene recognition, mechanism clarity, purchase clarity, and evidence boundaries.

### Evidence and demand

- Keep the evidence ledger for factual claims.
- Add or extend a friction ledger that records source, verbatim complaint, market, recurrence, intensity, associated form factors, and lifecycle.
- Scientific information may support a friction claim but must not be required for ordinary household-product discovery.
- Existing insight atoms that depend on the retired identity remain historical; they are not deleted.

### Analytics and account memory

- Remove narrator-story cadence and persona-hook cooldown logic.
- Track performance by `friction_id`, stage, mechanism, product, price band, hook family, format, and affiliate destination.
- The account-memory packet should direct topic rotation and funnel bottlenecks, not invent a daily personality.
- Daily reports must distinguish observed metrics from strategic hypotheses.

### Profiles and public surfaces

- Replace KR and US display names, bios, pinned introductions, and site copy that foreground the retired persona.
- Do not delete historical posts by default. Unpin identity-led introductions and publish/read back the new profile state.
- Any deletion requires separate explicit scope because it is not necessary for the repositioning.

### Bot fleet

All HeightCue bots and routines must receive the same contract:

- no fixed narrator persona
- no biography generation
- friction-first demand and sourcing
- low-consideration eligibility
- revenue hierarchy
- observed-value discipline
- preserve existing disclosure and compliance rules

Prompts, routine payloads, project policy, and role-specific contexts must be audited for stale persona phrases. A static repository/profile scan must fail if active files still contain banned identity tokens outside archives, migration notes, tests, or historical data.

## 7. Migration Strategy

### Preserve history

The redesign is prospective. It does not rewrite historical ledgers, published-post records, evidence, analytics, or old design files.

### Active-versus-archive boundary

The migration must define explicit archive paths. Active prompt assembly, active profile configuration, current SSOT sections, and live routine prompts may not read identity archives.

### Compatibility

If code currently requires a `persona` context key, provide a temporary editorial-context value under the same interface, then remove the compatibility alias after all consumers migrate and tests prove no dependency remains.

### No silent partial migration

The launch gate remains closed if any live generator, profile, cron prompt, bot context, or public bio still requires the retired persona. Historical mentions are allowed only in declared archive or evidence paths.

## 8. Testing and Acceptance Criteria

### Static contract tests

- Active files contain no prohibited persona tokens.
- Archived and historical files are explicitly allowlisted.
- Prompt assembly does not load `story-bank.md`.
- Every generated candidate has `friction_id`, stage, market, and evidence pointers where claims require them.
- Product verdicts contain no narrator biography.

### Content tests

Golden tests must cover KR and US examples for:

- friction discovery with no commercial coupling
- mechanism bridge with no brand or link
- product verdict with disclosure, mechanism, rejection evidence, `skip if`, and attributable route
- refusal of creator testimony and fictional family experience
- ordinary household goods and stricter nutrition cases

### Sourcing tests

- Category rotation without demand cannot create a sourcing request.
- Candidates outside low-consideration and safety boundaries fail closed.
- Component scores and source pointers survive serialization and review.
- A high-view pattern cannot outrank a verified revenue pattern solely on views.

### End-to-end rehearsal

For both KR and US:

1. ingest a real or approved friction signal
2. create a sourcing request
3. return researched candidates
4. independently approve evidence
5. generate all three stages
6. verify commercial separation
7. generate a product verdict with correct disclosure and link route
8. run `publish=false`
9. read back preview artifacts
10. confirm no paid generation or real publication occurred

### Public-state verification

After approved launch:

- read back KR and US profile names and bios
- confirm new pinned state
- confirm affiliate disclosures remain visible
- confirm no unintended post deletion
- verify the first live persona-free publication by Graph API and permalink
- verify attribution identifiers are stored in the publication ledger

### Regression and adversarial review

- Run the full repository suite and targeted persona-removal tests.
- Run prompt-injection and stale-context tests to ensure archived identity text cannot re-enter generation.
- Independently review both compliance and commercial coherence.
- `fail-closed` is not completion if any relevant live path still errors.

## 9. Rollout

1. Freeze new identity-led generation while migration is in progress.
2. Update SSOT, active contexts, prompt assembly, and tests.
3. Update sourcing, friction ledger, ranking, analytics, and bot/routine contracts.
4. Rehearse both markets with `publish=false`.
5. Independently review and correct all failures.
6. Update public profiles and pins, then read them back.
7. Launch a measured persona-free cohort.
8. Compare against the historical baseline by friction stage, clicks, orders, and commission.
9. Do not declare the strategy successful until attributable revenue evidence exists.

## 10. Non-goals

- Building a memorable creator brand
- Replacing the retired persona with a parent, mother, expert, or mascot persona
- Expanding to general household gadgets outside child-household friction in this cycle
- Deleting historical posts or rewriting historical analytics
- Relaxing health, affiliate, evidence, or publication safeguards
- Treating views as revenue

## 11. Completion Definition

The redesign is complete only when:

- all active generation and automation paths are persona-free
- the public KR and US profiles no longer foreground the retired identity
- sourcing is driven by validated friction and low-consideration eligibility
- content stages share measurable friction identity without illegal commercial coupling
- both markets pass real `publish=false` end-to-end rehearsal
- full regression and independent review pass
- the first approved live output is remotely verified and attributable

Commercial success remains a separate outcome and requires verified clicks, orders, and commission after launch.
