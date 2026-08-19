# HeightCue operator runbook

## Browser-first invariant
All external observation, judgment, and mutation must be performed and re-verified in the human-visible Aside Browser UI. Local HTML, PDF/PNG rendering, caption generation, and ffmpeg verification may run locally.

## Revenue gate
No monetized product link may go live until the affiliate provider visibly approves the account and the user personally completes terms, identity, settlement-bank, and tax steps. HeightCue never becomes merchant of record.

## Content gate
Each public Short must have two official/primary sources, an exact Korean transcript, Korean SRT, parent-targeted/not-made-for-kids setting, AI disclosure, Education category, medical limitation language, and clean YouTube checks. Never promise height gain or use fear about a closing growth window to sell.

## Render gate
Render each 1080x1920 slide through a single-page PDF with `@page { size:1080px 1920px; margin:0 }`, then `pdftoppm -r 96`. Do not use a full-page screenshot for slides. Verify start/middle/end frames plus H.264/AAC/1080x1920/30fps/<60s.

## Measurement
Use `content/registry.json` for deduplication, this folder's `performance-log.csv` for timestamped browser observations, and `affiliate-readiness.json` for the revenue activation gate. At the final daily run, append one observation per public video. After affiliate approval, add browser-observed clicks, ordered items, and commission without storing customer-level data.
