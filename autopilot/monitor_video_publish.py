#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HeightCue I2V UGC — 발행 대기 영상 감시자 (Task 15).

한 줄 요약: **QA 를 통과해 발행만 남은 영상이 생겼을 때에만, 딱 그 사실만
바이트로 안정되게 찍는다.**

왜 이 파일이 따로 있는가
------------------------
Hermes 크론의 monitor 모드는 매 틱 이 스크립트를 **LLM 없이** 돌리고 stdout 의
정확한 바이트를 직전 틱과 비교한다. 같으면 에이전트를 아예 깨우지 않고, 다르면
diff 를 프롬프트에 실어 깨운다. 따라서 이 스크립트의 유일한 계약은 결정성이다.

출력에 넣지 않는 것과 그 이유:
* **시각·경과시간** (``created_at``/``updated_at``/리스 남은 시간) — 매 틱 달라져
  발행할 영상이 하나도 없어도 5분마다 LLM 이 깨어난다.
* **캡션·링크·패킷 전문** — 깨우는 신호에 본문이 필요 없다. 에이전트는 깨어난 뒤
  ``video_handoff.py claim`` 으로 권위 있는 패킷을 직접 읽는다. 여기에 캡션을
  실으면 오래된 사본을 보고 발행할 위험이 생긴다.
* **자격증명** — 원장이 오염돼도 크론 로그로 새지 않도록 화이트리스트로만 찍는다.

정렬은 job_id 사전순으로 고정한다. 원장의 물리적 순서(생성 시각·회수 순서)는
같은 상태에서도 흔들릴 수 있고, 그 흔들림이 곧 헛된 기동이다.

읽기 전용이다 — 원장을 절대 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import video_handoff as vh  # noqa: E402
import video_queue as vq  # noqa: E402

#: 발행할 영상이 없을 때의 출력. 빈 문자열이 아니라 **안정된 표지**를 쓴다 —
#: 빈 출력은 '모니터가 죽었다'와 구분되지 않는다.
NO_READY_OUTPUT = "video_publish_ready=0\n"

#: sha256 은 동일성 확인용 접두만 찍는다. 전문은 패킷에 있다.
SHA_PREFIX_LEN = 12


def _row(packet: Dict[str, Any]) -> str:
    """패킷 하나 → 한 줄. 화이트리스트한 필드만, 고정된 순서로."""
    sha = str(packet.get("video_sha256") or "")[:SHA_PREFIX_LEN]
    check = "yes" if packet.get("requires_existence_check") else "no"
    return (
        "job={job} state={state} market={market} product={product} "
        "attempts={attempts} sha={sha} existence_check={check}"
    ).format(
        job=packet.get("job_id") or "?",
        state=vh.STATE_READY_TO_PUBLISH,
        market=packet.get("market") or "?",
        product=packet.get("product_id") or "?",
        attempts=int(packet.get("attempts") or 0),
        sha=sha,
        check=check,
    )


def collect(ledger: Any) -> List[Dict[str, Any]]:
    """발행 준비된 패킷 + 시도 횟수. job_id 사전순으로 **고정** 정렬한다."""
    packets = vh.list_ready(ledger)
    attempts = {e["job_id"]: e.get("attempts", 0)
                for e in ledger.list_jobs(vh.STATE_READY_TO_PUBLISH)}
    enriched = [dict(p, attempts=attempts.get(p.get("job_id"), 0))
                for p in packets]
    return sorted(enriched, key=lambda p: str(p.get("job_id") or ""))


def render(ledger: Any) -> str:
    packets = collect(ledger)
    if not packets:
        return NO_READY_OUTPUT
    lines = ["video_publish_ready=%d" % len(packets)]
    lines.extend(_row(p) for p in packets)
    return "\n".join(lines) + "\n"


def emit(ledger: Any) -> None:
    sys.stdout.write(render(ledger))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="monitor_video_publish.py",
        description="발행 대기 영상 감시자 (Hermes 크론 monitor 모드용)")
    parser.add_argument("--root", default=None,
                        help="원장 디렉터리 (기본: state/video)")
    args = parser.parse_args(argv)

    # 모니터는 절대 시끄럽게 죽지 않는다. 예외 문구에는 경로·시각이 섞이기
    # 쉬운데, 그것이 stdout 으로 새면 매 틱 '변경됨'이 되어 에이전트를 깨운다.
    # 읽을 수 없는 원장은 '발행할 것 없음'과 같은 조용한 출력으로 떨어뜨리고,
    # 진단은 stderr 로만 보낸다(해시 대상이 아니다).
    try:
        ledger = vq.VideoLedger(args.root)
        sys.stdout.write(render(ledger))
    except Exception as exc:                       # noqa: BLE001 — 의도적 광폭
        sys.stdout.write(NO_READY_OUTPUT)
        print("monitor_video_publish: %s: %s" % (type(exc).__name__, exc),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
