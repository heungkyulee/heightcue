# Implementation Plan: Persona-Free Friction Commerce

## Reference
- **Spec:** `docs/superpowers/specs/2026-08-31-persona-free-friction-commerce-design.md`
- **Workspace ruling:** Execute in the shared working tree because the current uncommitted HeightCue execution state is required and project policy forbids stash/reset. Stage and commit only named paths.

## Global Constraints

- Do not restore or replace the retired narrator with another demographic persona.
- Active generation, profiles, bot contexts, and routines must not use `167cm`, `5'6" Uncle`, childhood-height biography, or story-bank material.
- Historical posts, ledgers, archives, and prior design documents remain unchanged unless explicitly converted into active inputs.
- Friction demand, low-consideration eligibility, source pointers, and revenue hierarchy must be inspectable rather than hidden in an LLM verdict.
- Preserve all affiliate disclosure, medical-claim, evidence, product-truth, product-fidelity, publication-readback, credential, payment, and fail-closed gates.
- No stash, reset, checkout-overwrite, `git add -A`, or deletion of concurrent work.
- New behavior follows RED → GREEN → REFACTOR.
- Browser-dependent operations use Aside CLI account `u0`.
- External writes are read back before success is claimed.

## Phase 1: Active Identity Contract and SSOT

1. **Create persona-free contract regression tests**
   - **Action:** Add tests that enumerate active SSOT/context/generation files, reject retired identity tokens in active sections, assert story-bank is not loaded, and allow declared archive/history paths.
   - **Files:** `autopilot/test_persona_free_contract.py`
   - **Commands:**
     - `../.venv/bin/python -m pytest test_persona_free_contract.py -q`
   - **Validation:** Tests fail for the retired active identity and story-bank prompt assembly.

2. **Rewrite active product and editorial SSOT**
   - **Action:** Replace identity-dependent positioning with persona-free household-friction commerce; document low-consideration eligibility, three stages, friction identity, and revenue hierarchy. Convert `context/persona.md` into an editorial operating contract without changing the loader interface yet. Remove active story-bank references from maps and status docs.
   - **Files:** `heightcue-SSOT-v2.md`, `context/persona.md`, `AGENTS.md`, `LAUNCH-STATUS.md`, `README-autopilot.md`
   - **Commands:**
     - `../.venv/bin/python -m pytest test_persona_free_contract.py -q`
   - **Validation:** Static contract passes and active docs contain no retired narrator requirements.

3. **Rewrite voice and generation skill contracts**
   - **Action:** Remove narrator-biography angles and mandatory identity sentences; define friction discovery, mechanism bridge, and product verdict outputs for KR/US; retain disclosures and compliance boundaries.
   - **Files:** `context/voice-kr.md`, `context/voice-us.md`, `heightcue-gemini-skills.md`, `context/user-intent-contract.md`
   - **Commands:**
     - `../.venv/bin/python -m pytest test_persona_free_contract.py test_posts.json -q` (where applicable; use the repository's actual post test command if JSON is not a pytest target)
     - `../.venv/bin/python post_check.py test_posts.json --test`
   - **Validation:** Static contract and post-check baseline pass.

## Phase 2: Friction Data and Low-Consideration Selection

4. **Add friction ledger behavior with TDD**
   - **Action:** Define friction records, lifecycle, source pointers, recurrence/intensity, markets, mechanisms, and stage linkage. Implement load/append/validate/pick behavior without allowing category-only demand.
   - **Files:** `autopilot/test_friction.py`, `autopilot/friction.py`, `autopilot/state/friction_signals.jsonl` (runtime state, not fabricated fixtures)
   - **Commands:**
     - `../.venv/bin/python -m pytest test_friction.py -q`
   - **Validation:** RED confirms missing behavior; GREEN covers source validation, dedupe, lifecycle, selection, and category-only rejection.

5. **Add inspectable low-consideration candidate scoring with TDD**
   - **Action:** Add component scores and eligibility gates; persist source pointers and score components; rank verified revenue above clicks, clicks above progression, progression above qualified engagement, and views last.
   - **Files:** `autopilot/test_sourcing.py` or focused existing sourcing tests, `autopilot/sourcing.py`
   - **Commands:**
     - `../.venv/bin/python -m pytest test_queue.py test_ops.py test_sourcing.py -q` (use existing files actually present)
   - **Validation:** Category rotation cannot create demand; excluded/high-consideration candidates fail closed; score serialization is inspectable; views cannot outrank verified revenue.

## Phase 3: Persona-Free Generation and Analytics

6. **Remove story-bank and narrator biography from prompt assembly with TDD**
   - **Action:** Write failing prompt-assembly tests, stop loading story-bank into active generation, preserve compatibility key only as persona-free editorial context, and reject retired identity tokens in generated candidates.
   - **Files:** `autopilot/test_generation_context.py`, `autopilot/common.py`, `autopilot/generate.py`, `autopilot/generation_ssot.py`
   - **Commands:**
     - `../.venv/bin/python -m pytest test_generation_context.py test_authoritative_generation.py test_value_tournament.py -q`
   - **Validation:** Active prompts contain editorial rules but no biography or story-bank material.

7. **Implement three-stage friction generation with TDD**
   - **Action:** Require `friction_id`, market, stage, source/evidence pointers as applicable; implement stage-specific commercial separation and verdict requirements; replace raw-memory angles with scene, mechanism, bad-review, before-after, and price-math families.
   - **Files:** focused generation tests, `autopilot/generate.py`, `autopilot/viral_intelligence.py`, `autopilot/run.py`, `autopilot/post_check.py`, `autopilot/test_posts.json`
   - **Commands:**
     - `../.venv/bin/python -m pytest test_value_tournament.py test_viral_intelligence.py test_authoritative_generation.py -q`
     - `../.venv/bin/python post_check.py test_posts.json --test`
   - **Validation:** KR/US golden cases pass; non-commercial stages have no product/link coupling; verdicts include disclosure, mechanism, rejection evidence, skip-if, and attributable route.

8. **Migrate analytics and account memory to friction/revenue dimensions with TDD**
   - **Action:** Remove persona cadence fields from active decision logic; aggregate by friction/stage/mechanism/product/price band/hook/destination; enforce observed-versus-hypothesis labeling and revenue hierarchy.
   - **Files:** `autopilot/test_analytics.py` or focused existing analytics tests, `autopilot/analytics.py`, `autopilot/state/account_memory.json` (generated schema/state migration), reporting code
   - **Commands:**
     - `../.venv/bin/python -m pytest test_analytics.py test_ops.py -q`
   - **Validation:** Reports do not promote view winners as revenue winners and do not prescribe narrator-story rotation.

## Phase 4: Bot Fleet and Routine Migration

9. **Inventory and test active fleet/routine/profile identity references**
   - **Action:** Build a machine-readable audit over current-profile HeightCue bot contexts, cron prompts, policies, and routine payloads; distinguish active from historical content.
   - **Files:** `~/.hermes/scripts/check_heightcue_persona_free.py`, corresponding test fixture/script if appropriate
   - **Commands:**
     - `python3 ~/.hermes/scripts/check_heightcue_persona_free.py`
   - **Validation:** Initial audit fails and prints exact active offenders.

10. **Migrate all HeightCue bots and routines**
    - **Action:** Replace active narrator contracts with friction-first commerce, low-consideration eligibility, revenue hierarchy, and observed-value discipline. Do not edit other profiles unless they are explicitly active HeightCue bot profiles owned by this fleet.
    - **Files:** Current-profile HeightCue bot/routine/policy files identified by the audit; runtime cron jobs via supported Hermes commands when stored outside files.
    - **Commands:**
      - `python3 ~/.hermes/scripts/check_heightcue_persona_free.py`
      - Hermes fleet/routine audit commands defined by project policy
    - **Validation:** Audit reports zero active offenders; all expected HeightCue roles and routines retain their distinct responsibilities.

## Phase 5: Regression and End-to-End Rehearsal

11. **Run targeted and full regression; fix every introduced failure**
    - **Action:** Run all focused tests, post checker, health check, compile/lint for changed files, then the full suite. For each defect, reproduce with a failing test before fixing.
    - **Files:** Only files required by observed failures.
    - **Commands:**
      - `../.venv/bin/python health.py`
      - `../.venv/bin/python -m pytest . -q`
      - `../.venv/bin/python post_check.py test_posts.json --test`
      - `../.venv/bin/python -m compileall .`
      - changed-file lint command used by the repository
    - **Validation:** Fresh output shows zero relevant failures; no unresolved error is labeled complete merely because it fails closed.

12. **Run KR and US publish=false E2E**
    - **Action:** Use real/approved friction signals and authoritative product truth; generate discovery, bridge, and verdict artifacts; verify commercial separation, disclosures, attribution, and no real publication/paid generation.
    - **Files:** Runtime state and evidence artifacts only.
    - **Commands:**
      - Repository-supported `run.py rehearsal`/targeted market commands with `publish=false`
      - Read-back commands for preview and Company OS state
    - **Validation:** Both markets produce persona-free artifacts with `friction_id`; no paid generation and no live post occur; exact outputs are read back.

## Phase 6: Public Migration and Launch

13. **Update public KR/US profile surfaces using Aside CLI**
    - **Action:** Change display names/bios to persona-free positioning with required disclosures; unpin identity-led introductions without deleting posts; verify saved values by reloading and reading the live profiles.
    - **Files:** Public external state; local profile truth/config if present.
    - **Commands:**
      - `aside --account u0 exec "..."`
      - exact Graph/API or Aside read-back commands
    - **Validation:** KR and US live names, bios, disclosures, and pin states match intended values; no post deletion.

14. **Publish and verify the first persona-free cohort**
    - **Action:** Publish the first approved persona-free content through the existing official pipeline, preserving schedule and safety gates. Verify media ID, canonical permalink, remote text, `friction_id`, attribution IDs, and Company OS/publication ledger records.
    - **Files:** Runtime publication ledgers and Company OS external state.
    - **Commands:**
      - Existing approved production command
      - Graph API and publication-ledger read-back
    - **Validation:** Remote publication and exact target state are verified. If no qualifying friction/product package is available, leave launch blocked with concrete evidence rather than fabricating one.

## Phase 7: Review and Finish

15. **Independent adversarial review and final verification**
    - **Action:** Review the full change set against the spec, active-token audit, commercial objective, compliance, and external read-backs. Correct all load-bearing findings and rerun covering tests.
    - **Files:** Review report plus necessary fixes.
    - **Commands:**
      - full fresh verification set from Phase 5
      - active fleet audit
      - git diff and changed-path review
    - **Validation:** Independent review has no unresolved Critical/Important findings; every completion claim has fresh evidence.

16. **Commit only named HeightCue paths and report rulings**
    - **Action:** Preserve concurrent work, stage exact paths, commit coherent changes, and report every implementation ruling and any genuine external blocker.
    - **Commands:**
      - `git status --short`
      - `git diff --check -- <changed paths>`
      - `git add <explicit paths> && git commit ...`
    - **Validation:** Commit contains only intended paths; no stash/reset/add-all occurred.

## Definition of Done

- Active code, prompts, bots, routines, and public profiles are persona-free.
- No replacement demographic persona was introduced.
- Friction demand and low-consideration eligibility are enforced and inspectable.
- The three content stages are measurable by `friction_id` without direct commercial coupling from non-commercial posts.
- Revenue/order/click hierarchy governs strategy; views alone cannot become a revenue verdict.
- KR and US pass real `publish=false` end-to-end rehearsal.
- Public profile changes are remotely read back.
- The first approved live persona-free publication is remotely verified and attributed, or a genuine external blocker is evidenced without fabricated output.
- Full regression, health, static audit, and independent review pass.
- Existing compliance and publication safeguards remain intact.
