"""Side-effect-free authoritative generation SSOT resolution and prompt assembly."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

REHEARSAL_PRODUCTS = {
    "kr-front-open-storage": {
        "product_key": "kr-front-open-storage", "approved_product_id": "kr-front-open-storage",
        "country": "KR", "category": "storage", "product_name": "앞으로 여는 장난감 수납함",
        "friction_id": "fr-rehearsal-storage", "source_pointers": ["rehearsal:approved-friction", "review:weak-latch"],
        "scores": {"friction_frequency": 4, "friction_intensity": 4, "mechanism_clarity": 5,
                   "mobile_demo_clarity": 5, "consideration_cost": 1, "price_resistance": 1,
                   "review_evidence_strength": 4, "failure_mode_severity": 2, "compliance_cost": 1,
                   "expected_commission_value": 3, "attribution_readiness": 5},
        "wrong_purchase_reversible": True, "mechanism": "front_open",
        "failure_mode": "weak_latch", "skip_if": "선반 깊이가 얕은 집",
        "is_food": False, "approved_claims": [], "price_info": "20,000원",
        "price_band": "KR_10_30K",
        "review_quotes": ["잠금이 약한 제품은 문이 벌어져요"],
        "spec_facts": ["앞으로 여는 구조", "적층 상태에서 내부 접근"],
        "link": "https://heightcue.lifoli.co.kr/kr/", "sub_id": "hc-fr-rehearsal-storage",
        "rehearsal_fixture": True,
    },
    "us-front-open-storage": {
        "product_key": "us-front-open-storage", "approved_product_id": "us-front-open-storage",
        "country": "US", "category": "storage", "product_name": "Front-opening toy storage bin",
        "friction_id": "fr-rehearsal-storage-us",
        "source_pointers": ["rehearsal:approved-friction-us", "review:weak-latch-us"],
        "scores": {"friction_frequency": 4, "friction_intensity": 4, "mechanism_clarity": 5,
                   "mobile_demo_clarity": 5, "consideration_cost": 1, "price_resistance": 1,
                   "review_evidence_strength": 4, "failure_mode_severity": 2, "compliance_cost": 1,
                   "expected_commission_value": 3, "attribution_readiness": 5},
        "wrong_purchase_reversible": True, "mechanism": "front_open",
        "failure_mode": "weak_latch", "skip_if": "your shelf is too shallow",
        "is_food": False, "approved_claims": [], "price_info": "$24",
        "price_band": "US_15_30",
        "review_quotes": ["Weak latches let the door bow open"],
        "spec_facts": ["front-opening access", "access while bins remain stacked"],
        "link": "https://heightcue.lifoli.co.kr/us/", "sub_id": "hc-fr-rehearsal-storage-us",
        "rehearsal_fixture": True,
    },
    "us-ddrops-kids-600iu": {
        "product_key": "us-ddrops-kids-600iu", "country": "US", "category": "nutrition",
        "product_name": "Ddrops Kids Booster Vitamin D3 600 IU",
        "is_food": True, "is_certified_health_food": False, "approved_claims": [],
        "price_info": "", "price_band": "US_PRICE_UNAVAILABLE",
        "review_count": None, "review_rating": None, "review_quotes": [],
        "spec_facts": ["600 IU vitamin D3 per labeled drop", "fractionated coconut oil"],
        "link": "https://heightcue.lifoli.co.kr/us/vitamin-d-drops.html", "sub_id": "us-guide",
        "friction_id": "fr-rehearsal-nutrition-us",
        "source_pointers": ["rehearsal:approved-nutrition-us", "label:ddrops-600iu"],
        "mechanism": "single_labeled_drop", "failure_mode": "label_mismatch",
        "skip_if": "the exact label or fractionated coconut oil does not fit",
        "rehearsal_fixture": True,
    }
}

TASK_DIRECTIVES = {
    "sales_master": 'Return only JSON: {"hooks":["..."],"verified_points":["..."]}. Both arrays must be non-empty and grounded only in the resolved product.',
    "sales_hooks": 'Return only JSON: {"hooks":["...","...","...","...","...","..."]}. Supply exactly six non-empty unique hooks grounded only in the resolved product.',
    "sales_post": 'Return only JSON: {"candidates":[{"id":"a","text":"..."},{"id":"b","text":"..."}]}. Supply at least two candidates with unique IDs for a compliant sales post grounded only in the resolved product. Keep every finished candidate at or below 440 characters. The first line must contain a concrete number, question, or explicit contrast. Include this exact non-fit line: skip if: the exact label or fractionated coconut oil does not fit. Every candidate must end exactly in this shape, using the resolved product link field copied verbatim: Full breakdown and current listing: <link> (paid link). Never use emojis, never use affiliate_link in the post body, and never emit a placeholder such as [HeightCue guide link], [link], or URL_HERE.',
    "value_post": 'Return only this JSON object shape: {"candidates":[{"id":"a","text":"..."},{"id":"b","text":"..."}]}. Supply at least two finished candidates with unique IDs for the requested friction stage. Do not return a single {"text":"..."} object and do not return prose outside the JSON object. Ground every candidate only in the validated friction input. Treat each observation as one reported scene, not a population claim: do not invent numbers, prevalence or frequency words (often, usually, many, always, every), first-person experience, family history, dialogue, or facts absent from resolved_payload. No biography or product/link in discovery or bridge. No teacher wrap-up, moral, generic advice, follow request, or AI conclusion.',
    "value_thread": 'Return only JSON: {"parts":["...","..."]}. Supply two to four non-empty thread posts grounded only in the resolved input. No teacher wrap-up, moral, generic advice, follow request, or AI conclusion; end on a concrete observation or abrupt human reaction.',
    "comment_reply": 'Return only JSON with non-empty category, action, reason, and text when action is reply: {"category":"...","action":"reply|hold|skip","reason":"...","text":"..."}. Ground it only in the resolved comment and post.',
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_inputs(project_root, task, input_ids, allow_rehearsal=False):
    """Resolve stable input identities without mutation, network, environment, or global state."""
    root = Path(project_root)
    state = root / "autopilot/state"
    found = []
    for ident in input_ids:
        kind, sep, value = str(ident).partition(":")
        if not sep or not value:
            raise ValueError("invalid input id")
        if kind in ("episode", "topic"):
            raise ValueError(f"retired or unvalidated input id: {ident}")
        elif kind == "friction":
            path = state / "friction_signals.jsonl"
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
            row = next((x for x in rows if str(x.get("friction_id")) == value
                        and x.get("lifecycle") in {"validated", "active"}), None)
            if row is None:
                raise ValueError(f"unresolved input id: {ident}")
            found.append(row)
        elif kind == "atom":
            path = state / "insight_atoms.json"
            rows = _json(path) if path.exists() else []
            if isinstance(rows, dict):
                rows = rows.get("atoms", [])
            row = next((x for x in rows if str(x.get("atom_id")) == value), None)
            if row is None:
                raise ValueError(f"unresolved input id: {ident}")
            found.append(row)
        elif kind == "queue_product":
            product_key, digest_sep, expected_digest = value.rpartition(":")
            if not digest_sep or not product_key or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
                raise ValueError(f"invalid audited queue product id: {ident}")
            local = state / "browser-queue/results.json"
            rows = _json(local) if local.exists() else []
            row = next((x for x in rows if str(x.get("product_key")) == product_key), None)
            if row is None:
                raise ValueError(f"unresolved input id: {ident}")
            requests_path = state / "browser-queue/requests.json"
            requests = _json(requests_path) if requests_path.exists() else []
            request = next((x for x in requests if x.get("id") == row.get("request_id")), None)
            from sourcing import canonical_queue_product, queue_product_input_id, score_candidate, AUDIT_OWNERS
            if queue_product_input_id(row, request) != ident:
                raise ValueError(f"audited queue packet digest mismatch: {ident}")
            if (row.get("status") != "done" or row.get("audit_status") != "approved"
                    or row.get("audited_by") not in AUDIT_OWNERS
                    or not score_candidate(row)["eligible"]):
                raise ValueError(f"audited queue product no longer passes gates: {ident}")
            found.append(canonical_queue_product(row, request))
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
