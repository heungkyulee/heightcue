# HeightCue Raw Identity & Account Context Overhaul

## 1. Overview and Goals
The current HeightCue generation pipeline produces structurally repetitive and overly polished content, feeling more like an AI factory than a top-tier Threader. 
This redesign aims to introduce extreme "raw" authenticity, diversify value posts to break the monotonous "Hero's Journey" template, implement a lightweight Account Context Packet (ACP) for true account memory, and simplify the generation pipeline by trusting Gemini 3.7 Flash's reasoning capabilities over heavy multi-round internal critic loops.

## 2. Architecture Changes

### A. Account Context Packet (ACP)
- **New State File:** `autopilot/state/account_memory.json`
- **Owner:** 윤서진 (HeightCue Brand & Channel Lead).
- **Purpose:** Maintains a living memory of the account's state, preventing repetitive angles and ensuring continuity.
- **Structure:**
  ```json
  {
    "current_tension": "Focus on myth-busting this week. Keep it slightly cynical.",
    "recent_angles_used": ["rant", "raw_memory", "rant"],
    "overused_hooks": ["I stopped growing at 5'6"],
    "audience_vibe": "Tired of miracle ads, asking about sleep routines.",
    "do_not_do_today": "Do not use the middle school basketball captain story."
  }
  ```
- **Integration:** `common.py` will read this file and inject it into the context payload for *all* generation calls (Sales, Value, Replies). 

### B. Value Post (V1) Diversification
- **Change:** Remove the hardcoded dependency on `story-bank.md` as the *only* source of value posts.
- **New Angles (5 Options):**
  1. `rant`: Industry fact-bomb (e.g., calling out bad labels at the mart).
  2. `shower_thought`: 1-2 sentence raw thoughts.
  3. `raw_memory`: Existing story-bank, but explicitly ending on unresolved emotion, no lessons.
  4. `myth_bust`: Destroying a specific parental misconception with data/logic.
  5. `community_qa`: Fictional or real DM/comment response style.
- **Implementation:** `generate.py`'s `make_value_post` will randomly (or sequentially based on ACP) select one of these 5 angles and instruct the prompt accordingly.

### C. Pipeline Simplification
- **Current Problem:** `generate.py` calls `_gemini` for 6 hooks, then `HOOK_CRITIC`, then generates multiple drafts, then `DRAFT_CRITIC`. This over-filters the text, killing the "raw" edge.
- **New Flow:**
  - Generate 3 distinct hooks.
  - Pick 1 directly (or use a lightweight heuristic).
  - Generate 1 draft explicitly instructed to be "RAW, unpolished, zero AI-wrap-up."
  - `post_check.py` checks *only* for hard compliance (FTC/KFTC rules, no medical claims). It does *not* critique the tone.
  - If compliance passes, it publishes.

### D. Tone & Manner Constraints (Anti-AI-Wrap-up)
- **Files to update:** `context/voice-kr.md`, `context/voice-us.md`, `context/compliance.md`.
- **New Hard Rules:**
  - ABSOLUTELY NO concluding wrap-ups ("So we must...", "Let's do our best", "Follow for more").
  - Allow fragmented sentences and trailing thoughts (e.g., ending with just "진짜 헛웃음만 나옴.").
  - Banned structure: The classic 4-part essay (Hook -> Story -> Advice -> Conclusion).

### E. The Brand Lead Bot
- **Profile:** Create `heightcue-brand-lead` (윤서진).
- **Routine:** A new step in `run.py` (e.g., `run.py digest`) or a dedicated cron job where 윤서진 reads yesterday's published posts, replies, and comments, and rewrites `account_memory.json` to guide the editors (정나영, 최유진) for the next day.

## 3. Implementation Steps (To be detailed in Plan)
1. Create the `heightcue-brand-lead` Bot profile and SOUL.
2. Initialize `account_memory.json` and update `common.py` to inject it into system prompts.
3. Rewrite `generate.py` to simplify the generation pipeline and support the 5 V1 angles.
4. Update `context/` markdown files to enforce the new "raw" constraints.
5. Create a `digest` function for 윤서진 to update the ACP daily.
6. Verify via `dry_run` to ensure the generated text is noticeably more human, fragmented, and varied.
