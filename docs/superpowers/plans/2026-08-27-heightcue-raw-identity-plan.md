# Implementation Plan: HeightCue Raw Identity & Account Context Overhaul

## Reference
- **Spec:** `docs/superpowers/specs/2026-08-27-heightcue-raw-identity-design.md`

## Phase 1: Brand Lead Profile & ACP Setup
1. **Create Brand Lead Profile**:
   - **Action**: Create the `heightcue-brand-lead` bot profile to manage KR and US channels.
   - **Files**: `~/.hermes/profiles/heightcue-brand-lead/profile.yaml`, `~/.hermes/profiles/heightcue-brand-lead/SOUL.md` (via `hermes profile create` or manual file creation).
   - **Commands**:
     - `mkdir -p ~/.hermes/profiles/heightcue-brand-lead`
     - Create and write YAML/MD files.
   - **Validation**: Verify the profile exists with `hermes profile list` and read the SOUL.md.

2. **Initialize Account Context Packet (ACP)**:
   - **Action**: Create the initial JSON state file for account memory.
   - **Files**: `autopilot/state/account_memory.json`
   - **Commands**: None (just file creation).
   - **Validation**: Ensure `account_memory.json` exists with default structure.

3. **Integrate ACP into Common Context**:
   - **Action**: Modify `recent_context` or create `account_context` in `common.py` to read `account_memory.json` and inject it into the context payload.
   - **Files**: `autopilot/common.py`
   - **Validation**: Run `python -c "from autopilot.common import *; print(load_skill...)"` or similar to verify ACP is accessible.

## Phase 2: Pipeline Simplification & Value Post Diversification
1. **Rewrite Generation Pipeline (`generate.py`)**:
   - **Action**: Remove the heavy `HOOK_CRITIC` and `DRAFT_CRITIC` loops. Implement a simplified flow: generate hooks, pick one, generate *one* raw draft. Implement logic to select one of the 5 V1 angles (rant, shower_thought, raw_memory, myth_bust, community_qa) based on ACP or randomly.
   - **Files**: `autopilot/generate.py`
   - **Validation**: Run existing tests and update them to reflect the simplified pipeline. (e.g., `python -m pytest autopilot/test_viral_intelligence.py`)

2. **Update Post Verification (`post_check.py`)**:
   - **Action**: Modify `check_post` to *only* check for hard compliance (FTC/medical claims) and stop evaluating tone/format (which kills the raw edge).
   - **Files**: `autopilot/post_check.py`, `autopilot/viral_intelligence.py` (if rules are stored there).
   - **Validation**: Run tests to ensure compliance checks still work but tone/format scores are removed or ignored.

## Phase 3: Tone & Manner Enforcement
1. **Update Voice Constraints (KR)**:
   - **Action**: Add hard rules against AI wrap-ups and enforce fragmented sentences.
   - **Files**: `context/voice-kr.md`
   - **Validation**: Manual review of the file.

2. **Update Voice Constraints (US)**:
   - **Action**: Add hard rules against AI wrap-ups and enforce fragmented sentences.
   - **Files**: `context/voice-us.md`
   - **Validation**: Manual review of the file.

3. **Update Compliance/General Rules**:
   - **Action**: Ensure `compliance.md` aligns with the new raw rules.
   - **Files**: `context/compliance.md`
   - **Validation**: Manual review of the file.

## Phase 4: Daily Digest Routine
1. **Implement Brand Lead Digest Script**:
   - **Action**: Create a script (or add to `run.py`) that acts as the Brand Lead's daily routine: read recent posts/comments, output an updated `account_memory.json`.
   - **Files**: `autopilot/run.py` or new `autopilot/digest.py`
   - **Validation**: Run the digest function (dry run) and verify `account_memory.json` is updated correctly.

## Phase 5: Verification & Golive
1. **Full Dry Run**:
   - **Action**: Run the full daily generation pipeline in dry-run mode for both KR and US.
   - **Commands**: `python autopilot/run.py daily --dry-run`
   - **Validation**: Inspect the generated drafts in the logs. Verify they are noticeably more raw, fragmented, and utilize different V1 angles compared to previous outputs. Ensure compliance checks pass.

## Definition of Done
- `heightcue-brand-lead` bot is active.
- `account_memory.json` is created, updated daily by the digest routine, and injected into all prompts.
- Generation pipeline is simplified (no internal critic loops for tone).
- Value posts utilize 5 distinct angles instead of just `story-bank.md`.
- Voice markdown files enforce extreme "raw" constraints and ban AI wrap-ups.
- Dry runs produce output that clearly demonstrates the new raw tone.
- All unit tests pass.