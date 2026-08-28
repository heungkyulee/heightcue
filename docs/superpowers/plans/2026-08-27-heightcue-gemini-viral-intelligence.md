# HeightCue Gemini Viral Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Gemini 3.7 Flash the verified sourcing, content, review, and learning judgment engine for HeightCue.

**Architecture:** Aside CLI performs authenticated browser I/O, Gemini ranks and writes, deterministic Python gates validate evidence/compliance/attribution, and a guarded playbook learner promotes only proven tactics with rollback.

**Tech Stack:** Python 3, pytest, OpenRouter Gemini 3.7 Flash, Hermes profiles/cron, Aside CLI.

**Spec:** `docs/superpowers/specs/2026-08-27-heightcue-gemini-viral-intelligence-design.md`

## Global Constraints

- Browser work uses `aside --account u0` only.
- Judgment model is `openrouter/google/gemini-3.7-flash`.
- No live publishing during verification.
- Learning may update only `playbook.md`; code, SSOT, base prompts, and compliance rules remain immutable.
- Every production behavior change follows RED-GREEN-REFACTOR.

---

### Task 1: Conversation intent and model contract

**Files:**
- Create: `context/user-intent-contract.md`
- Create: `autopilot/state/viral-goldens.json`
- Modify: `autopilot/config.json`
- Test: `autopilot/test_viral_intelligence.py`

**Interfaces:**
- Produces a stable model id and regression corpus consumed by generation and learning.

- [ ] Write failing tests asserting the configured provider/model and required golden categories.
- [ ] Run the targeted tests and confirm they fail.
- [ ] Add the intent contract, goldens, and Gemini 3.7 Flash config.
- [ ] Run the targeted tests and full suite.

### Task 2: Dual-lane sourcing contract

**Files:**
- Modify: `autopilot/sourcing.py`
- Modify: `aside-sourcing-routine.md`
- Test: `autopilot/test_viral_intelligence.py`

**Interfaces:**
- Produces `build_sourcing_requests(...): list[dict]` requests with `lane` and candidate-comparison requirements.

- [ ] Write a failing test proving discovery continues when demand signals are empty.
- [ ] Run it and confirm the expected failure.
- [ ] Implement demand/discovery lane requests and five-candidate/three-comparison/one-winner validation.
- [ ] Run targeted and full tests.

### Task 3: Gemini hook and draft tournament

**Files:**
- Create: `autopilot/viral_intelligence.py`
- Modify: `autopilot/generate.py`
- Test: `autopilot/test_viral_intelligence.py`

**Interfaces:**
- Produces `select_hook_candidates`, `select_draft_winner`, and generation metadata fields.

- [ ] Write failing tests for six-hook validation, blind scoring, top-two expansion, and winner metadata.
- [ ] Run them and confirm expected failures.
- [ ] Implement minimal deterministic ranking/parsing plus two separate Gemini prompts.
- [ ] Run targeted and full tests.

### Task 4: Metrics attribution and guarded learning

**Files:**
- Modify: `autopilot/metrics.py`
- Modify: `autopilot/improve.py`
- Test: `autopilot/test_viral_intelligence.py`

**Interfaces:**
- Produces attributed publication rows and `promote_playbook_candidate(...)` with snapshot/rollback.

- [ ] Write failing tests for required attribution fields, no-promotion without clicks/conversions, successful promotion, and rollback.
- [ ] Run them and confirm expected failures.
- [ ] Implement the minimal metadata and promotion guard.
- [ ] Run targeted and full tests.

### Task 5: Hermes fleet and cron model migration

**Files:**
- Modify through supported `hermes -p <profile> config set` and `hermes -p <profile> cron edit` commands only.

**Interfaces:**
- Produces profile and enabled-cron readback pinned to OpenRouter Gemini 3.7 Flash.

- [ ] Inventory `kong-coupang`, `maple-amazon`, and `loop-affiliate` configs/jobs.
- [ ] Set provider/model through Hermes CLI.
- [ ] Pin enabled agent cron jobs to the same provider/model; leave no-agent jobs model-free.
- [ ] Run config checks and direct `MODEL_OK` smoke calls.

### Task 6: End-to-end rehearsal and verification

**Files:**
- Runtime state only under `autopilot/state/`; no live publish.

**Interfaces:**
- Consumes all prior components and produces auditable rehearsal artifacts.

- [ ] Run the full pytest suite.
- [ ] Run validation and dry-run KR/US generation.
- [ ] Use Aside CLI account `u0` for one sourcing rehearsal.
- [ ] Read back queue results and confirm candidate/rejection/winner requirements.
- [ ] Run one guarded improve cycle and verify no unsupported promotion.
- [ ] Summarize exact verified outputs and remaining external blockers.
