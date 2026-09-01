#!/usr/bin/env python3
"""Build the HeightCue system atlas and one-page poster.

The outputs are static, self-contained HTML documents. No credentials or private
identifiers are embedded. Snapshot facts are intentionally explicit so a reader
can distinguish design intent, code enforcement, runtime observation, and gaps.
"""
from __future__ import annotations

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent

PAPER = "#f7f5f1"
PAPER2 = "#efe9dd"
INK = "#102a43"
MUTED = "#53687a"
SOFT = "#7b877f"
ACCENT = "#0e8074"
ACCENT_TINT = "#e3efec"
LINK = "#2e5aa8"
CORAL = "#ff966f"
RUST = "#b85450"
WHITE = "#ffffff"


def defs(slug: str) -> str:
    return f'''<defs>
      <marker id="{slug}-arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0,8 3,0 6" fill="{MUTED}"/></marker>
      <marker id="{slug}-accent" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0,8 3,0 6" fill="{ACCENT}"/></marker>
      <marker id="{slug}-link" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0,8 3,0 6" fill="{LINK}"/></marker>
      <marker id="{slug}-coral" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0,8 3,0 6" fill="{CORAL}"/></marker>
      <marker id="{slug}-open" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polyline points="0 0,8 3,0 6" fill="none" stroke="{MUTED}" stroke-width="1.2"/></marker>
    </defs>'''


def svg(slug: str, title: str, desc: str, w: int, h: int, body: str, *, cls: str = "diagram-svg") -> str:
    return f'''<svg class="{cls}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="{slug}-title {slug}-desc">
      <title id="{slug}-title">{escape(title)}</title>
      <desc id="{slug}-desc">{escape(desc)}</desc>
      {defs(slug)}
      <rect width="{w}" height="{h}" fill="{PAPER}"/>
      {body}
    </svg>'''


def zone(x: int, y: int, w: int, h: int, label: str, *, dashed: bool = False) -> str:
    dash = ' stroke-dasharray="4,4"' if dashed else ""
    return f'''<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="rgba(16,42,67,.025)" stroke="rgba(16,42,67,.18)" stroke-width="1"{dash}/>
    <rect x="{x+12}" y="{y-6}" width="{max(72, len(label)*8+20)}" height="16" rx="2" fill="{PAPER}"/>
    <text x="{x+20}" y="{y+5}" class="svg-eyebrow">{escape(label.upper())}</text>'''


def node(x: int, y: int, w: int, h: int, title: str, sub: str = "", tag: str = "STEP", kind: str = "default") -> str:
    styles = {
        "default": (WHITE, INK, INK),
        "focal": (ACCENT_TINT, ACCENT, INK),
        "store": ("rgba(16,42,67,.05)", MUTED, INK),
        "external": ("rgba(16,42,67,.025)", "rgba(16,42,67,.30)", INK),
        "optional": ("rgba(16,42,67,.018)", "rgba(16,42,67,.24)", MUTED),
        "risk": ("rgba(184,84,80,.07)", RUST, INK),
        "coral": ("rgba(255,150,111,.12)", CORAL, INK),
        "dark": (INK, INK, PAPER),
    }
    fill, stroke, text = styles[kind]
    dash = ' stroke-dasharray="5,4"' if kind == "optional" else ""
    tag_w = max(36, min(w - 16, len(tag) * 7 + 16))
    # Compact nodes need an explicit vertical stack. Centering the title in a
    # 56–64 px box put it on top of the type tag in real browser rendering.
    if sub:
        title_y = y + (34 if h <= 68 else h // 2 - 4)
    else:
        title_y = y + (36 if h <= 64 else h // 2 + 2)
    sub_lines = sub.split("\n") if sub else []
    sub_markup = "".join(
        f'<text x="{x+w/2}" y="{title_y+18+i*14}" class="svg-sub">{escape(text_line)}</text>'
        for i, text_line in enumerate(sub_lines)
    )
    return f'''<g class="svg-node">
      <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.2"{dash}/>
      <rect x="{x+8}" y="{y+6}" width="{tag_w}" height="14" rx="2" fill="transparent" stroke="{stroke}" stroke-opacity=".42"/>
      <text x="{x+8+tag_w/2:.1f}" y="{y+16}" class="svg-tag" fill="{stroke}">{escape(tag.upper())}</text>
      <text x="{x+w/2}" y="{title_y}" class="svg-name" fill="{text}">{escape(title)}</text>
      {sub_markup}
    </g>'''


def line(slug: str, x1: int, y1: int, x2: int, y2: int, *, style: str = "default", dashed: bool = False, label: str = "", label_x: int | None = None, label_y: int | None = None) -> str:
    color = {"default": MUTED, "accent": ACCENT, "link": LINK, "coral": CORAL, "risk": RUST}.get(style, MUTED)
    marker = {"default": "arrow", "accent": "accent", "link": "link", "coral": "coral", "risk": "coral"}.get(style, "arrow")
    dash = ' stroke-dasharray="5,4"' if dashed else ""
    width = "1.6" if style in {"accent", "coral", "risk"} else "1.2"
    out = f'<path d="M {x1},{y1} H {x2}" fill="none" stroke="{color}" stroke-width="{width}"{dash} marker-end="url(#{slug}-{marker})"/>' if y1 == y2 else f'<path d="M {x1},{y1} V {y2}" fill="none" stroke="{color}" stroke-width="{width}"{dash} marker-end="url(#{slug}-{marker})"/>'
    if label:
        lx = label_x if label_x is not None else (x1+x2)//2
        ly = label_y if label_y is not None else min(y1,y2)-12
        tw = max(48, len(label)*7+20)
        out += f'<rect x="{lx-tw/2}" y="{ly-14}" width="{tw}" height="16" rx="2" fill="{PAPER}"/><text x="{lx}" y="{ly-3}" class="svg-arrow-label">{escape(label.upper())}</text>'
    return out


def elbow(slug: str, x1: int, y1: int, x2: int, y2: int, *, via_x: int | None = None, style: str = "default", dashed: bool = False, label: str = "", label_y: int | None = None) -> str:
    color = {"default": MUTED, "accent": ACCENT, "link": LINK, "coral": CORAL, "risk": RUST}.get(style, MUTED)
    marker = {"default": "arrow", "accent": "accent", "link": "link", "coral": "coral", "risk": "coral"}.get(style, "arrow")
    dash = ' stroke-dasharray="5,4"' if dashed else ""
    width = "1.6" if style in {"accent", "coral", "risk"} else "1.2"
    vx = via_x if via_x is not None else ((x1+x2)//8)*4
    r = 8
    sy = 1 if y2 > y1 else -1
    ex = 1 if x2 > vx else -1
    d = f'M {x1},{y1} H {vx-r if vx>x1 else vx+r} Q {vx},{y1} {vx},{y1+sy*r} V {y2-sy*r} Q {vx},{y2} {vx+ex*r},{y2} H {x2}'
    out = f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"{dash} marker-end="url(#{slug}-{marker})"/>'
    if label:
        ly = label_y if label_y is not None else ((y1+y2)//8)*4
        tw = max(48, len(label)*7+20)
        lx = vx + 12 + tw/2
        out += f'<rect x="{lx-tw/2}" y="{ly-8}" width="{tw}" height="16" rx="2" fill="{PAPER}"/><text x="{lx}" y="{ly+3}" class="svg-arrow-label">{escape(label.upper())}</text>'
    return out


def legend(items: list[tuple[str, str]], y: int, w: int) -> str:
    x = 32
    bits = [f'<line x1="32" y1="{y-20}" x2="{w-32}" y2="{y-20}" stroke="rgba(16,42,67,.15)"/>', f'<text x="32" y="{y}" class="svg-eyebrow">LEGEND</text>']
    x = 132
    for label, kind in items:
        if kind == "solid":
            bits.append(f'<line x1="{x}" y1="{y-4}" x2="{x+32}" y2="{y-4}" stroke="{MUTED}" stroke-width="1.4"/>')
        elif kind == "dashed":
            bits.append(f'<line x1="{x}" y1="{y-4}" x2="{x+32}" y2="{y-4}" stroke="{MUTED}" stroke-width="1.2" stroke-dasharray="5,4"/>')
        elif kind == "accent":
            bits.append(f'<rect x="{x}" y="{y-12}" width="20" height="16" rx="2" fill="{ACCENT_TINT}" stroke="{ACCENT}"/>')
        elif kind == "link":
            bits.append(f'<line x1="{x}" y1="{y-4}" x2="{x+32}" y2="{y-4}" stroke="{LINK}" stroke-width="1.4"/>')
        elif kind == "risk":
            bits.append(f'<rect x="{x}" y="{y-12}" width="20" height="16" rx="2" fill="rgba(184,84,80,.07)" stroke="{RUST}"/>')
        bits.append(f'<text x="{x+40}" y="{y}" class="svg-legend">{escape(label)}</text>')
        x += 168
    return "".join(bits)


BASE_CSS = f'''
:root {{ --paper:{PAPER}; --paper-2:{PAPER2}; --ink:{INK}; --muted:{MUTED}; --soft:{SOFT}; --accent:{ACCENT}; --accent-tint:{ACCENT_TINT}; --link:{LINK}; --coral:{CORAL}; --rust:{RUST}; --rule:rgba(16,42,67,.15); }}
* {{ box-sizing:border-box; }}
html {{ scroll-behavior:smooth; }}
body {{ margin:0; background:var(--paper); color:var(--ink); font-family:'Geist','Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',system-ui,sans-serif; line-height:1.55; }}
a {{ color:var(--link); }}
code,.mono {{ font-family:'Geist Mono','Noto Sans Mono CJK KR',ui-monospace,SFMono-Regular,monospace; }}
code {{ font-size:.88em; background:rgba(16,42,67,.05); padding:.08rem .28rem; border-radius:2px; overflow-wrap:anywhere; }}
main {{ width:min(1500px,100%); margin:0 auto; padding:32px 40px 80px; }}
header.hero {{ display:grid; grid-template-columns:minmax(0,1.35fr) minmax(300px,.65fr); gap:40px; padding:32px 0 40px; border-bottom:1px solid var(--rule); }}
.eyebrow {{ margin:0 0 8px; color:var(--accent); font:600 11px/1.2 'Geist Mono',monospace; letter-spacing:.16em; text-transform:uppercase; }}
h1,h2,h3 {{ margin:0; color:var(--ink); }}
h1 {{ font-family:'Instrument Serif','Iowan Old Style',Georgia,serif; font-size:clamp(42px,5vw,72px); font-weight:400; line-height:.98; letter-spacing:-.025em; }}
h2 {{ font-family:'Instrument Serif','Iowan Old Style',Georgia,serif; font-size:clamp(30px,3.4vw,48px); font-weight:400; line-height:1.05; }}
h3 {{ font-size:18px; line-height:1.25; }}
.lede {{ max-width:780px; font-size:18px; color:var(--muted); margin:20px 0 0; }}
.hero-meta {{ border-left:3px solid var(--accent); background:var(--accent-tint); padding:20px 24px; align-self:end; }}
.hero-meta dl {{ display:grid; grid-template-columns:112px 1fr; gap:8px 12px; margin:0; font-size:13px; }}
.hero-meta dt {{ color:var(--soft); font-family:'Geist Mono',monospace; }}
.hero-meta dd {{ margin:0; font-weight:600; }}
nav.toc {{ display:flex; gap:8px; flex-wrap:wrap; padding:20px 0; border-bottom:1px solid var(--rule); position:sticky; top:0; z-index:20; background:rgba(247,245,241,.96); backdrop-filter:blur(8px); }}
nav.toc a {{ text-decoration:none; border:1px solid var(--rule); padding:6px 10px; border-radius:4px; color:var(--muted); font-size:12px; }}
nav.toc a:hover {{ border-color:var(--accent); color:var(--accent); }}
section {{ padding:64px 0 8px; scroll-margin-top:70px; }}
.section-head {{ display:grid; grid-template-columns:160px minmax(0,1fr); gap:24px; align-items:start; margin-bottom:24px; }}
.section-no {{ color:var(--accent); font:600 12px/1.3 'Geist Mono',monospace; letter-spacing:.12em; }}
.section-intro {{ max-width:980px; color:var(--muted); margin:10px 0 0; }}
.diagram-frame {{ overflow-x:auto; border:1px solid var(--rule); background:var(--paper); border-radius:8px; padding:16px; margin:20px 0 24px; }}
.diagram-svg {{ display:block; width:100%; min-width:960px; height:auto; }}
.svg-eyebrow,.svg-tag,.svg-arrow-label,.svg-legend {{ font-family:'Geist Mono','Noto Sans Mono CJK KR',monospace; text-anchor:middle; letter-spacing:.08em; }}
.svg-eyebrow {{ font-size:10px; fill:{MUTED}; text-anchor:start; }}
.svg-tag {{ font-size:8px; font-weight:600; }}
.svg-name {{ font-family:'Geist','Apple SD Gothic Neo','Noto Sans KR',sans-serif; font-size:14px; font-weight:600; text-anchor:middle; }}
.svg-sub {{ font-family:'Geist Mono','Noto Sans Mono CJK KR',monospace; font-size:10px; fill:{MUTED}; text-anchor:middle; }}
.svg-arrow-label {{ font-size:9px; fill:{MUTED}; }}
.svg-legend {{ font-size:9px; fill:{MUTED}; text-anchor:start; }}
.grid {{ display:grid; gap:16px; }}
.grid.two {{ grid-template-columns:1.1fr .9fr; }}
.grid.three {{ grid-template-columns:1.15fr 1fr .85fr; }}
.card {{ border:1px solid var(--rule); border-radius:6px; background:#fff; padding:20px; }}
.card.accent {{ border-color:rgba(14,128,116,.45); background:var(--accent-tint); }}
.card.risk {{ border-color:rgba(184,84,80,.45); background:rgba(184,84,80,.05); }}
.card.coral {{ border-color:rgba(255,150,111,.55); background:rgba(255,150,111,.09); }}
.card p:last-child,.card ul:last-child {{ margin-bottom:0; }}
.metric-row {{ display:grid; grid-template-columns:repeat(5,1fr); gap:12px; margin:20px 0; }}
.metric {{ border-top:3px solid var(--ink); padding:12px 4px 0; }}
.metric.accent {{ border-color:var(--accent); }}
.metric.risk {{ border-color:var(--rust); }}
.metric .value {{ display:block; font:400 32px/1 'Instrument Serif',Georgia,serif; }}
.metric .label {{ color:var(--muted); font-size:12px; }}
.badge {{ display:inline-flex; align-items:center; gap:6px; padding:3px 8px; border:1px solid var(--rule); border-radius:3px; font:600 10px/1.4 'Geist Mono',monospace; text-transform:uppercase; letter-spacing:.06em; }}
.badge.declared {{ border-color:#8c6d3f; color:#6d532f; background:rgba(140,109,63,.07); }}
.badge.enforced {{ border-color:var(--accent); color:var(--accent); background:var(--accent-tint); }}
.badge.observed {{ border-color:var(--link); color:var(--link); background:rgba(46,90,168,.06); }}
.badge.gap {{ border-style:dashed; color:var(--muted); }}
.badge.conflict {{ border-color:var(--rust); color:var(--rust); background:rgba(184,84,80,.05); }}
table {{ width:100%; border-collapse:collapse; margin:16px 0 28px; font-size:13px; }}
th,td {{ padding:11px 12px; border-bottom:1px solid var(--rule); text-align:left; vertical-align:top; }}
th {{ color:var(--muted); font:600 10px/1.4 'Geist Mono',monospace; letter-spacing:.09em; text-transform:uppercase; }}
tbody tr:hover {{ background:rgba(16,42,67,.025); }}
.status-dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; background:var(--soft); }}
.status-dot.ok {{ background:var(--accent); }}
.status-dot.warn {{ background:var(--coral); }}
.status-dot.bad {{ background:var(--rust); }}
.callout {{ border-left:3px solid var(--coral); padding:12px 16px; background:rgba(255,150,111,.08); color:var(--muted); margin:16px 0; }}
.callout strong {{ color:var(--ink); }}
.source-list {{ columns:2; column-gap:32px; font-size:12px; }}
.source-list li {{ break-inside:avoid; margin:0 0 7px; }}
footer {{ margin-top:72px; padding-top:20px; border-top:1px solid var(--rule); color:var(--soft); font:11px/1.5 'Geist Mono',monospace; }}
@media (max-width:900px) {{ main {{ padding:20px 16px 56px; }} header.hero,.grid.two,.grid.three,.section-head {{ grid-template-columns:1fr; }} .hero-meta {{ border-left:0; border-top:3px solid var(--accent); }} .metric-row {{ grid-template-columns:repeat(2,1fr); }} .source-list {{ columns:1; }} nav.toc {{ position:static; }} }}
@media print {{ nav.toc {{ display:none; }} section {{ break-inside:avoid; }} main {{ max-width:none; padding:20px; }} .diagram-frame {{ overflow:visible; }} }}
'''


def html_shell(title: str, description: str, body: str, *, extra_css: str = "") -> str:
    return f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta name="description" content="{escape(description)}"/>
<title>{escape(title)}</title>
<style>{BASE_CSS}{extra_css}</style>
</head>
<body>{body}</body>
</html>'''


def system_context_diagram() -> str:
    s = "ctx"
    body = []
    body += [zone(24,52,228,500,"수요·소재 입력",dashed=True), zone(284,52,672,500,"HeightCue Autopilot"), zone(988,52,268,500,"외부 채널·성과",dashed=True)]
    # arrows first
    body += [
        line(s,216,140,316,140,label="signals",label_y=124),
        line(s,216,260,316,260,label="products",label_y=244),
        line(s,484,140,548,140),
        elbow(s,716,140,748,260,via_x=732,style="accent",label="evidence",label_y=200),
        line(s,916,260,1020,260,style="link",label="Graph API",label_y=244),
        f'<path d="M 1228,260 H 1240 V 500 H 1228" fill="none" stroke="{LINK}" stroke-width="1.2" marker-end="url(#{s}-link)"/>',
        f'<rect x="1136" y="304" width="88" height="16" fill="{PAPER}"/><text x="1180" y="315" class="svg-arrow-label" fill="{LINK}">US CLICK RPC</text>',
        elbow(s,1124,420,884,408,via_x=972,style="link",dashed=True,label="metrics",label_y=428),
        elbow(s,760,444,572,500,via_x=732,dashed=True,label="learning",label_y=468),
        elbow(s,440,444,440,500,via_x=440,dashed=True),
        line(s,672,500,672,456,dashed=True),
    ]
    body += [
        node(56,100,160,80,"Trend · Search","Aside harvest","INPUT","external"),
        node(56,220,160,80,"KR / US catalog","Coupang · Amazon","INPUT","external"),
        node(56,340,160,80,"Story · UX bank","episodes · patterns","INPUT","store"),
        node(316,100,168,80,"Source + Audit","KR queue · bypass gaps","CODE","default"),
        node(548,100,168,80,"Evidence Ledger","claim atoms · source hash","SSOT","focal"),
        node(748,220,168,80,"Generate + Select","Gemini tournament","MODEL","default"),
        node(748,340,168,80,"Policy Gates","post_check · attestation","GATE","risk"),
        node(548,460,168,80,"State Ledgers","JSON / JSONL files","STORE","store"),
        node(316,340,168,80,"Video Sidecar","queue · process unwired/OFF","VIDEO","optional"),
        node(1020,220,208,80,"Threads + public site","KR/US · US click RPC","CHANNEL","external"),
        node(1020,340,208,80,"Local analytics + revenue","views → clicks → orders","OUTCOME","coral"),
        node(1020,460,208,80,"LiFoli Company OS","control plane · click ingress only","OS","optional"),
    ]
    body.append(legend([("실행 경로","solid"),("외부 API","link"),("핵심 SSOT","accent"),("비활성·미연결","dashed"),("위험·성과","risk")],600,1280))
    return svg(s,"HeightCue 전체 시스템 경계","수요와 상품 신호가 증거 원장, 생성·검수, Threads 발행, 로컬 분석과 수익 학습으로 흐른다. 영상은 미배선 sidecar이고 Company OS에는 공개 US 클릭 RPC만 직접 연결된다.",1280,640,"".join(body))


def text_pipeline_diagram() -> str:
    s="textflow"
    body=[]
    body += [zone(24,48,1232,432,"텍스트 제휴 콘텐츠 — 라이브 경로")]
    # arrows before nodes: two rows, clockwise
    xs=[56,208,360,512,664,816,968,1120]
    for i in range(7): body.append(line(s,xs[i]+120,144,xs[i+1],144,style="accent" if i==3 else "default"))
    body.append(elbow(s,1180,184,1180,336,via_x=1180,style="link",label="publish",label_y=260))
    for i in range(7,0,-1):
        body.append(line(s,xs[i],376,xs[i-1]+120,376,style="link" if i in (7,6) else "default",dashed=i in (2,1)))
    top=[("Demand","trends · UX"),("Source","KR/US products"),("Audit","KR queue only"),("Evidence","atoms · claims"),("Generate","8 candidates"),("Tournament","blind ranking"),("Post-check","hard blocks"),("Publish boundary","Threads Graph")]
    bottom=[("Improve","prompts · rules"),("Weekly report","winners · holds"),("Revenue","clicks · orders"),("Analytics","views · replies"),("Comments","3-minute worker"),("Thread reply","disclosure · link"),("Published ledger","media_id · ts"),("Threads","KR / US")]
    for i,(t,sub) in enumerate(top): body.append(node(xs[i],104,120,80,t,sub,str(i+1),"focal" if t=="Evidence" else ("risk" if t in {"Post-check","Publish boundary"} else "default")))
    for i,(t,sub) in enumerate(bottom): body.append(node(xs[i],336,120,80,t,sub,str(16-i),"coral" if t=="Revenue" else ("store" if "ledger" in t.lower() or t=="Weekly report" else "default")))
    body.append(legend([("순차 처리","solid"),("핵심 증거","accent"),("Graph / 성과","link"),("학습 반환","dashed"),("검수 경계","risk")],516,1280))
    return svg(s,"텍스트 콘텐츠 수익 루프","수요 신호에서 상품 소싱, 증거 원장, 생성·블라인드 선발, 정책 검수, Threads 발행과 댓글, 분석·매출을 거쳐 다시 개선되는 라이브 텍스트 경로.",1280,556,"".join(body))


def evidence_layers_diagram() -> str:
    s="layers"
    body=[]
    labels=[
        ("L5","PUBLISH BOUNDARY","실발행 직전 실행 증명·정책·링크를 재검증","risk"),
        ("L4","POST CHECK","고지 · 금칙표현 · 사실성 · 형식 · 인과 게이트","default"),
        ("L3","GENERATION ATTESTATION","모델·프롬프트·입력·출력 digest + process seal","focal"),
        ("L2","EVIDENCE ATOMS","claim / source_url / retrieved_at / source_sha256 / used_count","default"),
        ("L1","SOURCE PROVENANCE","Aside queue audit · manual/API/US fallback gaps","store"),
    ]
    y=56
    for idx,name,sub,kind in labels:
        fill,stroke,text={"default":(WHITE,INK,INK),"focal":(ACCENT_TINT,ACCENT,INK),"risk":("rgba(184,84,80,.07)",RUST,INK),"store":("rgba(16,42,67,.05)",MUTED,INK)}[kind]
        body.append(f'<rect x="164" y="{y}" width="960" height="68" fill="{fill}" stroke="{stroke}"/><text x="188" y="{y+28}" class="svg-tag" fill="{stroke}" style="text-anchor:start">{idx}</text><text x="276" y="{y+30}" class="svg-name" style="text-anchor:start">{name}</text><text x="1096" y="{y+30}" class="svg-sub" style="text-anchor:end">{escape(sub)}</text>')
        y+=68
    body.append(f'<path d="M 124,380 V 80" stroke="{ACCENT}" stroke-width="1.6" marker-end="url(#{s}-accent)"/><text x="104" y="240" class="svg-arrow-label" transform="rotate(-90 104 240)">TRUST RISES</text>')
    body.append(legend([("문서·입력","solid"),("코드 강제","accent"),("발행 차단점","risk")],440,1280))
    return svg(s,"증거·컴플라이언스 강제 계층","소스 출처에서 증거 원자와 생성 증명을 거쳐 게시물 검수와 최종 발행 경계까지 신뢰가 누적되는 다섯 계층.",1280,480,"".join(body))


def generation_sequence_diagram() -> str:
    s="genseq"
    actors=[("run.py",100),("generate.py",340),("OpenRouter",580),("post_check",820),("publish.py",1060)]
    body=[]
    for name,x in actors:
        body.append(node(x-72,40,144,56,name,"", "ACTOR","focal" if name=="generate.py" else "default"))
        body.append(f'<line x1="{x}" y1="96" x2="{x}" y2="568" stroke="rgba(16,42,67,.22)" stroke-dasharray="3,3"/>')
    msgs=[
        (100,340,132,"generate_post(product,evidence)\n"),
        (340,580,176,"8 calls: 4× story + 4× value"),
        (580,340,220,"candidates + provider metadata"),
        (340,580,264,"1 blind tournament call"),
        (580,340,308,"winner + ranking"),
        (340,340,352,"seal model/input/output digests"),
        (340,820,396,"winner + provenance"),
        (820,340,440,"PASS / hard-fail reasons"),
        (340,100,484,"draft + report + evidence IDs"),
        (100,1060,528,"publish(dry_run? exact boundary)"),
    ]
    for i,(x1,x2,y,label) in enumerate(msgs):
        style="accent" if i in (5,6) else ("link" if i in (1,2,3,4) else "default")
        dashed=i in (2,4,7,8)
        color={"accent":ACCENT,"link":LINK,"default":MUTED}[style]
        marker={"accent":"accent","link":"link","default":"arrow"}[style]
        dash_attr=' stroke-dasharray="5,4"' if dashed else ""
        body.append(f'<path d="M {x1},{y} H {x2}" stroke="{color}" stroke-width="{1.6 if style=="accent" else 1.2}" fill="none"{dash_attr} marker-end="url(#{s}-{marker})"/>')
        lx=(x1+x2)/2
        tw=max(80,len(label)*6+20)
        body.append(f'<rect x="{lx-tw/2}" y="{y-22}" width="{tw}" height="14" fill="{PAPER}"/><text x="{lx}" y="{y-12}" class="svg-arrow-label">{escape(label.strip())}</text>')
    return svg(s,"생성·블라인드 토너먼트·실행 증명 시퀀스","오케스트레이터가 생성기를 호출하고 OpenRouter에서 후보 여덟 개와 블라인드 선발을 받은 뒤 프로세스 증명을 봉인하고 검수와 발행 경계로 전달하는 호출 순서.",1160,600,"".join(body))


def publish_sequence_diagram() -> str:
    s="pubseq"
    actors=[("run.py",100),("publish.py",340),("Threads Graph",580),("comments.py",820),("state ledgers",1060)]
    body=[]
    for name,x in actors:
        body.append(node(x-72,40,144,56,name,"", "ACTOR","focal" if name=="publish.py" else "external" if name=="Threads Graph" else "default"))
        body.append(f'<line x1="{x}" y1="96" x2="{x}" y2="584" stroke="rgba(16,42,67,.22)" stroke-dasharray="3,3"/>')
    msgs=[
        (100,340,132,"preflight: attestation + policy + link","default",False),
        (340,1060,176,"dry-run → preview.jsonl","default",True),
        (340,580,220,"POST /media: text or video","link",False),
        (580,340,264,"creation_id","link",True),
        (340,580,308,"POST /media_publish","link",False),
        (580,340,352,"media_id","accent",True),
        (340,580,396,"GET media existence","link",False),
        (340,1060,440,"append published.jsonl","default",False),
        (100,820,484,"thread/comment task","default",True),
        (820,580,528,"reply + disclosure + link","link",False),
        (820,1060,572,"commented/replies JSONL","default",False),
    ]
    for x1,x2,y,label,style,dashed in msgs:
        color={"accent":ACCENT,"link":LINK,"default":MUTED}[style]
        marker={"accent":"accent","link":"link","default":"arrow"}[style]
        dash_attr=' stroke-dasharray="5,4"' if dashed else ""
        body.append(f'<path d="M {x1},{y} H {x2}" stroke="{color}" stroke-width="{1.6 if style=="accent" else 1.2}" fill="none"{dash_attr} marker-end="url(#{s}-{marker})"/>')
        lx=(x1+x2)/2; tw=max(84,len(label)*6+20)
        body.append(f'<rect x="{lx-tw/2}" y="{y-22}" width="{tw}" height="14" fill="{PAPER}"/><text x="{lx}" y="{y-12}" class="svg-arrow-label">{escape(label)}</text>')
    return svg(s,"Threads 텍스트 발행·댓글 시퀀스","최종 경계 검사를 통과한 초안이 dry-run이면 preview로, 실발행이면 Threads 컨테이너 생성과 publish, 존재 검증, 원장 기록 및 댓글 워커로 이어지는 순서.",1160,624,"".join(body))


def data_stores_diagram() -> str:
    s="stores"
    body=[]
    body += [zone(24,48,376,472,"입력·지식"),zone(452,48,376,472,"실행·발행"),zone(880,48,376,472,"성과·영상")]
    body += [
        line(s,176,148,484,148),line(s,176,260,484,260),
        line(s,604,188,912,188),line(s,604,300,912,300),
        elbow(s,1092,332,604,412,via_x=852,dashed=True,label="weekly",label_y=372),
    ]
    left=[("sourcing_queue.json","audit status + provenance"),("evidence_atoms.jsonl","claims + hashes"),("story_bank.json","episodes + usage"),("viral_ux.json","proven / novel patterns")]
    mid=[("preview.jsonl","dry-run artifacts"),("published.jsonl","media_id + body + meta"),("holds.jsonl","flagged / held"),("comments*.jsonl","comment/reply idempotency")]
    right=[("analytics.jsonl","views + interactions"),("revenue.jsonl","click/order/commission"),("weekly_reports/","winners + prompt updates"),("state/video/","ledger + events + artifacts")]
    for col,items in zip([56,484,912],[left,mid,right]):
        for i,(t,sub) in enumerate(items): body.append(node(col,104+i*100,312,76,t,sub,"FILE","focal" if t=="evidence_atoms.jsonl" else "store"))
    body.append(legend([("파일/원장","solid"),("핵심 증거 SSOT","accent"),("주간 학습","dashed")],560,1280))
    return svg(s,"파일 기반 상태·증거 원장 지도","입력과 지식, 실행과 발행, 성과와 영상으로 나뉜 HeightCue의 JSON 및 JSONL 파일 원장과 주요 의존 관계.",1280,600,"".join(body))


def video_flow_diagram() -> str:
    s="video"
    body=[]
    body += [zone(24,48,304,492,"소재·계약"),zone(360,48,568,492,"생성·조립"),zone(960,48,296,492,"검수·발행")]
    # connectors
    body += [
        line(s,176,144,392,144), line(s,568,144,616,144,style="link"), line(s,792,144,992,144,style="link"),
        elbow(s,1080,184,1080,260,via_x=1080),
        line(s,992,300,880,300), line(s,704,300,568,300), line(s,392,300,328,300),
        elbow(s,176,340,176,416,via_x=176,style="accent",label="QA PASS",label_y=380),
        line(s,264,456,392,456,style="accent"), line(s,568,456,616,456,style="link"), line(s,792,456,992,456,style="link"),
    ]
    body += [
        node(56,104,240,80,"Real product assets","source URL · sha256 · market","INPUT","default"),
        node(392,104,176,80,"Storyboard","3 cuts × 5s · spoken line","CONTRACT","default"),
        node(616,104,176,80,"First frame","Codex · gpt-image-2","IMAGE","focal"),
        node(992,104,176,80,"FAL storage","signed upload → public URL","CLOUD","external"),
        node(992,260,176,80,"MiniMax H3 Max","768P · native audio/dialogue","I2V","external"),
        node(704,260,176,80,"OpenMontage","staging · transcript probe","MEDIA","default"),
        node(392,260,176,80,"Remotion compose","clean + subtitle/CTA + SRT","RENDER","default"),
        node(56,260,240,80,"QA + fidelity","decode · duration · speech · overlays","GATE","risk"),
        node(56,416,208,80,"Handoff packet","caption · media path · qa report","PACKET","store"),
        node(392,416,176,80,"Video ledger","ready_to_publish","STATE","store"),
        node(616,416,176,80,"Threads publish","container → publish","CHANNEL","external"),
        node(992,416,176,80,"Existence monitor","GET media · reconcile","VERIFY","coral"),
    ]
    body.append(legend([("로컬 계약","solid"),("구독·외부 API","link"),("충실도·품질 게이트","risk"),("핵심 이미지 단계","accent")],580,1280))
    return svg(s,"I2V UGC 영상 생성·검수·발행 경로","실제 상품 이미지와 계보에서 스토리보드, Codex 첫 프레임, FAL과 MiniMax H3 Max, OpenMontage와 Remotion 조립, QA, 발행 및 존재 검증으로 이어지는 영상 경로.",1280,620,"".join(body))


def video_state_diagram() -> str:
    s="vstate"
    body=[]
    # Core transitions first. Direct-to-dead-letter edges are kept in the
    # exact matrix below the figure so this view stays traceable.
    body += [
        line(s,80,160,120,160,label="enqueue",label_y=144),
        line(s,280,160,352,160,label="claim",label_y=144),
        line(s,512,160,584,160,style="accent",label="QA PASS",label_y=144),
        line(s,744,160,816,160,style="link",label="claim",label_y=144),
        line(s,976,160,1048,160,style="accent",label="VERIFY",label_y=144),
        f'<path d="M 432,200 V 332" fill="none" stroke="{RUST}" stroke-width="1.2" marker-end="url(#{s}-coral)"/>',
        f'<rect x="448" y="248" width="64" height="14" fill="{PAPER}"/><text x="480" y="259" class="svg-arrow-label" fill="{RUST}">QA FAIL</text>',
        f'<path d="M 664,200 V 332" fill="none" stroke="{MUTED}" stroke-width="1.2" stroke-dasharray="5,4" marker-end="url(#{s}-arrow)"/>',
        elbow(s,896,200,744,352,via_x=780,dashed=True),
        f'<path d="M 352,392 H 312 Q 304,392 304,384 V 240 Q 304,232 296,232 H 184 Q 176,232 176,224 V 200" fill="none" stroke="{MUTED}" stroke-width="1.2" stroke-dasharray="5,4" marker-end="url(#{s}-arrow)"/>',
        f'<path d="M 640,332 V 308 Q 640,300 632,300 H 328 Q 320,300 320,292 V 188 Q 320,180 312,180 H 280" fill="none" stroke="{MUTED}" stroke-width="1.2" stroke-dasharray="5,4" marker-end="url(#{s}-arrow)"/>',
        line(s,744,372,816,372,style="risk",label="attempts ≥ 3",label_y=356),
    ]
    body += [
        f'<circle cx="64" cy="160" r="7" fill="{INK}"/>',
        node(120,120,160,80,"queued","no lease","STATE","default"),
        node(352,120,160,80,"generating","lease owner","STATE","default"),
        node(584,120,160,80,"ready_to_publish","packet + QA","STATE","focal"),
        node(816,120,160,80,"publishing","lease owner","STATE","default"),
        node(1048,120,160,80,"published","terminal · media_id","STATE","dark"),
        node(352,332,160,80,"qa_failed","operator requeue","STATE","risk"),
        node(584,332,160,80,"retryable_failed","attempt tracked","STATE","optional"),
        node(816,332,160,80,"dead_letter","human required","STATE","risk"),
    ]
    body.append(legend([("핵심 정상 전이","solid"),("검증 성공","accent"),("재시도·회수","dashed"),("종결 실패","risk")],476,1280))
    return svg(s,"영상 잡 원장 핵심 상태 흐름","실제 8개 ledger 상태의 정상 경로와 QA 실패·재시도 경로를 보여 주며 모든 허용 전이는 바로 아래 행렬에 명시한다.",1280,516,"".join(body))


def org_chart_diagram() -> str:
    s="org"
    body=[]
    # parent buses before nodes
    body += [
        f'<path d="M 640,112 V 152 H 280 V 184 M 640,152 H 1000 V 184" fill="none" stroke="{MUTED}" stroke-width="1.2"/>',
        f'<path d="M 280,264 V 304 H 88 M 280,304 H 580 M 88,304 V 336 M 252,304 V 336 M 416,304 V 336 M 580,304 V 336" fill="none" stroke="{MUTED}" stroke-width="1.2"/>',
        f'<path d="M 1000,264 V 304 H 776 M 1000,304 H 1104 M 776,304 V 336 M 940,304 V 336 M 1104,304 V 336" fill="none" stroke="{MUTED}" stroke-width="1.2"/>',
        f'<path d="M 1160,224 H 1216 V 448 H 824 M 824,448 V 480 M 1104,448 V 480" fill="none" stroke="{MUTED}" stroke-width="1.2" stroke-dasharray="5,4"/>',
    ]
    body += [
        node(520,32,240,80,"폴리 · HeightCue OS","company orchestrator → repo SSOT","ROOT","focal"),
        node(120,184,320,80,"Delivery & Proof","송재현 · 서하늘 · 정나영 · 최유진","POD","default"),
        node(840,184,320,80,"Research & Revenue","이민재 · 김예린 · 정다은 · 배지훈 · 윤서진","POD","default"),
        node(16,336,144,88,"송재현","@jaehyun-publisher\npublisher · final gate","BOT","default"),
        node(180,336,144,88,"서하늘","@haneul-proof\nproof · compliance","BOT","default"),
        node(344,336,144,88,"정나영","@nayoung-threads-kr\nKR Threads · PM","BOT","default"),
        node(508,336,144,88,"최유진","@yujin-threads-us\nUS Threads · PM","BOT","default"),
        node(704,336,144,88,"이민재","@minjae-coupang\nCoupang sourcing","BOT","default"),
        node(868,336,144,88,"김예린","@yerin-amazon\nAmazon sourcing","BOT","default"),
        node(1032,336,144,88,"정다은","@daeun-research\ncontent intelligence","BOT","default"),
        node(704,480,240,88,"배지훈","@jihun-affiliate\naffiliate ops · weekly ROI","BOT","coral"),
        node(984,480,240,88,"윤서진","@seojin-brand\nfull-funnel · revenue KPI","BOT","coral"),
    ]
    body.append(legend([("책임 계층","solid"),("교차 기능 연결","dashed"),("명시된 수익 책임","risk"),("회사 오케스트레이터","accent")],600,1280))
    return svg(s,"HeightCue Hermes Bot 조직·책임 지도","회사 오케스트레이터 폴리 아래 HeightCue 운영 프로필 아홉 개를 Delivery·Proof와 Research·Revenue로 분리한 조직도. Delivery와 Engineering은 별도 경계다.",1280,640,"".join(body))


def scheduler_diagram() -> str:
    s="deploy"
    body=[]
    body += [zone(24,52,376,444,"macOS host",dashed=True),zone(452,52,376,444,"Hermes profiles",dashed=True),zone(880,52,376,444,"runtime + external",dashed=True)]
    body += [
        line(s,72+280,148,500,148,label="shell",label_y=132),
        line(s,72+280,292,500,292,dashed=True,label="routine",label_y=276),
        line(s,780,148,912,148,style="accent",label="commands",label_y=132),
        line(s,780,292,912,292,style="accent",label="same repo",label_y=276),
        elbow(s,1088,188,1088,356,via_x=1088,style="link",label="API/CLI",label_y=256),
        elbow(s,992,396,780,412,via_x=852,dashed=True,label="status",label_y=384),
    ]
    body += [
        node(72,108,280,80,"User crontab","9 jobs · PATH includes Aside","SCHED","default"),
        node(72,252,280,80,"Hermes gateway","default profile active","PROC","default"),
        node(500,108,280,80,"Script-only routines","publisher + affiliate profiles","CRON","risk"),
        node(500,252,280,80,"Agent routines","research · proof · sourcing · PM","CRON","default"),
        node(912,108,176,80,"Autopilot repo","run.py · scripts","CODE","focal"),
        node(912,252,176,80,"Aside CLI","authenticated browser work","CLI","external"),
        node(912,356,176,80,"JSONL state","file locks · ledgers","STORE","store"),
        node(1108,252,120,80,"External APIs","Threads · AI","NET","external"),
    ]
    body.append(legend([("실행","solid"),("Hermes routine","dashed"),("같은 런타임 경계","accent"),("중복 가능성","risk"),("외부 호출","link")],548,1280))
    return svg(s,"실행 배치·스케줄러 배치도","한 macOS 호스트의 user crontab과 Hermes profile routines가 동일 HeightCue 저장소와 Aside 및 외부 API를 호출하며 파일 원장을 공유하는 실제 배치 구조.",1280,588,"".join(body))


def lifoli_diagram() -> str:
    s="lifoli"
    body=[]
    body += [zone(24,52,360,428,"HeightCue execution plane"),zone(424,52,328,428,"verified browser ingress"),zone(792,52,464,428,"LiFoli Company OS",dashed=True)]
    body += [
        line(s,192,176,192,240),
        line(s,312,408,480,408,style="link",label="US CLICK RPC",label_y=384),
        line(s,312,264,436,264,dashed=True,label="NO LEDGER SYNC",label_y=240),
        f'<path d="M 676,368 H 744 Q 752,368 752,360 V 144 Q 752,136 760,136 H 856" fill="none" stroke="{LINK}" stroke-width="1.2" marker-end="url(#{s}-link)"/>',
        f'<rect x="764" y="116" width="80" height="14" fill="{PAPER}"/><text x="804" y="127" class="svg-arrow-label" fill="{LINK}">ADMIN READ</text>',
    ]
    body += [
        node(72,96,240,80,"Autopilot runtime","generation · publish · comments","CODE","focal"),
        node(72,240,240,80,"Local operating ledgers","JSON / JSONL · media_id · revenue","STATE","store"),
        node(72,368,240,80,"Public HeightCue site","app.js · US Amazon links only","WEB","external"),
        node(436,224,280,80,"No ledger sync worker","publication · insights · commission","GAP","risk"),
        node(480,368,216,80,"Public click RPC + DB","hc_public_events\nhc_funnel_observations","DB","coral"),
        node(856,96,240,80,"Company OS admin","Supabase-backed HeightCue views","UI","default"),
        node(816,224,200,80,"Projects · agents","ownership · assets · edits","MODEL","store"),
        node(1032,224,200,80,"Experiments · funnel","channels · publications","MODEL","store"),
        node(924,368,240,80,"KPI · profitability","targets · costs · controls","OS","default"),
    ]
    body.append(legend([("Autopilot 실행","solid"),("검증된 browser RPC","link"),("미배선 ledger bridge","risk"),("별도 control plane","dashed")],520,1280))
    return svg(s,"HeightCue와 LiFoli Company OS 경계","Autopilot과 Company OS는 별도 실행 경계이지만 공개 HeightCue 사이트의 US Amazon 외부 링크 클릭만 Supabase RPC로 직접 기록된다. 발행, Threads insights, 주문과 수수료 ledger 동기화 worker는 확인되지 않았다.",1280,560,"".join(body))


def flywheel_diagram() -> str:
    s="loop"
    cx,cy,r=640,304,208
    stations=[(640,64,"Demand","search · trends"),(840,152,"Source","products · UX"),(840,356,"Create","text / video"),(640,460,"Distribute","Threads KR / US"),(440,356,"Measure","views → orders"),(440,152,"Learn","prompts · sourcing")]
    body=[]
    # circular ring arcs and spokes first
    # hand tuned circular-ish arcs; type-specific loop arcs
    pts=[(640,104),(800,176),(800,364),(640,420),(480,364),(480,176)]
    for i in range(len(pts)):
        x1,y1=pts[i]; x2,y2=pts[(i+1)%len(pts)]
        body.append(f'<path d="M {x1},{y1} A {r},{r} 0 0 1 {x2},{y2}" fill="none" stroke="{MUTED}" stroke-width="1.2" marker-end="url(#{s}-arrow)"/>')
    for x,y,_,_ in stations:
        # spokes stop before hub
        sx=x + (0 if x==cx else (-80 if x>cx else 80)); sy=y + (40 if y<cy else -40)
        ex=cx + (0 if x==cx else (112 if x>cx else -112)); ey=cy + (64 if y>cy else -64 if y<cy else 0)
        body.append(f'<path d="M {sx},{sy} L {ex},{ey}" fill="none" stroke="{SOFT}" stroke-width="1" stroke-dasharray="5,4" marker-end="url(#{s}-arrow)"/>')
    for x,y,t,sub in stations:
        body.append(node(x-80,y-40,160,80,t,sub,"LOOP","coral" if t=="Measure" else "default"))
    body.append(node(528,240,224,128,"Evidence + Revenue","shared operating record","HUB","dark"))
    body.append(legend([("운영 흐름","solid"),("공유 원장 write-back","dashed"),("수익 측정 focal","risk")],560,1280))
    return svg(s,"HeightCue 수익 학습 플라이휠","수요, 소싱, 제작, 배포, 측정, 학습이 반복되고 각 단계가 증거와 수익의 공유 운영 원장으로 기록되는 강화 루프.",1280,600,"".join(body))


def atlas_body() -> str:
    return f'''<main>
<header class="hero">
  <div><p class="eyebrow">HEIGHTCUE · SYSTEM ATLAS · 2026-08-29 18:50 KST</p><h1>HeightCue 전체 시스템 아틀라스</h1><p class="lede">단순한 박스 연결도가 아니라, <strong>무엇이 선언됐고, 무엇이 코드로 강제되며, 무엇이 실제로 관측됐고, 어디가 아직 비어 있는지</strong>를 한 문서에서 분리해 읽도록 만든 실행·수익·조직·영상 지도입니다.</p></div>
  <aside class="hero-meta"><dl><dt>북극성</dt><dd>실제 제휴 주문·수수료·순수익</dd><dt>라이브 경로</dt><dd>Threads KR / US 텍스트</dd><dt>영상 경로</dt><dd>큐 존재 · process 미배선 / OFF</dd><dt>Company OS</dt><dd>US 공개 클릭만 직결</dd><dt>실행 기반</dt><dd>macOS · Python · Hermes · Aside</dd><dt>상태 기준</dt><dd>코드 + 로컬 원장 + live health</dd><dt>비밀정보</dt><dd>[REDACTED] · 문서에 미포함</dd></dl></aside>
</header>
<nav class="toc" aria-label="Atlas sections"><a href="#legend">읽는 법</a><a href="#context">전체 경계</a><a href="#text">텍스트</a><a href="#trust">증거·검수</a><a href="#stores">데이터</a><a href="#video">영상</a><a href="#agents">봇·운영</a><a href="#lifoli">Company OS</a><a href="#economics">수익 루프</a><a href="#truth">현재 진실</a><a href="#sources">근거</a></nav>

<section id="legend"><div class="section-head"><div class="section-no">00 / READ ME</div><div><h2>같은 사실처럼 보이면 안 되는 다섯 상태</h2><p class="section-intro">이 아틀라스의 핵심은 구조보다 <em>증거 수준</em>입니다. 문서의 희망, 코드의 강제, 런타임의 실제 관측을 한 색으로 섞지 않습니다.</p></div></div>
<div class="grid three">
 <div class="card"><span class="badge declared">Declared</span><h3>선언</h3><p>SSOT·AGENTS·운영 문서가 요구하는 목표와 규칙. 코드가 아직 강제하지 않을 수 있습니다.</p></div>
 <div class="card accent"><span class="badge enforced">Enforced</span><h3>강제</h3><p>실행 경계·검증기·상태 머신·락·멱등키처럼 우회하면 실패하는 코드 계약입니다.</p></div>
 <div class="card"><span class="badge observed">Observed</span><h3>관측</h3><p>2026-08-29 18:50 KST의 health, status, JSONL, process, crontab에서 실제 확인한 값입니다.</p></div>
 <div class="card"><span class="badge gap">Unverified</span><h3>미검증·미배선</h3><p>코드나 문서 조각은 있으나 end-to-end 실행이 입증되지 않은 경계입니다.</p></div>
 <div class="card risk"><span class="badge conflict">Conflict</span><h3>모순·위험</h3><p>문서끼리, 선언과 코드, 스케줄러끼리 어긋나거나 보안·중복 실행 위험이 있는 곳입니다.</p></div>
 <div class="card coral"><p class="eyebrow">경제적 기준</p><h3>게시량과 조회수는 중간 신호</h3><p>의사결정의 최종 기준은 클릭, 주문, 수수료, 비용을 합친 실제 제휴 순수익입니다.</p></div>
</div></section>

<section id="context"><div class="section-head"><div class="section-no">01 / CONTEXT</div><div><h2>전체 시스템과 책임 경계</h2><p class="section-intro">HeightCue는 하나의 거대한 앱이 아니라, 파일 기반 Autopilot 실행 plane을 중심으로 외부 수요·상품·AI·Threads와 연결되고, 영상 sidecar와 LiFoli control plane이 별도 경계에 놓인 시스템입니다.</p></div></div><div class="diagram-frame">{system_context_diagram()}</div>
<div class="grid three"><div class="card"><p class="eyebrow">TEXT PLANE</p><h3>현재 실운영 축</h3><p>KR/US 제품 소싱 → 증거 → 텍스트 생성 → 검수 → Threads → 댓글 → 분석/수익.</p></div><div class="card"><p class="eyebrow">VIDEO SIDECAR</p><h3>계약·큐 구현, 종단 미배선</h3><p>원장 3건이 <code>queued</code>이고 두 enable 플래그가 OFF입니다. <code>video process</code>도 아직 오케스트레이터 미배선으로 종료됩니다.</p></div><div class="card"><p class="eyebrow">COMPANY OS</p><h3>분리된 control plane + 클릭 ingress</h3><p>공개 사이트의 US Amazon 링크 클릭만 Supabase RPC로 직결됩니다. 로컬 발행·insights·revenue 원장은 동기화되지 않습니다.</p></div></div></section>

<section id="text"><div class="section-head"><div class="section-no">02 / TEXT</div><div><h2>수요 → 증거 → 발행 → 수익 텍스트 루프</h2><p class="section-intro"><code>run.py</code>가 단일 CLI 오케스트레이터입니다. 일일 실행은 분석·소싱·콘텐츠 생성, 시간대 실행은 유형/시장별 게시, 댓글 워커는 별도 3분 cadence로 움직입니다.</p></div></div><div class="diagram-frame">{text_pipeline_diagram()}</div>
<table><thead><tr><th>단계</th><th>핵심 모듈</th><th>입력 → 출력</th><th>실패 의미</th></tr></thead><tbody>
<tr><td>Demand / harvest</td><td><code>harvest.py</code>, <code>viral_intelligence.py</code></td><td>Aside 검색·UX 후보 → evidence/viral/UX stores</td><td><code>demand_signals.json</code>과 그 producer는 현재 없어 명시적 수요 큐가 비어 있음</td></tr>
<tr><td>Product source</td><td><code>sourcing.py</code></td><td>Coupang/Amazon 후보 → 링크 모드·상품 메타</td><td>KR Aside queue만 <code>is_audit_approved()</code>로 fail closed. <code>manual_products.json</code>·Coupang Open API fallback과 US registry는 같은 감사 계약을 우회</td></tr>
<tr><td>Evidence</td><td><code>evidence.py</code></td><td>검증 주장/출처 → claim atom + SHA-256</td><td>가치글의 관측 수치·주장은 근거 없이 쓰면 안 됨</td></tr>
<tr><td>Generate / select</td><td><code>generate.py</code></td><td>상품+증거+voice → 8개 후보 → blind winner</td><td>실패 시 저품질 fallback 발행 대신 후보 없음/보류</td></tr>
<tr><td>Check</td><td><code>post_check.py</code></td><td>당선 초안 → PASS 또는 hard fail / risk notes</td><td>hard fail은 publish boundary에서 재검증되어 차단</td></tr>
<tr><td>Publish / thread</td><td><code>publish.py</code>, <code>run.py</code></td><td>초안 → preview 또는 Threads media_id + JSONL</td><td>컨테이너/발행/존재 확인 실패 시 성공으로 간주 금지</td></tr>
<tr><td>Engage</td><td><code>comments.py</code></td><td>게시물/댓글 → idempotent replies + ledger</td><td>실패는 다음 3분 실행에서 재처리 가능</td></tr>
<tr><td>Learn</td><td><code>analytics.py</code>, <code>improve.py</code></td><td>성과+<code>revenue.json</code> → weekly report / playbook</td><td>게시물별 주문·수수료 자동 귀속이 닫히지 않아 조회수·클릭 대리 지표에 편향</td></tr>
</tbody></table>
<div class="callout"><strong>실행 증명:</strong> 생성기는 provider/model, 입력·출력 digest, prompt/hash를 provenance로 남기고 publish boundary가 같은 프로세스의 seal을 확인합니다. 다만 seal 키가 프로세스 로컬이어서 사후 독립 검증 가능한 장기 서명은 아닙니다.</div>
<div class="callout"><strong>핵심 공백:</strong> 신호 수집 조각은 있으나 명시적 demand signal 생성 원장이 없고, 실제 주문·수수료를 publication/sub-ID에 자동 귀속하는 종단 데이터 경로도 확인되지 않았습니다.</div></section>

<section id="trust"><div class="section-head"><div class="section-no">03 / TRUST</div><div><h2>증거, 실행 증명, 정책 게이트</h2><p class="section-intro">“좋은 글”이 아니라 “근거가 있고, 실제 지정 모델로 만들었으며, 규정을 통과한 글”만 발행 경계에 도달해야 합니다.</p></div></div><div class="diagram-frame">{evidence_layers_diagram()}</div><div class="diagram-frame">{generation_sequence_diagram()}</div>
<table><thead><tr><th>통제</th><th>상태</th><th>실제 강제 위치</th><th>남는 공백</th></tr></thead><tbody>
<tr><td>증거 원자 provenance</td><td><span class="badge enforced">Enforced</span></td><td><code>evidence.py</code> schema · URL scheme · SHA-256 · used_count</td><td>원 출처 품질 자체는 사람이/수집기가 판단</td></tr>
<tr><td>상품 소싱 provenance</td><td><span class="badge enforced">Partial</span> <span class="badge conflict">Bypass</span></td><td>KR Aside queue의 audit owner·price/review/official provenance</td><td>manual/Open API fallback과 US registry에는 동등한 audit gate가 없음</td></tr>
<tr><td>지정 생성 모델</td><td><span class="badge enforced">Enforced</span></td><td><code>execution_contract.py</code> + generation provenance</td><td>프로세스 종료 후 HMAC 재검증 불가</td></tr>
<tr><td>표현·고지·형식</td><td><span class="badge enforced">Enforced</span></td><td><code>post_check.py</code>와 publish preflight의 이중 경계</td><td>모든 의미적 오해를 정규식으로 잡을 수는 없음</td></tr>
<tr><td>관측하지 않은 수치 금지</td><td><span class="badge declared">Declared</span> <span class="badge enforced">Partial</span></td><td>evidence 사용 규칙 + causal gate</td><td>자유 서술 전체에 대한 완전한 사실 검증은 미검증</td></tr>
<tr><td>광고·제휴 고지</td><td><span class="badge enforced">Enforced</span> <span class="badge conflict">Drift</span></td><td>본문/댓글/영상 overlay 계약</td><td>KR site arm은 <code>run.py</code>가 랜딩 고지를 전제하지만 <code>post_check.py</code>는 판매글 둘째 줄의 exact 고지를 요구; 영상 픽셀 검증도 제한적</td></tr>
<tr><td>사람 승인</td><td><span class="badge declared">Conditional</span></td><td>flagged/hold와 weekly review</td><td><code>auto_publish_clean=true</code>인 clean 텍스트는 무인 발행</td></tr>
</tbody></table></section>

<section id="publish"><div class="section-head"><div class="section-no">04 / PUBLISH</div><div><h2>Threads 발행·댓글의 정확한 호출 순서</h2><p class="section-intro">성공은 HTTP 200이나 파일 존재가 아닙니다. 실발행에서는 컨테이너 생성, publish, media_id 회수, 존재 확인, 원장 기록이 모두 필요합니다.</p></div></div><div class="diagram-frame">{publish_sequence_diagram()}</div>
<div class="grid two"><div class="card"><h3>Dry run</h3><p><code>mode.dry_run=true</code>이거나 <code>publish=false</code>면 <code>preview.jsonl</code>에만 기록합니다. media_id 없는 리허설은 실제 게시물이 아닙니다.</p></div><div class="card accent"><h3>Live publish</h3><p>실행 attestation과 정책을 다시 확인한 뒤 Threads Graph API를 호출하고, 반환 media_id와 공개 존재 여부를 성공 기준으로 삼습니다.</p></div></div></section>

<section id="stores"><div class="section-head"><div class="section-no">05 / DATA</div><div><h2>파일 원장과 상태 데이터</h2><p class="section-intro">주요 operational SSOT는 데이터베이스가 아니라 <code>autopilot/state/</code> 아래의 JSON·JSONL 파일입니다. 각 파일의 “존재”가 아니라 필드와 상태 전이가 진실입니다.</p></div></div><div class="diagram-frame">{data_stores_diagram()}</div>
<div class="callout"><strong>동시성 경계:</strong> 영상 원장은 atomic replace + lock + lease를 명시적으로 구현합니다. 텍스트 JSONL의 모든 writer가 같은 수준의 파일 락·dedupe를 공유하는지는 파일별로 다르므로, 스케줄 중복은 단순한 운영 잡음이 아니라 실제 중복 게시 위험입니다.</div></section>

<section id="video"><div class="section-head"><div class="section-no">06 / VIDEO</div><div><h2>I2V UGC: 실제 상품 → 네이티브 대사 → 3개 산출물</h2><p class="section-intro">영상은 별도 원장·예산·리스·QA를 가진 sidecar입니다. 개별 구성요소와 실험 산출물은 존재하지만, 현행 <code>run.py video process</code>는 <strong>종단 오케스트레이터 미배선</strong>으로 종료하므로 아래 경로 전체가 자동 실행된다고 읽으면 안 됩니다.</p></div></div><div class="diagram-frame">{video_flow_diagram()}</div>
<div class="grid three"><div class="card accent"><h3>산출물 1 · Clean master</h3><p>네이티브 음성 + 전 구간 광고 고지. 자막/CTA를 굽지 않은 후보정 기준본.</p></div><div class="card"><h3>산출물 2 · Published master</h3><p>고지 + 자막 + CTA. 자막·CTA는 상품을 가리지 않도록 안전영역을 지켜야 합니다.</p></div><div class="card"><h3>산출물 3 · SRT sidecar</h3><p>대사 타이밍을 외부 자막 파일로 보존. 한국어는 <code>keep-all</code>과 balanced wrapping 규칙.</p></div></div>
<table><thead><tr><th>영상 불변 조건</th><th>강제/검수 지점</th><th>현재 상태</th></tr></thead><tbody>
<tr><td>실제 상품 이미지와 출처 hash</td><td><code>product_assets.py</code>, <code>product_fidelity.py</code></td><td>주 원장 3건에 공식 이미지 URL·SHA·rights evidence 존재; 모두 queued/attempts 0</td></tr>
<tr><td>첫 프레임은 Hermes openai-codex / gpt-image-2</td><td><code>codex_image_bridge.py</code></td><td>경로 고정 · OAuth 구독 사용</td></tr>
<tr><td>H3 Max 768P · 5초/컷 · 네이티브 오디오/대사</td><td><code>video_generate.py</code>, storyboard contract</td><td>실험 spend ledger는 실제 비용을 기록하고 H.264/AAC 768×1344 · 5.184초 컷 1개를 디코딩 확인; production 설정은 OFF</td></tr>
<tr><td>고지 전 구간 · 22px/500 · scrim .32</td><td>Remotion composition + QA</td><td>선언/렌더 계약 · 픽셀 검증은 제한적</td></tr>
<tr><td>음성 존재·전사 품질</td><td>OpenMontage venv + faster-whisper base probe</td><td>import/실행 probe 존재 · 한국어 의미 정확도는 사람 QA 필요</td></tr>
<tr><td>종단 worker</td><td><code>run.py video process</code></td><td><span class="badge gap">BLOCKED</span> 현재 “미배선” 메시지와 exit 5; claim→generate→compose→QA 자동 전이 없음</td></tr>
<tr><td>시장 gate</td><td><code>VIDEO_DEFAULTS["markets"]</code></td><td><span class="badge conflict">Mismatch</span> config에 video 섹션이 없어 기본 <code>["KR"]</code>; 주 원장 3건은 모두 US라 설정을 그대로 쓰면 대상 밖</td></tr>
<tr><td>회귀 검증</td><td><code>test_fal_upload.py</code></td><td><span class="badge conflict">31 pass · 1 fail</span> 폐기된 <code>motion_prompt</code> 인자를 테스트가 계속 전달하는 계약 drift</td></tr>
<tr><td>완전 디코딩·실재 Threads media</td><td>ffprobe/decoder + publish monitor</td><td>경로 존재 · end-to-end 실발행 0</td></tr>
</tbody></table>
<div class="diagram-frame">{video_state_diagram()}</div>
<h3>코드 정본 전이 행렬</h3>
<table><thead><tr><th>현재 상태</th><th>허용되는 다음 상태 — 그 외는 모두 <code>StateError</code></th></tr></thead><tbody>
<tr><td><code>queued</code></td><td><code>generating</code> · <code>retryable_failed</code> · <code>dead_letter</code></td></tr>
<tr><td><code>generating</code></td><td><code>ready_to_publish</code> · <code>qa_failed</code> · <code>retryable_failed</code> · <code>dead_letter</code></td></tr>
<tr><td><code>qa_failed</code></td><td><code>queued</code> · <code>dead_letter</code></td></tr>
<tr><td><code>ready_to_publish</code></td><td><code>publishing</code> · <code>retryable_failed</code> · <code>dead_letter</code></td></tr>
<tr><td><code>publishing</code></td><td><code>published</code> · <code>retryable_failed</code> · <code>dead_letter</code></td></tr>
<tr><td><code>retryable_failed</code></td><td><code>queued</code> · <code>dead_letter</code></td></tr>
<tr><td><code>published</code> · <code>dead_letter</code></td><td>종결 상태 — 다음 전이 없음</td></tr>
</tbody></table>
<div class="grid two"><div class="card"><h3>멱등성과 리스</h3><p>시장+상품+소스 hash+스토리보드 fingerprint+파이프라인 버전이 idempotency key입니다. claim은 900초 lease, 최대 3회 시도, stale lease 회수와 dead letter를 가집니다.</p></div><div class="card risk"><h3>계약 ≠ 자동 운영</h3><p>상태 머신·예산·생성·합성·QA·handoff 모듈이 있어도 <code>process</code> dispatcher가 없으면 원장은 움직이지 않습니다. publishing 회수 시에는 외부 존재 reconciliation을 생성 재진입보다 먼저 해야 합니다.</p></div></div></section>

<section id="agents"><div class="section-head"><div class="section-no">07 / ORCHESTRATION</div><div><h2>Hermes Bot Mode, 스케줄러, Aside</h2><p class="section-intro">역할은 프로필로 격리되지만 실제 Python 실행은 같은 HeightCue 저장소와 파일 원장을 공유합니다. Delivery(콘텐츠·발행)와 Engineering(코드·QA)은 분리 원칙이며, 여기에는 HeightCue 운영 프로필만 표시합니다.</p></div></div><div class="diagram-frame">{org_chart_diagram()}</div><div class="diagram-frame">{scheduler_diagram()}</div>
<table><thead><tr><th>실행군</th><th>대표 cadence</th><th>호출</th><th>관측 상태</th></tr></thead><tbody>
<tr><td>macOS user crontab</td><td>08:30 harvest · 09:30 daily · 12:30/16:00/19:30 posts · 댓글 3분 · 일 21:00 weekly · health 4시간 · log rotate</td><td>repo venv의 <code>harvest.py</code>, <code>run.py</code>, <code>health.py</code></td><td><span class="badge observed">9 jobs</span> health가 등록 확인</td></tr>
<tr><td>Publisher script routines</td><td>daily/midday/afternoon/evening/comments</td><td>동일 <code>run.py</code> 명령</td><td><span class="badge conflict">Overlap risk</span> user crontab과 시간·명령 겹침</td></tr>
<tr><td>Research / sourcing agents</td><td>시간별·일별</td><td>Aside + 코드/파일 감사</td><td>프로필별 enabled routine과 최근 run 기록 존재</td></tr>
<tr><td>Default Hermes gateway</td><td>상시</td><td>Bot routing · cron · desktop</td><td><span class="badge observed">launchd running</span> <span class="badge conflict">CLI drift</span> profile-local cron list는 동시에 “gateway not running”을 오표시</td></tr>
<tr><td>사람 에스컬레이션</td><td>예외 시</td><td>검색어 승인 · OAuth scope · 실행 계약 변경 · 공개 중지/비가역 조치</td><td>clean 자동 게시와 분리된 사람 gate</td></tr>
<tr><td>Browser-dependent work</td><td>필요 시</td><td><code>aside --account u0 exec/repl</code></td><td><span class="badge enforced">Default path</span> health에서 접근 가능</td></tr>
</tbody></table>
<div class="callout"><strong>중요:</strong> 프로필이 분리돼도 스크립트가 동일 저장소를 호출하면 스케줄러 중복은 격리되지 않습니다. 라이브 user crontab과 publisher routine의 09:30, 12:30, 16:00, 19:30, 주간 명령은 단일 소유자로 정리하거나 멱등성을 발행 경계까지 증명해야 합니다.</div></section>

<section id="lifoli"><div class="section-head"><div class="section-no">08 / COMPANY OS</div><div><h2>LiFoli는 별도 control plane — 공개 클릭 한 구간만 직접 연결</h2><p class="section-intro">Company OS는 HeightCue의 프로젝트·에이전트·KPI·수익성 모델과 관리 화면을 제공합니다. 직접 확인된 런타임 연결은 공개 사이트의 <strong>US Amazon 외부 링크 클릭 → Supabase RPC</strong>뿐이며, Autopilot의 발행·Threads insights·주문·수수료 원장 동기화는 미배선입니다.</p></div></div><div class="diagram-frame">{lifoli_diagram()}</div>
<table><thead><tr><th>경계</th><th>HeightCue Autopilot</th><th>LiFoli Company OS</th><th>연결 상태</th></tr></thead><tbody>
<tr><td>실행</td><td>Python CLI·cron·Hermes routines</td><td>관리 UI·조직·프로젝트 control plane</td><td>분리</td></tr>
<tr><td>직접 연결</td><td><code>app.js</code>가 US Amazon 링크 클릭만 전송</td><td><code>hc_record_public_click</code> → <code>hc_public_events</code> + <code>hc_funnel_observations</code></td><td><span class="badge observed">코드 경로 관측</span> KR site click은 이 RPC에서 건너뜀</td></tr>
<tr><td>로컬 상태</td><td>JSON/JSONL · media_id · evidence · revenue</td><td>Supabase tables/migrations · admin views</td><td>publication/insights/revenue sync worker 미발견</td></tr>
<tr><td>성과</td><td>Threads analytics + 제휴 revenue ledger</td><td>클릭·KPI·profitability·project metrics 표현</td><td>공개 클릭은 기록; 주문·수수료는 <code>null</code>로 남아 북극성 귀속 미완성</td></tr>
<tr><td>에이전트</td><td>Hermes profiles + routines</td><td>조직도·책임·운영 가시화</td><td>개념/관리 모델 연결</td></tr>
</tbody></table></section>

<section id="economics"><div class="section-head"><div class="section-no">09 / ECONOMICS</div><div><h2>수익이 학습을 닫는 플라이휠</h2><p class="section-intro">조회수나 문장 점수는 “무엇이 먹혔는지”를 추정하는 중간 신호입니다. 시스템이 완성되는 지점은 실제 링크 클릭·주문·수수료·생성비가 증거와 다시 결합될 때입니다.</p></div></div><div class="diagram-frame">{flywheel_diagram()}</div>
<table><thead><tr><th>계층</th><th>지표</th><th>의사결정에 쓰는 법</th></tr></thead><tbody>
<tr><td>Attention</td><td>views · likes · replies · reposts</td><td>훅/서사/시간대의 초기 진단. 최종 승자 판정 금지.</td></tr>
<tr><td>Intent</td><td>link clicks · landing visits</td><td>CTA·제품 적합도·링크 접근성 진단.</td></tr>
<tr><td>Conversion</td><td>orders · conversion rate</td><td>실제 구매 발생 여부. 채널/상품/포스트 연결 필요.</td></tr>
<tr><td>Economics</td><td>commission · refunds · AI/media cost · net profit</td><td>북극성. 다음 소싱·콘텐츠 유형·영상 예산 배분을 결정.</td></tr>
</tbody></table><div class="callout"><strong>명칭 주의:</strong> <code>analytics.attribution_complete</code>는 훅·각도·상품·폼팩터 같은 콘텐츠 메타데이터 완전성입니다. 주문·수수료 경제 귀속 완료를 뜻하지 않으며 현행 <code>conversions</code>·<code>commission</code> 결합은 비어 있습니다.</div></section>

<section id="truth"><div class="section-head"><div class="section-no">10 / SNAPSHOT</div><div><h2>2026-08-29 현재 운영 진실</h2><p class="section-intro">아래는 선언값이 아니라 18:50 KST에 <code>health.py --json</code>, <code>run.py status</code>, <code>run.py video status --json</code>, crontab과 프로세스에서 다시 읽은 관측값입니다.</p></div></div>
<div class="metric-row"><div class="metric accent"><span class="value">OK</span><span class="label">overall health</span></div><div class="metric"><span class="value">22</span><span class="label">검증된 실발행 이력</span></div><div class="metric"><span class="value">29</span><span class="label">preview 리허설</span></div><div class="metric"><span class="value">10 / 7</span><span class="label">증거 원자 / 미사용</span></div><div class="metric risk"><span class="value">3 · OFF</span><span class="label">영상 queued · 생성 비활성</span></div></div>
<table><thead><tr><th>항목</th><th>관측</th><th>해석</th></tr></thead><tbody>
<tr><td>발행 게이트</td><td><span class="status-dot ok"></span><code>publish=true</code>, <code>dry_run=false</code></td><td>clean 텍스트 자동 실발행이 현재 허용됨</td></tr>
<tr><td>최근 발행</td><td><span class="status-dot ok"></span>약 2.7시간 전</td><td>live path가 최근 동작</td></tr>
<tr><td>소싱 큐</td><td><span class="status-dot warn"></span>감사 보류 31 · 통과 0 · 대기 0</td><td>신규 감사 통과 재고가 비어 있음</td></tr>
<tr><td>US registry</td><td><span class="status-dot warn"></span>1건</td><td>US 상품 다양성/재고가 얇음</td></tr>
<tr><td>보류함</td><td><span class="status-dot warn"></span>1건</td><td>weekly review 대상</td></tr>
<tr><td>댓글 cron</td><td><span class="status-dot ok"></span>3분 전 실행</td><td>engagement worker cadence 정상</td></tr>
<tr><td>오류</td><td><span class="status-dot ok"></span>최근 24h harvest 2건, 이후 회복 확인</td><td>현재 overall fail 사유 아님</td></tr>
<tr><td>영상</td><td><span class="status-dot warn"></span>enabled=false · production_generation_enabled=false · markets 기본 KR · $2/day · max 1/run</td><td>주 queue 3건은 US라 gate 불일치; 별도 $1.40 실험 spend/output은 있으나 queued jobs의 상태 전이와 연결되지 않음</td></tr>
</tbody></table>
<h3>결함·모순·운영 위험</h3>
<table><thead><tr><th>심각도</th><th>관측된 문제</th><th>왜 위험한가</th><th>정확한 다음 조치</th></tr></thead><tbody>
<tr><td><span class="badge conflict">Critical</span></td><td><code>cron.log</code>에 외부 API query credential이 평문으로 기록된 흔적</td><td>로컬 로그·백업·공유에서 credential 노출 가능</td><td>credential 회전, URL query logging 제거, 기존 로그 안전 정리</td></tr>
<tr><td><span class="badge conflict">High</span></td><td>user crontab과 Hermes publisher routines의 명령/시간 중복</td><td>중복 생성·중복 게시·댓글 중복·비용 중복 가능</td><td>스케줄 소유자 하나로 통합 + 발행 멱등키를 외부 media까지 검증</td></tr>
<tr><td><span class="badge conflict">High</span></td><td><code>run.py video process</code> 종단 오케스트레이터 미배선</td><td>5분 poller가 실행돼도 queued 잡은 생성·합성·QA로 전이하지 않음</td><td>claim→generate→compose→QA dispatcher를 상태 전이·예산·lease 계약에 연결</td></tr>
<tr><td><span class="badge conflict">High</span></td><td>상품 provenance gate가 KR Aside queue에만 적용</td><td>manual/Open API/US registry 상품이 audit owner·review/official provenance 없이 판매글 소재가 될 수 있음</td><td>모든 source adapter가 공통 <code>ProductEvidence</code>와 audit gate를 통과한 뒤에만 <code>pick()</code></td></tr>
<tr><td><span class="badge conflict">High</span></td><td>영상 기본 market KR ↔ queued jobs US</td><td>process를 배선·활성화해도 현재 3건은 market gate 밖</td><td>US를 명시적으로 승인하거나 queue를 허용 market과 맞춘 뒤 canary</td></tr>
<tr><td><span class="badge conflict">High</span></td><td>영상 publishing 회수 후 생성 경로 재진입</td><td>외부 API가 성공한 뒤 worker가 죽으면 재발행 가능성</td><td>recovered_from=publishing이면 먼저 Threads existence reconcile</td></tr>
<tr><td><span class="badge gap">Gap</span></td><td>LiFoli에는 공개 US 클릭 RPC만 연결; publication/insights/revenue sync 미배선</td><td>Company OS의 퍼널·수익 숫자가 로컬 운영 진실과 drift할 수 있음</td><td>source timestamp와 reconciliation status를 가진 read-only ledger sync worker</td></tr>
<tr><td><span class="badge gap">Gap</span></td><td><code>demand_signals.json</code>과 producer 부재</td><td>신호 수집과 상품 소싱 사이에 명시적 수요 계약·재현 가능한 큐가 없음</td><td>관측 출처·시각·시장·의도·confidence를 가진 demand signal 원장 정의</td></tr>
<tr><td><span class="badge gap">Gap</span></td><td>게시물별 주문·수수료 자동 귀속 미완성</td><td>북극성 대신 조회·좋아요·클릭을 최적화할 위험</td><td>affiliate report → sub-ID/publication → order/commission → net profit reconciliation</td></tr>
<tr><td><span class="badge conflict">Drift</span></td><td>profile cron CLI의 gateway false negative + FAL adapter test 1건 실패</td><td>실행 중인 scheduler를 정지로 오판하거나 stale test 계약을 production 결함으로 오판</td><td>multiplex-aware status probe + <code>motion_prompt</code> 폐기 계약에 테스트 갱신</td></tr>
<tr><td><span class="badge gap">Gap</span></td><td>영상 end-to-end 실발행 0</td><td>계약·단위 테스트가 실제 네이티브 음성, 픽셀 고지, Graph publish를 보장하지 않음</td><td>enable 전 1건 canary: generation → decode → transcript → visual QA → private review → publish verification</td></tr>
<tr><td><span class="badge conflict">Drift</span></td><td>문서별 모델명·발행량·프로필 완료·각색 허용 표현 불일치</td><td>운영자가 다른 문서를 읽고 다른 규칙을 적용</td><td>SSOT에 현재 결정만 남기고 코드 상수를 import하는 검증 추가</td></tr>
</tbody></table></section>

<section id="operator"><div class="section-head"><div class="section-no">11 / OPERATOR</div><div><h2>운영자가 실제로 만지는 진입점</h2><p class="section-intro">대시보드가 모든 진실을 대표하지 않습니다. 장애·실행·상태 판단은 아래 순서와 실제 외부 산출물을 기준으로 합니다.</p></div></div>
<div class="grid two"><div class="card accent"><h3>상태 먼저</h3><p><code>cd autopilot && ../.venv/bin/python health.py</code></p><p><code>../.venv/bin/python run.py status</code></p><p><code>../.venv/bin/python run.py video status --json</code></p></div><div class="card"><h3>텍스트 실행</h3><p><code>run.py daily</code> · <code>run.py post story kr</code> · <code>run.py post value us</code> · <code>run.py comments</code> · <code>run.py weekly</code></p></div><div class="card"><h3>영상 CLI</h3><p><code>video enqueue</code> · <code>publish</code> · <code>reconcile</code>는 구현. <code>video process</code>는 현재 종단 미배선으로 <strong>exit 5</strong>이므로 작동하는 worker로 취급하지 않습니다.</p></div><div class="card risk"><h3>성공 판정</h3><p>크론 exit나 파일 존재만 보지 않습니다. 텍스트/영상 모두 <strong>media_id + Threads Graph 존재 확인</strong>이 외부 부작용의 최종 증거입니다.</p></div></div></section>

<section id="sources"><div class="section-head"><div class="section-no">12 / SOURCES</div><div><h2>근거·파일 지도</h2><p class="section-intro">비밀정보와 private identifier는 포함하지 않았습니다. 경로는 저장소 상대 경로로 표시합니다.</p></div></div>
<ul class="source-list">
<li><code>heightcue-SSOT-v2.md</code> — 전략·북극성·채널·불변 규칙</li><li><code>AGENTS.md</code> — 파일 지도·실행 규칙·봇 핸들</li><li><code>LAUNCH-STATUS.md</code> — 운영 준비/관측 상태</li><li><code>README-autopilot.md</code> — CLI와 크론 사용법</li><li><code>context/execution-contract.json</code> — 모델/발행 실행 계약</li><li><code>context/user-intent-contract.md</code> — 사용자 의도·비타협 조건</li><li><code>context/compliance.md</code> — 금칙·고지·법적 경계</li><li><code>autopilot/run.py</code> — CLI 오케스트레이터와 영상 미배선 경계</li><li><code>autopilot/sourcing.py</code> — KR/US 소싱·감사</li><li><code>autopilot/evidence.py</code> — 증거 원자 SSOT</li><li><code>autopilot/harvest.py</code> — Aside 기반 신호 수집</li><li><code>autopilot/generate.py</code> — 후보 생성·블라인드 선발</li><li><code>autopilot/execution_contract.py</code> — 실행 provenance·seal</li><li><code>autopilot/post_check.py</code> — 정책/인과/형식 검사</li><li><code>autopilot/publish.py</code> — Threads 발행 경계</li><li><code>autopilot/comments.py</code> — 댓글·답글 워커</li><li><code>autopilot/analytics.py</code> — 성과 수집</li><li><code>autopilot/improve.py</code> — 주간 개선 루프</li><li><code>autopilot/video_*.py</code> — 영상 계약·큐·생성·QA·handoff</li><li><code>autopilot/product_*.py</code> — 상품 자산·충실도</li><li><code>app.js</code> — 공개 US Amazon 클릭 RPC</li><li><code>OpenMontage/</code> — 전사·조립·미디어 QA 경계</li><li><code>lifoli/supabase/migrations/20260821_heightcue_public_clicks_v44.sql</code> — 공개 클릭 DB 경로</li><li><code>~/.hermes/profiles/*</code> — Bot profile·SOUL·routines</li><li><code>crontab -l</code> — live user scheduler</li>
</ul>
<h3>실행 모듈 지도 · <code>autopilot/*.py</code> 직접 범위 63 files</h3>
<table><thead><tr><th>경계</th><th>현행 모듈</th><th>책임</th></tr></thead><tbody>
<tr><td>Control</td><td><code>run.py</code> · <code>common.py</code> · <code>health.py</code> · <code>validate.py</code></td><td>CLI 라우팅, 설정/상태 경로, 운영 health, 입력 검증</td></tr>
<tr><td>Demand & sourcing</td><td><code>harvest.py</code> · <code>sourcing.py</code> · <code>briefing.py</code> · <code>collect_viral_ugc.py</code> · <code>viral_ugc.py</code></td><td>Aside 신호 수집, KR/US 상품 감사, 인텔리전스·콘텐츠 brief</td></tr>
<tr><td>Evidence & creation</td><td><code>evidence.py</code> · <code>generate.py</code> · <code>viral_intelligence.py</code> · <code>execution_contract.py</code> · <code>post_check.py</code></td><td>원자 원장, 8+1 토너먼트, 모델/입출력 seal, 인과·형식·정책 게이트</td></tr>
<tr><td>Distribution & learning</td><td><code>publish.py</code> · <code>comments.py</code> · <code>analytics.py</code> · <code>improve.py</code></td><td>예약/검증 발행, 댓글·답글, 성과·수익 ledger, 주간 개선</td></tr>
<tr><td>Video contracts</td><td><code>video_contracts.py</code> · <code>video_queue.py</code> · <code>video_storyboard.py</code> · <code>product_assets.py</code> · <code>product_fidelity.py</code></td><td>상태·스키마 SSOT, lease/멱등성, 컷 계약, 자산 provenance·상품 동일성</td></tr>
<tr><td>Video execution</td><td><code>codex_image_bridge.py</code> · <code>fal_upload.py</code> · <code>video_generate.py</code> · <code>stage_assets.py</code> · <code>video_compose.py</code> · <code>video_qa.py</code> · <code>video_handoff.py</code> · <code>monitor_video_publish.py</code></td><td>첫 프레임→I2V→조립→QA→발행 packet→외부 존재 reconcile</td></tr>
<tr><td>Verification</td><td><code>test_*.py</code> 27개 + 별도 검증 스크립트</td><td>텍스트 계약·댓글·증거·영상 상태·생성·QA·handoff·모니터 검증. 현재 focused FAL suite는 31 pass / 1 contract-drift fail</td></tr>
</tbody></table>
<h3><code>autopilot/state/</code> 전체 인벤토리 · 57 files</h3>
<table><thead><tr><th>영역</th><th>파일 수</th><th>정본/산출물</th></tr></thead><tbody>
<tr><td>발행·운영 진실</td><td>7</td><td><code>published.jsonl</code> · <code>preview.jsonl</code> · <code>holdbox.jsonl</code> · <code>deletions.jsonl</code> · <code>gone_posts.json</code> · <code>errors.jsonl</code> · verified migration backup</td></tr>
<tr><td>Engagement</td><td>3</td><td><code>comments_log.jsonl</code> · <code>replies_handled.json</code> · <code>comments.lock</code></td></tr>
<tr><td>증거·리서치·콘텐츠 메모리</td><td>14</td><td><code>evidence.jsonl</code> · <code>evidence_rejects.jsonl</code> · <code>insight_atoms.json</code> · <code>ux_discovery.json</code> · <code>sourced_history.json</code> · <code>us_products.json</code> · viral seeds/goldens · account/model memory · brief/playbook · intelligence JSON/MD</td></tr>
<tr><td>성과·수익·개선</td><td>3</td><td><code>metrics.jsonl</code> · <code>revenue.json</code> · <code>weekly_report.md</code></td></tr>
<tr><td>Aside browser queue</td><td>4</td><td><code>browser-queue/requests.json</code> · <code>results.json</code> · <code>failed.json</code> · re-audit backup</td></tr>
<tr><td>영상 원장·비용·fixture</td><td>7</td><td><code>video/ledger.json</code> · <code>events.jsonl</code> · <code>spend_ledger.json</code> + <code>_18d_*</code> 검증 fixture 4개</td></tr>
<tr><td>상품 자산 provenance</td><td>7</td><td>상품별 <code>product_assets.json</code> · events ledger · source image 5개</td></tr>
<tr><td>Preview·archive·logs·housekeeping</td><td>12</td><td>preview HTML 5 · dry-run archive 4 · <code>cron.log</code> · <code>rehearsal-run.log</code> · <code>.DS_Store</code></td></tr>
</tbody></table>
<div class="callout"><strong>레거시·동결 영역:</strong> Shorts·articles·<code>en/</code>·<code>cn/</code>·pre-v46 Company OS publication 객체·retired D3 URL은 현행 텍스트/영상 실행 경로에 재진입시키지 않는 audit/archive 영역입니다.</div>
<h3>외부 의존성과 신뢰 경계</h3>
<table><thead><tr><th>외부 시스템</th><th>용도</th><th>신뢰/실패 경계</th></tr></thead><tbody>
<tr><td>Aside CLI</td><td>인증 브라우저 검색·수집·검증</td><td>주제별 독립 호출, JSON 회수/파싱, health에서 접근성 관측</td></tr>
<tr><td>Coupang · Amazon</td><td>KR/US 상품·affiliate 목적지</td><td>KR Aside queue는 audit/provenance 강제; manual/API/US registry adapter는 동등 gate 미배선</td></tr>
<tr><td>OpenRouter Gemini</td><td>본문 후보 8개 + 블라인드 심사 1회</td><td>실행 모델은 runtime config 정본, provider metadata와 digest seal 필요</td></tr>
<tr><td>Threads Graph API</td><td>KR/US 게시·스레드·댓글·analytics</td><td>publish 응답만 믿지 않고 ID+본문 GET 대조 후 <code>verified</code>; 불일치는 <code>verification_pending</code></td></tr>
<tr><td>Hermes openai-codex</td><td>실제 상품사진 기반 첫 프레임</td><td><code>gpt-image-2-medium</code> / underlying <code>gpt-image-2</code> 외 fail closed</td></tr>
<tr><td>FAL storage · MiniMax</td><td>자산 업로드 · <code>minimax/h3-max/image-to-video</code></td><td>9:16 · 768P · 5초/컷 · 비용 ledger; 두 enable gate가 현재 OFF</td></tr>
<tr><td>OpenMontage · ffmpeg · faster-whisper</td><td>조립·디코딩·오디오/전사 QA·SRT</td><td>별도 venv/interpreter probe; import 성공과 실제 한국어 의미 품질을 구분</td></tr>
<tr><td>HeightCue public site · Supabase RPC</td><td>US Amazon 외부 링크 click ingress</td><td><code>app.js</code> → <code>hc_record_public_click</code>; 클릭만 직접 기록하고 주문·수수료는 채우지 않음</td></tr>
<tr><td>LiFoli Company OS</td><td>조직·프로젝트·KPI·profitability control plane</td><td>관리 스키마/UI는 관측; Autopilot publication·Threads insights·affiliate revenue sync worker는 미배선</td></tr>
</tbody></table>
<div class="callout"><strong>근거 우선순위:</strong> <code>heightcue-SSOT-v2.md</code> → 현행 코드 → <code>LAUNCH-STATUS.md</code> → <code>README-autopilot.md</code>. 다만 “실제로 돌고 있는가”는 언제나 live state와 외부 media existence가 문서를 이깁니다.</div></section>
<footer>HEIGHTCUE SYSTEM ATLAS · GENERATED 2026-08-29 · BRAND TOKENS: PAPER #F7F5F1 · INK #102A43 · ACCENT #0E8074 · CORAL #FF966F · SECRETS [REDACTED]</footer>
</main>'''


POSTER_CSS = '''
body.poster { overflow:auto; }
.poster-wrap { width:1280px; min-height:720px; margin:0 auto; padding:24px 32px; background:var(--paper); }
.poster-head { display:flex; justify-content:space-between; align-items:flex-end; border-bottom:1px solid var(--rule); padding-bottom:12px; }
.poster-head h1 { font-size:44px; }
.poster-kicker { color:var(--muted); font-size:13px; max-width:520px; text-align:right; }
.poster-canvas { margin-top:16px; }
.poster-svg { width:100%; height:auto; display:block; }
.poster-foot { display:grid; grid-template-columns:1.25fr 1fr 1fr; gap:16px; margin-top:12px; }
.poster-foot .card { padding:12px 14px; font-size:11px; }
.poster-foot h3 { font-size:13px; margin-bottom:5px; }
.poster-foot p { margin:0; color:var(--muted); }
@media(max-width:1280px){ .poster-wrap{ transform-origin:top left; } }
'''


def poster_diagram() -> str:
    s="poster"
    body=[]
    # Zones
    body += [zone(16,40,1216,112,"ORCHESTRATION — macOS crontab · Hermes profiles · Aside CLI"), zone(16,168,1216,224,"LIVE TEXT REVENUE PATH"), zone(16,416,1216,112,"VIDEO SIDECAR — COMPONENTS OBSERVED · PROCESS UNWIRED / OFF",dashed=True), zone(16,552,1216,112,"TRUTH + LEARNING + COMPANY OS")]
    # Primary arrows first
    xs=[40,232,424,616,808,1000]
    for i in range(5): body.append(line(s,xs[i]+152,280,xs[i+1],280,style="accent" if i==2 else "default"))
    # orchestrator triggers
    for x in [116,308,500,692,884,1076]: body.append(f'<path d="M {x},136 V 224" stroke="{MUTED}" stroke-dasharray="5,4" marker-end="url(#{s}-arrow)"/>')
    # feedback and sidecar
    body += [
        f'<path d="M 1076,328 H 1200 Q 1208,328 1208,336 V 532 Q 1208,540 1200,540 H 760 C 752,540 752,528 744,528 C 736,528 736,540 728,540 H 448 Q 440,540 440,548 V 572" fill="none" stroke="{LINK}" stroke-width="1.2" stroke-dasharray="5,4" marker-end="url(#{s}-link)"/>',
        f'<rect x="1016" y="516" width="92" height="14" fill="{PAPER}"/><text x="1062" y="527" class="svg-arrow-label">ECONOMICS</text>',
        f'<path d="M 744,572 V 412 Q 744,404 736,404 H 408 Q 400,404 400,396 V 312 Q 400,304 392,304 H 384" fill="none" stroke="{MUTED}" stroke-width="1.2" stroke-dasharray="5,4" marker-end="url(#{s}-arrow)"/>',
        f'<rect x="504" y="380" width="112" height="14" fill="{PAPER}"/><text x="560" y="391" class="svg-arrow-label">WEEKLY LEARNING</text>',
        f'<path d="M 332,328 V 368 Q 332,376 324,376 H 156 Q 148,376 148,384 V 436" fill="none" stroke="{MUTED}" stroke-width="1.2" stroke-dasharray="5,4" marker-end="url(#{s}-arrow)"/>',
        f'<rect x="176" y="352" width="112" height="14" fill="{PAPER}"/><text x="232" y="363" class="svg-arrow-label">ELIGIBLE PRODUCT</text>',
        f'<path d="M 1152,328 H 1216 Q 1224,328 1224,336 V 552 Q 1224,560 1216,560 H 1192 Q 1184,560 1184,568 V 572" fill="none" stroke="{LINK}" stroke-width="1.2" marker-end="url(#{s}-link)"/>',
        f'<rect x="1136" y="540" width="72" height="14" fill="{PAPER}"/><text x="1172" y="551" class="svg-arrow-label" fill="{LINK}">US CLICK RPC</text>',
        line(s,256,472,280,472,style="link",dashed=True),
        line(s,496,472,520,472,style="link",dashed=True),
        line(s,736,472,760,472,style="link",dashed=True),
        line(s,976,472,1000,472,style="link",dashed=True),
    ]
    # orchestration strip
    body += [node(40,68,216,68,"User crontab","9 live jobs","SCHED","default"),node(280,68,216,68,"Hermes Bot Mode","Foli + 9 specialist profiles","AGENTS","focal"),node(520,68,216,68,"Aside CLI","authenticated browser path","BROWSER","external"),node(760,68,216,68,"Shared repo + state","file ledgers","RUNTIME","store"),node(1000,68,216,68,"External services","retail · AI · Threads","CLOUD","external")]
    # stages
    stages=[("1. SIGNAL","trends · UX\nproducts"),("2. PROVE","KR queue audit\nevidence · fallback gaps"),("3. CREATE","8 candidates\nblind tournament"),("4. GATE","attestation\npolicy + disclosure"),("5. PUBLISH","Threads KR / US\ncomments · replies"),("6. MEASURE","views → clicks\norders/commission GAP")]
    for i,(t,sub) in enumerate(stages):
        body.append(node(xs[i],224,152,104,t,sub,"STAGE","focal" if i==1 else "risk" if i==3 else "coral" if i==5 else "default"))
    # video
    body += [node(40,436,216,72,"Real product + storyboard","3 US jobs · default market KR","VIDEO","optional"),node(280,436,216,72,"Codex first frame","gpt-image-2","IMAGE","optional"),node(520,436,216,72,"FAL → H3 Max 768P","native dialogue · component run","I2V","optional"),node(760,436,216,72,"OpenMontage + QA","clean · captioned · SRT","QA","optional"),node(1000,436,216,72,"Threads handoff","not reached end-to-end","PUBLISH","optional")]
    # bottom
    body += [node(40,572,240,64,"Observed truth","health · JSONL · media_id","STATE","store"),node(320,572,240,64,"Evidence + revenue hub","shared economic record","NORTH STAR","dark"),node(600,572,240,64,"Weekly improve","prompts · sourcing · cadence","LEARN","default"),node(880,572,336,64,"LiFoli Company OS","US click RPC only · ledgers unsynced","CONTROL","optional")]
    return svg(s,"HeightCue 전체 시스템 요약 포스터","Hermes와 macOS 스케줄러가 부분 감사 텍스트 경로를 운용하고, US queue와 KR-only 기본 설정이 어긋난 미배선 영상 sidecar, US 공개 클릭만 직접 연결된 LiFoli control plane을 분리한 한 장 지도.",1248,680,"".join(body),cls="poster-svg")


def poster_body() -> str:
    return f'''<div class="poster-wrap"><div class="poster-head"><div><p class="eyebrow">HEIGHTCUE · ONE-PAGE SYSTEM MAP</p><h1>수요를 증거로, 증거를 수익으로.</h1></div><p class="poster-kicker">실운영 텍스트 루프 · process 미배선 영상 sidecar · Hermes 조직/스케줄 · US 클릭만 직결된 LiFoli control plane을 한 화면에 분리했습니다. Snapshot: 2026-08-29 18:50 KST.</p></div><div class="poster-canvas">{poster_diagram()}</div><div class="poster-foot"><div class="card accent"><h3>북극성</h3><p>게시량·조회수가 아니라 실제 클릭 → 주문 → 수수료 → 생성비를 합친 제휴 순수익.</p></div><div class="card"><h3>현재 관측</h3><p>health OK · 실발행 22 · evidence 10(미사용 7) · 영상 queued 3 / process OFF / default KR.</p></div><div class="card risk"><h3>최우선 위험</h3><p>소싱 fallback audit 우회 · 영상 US↔KR gate · 스케줄 중복 · credential 로그 · 경제 귀속 미배선.</p></div></div></div>'''


def main() -> None:
    atlas = html_shell("HeightCue 전체 시스템 아틀라스 · 2026-08-29", "HeightCue 텍스트·영상·증거·발행·분석·수익·Hermes Bot Mode·LiFoli Company OS 전체 구조 아틀라스", atlas_body())
    poster = html_shell("HeightCue 한 장 시스템 지도 · 2026-08-29", "HeightCue 전체 시스템을 한 화면에 요약한 포스터", poster_body(), extra_css=POSTER_CSS).replace('<body>', '<body class="poster">')
    atlas_path = OUT / "heightcue-system-atlas-2026-08-29.html"
    poster_path = OUT / "heightcue-system-poster-2026-08-29.html"
    atlas_path.write_text(atlas, encoding="utf-8")
    poster_path.write_text(poster, encoding="utf-8")
    print(f"wrote {atlas_path} ({atlas_path.stat().st_size} bytes)")
    print(f"wrote {poster_path} ({poster_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
