# -*- coding: utf-8 -*-
"""파이프라인 A-1: 상품 자동 소싱 (쿠팡 파트너스 Open API).

- 카테고리 로테이션으로 컨셉에 맞는 후보를 검색하고, 블랙리스트·중복을 거른 뒤 1개를 고른다.
- 쿠팡 API 키가 없으면 '수동 소싱 모드': state/manual_products.json 의 대기열에서 꺼낸다.
- Open API 검색 응답에는 리뷰 수가 없으므로, 리뷰 인용 데이터(review_quotes 등)는 비워 보낸다
  → 생성 스킬의 데이터 정직성 규칙에 따라 리뷰 관련 문장은 자동 생략된다.
  리뷰 수치를 쓰고 싶은 상품은 manual_products.json 에 review_count/review_quotes 를 채워 넣으면 된다.
"""
import hashlib
import hmac
import json
import os
import time
import urllib.parse
from collections import defaultdict

import requests

from common import log, read_json, state_path, write_json
import friction
import journey_policy

COUPANG_HOST = "https://api-gateway.coupang.com"

CANDIDATE_SCORE_FIELDS = (
    "friction_frequency", "friction_intensity", "mechanism_clarity", "mobile_demo_clarity",
    "consideration_cost", "price_resistance", "review_evidence_strength",
    "failure_mode_severity", "compliance_cost", "expected_commission_value",
    "attribution_readiness",
)
NEGATIVE_SCORE_FIELDS = {"consideration_cost", "price_resistance", "failure_mode_severity", "compliance_cost"}


def score_candidate(candidate):
    """Return an inspectable low-consideration gate and component score."""
    row = dict(candidate or {})
    components = row.get("scores") or {}
    reasons = []
    reasons.extend(journey_policy.product_eligibility(row)["reasons"])
    if not row.get("friction_id"):
        reasons.append("friction_id_missing")
    pointers = row.get("source_pointers")
    if (not isinstance(pointers, list) or not pointers
            or any(not isinstance(pointer, str) or not pointer.strip() for pointer in pointers)):
        reasons.append("source_pointers_missing")
    for flag in ("requires_professional_advice", "high_risk_child_safety",
                 "creator_testimony_required", "health_outcome_primary"):
        if row.get(flag):
            reasons.append(flag)
    if not row.get("wrong_purchase_reversible"):
        reasons.append("wrong_purchase_not_reversible")
    parsed = {}
    for field in CANDIDATE_SCORE_FIELDS:
        value = components.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 5:
            reasons.append(f"score_invalid:{field}")
        else:
            parsed[field] = float(value)
    total = sum((-value if field in NEGATIVE_SCORE_FIELDS else value)
                for field, value in parsed.items())
    return {"eligible": not reasons, "reasons": reasons, "final_score": round(total, 3),
            "components": parsed, "source_pointers": list(pointers or []) if isinstance(pointers, list) else [],
            "friction_id": row.get("friction_id")}


def revenue_rank(metrics):
    """Lexicographic learning hierarchy: commission/orders, clicks, progression, engagement, views."""
    row = metrics or {}
    return (float(row.get("commission") or 0), int(row.get("orders") or 0),
            int(row.get("clicks") or 0), int(row.get("progression") or 0),
            int(row.get("qualified_engagement") or 0), int(row.get("views") or 0))

# 컨셉 카테고리 로테이션 (요일 % len 으로 순환)
# keyword: 스펙 수식어를 포함해 저가 양산형이 상위에 깔리는 것을 방지 (선별자 소싱)
# alt_keywords: 1차 검색 결과가 저가 양산형 위주일 때 쓰는 보강 검색어
# 4대 하이엔드 카테고리 하드락 — 학부모가 지갑 여는 솔루션만. 측정도구·잡화 영구 금지.
CATEGORIES = [
    {"key": "nutrition", "keyword": "어린이 액상 비타민D3 드롭", "is_food": True},
    {"key": "nutrition", "keyword": "어린이 츄어블 칼슘 마그네슘", "is_food": True},
    {"key": "sleep",     "keyword": "어린이 고밀도 매트리스 토퍼", "is_food": False},
    {"key": "sleep",     "keyword": "어린이 경추 베개", "is_food": False},
    {"key": "posture",   "keyword": "초등학생 발받침 의자", "is_food": False},
    {"key": "posture",   "keyword": "어린이 바른자세 높이조절 독서대", "is_food": False},
    {"key": "exercise",  "keyword": "어린이 무소음 실내 줄넘기 매트", "is_food": False},
    {"key": "exercise",  "keyword": "어린이 접이식 트램폴린", "is_food": False},
    # ── UX 파괴 폼팩터 키워드 (2026-08-27 추가) ─────────────────────────────
    # 바이럴/저장 반응은 스펙 비교가 아니라 폼팩터 혁신에서 나온다 (예: 녹는 겔 마스크, 거즈 토너패드).
    # 뻔한 형태(알약·일반매트·일반의자·일반줄넘기) 대신 부모의 행동 수고(실랑이·세탁·층간소음·잔소리)를
    # 물리적으로 없애는 새 폼팩터를 우선 소싱한다. 4대 카테고리 하드락은 그대로 유지.
    {"key": "nutrition", "keyword": "밥에 뿌려먹는 어린이 영양제 분말", "is_food": True},
    {"key": "nutrition", "keyword": "어린이 짜먹는 스틱 영양제", "is_food": True},
    {"key": "sleep",     "keyword": "어린이 워셔블 에어 매트리스 토퍼", "is_food": False},
    {"key": "sleep",     "keyword": "어린이 쿨링 젤 매트", "is_food": False},
    {"key": "posture",   "keyword": "어린이 무릎의자", "is_food": False},
    {"key": "posture",   "keyword": "어린이 바른자세 밸런스 방석", "is_food": False},
    {"key": "exercise",  "keyword": "어린이 점프 터치 카운터", "is_food": False},
    {"key": "exercise",  "keyword": "어린이 무소음 점핑 쿠션", "is_food": False},
]

# ── UX 폼팩터 발굴 저장소 (state/ux_discovery.json) ────────────────────────────
# CATEGORIES는 초기 시드일 뿐, 실제 키워드 로테이션은 이 저장소가 담당한다.
# 라이프사이클: candidate(발굴됨) → active(소싱 성공) → retired(저성과 은퇴).
# 소싱 워커(Aside 루틴)가 매 실행 쿠팡을 훑어 신규 폼팩터를 실물 근거와 함께 추가하고,
# improve.py가 주 1회 성과(클릭/뷰)를 반영해 승격/은퇴를 결정한다.

UX_SEED = [
    # proven — 이미 검증된 잘 팔리는 형태 (exploit)
    {"id": "ff-nut-liquid-d3",      "category": "nutrition", "ux_grade": "proven", "name": "액상 비타민D 드롭",       "keyword": "어린이 액상 비타민D3 드롭", "is_food": True,  "friction_solved": "알약 삼키기 거부 없이 한 방울로 끝"},
    {"id": "ff-nut-chewable-cal",   "category": "nutrition", "ux_grade": "proven", "name": "츄어블 칼슘·마그네슘",     "keyword": "어린이 츄어블 칼슘 마그네슘", "is_food": True,  "friction_solved": "씩어먹는 형태로 섭취 저항 감소"},
    {"id": "ff-slp-dense-topper",   "category": "sleep",     "ux_grade": "proven", "name": "고밀도 매트리스 토퍼",     "keyword": "어린이 고밀도 매트리스 토퍼", "is_food": False, "friction_solved": "꺼짐 없는 지지력으로 수면 환경 개선"},
    {"id": "ff-slp-neck-pillow",    "category": "sleep",     "ux_grade": "proven", "name": "경추 베개",                "keyword": "어린이 경추 베개",           "is_food": False, "friction_solved": "높이 안 맞는 성인 베개 대체"},
    {"id": "ff-pos-footrest-chair", "category": "posture",   "ux_grade": "proven", "name": "발받침 의자",              "keyword": "초등학생 발받침 의자",       "is_food": False, "friction_solved": "다리 댈랑거림 없이 앉은 자세 지지"},
    {"id": "ff-pos-bookstand",      "category": "posture",   "ux_grade": "proven", "name": "높이조절 독서대",          "keyword": "어린이 바른자세 높이조절 독서대", "is_food": False, "friction_solved": "고개 숙임 자체를 물리적으로 차단"},
    {"id": "ff-exc-silent-rope",    "category": "exercise",  "ux_grade": "proven", "name": "무소음 실내 줄넘기 매트",   "keyword": "어린이 무소음 실내 줄넘기 매트", "is_food": False, "friction_solved": "층간소음 걱정 없는 실내 운동"},
    {"id": "ff-exc-trampoline",     "category": "exercise",  "ux_grade": "proven", "name": "접이식 트램폴린",          "keyword": "어린이 접이식 트램폴린",      "is_food": False, "friction_solved": "밖에 안 나가도 집에서 점프 운동"},
    # novel — UX 파괴 폼팩터 (explore)
    {"id": "ff-nut-sprinkle",       "category": "nutrition", "ux_grade": "novel",  "name": "밥에 뿌려먹는 무미 분말",   "keyword": "밥에 뿌려먹는 어린이 영양제 분말", "is_food": True,  "friction_solved": "약 먹이기 실랑이 제로 — 밥에 톡 뿌리면 끝"},
    {"id": "ff-nut-squeeze-stick",  "category": "nutrition", "ux_grade": "novel",  "name": "짜먹는 스틱/파우치",        "keyword": "어린이 짜먹는 스틱 영양제",     "is_food": True,  "friction_solved": "간식처럼 짜먹어 아이가 먼저 찾음"},
    {"id": "ff-slp-washable",       "category": "sleep",     "ux_grade": "novel",  "name": "워셔블 에어폼 매트",       "keyword": "어린이 워셔블 에어 매트리스 토퍼", "is_food": False, "friction_solved": "이불 세탁소 대신 샤워기로 1분 세척"},
    {"id": "ff-slp-cooling-gel",    "category": "sleep",     "ux_grade": "novel",  "name": "쿨링 젤 매트",             "keyword": "어린이 쿨링 젤 매트",          "is_food": False, "friction_solved": "밤새 땀 흘려 깨는 아이 열감 관리"},
    {"id": "ff-pos-kneeling",       "category": "posture",   "ux_grade": "novel",  "name": "무릎의자",                "keyword": "어린이 무릎의자",             "is_food": False, "friction_solved": "'허리 펴' 잔소리 없이 구조적으로 못 구부림"},
    {"id": "ff-pos-balance",        "category": "posture",   "ux_grade": "novel",  "name": "밸런스 방석",              "keyword": "어린이 바른자세 밸런스 방석",    "is_food": False, "friction_solved": "있던 의자에 얹기만 하면 끝"},
    {"id": "ff-exc-jump-counter",   "category": "exercise",  "ux_grade": "novel",  "name": "점프 터치 카운터",         "keyword": "어린이 점프 터치 카운터",      "is_food": False, "friction_solved": "운동 잔소리 없이 게임처럼 스스로 점프"},
    {"id": "ff-exc-jump-cushion",   "category": "exercise",  "ux_grade": "novel",  "name": "무소음 점핑 쿠션",         "keyword": "어린이 무소음 점핑 쿠션",       "is_food": False, "friction_solved": "층간소음 0으로 거실 점프 허용"},
]


def ux_store(cfg):
    """ux_discovery.json 로드. 없거나 비었으면 시드로 생성."""
    path = state_path(cfg, "ux_discovery.json")
    data = read_json(path, None)
    if not data or not data.get("formfactors"):
        data = {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "formfactors": [dict(f, status="active", sourced_count=0,
                                     stats={"posts": 0, "views": 0, "clicks": 0},
                                     discovered_by="seed", discovered_ts=0) for f in UX_SEED]}
        write_json(path, data)
    return data


def save_ux_store(cfg, data):
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write_json(state_path(cfg, "ux_discovery.json"), data)


def _ux_pool(data, grade):
    return [f for f in data["formfactors"]
            if f.get("status") in ("candidate", "active") and f.get("ux_grade") == grade]


def _mark_sourced(cfg, result):
    """큐 결과 소비 시 폼팩터 이력 갱신 (candidate → active 승격 포함)."""
    ffid = result.get("formfactor_id")
    if not ffid:
        return
    data = ux_store(cfg)
    for f in data["formfactors"]:
        if f["id"] == ffid:
            f["sourced_count"] = f.get("sourced_count", 0) + 1
            f["last_sourced"] = time.strftime("%Y-%m-%d")
            if f.get("status") == "candidate":
                f["status"] = "active"
                log(f"UX 폼팩터 승격: {f.get('name')} candidate → active")
            save_ux_store(cfg, data)
            return


def update_ux_stats(cfg):
    """주간(improve.py 호출): 폼팩터별 성과 반영 + 저성과 은퇴 + 발굴 감사 요약 반환."""
    from common import read_jsonl  # 순환 임포트 회피용 지연 임포트 아님, 명시용
    import analytics
    excluded_ids = analytics.analysis_excluded_ids(cfg)
    rows = read_jsonl(state_path(cfg, "metrics.jsonl"))
    latest = {}
    for r in rows:
        if (str(r.get("media_id") or "").startswith("DRY-") or r.get("dry_run")
                or str(r.get("media_id") or "") in excluded_ids):
            continue
        latest[r.get("media_id")] = r
    per = defaultdict(lambda: {"posts": 0, "views": 0, "clicks": 0})
    for r in latest.values():
        ffid = r.get("formfactor_id")
        if not ffid:
            continue
        per[ffid]["posts"] += 1
        per[ffid]["views"] += (r.get("insights") or {}).get("views") or 0
        per[ffid]["clicks"] += r.get("link_clicks") or 0
    data = ux_store(cfg)
    ctrs = sorted(v["clicks"] / v["views"] for v in per.values() if v["views"] >= 500)
    median_ctr = ctrs[len(ctrs) // 2] if ctrs else None
    retired = []
    for f in data["formfactors"]:
        s = per.get(f["id"])
        if s:
            f["stats"] = s
            # 은퇴 규칙: 표본 충분(게시 3+·뷰 1500+) + CTR이 중앙값의 30% 미만
            if (median_ctr and f.get("status") == "active" and s["posts"] >= 3
                    and s["views"] >= 1500 and (s["clicks"] / s["views"]) < 0.3 * median_ctr):
                f["status"] = "retired"
                f["retired_reason"] = f"CTR {s['clicks']}/{s['views']} < 중앙값 30% ({time.strftime('%Y-%m-%d')})"
                retired.append(f["name"])
    save_ux_store(cfg, data)
    week_ago = time.time() - 7 * 86400
    new = [f["name"] for f in data["formfactors"] if (f.get("discovered_ts") or 0) >= week_ago]
    ff = data["formfactors"]
    audit = {
        "formfactors_total": len(ff),
        "novel_active": sum(1 for f in ff if f["ux_grade"] == "novel" and f["status"] == "active"),
        "proven_active": sum(1 for f in ff if f["ux_grade"] == "proven" and f["status"] == "active"),
        "candidates": sum(1 for f in ff if f["status"] == "candidate"),
        "new_this_week": new,
        "retired": retired,
        "alerts": ([] if new else ["최근 7일 신규 UX 발굴 0건 — 소싱 워커 발굴 패스 점검 필요"]),
    }
    return audit


# 상품명 블랙리스트 — 이 단어가 상품명에 있으면 후보에서 제외
NAME_BLACKLIST = ["무게 담요", "무게담요", "중량 담요", "멜라토닌", "키성장", "키 성장", "성장기 영양제 키", "성조숙",
                  # 측정도구·잡화 영구 금지 — 학부모는 솔루션에 돈 쓰지, 도구에 안 씀
                  "키재기", "키 재기", "줄자", "스티커", "포스터", "교정기", "마사지기", "문구"]


def _sign(method, path_with_query, access_key, secret_key):
    signed_date = time.strftime("%y%m%dT%H%M%SZ", time.gmtime())
    parts = path_with_query.split("?")
    path, query = parts[0], (parts[1] if len(parts) > 1 else "")
    message = signed_date + method + path + query
    signature = hmac.new(secret_key.encode(), message.encode(), hashlib.sha256).hexdigest()
    return (f"CEA algorithm=HmacSHA256, access-key={access_key}, "
            f"signed-date={signed_date}, signature={signature}")


def _coupang_get(cfg, path_with_query):
    auth = _sign("GET", path_with_query, cfg["coupang"]["access_key"], cfg["coupang"]["secret_key"])
    r = requests.get(COUPANG_HOST + path_with_query, headers={"Authorization": auth}, timeout=15)
    r.raise_for_status()
    return r.json()


def search_products(cfg, keyword, limit=20):
    q = urllib.parse.quote(keyword)
    path = f"/v2/providers/affiliate_open_api/apis/openapi/products/search?keyword={q}&limit={limit}"
    data = _coupang_get(cfg, path)
    return (data.get("data") or {}).get("productData") or []


def make_deeplink(cfg, url, sub_id):
    path = "/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink"
    auth = _sign("POST", path, cfg["coupang"]["access_key"], cfg["coupang"]["secret_key"])
    r = requests.post(COUPANG_HOST + path,
                      headers={"Authorization": auth, "Content-Type": "application/json"},
                      data=json.dumps({"coupangUrls": [url], "subId": sub_id}), timeout=15)
    r.raise_for_status()
    items = (r.json().get("data") or [])
    return items[0]["shortenUrl"] if items else url


def _blacklisted(name):
    return any(b in name for b in NAME_BLACKLIST) or bool(journey_policy.retired_product_reasons(name))


# 근거·컴플라이언스 승인 권한을 가진 봇 핸들.
# 프로필 개명 이력을 포함한다 — 개명 전 승인된 큐 항목을 소급 무효화하지 않기 위함.
# 현행: haneul-proof (서하늘) / 레거시: mungchi-proof
AUDIT_OWNERS = ("haneul-proof", "mungchi-proof")


def audit_readiness_reasons(result):
    """근거 감사 인계에 필요한 provenance 결손을 반환한다.

    audit_status=approved는 근거 리드의 최종 승인값이다. 승인값이 있더라도
    필수 provenance가 빠진 결과는 발행 큐에서 소비하지 않는다.
    """
    reasons = []
    if result.get("audit_status") != "approved":
        reasons.append("audit_status_not_approved")
    else:
        # 근거·컴플라이언스 승인 봇. 프로필 개명(mungchi-proof -> haneul-proof) 이력을
        # 함께 허용해 개명 전 승인된 큐 항목이 소급 무효화되지 않게 한다.
        if result.get("audited_by") not in AUDIT_OWNERS:
            reasons.append("audit_owner_mismatch")
        if result.get("source_reverified_via") != "aside:u0":
            reasons.append("audit_source_not_reverified_with_aside_u0")
        if not result.get("source_reverified_at"):
            reasons.append("audit_source_reverified_at_missing")
    if not result.get("product_url"):
        reasons.append("product_url_missing")
    if not result.get("collected_at"):
        reasons.append("collected_at_missing")
    if not result.get("sub_id"):
        reasons.append("sub_id_missing")

    if not result.get("friction_id"):
        reasons.append("friction_id_missing")
    pointers = result.get("source_pointers")
    if not pointers:
        reasons.append("friction_source_pointers_missing")
    elif (not isinstance(pointers, list)
          or not all(isinstance(pointer, str) and pointer.strip() for pointer in pointers)):
        reasons.append("friction_source_pointers_invalid")

    price = result.get("price_provenance") or {}
    if not isinstance(price, dict):
        reasons.append("price_provenance_invalid")
        price = {}
    if not price.get("regular_price"):
        reasons.append("regular_price_missing")
    if "variable_price" not in price:
        reasons.append("variable_price_separation_missing")
    if not price.get("source_url"):
        reasons.append("price_source_missing")

    reviews = result.get("review_provenance") or []
    if not isinstance(reviews, list):
        reasons.append("review_provenance_invalid")
        reviews = []
    if not reviews:
        reasons.append("review_provenance_missing")
    else:
        required = ("review_id", "quote", "source_url", "original_location")
        for i, review in enumerate(reviews):
            if not isinstance(review, dict):
                reasons.append(f"review_{i}_invalid")
                continue
            missing = [key for key in required if not review.get(key)]
            if missing:
                reasons.append(f"review_{i}_missing:{','.join(missing)}")

    official = result.get("official_provenance") or []
    if not isinstance(official, list):
        reasons.append("official_provenance_invalid")
        official = []
    if not official:
        reasons.append("official_provenance_missing")
    else:
        required = ("quote", "source_url", "original_location")
        for i, source in enumerate(official):
            if not isinstance(source, dict):
                reasons.append(f"official_{i}_invalid")
                continue
            missing = [key for key in required if not source.get(key)]
            if missing:
                reasons.append(f"official_{i}_missing:{','.join(missing)}")
    if result.get("is_food") and not result.get("approved_claims"):
        reasons.append("approved_claim_original_missing")
    reasons.extend(comparison_readiness_reasons(result))
    return reasons


def is_audit_approved(result):
    return not audit_readiness_reasons(result)


# ── 공식 이미지 provenance 노출 (영상 truth layer 용) ────────────────────────
# 주의: 아래 두 함수는 **읽기 전용 노출 계층**이다. 기존 감사 게이트
# (audit_readiness_reasons / is_audit_approved / 블랙리스트)를 대체하거나
# 완화하지 않는다. 소비자(product_assets.py)가 승인된 결과에서 이미지
# provenance만 꺼내 쓰기 위한 접근자다.

#: 공식 이미지 provenance 항목에 필요한 키 (product_assets 와 동일 계약).
IMAGE_PROVENANCE_KEYS = ("source_url", "market", "product_id", "option",
                         "official_page_url", "rights_basis", "rights_holder",
                         "captured_at")


def approved_image_sources(result):
    """소싱 결과에 담긴 공식 이미지 provenance 목록을 그대로 노출한다."""
    sources = (result or {}).get("official_image_provenance") or []
    return sources if isinstance(sources, list) else []


def image_provenance_reasons(result):
    """공식 이미지 provenance 결손 사유를 반환한다 (빈 리스트면 완전함)."""
    reasons = []
    sources = approved_image_sources(result)
    if not sources:
        reasons.append("official_image_provenance_missing")
        return reasons
    for i, source in enumerate(sources):
        if not isinstance(source, dict):
            reasons.append(f"image_{i}_invalid")
            continue
        missing = [k for k in IMAGE_PROVENANCE_KEYS if not source.get(k)]
        if missing:
            reasons.append(f"image_{i}_missing:{','.join(missing)}")
    return reasons


def comparison_readiness_reasons(result):
    """5개 발굴 → 3개 비교 → 2개 탈락 → 1개 승자 계약의 결손을 반환한다."""
    reasons = []
    if len(result.get("candidate_pool") or []) < 5:
        reasons.append("candidate_pool_lt_5")
    if len(result.get("compared_candidates") or []) < 3:
        reasons.append("compared_candidates_lt_3")
    rejected = [item for item in (result.get("rejected_candidates") or [])
                if item.get("name") and item.get("reason")]
    if len(rejected) < 2:
        reasons.append("rejected_candidates_lt_2")
    if not result.get("winner_reasons"):
        reasons.append("winner_reasons_missing")
    if result.get("winner_count") != 1:
        reasons.append("winner_count_not_1")
    if result.get("judgment_status") != "approved":
        reasons.append("gemini_judgment_not_approved")
    if result.get("judged_by") != "openrouter/google/gemini-3.7-flash":
        reasons.append("gemini_judgment_model_mismatch")
    return reasons


# ── 브라우저 큐 (Aside 소싱 브리지) ─────────────────────────────────────────
# Claude/오토파일럿이 requests.json에 소싱 요청을 쓰고, Aside 루틴(로그인된 쿠팡
# 파트너스 브라우저)이 결과를 results.json에 채운다. 스키마는 aside-sourcing-routine.md 참조.

def _queue_paths(cfg):
    qdir = os.path.join(cfg["paths"]["state_dir"], "browser-queue")
    os.makedirs(qdir, exist_ok=True)
    return (os.path.join(qdir, "requests.json"),
            os.path.join(qdir, "results.json"),
            os.path.join(qdir, "failed.json"))


def top_up_requests(cfg, buffer_target=3):
    """Create requests exclusively from validated friction-ledger demand."""
    req_p, res_p, _ = _queue_paths(cfg)
    reqs = read_json(req_p, [])
    results = read_json(res_p, [])
    used = {h.get("product_key") for h in read_json(state_path(cfg, "sourced_history.json"), [])}
    ready = [r for r in results if r.get("status") == "done"
             and r.get("product_key") not in used and is_audit_approved(r)
             and score_candidate(r)["eligible"]]
    pending = [q for q in reqs if q.get("status") == "pending"]
    need = buffer_target - len(ready) - len(pending)
    added = 0
    used_friction_ids = {q.get("friction_id") for q in reqs}
    available = [s for s in friction.load_signals(state_path(cfg, "friction_signals.jsonl"))
                 if s.get("lifecycle") in {"validated", "active"}
                 and s.get("market") == "KR" and s.get("friction_id") not in used_friction_ids
                 and s.get("sourcing_keyword")]
    available.sort(key=lambda s: (-s["recurrence"], -s["intensity"], s["friction_id"]))
    comparison_contract = {
        "candidate_pool_min": 5,
        "compared_min": 3,
        "rejected_min": 2,
        "winner_count": 1,
    }

    while need > 0 and available:
        signal = available.pop(0)
        request = {
            "id": f"req-{time.strftime('%Y%m%d%H%M%S')}-{added}",
            "status": "pending", "country": "KR",
            "lane": "friction", "friction_id": signal["friction_id"],
            "source_pointers": [signal["source_pointer"]],
            "category": signal["domain"], "keyword": signal["sourcing_keyword"],
            "is_food": bool(signal.get("is_food")),
            "formfactor_id": (signal.get("mechanisms") or [None])[0],
            "friction_solved": signal["verbatim"],
            "comparison_contract": comparison_contract,
            "sub_id": f"{cfg['coupang'].get('sub_id_prefix', 'hc')}-{signal['friction_id']}",
            "needs": ["파트너스 공식 링크 + 요청 subId 필수(누락 시 done 금지)", "리뷰 수",
                      "쿠팡 상품 원페이지 URL + 수집시각",
                      "일반 반복구매가와 쿠폰·와우·첫구매 변동가를 price_provenance로 분리하고 가격 출처 URL 기록",
                      "경쟁·고가 제품의 실제 가격이 언급된 리뷰 우선 수집(있으면 가격 숫자 그대로 — F1 가격역전 포맷 탄약)",
                      "서로 다른 5개 후보군(candidate_pool) 발굴: 유명 고가 기준점·베스트셀러·저가 양산형·UX 혁신형·대체 폼팩터",
                      "5개 중 최소 3개를 compared_candidates로 실제 비교하고 2개 이상을 rejected_candidates(이름/가격/실근거 탈락사유)로 탈락",
                      "winner_count=1과 승자 선정 이유(winner_reasons)를 기록",
                      "폼팩터/UX 혁신 판정: 기존 형태(알약·일반매트·일반의자·일반줄넘기) 대비 부모의 어떤 수고(실랑이·세탁·층간소음)를 없애는 폼팩터인지 1문장을 winner_reasons에 포함. 조건 비슷하면 UX 혁신 폼팩터를 승자로",
                      "실제 리뷰 인용 3개 + 각 문장의 리뷰 식별자·원문 URL·원문 위치를 review_provenance에 기록(효능·신체변화 금지)",
                      "스펙 사실 2~3개 + 각 문장의 공식 원문·URL·상세페이지 위치를 official_provenance에 기록",
                      "식품이면 건강기능식품 인증 여부 + 식약처 인정 기능성 원문 필수(통이미지로 원문 미확보 시 done 금지)",
                      "audit_status는 pending으로 제출(@mungchi-proof만 approved 가능)",
                      "결과에 요청의 formfactor_id·ux_grade 그대로 기록"],
            "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        reqs.append(request)
        added += 1
        need -= 1
    if added:
        write_json(req_p, reqs)
        log(f"소싱 요청 {added}건 추가 → state/browser-queue/requests.json (Aside 루틴이 채움)")
    elif need > 0:
        log("소싱 요청 생성 안 함: 검증된 friction ledger 입력 없음")
    return added


def _rehearsal_fixture_allowed(cfg):
    mode = cfg.get("mode") or {}
    return mode.get("_rehearsal") is True and mode.get("publish") is False


def canonical_queue_product(product, request=None):
    """Build the one trusted KR queue packet used by selection and resolution."""
    packet = {key: value for key, value in product.items() if not str(key).startswith("_")}
    request = request or {}
    for key in ("formfactor_id", "ux_grade"):
        if not packet.get(key) and request.get(key):
            packet[key] = request[key]
    if not packet.get("country"):
        packet["country"] = "KR"
    return packet


def queue_product_input_id(product, request=None):
    """Immutable identity for the canonical audited queue packet."""
    packet = canonical_queue_product(product, request)
    raw = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"queue_product:{packet['product_key']}:{hashlib.sha256(raw).hexdigest()}"


def pick_us(cfg, dry_run=False, min_interval_days=7):
    """US 판매글 소재 선택 — Company OS Supabase 실행 SSOT.

    실운영은 원자적 RPC claim만 사용한다. Supabase 장애나 활성 상품 부재 시
    로컬 JSON으로 폴백하지 않는다. 오래된 상품을 발행하는 것보다 슬롯을
    fail-closed 하는 것이 승인·근거 계약에 안전하다.
    """
    if _rehearsal_fixture_allowed(cfg):
        from generation_ssot import REHEARSAL_PRODUCTS
        return dict(REHEARSAL_PRODUCTS["us-front-open-storage"])
    if dry_run or (cfg.get("mode") or {}).get("_rehearsal"):
        log("US 소싱(dry): 리허설 경계 밖에서는 상품 픽스처를 사용하지 않음")
        return None
    import companyos
    product = companyos.claim_us_product(cfg)
    if product:
        log(f"US 소싱(Company OS): {product.get('product_name', '')[:40]}")
    else:
        log("US 판매 소재 없음 (승인+오퍼+랜딩 검증+쿨다운 게이트) → 오늘 US 판매글 건너뜀")
    return product


def _pick_from_queue(cfg):
    req_p, res_p, _ = _queue_paths(cfg)
    results = read_json(res_p, [])
    history = read_json(state_path(cfg, "sourced_history.json"), [])
    used = {h.get("product_key") for h in history}
    for r in results:
        if (r.get("status") == "done" and r.get("product_key")
                and r["product_key"] not in used
                and not _blacklisted(r.get("product_name", ""))
                and is_audit_approved(r)
                and score_candidate(r)["eligible"]):
            reqs = read_json(req_p, [])
            request = next((q for q in reqs if q.get("id") == r.get("request_id")), None)
            packet = canonical_queue_product(r, request)
            packet["_generation_input_id"] = queue_product_input_id(r, request)
            if not cfg["mode"].get("_rehearsal"):
                history.append({"product_key": r["product_key"], "ts": time.strftime("%Y-%m-%d")})
                write_json(state_path(cfg, "sourced_history.json"), history)
                for q in reqs:
                    if q.get("id") == r.get("request_id"):
                        q["status"] = "consumed"
                write_json(req_p, reqs)
                _mark_sourced(cfg, packet)
            log(f"소싱(브라우저 큐): {packet.get('product_name', '')[:40]}")
            return packet
    return None


def pick(cfg, dry_run=False):
    """오늘의 판매글 상품 1개를 고른다. 반환: product dict 또는 None."""
    if _rehearsal_fixture_allowed(cfg):
        from generation_ssot import REHEARSAL_PRODUCTS
        return dict(REHEARSAL_PRODUCTS["kr-front-open-storage"])
    if dry_run or (cfg.get("mode") or {}).get("_rehearsal"):
        log("KR 소싱(dry): 리허설 경계 밖에서는 상품 픽스처를 사용하지 않음")
        return None

    # 1순위: 브라우저 큐(Aside)가 채운 결과
    queued = _pick_from_queue(cfg)
    if queued:
        return queued

    log("소싱: 검증된 friction + 저관여 점수 + 감사 승인을 모두 통과한 큐 후보 없음")
    top_up_requests(cfg)
    return None
