# HeightCue 전사 복구 및 수익 자동화

## Goal
진단에서 확인한 P0–P2 결함을 실제 코드·운영 데이터·자동화까지 수정하고, 검증된 수요→상품→근거→발행→클릭·주문·수수료 측정의 최소 경제 루프를 실운영 가능한 상태로 만든다.

## Scope
1. Threads 비밀값 로그 유출 차단·기존 로그 정화·권한 강화
2. placeholder/미검증 출처 fail-closed 차단과 기존 원장 격리
3. Kanban acceptance-aware 완료 상태
4. KR direct 귀속 실험의 실제 media_id/permalink/subId 경로
5. KR·US 리테일러 성과 read-back 및 주기 측정
6. friction·상품 후보·외부 유통 입력 복구
7. lint·tests·health·실운영 검증과 선택적 커밋

## Constraints
- 공유 작업 트리: stash/reset/add -A 금지. 수정 경로만 선택적으로 stage/commit.
- 계정 토큰 교체와 기존 게시물 삭제는 외부 계정 상태 변경이므로 구현과 별도 승인 지점으로 분리.
- 선언값·파일 존재·에이전트 자기보고를 성공 증거로 인정하지 않음.
- 새 동작은 RED→GREEN TDD. 실발행은 media_id/permalink/Graph read-back, 매출은 리테일러 read-back이 있어야 완료.
- 브라우저 작업은 Aside CLI만 사용.

## Phases
- [x] Phase 1: 진단·범위·변경 경계 확정
- [x] Phase 2: 보안·근거 무결성 P0 복구
- [x] Phase 3: Kanban·귀속 실험 복구
- [>] Phase 4: 수익 측정·friction·유통 자동화 복구
- [ ] Phase 5: 전체 검증·선택적 커밋·운영 보고

## Current Phase
Phase 4 — 수익 측정·friction·유통 자동화 복구

## Next Step
Coupang direct arm은 최종 승인/API key capability로 blocked 상태를 유지한다. 외부 승인과 분리해 friction·상품 후보·외부 유통 입력의 실제 소비 가능성을 검증한다.

## Decisions
- 기존 미커밋 변경은 소유권을 추정하지 않고 보존한다.
- 수익 우선순위는 commission/orders > affiliate clicks > progression > qualified engagement > views.
- 단순 발행량 증가는 목표가 아니다. 첫 귀속 가능한 KR direct 표본을 end-to-end로 완주한다.

## Errors Encountered
| Error | Status |
|---|---|
| health.py overall fail: KR verified external reply 없음 | Phase 4에서 실증 복구 |
| changed-file Ruff E702 in test_health.py | Phase 5에서 수정·재검증 |
| active placeholder evidence 실제 발행 | Phase 2에서 격리·재발 방지 |
| Threads token이 cron.log에 노출 | Phase 2에서 redaction·정화, 회전은 승인 지점 |
| publish acceptance 미충족 task가 done | Phase 3에서 상태 계약 교정 |
