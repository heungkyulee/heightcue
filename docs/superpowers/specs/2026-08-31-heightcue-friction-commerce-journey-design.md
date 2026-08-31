# HeightCue Friction-Commerce User Journey Design

## Status
Approved by the operator on 2026-08-31 for end-to-end implementation without further approval prompts.

## Objective
Make HeightCue a coherent, persona-free parenting-friction commerce system in which every public surface, content stage, sourcing decision, landing page, outreach reply, attribution record, and report follows the same contract. A caregiver must be able to move from a recognizable household problem to a trustworthy product verdict and retailer click without stale positioning, dead ends, unsupported claims, missing disclosures, misleading price data, or unexplained changes in voice.

## Non-negotiables
1. `heightcue-SSOT-v2.md` (current v3 content) remains the strategy SSOT: no narrator biography or replacement demographic persona.
2. Historical posts and logs remain unchanged. They are excluded from active prompt assembly and analytics when marked legacy; they are not rewritten as if the new strategy existed earlier.
3. Verified commission and orders remain the north star. Commission per 1,000 verified impressions is a diagnostic efficiency metric, not a replacement objective.
4. No product reaches a verdict or landing without demand provenance, evidence completeness, approval, active offer, exact tracking binding, and live read-back.
5. No browser-dependent operation uses anything except Aside CLI account `u0`.
6. Affiliate, medical, evidence, publication, idempotency, and product-fidelity gates remain fail-closed.

## Problem
The internal strategy and current Threads profiles have moved to persona-free parenting friction, but public homepages and several operational documents still expose the retired 167 cm identity and stadiometer catalog. KR and US landing journeys disagree with the active content generator. US pages display manually fixed Amazon prices and review counts. Outreach exists only as a short playbook and is not an executable, attributable distribution loop. Analytics ranks revenue correctly but does not expose a complete user-journey funnel or a guarded commission-per-impression diagnostic. The resulting system can publish valid individual posts while the caregiver journey remains incoherent.

## Decision
Implement a journey-consistency release rather than another rebrand. Keep the current friction-led three-stage architecture and extend it through public site generation, outreach, and analytics. Retire stale measurement-commerce surfaces. Consolidate category and persona rules into imported constants and generated documentation markers so runtime, validators, and operator docs cannot drift silently.

## Audience and Category Boundary
HeightCue serves caregivers of children. It does not become a generic household-gadget account.

Public navigation uses five caregiver-readable problem groups:
1. Sleep & Morning / 잠과 아침
2. Meals & Lunchboxes / 식사와 도시락
3. Play & Movement / 놀이와 움직임
4. Study & Routines / 공부와 루틴
5. Storage & Mess / 정리·수납·어질러짐

Runtime eligibility remains demand-led and mechanism-led, not inventory rotation. Source records may use more granular internal categories, but every active product maps to one public group. Nutrition products remain evidence-gated and never sell through height, hormone, deficiency, or medical-outcome implication. Measurement tools, height charts, stadiometers, posture-correction promises, growth-result products, generic novelty gadgets, and high-risk child-safety products fail closed.

## Canonical Public Positioning
### KR
- Display name: `HeightCue | 생활 마찰 해결`
- Promise: `아이 키우는 집의 반복되는 귀찮음을 찾습니다. 구조적으로 줄여주는 물건만 판정합니다.`
- Affiliate relationship remains conspicuous where a commercial route appears.

### US
- Display name: `HeightCue | Parenting Fixes`
- Promise: `Small fixes for everyday parenting friction. We check the bad reviews first.`
- Account-associated statement: `As an Amazon Associate I earn from qualifying purchases.`

No active page or prompt uses `167cm`, `5'6" Uncle`, `팩트폭격기`, narrator age, fabricated family experience, or childhood anecdotes.

## User Journey
### 1. Discovery
A caregiver encounters an original post or a useful HeightCue reply inside a real parenting conversation. The content names one repeated household scene and its cost in time, mess, noise, space, waste, or arguments. It contains no product, brand, affiliate link, profile instruction, or disguised recommendation.

### 2. Profile
The profile promise matches the discovery content. It does not switch to height anxiety, measurement products, or a narrator biography. The account link opens the locale hub.

### 3. Locale Hub
The hub shows:
- this week's verified verdicts, if any;
- the five problem groups;
- a plain explanation of the selection rule;
- a product-submission route;
- affiliate and medical/evidence disclosure.

Empty categories are honest empty states with a useful return route, never placeholder products. Old measurement URLs return a non-commercial archival explanation and clear navigation to the current hub; affiliate purchase links for stadiometers are removed.

### 4. Category Page
The caregiver sees products only for the selected problem group. Each card names the household friction, mechanism, one verified trade-off, and a skip condition before the commercial CTA. Cards do not show stale retailer prices, ratings, review counts, or availability.

### 5. Product Verdict Page
Required order:
1. recognizable friction;
2. affiliate disclosure;
3. what the mechanism changes;
4. one or two approved facts;
5. repeated bad-review failure mode;
6. why the selected product reduces that failure;
7. explicit skip condition;
8. `Check current price` retailer CTA;
9. related non-commercial content.

The page also states what the product does not solve. US Amazon pages never show price or availability unless supplied through an approved Amazon mechanism and refreshed under policy. Review excerpts are exact stored substrings with provenance.

### 6. Retailer
The route uses the approved offer and tracking identifier returned by the workflow claim. A click is a real user action; no automatic redirect occurs. Link-level disclosure is adjacent to the CTA.

### 7. Learning
Clicks, orders, returns/cancellations, and verified commission flow back to the same `friction_id`, product, offer, evidence revision, landing, content item, and experiment arm. Missing values remain `null`, never zero.

## Content Portfolio and Cadence
Replace the stale ten-original-post daily plan with a distribution-led default:
- KR: two original posts per day and 10–15 relevant external replies;
- US: two original posts per day and 10–15 relevant external replies;
- meaningful replies on owned posts receive responses;
- weekends may add one tested original, but this is an experiment field rather than a permanent rule.

Original allocation is adaptive to validated friction demand and attributable downstream performance. No fixed 45/25/20/10 quota is encoded. Direct commercial CTAs appear only in verdict-stage content. Discovery and bridge stages remain commercially separated.

## Outreach Architecture
Add an Aside-only outreach worker with these stages:
1. search live KR or US parenting conversations using market-specific query packs;
2. capture source URL, author, exact post text, timestamp, and topic;
3. classify relevance and safety;
4. bind or create a friction-led demand signal without inventing facts;
5. generate a specific, link-free reply using only source context and approved generic mechanisms;
6. reject medical, diagnostic, disputed, minor-identifying, context-missing, promotional, or generic-reply candidates;
7. publish through Aside only when all deterministic checks pass;
8. read back the exact reply and record the remote reply ID;
9. collect available reply performance and aggregate profile progression without claiming per-reply causality where the platform does not expose it.

The worker never likes, follows, DMs, posts affiliate links, or tells the reader to visit the profile. It has a durable idempotency key derived from market and source post ID so a retry cannot double-reply.

## Content and Reply Safety
Add deterministic rejection for language that shames caregivers or attacks commerce as a class. Must-catch examples include buyer-directed `lazy`, `호구`, `애 잡다`, `그냥 먹이면`, and generalized seller abuse. Must-pass examples include precise criticism of a claim, product mechanism, hidden condition, or observed failure mode. The gate scans every reader-visible text field and both KR/US natural forms. Existing causal, disclosure, evidence, and stage-separation checks remain in force.

## Canonical Policy Module
Create one importable policy module that owns:
- active positioning strings;
- retired persona tokens;
- public category groups and mappings;
- product blacklist;
- caregiver-shaming patterns;
- cadence defaults;
- exact disclosure strings;
- journey-required metadata fields.

Runtime generators, sourcing, site generation, outreach, health checks, and tests import this module. Documentation contains generated marker blocks or explicit references rather than independent contradictory copies.

## Site Architecture
Use the existing static GitHub Pages deployment and product workflow; do not introduce a new web framework.

Generated surfaces:
- `/` locale-aware neutral gateway or KR default with explicit KR/US switch;
- `/kr/` KR friction hub;
- `/us/` US friction hub;
- `/kr/c/<group>.html`, `/us/c/<group>.html` category pages;
- existing approved product URLs rendered under locale product paths;
- archival measurement explanation at old anchors without affiliate links.

Site generation reads only active, approved, landing-verified workflow packets. A build manifest records product ID, offer ID, evidence revision, tracking key, source path, output path, and content digest. Deployment succeeds only after local static validation and live read-back of positioning, disclosure, product binding, canonical links, and absence of retired tokens/static Amazon prices.

## Profile Links
Prefer one locale-hub link per account because it is stable and fully attributable. If Threads' five-link capability is later proven controllable and measurable in the current web UI/API, it may be tested behind a configuration flag. The standard implementation does not depend on an unverified multi-link workflow.

## Analytics
Keep lexicographic business ranking:
1. verified commission;
2. verified orders;
3. verified retailer clicks;
4. guide/profile progression;
5. qualified engagement;
6. views.

Add:
- impressions with provenance;
- `commission_per_1000_verified_impressions`, emitted only when impressions are observed and the configured minimum sample is met;
- external reply records and their available engagement;
- profile visits and follower deltas where exposed;
- landing progression;
- returns and cancellations where supplied;
- funnel gap diagnostics by `friction_id`.

The normalized efficiency metric never crowns a revenue winner by itself. A high-view item with no downstream evidence remains a reach observation.

## Experiment Semantics
Do not encode a universal 12-post/3,000-view stop rule. Each experiment declares:
- decision metric;
- observation unit;
- minimum evidence for that metric;
- guardrail metrics;
- attribution completeness requirement;
- decision state: `insufficient`, `continue`, `expand`, `modify`, or `stop`.

Compliance failures stop immediately. Revenue experiments cannot conclude from views alone. Missing or unattributed retailer outcomes keep the decision `insufficient`.

## Operational Documents
Update and reconcile:
- `AGENTS.md` file map and invariants;
- `LAUNCH-STATUS.md` current profile, cadence, sourcing, site, and outreach state;
- `reply-outreach.md` executable contract;
- `aside-sourcing-routine.md` category and blacklist references;
- `heightcue-gemini-skills.md` safety language and canonical-policy reference;
- public disclosures and sitemap.

Historical reports, publication logs, archived persona specs, and old metrics are not rewritten.

## Rollout
1. Freeze no process; first add tests and policy contracts.
2. Reconcile runtime category/persona/safety rules.
3. Build and validate new static site locally.
4. Deploy and live-read back the entire KR and US journey.
5. Update profile links if they do not already target the locale hubs; verify through live profile read-back.
6. Install the Aside outreach routine in preview/dry-run mode, verify real source discovery and deterministic rejection, then enable posting with read-back and idempotency.
7. Change original-post cadence only after crontab and runtime configuration agree; verify registered crontab.
8. Collect a fresh daily and weekly report without publishing test artifacts.

## Failure Semantics
- Empty product workflow: render honest empty states; never restore stale measurement products.
- Missing Amazon-compliant dynamic price source: omit price and use `Check current price on Amazon`.
- Site build or read-back mismatch: do not deploy or do not mark deployment verified.
- Aside source discovery failure: emit no replies; do not synthesize targets.
- Reply publish accepted but read-back missing: mark `verification_pending` and block retry on the same idempotency key.
- Analytics missing values: preserve `null` and exclude from normalized calculations.
- Conflicting documentation: health check reports drift as an operational error.

## Verification and Acceptance Criteria
1. No active prompt, profile text, generated homepage, category page, product page, or current operations document requires the retired narrator.
2. KR and US live homepages contain the canonical positioning and no stadiometer affiliate links.
3. US live pages contain no manually fixed Amazon price, availability, rating, or review count.
4. Every active product card and CTA binds to an approved workflow packet and exact tracking route.
5. Every category has a valid journey, including honest empty-state navigation.
6. Mobile 390px and desktop layouts have no clipped copy, overlapping CTA, unreadable disclosure, or dead-end navigation.
7. All internal links, canonical links, language switches, disclosure links, submission route, product routes, and retailer CTA hrefs validate.
8. Caregiver-shaming regression corpus has zero misses and zero false positives.
9. Discovery/bridge content cannot carry product or affiliate routes; verdict content cannot omit disclosure, failure mode, skip condition, or route.
10. Outreach uses only real source URLs, publishes no generic/promotional/medical replies, prevents duplicate replies, and stores exact remote read-back IDs.
11. Registered crontab and runtime cadence match two originals per account per day; health reports the outreach worker separately.
12. Analytics keeps missing values null, retains the revenue hierarchy, and guards commission-per-1,000 by observed impressions and minimum evidence.
13. Focused tests, full suites, health, validation, queue E2E, dry-run, site build, link checker, mobile visual QA, live deployment read-back, and profile read-back all pass after the final change.
14. No test post, dry-run reply, or preview consumption record remains in production state.
