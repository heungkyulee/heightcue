#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
heightcue 포스트 검사기 — 바이럴 포맷 중심 (v3)

역할이 바뀌었다 (운영자 요청):
  이 검사기의 본업은 '쓰레드에서 먹히는 포맷인가'를 보는 것이다. 규제 검사가 아니다.

  1) 바이럴 포맷 검사 (본업) — 훅·길이·구조·CTA를 점검하고 포맷 점수를 낸다.
     반려(FAIL)는 딱 하나: 500자 초과 (Threads가 게시 자체를 거부하므로).
     나머지 포맷 지적은 전부 개선 제안이다.

  2) 리스크 메모 (참고용, 차단 없음) — 고지 누락·키 표현·반사실 프레임 등 법적 신호를
     한 줄씩 승인 화면에 표시만 한다. 게시 여부는 승인자(운영자)가 판단한다.

사용법:
  python3 post_check.py posts.json            # 검사
  python3 post_check.py posts.json --test     # expect(포맷 PASS/FAIL) + expect_notes(some/none) 대조

입력 형식은 기존과 동일: [{"id","country","post_type","text","product":{...}}, ...]
"""

import json
import re
import sys

# ═══════════════════════════════ 1. 바이럴 포맷 검사 ═══════════════════════════════

MAX_LEN = 500          # Threads 하드 리밋 — 초과 시 게시 불가 (유일한 FAIL)
SWEET_MIN, SWEET_MAX = 120, 480   # 권장 분량
HOOK_MAX = 70          # 훅(1행)이 이보다 길면 피드·알림에서 잘림
KR_DISCLOSURE = "쿠팡 파트너스 활동의 일환으로"
KR_DISCLOSURE_EXACT = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."

RE_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF\U00002190-\U000021FF]"
)
RE_NUMBERED_MARKER = re.compile(r"(^|\s)(?:[①②③④⑤⑥⑦⑧⑨⑩]|\d+[.)])\s*")
RE_ANY_LINK = re.compile(r"\[쿠팡|\[amazon|\[아마존|https?://", re.I)
RE_CTA = re.compile(r"👉|링크|link|where to buy|breakdown|→", re.I)

RE_COUPANG_LINK = re.compile(r"\[쿠팡|link\.coupang\.com|coupang\.com", re.I)
RE_SKIP_IF = re.compile(r"비추천|사지\s*마세요|skip if|skip this|not for you", re.I)

# 훅에 있으면 좋은 신호들 (숫자·인용·질문·대비·직격)
HOOK_SIGNALS = [
    (r"\d", "숫자"),
    (r"[\"“'‘]", "인용"),
    (r"\?", "질문"),
    (r"—|vs|근데|대신|아니라|not|but|instead", "대비/반전"),
    (r"걸렀|rejected|비교|compared|뜯어|제일|most|1위|공통", "선별/최상급"),
]


def first_nonempty_lines(text, n=1):
    out = []
    for line in text.splitlines():
        if line.strip():
            out.append(line.strip())
            if len(out) >= n:
                break
    return out


def format_check(text, post_type, country):
    """포맷 FAIL 목록과 개선 제안 목록, 훅 신호를 돌려준다."""
    fails, tips = [], []
    lines = first_nonempty_lines(text, 3)
    hook = lines[0] if lines else ""

    # 길이 — 유일한 하드 리밋
    n = len(text)
    if n > MAX_LEN:
        fails.append(f"길이 {n}자 > {MAX_LEN}자 — Threads가 게시를 거부함. 잘라야 함")
    elif n > SWEET_MAX:
        tips.append(f"길이 {n}자 — 잘리기 직전. {SWEET_MAX}자 이하 권장")
    elif post_type == "sales" and n < SWEET_MIN:
        tips.append(f"길이 {n}자 — 판매글치고 얇음. 검증 근거가 빠졌는지 확인")

    # 훅 (1행)
    signals = [name for pat, name in HOOK_SIGNALS if re.search(pat, hook, re.I)]
    if not hook:
        tips.append("훅 없음 — 첫 줄이 비어 있음")
    else:
        if len(hook) > HOOK_MAX:
            tips.append(f"훅이 {len(hook)}자 — 피드·알림에서 잘림. {HOOK_MAX}자 이하로")
        if KR_DISCLOSURE in hook:
            tips.append("첫 줄이 고지 문구 — 훅을 1행에, 고지는 2행에 (도달 손실)")
        if RE_ANY_LINK.search(hook):
            tips.append("첫 줄에 링크 — 훅이 먼저, 링크는 마지막에")
        if not signals:
            tips.append("훅 신호 약함 — 숫자·인용·질문·대비·선별 중 하나는 들어가야 세다")

    # 구조
    emoji_n = len(RE_EMOJI.findall(text))
    if country in {"KR", "US"} and emoji_n:
        tips.append(f"{country} 보이스에 이모지 {emoji_n}개 — 텍스트 전용 규칙 위반")
    elif emoji_n > 3:
        tips.append(f"이모지 {emoji_n}개 — 0~2개 권장 (많으면 광고 티)")
    if country in {"KR", "US"} and RE_NUMBERED_MARKER.search(text):
        tips.append(f"{country} 보이스에 번호 목록 — 친구에게 문자하듯 평문으로 풀어쓰기")
    if text.count("#") > 3:
        tips.append("해시태그 남용 — Threads는 태그 스팸을 눌러버림")
    if post_type == "sales":
        if not RE_ANY_LINK.search(text):
            tips.append("판매글인데 링크 없음")
        elif not RE_CTA.search(text):
            tips.append("CTA(👉 등) 없음 — 링크만 덜렁 있으면 클릭이 죽음")
        if not RE_SKIP_IF.search(text):
            tips.append("비추천(skip if) 슬롯 없음 — 비토권이 신뢰를 만든다. 한 줄 추가 권장")
    body_lines = [l for l in text.splitlines() if l.strip()]
    if n > 250 and len(body_lines) <= 2:
        tips.append("줄바꿈 부족 — 벽글은 스크롤에서 죽는다. 2~3줄 단위로 끊기")

    score = max(0, 100 - 15 * len(fails) - 8 * len(tips))
    return fails, tips, signals, score


# ═════════════════════════ 2. 리스크 메모 (참고용, 차단 없음) ═════════════════════════

RE_US_AD = re.compile(r"(^|\s)#ad\b", re.I)
RE_PAID_LINK = re.compile(r"\(paid link\)", re.I)
RE_AMAZON_LINK = re.compile(r"\[amazon|amazon\.com|amzn\.to|\[아마존", re.I)

RISK_KR = [
    (r"키\s*성장|키성장|키\s*크(는|려|기)|키(가|는|도)\s*(커|큰|크|자라)|숨은\s*키", "키 표현 — 식품이면 식품표시광고법 형사 조항 영역"),
    (r"성장\s*호르몬|성장판", "성장호르몬/성장판 — 식약처 단속 1순위 표현"),
    (r"성장\s*지연|저신장|거북목|척추측만", "질환명 — 의료 효능 오인 소지"),
    (r"백분위", "백분위 — 키 결과 프레임"),
    (r"통잠", "통잠 — 효과 단정 소지"),
    (r"임상(적으로)?\s*(입증|증명)", "임상 입증 주장 — 실증 자료 필요"),
    (r"(의사|약사|전문의|소아과)\s*추천", "전문가 추천 표방"),
    (r"돌아간다면|시절로\s*돌아|15년\s*전으로|(먹었|썼|있었|해줬|받았|샀)더라면|(먹었|샀|썼|해줬)을\s*(거|텐데|것)", "반사실 구매 프레임 — 제품과 결합 시 암시 광고"),
    (r"혹시.{0,12}(부족|결핍)", "결핍 암시 훅 — 미인정 기능성 표방 적발 유형"),
    (r"골든\s*타임|더\s*늦기\s*전에|늦으면\s*후회", "성장 시한·공포 프레임 — 식품·키 맥락이면 위험"),
    (r"(아이|애)가?\s*원망", "아이의 미래 원망 협박 — 죄책감 조성 광고"),
    (r"(병원|약국|의원|제약사|제약회사).{0,8}(납품|공급|처방)|(의사|소아과|전문의)들?(이|가).{0,8}(쓰는|나눠|처방|권하는)", "권위 주장(납품·처방·사용 이력) — 소싱 데이터에 근거 있는지 확인 필수"),
    (r"똑같은\s*효과|동일한\s*효(과|능)|같은\s*효능|효과는\s*같", "효능 동치 주장 — 스펙 동치(같은 소재·구조)로만 쓸 것"),
    (r"품절\s*임박|오늘만|마감\s*임박|서두르세요|끝나기\s*전에", "긴급성 표현 — 화면에서 실제 확인된 조건인지 검증 필요"),
    (r"(우리|저희|제)\s*(집|애|아이|아들|딸|첫째|둘째|막내)", "가족 언급 — 실화(사실)인지만 확인"),
    (r"먹여\s*봤|먹였더니|먹이니|발라\s*봤|재워\s*봤|써\s*봤더니|정착했(?!다는|다고)", "체험담 화법 — 실사용 사실인지만 확인"),
    (r"무게\s*담요", "아동 무게담요 — 리콜·사망 사례 제품"),
    (r"멜라토닌", "멜라토닌 — 아동 투여 민감"),
]
RISK_US = [
    (r"gr[eo]w\s+taller|gets?\s+taller|height\s+growth|growth\s+spurt|growth\s+hormone|growth\s+plate|bone\s+growth", "height/growth claim — FTC substantiation territory"),
    (r"percentile", "percentile — outcome framing"),
    (r"clinically\s+proven", "'clinically proven' — needs RCT-level evidence"),
    (r"true\s+height|hidden\s+inch", "hidden-height claim"),
    (r"appetite", "appetite claim"),
    (r"(doctor|pediatrician)[- ]recommended", "expert endorsement"),
    (r"if\s+i\s+could\s+go\s+back|wish\s+(my\s+parents|i|we)\s+had|would\s+have\s+(bought|taken|used|given)", "counterfactual-purchase framing"),
    (r"\bmy\s+(son|daughter|kid|kids|child|children|toddler)\b|\bmy\s+\d+[- ]?year[- ]?old\b", "family reference — verify it's real"),
    (r"weighted\s+blanket", "kids' weighted blanket — recall & fatality history"),
    (r"melatonin", "melatonin — sensitive for kids"),
    (r"behind\s+the\s+curve|golden\s+window|before\s+it'?s\s+too\s+late", "growth-window fear framing"),
    (r"(clinics?|hospitals?|pharmac\w+|doctors?|pediatricians?)\s+\w*\s*(dispense|stock|prescribe|hand\s+out)", "authority claim (dispense/prescribe) — must exist in sourcing data"),
    (r"same\s+(effect|results?|benefits?)|works\s+the\s+same", "efficacy-equivalence claim — compare specs, not effects"),
    (r"today\s+only|selling\s+out|before\s+(it'?s|the\s+deal\s+is)\s+gone|deal\s+ends", "urgency claim — verify it's real on screen"),
    (r"most\s+(of\s+them|kids?'?\s+vitamin\w*|brands?)\s+.*\b(syrup|candy)\b", "unsupported market-wide comparison — requires a defined comparison set and saved label evidence"),
    (r"kids?\s+(do(?:es)?n'?t|won'?t)\s+(even\s+)?notice", "generalized child-experience claim — requires attributable review evidence"),
    (r"bone\s+foundation", "vague supplement health implication — use only substantiated label/approved claim wording"),
]
SOURCE_DEFICIENT_KR = [
    (r"타사|다른\s+(?:제품|브랜드|제형)", "source-deficient competitor comparison — 비교 대상 근거가 저장되지 않음"),
]
SOURCE_DEFICIENT_US = [
    (r"\bother\s+(?:options?|products?|brands?)\b.{0,48}\b(?:require|use|need|have|come\s+with)\b", "source-deficient market comparison — 'other options' claims are blocked until a defined comparison set is saved"),
    (r"\bmost\s+of\s+them\b", "source-deficient market comparison — 'Most of them' is blocked until a defined comparison set is saved"),
    (r"\bmost\s+kids?'?\s+vitamin\s+d?\s*gumm(?:y|ies)\b", "source-deficient market comparison — competing gummy labels are not saved"),
    (r"\bkids?\s+don'?t\s+even\s+notice\b", "source-deficient child-experience claim — no attributable review quote is saved"),
    (r"\bbuilds?\s+bone\s+foundation\b", "unsupported supplement implication — no approved claim supports this wording"),
    (r"\bpure\b", "unqualified purity claim — exact purity substantiation is not saved"),
    (r"\bled\s+the\s+league\s+in\s+steals\b", "story-source gap — story bank supports captain/city-title facts, not a league steals record"),
]
DDROPS_UNSUPPORTED = [
    (r"\b(?:other|alternative)\s+(?:formats?|options?|products?|brands?)\b", "Ddrops evidence boundary — competitor formats are not in the saved label-fact bundle"),
    (r"\bmultiple\s+(?:doses?|servings?|drops?|measurements?)\b", "Ddrops evidence boundary — multi-dose comparison is not in the saved label-fact bundle"),
    (r"\bcomplex\s+(?:extras?|ingredients?|additives?)\b", "Ddrops evidence boundary — competitor ingredient complexity is not in the saved label-fact bundle"),
    (r"\bbloated\s+(?:formulas?|ingredient\s+lists?)\b", "Ddrops evidence boundary — formula complexity comparison is not in the saved label-fact bundle"),
    (r"\b(?:filler\s+lists?|measur(?:e|ing)\s+spoons?|(?:daily\s+)?vitamin\s+d\s+intake|(?:another|existing)\s+routine)\b", "Ddrops evidence boundary — convenience/intake comparison is not in the saved label-fact bundle"),
    (r"\b(?:syrups?|syringes?|gumm(?:y|ies)|chewables?|tablets?|capsules?|multivitamins?|powders?|sprays?|droppers?|additives?)\b", "Ddrops evidence boundary — competitor form-factor claims are not in the saved label-fact bundle"),
    (r"\bvitamin\s+d\s+from\s+food\b", "Ddrops evidence boundary — dietary sufficiency is not in the saved label-fact bundle"),
    (r"\b(?:adequate|enough)\s+vitamin\s+d3?\b", "Ddrops evidence boundary — vitamin D sufficiency is not in the saved label-fact bundle"),
    (r"\bzero\s+sugar\b|\bno\s+sugar\b|\bno\s+syrup\b|\bsyrup[- ]free\b", "Ddrops evidence boundary — sugar/syrup claim is not in the saved label-fact bundle"),
    (r"\bno\s+mess\b|\bmeasur(?:e|ing)\s+(?:cup|mess)\b|\bzero\s+measuring\b", "Ddrops evidence boundary — convenience claim is not in the saved label-fact bundle"),
    (r"\b(?:mix|go(?:es)?)\s+(?:it\s+)?(?:right\s+)?into\s+food\b|\bin\s+food\b", "Ddrops evidence boundary — food-use wording is not in the saved label-fact bundle"),
    (r"\b(?:battle|standoff)s?\b", "Ddrops evidence boundary — adherence/battle outcome is not in the saved label-fact bundle"),
    (r"\b(?:bone|absorp\w*|adherence)\b", "Ddrops evidence boundary — health/adherence implication exceeds the two saved label facts"),
]
RE_QUOTED = re.compile(r"(?:[\"“][^\"”]{0,160}[\"”]|['‘][^'’]{0,160}['’])")


def _norm_evidence(value):
    """원문 대조용 정규화: 공백·문장부호 차이만 제거하고 내용은 바꾸지 않는다."""
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())


def review_evidence_notes(text, product):
    """리뷰로 귀속한 따옴표 문장이 저장된 리뷰 원문에 실제 존재하는지 확인한다."""
    if not product.get("product_key"):
        return []  # 레거시 픽스처·가치글처럼 제품 증거 묶음이 아닌 입력
    sources = [_norm_evidence(q) for q in (product.get("review_quotes") or []) if q]
    notes = []
    for match in RE_QUOTED.finditer(text):
        start, end = match.span()
        nearby = text[max(0, start - 80):min(len(text), end + 80)]
        if not re.search(r"리뷰|후기|review", nearby, re.I):
            continue
        quoted = match.group(0)[1:-1].strip()
        needle = _norm_evidence(quoted)
        if len(needle) >= 4 and not any(needle in source for source in sources):
            notes.append(f"리뷰 인용 원문 결손 — 저장된 review_quotes에서 정확한 문구를 찾지 못함 → «{quoted}»")
    return notes


def evidence_boundary_notes(text, country, product):
    """Return unsupported-evidence notes without post structure/disclosure checks."""
    notes = []
    for pat, label in (SOURCE_DEFICIENT_US if country == "US" else SOURCE_DEFICIENT_KR):
        m = re.search(pat, text, re.I)
        if m:
            notes.append(f"{label} → «{m.group(0).strip()}»")
    product_key = str(product.get("product_key") or "").lower()
    if country == "US" and "ddrops" in product_key:
        for pat, label in DDROPS_UNSUPPORTED:
            m = re.search(pat, text, re.I)
            if m:
                notes.append(f"{label} → «{m.group(0).strip()}»")
    notes.extend(review_evidence_notes(text, product))
    return notes


def risk_notes(text, country, post_type, product):
    """참고용 리스크 메모. 어떤 경우에도 차단하지 않는다."""
    notes = evidence_boundary_notes(text, country, product)

    product_key = str(product.get("product_key") or "").lower()
    if country == "US" and "ddrops" in product_key and post_type == "sales":
        skip_match = re.search(r"(?im)^\s*skip if:\s*\S.{8,}$", text)
        if not skip_match:
            notes.append("Ddrops evidence boundary — a natural 'skip if:' non-fit line is required")
        elif not re.search(r"exact\s+label|fractionated\s+coconut\s+oil", skip_match.group(0), re.I):
            notes.append("Ddrops evidence boundary — skip if must be based on the exact label or fractionated coconut oil")
    has_link = bool(RE_ANY_LINK.search(text))
    commercial = (post_type == "sales") or has_link
    if not commercial:
        return notes  # 링크 없는 가치글은 광고가 아니므로 메모 대상 아님

    # 고지 상태. US 판매글은 자사 사이트 경유여도 추천 자체가 제휴 퍼널이므로 첫 줄 #ad를 요구한다.
    if country == "KR":
        if RE_COUPANG_LINK.search(text):
            head = first_nonempty_lines(text, 2)
            if len(head) < 2 or head[1] != KR_DISCLOSURE_EXACT:
                notes.append("쿠팡 직링크 고지가 둘째 줄의 불변 문구와 정확히 일치하지 않음 — 위치·문구 수정 금지")
    else:
        first = (first_nonempty_lines(text, 1) or [""])[0]
        if post_type == "sales" and not re.search(r"#ad\s*$", first, re.I):
            notes.append("US 판매 추천인데 훅 행 끝 #ad가 없음 — 자사 가이드 경유도 제휴 퍼널이므로 고지 위치 고정")
        if RE_AMAZON_LINK.search(text):
            if post_type != "sales" and not RE_US_AD.search(first):
                notes.append("아마존 링크가 글에 있는데 #ad 미표시(첫 줄 기준) — FTC 고지 요건")
            if not RE_PAID_LINK.search(text):
                notes.append("(paid link) 미표기")

    unquoted = RE_QUOTED.sub(" ", text)
    for pat, label in (RISK_KR if country == "KR" else RISK_US):
        target = unquoted if ("가족" in label or "체험담" in label or "family" in label) else text
        m = re.search(pat, target, re.I)
        if m:
            suffix = ""
            if product.get("is_food") and ("키" in label or "성장" in label or "growth" in label.lower()):
                suffix = " [식품 — 리스크 최상급]"
            notes.append(f"{label}{suffix} → «{m.group(0).strip()}»")
    if product.get("is_food") and not product.get("is_certified_health_food") and re.search(r"건강기능식품", text):
        notes.append("인증 없는 일반식품의 건강기능식품 표방 → 확인 필요")
    return notes


# ═══════════════════════════════════ 실행 ═══════════════════════════════════

def check_post(post):
    text = post.get("text", "")
    country = post.get("country", "KR").upper()
    post_type = post.get("post_type", "sales")
    product = post.get("product", {}) or {}

    fails, tips, signals, score = format_check(text, post_type, country)
    notes = risk_notes(text, country, post_type, product)
    return {
        "id": post.get("id", "?"),
        "verdict": "FAIL" if fails else "PASS",   # FAIL = 게시 불가(500자 초과)뿐
        "format_score": score,
        "hook_signals": signals,
        "format_fails": fails,
        "format_tips": tips,
        "risk_notes": notes,                       # 참고용 — 차단 없음
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    test_mode = "--test" in sys.argv
    with open(sys.argv[1], encoding="utf-8") as f:
        posts = json.load(f)

    mismatches = 0
    for post in posts:
        r = check_post(post)
        mark = ""
        if test_mode:
            ok = r["verdict"] == post.get("expect", "PASS")
            want_notes = post.get("expect_notes")
            if want_notes == "some":
                ok = ok and len(r["risk_notes"]) > 0
            elif want_notes == "none":
                ok = ok and len(r["risk_notes"]) == 0
            want_tips = post.get("expect_tips")
            if want_tips == "some":
                ok = ok and len(r["format_tips"]) > 0
            elif want_tips == "none":
                ok = ok and len(r["format_tips"]) == 0
            mark = "  ✓ 예상대로" if ok else "  ✗ 예상과 다름!"
            if not ok:
                mismatches += 1
        sig = f" 훅신호[{','.join(r['hook_signals'])}]" if r["hook_signals"] else ""
        print(f"[{r['verdict']}] {r['id']}  포맷 {r['format_score']}점{sig}{mark}")
        for v in r["format_fails"]:
            print(f"    ✖ {v}")
        for v in r["format_tips"]:
            print(f"    ▲ {v}")
        for v in r["risk_notes"]:
            print(f"    ~ 참고: {v}")

    if test_mode:
        total = len(posts)
        print(f"\n회귀 테스트: {total - mismatches}/{total} 일치")
        sys.exit(0 if mismatches == 0 else 1)


if __name__ == "__main__":
    main()
