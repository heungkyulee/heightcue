# AGENTS.md — heightcue 파일 맵 (새 세션은 이것부터 읽기)

> 목적: 다음 세션이 "소싱 스킬이 어느 파일이지?"를 헤매지 않게 하는 지도.
> 갱신 규칙: 파일을 추가/이동/폐기하면 이 문서도 같이 고친다. 자가개선 파이프라인(improve.py)은 이 문서를 수정할 수 없다.

## 0. 30초 요약

- heightcue = 키 고민 부모 대상 Threads 단일 채널 어필리에이트 (KR 쿠팡 / US 아마존 투트랙).
- **이 레포(`~/heightcue-autopilot`)가 실행 SSOT다.** LiFoli Company OS(`lifoli.co.kr/admin#/heightcue`, Supabase v46)는 별도 트랙이며 서로 섞지 않는다.
- 실행은 이 맥의 crontab + Aside 루틴. 콘텐츠 생성 모델은 OpenRouter 경유 `google/gemini-3.1-pro-preview` (`-preview` 없는 슬러그는 존재하지 않음).
- **파이썬은 반드시 `~/heightcue-autopilot/.venv/bin/python3`** — 시스템/샌드박스 python3에는 `requests`가 없어 즉사한다.

## 1. 기획 문서 (우선순위 순)

| 파일 | 역할 |
|---|---|
| `heightcue-SSOT-v2.md` | **최신 기획서(SSOT).** 철칙 3개(§0), 브랜드/페르소나(§2), 가치글·판매글 2레이어(§3), 파이프라인 A/B/C(§4~6), US 트랙(§7), 자율 운영(§8), 부록 A 표현 가드레일, 부록 B 검사기 기준. v1 기획서(구 버전, 파이프라인 3개짜리 짧은 문서)는 이 문서로 대체됨 — v1이 다시 보이면 참고만 하고 기준으로 삼지 말 것 |
| `LAUNCH-STATUS.md` | **현재 가동 상태 대시보드.** 계정/토큰/발행 게이트/남은 인간 단계. "지금 뭐가 켜져 있지?"는 여기 |
| `README-autopilot.md` | 셋업 가이드 (venv, config.json, crontab, 리허설→golive 절차) |
| `README.md` | 공개 사이트용 리드미 (기획 문서 아님) |

## 2. 파이프라인 A — 소싱

| 파일 | 역할 |
|---|---|
| `autopilot/sourcing.py` | 소싱 코드. `top_up_requests()`는 **검증된 가치글 수요 신호(`demand_signals.json`)가 있을 때만** 요청을 만들며 카테고리 재고 로테이션을 금지한다. 감사 승인+상품/가격/리뷰/공식원문/subId provenance 하드게이트, UX 저장소 함수, 블랙리스트, 쿠팡 Open API(키 발급 전 미사용), US 레지스트리 `pick_us()` 포함 |
| `autopilot/state/ux_discovery.json` | **UX 폼팩터 단일 진실 공급원.** 라이프사이클 candidate(워커 발굴) → active(소싱 성공 승격) → retired(저성과 은퇴). 워커가 매 실행 실물 근거(evidence)와 함께 신규 후보 추가, improve.py가 주 1회 성과 반영 |
| `autopilot/state/revenue.json` | 월 수익 추적 (워커가 주 1회 쿠팡 파트너스 리포트에서 읽어 기록 — 무인). 주간 리포트 "북극성 월 수수료 1,000만원" 섹션의 입력 |
| `aside-sourcing-routine.md` | **소싱 기준 문서** (선정 기준, 폼팩터/UX 혁신 필터, results.json 스키마, 금지사항). Aside 루틴 프롬프트의 원본 |
| Aside 루틴 `HeightCue 쿠팡 소싱·UX발굴 워커` (ID `IHsZG7MfkKsKpE3Q`, 매시간) | **실제 소싱 실행기.** `validated` 수요 신호가 연결된 큐만 처리(최대 3건), provenance를 `pending`으로 제출하고 @mungchi-proof 승인 전 소비 금지. 공급자 중심 UX/카테고리 발굴만으로 신규 요청 생성 금지. 프롬프트는 Aside 루틴에 저장돼 있음 — 수정 시 이 문서·위 md와 3중 동기화할 것 |
| `autopilot/state/browser-queue/requests.json` | 소싱 요청 큐 (pending → filled/failed) |
| `autopilot/state/browser-queue/results.json` | 소싱 결과 (winner_reasons에 UX 혁신점 1문장 필수). **절대 수동으로 가짜 데이터를 넣지 말 것** — 2026-08-27 플레이스홀더 사고 전례 |
| `autopilot/state/sourced_history.json` | 소싱 이력 (중복 방지) |
| `autopilot/state/us_products.json` | US 판매 소재 레지스트리 (사이트 가이드 페이지 경유) |

## 3. 파이프라인 A — 콘텐츠 생성

| 파일 | 역할 |
|---|---|
| `heightcue-gemini-skills.md` | **콘텐츠 생성 스킬 프롬프트** (v2.2 합법 어그로 · 컨텍스트 분리). SKILL A2(마스터 조사 노트), A3-KR/A3-US(쓰레드 변환), A4(검수 의견), A5(댓글 분류), V1(가치글). `common.load_skill()`이 `context/`(compliance·persona·voice-kr/us) + 스킬 본문을 합성해 system 메시지로 주입 — 공통 규칙(금지 목록·페르소나·말투)은 `context/`에서, 스킬별 규칙은 이 파일에서 수정 |
| `context/` | **공통 프롬프트 컨텍스트** (v2.2 신설). `compliance.md`(합법 어그로 제1원칙·절대 금지 1~10·고지 불변 문구), `persona.md`(코어 3요소·하이브리드 각색), `voice-kr.md`/`voice-us.md`(말투·AI냄새 박멸·바이럴 문장). 가드레일 완화 금지·강화만 가능 (SSOT 부록 A 동기화) |
| `autopilot/generate.py` | LLM 호출 계층 (OpenRouter, JSON 모드, dry_run 지원, 언어 게이트) |
| `story-bank.md` | 운영자 실화 에피소드 저장소 (하이브리드 모드: 코어 3요소 고정, 세부 각색 허용, 제품 체험담 각색 금지). 자가개선이 읽기만 가능 |
| `autopilot/post_check.py` | 포스트 검사기 v3 (바이럴 포맷 점수 + 리스크 메모, 반려는 500자 초과뿐). 회귀: `post_check.py test_posts.json --test` (기준선 29/29) |
| `autopilot/test_posts.json` | 검사기 회귀 테스트셋 |

## 4. 파이프라인 B — 발행

| 파일 | 역할 |
|---|---|
| `autopilot/publish.py` | Threads 공식 API 발행 (컨테이너→publish, link_attachment/reply_to_id A/B, 토큰 주 1회 갱신) |
| `autopilot/run.py` | **오케스트레이터.** `daily`/`post`/`comments`/`weekly`/`rehearsal`/`status`/`golive`/`dryrun`/`context` 명령. 진입점은 항상 이 파일 |
| `crontab.txt` | 스케줄 원본 (09:30 daily / 12:30·16:00·19:30 post / 14:00 comments / 일 21:00 weekly). 등록: `crontab ~/heightcue-autopilot/crontab.txt` |
| `autopilot/config.json` | 실제 설정 (mode/cadence/openrouter/threads/coupang/amazon 키). git 미추적. 예시는 `config.example.json` |

## 5. 파이프라인 C — 분석·자가개선·댓글

| 파일 | 역할 |
|---|---|
| `autopilot/analytics.py` | 지표 수집 (Threads Insights + URL별 클릭, **폼팩터/ux_grade 태그 포함**) → `state/metrics.jsonl`. 주간 요약에 `by_ux_grade`(proven vs novel 성과 비교) |
| `autopilot/improve.py` | 주간 자가개선 — **`state/playbook.md` 갱신 경로로만.** 스킬/SSOT/스토리뱅크 자동 수정 금지. 추가로 `sourcing.update_ux_stats()` 호출(폼팩터 성과 반영·저성과 은퇴·발굴 정체 경보) + 주간 리포트에 북극성·UX 발굴 감사 섹션 |
| `autopilot/comments.py` | 댓글 분류·자동 답글 (의료=소아과 권유 고정, 분쟁=보류) → `state/holdbox.jsonl` |
| `reply-outreach.md` | 답글 파고들기 플레이북 (콜드스타트: 인플루언서 글에 링크 없는 답글 10~15/일) |
| `autopilot/state/playbook.md` | 자가개선 산출물 (생성 시 스타일 힌트로 주입) |
| `autopilot/state/weekly_report.md` | 주간 리포트 (운영자 주 10~15분 확인 지점) |

## 6. 공개 사이트 (heightcue.lifoli.co.kr — GitHub Pages, main 푸시 후 ~1분 반영)

| 파일 | 역할 |
|---|---|
| `autopilot/sitegen.py` / `sitegen_lt.py` | KR 제품 랜딩 자동 생성·git 배포 (링크트리 스타일). 배포 실패 시 직링크 폴백 |
| `kr/` `us/` | 라이브 페이지 (kr 허브+제품 p/, us 가이드). `us/vitamin-d-drops.html`이 US 판매 앵커 |
| `index.html`, `styles.css`, `CNAME`, `robots.txt`, `sitemap.xml`, `disclosure.html`, `privacy.html` | 사이트 공통 |
| `content/registry.json` | 콘텐츠 중복 방지 레지스트리 |

## 7. 검증·테스트 (코드 수정 후 필수)

```bash
cd ~/heightcue-autopilot/autopilot
../.venv/bin/python validate.py            # 자격증명·모델 슬러그 실호출 검증 (실패 시 exit 1)
../.venv/bin/python test_ops.py            # 운영 안전장치 테스트
../.venv/bin/python test_queue.py          # 브라우저 큐 E2E
../.venv/bin/python post_check.py test_posts.json --test   # 포맷 회귀 29/29
```

## 8. 레거시 — 기준으로 삼지 말 것

| 파일/폴더 | 상태 |
|---|---|
| `ops/RUNBOOK.md`, `ops/affiliate-readiness.json`, `ops/performance-log.csv` | 유튜브 쇼츠 시절(2026-08-20 전후) 운영 규칙. 현행 아님 |
| `articles/`, `assets/shorts/` | 쇼츠·교육 아티클 시절 자산. 동결 |
| `en/`, `cn/` | 초기 실험 랜딩. 현행 트랙은 KR/US만 |
| v1 기획서 (Gemini 파이프라인 3개짜리 짧은 문서) | `heightcue-SSOT-v2.md`로 대체됨 |

## 9. 불변 규칙 (요약 — 원문은 SSOT §0·부록 A)

1. `@heightcue` 핸들 변경 금지 (쿠팡 파트너스 등록 채널과 일치해야 함).
2. 제휴 고지 2행 유지 (KR 쿠팡 문구 / US Associates 문구).
3. 가짜 체험담·효능 암시 금지 (리뷰는 실제 원문 복사만). TruHeight FTC $4M 벌금 사례.
4. 카테고리 하드락: 영양/숙면/자세/운동만. 측정도구·잡화 소싱 금지.
5. 구매·결제·파트너스 설정 변경·계정정보 파일 기록 금지.
