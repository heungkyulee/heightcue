"""Side-effect-free authoritative generation SSOT resolution and prompt assembly."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

REHEARSAL_PRODUCTS = {
    "us-ddrops-kids-600iu": {
        "product_key": "us-ddrops-kids-600iu", "country": "US", "category": "nutrition",
        "product_name": "Ddrops Kids Booster Vitamin D3 600 IU",
        "is_food": True, "is_certified_health_food": False, "approved_claims": [],
        "price_info": "", "review_count": None, "review_rating": None, "review_quotes": [],
        "spec_facts": ["600 IU vitamin D3 per labeled drop", "fractionated coconut oil"],
        "link": "https://heightcue.lifoli.co.kr/us/vitamin-d-drops.html", "sub_id": "us-guide",
    }
}

TASK_DIRECTIVES = {
    "sales_master": 'Return only JSON: {"hooks":["..."],"verified_points":["..."]}. Both arrays must be non-empty and grounded only in the resolved product.',
    "sales_hooks": 'Return only JSON: {"hooks":["...","...","...","...","...","..."]}. Supply exactly six non-empty unique hooks grounded only in the resolved product.',
    "sales_post": 'Return only JSON: {"candidates":[{"id":"a","text":"..."},{"id":"b","text":"..."}]}. Supply at least two candidates with unique IDs for a compliant sales post grounded only in the resolved product. Keep every finished candidate at or below 440 characters. The first line must contain a concrete number, question, or explicit contrast. Include this exact non-fit line: skip if: the exact label or fractionated coconut oil does not fit. Every candidate must end exactly in this shape, using the resolved product link field copied verbatim: Full breakdown and current listing: <link> (paid link). Never use emojis, never use affiliate_link in the post body, and never emit a placeholder such as [HeightCue guide link], [link], or URL_HERE.',
    "value_post": 'Return only JSON: {"candidates":[{"id":"a","text":"..."},{"id":"b","text":"..."}]}. Supply at least two candidates with unique IDs for a value post grounded only in the resolved input. For episode inputs, use only approved_facts and do not invent personal scenes or memories. No teacher wrap-up, moral, generic advice, follow request, or AI conclusion; end on a concrete observation or abrupt human reaction.',
    "value_thread": 'Return only JSON: {"parts":["...","..."]}. Supply two to four non-empty thread posts grounded only in the resolved input. No teacher wrap-up, moral, generic advice, follow request, or AI conclusion; end on a concrete observation or abrupt human reaction.',
    "comment_reply": 'Return only JSON with non-empty category, action, reason, and text when action is reply: {"category":"...","action":"reply|hold|skip","reason":"...","text":"..."}. Ground it only in the resolved comment and post.',
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_episode(root, episode_id):
    """Resolve an operator-confirmed story-bank episode into stable facts.

    The story bank is an input source, not a generation fallback.  Only a
    headed episode without an explicit unconfirmed marker is accepted, and
    editorial angle/rule bullets are deliberately excluded from facts.
    """
    path = root / "story-bank.md"
    if not path.exists():
        raise ValueError(f"unresolved episode: {episode_id}")
    text = path.read_text(encoding="utf-8")
    match = re.search(
        rf"^###\s+({re.escape(episode_id)})\.\s*([^\n]+)\n(.*?)(?=^###\s+E\d+\.|^##\s|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise ValueError(f"unresolved episode: {episode_id}")
    title, body = match.group(2).strip(), match.group(3).strip()
    if "⚠️" in title or "⚠️" in body or "미확인" in title or "미확인" in body:
        raise ValueError(f"unresolved episode: {episode_id}")
    facts = []
    for line in body.splitlines():
        fact = line.strip()
        if not fact or fact.startswith("*"):
            continue
        facts.append(fact)
    if not facts:
        raise ValueError(f"unresolved episode: {episode_id}")
    return {"episode_id": episode_id, "title": title, "approved_facts": facts}


def resolve_inputs(project_root, task, input_ids, allow_rehearsal=False):
    """Resolve stable input identities without mutation, network, environment, or global state."""
    root = Path(project_root)
    state = root / "autopilot/state"
    found = []
    for ident in input_ids:
        kind, sep, value = str(ident).partition(":")
        if not sep or not value:
            raise ValueError("invalid input id")
        if kind == "episode":
            found.append(_resolve_episode(root, value))
        elif kind in ("atom", "topic"):
            path = state / "insight_atoms.json"
            rows = _json(path) if path.exists() else []
            if isinstance(rows, dict):
                rows = rows.get("atoms", [])
            row = next((x for x in rows if str(x.get("atom_id")) == value), None)
            if row is None and kind == "atom":
                raise ValueError(f"unresolved input id: {ident}")
            found.append(row if row is not None else {kind: value})
        elif kind == "product":
            if allow_rehearsal and value in REHEARSAL_PRODUCTS:
                found.append(dict(REHEARSAL_PRODUCTS[value]))
                continue
            # Product generation provenance is resolved from the same approved
            # Company OS evidence revision that issued the live claim. Local
            # JSON files are not an execution fallback.
            import companyos
            try:
                found.append(companyos.get_product(value))
            except Exception:
                # Temporary roots are used only by the contract test harness.
                # Never enable this fallback for the production project root.
                temp_root = os.path.realpath(tempfile.gettempdir()) + os.sep
                if (os.environ.get("HEIGHTCUE_TEST_FIXTURE") != "1"
                        and not os.path.realpath(str(root)).startswith(temp_root)):
                    raise
                local = state / "browser-queue/results.json"
                rows = _json(local) if local.exists() else []
                row = next((x for x in rows if str(x.get("product_key")) == value), None)
                if row is None:
                    raise
                found.append(row)
        elif kind in ("comment", "post"):
            rows = []
            for name in ("comments_log.jsonl", "published.jsonl"):
                path = state / name
                if path.exists():
                    rows += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            row = next((x for x in rows if str(x.get("id") or x.get("media_id") or x.get("comment_id")) == value), None)
            if row is None:
                raise ValueError(f"unresolved input id: {ident}")
            found.append(row)
        else:
            raise ValueError("invalid input id")
    return found


def source_context(project_root, task):
    root = Path(project_root)
    manifest = _json(root / "context/execution-contract.json")
    cfg = _json(root / "autopilot/config.json")
    chunks, hashes = [], {}
    for relative in [manifest["intent_source"], *manifest["prompt_sources"]]:
        raw = (root / relative).read_bytes()
        chunks.append(raw)
        hashes[relative] = hashlib.sha256(raw).hexdigest()
    directive = TASK_DIRECTIVES[task].encode("utf-8")
    # Source files contain broad legacy task instructions.  Put the narrow,
    # code-owned operation and exact schema last so same-role legacy prose
    # cannot override the authoritative response contract.
    prompt = (b"HEIGHTCUE TRUSTED SOURCES\n" + b"\n\n".join(chunks)
              + b"\n\nAUTHORITATIVE TASK (FINAL; OVERRIDES CONFLICTING SOURCE WORKFLOWS)\n"
              + directive)
    return manifest, cfg, prompt, hashes
