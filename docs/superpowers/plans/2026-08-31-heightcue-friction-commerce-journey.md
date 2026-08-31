# Implementation Plan: HeightCue Friction-Commerce User Journey

## Reference
- **Spec:** `docs/superpowers/specs/2026-08-31-heightcue-friction-commerce-journey-design.md`

## Phase 1: Canonical Contracts and Drift Detection
1. **Add a canonical operating-policy module with regression tests**
   - **Action:** Define active positioning, retired persona tokens, public category groups/mappings, product blacklist, caregiver-shaming patterns, cadence defaults, exact disclosures, and journey metadata. Add must-catch/must-pass corpora before implementation.
   - **Files:** `autopilot/test_journey_policy.py`, `autopilot/journey_policy.py`
   - **Commands:**
     - `../.venv/bin/python test_journey_policy.py` (RED, then GREEN)
   - **Validation:** Natural KR/US shaming is rejected, precise product/claim criticism passes, stadiometers and retired persona tokens are inactive.

2. **Wire policy into sourcing and output checks**
   - **Action:** Replace duplicated category/blacklist/disclosure strings in touched paths with imported policy. Scan all reader-visible post fields for caregiver shaming and retired persona use. Preserve existing stage and evidence gates.
   - **Files:** `autopilot/test_sourcing.py`, `autopilot/test_persona_free_runtime.py`, `autopilot/test_posts.json`, `autopilot/sourcing.py`, `autopilot/post_check.py`, `autopilot/generation_worker.py`
   - **Commands:**
     - focused RED/GREEN commands for each changed test
     - `../.venv/bin/python post_check.py test_posts.json --test`
   - **Validation:** Production checker catches known bad KR/US outputs and passes known-good friction copy.

3. **Add document/config drift health checks**
   - **Action:** Detect retired persona, legacy ten-post cadence, measurement-commerce language, and obsolete category locks in active operational docs/config while excluding archives/history.
   - **Files:** `autopilot/test_health.py`, `autopilot/health.py`
   - **Commands:** `../.venv/bin/python test_health.py`
   - **Validation:** A fixture containing a stale active contract fails; current reconciled files pass.

## Phase 2: Coherent Static Site Journey
4. **Create site journey renderer tests**
   - **Action:** Specify KR/US hubs, five category pages, honest empty states, approved product cards, archival measurement page, disclosure placement, locale switching, canonical links, and Amazon static-price prohibition.
   - **Files:** `autopilot/test_site_journey.py`, `autopilot/site_journey.py`
   - **Commands:** `../.venv/bin/python test_site_journey.py` (RED, then GREEN)
   - **Validation:** Temp builds contain every route and fail when stale persona/stadiometer affiliate/static Amazon pricing appears.

5. **Integrate workflow packets and manifests**
   - **Action:** Render from active approved workflow data only; emit binding manifest with product, offer, revision, tracking, paths, and digests. Preserve current product-specific generator interfaces where compatible.
   - **Files:** `autopilot/companyos.py`, `autopilot/sitegen.py`, `autopilot/sitegen_lt.py`, `autopilot/site_journey.py`, corresponding tests
   - **Commands:** focused tests plus `../.venv/bin/python test_queue.py`
   - **Validation:** Unapproved or incomplete packets cannot render a CTA; active packets retain exact bindings.

6. **Generate and locally validate public files**
   - **Action:** Replace stale root/KR/US public surfaces, remove measurement affiliate links and US fixed prices/reviews, update disclosure/sitemap/robots where needed, and create category/archival routes.
   - **Files:** `index.html`, `kr/index.html`, `us/index.html`, `kr/c/*.html`, `us/c/*.html`, shared CSS/assets, `sitemap.xml`, `disclosure.html`
   - **Commands:** site build; static validator; link checker; source scans
   - **Validation:** Zero dead internal links; zero retired tokens on active routes; valid empty-state journeys.

7. **Perform mobile and desktop visual QA**
   - **Action:** Serve the generated site locally and inspect at 390×844 and desktop through Aside CLI only. Check hierarchy, wrapping, disclosures, CTA order, language switch, empty states, and no dead ends.
   - **Files:** fixes limited to site renderer/styles/tests
   - **Commands:** local server plus `aside --account u0 exec/repl`
   - **Validation:** Screenshot/read-back evidence confirms no clipping, overlap, awkward copy, or broken journey.

## Phase 3: Attributable Outreach
8. **Add outreach ledger and deterministic candidate gate**
   - **Action:** Define source, friction, safety, status, idempotency, publication, and read-back records. Reject fake/missing URLs, promotional CTAs, medical context, generic replies, duplicates, and minor-identifying data.
   - **Files:** `autopilot/test_outreach.py`, `autopilot/outreach.py`, state schema/example
   - **Commands:** `../.venv/bin/python test_outreach.py` (vertical RED/GREEN cycles)
   - **Validation:** Retry cannot double-reserve or double-publish; unsafe candidates fail before browser invocation.

9. **Implement Aside-only discovery and publish adapter**
   - **Action:** Invoke `aside --account u0` per market, parse hostile stdout with shape validation, capture real source URLs/text, publish only eligible replies, and read back exact reply IDs. Provide rehearsal mode that cannot publish.
   - **Files:** `autopilot/outreach.py`, `autopilot/test_outreach.py`, `reply-outreach.md`
   - **Commands:** parser/gate tests; real read-only discovery rehearsal; controlled posting only after all gates pass
   - **Validation:** Rehearsal returns real attributable candidates with `published:false`; live run records exact provider read-back.

10. **Integrate outreach health, cadence, and scheduler**
    - **Action:** Add run command, lock, quiet no-op behavior, health status, and cron entries. Change original cadence to two per market daily without disturbing comments/evidence schedules.
    - **Files:** `autopilot/run.py`, `autopilot/test_ops.py`, `autopilot/test_health.py`, `autopilot/health.py`, `crontab.txt`, `config.example.json`
    - **Commands:** focused tests; `crontab` diff validation; register with `crontab crontab.txt`; `crontab -l`
    - **Validation:** Runtime and registered cron agree; outreach cannot overlap; no silent missing executable/PATH failure.

## Phase 4: Journey Analytics
11. **Add guarded normalized metrics and outreach attribution**
    - **Action:** Preserve revenue hierarchy and null semantics; add reply records, profile/follower observations where supplied, landing progression, returns/cancellations, and guarded commission per 1,000 observed impressions.
    - **Files:** `autopilot/test_analytics.py` or existing analytics test file, `autopilot/analytics.py`
    - **Commands:** focused RED/GREEN tests
    - **Validation:** Missing impressions/commission remain null; normalized metric appears only above configured evidence; views cannot beat verified revenue.

12. **Add metric-specific experiment decisions**
    - **Action:** Replace universal sample assumptions with per-experiment decision contracts and `insufficient/continue/expand/modify/stop` states.
    - **Files:** analytics/improvement tests, `autopilot/analytics.py`, `autopilot/improve.py`
    - **Commands:** focused tests
    - **Validation:** Revenue experiment with views but no attribution remains insufficient; compliance failure stops immediately.

## Phase 5: Reconcile Documentation and Live State
13. **Reconcile active docs and generation instructions**
    - **Action:** Update file map, launch status, sourcing routine, outreach contract, generation skill, and user-intent references to canonical policy. Do not edit archives/logs/history.
    - **Files:** `AGENTS.md`, `LAUNCH-STATUS.md`, `aside-sourcing-routine.md`, `reply-outreach.md`, `heightcue-gemini-skills.md`, `context/user-intent-contract.md`
    - **Commands:** drift health check; exact source scans
    - **Validation:** No active contradiction on persona, categories, cadence, site, or outreach.

14. **Deploy and verify public site**
    - **Action:** Commit only owned paths, push GitHub Pages source, wait for deployment through bounded checks, and read back KR/US hubs/category/product/archive routes with Aside.
    - **Files:** generated public files and source renderer
    - **Commands:** git commit/push; Aside live inspection
    - **Validation:** Live exact text/links match manifest; no stadiometer affiliate links or static Amazon price/rating/review data.

15. **Verify and repair Threads profile journey**
    - **Action:** Read back KR/US display names, bios, and profile links; update only mismatches through Aside; re-read exact targets.
    - **Files:** none unless operational record is updated
    - **Commands:** Aside profile inspection/update/read-back
    - **Validation:** Profiles match canonical positioning and lead to their locale hubs.

16. **Run real outreach rehearsal and first bounded live execution**
    - **Action:** Discover real conversations, validate candidates, execute the configured daily batch, read back every reply, and confirm no links/promotional language/duplicates.
    - **Files:** append-only outreach ledger and operational reports
    - **Commands:** `run.py outreach rehearsal`; `run.py outreach`; health/read-back
    - **Validation:** Every live reply has a real source and verified remote ID; no unsafe or unverifiable record is marked published.

## Phase 6: Full Verification and Closeout
17. **Run full local verification**
    - **Commands:**
      - `../.venv/bin/python health.py`
      - `../.venv/bin/python test_health.py`
      - `../.venv/bin/python validate.py`
      - `../.venv/bin/python test_ops.py`
      - `../.venv/bin/python test_queue.py`
      - `../.venv/bin/python post_check.py test_posts.json --test`
      - `../.venv/bin/python test_comments.py`
      - all new focused suites and remaining repository tests
      - `python3 ~/.hermes/scripts/check_rename_integrity.py`
    - **Validation:** Fresh zero-failure outputs after the final change.

18. **Verify external and production state**
    - **Action:** Read back live website, profiles, registered crontab, publication/reply ledgers, health, and fresh analytics. Assert no test residue.
    - **Validation:** External state and local manifests agree; all acceptance criteria are evidenced.

19. **Commit in path-scoped units**
    - **Action:** Use `git add <exact paths>` only. Never stash/reset/add-all. Preserve concurrent edits and report any shared-file commits explicitly.
    - **Validation:** `git status --short` shows unrelated working changes preserved.

## Definition of Done
- All 14 acceptance criteria in the design spec are verified.
- The live KR and US journey is coherent from Threads discovery/profile through locale hub, category, product verdict, retailer CTA, and attribution.
- Active surfaces contain no retired persona or measurement-commerce contradiction.
- Amazon policy-sensitive dynamic data is absent unless supplied through an approved mechanism.
- Outreach is real, safe, idempotent, attributable, scheduled, and externally read back.
- Runtime and registered cadence match the approved distribution-led operating model.
- Revenue hierarchy, null semantics, and normalized diagnostics behave as specified.
- Full tests, health, validation, visual QA, live read-back, profile read-back, and scheduler checks pass after the final change.
- No test artifacts or unrelated concurrent changes are committed.
