#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""상품 충실도 검사 — 생성 프레임을 **실제 상품 사진과 대조**한다 (Task 24).

왜 이게 존재하나
----------------
Task 22/23 은 두 결함을 "자동 검사 불가"로 결론냈다. 그 결론은 **오프라인
지각 해시(dHash)** 만 놓고 본 것으로, 그 범위에서는 옳다: 레퍼런스는 흰
배경 카탈로그 컷아웃이고 산출물은 주방·손·자연광 속의 같은 상품이라
밝기 구조 비교는 구조적으로 통과가 불가능하다.

그러나 **비전-언어 모델에게 생성 프레임과 실제 상품 사진을 나란히 주고
글자를 읽게 하는 것**은 전혀 다른 접근이고, 실제로 된다. 이 모듈이 그것이다.

잡아야 하는 두 결함 (둘 다 실제 유료 산출물에서 나왔다)
  1. **위조된 라벨 글자** — 녹색 뱃지가 ``ORGANIC`` 대신 ``ORCAIN``.
     한 글자 차이다. "비슷해 보인다"로는 절대 못 잡는다. 그래서 모델에게
     **글자를 그대로 읽어 문자열로 뱉으라고** 요구하고, 레퍼런스의 알려진
     표기와 대조시킨다.
  2. **물리적으로 불가능한 형상** — 병에 입구가 둘(위에 캡, 아래에 두 번째
     나사목에서 방울이 떨어짐). 실제 병은 목이 하나이고, 뒤집어서 쓴다.

정당한 뒤집힘을 결함으로 신고하지 않는 것이 중요하다. 병이 뒤집혀 라벨이
거꾸로 읽히는 것은 **정상**이며, 프롬프트가 이를 명시한다.

설계 원칙 (video_qa 와 동일)
---------------------------
* **FAIL CLOSED.** 모델에 닿지 못함 / 응답 파싱 불가 / 낮은 확신도는 전부
  실패다. 답하지 못한 비전 모델은 좋은 프레임의 증거가 아니다.
* **결함 나열이 verdict 를 이긴다.** 모델이 ``forged`` 를 적어놓고
  ``pass`` 라고 말하면 결함을 믿는다.
* **가독성은 충실도와 분리해서 보고한다.** 초점이 나가 브랜드를 못 읽는
  프레임은 위조가 아니므로 통과지만, 마케팅 소재로는 쓸모가 없다.
  ``brand_illegible`` 로 따로 신고한다 (판정하지는 않는다).
* **주입 시임.** ``client=`` 를 넣으면 네트워크가 0 이다. 테스트 전체가
  오프라인으로 돈다 (``fetcher=``/``runner=`` 하우스 패턴과 같다).
* **호출 예산이 있다.** ``max_calls`` 로 QA 1회 비용이 묶인다.

프로바이더
---------
OpenRouter 경유 ``config.json``의 ``openrouter.model`` — 이 레포가 이미
콘텐츠 생성에 쓰는 바로 그 경로이며, 자격증명은 읽지도 로그하지도 않는다
(``codex_image_bridge`` 와 같은 집 규칙).
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

# 한 번의 QA 실행에서 비전 모델을 부를 최대 횟수. 프레임이 이보다 많으면
# 균등 간격으로 솎아낸다 (앞부분만 보고 끝내지 않는다).
DEFAULT_MAX_CALLS = 4
#: 레퍼런스 사진 상한 — 토큰이 곧 돈이다.
DEFAULT_REFERENCE_LIMIT = 3
#: 이 아래면 "모델이 사실상 모른다"로 보고 실패시킨다.
MIN_CONFIDENCE = 0.55

#: 스테이징 자산은 **전부 JPEG** 다. 한때 비교기가 PNG 만 받아 검사가
#: 아예 돌지 못했다 — 그 버그를 여기서 되풀이하지 않는다.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

_REQUIRED_KEYS = ("on_pack_text", "geometry_findings", "colour_findings",
                  "missing_or_invented", "legibility", "verdict", "confidence")
_VERDICTS = ("pass", "fail")
_DEFECT_STATUSES = ("forged", "misspelled", "invented", "wrong")
_ILLEGIBLE = ("unreadable", "illegible")


class FidelityUnavailable(Exception):
    """검사를 수행할 수 없다 — 통과가 아니라 실패로 취급한다."""


# ---------------------------------------------------------------------------
# 이미지 인코딩
# ---------------------------------------------------------------------------


def encode_image_data_url(path: str) -> str:
    """이미지를 ``data:`` URL 로 만든다. 형식은 매직바이트로 실측한다.

    확장자를 믿지 않는다 — Amazon 재인코딩 자산은 이름과 실제가 어긋날 수
    있고, 잘못된 mime 은 조용한 오판독으로 이어진다.
    """
    if not isinstance(path, str) or not path.strip():
        raise FidelityUnavailable("이미지 경로가 비었다")
    full = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(full):
        raise FidelityUnavailable(f"이미지가 없다: {full}")
    if os.path.getsize(full) <= 0:
        raise FidelityUnavailable(f"이미지가 비었다: {full}")
    with open(full, "rb") as fh:
        blob = fh.read()
    mime = None
    for magic, candidate in _MAGIC:
        if blob.startswith(magic):
            mime = candidate
            break
    if mime is None:
        raise FidelityUnavailable(
            f"지원하지 않는 이미지 형식이다 (선두 {blob[:8]!r}): {full}")
    return f"data:{mime};base64," + base64.b64encode(blob).decode("ascii")


def reference_photos(asset_dir: str,
                     limit: int = DEFAULT_REFERENCE_LIMIT) -> List[str]:
    """스테이징된 상품 사진 경로를 고른다 (JPEG/PNG 모두 허용).

    디렉터리가 없으면 예외가 아니라 빈 목록이다 — 없다는 사실은 호출자가
    fail closed 로 처리한다.
    """
    if not asset_dir or not os.path.isdir(asset_dir):
        return []
    exts = (".jpeg", ".jpg", ".png")
    names = sorted(n for n in os.listdir(asset_dir)
                   if n.lower().endswith(exts))
    return [os.path.join(asset_dir, n) for n in names][:max(0, int(limit))]


def known_wording(asset_dir: str) -> List[str]:
    """매니페스트의 ``spec_facts`` 에서 온-팩 표기 힌트를 뽑는다 (있으면)."""
    manifest = os.path.join(asset_dir or "", "product_assets.json")
    if not os.path.isfile(manifest):
        return []
    try:
        with open(manifest, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:                              # noqa: BLE001
        return []
    out: List[str] = []
    for key in ("marketed_option", "product_id"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            out.append(val.strip())
    for fact in data.get("spec_facts") or []:
        if isinstance(fact, str) and fact.strip():
            out.append(fact.strip())
    return out


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------


PROMPT_HEADER = """You are a strict product-fidelity auditor for advertising footage.

The FIRST image is a GENERATED frame. Every image after it is a REAL
photograph of the actual product (ground truth).

Your job is to catch two failure classes that generative models produce:

1. FORGED / MISSPELLED ON-PACK TEXT. You must TRANSCRIBE the lettering you
   can actually read in the generated frame, letter by letter, and compare
   each string against the real photographs. A single wrong letter is a
   failure (e.g. reading "ORCAIN" where the real pack says "ORGANIC").
   Do not guess what the pack "should" say from memory — read the pixels,
   then compare with the reference photos.
   If a string is too small or too blurred to read, say so; do NOT invent it.

2. PHYSICALLY IMPOSSIBLE OR ALTERED GEOMETRY. Count the openings, necks,
   caps, spouts and seams on the product in the generated frame and compare
   with the real photographs. The real bottle has exactly ONE neck with ONE
   opening. A second orifice, a second threaded neck, a cap in an impossible
   place, or liquid leaving a sealed container is a failure.

IMPORTANT — do NOT flag these, they are legitimate:
* The bottle held INVERTED (upside-down) to dispense a drop. That is the
  correct way to use this product; the label reading upside-down in that
  pose is CORRECT, not a defect.
* The cap unscrewed and resting on a surface while the bottle dispenses.
* A kitchen/hand/natural-light setting instead of the white catalogue
  background of the reference photos. Background differences are NOT defects.
* Partial occlusion by fingers, cropping, or a soft/out-of-focus label.
  Out-of-focus text is NOT forged text — report it as legibility, not fidelity.

Also report: wrong colours or proportions, and any component that is missing
from, or invented onto, the product relative to the real photographs.
"""

PROMPT_SCHEMA = """
Reply with ONE JSON object and nothing else:

{
  "on_pack_text": [
    {"read": "<exact string you read in the generated frame>",
     "expected": "<the matching string on the real pack, or null>",
     "status": "faithful" | "forged" | "misspelled" | "illegible"}
  ],
  "geometry_findings": ["<impossible or altered geometry, empty list if none>"],
  "colour_findings":   ["<wrong colour/proportion, empty list if none>"],
  "missing_or_invented": ["<missing or invented component, empty if none>"],
  "legibility": "legible" | "partial" | "unreadable",
  "verdict": "pass" | "fail",
  "confidence": <number 0.0-1.0, your confidence in this verdict>,
  "notes": "<one or two sentences>"
}

"legibility" describes only whether the brand is readable at all; a soft
label is "unreadable" but still a "pass" if nothing is forged.
Set "verdict" to "fail" if ANY on_pack_text status is forged/misspelled, or
any geometry / missing_or_invented finding exists.
If you cannot see the product well enough to judge, give a low confidence.
"""


def build_prompt(known: Optional[Sequence[str]] = None) -> str:
    parts = [PROMPT_HEADER]
    tokens = [str(t).strip() for t in (known or []) if str(t).strip()]
    if tokens:
        parts.append(
            "Known wording that appears on the real pack (compare against the\n"
            "reference photographs too — this list is a hint, not exhaustive):\n"
            + "\n".join(f"  - {t}" for t in tokens))
    parts.append(PROMPT_SCHEMA)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 응답 파싱 — 못 읽으면 실패
# ---------------------------------------------------------------------------


def parse_verdict(text: Any) -> Dict[str, Any]:
    """모델 응답에서 구조화된 판정을 뽑는다. 실패하면 예외다 (fail closed)."""
    if not isinstance(text, str) or not text.strip():
        raise FidelityUnavailable("비전 모델이 빈 응답을 돌려줬다")
    raw = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if fence:
        raw = fence.group(1).strip()
    if not raw.startswith("{"):
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            raise FidelityUnavailable(
                f"응답에 JSON 객체가 없다: {text.strip()[:200]!r}")
        raw = raw[start:end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FidelityUnavailable(
            f"응답 JSON 파싱 실패: {exc} — {text.strip()[:200]!r}") from exc
    if not isinstance(data, dict):
        raise FidelityUnavailable(f"응답이 객체가 아니다: {type(data).__name__}")

    missing = [k for k in _REQUIRED_KEYS if k not in data]
    if missing:
        raise FidelityUnavailable(f"응답에 필수 키가 없다: {missing}")

    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in _VERDICTS:
        raise FidelityUnavailable(f"알 수 없는 verdict 값: {data.get('verdict')!r}")
    data["verdict"] = verdict

    conf = data.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        raise FidelityUnavailable(f"confidence 가 숫자가 아니다: {conf!r}")
    conf = float(conf)
    if not 0.0 <= conf <= 1.0:
        raise FidelityUnavailable(f"confidence 가 0..1 범위 밖이다: {conf}")
    data["confidence"] = conf

    for key in ("on_pack_text", "geometry_findings", "colour_findings",
                "missing_or_invented"):
        if data.get(key) is None:
            data[key] = []
        if not isinstance(data[key], list):
            raise FidelityUnavailable(f"{key} 가 배열이 아니다: {data[key]!r}")
    return data


def _defects(v: Dict[str, Any]) -> List[str]:
    """모델의 verdict 와 무관하게, 나열된 결함을 사실로 수집한다."""
    out: List[str] = []
    for item in v.get("on_pack_text") or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status in _DEFECT_STATUSES:
            out.append(
                f"on-pack text {status}: read {item.get('read')!r}, "
                f"expected {item.get('expected')!r}")
    for key, label in (("geometry_findings", "geometry"),
                       ("colour_findings", "colour"),
                       ("missing_or_invented", "component")):
        for item in v.get(key) or []:
            if isinstance(item, str) and item.strip():
                out.append(f"{label}: {item.strip()}")
    return out


# ---------------------------------------------------------------------------
# 기본 클라이언트 (OpenRouter) — 시임의 프로덕션 구현
# ---------------------------------------------------------------------------


def default_client(prompt: str, images: Sequence[str],
                   cfg: Optional[Dict[str, Any]] = None,
                   timeout: int = 180) -> str:
    """OpenRouter 비전 호출. 자격증명은 읽지도 로그하지도 않고 헤더로만 쓴다."""
    import requests                                # 지연 import — 오프라인 테스트 보호

    if cfg is None:
        import common
        cfg = common.load_config()
    node = (cfg or {}).get("openrouter") or {}
    api_key = node.get("api_key")
    model = node.get("vision_model") or node.get("model")
    if not api_key or not model:
        raise FidelityUnavailable(
            "openrouter 설정(api_key/model)이 없다 — 검사를 돌릴 수 없다")

    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for url in images:
        content.append({"type": "image_url", "image_url": {"url": url}})
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        json={"model": model,
              "messages": [{"role": "user", "content": content}],
              "temperature": 0.0},
        timeout=timeout,
        headers={"Authorization": f"Bearer {api_key}",
                 "HTTP-Referer": "https://heightcue.local",
                 "X-Title": "heightcue-autopilot"},
    )
    if resp.status_code != 200:
        raise FidelityUnavailable(
            f"비전 모델 HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()["choices"][0]["message"]["content"] or ""
    except Exception as exc:                       # noqa: BLE001
        raise FidelityUnavailable(f"비전 응답 구조가 예상과 다르다: {exc}") from exc


# ---------------------------------------------------------------------------
# 판정
# ---------------------------------------------------------------------------


def check_frame(frame_path: str, references: Sequence[str], *,
                client=None, known_wording: Optional[Sequence[str]] = None,
                min_confidence: float = MIN_CONFIDENCE) -> Dict[str, Any]:
    """프레임 1장을 실제 상품 사진과 대조한다. 못 하면 실패다."""
    result: Dict[str, Any] = {
        "frame_path": frame_path,
        "passed": False,
        "reason": "",
        "defects": [],
        "brand_illegible": False,
        "confidence": None,
        "verdict": None,
        "reference_count": len(references or []),
        "min_confidence": float(min_confidence),
    }
    refs = [r for r in (references or []) if r]
    if not refs:
        result["reason"] = ("대조할 실제 상품 사진이 없다 — 레퍼런스 없는 "
                            "충실도 검사는 통과가 아니다")
        return result

    try:
        images = [encode_image_data_url(frame_path)]
        images += [encode_image_data_url(r) for r in refs]
    except FidelityUnavailable as exc:
        result["reason"] = f"이미지 인코딩 실패: {exc}"
        return result

    prompt = build_prompt(known_wording)
    call = client or default_client
    try:
        raw = call(prompt, images)
    except Exception as exc:                       # noqa: BLE001 — fail closed
        result["reason"] = f"비전 모델 호출 실패: {exc}"
        return result

    try:
        verdict = parse_verdict(raw)
    except FidelityUnavailable as exc:
        result["reason"] = f"판정 파싱 실패: {exc}"
        return result

    result["raw_verdict"] = verdict
    result["verdict"] = verdict["verdict"]
    result["confidence"] = verdict["confidence"]
    result["notes"] = verdict.get("notes")
    result["legibility"] = verdict.get("legibility")
    result["brand_illegible"] = (
        str(verdict.get("legibility") or "").strip().lower() in _ILLEGIBLE)
    defects = _defects(verdict)
    result["defects"] = defects

    if defects:
        result["reason"] = "결함이 발견됐다: " + "; ".join(defects)
        return result
    if verdict["verdict"] != "pass":
        result["reason"] = ("모델이 fail 로 판정했다: "
                            + str(verdict.get("notes") or ""))
        return result
    if verdict["confidence"] < min_confidence:
        result["reason"] = (
            f"모델 confidence {verdict['confidence']:.2f} 가 임계 "
            f"{min_confidence:.2f} 미만이다 — 답하지 못한 검사는 통과가 아니다")
        return result

    result["passed"] = True
    result["reason"] = (f"실제 상품 사진 {len(refs)}장과 대조: 위조 글자 없음, "
                        f"불가능 형상 없음 (confidence "
                        f"{verdict['confidence']:.2f})")
    return result


def _thin(items: Sequence[str], budget: int) -> List[str]:
    """예산에 맞춰 균등 간격으로 솎아낸다 (앞부분만 보지 않는다)."""
    items = list(items)
    if budget <= 0 or not items:
        return []
    if len(items) <= budget:
        return items
    step = (len(items) - 1) / float(budget - 1) if budget > 1 else 0.0
    return [items[int(round(i * step))] for i in range(budget)]


def check_frames(frame_paths: Sequence[str], references: Sequence[str], *,
                 client=None, known_wording: Optional[Sequence[str]] = None,
                 max_calls: int = DEFAULT_MAX_CALLS,
                 min_confidence: float = MIN_CONFIDENCE) -> Dict[str, Any]:
    """여러 프레임을 예산 안에서 검사한다. 한 장이라도 걸리면 전체 실패."""
    out: Dict[str, Any] = {
        "passed": False, "reason": "", "frames": [], "calls": 0,
        "max_calls": int(max_calls), "reference_count": len(references or []),
        "brand_illegible_frames": [],
    }
    chosen = _thin([p for p in (frame_paths or []) if p], int(max_calls))
    if not chosen:
        out["reason"] = "검사할 프레임이 없다 — 돌지 못한 검사는 통과가 아니다"
        return out

    failures: List[str] = []
    for path in chosen:
        r = check_frame(path, references, client=client,
                        known_wording=known_wording,
                        min_confidence=min_confidence)
        out["frames"].append(r)
        out["calls"] += 1
        if r.get("brand_illegible"):
            out["brand_illegible_frames"].append(path)
        if not r["passed"]:
            failures.append(f"{os.path.basename(path)}: {r['reason']}")

    if failures:
        out["reason"] = " | ".join(failures)
        return out
    out["passed"] = True
    out["reason"] = (f"{len(chosen)}장을 실제 상품 사진 "
                     f"{len(references)}장과 대조: 결함 없음")
    if out["brand_illegible_frames"]:
        out["reason"] += (f" (다만 {len(out['brand_illegible_frames'])}장은 "
                          "브랜드가 판독 불가 — 위조는 아니나 소재 가치 낮음)")
    return out
