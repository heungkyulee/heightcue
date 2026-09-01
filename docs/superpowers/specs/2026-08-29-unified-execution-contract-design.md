# HeightCue Unified Execution Contract — Design

## Problem

Hermes sessions, Bot Chats, agent-backed routines, and script-only routines currently share a project name but not one deterministic content contract. A Bot profile can declare one model and intent while a script-only routine bypasses that profile and reads `autopilot/config.json`, repository prompt files, and state files directly. This makes outputs difficult to predict and provenance difficult to reconstruct.

## Goal

Every HeightCue content operation must consume the same versioned intent contract and emit enough provenance to explain exactly how an output was produced and whether it reached the external system.

The change must preserve the current reliable execution architecture: `run.py` remains the orchestrator, `generate.py` remains the OpenRouter LLM layer, `post_check.py` remains the output gate, and `publish.py` remains the Threads side-effect boundary.

## Non-goals

- Replacing script-only routines with autonomous agents.
- Moving secrets out of the existing ignored `config.json` in this change.
- Rewriting the content prompts, business policy, or publishing schedule.
- Replaying or rewriting historical JSONL records.

## Architecture

### 1. Versioned execution contract

Add `context/execution-contract.json`, a non-secret canonical manifest containing:

- contract ID and schema version
- owner profile and execution mode
- business KPI statement
- canonical intent document path
- prompt source paths
- model source (`autopilot/config.json:openrouter.model`)
- validator and publisher names
- required provenance fields

The JSON manifest is machine-readable. `context/user-intent-contract.md` remains the human-editable policy body.

### 2. Common context builder

Add `autopilot/execution_contract.py` with focused public functions:

- `load_manifest(cfg)` validates the contract manifest.
- `build_context(cfg, task, country, runtime_context=None)` loads the exact intent/prompt sources, computes SHA-256 digests, resolves the active model from runtime config, and returns a JSON-serializable contract packet.
- `generation_provenance(packet, input_ids=None)` returns compact immutable metadata suitable for publication records.
- `merge_provenance(meta, provenance)` adds provenance without allowing callers to overwrite canonical contract fields.

No credentials or full private input bodies are recorded. Only paths, versions, IDs, model names, and hashes are persisted.

### 3. Generation wiring

Every non-dry-run content function receives the same contract packet in its LLM payload:

- sales master and sales drafts
- hook generation and critic calls
- value post and value thread
- comment reply classification

The packet is not a second prose prompt. It is a structured payload entry named `execution_contract`, while the human policy remains in the existing prompt bundle. This avoids duplicating long policy text and makes drift detectable.

Each returned generation object includes compact `_provenance` metadata. Critic calls also receive the contract identity/version, but blind candidate payloads remain blind with respect to angle and hidden generation rationale.

### 4. Publication wiring

`run.py` and `comments.py` pass generated `_provenance` into `publish.publish_text()` metadata. `publish.py` adds side-effect provenance:

- `publish_status`: `dry_run`, `blocked`, `api_published`
- `publisher`: `publish.publish_text`
- `published_media_id` when available

Historical records are not modified.

### 5. Bot ownership semantics

송재현's SOUL must state explicitly:

- recurring text routines are script-owned execution under the Bot's operational ownership
- the Bot profile model does not control those script-only runs
- the repository execution contract is authoritative for generated text
- changing content intent requires changing the shared contract/prompt sources, not only Bot memory

Routine names remain stable to avoid breaking job IDs and delivery routing.

## Error handling

- Missing or malformed manifest: fail closed before an LLM call.
- Missing canonical source file: fail closed with the exact path.
- Missing model in runtime config: fail closed.
- Hashing failure: fail closed; an unexplained prompt bundle is not publishable.
- Caller-provided provenance conflicting with canonical fields: canonical fields win.
- Publication failure: no `api_published` record is written.

## Acceptance criteria

1. A test proves session/Bot descriptions cannot override the runtime content model recorded in the contract packet.
2. Every generation path includes the same contract ID, schema version, active model, task, country, and source hashes.
3. Comment reply publication records include generation provenance and the exact target comment ID.
4. Sales/value/thread publication metadata includes generation provenance.
5. Dry-run and live publication statuses are distinguishable.
6. No secret values or full comment bodies are stored in provenance.
7. Existing content, comment, thread, queue, and operations tests remain green.
8. `health.py`, model validation, post-format regression, and bot rename-integrity checks pass.

## Verification

- New focused unit tests for manifest validation, deterministic hashing, model resolution, provenance merge, and secret exclusion.
- Existing `test_comments.py`, `test_value_tournament.py`, `test_thread.py`, `test_ops.py`, `test_queue.py`, and `post_check.py test_posts.json --test`.
- `health.py --json` for current operational truth.
- `validate.py` for active OpenRouter model reachability.
- `python3 ~/.hermes/scripts/check_rename_integrity.py` for profile/routine references.
- Independent diff review before completion.
