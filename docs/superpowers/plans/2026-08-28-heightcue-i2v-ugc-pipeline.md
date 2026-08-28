# Implementation Plan: HeightCue I2V UGC Pipeline

## Reference

- **Approved spec:** `docs/superpowers/specs/2026-08-28-heightcue-i2v-ugc-pipeline-design.md`
- **Primary repository:** `/Users/leeheungkyu/heightcue-autopilot`
- **Generation runtime repository:** `/Users/leeheungkyu/OpenMontage`
- **Publishing profile:** `/Users/leeheungkyu/.hermes/profiles/pip-publisher`

## Locked implementation decisions

- HeightCue's internal image-model alias remains `gpt-image-gen-2`; the OpenAI provider request uses the verified API model ID `gpt-image-2`. Both identifiers must appear in every generation manifest.
- Video generation uses fal.ai `minimax/h3-max/image-to-video`, `768P`, `9:16`, and 5-second generated cuts. It never silently falls back to Hailuo 03, text-to-video, or another provider.
- OpenMontage owns image editing, I2V, Remotion composition, media probing, and low-level generation outputs.
- HeightCue owns product evidence, reference patterns, storyboards, policy gates, job state, retries, and publishing handoff.
- Threads and YouTube reference collection uses Aside `u0` for visual/browser inspection. YouTube metadata and subtitles may use agent-reach/`yt-dlp`. Reference media is analyzed for structure, not copied.
- The publishing handoff contains a local MP4. 송재현 (`pip-publisher`) uploads that file through Aside `u0`; no public video-hosting layer is introduced.
- Existing text-publishing cron jobs remain untouched. Video publishing gets a separate monitor-driven agent cron so an unchanged empty queue costs no model call.
- Runtime state, downloaded product assets, generated frames, videos, and provider responses are never committed.

## TDD execution protocol

Every implementation task follows the same non-negotiable loop:

1. **RED:** Add or change the named test first and run its exact command. Capture the expected failure caused by the missing behavior—not an import typo or broken fixture.
2. **GREEN:** Implement the smallest production change that makes the new test pass.
3. **REFACTOR:** Improve names and boundaries without widening scope, then rerun the focused test and the named regression set.
4. **VERIFY:** Run `git diff --check`, inspect staged paths, and commit only the task's files.

A task is not complete if its test passed before the production change unless the test is first proven to fail against the pre-change behavior by reverting or isolating the relevant change safely.

## Repository safety before implementation

1. In `/Users/leeheungkyu/OpenMontage`, preserve the pre-existing uncommitted edits to `tools/video/minimax_fal_video.py`, `tools/video/video_compose.py`, and `tests/tools/test_new_video_model_support.py`, plus untracked `tests/tools/test_minimax_fal_video.py`. Inspect and incorporate them; do not reset or overwrite them.
2. In `/Users/leeheungkyu/heightcue-autopilot`, stage only files named by each task. The repository already contains many unrelated untracked files.
3. Before each commit, run `git diff --check` and inspect `git diff --cached --name-only`.
4. Never add credentials to either repository. OpenMontage loads `FAL_KEY` from its existing `.env`. A live OpenAI edit is blocked until `OPENAI_API_KEY` is configured; unit and dry-run work may proceed, but completion cannot be claimed without the live generation gate.

## Phase 1: Correct and lock provider contracts

### Task 1: Route MiniMax H3 Max I2V to its real endpoint

- **Action:**
  1. Add a failing contract test for `image_to_video`.
  2. Assert that the queue submission URL is exactly `https://queue.fal.run/minimax/h3-max/image-to-video`.
  3. Assert the payload includes `image_url`, optional `end_image_url`, `duration`, `resolution`, `prompt_expansion_mode`, and `enable_safety_checker`.
  4. Assert a missing `image_url` fails before any network call.
  5. Assert provider failure does not invoke Hailuo 03 or T2V.
  6. Implement the smallest endpoint-routing and cost-estimation change that passes.
- **Files:**
  - Modify `/Users/leeheungkyu/OpenMontage/tools/video/minimax_fal_video.py`
  - Modify or create `/Users/leeheungkyu/OpenMontage/tests/tools/test_minimax_fal_video.py`
- **Commands:**
  - `cd /Users/leeheungkyu/OpenMontage && .venv/bin/python -m pytest tests/tools/test_minimax_fal_video.py -q`
  - `cd /Users/leeheungkyu/OpenMontage && .venv/bin/python -m pytest tests/tools/test_new_video_model_support.py -q`
  - `cd /Users/leeheungkyu/OpenMontage && git diff --check`
- **Validation:** All I2V contract tests pass; the test spy observes only `minimax/h3-max/image-to-video`; no fallback request occurs.
- **Commit:** `fix: route MiniMax H3 Max image generation correctly`

### Task 2: Add product-preserving GPT Image editing

- **Action:**
  1. Add failing tests for an `edit` operation that requires one or more source image paths.
  2. Map internal alias `gpt-image-gen-2` to provider model `gpt-image-2` explicitly.
  3. Use the OpenAI Images edit API with source files, `size="1024x1536"`, configured quality, and a product-preservation prompt.
  4. Return both `model_alias` and `provider_model` in `ToolResult.data`.
  5. Reject text-only use when `operation="edit"`; do not generate a replacement product from text.
  6. Keep the existing text-to-image behavior backward compatible.
- **Files:**
  - Modify `/Users/leeheungkyu/OpenMontage/tools/graphics/openai_image.py`
  - Create `/Users/leeheungkyu/OpenMontage/tests/tools/test_openai_image.py`
- **Commands:**
  - `cd /Users/leeheungkyu/OpenMontage && .venv/bin/python -m pytest tests/tools/test_openai_image.py -q`
  - `cd /Users/leeheungkyu/OpenMontage && .venv/bin/python -m pytest tests/tools -q`
- **Validation:** Mocked OpenAI client receives `model="gpt-image-2"` and the actual source file; outputs are portrait images; manifests preserve both identifiers; existing tool tests still pass.
- **Commit:** `feat: add product-grounded GPT Image editing`

### Task 3: Register a HeightCue-specific OpenMontage pipeline

- **Action:**
  1. Add a pipeline contract test before the manifest.
  2. Define stages `research → proposal → script → scene_plan → assets → edit → compose` using existing canonical stage names.
  3. Require `openai_image`, `minimax_fal_video`, `video_compose`, `frame_sampler`, and `transcriber` where appropriate.
  4. Lock `render_runtime: remotion`, `composition_mode: atelier`, `aspect_ratio: 9:16`, and the approved provider/model choices in the proposal metadata.
  5. Do not add a new generalized framework or unrelated artifact schemas.
- **Files:**
  - Create `/Users/leeheungkyu/OpenMontage/pipeline_defs/heightcue-ugc.yaml`
  - Create `/Users/leeheungkyu/OpenMontage/tests/contracts/test_heightcue_ugc_pipeline.py`
- **Commands:**
  - `cd /Users/leeheungkyu/OpenMontage && .venv/bin/python -m pytest tests/contracts/test_heightcue_ugc_pipeline.py -q`
  - `cd /Users/leeheungkyu/OpenMontage && .venv/bin/python -m pytest tests/test_pipeline_loader.py tests/contracts -q`
- **Validation:** `load_pipeline("heightcue-ugc")` succeeds, stage order is fixed, required tools resolve, and runtime/model locks are test-visible.
- **Commit:** `feat: register HeightCue UGC pipeline`

## Phase 2: Define HeightCue contracts and deterministic state

### Task 4: Add typed video-job and handoff contracts

- **Action:**
  1. Write failing tests for valid and invalid product evidence, storyboards, generation manifests, QA reports, and handoffs.
  2. Implement dependency-light dataclasses plus explicit validation; do not add Pydantic to HeightCue.
  3. Include IDs and lineage: `run_id`, `job_id`, `product_id`, `market`, source URLs, source hashes, rights/provenance, selected viral-pattern IDs, content draft ID, per-cut prompts, model alias/provider model, provider request IDs, costs, outputs, QA results, and publish state.
  4. Use atomic JSON writes and append-only JSONL events.
  5. Define states `queued`, `generating`, `qa_failed`, `ready_to_publish`, `publishing`, `published`, `retryable_failed`, and `dead_letter`.
- **Files:**
  - Create `autopilot/video_contracts.py`
  - Create `autopilot/test_video_contracts.py`
  - Modify `.gitignore`
- **Commands:**
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python -m unittest -v test_video_contracts.py`
- **Validation:** Invalid lineage, unsupported duration, missing rights evidence, wrong aspect ratio, or a provider-model mismatch fails deterministically; valid objects round-trip without losing fields.
- **Commit:** `feat: define HeightCue video job contracts`

### Task 5: Add an idempotent video-job ledger

- **Action:**
  1. Write failing tests for enqueue, claim, heartbeat, complete, retry, dead-letter, and duplicate suppression.
  2. Implement a file-backed ledger under `autopilot/state/video/` using atomic replacement and a lock file.
  3. Build the idempotency key from market, product ID, source hashes, storyboard hash, and pipeline version.
  4. Recover stale `generating` or `publishing` claims after a bounded lease.
  5. Expose a small CLI for tests, cron monitors, and operators.
- **Files:**
  - Create `autopilot/video_queue.py`
  - Create `autopilot/test_video_queue.py`
- **Commands:**
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python -m unittest -v test_video_queue.py`
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python video_queue.py status`
- **Validation:** Two concurrent claims cannot own the same job; duplicate enqueue returns the existing job; stale leases recover; published jobs cannot be republished.
- **Commit:** `feat: add idempotent video queue`

## Phase 3: Build the reference-intelligence input

### Task 6: Normalize viral UGC observations without copying media

- **Action:**
  1. Add parser/scoring tests using local fixtures derived from already observed public post metadata.
  2. Store observations and analyst inference separately.
  3. Extract reusable grammar: 0–2 second hook, product reveal timing, shot count, hand/face/product ratio, camera movement, demo action, proof moment, caption/voice structure, disclosure, CTA, and engagement snapshot.
  4. Separate KR and US ledgers and reject entries with missing source URLs or observation dates.
  5. Score patterns by market fit, product fit, evidence quality, recency, engagement signals, and policy compatibility.
  6. Save only metadata, notes, timestamps, and source URLs; do not download or reuse creators' media in generated outputs.
- **Files:**
  - Create `autopilot/viral_ugc.py`
  - Create `autopilot/test_viral_ugc.py`
  - Create `autopilot/fixtures/viral_ugc_sample.jsonl`
- **Commands:**
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python -m unittest -v test_viral_ugc.py`
- **Validation:** The same fixture always selects the same compatible patterns; non-compliant disclosure or unsupported observation fields are flagged rather than silently learned.
- **Commit:** `feat: add viral UGC pattern ledger`

### Task 7: Add a read-only Aside/YouTube collection routine

- **Action:**
  1. Create a deterministic collection brief that directs Aside `u0` to inspect Threads visually and directs agent-reach/`yt-dlp` to collect YouTube metadata/subtitles.
  2. Split the run into small bounded searches to avoid the previous 300-second timeout.
  3. Write raw results to a temporary file, validate them through `viral_ugc.py`, then atomically append accepted observations.
  4. Include a fixture-only `--dry-run` path for CI; live browser collection is an explicit integration test.
  5. Run `agent-reach doctor --json` before YouTube collection and `agent-reach check-update` after a substantial collection run.
- **Files:**
  - Create `autopilot/collect_viral_ugc.py`
  - Create `autopilot/test_collect_viral_ugc.py`
  - Create `docs/operations/viral-ugc-collection-brief.md`
- **Commands:**
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python -m unittest -v test_collect_viral_ugc.py`
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python collect_viral_ugc.py --dry-run --fixture fixtures/viral_ugc_sample.jsonl`
  - Live gate: `aside --account u0 exec "Read-only: execute the HeightCue viral UGC collection brief in docs/operations/viral-ugc-collection-brief.md for one bounded Threads query and return structured observations only."`
- **Validation:** Dry-run is deterministic; a bounded live run yields at least one source-linked observation or an explicit evidence-backed empty result; no social write occurs.
- **Commit:** `feat: collect viral UGC references safely`

## Phase 4: Produce grounded storyboards and first frames

### Task 8: Generate evidence-bound micro-storyboards

- **Action:**
  1. Add failing tests for 5-second/1-cut, 10-second/2-cut, and 15-second/3-cut plans.
  2. Build the model payload from approved product evidence, content-draft evidence, and selected UGC grammar.
  3. Require one action and one utility per cut; reject montage-like first-frame prompts.
  4. Keep claims and spoken lines traceable to source evidence.
  5. Default to 10 seconds/2 cuts; choose 5 or 15 seconds only through explicit complexity rules.
  6. Use the existing OpenRouter generation path and structured JSON validation.
- **Files:**
  - Create `autopilot/video_storyboard.py`
  - Create `autopilot/test_video_storyboard.py`
  - Modify `autopilot/generate.py` only if a reusable structured-generation helper is required.
  - Modify `autopilot/config.example.json`
- **Commands:**
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python -m unittest -v test_video_storyboard.py`
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python post_check.py test_posts.json --test`
- **Validation:** Every storyboard has 1–3 five-second cuts, a source-backed voice line, one-action first-frame prompt, motion-only I2V prompt, selected reference-pattern IDs, and no unsupported claim.
- **Commit:** `feat: generate grounded UGC storyboards`

### Task 9: Source and fingerprint official product images

- **Action:**
  1. Add tests for accepted MIME types, dimensions, perceptual/source hashes, provenance, and option matching.
  2. Consume only source assets already approved by the sourcing/proof flow.
  3. Download to the run workspace, strip unsafe filenames, record redirects and final URLs, and reject HTML/error placeholders.
  4. Require the image to match the exact marketed option/variant.
- **Files:**
  - Create `autopilot/product_assets.py`
  - Create `autopilot/test_product_assets.py`
  - Modify `autopilot/sourcing.py` only to expose the approved asset/provenance fields without weakening current gates.
- **Commands:**
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python -m unittest -v test_product_assets.py`
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python -m unittest -v test_queue.py test_ops.py`
- **Validation:** Missing rights/provenance or option mismatch blocks generation; a valid asset has stable source and content hashes and a local workspace path.
- **Commit:** `feat: prepare verified product assets for video`

### Task 10: Generate one first frame per cut

- **Action:**
  1. Add orchestration tests that mock OpenMontage but inspect every call.
  2. Invoke `openai_image` in edit mode with the verified product image and one cut's first-frame prompt.
  3. Save each output under `/Users/leeheungkyu/OpenMontage/projects/heightcue_<run_id>/assets/frames/`.
  4. Verify portrait dimensions and record hashes before paying for video generation.
  5. Stop the job if `OPENAI_API_KEY` is unavailable; do not substitute Hermes image generation or another provider in the production path.
- **Files:**
  - Create `autopilot/video_generate.py`
  - Create `autopilot/test_video_generate.py`
- **Commands:**
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python -m unittest -v test_video_generate.py`
  - Live preflight: `cd /Users/leeheungkyu/OpenMontage && .venv/bin/python -c "from tools.tool_registry import registry; import json; registry.discover(); print(json.dumps(registry.provider_menu_summary(), indent=2))"`
- **Validation:** Exactly one portrait frame is produced per cut; each frame references the official product input; manifest IDs are correct; missing credentials produce a clear blocked state and zero video charges.
- **Commit:** `feat: generate product-grounded UGC first frames`

## Phase 5: Generate, compose, and inspect the video

### Task 11: Generate H3 Max I2V cuts with a cost gate

- **Action:**
  1. Add tests for one request per cut, fixed 5-second duration, 768P, first-frame URL/data input, no T2V path, and retry accounting.
  2. Record tool, provider, endpoint, model, estimated cost, and approval policy in the run decision log before the call.
  3. Execute only after first-frame checks pass.
  4. On retryable fal.ai errors, retry within the approved limit; on model/content failures, mark `qa_failed` or `retryable_failed` without provider fallback.
- **Files:**
  - Modify `autopilot/video_generate.py`
  - Modify `autopilot/test_video_generate.py`
- **Commands:**
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python -m unittest -v test_video_generate.py`
  - `cd /Users/leeheungkyu/OpenMontage && .venv/bin/python -m pytest tests/tools/test_minimax_fal_video.py -q`
- **Validation:** A 10-second storyboard creates exactly two H3 Max I2V requests; every request is 5 seconds/768P and uses its corresponding first frame; the manifest contains actual provider request IDs and costs.
- **Commit:** `feat: generate MiniMax H3 Max UGC cuts`

### Task 12: Compose cuts and deterministic overlays with Remotion

- **Action:**
  1. Add a composition contract test with two synthetic clips.
  2. Generate OpenMontage `proposal_packet`, `edit_decisions`, and `asset_manifest` inputs with `render_runtime="remotion"` carried unchanged.
  3. Compose cuts, native audio/voice, verified captions, affiliate disclosure, and CTA without asking the video model to render text.
  4. Produce H.264/AAC MP4, 9:16, 768P-class portrait output, and exact 5/10/15-second duration.
  5. Fail rather than silently switching to HyperFrames or FFmpeg if the approved Remotion path is unavailable.
- **Files:**
  - Modify `autopilot/video_generate.py`
  - Create `autopilot/test_video_compose.py`
  - Create the minimal project-local Remotion entry/template under `/Users/leeheungkyu/OpenMontage/remotion/src/heightcue/` if existing atelier inputs cannot express the overlay contract.
- **Commands:**
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python -m unittest -v test_video_compose.py`
  - `cd /Users/leeheungkyu/OpenMontage && .venv/bin/python -m pytest tests/tools/test_video_compose* -q`
- **Validation:** Synthetic 2-cut render has expected duration, portrait dimensions, H.264 video, AAC audio, readable deterministic overlays, and no runtime swap.
- **Commit:** `feat: compose HeightCue UGC videos with Remotion`

### Task 13: Enforce product, speech, policy, and technical QA

- **Action:**
  1. Add tests with known-good and intentionally bad frame/audio/metadata fixtures.
  2. Sample first, middle, transition, and final frames through OpenMontage `frame_sampler`.
  3. Compare product identity/option against source and first frames using deterministic hash/similarity checks plus a structured visual-review gate for ambiguous cases.
  4. Transcribe audio, normalize text, and compare it against approved spoken lines.
  5. Reuse HeightCue claim, disclosure, link, market, and option gates; add duration, aspect ratio, resolution, codec, audio, black-frame, and duplicate-frame checks.
  6. Allow at most the specified same-provider regeneration attempts; otherwise dead-letter the job.
- **Files:**
  - Create `autopilot/video_qa.py`
  - Create `autopilot/test_video_qa.py`
  - Modify `autopilot/post_check.py` only through reusable public helpers; do not weaken existing rules.
- **Commands:**
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python -m unittest -v test_video_qa.py`
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python post_check.py test_posts.json --test`
- **Validation:** Every deliberately bad fixture fails the correct gate; the known-good fixture passes all gates; a QA failure cannot enter `ready_to_publish`.
- **Commit:** `feat: enforce UGC video quality gates`

## Phase 6: Handoff and publish through the existing team

### Task 14: Create an atomic publishing handoff

- **Action:**
  1. Add tests for handoff creation, claim, publish acknowledgement, failure, retry, and duplicate acknowledgement.
  2. Produce a ready package containing local MP4 path/hash, cover frame, exact Threads copy, disclosures, CTA/link, market/account, tracking IDs, QA report path, lineage, and idempotency key.
  3. Expose CLI commands `list-ready`, `claim`, `mark-published`, and `mark-failed` for the publisher agent.
  4. Append publish evidence to existing HeightCue logs without breaking text-post analytics.
- **Files:**
  - Create `autopilot/video_handoff.py`
  - Create `autopilot/test_video_handoff.py`
  - Modify `autopilot/analytics.py`
- **Commands:**
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python -m unittest -v test_video_handoff.py`
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python video_handoff.py list-ready --json`
- **Validation:** Only QA-passed jobs are returned; claims are exclusive; acknowledgement requires a real post URL/ID; repeated acknowledgement is idempotent.
- **Commit:** `feat: hand verified videos to publishing`

### Task 15: Add a monitor-driven 송재현 video-publishing cron

- **Action:**
  1. Create a deterministic monitor script that outputs only ready job IDs, states, and attempt numbers—never timestamps or secrets.
  2. Add a self-contained cron prompt instructing 송재현 to claim one job, verify the MP4/hash, use `aside --account u0` to upload to the correct Threads account, preserve copy/disclosure exactly, submit once, read back the resulting post, then acknowledge it with the real post URL/ID.
  3. On uncertainty or failed verification, do not resubmit; call `mark-failed` with evidence so the monitor output changes and retry policy can act.
  4. Create a separate agent cron in `pip-publisher`; do not convert or edit the existing no-agent text-post jobs.
  5. Pin provider/model to OpenRouter `google/gemini-3.7-flash` and workdir to HeightCue.
- **Files:**
  - Create `autopilot/monitor_video_publish.py`
  - Create `autopilot/test_monitor_video_publish.py`
  - Create `docs/operations/video-publisher-cron-prompt.md`
  - Create `/Users/leeheungkyu/.hermes/profiles/pip-publisher/scripts/heightcue-video-ready.py` as a thin wrapper into the repository monitor.
- **Commands:**
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python -m unittest -v test_monitor_video_publish.py`
  - `HERMES_HOME=/Users/leeheungkyu/.hermes/profiles/pip-publisher hermes cron list`
  - Create with an exact self-contained prompt: `HERMES_HOME=/Users/leeheungkyu/.hermes/profiles/pip-publisher hermes cron create "every 5m" "<contents of docs/operations/video-publisher-cron-prompt.md>" --name "[bot:pip-publisher] HeightCue 영상 발행" --deliver "bot-chat:pip-publisher" --monitor-script "heightcue-video-ready.py" --workdir "/Users/leeheungkyu/heightcue-autopilot" --model "google/gemini-3.7-flash" --provider "openrouter"`
  - Read back the generated job ID with `HERMES_HOME=/Users/leeheungkyu/.hermes/profiles/pip-publisher hermes cron list`; never guess it.
- **Validation:** Empty/unchanged queue suppresses agent runs; adding one fixture job wakes one run; the job has the correct model/provider/workdir/monitor; existing five text/comment jobs are unchanged.
- **Commit:** `feat: wake publisher for ready UGC videos`

## Phase 7: Integrate with HeightCue operation modes

### Task 16: Add explicit video commands without changing current daily cadence

- **Action:**
  1. Add CLI tests before wiring commands.
  2. Add `run.py video enqueue`, `run.py video process`, `run.py video status`, and `run.py video rehearsal`.
  3. Keep existing `daily`, `post`, `comments`, and `weekly` behavior unchanged.
  4. In rehearsal mode, build the full job/storyboard/manifest/QA plan with provider calls disabled.
  5. Add config flags for enablement, markets, daily budget, max jobs, retry limits, and kill switch. Default production generation remains disabled until the live end-to-end gate passes.
- **Files:**
  - Modify `autopilot/run.py`
  - Modify `autopilot/config.example.json`
  - Create `autopilot/test_video_run.py`
  - Modify `README-autopilot.md`
  - Modify `LAUNCH-STATUS.md`
- **Commands:**
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python -m unittest -v test_video_run.py`
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python run.py video rehearsal --market KR --fixture fixtures/viral_ugc_sample.jsonl`
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python run.py status`
- **Validation:** Rehearsal creates no paid requests and no publish-ready artifact; old commands and schedules behave identically; kill switch stops new claims cleanly.
- **Commit:** `feat: integrate video workflow commands`

## Phase 8: Regression, live sample, and controlled activation

### Task 17: Run all repository and provider-contract regressions

- **Commands:**
  - `cd /Users/leeheungkyu/OpenMontage && .venv/bin/python -m pytest tests/tools/test_minimax_fal_video.py tests/tools/test_openai_image.py tests/contracts/test_heightcue_ugc_pipeline.py -q`
  - `cd /Users/leeheungkyu/OpenMontage && .venv/bin/python -m pytest -q`
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python validate.py`
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python test_ops.py`
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python test_queue.py`
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python post_check.py test_posts.json --test`
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python -m unittest discover -p 'test_video*.py' -v`
  - `cd /Users/leeheungkyu/heightcue-autopilot && git diff --check`
  - `cd /Users/leeheungkyu/OpenMontage && git diff --check`
- **Validation:** Every command exits 0. If the full OpenMontage suite has pre-existing unrelated failures, record the exact baseline and require every touched-area test plus no new failures.

### Task 18: Generate one paid but unpublished live sample

- **Prerequisites:**
  - `FAL_KEY` remains available to OpenMontage.
  - `OPENAI_API_KEY` is configured through the user's approved credential path. This is the only known external blocker; do not replace the model.
  - One product has approved official imagery, provenance, exact option, content evidence, and a non-sensitive category.
- **Action:**
  1. Run provider preflight and record exact available tools/models.
  2. Estimate and record total sample cost before calling paid providers.
  3. Generate one 5-second/1-cut 768P KR sample to minimize the first paid test.
  4. Run every QA gate and inspect the final video manually/visually.
  5. Keep it unpublished and deliver the MP4, first frame, storyboard, manifest, and QA report for review.
- **Commands:**
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python run.py video enqueue --market KR --product-id <approved_product_id> --duration 5`
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python run.py video process --max-jobs 1 --no-publish`
  - Inspect with `ffprobe` and OpenMontage frame sampling/transcription tools.
- **Validation:** A real 5-second portrait MP4 exists and is playable; product identity and option are preserved; spoken/caption claims match evidence; the provider manifests contain real request IDs/costs; no Threads post was created.

### Task 19: Publish one controlled canary and read it back

- **Action:**
  1. Move only the approved live sample to `ready_to_publish`.
  2. Run the publisher cron once by its actual ID.
  3. Observe Aside upload, then read back the exact Threads post and verify video playback, copy, disclosure, affiliate link/CTA, account, and absence of duplicate posts.
  4. Record the post URL/ID and publishing evidence in HeightCue.
  5. If verification fails, stop activation and dead-letter rather than repost automatically.
- **Commands:**
  - `HERMES_HOME=/Users/leeheungkyu/.hermes/profiles/pip-publisher hermes cron list`
  - `HERMES_HOME=/Users/leeheungkyu/.hermes/profiles/pip-publisher hermes cron run <actual_job_id>`
  - `HERMES_HOME=/Users/leeheungkyu/.hermes/profiles/pip-publisher hermes cron runs <actual_job_id>`
- **Validation:** Exactly one verified Threads post exists with the correct video and disclosure; HeightCue shows `published` with the same URL/ID; retrying the same job produces no second post.

### Task 20: Enable a small canary budget and performance loop

- **Action:**
  1. Enable at most one generated sales video per market per day, within an explicit daily cost cap.
  2. Extend analytics to track pattern ID, storyboard family, product, hook, duration, views, shares, saves, replies, clicks, and attributed revenue at 24 hours, 72 hours, and 7 days.
  3. Promote or retire formats only from HeightCue's own performance evidence; never auto-edit the compliance rules, SSOT, or story bank.
  4. Document kill switch and rollback.
- **Files:**
  - Modify `autopilot/analytics.py`
  - Modify `autopilot/improve.py`
  - Modify `autopilot/config.example.json`
  - Modify `README-autopilot.md`
  - Modify `LAUNCH-STATUS.md`
- **Commands:**
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python -m unittest -v test_video_analytics.py`
  - `cd /Users/leeheungkyu/heightcue-autopilot/autopilot && ../.venv/bin/python run.py weekly`
- **Validation:** Metrics join by real video job/tracking IDs; missing attribution stays unknown instead of becoming zero; disabling the video kill switch stops generation and publishing while preserving existing text operations.
- **Commit:** `feat: learn from HeightCue UGC performance`

## Definition of Done

- [ ] OpenMontage sends I2V only to `minimax/h3-max/image-to-video` and has passing provider-contract tests.
- [ ] OpenMontage edits real product images with provider model `gpt-image-2` while preserving HeightCue alias `gpt-image-gen-2` in manifests.
- [ ] A HeightCue-specific OpenMontage pipeline loads and locks Remotion plus the approved providers/models.
- [ ] HeightCue stores source provenance, rights, exact product option, storyboard, generation lineage, costs, QA, handoff, and publishing evidence.
- [ ] Viral UGC collection uses Aside/agent-reach read-only routes, separates observation from inference, and copies no creator media.
- [ ] Storyboards produce only 1–3 five-second cuts with one action/utility per cut and source-backed spoken claims.
- [ ] First-frame and video generation stop clearly if required credentials or provider capabilities are unavailable; no silent fallback occurs.
- [ ] Product identity, speech, policy, disclosure, duration, portrait format, codec, audio, and frame-quality gates all pass before handoff.
- [ ] 송재현 receives ready local MP4s through a monitor-driven Gemini 3.7 Flash cron, uploads with Aside `u0`, reads back the post, and cannot duplicate-publish a job.
- [ ] Existing HeightCue text generation, publishing, comments, analytics, and cron schedules pass regression tests unchanged.
- [ ] One real paid sample is generated and QA-verified before publication.
- [ ] One controlled canary is published exactly once and externally read back before production enablement.
- [ ] A kill switch, cost cap, retry limit, dead-letter path, and 24h/72h/7d performance loop are documented and tested.
- [ ] All named tests and validations pass, and each external write is verified by reading back the exact target.
