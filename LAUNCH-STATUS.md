# heightcue LAUNCH STATUS — 단일 현황판

> 헷갈릴 때는 이 문서만 본다. 갱신: 2026-08-31 (Hermes Agent) — persona-free friction commerce 전환 반영
> 전략·가드레일 = `heightcue-SSOT-v2.md` · 실행 방법 = `README-autopilot.md` · 운영 폴더 = `~/heightcue-autopilot`

## 🔒 확정 스코프 (운영자 확정)

**하는 것 — 이 둘만:**

| 트랙 | 채널 | 수익 | 기본 운영량 |
|---|---|---|---|
| KR | Threads @heightcue | 쿠팡 파트너스 | 원문 2/일 + 실제 외부 대화 답글 10~15/일 |
| US | Threads @heightcue_us | Amazon Associates (검증 가이드 경유) | 원문 2/일 + 실제 외부 대화 답글 10~15/일 |

**사업 정의:** 부모가 반복해서 겪는 잠·아침, 식사·도시락, 놀이·움직임, 공부·루틴, 정리·공간 마찰을 찾고, 구조 차이를 근거로 비교해 구매 판단까지 연결한다. 고정 인물·가족 경험·의료 권위를 연기하지 않는다.

**안 하는 것 (동결):** 신장계 상업 추천 · 키 성장 효능 암시 · 유튜브 쇼츠 신규 제작 · 인스타그램 · 핀터레스트 · CN 확장.

## 현재 상태

| 구성 요소 | 상태 | 비고 |
|---|---|---|
| KR/US Threads 계정·장기 토큰 | ✅ | KR `HeightCue \| 생활 마찰 해결`, US `HeightCue \| Parenting Fixes`; 핸들은 `@heightcue`, `@heightcue_us` 유지 |
| OpenRouter | ✅ | 콘텐츠·검토·외부 답글 생성 모델 `google/gemini-3.7-flash`; `validate.py`가 실제 공급자 응답을 검증 |
| 아마존 어소시에이트 | ✅ active | 사이트 등록 앵커와 승인 가이드 1개. 정적 가격·재고·리뷰 수는 표시하지 않음. ⚠️ 정산 수단 미설정 |
| 쿠팡 파트너스 | ✅ active | 등록 채널 `@heightcue`; 기존 KR 제품 페이지 2개는 상업 CTA 없는 퇴역 안내로 전환 |
| 오토파일럿 코드 | ✅ | persona-free stage 계약, 부모 비난·퇴역 페르소나 출력 게이트, 수익 우선순위, metric-specific 실험 판정 |
| 상품 실행 원장 | ✅ | US는 LiFoli Company OS/Supabase 승인·오퍼·랜딩 패킷이 SSOT. `state/us_products.json`은 런타임 원천이 아님 |
| 공개 사이트 | ✅ 로컬 검증 | friction category 허브·빈 상태·측정 교육 아카이브·승인 상품 상세·고지·개인정보·sitemap 생성. 내부 링크 검사 0건 |
| 스토리 뱅크 | 📦 archive | 과거 167cm/5'6"/26세·가족 경험은 활성 생성 입력에서 제외. 역사 원장은 수정하지 않음 |
| 발행 게이트 | 🟢 가동 | `dry_run=false`, `publish=true`; discovery/bridge는 링크·상품·상업 연결 금지 |
| crontab 원본 | ✅ 갱신 | 09:30 daily 1회 + 10:30/15:00/20:30 외부 답글 슬롯 + 3분 comments + weekly/harvest/health. OS 등록은 첫 외부 답글 검증 뒤 수행 |
| 기본 발행 물량 | ✅ 시장별 원문 2건/일 | `daily` 한 번으로 KR 2건·US 2건. 추가 원문 슬롯은 제거 |
| 외부 답글 | 🟡 첫 실검증 전 | Aside `u0` 전용 발견→Gemini 근거 결속→사전 예약→발행→별도 read-back 구현. 첫 검증 후 health가 KR/US 최근 성공을 감시 |

## 남은 일 — 소유자별

**계정 주인만 가능한 일:**
1. 아마존 정산 수단 선택(해외 수취 계좌/수표/기프트카드)
2. 쿠팡 대시보드에서 API 키 발급 가능 여부 확인 — 발급 전까지 브라우저 큐를 유지

**자동화가 수행하는 일:**
1. `outreach_worker.py`가 실제 Threads 대화를 Aside로 찾고, 상업 연결 없는 구체 답글을 발행·read-back
2. `build_journey.py`가 Company OS 승인 패킷으로 사이트를 재생성하고, 죽은 내부 링크와 정책 위반 가격 표시를 차단
3. `health.py`가 발행·댓글·외부 답글·상품 워크플로·활성 계약 드리프트를 함께 감시

## 안전장치 요약

* 실발행은 `publish: true`를 명시해야만 가능(`golive` 명령이 켬). 끄면(또는 crontab 제거) 전체 정지.
* 무인 강건성: 한 단계가 실패해도 나머지는 계속 실행되고 `state/errors.jsonl`에 기록한다. Threads 토큰은 weekly가 자동 갱신한다.
* persona-free: 과거 개인·가족·신체 서사는 활성 입력에서 제외하고, 부모가 아닌 구체적인 허위 주장·불편한 구조·낭비되는 구매를 비판한다.
* 판매글: KR 정확 승인 문구, US `#ad`와 Associates 문구를 강제한다. 검증되지 않은 가격·재고·리뷰 수는 사이트에 표시하지 않는다.
* discovery/bridge: 브랜드·상품·제휴 링크·프로필 유도 금지. verdict만 승인 패킷과 랜딩 경로를 사용할 수 있다.
* 외부 답글: 실제 원문 URL 사전 예약, 링크·브랜드·상품·의료 답변 금지, 별도 provider read-back이 일치해야 `verified`다.
* 수익 판단: 수수료→주문→클릭→진행→유효 반응→조회. `commission_per_1000_verified_impressions`는 관측·최소 표본 충족 때만 보조 지표로 계산한다.

## 영상(I2V UGC) 파이프라인 — 상태와 **검증되지 않는 것들**

**상태: 유료 호출 0건.** `run.py video enqueue|process|status|rehearsal` 배선 완료
(Task 16). `video.production_generation_enabled` 기본 `false` 이며, 이 플래그가 꺼져
있는 한 `process` 는 잡을 claim 조차 하지 않는다. 무료 점검은 `run.py video rehearsal`.

**배포 전제조건(미충족 시 실행 금지): 전사 백엔드(transcriber 파일이 아니라).** QA
게이트는 fail-closed 라 전사가 돌지 못하면 **모든 실영상이 QA 실패한다** — 영상은 전량
탈락하고 비용만 나간다. `rehearsal` 이 이 사실을 먼저 보고하고 미충족이면 exit 1 로 끝낸다.

**파일 존재는 실행 가능을 뜻하지 않는다.** `tools/analysis/transcriber.py` 는 그 자체로
아무것도 하지 못한다 — 내부에서 `faster_whisper`(대안: `whisperx`)를 import 한다. 파일만
확인하는 프로브는 백엔드가 하나도 없어도 `[충족]` 을 찍어 **유료 실행을 승인해버린다**
(2026-08-28 라운드 1 의 실제 결함 — 그 상태로 실행했다면 생성한 영상이 전량 QA 탈락했다).
현재 프로브는 `video_qa._openmontage_call` 과 **같은 인터프리터·같은 cwd·같은 sys.path**
로 백엔드를 실제 import 해 보고, 하나도 되지 않으면 `[미충족]` 으로 exit 1 한다. 프로브
실패·타임아웃·해석 불가 출력도 전부 미충족이다(확인 못 한 것은 충족으로 세지 않는다).

**2026-08-28 현재 이 머신 상태: 충족.** OpenMontage venv 에 `faster-whisper 1.2.1`
(+`ctranslate2 4.8.1`)이 설치돼 있고 `run.py video rehearsal` 이 `[충족]` · EXIT=0 이다.
설치가 필요할 때 운영자가 실행할 명령:

```bash
cd /Users/leeheungkyu/OpenMontage && .venv/bin/python -m pip install faster-whisper
# 반드시 OpenMontage 자기 venv 다 — QA 전사는 이 인터프리터로만 돈다.
```

**남은 한계(초록도 최종 판정은 아니다).** 프로브는 백엔드 import 까지만 확인한다 — 모델
가중치 내려받기와 실제 전사 품질은 검증하지 않는다. `[충족]` 은 "전사가 돌 수 있다"는
뜻이지 "QA 가 통과한다"는 뜻이 아니다.

**`process` 종단 오케스트레이터 배선 완료.** 대기 잡을 lease로 claim한 뒤 스토리보드 →
실물 Ken-Burns 사진/생성 첫 프레임 → MiniMax H3 Max 모션 컷 → 클린 마스터 + 자막본 +
SRT → 두 산출물 QA → `video_handoff.promote_to_ready`까지 실행한다. 한 번에
`video.max_jobs_per_run`까지만 처리하고, QA 실패는 `qa_failed`, 재시도 가능 실패는 원장
정책에 따라 `queued`/`dead_letter`로 남긴다. **이 프로세스는 발행하지 않으며** 성공
종착점은 `ready_to_publish`다.

### 이 시스템이 **검증하지 않는** 것 (운영자는 이걸 알고 켜야 한다)

아래는 이번 빌드를 통과해 그대로 남은 알려진 공백이다. 시스템이 실제보다 더 많이
검증한다고 믿으면 안 된다.

* **제품 충실도는 프롬프트 텍스트로만 강제된다.** 지각적 해시(perceptual hash)도,
  임베딩 유사도 비교도, 사람 게이트도 **없다.** 모델이 제품을 다른 형태·색·브랜드로
  재해석해 그려도 **아무것도 잡아내지 못한다.** 남는 것은 원본 자산의 sha256 으로
  "무엇을 입력했는가"를 사후 귀속할 수 있다는 것뿐 — 출력이 그 제품인지는 보증하지 않는다.
* **제휴 고지가 실제 렌더된 영상에 보이는지는 픽셀로 확인하지 않는다.** 현재는
  **렌더러가 그렇게 보고했다(renderer-reported)** 는 진술을 신뢰한다. 렌더러가 오버레이를
  누락하거나 화면 밖으로 밀어내도 파이프라인은 통과시킨다. 법적으로 가장 비싼 실패
  지점이 가장 약하게 검증되고 있다.
* **Threads 실존 확인기(existence-checker)가 없다.** 발행 도중 죽은 잡이 이미
  올라갔는지 확인할 방법이 없으므로, 해당 잡은 **사람이 개입할 때까지 차단**된다.
  조용한 재발행(중복 게시)보다 막는 쪽을 택한 결과이며, 의도된 동작이다.
* **MP4 길이·해상도는 헤더 선언값이다.** 페이로드 검사로 교차 확인은 하지만
  **완전한 디코딩은 하지 않는다.** 헤더와 실제 프레임이 어긋나는 파일은 통과할 수 있다.



## 역사 기록 — 아래는 현행 운영 지침이 아님

### 2026-08-27 — v2.1 바이럴 포맷 이식 + 링크 모드 A/B + KR 제품 랜딩 페이지
- **스킬 v2.1** (`heightcue-gemini-skills.md`): K-뷰티 벤치마크(@skin.pick.seoul 175개 전수 분해) 공식 이식.
  4대 포맷(F1 가격역전/F2 언더독/F3 신문물/F4 가치글 쇼핑가이드), fair point + 비추천(skip if) 의무 슬롯,
  슬롯-근거 게이트(권위·배경 주장은 소싱 데이터에 있을 때만, 없으면 리뷰 수·평점 등 수치로 대체).
- **링크 모드 A/B** (`run.py _sales_arm`): KR 판매글 direct(쿠팡 직링크+2행 고지) vs site(자사 제품페이지 경유, 글 내 고지 없음) 교대.
  US `#ad on/off` 실험은 컴플라이언스 감사로 폐기했다. 모든 US 판매글은 자사 가이드 경유 여부와 무관하게 첫 줄 `#ad`를 강제하며, 고지 유무는 성과 실험 대상이 아니다.
- **KR 제품 랜딩 페이지** (`autopilot/sitegen.py`): 소싱 실데이터로 `kr/p/<product_key>.html` 자동 생성→git push→라이브 검증.
  글의 제품이 페이지에 정확히 표시(제품명·가격+확인시점·리뷰 원문 verbatim·추천/비추천·쿠팡 CTA). 배포 실패 시 direct 모드 자동 폴백.
  첫 실배포: `kr/p/kr-sleepcomfort-junior-milkpillow-plus-55x34x8.html` (라이브 200 확인).
- **검사기** (`post_check.py`): 고지 메모를 '글 안에 제휴 링크가 있을 때'로 한정. 신규 리스크 패턴(무근거 권위 납품/처방, 효능 동치, 창작 긴급성, KR/US 각각). 비추천 슬롯 없으면 포맷 팁. 체험담 오탐 수정(리뷰 귀속 "정착했다는" 제외).
- **생성기** (`generate.py`): LLM 빈 응답(content=None) 방어 재시도 추가. link_mode/ad_mode를 A3 페이로드로 전달.
- 회귀: post_check 37/37 (케이스 8개 추가), test_ops/test_queue PASS, dryrun 정상. 발행은 하지 않음(샘플 글만 생성·검수).

## 2026-08-27 (2차) — KR 랜딩·허브를 링크트리 클론으로 전면 교체
- 운영자 피드백: 에디토리얼형 랜딩 2종 모두 "조잡하다" 리젝 → **Linktree UX 그대로 클론**으로 확정.
- `kr/lt.css` + `autopilot/sitegen_lt.py`(렌더러) 신설, `sitegen.py`는 배포·검증·폴백만 담당. 구 에디토리얼 렌더러는 `sitegen_editorial_backup.py.bak`.
- 구조: 단색 딥틸 배경 · Threads 프로필 사진 아바타(`assets/brand/heightcue-pfp.jpg`) · 이름/바이오 · 고지 1줄 · 흰 필 버튼 스택(제품+가격 → 30초 판단 요약(접이식: 정착 이유/솔직한 한 줄/비추천/리뷰 원문) → 다른 추천템 → Threads 팔로우).
- `kr/index.html`(Threads 바이오 링크 목적지)도 동일 스타일 허브로 교체 — `kr/p/catalog.json` 업서트 시 자동 재생성.
- 여정 스크린샷 모니터링: 바탕화면 `heightcue_screenshots/{ts}/`에 조회→글→랜딩→쿠팡 클릭 단위 저장 (11-19-04 구버전 / 11-23-01 에디토리얼 / 11-42-33 링크트리판).
- 회귀 37/37 + test_ops/test_queue PASS + dryrun 무오류. CTA→쿠팡 정확 상품 연결 실클릭 검증.
