#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render the active HeightCue friction-commerce static journey."""

from __future__ import annotations

import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import urlparse

import journey_policy


class SiteJourneyError(RuntimeError):
    pass


SITE_BASE = "https://heightcue.lifoli.co.kr"


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _shell(*, market: str, title: str, description: str, canonical: str, body: str) -> str:
    lang = "ko" if market == "KR" else "en"
    locale_path = "kr" if market == "KR" else "us"
    other_path = "us" if market == "KR" else "kr"
    other_label = "English" if market == "KR" else "한국어"
    return f"""<!doctype html>
<html lang="{lang}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><meta name="description" content="{description}">
<link rel="canonical" href="{canonical}"><link rel="stylesheet" href="/journey.css?v=1">
<script defer src="/analytics.js"></script></head><body>
<header class="top"><a class="wordmark" href="/{locale_path}/">HeightCue</a><nav><a href="/{locale_path}/">{ '홈' if market == 'KR' else 'Home'}</a><a href="/{other_path}/">{other_label}</a></nav></header>
{body}
<footer><a href="/{locale_path}/#categories">{ '전체 카테고리' if market == 'KR' else 'All categories'}</a><a href="/measurement/">{ '키 측정 교육 자료' if market == 'KR' else 'Height measurement education'}</a><a href="/disclosure.html">{ '고지' if market == 'KR' else 'Disclosures'}</a></footer>
</body></html>"""


def _category_nav(market: str) -> str:
    locale = market.lower()
    items = []
    for key, data in journey_policy.PUBLIC_CATEGORIES.items():
        items.append(f'<a class="category" href="/{locale}/c/{key}.html"><span>{data[market]}</span><b>→</b></a>')
    return '<div class="category-grid">' + "".join(items) + "</div>"


def _landing_relative(product: dict) -> str:
    parsed = urlparse(str(product.get("landing_url", "")))
    if parsed.scheme != "https" or parsed.netloc != "heightcue.lifoli.co.kr":
        raise SiteJourneyError("landing_host_mismatch")
    relative = parsed.path.lstrip("/")
    market = str(product.get("market", "")).lower()
    if not relative.startswith(f"{market}/") or not relative.endswith(".html") or ".." in relative:
        raise SiteJourneyError("landing_path_mismatch")
    return relative


def _product_card(product: dict, market: str) -> str:
    name = html.escape(str(product["product_name"]))
    mechanism = html.escape(str(product["mechanism"]).replace("_", " "))
    failure = html.escape(str(product["failure_mode"]).replace("_", " "))
    skip_if = html.escape(str(product["skip_if"]))
    url = "/" + _landing_relative(product)
    key = html.escape(str(product["product_key"]), quote=True)
    is_kr = market == "KR"
    disclosure = journey_policy.AFFILIATE_DISCLOSURES["KR" if is_kr else "US_ACCOUNT"]
    label = "판정 보기" if is_kr else "Check current listing"
    ad = "" if is_kr else '<span class="ad">#ad</span>'
    return f"""<article class="verdict" data-product-key="{key}">{ad}<p class="card-kicker">{'반복 마찰 판정' if is_kr else 'FRICTION VERDICT'}</p><h3>{name}</h3><dl><div><dt>{'줄이는 동작' if is_kr else 'Mechanism'}</dt><dd>{mechanism}</dd></div><div><dt>{'반복된 실패' if is_kr else 'Failure mode'}</dt><dd>{failure}</dd></div><div><dt>{'사지 말아야 할 때' if is_kr else 'Skip if'}</dt><dd>{skip_if}</dd></div></dl><a class="button" href="{url}" data-track="{html.escape(str(product['tracking_key']), quote=True)}">{label}</a><p class="affiliate">{html.escape(disclosure)}</p></article>"""


def _eligible_packet(product: dict) -> bool:
    required = (
        "product_key", "product_name", "market", "category", "friction_id", "mechanism",
        "failure_mode", "skip_if", "workflow_state", "evidence_revision", "approved_revision",
        "offer_id", "tracking_key", "landing_url", "affiliate_url", "source_pointers",
    )
    missing = [key for key in required if product.get(key) in (None, "", [])]
    if missing:
        raise SiteJourneyError("product_packet_missing:" + ",".join(missing))
    mapped = journey_policy.map_category(product.get("category"))
    if not mapped:
        raise SiteJourneyError("unsupported_friction_category")
    policy = journey_policy.product_eligibility(product)
    hard = [reason for reason in policy["reasons"] if reason != "unsupported_friction_category"]
    if hard:
        raise SiteJourneyError("product_policy:" + ",".join(hard))
    return bool(
        product.get("workflow_state") in {"active", "published"}
        and product.get("evidence_revision") == product.get("approved_revision")
        and product.get("landing_verified") is True
        and product.get("offer_active") is True
    )


def _detail_page(product: dict) -> str:
    market = product["market"]
    is_kr = market == "KR"
    name = html.escape(str(product["product_name"]))
    mechanism = html.escape(str(product["mechanism"]).replace("_", " "))
    failure = html.escape(str(product["failure_mode"]).replace("_", " "))
    skip_if = html.escape(str(product["skip_if"]))
    affiliate_url = html.escape(str(product["affiliate_url"]), quote=True)
    tracking = html.escape(str(product["tracking_key"]), quote=True)
    key = html.escape(str(product["product_key"]), quote=True)
    disclosure = journey_policy.AFFILIATE_DISCLOSURES["KR" if is_kr else "US_ACCOUNT"]
    sources = "".join(
        f'<li><a href="{html.escape(str(url), quote=True)}" rel="noopener noreferrer">{html.escape(urlparse(str(url)).netloc or str(url))}</a></li>'
        for url in product["source_pointers"]
    )
    body = f"""<main><section class="hero compact"><p class="kicker">{'판정 기록' if is_kr else 'VERDICT RECORD'} {' ' if is_kr else '· #ad'}</p><h1>{name}</h1><p class="lede">{'이 판정은 키·성장 결과가 아니라 반복 동작과 제품 구조만 다룹니다.' if is_kr else 'This verdict covers the repeated task and product format—not height, sleep, or health outcomes.'}</p></section><section class="verdict detail"><dl><div><dt>{'줄이는 동작' if is_kr else 'Mechanism'}</dt><dd>{mechanism}</dd></div><div><dt>{'관찰된 실패 조건' if is_kr else 'Observed failure mode'}</dt><dd>{failure}</dd></div><div><dt>{'사지 말아야 할 때' if is_kr else 'Skip if'}</dt><dd>{skip_if}</dd></div></dl><p class="affiliate">{html.escape(disclosure)}</p><a class="button" data-track="{tracking}" data-market="{market}" data-product-key="{key}" href="{affiliate_url}" target="_blank" rel="sponsored nofollow noopener noreferrer">{'현재 상품 페이지 확인' if is_kr else 'Check current listing'}</a><h2>{'근거 출처' if is_kr else 'Evidence sources'}</h2><ul class="sources">{sources}</ul><div class="actions"><a class="button secondary" href="/{market.lower()}/">{'전체 카테고리' if is_kr else 'All categories'}</a></div></section></main>"""
    return _shell(
        market=market,
        title=f"{name} | HeightCue",
        description=("반복 마찰과 제품 구조를 확인한 판정." if is_kr else "A verdict on repeated friction and product format."),
        canonical=str(product["landing_url"]),
        body=body,
    )


def _hub(market: str, products=None) -> str:
    is_kr = market == "KR"
    position = journey_policy.POSITIONING[market]
    title = "HeightCue | 생활 마찰 제품 판정" if is_kr else "HeightCue | Parenting Friction Verdicts"
    description = position
    verdicts = "".join(_product_card(product, market) for product in (products or [])[:3])
    verdict_body = (f'<div class="verdict-grid">{verdicts}</div>' if verdicts else
                    f'<p>{"현재 승인·랜딩 검증까지 끝난 제품만 여기에 표시합니다. 비어 있으면 검증 중이라는 뜻입니다." if is_kr else "Only products with current evidence approval and verified landings appear here. Empty means the review is still in progress."}</p>')
    body = f"""<main><section class="hero"><p class="kicker">HEIGHTCUE · {market}</p><h1>{position}</h1>
<p class="lede">{'키를 키운다는 제품이 아니라, 매일 반복되는 시간·어질러짐·실랑이를 줄이는 구조를 봅니다.' if is_kr else 'We do not sell height promises. We compare product structures that reduce repeated time, mess, and arguments.'}</p>
<div class="proof-strip"><span>{'장면부터' if is_kr else 'Scene first'}</span><i>→</i><span>{'구조 확인' if is_kr else 'Mechanism'}</span><i>→</i><span>{'실패 조건' if is_kr else 'Failure mode'}</span><i>→</i><span>{'구매 판정' if is_kr else 'Verdict'}</span></div></section>
<section id="categories"><div class="section-head"><h2>{'지금 줄이고 싶은 불편' if is_kr else 'Choose the friction to reduce'}</h2><p>{'제품명이 아니라 집에서 반복되는 장면부터 고르세요.' if is_kr else 'Start with the repeated household scene, not a product name.'}</p></div>{_category_nav(market)}</section>
<section class="empty"><h2>{'이번 주 판정' if is_kr else "This week's verdicts"}</h2>{verdict_body}<div class="actions"><a class="button" href="#categories">{'전체 카테고리' if is_kr else 'All categories'}</a><a class="button secondary" href="https://www.threads.com/@{'heightcue' if is_kr else 'heightcue_us'}" rel="noopener">{'제품 제보' if is_kr else 'Submit a product'}</a></div></section></main>"""
    return _shell(market=market, title=title, description=description,
                  canonical=f"{SITE_BASE}/{market.lower()}/", body=body)


def _category_page(market: str, key: str, products=None) -> str:
    data = journey_policy.PUBLIC_CATEGORIES[key]
    is_kr = market == "KR"
    label = data[market]
    cards = "".join(_product_card(product, market) for product in (products or []))
    results = (f'<section class="verdict-grid">{cards}</section>' if cards else
               f'''<section class="empty"><h2>{"검증 중입니다" if is_kr else "Reviews in progress"}</h2><p>{"승인 근거와 실제 랜딩 연결이 모두 확인된 제품만 표시합니다." if is_kr else "A product appears only after evidence approval and exact landing verification."}</p><div class="actions"><a class="button" href="/{market.lower()}/">{"전체 카테고리" if is_kr else "All categories"}</a><a class="button secondary" href="https://www.threads.com/@{'heightcue' if is_kr else 'heightcue_us'}" rel="noopener">{"제품 제보" if is_kr else "Submit a product"}</a></div></section>''')
    body = f"""<main><section class="hero compact"><p class="kicker">{label}</p><h1>{label}</h1><p class="lede">{'반복되는 장면과 제품 구조가 맞는지 먼저 확인합니다.' if is_kr else 'We match a repeated scene to a product mechanism before making a verdict.'}</p></section>{results}</main>"""
    return _shell(market=market, title=f"{label} | HeightCue", description=label,
                  canonical=f"{SITE_BASE}/{market.lower()}/c/{key}.html", body=body)


def _root() -> str:
    return """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HeightCue</title><link rel="canonical" href="https://heightcue.lifoli.co.kr/"><link rel="stylesheet" href="/journey.css?v=1"></head><body><main class="locale"><p class="kicker">HEIGHTCUE</p><h1>반복되는 육아 마찰을 줄이는 제품 판정</h1><p>Choose your market</p><div class="actions"><a class="button" href="/kr/">한국</a><a class="button secondary" href="/us/">United States</a></div></main></body></html>"""


def _measurement_archive() -> str:
    return _shell(market="US", title="Height measurement education | HeightCue",
                  description="Historical education, not a shopping category.",
                  canonical=f"{SITE_BASE}/measurement/",
                  body="""<main><section class="hero"><p class="kicker">HISTORICAL EDUCATION</p><h1>Height measurement is not a commerce category.</h1><p class="lede">These materials remain for historical education. Measurement tools record height; they do not change it. HeightCue does not recommend or monetize stadiometers here.</p><div class="actions"><a class="button" href="/kr/">한국 카테고리</a><a class="button secondary" href="/us/">US categories</a></div></section></main>""")


def _legal_page(kind: str) -> str:
    if kind == "disclosure":
        body = f"""<main><section class="hero compact"><p class="kicker">DISCLOSURES</p><h1>제휴 및 편집 원칙</h1><p class="lede">{html.escape(journey_policy.AFFILIATE_DISCLOSURES['KR'])}</p><p class="lede">{html.escape(journey_policy.AFFILIATE_DISCLOSURES['US_ACCOUNT'])}</p><p>HeightCue는 제품을 직접 판매하지 않습니다. 제휴 수수료는 판정 결과나 표시 순위를 바꾸지 않습니다. 건강·성장 결과를 보장하지 않으며, 진료가 필요한 신호는 제품 구매보다 의료진 상담이 우선입니다.</p><div class="actions"><a class="button" href="/kr/">한국 홈</a><a class="button secondary" href="/us/">US home</a></div></section></main>"""
        return _shell(market="KR", title="제휴 및 편집 원칙 | HeightCue", description="HeightCue affiliate and editorial disclosures.", canonical=f"{SITE_BASE}/disclosure.html", body=body)
    body = """<main><section class="hero compact"><p class="kicker">PRIVACY</p><h1>개인정보 처리 안내</h1><p class="lede">이 정적 가이드는 이름, 아이의 얼굴, 병력 또는 검사 결과를 요청하지 않습니다.</p><p>외부 판매처로 이동할 때 해당 판매처의 개인정보 처리방침이 적용됩니다. 사이트 운영 지표는 개인을 식별하지 않는 집계 단위로만 사용합니다.</p><div class="actions"><a class="button" href="/kr/">한국 홈</a><a class="button secondary" href="/us/">US home</a></div></section></main>"""
    return _shell(market="KR", title="개인정보 처리 안내 | HeightCue", description="HeightCue privacy notice.", canonical=f"{SITE_BASE}/privacy.html", body=body)


class _HrefParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag in {"a", "link"}:
            value = dict(attrs).get("href")
            if value:
                self.hrefs.append(value)


def validate_site(output_root):
    """Return every broken root-relative link in deterministic order."""
    root = Path(output_root)
    broken = set()
    for source in sorted(root.rglob("*.html")):
        parser = _HrefParser()
        parser.feed(source.read_text(encoding="utf-8"))
        for href in parser.hrefs:
            if not href.startswith("/") or href.startswith("//"):
                continue
            target = href.split("#", 1)[0].split("?", 1)[0].lstrip("/")
            if not target:
                target = "index.html"
            elif target.endswith("/"):
                target += "index.html"
            path = root / target
            if not path.is_file():
                broken.add(f"{source.relative_to(root).as_posix()} -> {href}")
    return sorted(broken)


_CSS_OLD = """*{box-sizing:border-box}html{background:#f4f7f5;color:#10231c;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}body{margin:0}a{color:inherit}.top{height:64px;display:flex;align-items:center;justify-content:space-between;padding:0 max(22px,calc((100vw - 1040px)/2));border-bottom:1px solid #cddbd4;background:rgba(244,247,245,.94);position:sticky;top:0}.wordmark{font-weight:900;text-decoration:none;letter-spacing:-.04em}.top nav{display:flex;gap:18px;font-size:14px}main{max-width:1040px;margin:auto;padding:72px 22px}.hero{max-width:860px;padding-bottom:72px}.hero.compact{padding-bottom:36px}.kicker{font:700 12px ui-monospace,SFMono-Regular,monospace;letter-spacing:.12em;color:#b64827}.hero h1,.locale h1{font-size:clamp(38px,7vw,78px);line-height:1.02;letter-spacing:-.065em;margin:.2em 0}.lede{font-size:clamp(17px,2.2vw,22px);line-height:1.55;color:#455a51;max-width:760px}.proof-strip{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:32px;padding:14px 18px;border:1px solid #9eb9ac;border-radius:14px;background:#e8f1ed;font:700 13px ui-monospace,SFMono-Regular,monospace}.proof-strip i{color:#b64827}.section-head{display:flex;justify-content:space-between;align-items:end;gap:24px}.section-head h2,.empty h2{font-size:28px;letter-spacing:-.04em}.section-head p,.empty p{color:#53675e}.category-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin:24px 0 72px}.category{min-height:112px;padding:24px;border:1px solid #b8cbc1;border-radius:18px;background:#fff;text-decoration:none;display:flex;justify-content:space-between;align-items:end;font-size:20px;font-weight:800}.category:last-child{grid-column:1/-1}.category b{color:#b64827}.empty{padding:28px;border:1px dashed #87a696;border-radius:18px;background:#edf4f0}.actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:24px}.button{display:inline-flex;justify-content:center;padding:13px 18px;border-radius:12px;background:#b64827;color:white;text-decoration:none;font-weight:800}.button.secondary{background:#173f31}.locale{min-height:100vh;display:flex;flex-direction:column;justify-content:center}footer{max-width:1040px;margin:auto;padding:28px 22px 48px;border-top:1px solid #cddbd4;display:flex;gap:20px;flex-wrap:wrap;font-size:13px;color:#53675e}@media(max-width:640px){main{padding-top:48px}.category-grid{grid-template-columns:1fr}.category:last-child{grid-column:auto}.section-head{display:block}.proof-strip{gap:8px}.top nav{gap:12px}.hero h1,.locale h1{font-size:42px}}"""

_CSS = """:root{--board-navy:#0b1f33;--paper-blue:#f3f7fa;--inspection-cyan:#00b7c7;--verdict-amber:#ffb000;--rule:#b8c7d4;--ink:#15283b;--muted:#52677a;--white:#fff}*{box-sizing:border-box}html{background:var(--paper-blue);color:var(--ink);font-family:'Avenir Next','Apple SD Gothic Neo','Noto Sans KR',system-ui,sans-serif}body{margin:0;background-image:linear-gradient(rgba(11,31,51,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(11,31,51,.035) 1px,transparent 1px);background-size:24px 24px}a{color:inherit}.top{min-height:64px;display:flex;align-items:center;justify-content:space-between;padding:0 max(22px,calc((100vw - 1080px)/2));border-bottom:4px solid var(--inspection-cyan);background:var(--board-navy);color:var(--white);position:sticky;top:0;z-index:10}.wordmark{font-family:'Arial Narrow','Avenir Next Condensed',sans-serif;font-size:22px;font-weight:900;text-decoration:none;letter-spacing:-.04em;text-transform:uppercase}.top nav{display:flex;gap:20px;font:700 13px ui-monospace,SFMono-Regular,Consolas,monospace}.top nav a{text-underline-offset:5px}main{max-width:1080px;margin:auto;padding:72px 24px}.hero{max-width:920px;padding:34px 0 72px 30px;border-left:10px solid var(--inspection-cyan)}.hero.compact{padding-bottom:38px}.kicker,.card-kicker{font:800 12px ui-monospace,SFMono-Regular,Consolas,monospace;letter-spacing:.14em;color:#006f7a}.hero h1,.locale h1{max-width:960px;font-family:'Arial Narrow','Avenir Next Condensed','Apple SD Gothic Neo',sans-serif;font-size:clamp(40px,7.4vw,84px);font-weight:900;line-height:1.04;letter-spacing:-.055em;margin:.18em 0;padding-bottom:4px;color:var(--board-navy)}.lede{font-size:clamp(17px,2.2vw,22px);font-weight:560;line-height:1.58;color:var(--muted);max-width:780px}.proof-strip{display:grid;grid-template-columns:repeat(4,1fr);gap:2px;margin-top:34px;background:var(--board-navy);border:2px solid var(--board-navy);font:800 12px ui-monospace,SFMono-Regular,Consolas,monospace;text-transform:uppercase}.proof-strip span{min-height:52px;display:flex;align-items:center;padding:12px;background:var(--white);border-top:7px solid var(--inspection-cyan)}.proof-strip span:last-of-type{border-top-color:var(--verdict-amber)}.proof-strip i{display:none}.section-head{display:flex;justify-content:space-between;align-items:end;gap:36px;border-bottom:2px solid var(--board-navy)}.section-head h2,.empty h2,.detail h2{font-family:'Arial Narrow','Avenir Next Condensed',sans-serif;font-size:32px;letter-spacing:-.035em;margin-bottom:14px}.section-head p,.empty p{color:var(--muted);max-width:440px}.category-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:16px;margin:24px 0 72px}.category{min-height:116px;padding:20px 22px;border:2px solid var(--board-navy);border-top:8px solid var(--inspection-cyan);background:var(--white);box-shadow:5px 5px 0 var(--rule);text-decoration:none;display:flex;justify-content:space-between;align-items:end;font-family:'Arial Narrow','Avenir Next Condensed',sans-serif;font-size:23px;font-weight:900;transition:transform .14s ease,box-shadow .14s ease}.category:hover{transform:translate(-2px,-2px);box-shadow:8px 8px 0 var(--board-navy)}.category:last-child{grid-column:1/-1;border-top-color:var(--verdict-amber)}.category b{color:#007b86;font:900 24px ui-monospace,SFMono-Regular,monospace}.empty{padding:30px;border:2px solid var(--board-navy);border-left:10px solid var(--verdict-amber);background:var(--white);box-shadow:8px 8px 0 var(--board-navy)}.verdict-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:22px}.verdict{position:relative;padding:28px;border:2px solid var(--board-navy);border-top:10px solid var(--verdict-amber);background:var(--white);box-shadow:7px 7px 0 var(--board-navy)}.verdict.detail{max-width:820px}.verdict h3{font-family:'Arial Narrow','Avenir Next Condensed',sans-serif;font-size:30px;line-height:1.05;margin:10px 0 24px}.verdict dl{display:grid;gap:0;margin:0 0 24px}.verdict dl div{padding:14px 0;border-top:1px solid var(--rule)}.verdict dt{font:800 11px ui-monospace,SFMono-Regular,Consolas,monospace;letter-spacing:.08em;text-transform:uppercase;color:#006f7a}.verdict dd{margin:7px 0 0;font-weight:650;line-height:1.5}.ad{position:absolute;top:12px;right:12px;padding:4px 7px;background:var(--verdict-amber);font:900 11px ui-monospace,SFMono-Regular,monospace}.affiliate{font-size:12px!important;line-height:1.45;color:var(--muted)}.sources{padding-left:20px;line-height:1.9}.actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:24px}.button{min-height:44px;display:inline-flex;align-items:center;justify-content:center;padding:12px 18px;border:2px solid var(--board-navy);background:var(--board-navy);color:var(--white);text-decoration:none;font-weight:850;line-height:1.15}.button.secondary{background:var(--white);color:var(--board-navy)}.button:hover{background:#163b5c}.button.secondary:hover{background:#d9f6f8}.locale{min-height:100vh;display:flex;flex-direction:column;justify-content:center}.locale .actions{max-width:440px}.locale .button{flex:1}footer{max-width:1080px;margin:auto;padding:30px 24px 52px;border-top:4px solid var(--board-navy);display:flex;gap:24px;flex-wrap:wrap;font:700 12px ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--muted)}.top a,footer a,.sources a{min-height:44px;display:inline-flex;align-items:center}a:focus-visible{outline:4px solid var(--verdict-amber);outline-offset:4px}@media(max-width:680px){main{padding:42px 18px}.top{padding:0 18px}.top nav{gap:12px}.hero{padding:20px 0 48px 18px;border-left-width:7px}.hero h1,.locale h1{font-size:44px}.proof-strip{grid-template-columns:1fr 1fr}.section-head{display:block}.category-grid{grid-template-columns:1fr}.category:last-child{grid-column:auto}.verdict{padding:22px;box-shadow:5px 5px 0 var(--board-navy)}.button{width:100%}.locale .actions{width:100%}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}"""


def _retired_page(item: dict) -> str:
    market = item.get("market", "KR")
    is_kr = market == "KR"
    title = html.escape(str(item.get("title", "Retired verdict")))
    home = market.lower()
    message = "판매 판정에서 퇴역했습니다." if is_kr else "This verdict has been retired from commerce."
    detail = ("가격·재고·리뷰 또는 근거 승인이 오래되어 제휴 링크를 제거했습니다. 현재 검증된 카테고리에서 다시 선택해 주세요."
              if is_kr else "The price, availability, review snapshot, or evidence approval is stale, so the affiliate link was removed. Return to the current categories.")
    return f"""<!doctype html><html lang="{'ko' if is_kr else 'en'}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,follow"><title>{title} | HeightCue</title><link rel="stylesheet" href="/journey.css?v=1"></head><body><main><section class="hero"><p class="kicker">RETIRED VERDICT</p><h1>{message}</h1><p class="lede">{detail}</p><div class="actions"><a class="button" href="/{home}/">{'현재 카테고리 보기' if is_kr else 'Browse current categories'}</a><a class="button secondary" href="/disclosure.html">{'편집 원칙' if is_kr else 'Editorial policy'}</a></div></section></main></body></html>"""


def build_site(output_root, products, retired=None):
    """Build a complete, recoverable locale journey into ``output_root``."""
    root = Path(output_root)
    active = []
    for packet in products:
        if _eligible_packet(packet):
            digest = hashlib.sha256(
                json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            active.append({**packet, "category": journey_policy.map_category(packet["category"]), "_packet_digest": digest})
    by_market = {market: [p for p in active if p.get("market") == market] for market in ("KR", "US")}
    routes = [
        "index.html", "kr/index.html", "us/index.html", "measurement/index.html",
        "disclosure.html", "privacy.html", "sitemap.xml",
    ]
    routes += [f"kr/c/{key}.html" for key in journey_policy.PUBLIC_CATEGORIES]
    routes += [f"us/c/{key}.html" for key in journey_policy.PUBLIC_CATEGORIES]
    detail_routes = [_landing_relative(product) for product in active]
    if len(detail_routes) != len(set(detail_routes)):
        raise SiteJourneyError("duplicate_landing_path")
    retired = list(retired or [])
    retired_routes = []
    for item in retired:
        relative = str(item.get("path", "")).lstrip("/")
        if not relative.endswith(".html") or ".." in relative:
            raise SiteJourneyError("retired_landing_path_invalid")
        retired_routes.append(relative)
    if set(retired_routes) & set(detail_routes):
        raise SiteJourneyError("retired_landing_conflicts_with_active")
    routes += detail_routes
    routes += retired_routes
    _write(root, "index.html", _root())
    _write(root, "kr/index.html", _hub("KR", by_market["KR"]))
    _write(root, "us/index.html", _hub("US", by_market["US"]))
    _write(root, "measurement/index.html", _measurement_archive())
    _write(root, "disclosure.html", _legal_page("disclosure"))
    _write(root, "privacy.html", _legal_page("privacy"))
    for key in journey_policy.PUBLIC_CATEGORIES:
        kr_products = [p for p in by_market["KR"] if p["category"] == key]
        us_products = [p for p in by_market["US"] if p["category"] == key]
        _write(root, f"kr/c/{key}.html", _category_page("KR", key, kr_products))
        _write(root, f"us/c/{key}.html", _category_page("US", key, us_products))
    for product in active:
        _write(root, _landing_relative(product), _detail_page(product))
    for item, relative in zip(retired, retired_routes):
        _write(root, relative, _retired_page(item))
    urls = [
        f"{SITE_BASE}/{route.replace('index.html', '')}"
        for route in sorted(routes)
        if route != "sitemap.xml" and route not in set(retired_routes)
    ]
    _write(root, "sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(f"<url><loc>{url}</loc></url>" for url in urls) + "</urlset>")
    _write(root, "journey.css", _CSS)
    manifest_products = [{
        "product_key": p["product_key"],
        "market": p["market"],
        "evidence_revision": p["evidence_revision"],
        "approved_revision": p["approved_revision"],
        "offer_id": p["offer_id"],
        "tracking_key": p["tracking_key"],
        "landing_url": p["landing_url"],
        "packet_digest": p["_packet_digest"],
    } for p in active]
    manifest = {"routes": sorted(routes), "products": manifest_products}
    _write(root, "journey-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return manifest
