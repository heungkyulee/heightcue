#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""증거 원장 (Evidence Ledger) — 가치글 레이어의 입력 공급 계층.

판매글이 `sourcing.py`(상품 provenance 하드게이트)를 갖는 것과 대칭으로,
가치글은 이 모듈이 '검증된 인사이트 원자'를 공급한다. run.py:127의
하드코딩 topic 문자열을 대체하는 것이 이 모듈의 존재 이유다.

흐름:
    Aside 수집 워커 → evidence.jsonl (원본 수확)
                    → claim_gate()   (1차출처·인과과장·반론 검사)
                    → insight_atoms.json (채널 중립 원자)
                    → pick_atom()    (채널·국가별 소진 추적하며 공급)

핵심 설계: 원자는 채널을 모른다. Threads/TikTok/IG/YouTube는 같은 원자를
서로 다르게 렌더링할 뿐이며, 채널이 늘어도 수집기는 바뀌지 않는다.
"""
import hashlib
import re
import time

from common import append_jsonl, is_real_publication, log, read_json, read_jsonl, state_path, write_json

# ── 주제 거리 체계 (D0=판매 직결 … D3=확산) ────────────────────────────────
# 가치글에는 제품이 없으므로 소싱 카테고리 하드락(영양/숙면/자세/운동)이
# 적용되지 않는다. 다만 무한 확장은 채널 정체성을 지우므로 거리로 통제한다.
TOPIC_DISTANCE = {
    # D0 — 판매 전환 직결
    "nutrition": 0, "sleep": 0, "posture": 0, "exercise": 0, "checkup": 0,
    # D1 — 성장기 생활, 신뢰 형성
    "growth_data": 1, "eating_habits": 1, "stress_growth": 1, "screen_time": 1,
    # D2 — 정신·훈육·마인드셋, 도달 확장
    "discipline": 2, "self_regulation": 2, "manners": 2, "mindset": 2,
    "emotional_dev": 2, "sibling_peer": 2,
    # D3 — 서사·사회적 시선, 2차 확산
    "operator_story": 3, "social_gaze": 3, "body_image": 3,
}

# 목표 배분 (SSOT §3 레이어1 확장). pick_atom이 부족한 거리를 우선 공급한다.
DISTANCE_MIX = {0: 0.40, 1: 0.30, 2: 0.20, 3: 0.10}

# 1차 출처 — 이것 없이는 게이트 통과 불가
PRIMARY_SOURCE_TYPES = {"paper", "gov", "guideline", "official_stat"}
SECONDARY_SOURCE_TYPES = {"news", "viral_post", "blog", "book"}
ALL_SOURCE_TYPES = PRIMARY_SOURCE_TYPES | SECONDARY_SOURCE_TYPES

CONFIDENCE_LEVELS = {"strong", "moderate", "weak"}

# 인과 단정 패턴 — 상관을 인과로 파는 순간 우리는 TruHeight가 된다.
# 한국어 활용형 주의: 크다→큽니다/컸다, 자라다→자랍니다. 어간만 잡으면 다 샌다.
_GROW = r"(?:크|커|컸|큽|자라|자랄|자란|자랍)"
CAUSAL_PATTERNS_KR = [
    # 조건절(~면) + 성장 단정. '수면'은 주제 어휘이므로 제외.
    rf"(?<!수)[가-힣]면\s*(?:키[가는를]?\s*)?{_GROW}",
    rf"때문에\s*(?:키[가는]?\s*)?(?:안\s*)?{_GROW}",
    rf"\d+\s*cm\s*(?:더|는|씩)?\s*{_GROW}",
    rf"확실히\s*{_GROW}",
    rf"반드시\s*{_GROW}",
    r"보장",
]
CAUSAL_PATTERNS_EN = [
    r"\bmakes?\s+(?:kids?|children|them)\s+taller\b",
    r"\bwill\s+(?:grow|add)\s+\d+",
    r"\bguarantee",
    r"\bproven\s+to\s+increase\s+height\b",
]

# 제품·브랜드 효능 암시 — 가치글은 비상업 레이어다.
COMMERCIAL_PATTERNS = [
    r"https?://", r"쿠팡", r"아마존", r"amazon", r"coupang",
    r"구매", r"할인", r"최저가", r"링크", r"프로필\s*링크",
]


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S+09:00")


def _claim_hash(claim):
    """공백·조사 흔들림을 흡수한 중복 판정 키."""
    norm = re.sub(r"[\s\.,!?~·\-—'\"()]", "", (claim or "")).lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _evidence_path(cfg):
    return state_path(cfg, "evidence.jsonl")


def _atoms_path(cfg):
    return state_path(cfg, "insight_atoms.json")


# ── claim_gate ──────────────────────────────────────────────────────────────

def claim_gate(record, known_hashes=None):
    """원본 증거 1건이 인사이트 원자가 될 자격이 있는지 판정.

    반환: (ok: bool, reasons: list[str])
    무인 운영이므로 이 함수가 유일한 방어선이다. 애매하면 반려한다.
    """
    reasons = []
    known_hashes = known_hashes or set()

    claim = (record.get("claim") or "").strip()
    if not claim:
        reasons.append("claim_missing")
    elif len(claim) > 200:
        reasons.append("claim_too_long")

    # ① 주제 — 알려진 분류 체계 안에 있어야 한다
    topic = record.get("topic")
    if topic not in TOPIC_DISTANCE:
        reasons.append(f"topic_unknown:{topic}")

    # ② 1차 출처 강제 — 블로그·언론 재인용만으로는 발행 불가
    sources = record.get("sources") or []
    if not isinstance(sources, list) or not sources:
        reasons.append("sources_missing")
    else:
        types = set()
        for src in sources:
            if not isinstance(src, dict):
                reasons.append("source_malformed")
                continue
            stype = src.get("type")
            if stype not in ALL_SOURCE_TYPES:
                reasons.append(f"source_type_invalid:{stype}")
            if not src.get("url") and not src.get("doi"):
                reasons.append("source_locator_missing")
            types.add(stype)
        if not (types & PRIMARY_SOURCE_TYPES):
            reasons.append("primary_source_required")

    # ③ 확신도
    confidence = record.get("confidence")
    if confidence not in CONFIDENCE_LEVELS:
        reasons.append(f"confidence_invalid:{confidence}")

    # ④ 반론 필수 — "유전이 최대 변수"까지 정직하게가 채널 신뢰의 원천(SSOT §3)
    if not (record.get("counter_claim") or "").strip():
        reasons.append("counter_claim_required")

    # ⑤ 상관 ≠ 인과. weak/moderate 확신도에서 인과 단정은 즉시 반려
    blob = " ".join([claim, record.get("counter_claim") or ""] +
                    list(record.get("hook_seeds") or []))
    for pattern in CAUSAL_PATTERNS_KR + CAUSAL_PATTERNS_EN:
        if re.search(pattern, blob, re.IGNORECASE):
            reasons.append(f"causal_overreach:{pattern}")
            break

    # ⑥ 상업 요소 혼입 금지
    for pattern in COMMERCIAL_PATTERNS:
        if re.search(pattern, blob, re.IGNORECASE):
            reasons.append(f"commercial_leak:{pattern}")
            break

    # ⑦ 바이럴 수확은 '구조만' — 남의 문장 복제는 표절이자 브랜드 자살
    if any((s.get("type") == "viral_post") for s in sources if isinstance(s, dict)):
        if not record.get("structure_only"):
            reasons.append("viral_requires_structure_only")
        for seed in (record.get("hook_seeds") or []):
            for src in sources:
                excerpt = (src.get("excerpt") or "") if isinstance(src, dict) else ""
                if excerpt and seed and seed.strip() in excerpt:
                    reasons.append("viral_verbatim_copy")
                    break

    # ⑧ 중복
    if _claim_hash(claim) in known_hashes:
        reasons.append("duplicate_claim")

    return (not reasons), sorted(set(reasons))


# ── 수확 → 원자 승격 ────────────────────────────────────────────────────────

def record_evidence(cfg, record):
    """Aside 수집 워커가 원본 증거를 원장에 적재한다."""
    entry = dict(record)
    entry.setdefault("harvested_at", _now())
    entry["claim_hash"] = _claim_hash(entry.get("claim"))
    append_jsonl(_evidence_path(cfg), entry)
    return entry


def atom_store(cfg):
    store = read_json(_atoms_path(cfg), None)
    if not isinstance(store, dict) or "atoms" not in store:
        store = {"atoms": [], "updated_at": None}
    return store


def save_atoms(cfg, store):
    store["updated_at"] = _now()
    write_json(_atoms_path(cfg), store)
    return store


def promote_pending(cfg):
    """원장의 미처리 증거를 게이트에 태워 원자로 승격한다 (무인).

    반환: {"promoted": n, "rejected": n, "details": [...]}
    """
    store = atom_store(cfg)
    known = {a.get("claim_hash") for a in store["atoms"]}
    promoted_ids = {a.get("source_evidence_id") for a in store["atoms"]}
    rejected_log = state_path(cfg, "evidence_rejects.jsonl")
    seen_rejects = {r.get("evidence_id") for r in read_jsonl(rejected_log)}

    promoted, rejected, details = 0, 0, []
    for rec in read_jsonl(_evidence_path(cfg)):
        eid = rec.get("evidence_id")
        if not eid or eid in promoted_ids or eid in seen_rejects:
            continue
        ok, reasons = claim_gate(rec, known_hashes=known)
        if not ok:
            append_jsonl(rejected_log, {"evidence_id": eid, "reasons": reasons,
                                        "claim": rec.get("claim"), "at": _now()})
            rejected += 1
            details.append({"evidence_id": eid, "ok": False, "reasons": reasons})
            continue

        atom = {
            "atom_id": rec.get("atom_id") or f"atom-{time.strftime('%Y%m%d')}-{eid}",
            "source_evidence_id": eid,
            "claim": rec["claim"].strip(),
            "counter_claim": rec["counter_claim"].strip(),
            "topic": rec["topic"],
            "distance": TOPIC_DISTANCE[rec["topic"]],
            "confidence": rec["confidence"],
            "sources": rec["sources"],
            "parent_emotion": rec.get("parent_emotion"),
            "hook_seeds": rec.get("hook_seeds") or [],
            "structure_only": bool(rec.get("structure_only")),
            "claim_hash": _claim_hash(rec["claim"]),
            "promoted_at": _now(),
            "used_in": {},          # {"threads_kr": [media_id, ...], ...}
            "performance": {},      # 채널별 성과 회수 지점
        }
        store["atoms"].append(atom)
        known.add(atom["claim_hash"])
        promoted += 1
        details.append({"evidence_id": eid, "ok": True, "atom_id": atom["atom_id"]})

    if promoted:
        save_atoms(cfg, store)
    log(f"증거 승격: 통과 {promoted}건 / 반려 {rejected}건")
    return {"promoted": promoted, "rejected": rejected, "details": details}


# ── 공급 (채널 중립) ────────────────────────────────────────────────────────

def channel_key(channel, country):
    return f"{channel}_{country}".lower()


def _distance_deficit(atoms, ckey):
    """이 채널에서 실제 사용된 거리 분포와 목표 배분의 격차를 계산."""
    used = [a for a in atoms if a.get("used_in", {}).get(ckey)]
    total = len(used) or 1
    actual = {d: 0 for d in DISTANCE_MIX}
    for a in used:
        actual[a.get("distance", 0)] = actual.get(a.get("distance", 0), 0) + 1
    return {d: DISTANCE_MIX[d] - (actual.get(d, 0) / total) for d in DISTANCE_MIX}


def pick_atom(cfg, country="KR", channel="threads", topic=None, max_reuse=1):
    """이 채널·국가에 아직 덜 쓰인 원자를 목표 배분에 맞춰 고른다.

    같은 원자를 Threads엔 썼고 TikTok엔 안 썼다면 TikTok에는 여전히 신선하다.
    이것이 멀티채널 확장에서 원자 단위 관리가 필요한 이유다.
    """
    store = atom_store(cfg)
    atoms = store["atoms"]
    ckey = channel_key(channel, country)

    pool = [a for a in atoms
            if len(a.get("used_in", {}).get(ckey, [])) < max_reuse
            and (topic is None or a.get("topic") == topic)]
    if not pool:
        return None

    deficit = _distance_deficit(atoms, ckey)
    # 부족한 거리 우선 → 미사용 우선 → 강한 확신도 우선
    conf_rank = {"strong": 0, "moderate": 1, "weak": 2}
    pool.sort(key=lambda a: (
        -deficit.get(a.get("distance", 0), 0),
        len(a.get("used_in", {}).get(ckey, [])),
        conf_rank.get(a.get("confidence"), 3),
        a.get("promoted_at", ""),
    ))
    return pool[0]


def mark_used(cfg, atom_id, channel, country, media_id):
    """발행 성공 후 소진 기록. 채널별로 분리 추적한다."""
    store = atom_store(cfg)
    ckey = channel_key(channel, country)
    for atom in store["atoms"]:
        if atom.get("atom_id") == atom_id:
            atom.setdefault("used_in", {}).setdefault(ckey, []).append(media_id)
            save_atoms(cfg, store)
            return atom
    return None


def rebuild_used_in_from_publications(cfg):
    """Append-only 게시 원장에서 실제 root 게시물의 원자 소진 상태를 재구축한다."""
    store = atom_store(cfg)
    atom_by_id = {atom.get("atom_id"): atom for atom in store["atoms"] if atom.get("atom_id")}
    for atom in store["atoms"]:
        atom["used_in"] = {}

    real_roots = 0
    unknown = set()
    seen = set()
    for row in read_jsonl(state_path(cfg, "published.jsonl")):
        media_id = str(row.get("media_id") or "")
        if not is_real_publication(row) or re.match(r"^(?:dry|dryrun|preview|test)[-_]", media_id, re.I):
            continue
        meta = row.get("meta") or {}
        part = meta.get("thread_part")
        if part not in (None, 1, "1") and not (isinstance(part, str) and part.startswith("1/")):
            continue
        atom_id = meta.get("atom_id")
        if not atom_id:
            continue
        atom = atom_by_id.get(atom_id)
        if atom is None:
            unknown.add(atom_id)
            continue
        channel = str(meta.get("channel") or "threads").lower()
        country = str(row.get("country") or meta.get("country") or "").upper()
        if not country:
            continue
        dedupe_key = (atom_id, channel, country, media_id)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        atom.setdefault("used_in", {}).setdefault(channel_key(channel, country), []).append(media_id)
        real_roots += 1

    save_atoms(cfg, store)
    return {
        "atoms": len(store["atoms"]),
        "real_root_publications": real_roots,
        "unknown_atom_ids": sorted(unknown),
    }


def to_generation_topic(atom):
    """원자를 SKILL V1의 topic 입력으로 직렬화 (채널 중립 텍스트)."""
    if not atom:
        return None
    lines = [
        f"[검증된 사실] {atom['claim']}",
        f"[반드시 함께 말할 반론·한계] {atom['counter_claim']}",
        f"[확신도] {atom['confidence']}",
    ]
    if atom.get("parent_emotion"):
        lines.append(f"[부모의 감정 지점] {atom['parent_emotion']}")
    if atom.get("hook_seeds"):
        lines.append(f"[훅 씨앗(그대로 복사 금지, 변형할 것)] {' / '.join(atom['hook_seeds'][:3])}")
    lines.append("[규칙] 위 사실 범위를 넘는 단정 금지. 출처를 지어내지 말 것. "
                 "상관을 인과로 바꾸지 말 것.")
    return "\n".join(lines)


def inventory(cfg):
    """원장 재고 요약 — 주간 리포트·경보용."""
    store = atom_store(cfg)
    atoms = store["atoms"]
    by_distance = {}
    for a in atoms:
        by_distance[a.get("distance", 0)] = by_distance.get(a.get("distance", 0), 0) + 1
    unused = {}
    for ckey in {k for a in atoms for k in a.get("used_in", {})} | {"threads_kr", "threads_us"}:
        unused[ckey] = sum(1 for a in atoms if not a.get("used_in", {}).get(ckey))
    return {"total": len(atoms), "by_distance": by_distance, "unused_by_channel": unused}


if __name__ == "__main__":
    import sys
    from common import load_config
    cfg = load_config()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "promote":
        print(promote_pending(cfg))
    elif cmd == "status":
        print(inventory(cfg))
    elif cmd == "pick":
        atom = pick_atom(cfg, country=sys.argv[2] if len(sys.argv) > 2 else "KR")
        print(to_generation_topic(atom) if atom else "재고 없음")
