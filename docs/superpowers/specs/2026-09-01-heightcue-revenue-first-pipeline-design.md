# HeightCue Revenue-First Content Pipeline Design

**Date:** 2026-09-01  
**Status:** Approved direction  
**Owner:** LiFoli Corp.

## 1. Problem

HeightCue currently optimizes for rule compliance and output volume. It does not identify the failure the user sees: content that is technically valid but feels artificial, generic, repetitive, and not worth consuming.

The current system can publish AI slop because it lacks both:

1. a consumer-value detector that can say `KILL`, and
2. a remediation path that discards the concept instead of polishing bad copy.

The result is supply without demand: 24 weekly posts, 2,214 views, 7 replies, and 1 new follower. Product clicks, orders, and affiliate commission remain the business outcome.

## 2. Goal

Replace the active HeightCue generation path with a small revenue-first loop that maximizes:

1. affiliate commission,
2. attributed orders and clicks,
3. views and shares,
4. follower conversion.

The primary metric is **affiliate commission per 1,000 post views**. Publication count is not a success metric.

## 3. Non-goals

- Publishing every day
- Covering a balanced set of parenting categories
- Producing standalone research summaries
- Maintaining a fictional creator persona
- Filling every scheduled content slot
- Preserving the current journey-stage, evidence-atom, or AI-tournament architecture
- Claiming guaranteed virality or guaranteed sales

## 4. Operating promise

HeightCue makes one promise:

> 육아템, 살지 말지 빠르게 판정합니다.

Every public post must help a parent make or avoid a purchase. A post that cannot change a purchase decision does not belong on HeightCue.

## 5. Minimal safety floor

Only three non-negotiable rules remain in the active creative path:

1. Show the required affiliate disclosure.
2. Do not invent personal experience, DMs, reviews, testimonials, prices, or product facts.
3. Do not promise medical outcomes or guaranteed results.

These protect revenue, affiliate accounts, and credibility. All other editorial constraints are removed from generation.

## 6. Active architecture

### 6.1 Offer selector

Start from a sellable offer, never from an abstract topic.

Required input:

- active product and market,
- working affiliate destination,
- current price or an explicit `price changes` state,
- stock/availability observation,
- real demand evidence such as review volume, purchase signal, search interest, or repeated customer complaint,
- one concrete reason to buy now or skip.

If no sellable offer is ready, publish nothing.

### 6.2 Demand brief

Build one short brief from observed language:

- target buyer,
- exact frustrating scene or desire,
- product under consideration,
- strongest purchase objection,
- one surprising or useful verdict,
- proof available to show,
- desired action.

The brief must use real source language. It must not include a persona story or educational detour.

### 6.3 Three permitted creative formats

1. **결제 직전 판정**: pain or desire, verdict, proof, buy/skip condition, CTA.
2. **A 대 B 비교**: familiar option, decisive difference, winner, loser-fit, CTA.
3. **마찰 전후**: real recurring scene, product mechanism, visible before/after, who should skip, CTA.

A post uses one format only.

### 6.4 Writer

The writer receives only the offer truth pack, demand brief, selected format, and recent published copy.

It returns:

- three materially different hooks,
- one selected hook,
- one concise post,
- one visual-proof suggestion,
- one purchase CTA.

The writer is not asked to cite studies, balance categories, perform medical education, imitate a personality, or satisfy journey stages.

### 6.5 AI-slop kill gate

An independent editor returns only `PUBLISH` or `KILL` plus reason codes. It does not rewrite.

Kill when any of these are true:

- abstract information without a purchase decision,
- generic research-summary opening,
- no concrete product or scene,
- no clear desire, tension, surprise, or earned opinion,
- synthetic personal story, fake-looking DM, or borrowed authority,
- generic AI rhythm or interchangeable advice,
- same claim, hook, or conclusion as a recent post,
- free advice that removes the product's purchase case,
- no visible reason to follow HeightCue after reading,
- product facts cannot be shown or checked,
- copy sounds like a pipeline artifact rather than a person making a verdict.

A killed candidate is discarded. The system generates a new concept from a different demand signal. After two killed concepts for the same offer, the offer is skipped for that day.

### 6.6 Publisher

- Maximum one original post per market per day.
- Zero posts is valid and preferred over slop.
- No batch publication.
- No generic automated outreach replies during the initial rollout.
- Existing idempotent Threads publish and read-back verification remain in use.

### 6.7 Performance loop

For each published post, record:

- views,
- profile visits when available,
- follows,
- replies and shares,
- affiliate-link clicks,
- ordered items,
- commission,
- creative format, hook, product, and demand signal.

The weekly learner may only promote patterns supported by observed performance. It must retire losing product-angle-hook combinations instead of rewriting them indefinitely.

Primary ranking:

1. commission per 1,000 views,
2. orders per 1,000 views,
3. affiliate CTR,
4. follows per 1,000 views,
5. shares/replies per 1,000 views.

## 7. Control plane

Company OS is the single control point.

- `paused` blocks every publish and reply entry point before generation.
- Hermes jobs must not maintain an independent enabled state that can override Company OS.
- Only one publisher schedule per market may exist.
- Research, sourcing, and metric read-back may continue while publication is paused.

## 8. Reused components

Keep:

- product and affiliate-offer records,
- real product facts and source pointers,
- affiliate-link generation,
- `publish.py` idempotency and read-back verification,
- analytics and revenue read-back,
- state ledgers required for attribution.

Remove from the active path:

- five-category journey taxonomy as a publication requirement,
- discovery/bridge stages,
- standalone evidence-atom content,
- story bank and creator-persona generation,
- same-model candidate tournament,
- fixed multi-slot publishing,
- generic mechanism-based outreach replies,
- publication targets that reward volume.

Old modules may remain temporarily for rollback, but no active entry point may call them.

## 9. Migration

### Phase 0: Stop

Pause all HeightCue publishing, reply, and video-publishing jobs. Keep sourcing and metric read-back active.

### Phase 1: Dry-run

Generate ten private specimens across KR and US from real sellable offers. No public posting.

Acceptance per specimen:

- names a real product,
- makes one purchase verdict,
- contains one concrete scene or desire,
- uses checkable proof,
- contains a clear buy/skip condition,
- contains no synthetic persona or generic research summary,
- passes the independent slop gate.

### Phase 2: Controlled launch

Enable one original post per market per day. Keep automated outreach replies off.

### Phase 3: Learn

After enough observed views and affiliate events, retain winning formats and retire losing combinations. Do not add new formats until one of the three current formats produces repeatable clicks or orders.

### Phase 4: Public inventory cleanup

Produce an itemized list of existing AI-slop posts with `keep`, `archive/delete`, and rationale. Deletion requires explicit approval.

## 10. Failure handling

- Missing sellable offer: publish nothing.
- Missing real demand evidence: publish nothing.
- Writer failure: retry once with a different hook family.
- Slop-gate failure: discard, do not patch.
- Two concept kills for one offer: skip offer for the day.
- Publish uncertainty after API submission: keep the existing verification-pending behavior and never repost blindly.
- Missing performance data: do not infer success.

## 11. Tests

The implementation must prove:

1. Company OS pause blocks every publish and reply entry point.
2. A candidate cannot exist without a sellable offer and demand brief.
3. Generic research summaries, fake DMs, synthetic persona stories, product-free advice, and near-duplicates return `KILL`.
4. A valid candidate contains a product verdict, proof, buy/skip condition, and CTA.
5. Affiliate posts retain the required disclosure.
6. The market-level daily cap is one original post.
7. Two killed concepts skip the offer without publishing.
8. Existing publish idempotency and read-back tests still pass.
9. A dry run creates ten specimens and zero external posts.

## 12. Success criteria

The replacement is ready for controlled launch only when:

- all publication jobs remain paused,
- ten real-offer specimens exist,
- every specimen passes the slop gate,
- no specimen uses a fake persona, fake DM, generic research summary, or product-free advice,
- the full relevant test suite passes,
- browser verification confirms zero accidental public posts during rehearsal,
- the user approves the specimen set before any scheduler is resumed.
