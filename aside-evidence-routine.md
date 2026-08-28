# 증거 수집 루틴 (Evidence Harvesting) — Aside 워커 프롬프트 원본

> 이 문서는 Aside 루틴 `HeightCue 증거 수집 워커`의 프롬프트 원본이다.
> 수정 시 Aside 루틴·`AGENTS.md`와 3중 동기화할 것.
> 대응 코드: `autopilot/evidence.py` (claim_gate·승격·공급)

## 0. 역할

가치글 레이어(SSOT §3 레이어 1)에 **검증된 사실**을 공급한다.
소싱 워커가 판매글에 상품을 공급하는 것과 정확히 대칭인 역할이다.

**이 워커가 없으면 가치글은 LLM이 기억에서 지어낸 사실로 작성된다.**
2026-08-28 이전까지 실제로 그렇게 운영되었다(하드코딩 topic 문자열 1개).

## 1. 실행

```bash
aside --account u0 exec "<아래 수집 목표>"
```

산출물은 `autopilot/state/evidence.jsonl`에 append. 다음 `run.py daily`가
`claim_gate`를 태워 통과분만 `insight_atoms.json`으로 승격한다(무인).

## 2. 수집 대상 — 우선순위 순

| 순위 | 소스 | type | 비고 |
|---|---|---|---|
| 1 | PubMed / KoreaMed / Google Scholar | `paper` | DOI 또는 PubMed URL 필수 |
| 2 | 질병관리청 소아청소년 성장도표, 보건복지부, 교육부 | `gov` | 원문 URL 필수 |
| 3 | AAP, WHO, 대한소아청소년과학회 가이드라인 | `guideline` | |
| 4 | KOSIS·통계청 | `official_stat` | |
| 5 | 육아·교육 섹션 신규 보도 | `news` | **단독으로는 승격 불가** |
| 6 | 기존 육아 채널 바이럴 상위글 | `viral_post` | **구조만 수확** — 아래 §5 |

**1~4번(1차 출처) 중 최소 1개가 없으면 게이트에서 반려된다.**
뉴스·바이럴은 "무엇이 화제인가"를 알려줄 뿐 근거가 아니다.

## 3. 주제 축 — 거리(distance) 체계

가치글에는 제품이 없으므로 소싱 카테고리 하드락(영양/숙면/자세/운동)이
적용되지 않는다. 대신 거리로 통제한다. **D2·D3가 사람을 데려오고 D0가 수확한다.**

| 거리 | topic 값 | 목표 비중 |
|---|---|---|
| D0 | `nutrition` `sleep` `posture` `exercise` `checkup` | 40% |
| D1 | `growth_data` `eating_habits` `stress_growth` `screen_time` | 30% |
| D2 | `discipline` `self_regulation` `manners` `mindset` `emotional_dev` `sibling_peer` | 20% |
| D3 | `operator_story` `social_gaze` `body_image` | 10% |

`pick_atom()`이 채널별 실사용 분포를 보고 **부족한 거리를 우선 공급**하므로,
워커는 재고가 얇은 거리를 우선 수집하면 된다. 현재 재고 확인:

```bash
cd ~/heightcue-autopilot/autopilot && ../.venv/bin/python evidence.py status
```

## 4. 레코드 스키마 (evidence.jsonl 1행)

```json
{
  "evidence_id": "ev-20260828-01",
  "topic": "sleep",
  "claim": "성장호르몬 분비는 취침 시각보다 서파수면 총량과 연관이 크다고 보고된다",
  "counter_claim": "수면은 여러 변수 중 하나이며 성인 최종 키의 최대 결정 요인은 유전이다",
  "confidence": "strong | moderate | weak",
  "sources": [
    {"type": "paper", "doi": "10.xxxx/yyy", "url": "https://pubmed...", "year": 2024,
     "excerpt": "원문 인용(선택)"}
  ],
  "parent_emotion": "10시 취침 강박에서 오는 죄책감",
  "hook_seeds": ["10시 취침 강박, 근거가 생각보다 얇습니다"],
  "structure_only": false
}
```

### 필드별 주의

- **`claim`** — 200자 이내. 원문이 말한 범위를 넘지 말 것. 과장 요약이 최대 사고 원인이다.
- **`counter_claim`** — **필수.** 비어 있으면 자동 반려된다. "유전이 최대 변수"까지
  정직하게 말하는 것이 채널 신뢰의 원천이자 차별점이다(SSOT §3).
- **`confidence`** — 단일 관찰연구는 `weak`, 메타분석·가이드라인은 `strong`.
  모르면 낮게 잡는다.
- **`hook_seeds`** — 생성 시 변형 전제의 씨앗. 그대로 발행되지 않는다.
- **`parent_emotion`** — 부모의 감정 지점. 단, 공포·죄책감 **조성**이 아니라
  **해소** 방향이어야 한다(SSOT §3 규칙 ④).

## 5. 바이럴 수확 — 구조만

기존 육아 채널의 바이럴 글은 **훅의 형태·전개 리듬만** 가져온다.

- `structure_only: true`를 반드시 설정 (없으면 자동 반려)
- `hook_seeds`에 원문 문장을 그대로 넣으면 `viral_verbatim_copy`로 반려됨
- 남의 문장 복제는 표절이자 브랜드 자살이다

수확할 것: "훅이 몇 자에서 끊기는가", "어떤 감정을 건드리는가", "반전이 몇 번째 줄인가"
수확하지 말 것: 문장, 표현, 비유 그 자체

## 6. 자동 반려되는 것들 (claim_gate)

무인 운영이므로 게이트가 유일한 방어선이다. 애매하면 반려된다.

| 반려 사유 | 의미 |
|---|---|
| `primary_source_required` | 뉴스·블로그만 있고 논문·공공기관 출처가 없음 |
| `source_locator_missing` | 출처에 URL도 DOI도 없음 |
| `counter_claim_required` | 반론·한계 누락 |
| `causal_overreach` | "~하면 큰다", "○cm 더", "보장" 등 인과 단정 |
| `commercial_leak` | 링크·브랜드·구매 유도 혼입 (가치글은 비상업 레이어) |
| `viral_requires_structure_only` | 바이럴 출처인데 structure_only 미설정 |
| `viral_verbatim_copy` | 원문 문장 그대로 복사 |
| `topic_unknown` | 거리 체계에 없는 주제 |
| `duplicate_claim` | 이미 등록된 주장 |

반려 기록은 `state/evidence_rejects.jsonl`에 남는다. 반려율이 계속 높으면
수집 기준이 아니라 **워커 프롬프트**를 고쳐야 한다.

## 7. 멀티채널 확장 — 원자는 채널을 모른다

`insight_atoms.json`의 원자는 채널 중립이다. 하나의 원자가
Threads 480자 텍스트, TikTok 25초 대본, 카드뉴스 5장, YouTube 쇼츠로
각각 렌더링된다. **채널이 늘어도 이 워커는 바뀌지 않는다.**

소진은 `used_in`에 채널별로 분리 기록된다:

```json
"used_in": {"threads_kr": ["17845..."], "tiktok_kr": [], "threads_us": []}
```

Threads KR에서 쓴 원자도 TikTok KR·Threads US에는 여전히 신선하다.
따라서 채널 추가 시 **기존 원자 재고를 그대로 재활용**할 수 있다.

## 8. 금지사항

1. 원문을 읽지 않고 초록·헤드라인만으로 claim 작성 금지
2. 존재하지 않는 DOI·URL 생성 금지 (실재 확인 필수)
3. `insight_atoms.json` 직접 수정 금지 — 반드시 evidence.jsonl → 게이트 경유
4. 제품·브랜드 언급 금지 (가치글은 비상업 레이어)
5. 효능 단정·공포 조성 금지 (SSOT 부록 A)
