# Findings

## Executive
- 운영 엔진은 가동 중이지만 경제 엔진은 아직 닫히지 않았다.
- 최신 확인 수익은 2026-08-27 기준 KR 0원, 주문 0, 누적 클릭 7. 그 이후 KR은 stale, US clicks/orders/commission은 unmeasured.
- 2026-09-01 05:45 브리프 기준 KR 15 root posts/2,051 views, US 18/198 views. 판매글 4건은 총 41 views, measured clicks 0.

## Live operations
- health overall fail: 유일한 실패는 KR 최근 12시간 검증 외부답글 없음. 나머지 발행, 댓글 cron, crontab 10개, Aside, 증거 재고, Company OS 상품, 계약은 green.
- 오늘 09:30 daily는 US discovery 1건 + bridge 1건만 발행. KR friction ledger 입력 0, KR 승인 후보 0, US 판매 소재 0으로 판매/verdict는 모두 skip.
- 8/31 이후 12 publications 중 root 10; 전부 value, sales/verdict 0. stage는 discovery 5, bridge 1, legacy 6.
- 라이브 사이트 링크는 전부 200. KR 홈은 승인 verdict가 비어 있어 제품 전환 여정이 막히고, US는 Ddrops verdict 1건이 있음.

## Quality and security
- pytest 1325 passed, 93 subtests passed. Changed-file ruff는 1 error(test_health.py E702). validate.py는 OpenRouter/KR/US/Amazon 필수 자격 green. video rehearsal은 backend green이나 production disabled.
- 작업 트리는 main==origin/main이지만 16 tracked files에 477 insertions/38 deletions + 다수 untracked 산출물이 남아 release baseline이 불명확.
- state/cron.log에 Threads access token이 예외 URL로 반복 기록되고 파일 권한은 0644. 즉시 redaction + token rotation 필요.
- insight_atoms.json에 placeholder DOI/URL 2건이 active로 존재. 그중 1건은 실제 US post media_id 18162317194476654로 발행됨. claim_gate는 source type/locator 존재만 검사하고 실제 resolvability/authenticity를 검증하지 않음.

## Org and orchestration
- 전용 역할은 sourcing, proof, publisher, KR/US quality, brand, affiliate, research, Amazon, QA/builder로 분리되어 있음.
- attribution experiment t_90875898은 blocked. direct candidate→review→publish 하위 task는 모두 done으로 표시됐지만 publish task 실제 결과는 fail-closed hold이며 media_id/permalink가 없음. Kanban done semantics가 acceptance와 불일치.
- Company Work HeightCue 검색 결과 79 tasks: done 56, blocked 20, todo 1, archived 2. 다수 unit-test fixture가 운영 보드에 섞여 status signal을 오염시킴.

## Remediation observations — 2026-09-01
- Revenue read-back 자동화: Aside Browser 재시작 후 KR와 US 모두 live 성공. KR MTD 15 clicks / 0 orders / ₩0, US MTD 0 clicks / 0 orders / $0. `revenue.json`과 attribution artifact 동기화됨.
- Coupang Open API: 상품 검색 및 deeplink 호출 모두 401. Partners Open API 화면의 생성 버튼은 비활성이고 My Info 접근은 human 2FA 요구. 2FA 전에는 고유 subId direct 발행 불가.
- Outreach root cause: `outreach_worker`가 세 번째 positional 값을 limit로 넘겼지만 `discover()`는 runner로 받아 `TypeError: 'int' object is not callable`. TDD로 수정.
- Outreach live read-back: KR reply `DcuumgJk4Nw`가 실제 존재하고 exact text/parent/author가 일치해 verified ledger 복구. health 외부답글 green.
- Source quality: 해당 원문은 사주/시주 주장이었으므로 이후 astrology/horoscope/zodiac/birth chart/psychic 및 사주/시주/원국/운세/타로/점성술/궁합 맥락을 fail-closed 차단.
- Health: revenue success snapshot을 error recovery 증거로 인식하도록 보강 후 `overall=ok`, 모든 check ok.
- KR friction: 현재 canonical ledger에서 `pick_signal(..., 'KR')`은 None. Threads 검색 `아이 영양제 먹이기 힘들다`는 관측 가능한 결과 없음. `장난감 치우기 힘들어`는 검색 노이즈뿐이었고, `장난감 정리` 결과는 affiliate 광고 2건 + 훈육 조언 1건이라 `external_complaint`로 부적격. 허구 신호나 D3 억지 연결 금지.
- Coupang 2FA/승인: 휴대폰 OTP가 통과했고 My Info 진입을 실제 확인했다. Threads 채널에만 활동 스크린샷이 누락되어 있었으며, Graph read-back된 기존 KR 판매글(고지 + Coupang 상품 카드)을 Aside로 렌더링해 540,338-byte PNG 증빙을 업로드했다. Coupang UI가 `내 정보가 성공적으로 변경되었습니다`라고 확인했다.
- Coupang Open API 후속 read-back: 증빙 저장 직후에도 `생성` 버튼은 `disabled`; UI 문구상 최종 승인 회원만 API 키 발급 가능하다. 따라서 2FA는 resolved지만 최종 승인/API 키는 여전히 외부 심사 게이트이며 완료로 간주하지 않는다.
- Attribution false approval: 원본 `browser-queue/results.json`에는 nutrition 링크가 `sub_id_applied=false`·기본 채널 생성으로 명시됐지만, 후속 candidate/evidence packet이 `planned_sub_id`를 실제 적용값으로 오인해 approved 처리했다. `audit_readiness_reasons()`가 문자열 존재만 검사하고 적용 관측값을 확인하지 않은 것이 원인.
- Attribution gate fix: KR `link.coupang.com` 결과는 `sub_id_applied is True`가 아니면 `sub_id_not_applied`로 hold한다. RED 1 실패 → GREEN 1 통과, queue E2E PASS. 해당 source row·candidate·evidence packet·experiment artifact를 hold/blocked로 정정했고 파일 모드는 0600.
