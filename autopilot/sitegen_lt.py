# -*- coding: utf-8 -*-
"""링크트리 스타일 렌더러 — sitegen이 사용. (제품 페이지 + kr 허브 공용)

디자인 원칙: Linktree 문법 그대로 — 단색 배경, 중앙 아바타, 이름/바이오,
흰색 필 버튼 스택. 페이지당 버튼 4~5개, 긴 본문 없음. 상세 근거는 접이식 카드 1개.
"""
import html
import json
import os
import time

SITE_BASE = "https://heightcue.lifoli.co.kr"
KR_DISCLOSURE = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
THREADS_URL = "https://www.threads.com/@heightcue"

NAME = "heightcue | 167cm 팩트폭격기"
BIO = "성분표 뜯어보고 돈값 하는 것만 남깁니다."


def esc(s):
    return html.escape(str(s or ""), quote=True)


def display_name(name):
    return str(name or "").split(",")[0].strip()


def _head(title, desc, canonical):
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<meta property="og:title" content="{title}">
<meta property="og:type" content="website">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{SITE_BASE}/assets/brand/heightcue-pfp.jpg">
<link rel="canonical" href="{canonical}">
<link rel="icon" type="image/png" href="{SITE_BASE}/assets/brand/heightcue-a2.png">
<link rel="stylesheet" href="{SITE_BASE}/kr/lt.css?v=2">
<script defer src="{SITE_BASE}/app.js"></script>
<script defer src="/analytics.js"></script></head>
<body><div class="col">
<img class="avatar" src="{SITE_BASE}/assets/brand/heightcue-pfp.jpg" alt="{esc(NAME)}">
<div class="name">{esc(NAME)}</div>
<div class="bio">{esc(BIO)}</div>
<div class="disc">{KR_DISCLOSURE}</div>
<div class="links">"""


def _foot(fine_extra=""):
    return f"""</div>
<div class="fine">이 페이지는 특정 제품이 아이의 키·성장에 영향을 준다고 주장하지 않습니다. {fine_extra}구매·배송·반품·CS는 쿠팡 및 판매자가 담당합니다. · <a href="{SITE_BASE}/disclosure.html">고지</a> · <a href="{SITE_BASE}/privacy.html">개인정보</a></div>
<div class="brandfoot">HeightCue</div>
</div></body></html>
"""


def _price_line(price_info):
    """price_info(dict|str) → (표시 문자열, 쿠폰 문자열)."""
    if isinstance(price_info, dict):
        now = price_info.get("discounted_price_krw")
        pct = price_info.get("discount_pct")
        coupon = price_info.get("first_purchase_coupon_krw")
        parts = []
        if now:
            parts.append(f"{now:,}원")
        if pct:
            parts.append(f"{pct}% 할인")
        coupon_s = f"첫구매 쿠폰 {coupon:,}원 별도" if coupon else ""
        return " · ".join(parts), coupon_s
    return (str(price_info) if price_info else ""), ""


def render_product(product, master):
    """제품 랜딩 — 링크트리 스타일. 슬롯은 전부 소싱/마스터 데이터에서만."""
    name = esc(display_name(product.get("product_name")))
    key = esc(product.get("product_key"))
    link = esc(product.get("link"))
    slug_ = product.get("_slug")
    canonical = f"{SITE_BASE}/kr/p/{slug_}.html"
    collected = str(product.get("collected_at") or time.strftime("%Y-%m-%d"))[:10]
    price_s, coupon_s = _price_line(product.get("price_info"))
    review_count = product.get("review_count")

    sub_bits = [b for b in (price_s, coupon_s) if b]
    sub = f'<span class="sub">{esc(" · ".join(sub_bits))}</span>' if sub_bits else ""

    skip_if = [s for s in (master.get("skip_if") or []) if s][:1]
    quotes = [q for q in (product.get("review_quotes") or []) if q]
    # 대표 인용은 구매 전환·긍정 맥락 우선 — 부정 리뷰는 비추천 줄이 담당한다 (정직성은 유지, 위치만 분리)
    neg = ("아쉽", "불편", "가루", "환불", "별로", "실망")
    pos_quotes = [q for q in quotes if not any(n in q for n in neg)]
    pool = pos_quotes or quotes
    quote = min(pool, key=len) if pool else ""
    if len(quote) > 90:
        quote = quote[:88].rstrip() + "…"

    src_bits = []
    if review_count:
        src_bits.append(f"쿠팡 구매자 리뷰 {int(review_count):,}개")
    src_bits.append(f"{collected} 확인")
    proof = (f'<div class="proof"><p>“{esc(quote)}”</p>'
             f'<div class="src">{esc(" · ".join(src_bits))}</div></div>') if quote else ""
    skipline = (f'<div class="skipline">비추천: {esc(skip_if[0])}</div>' if skip_if else "")

    body = (f'<a class="lbtn" data-track="coupang-{key}" data-market="KR" data-product-key="{key}" '
            f'href="{link}" target="_blank" rel="sponsored nofollow noopener noreferrer">{name}{sub}</a>'
            f'{proof}{skipline}'
            f'<a class="lbtn hollow" href="{SITE_BASE}/kr/">다른 추천템 보기</a>'
            f'<a class="lbtn hollow" href="{THREADS_URL}" target="_blank" rel="noopener">Threads 팔로우 — @heightcue</a>')

    fine_extra = f"가격·재고·리뷰는 {esc(collected)} 확인 기준이며 게시 시점과 다를 수 있습니다. "
    return (_head(f"{name} | HeightCue", f"{name} — 리뷰 원문과 스펙 근거로 정리한 추천.", canonical)
            + body + _foot(fine_extra))


def render_hub(catalog):
    """kr 허브(바이오 링크 목적지) — 최근 제품 버튼 스택."""
    canonical = f"{SITE_BASE}/kr/"
    btns = []
    for it in catalog[:8]:
        sub_bits = [b for b in (it.get("price", ""), it.get("category_ko", "")) if b]
        sub = f'<span class="sub">{esc(" · ".join(sub_bits))}</span>' if sub_bits else ""
        btns.append(f'<a class="lbtn" href="{SITE_BASE}/kr/p/{esc(it["slug"])}.html">{esc(it["name"])}{sub}</a>')
    body = ("".join(btns)
            + f'<a class="lbtn hollow" href="{THREADS_URL}" target="_blank" rel="noopener">Threads 팔로우 — @heightcue</a>')
    return (_head("heightcue | 167cm 팩트폭격기", "성분표 뜯어보고 돈값 하는 것만 남깁니다.", canonical)
            + body + _foot())


CATEGORY_KO = {"sleep": "수면 환경", "posture": "자세 환경", "nutrition": "영양", "exercise": "운동 습관"}


def update_catalog(repo, product, slug_):
    """kr/p/catalog.json 업서트 → 허브 재생성. 반환: 갱신된 카탈로그."""
    path = os.path.join(repo, "kr", "p", "catalog.json")
    try:
        with open(path, encoding="utf-8") as f:
            catalog = json.load(f)
    except Exception:
        catalog = []
    price_s, _ = _price_line(product.get("price_info"))
    entry = {
        "slug": slug_,
        "name": display_name(product.get("product_name")),
        "price": price_s,
        "category_ko": CATEGORY_KO.get(product.get("category"), ""),
        "ts": time.strftime("%Y-%m-%d"),
    }
    catalog = [c for c in catalog if c.get("slug") != slug_]
    catalog.insert(0, entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=1)
    with open(os.path.join(repo, "kr", "index.html"), "w", encoding="utf-8") as f:
        f.write(render_hub(catalog))
    return catalog
