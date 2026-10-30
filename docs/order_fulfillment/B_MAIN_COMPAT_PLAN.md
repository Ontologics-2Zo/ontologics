# B/main 호환 점검·노트북 간소화 계획 (2026-10-29)

기준: feature/b-first-mvp, HEAD f264955, 통합 main ca7c391. 시작 시 clean, origin B보다 6 ahead. Git 무충돌 병합은 기능 호환 통과와 다르다. 이번 실제 유료 API 요청은 0, push/PR/commit 없음.

## 보존 기준

- 기존 세 노트북은 저장 출력·편집이 있으므로 바이트 단위 보존. 새 `CQ01_06_실제실행_간소화.ipynb`를 만든다. notebooks/와 artifacts/local/, JunhwaDocs/는 기존 ignore 유지, 강제 추가하지 않는다.
- 원본 입력·과거 실행·DB·settings/controller는 읽기 증거만 확인하며 실행/재개하지 않는다. 실행 DB는 매번 새 cluster/user/socket/HOME/TMPDIR와 새 schema.
- 변경 전 SHA 목록: `artifacts/local/order_fulfillment/main_compat_20261029/preservation_before.json` (기존 notebook/개인 문서와 지정 Task10 근거, PG 파일 제외). 완료 때 재대조한다.
- Task10 초기 CQ01 실패, 다음 CQ02 실패, 그때 CQ01 2대표 성공, 별도 후속 CQ02~06 8대표 성공을 구분한다. Task11·이후 무료 모의 검증은 새 실제 성공이 아니다.
- 이후 CQ04 실제4요청·의미검토·종료 성공과 전체 노트북 CQ01 intake 실제4요청·자동통과 후 의미검토 미제출/중단을 별개로 기록한다.

## 변경 순서·회귀 범위

1. 기존 notebook/runner/transport 무통신 경계 61검사 통과 후 수정한다. 공통 README·AGENT_RUNTIME·SERVICE_SPEC·TOOLS를 기준으로 차이를 조사한다.
2. B host/runtime의 공통 budget·신원 재검사·sandbox 전달 누락을 고친다. 공용 envelope/도구 권한·도메인·비용 정책은 변경하지 않는다. 기존 B 도구를 공통 AgentSpec에 등록할 수 없는 경계는 별도 검토로 남기고 유료 경로는 키 읽기 전에 차단한다. 숨은 alias/allowlist 확대/별도 예산 우회는 금지한다.
3. 표시/조작만 새 notebook UI에 분리: 사례→무료검사→이번 실행 확인→자동 상태 갱신→답변/근거 바로 아래 수동 의미검토→별도 정확 초안 승인→종료. 동작은 기존 Demo/CLI 사용. 승인문 자동 입력·검토 자동작성·자동통과 없음. 상세 JSON은 접기. 기존 결과 재생과 새 free/live 구분.
4. 새 기능 회귀: 무승인/오래된 승인·무료게이트 변경, 중복 시작, review report/hash·draft/version/hash 결합, 작성자 분리, 실패 중단, 결과 보존, 종료. UI 기본 셀은 무호출/무DB/무프로세스 검증.
5. 새 격리 PG에서 전체 unittest, CQ/도구/demo, 새 10대표 무료 HTTP 모의 실행을 수행하고 종료 증거·원본 SHA를 재검사한다. 설치된 lint/typecheck만 사용, 새 의존성 없음.

## 발견된 호환/우회 경계

- B Host의 오래된 override가 공통 budget/cost/sandbox/identity_lookup을 누락한다: 로컬 연결 보완 대상.
- B provider __call__은 공통 begin_paid_call을 사용하지 않는다: 실제 전송은 공통 비용 경로 통합 전 허용하지 않는다. 무료 HTTP 모의 실행과 실제 provider 정책 검증은 구분한다.
- 공통 AgentSpec은 공통 AgentGateway 도구만 허용한다. lookup_many/delivery_round/fulfillment_status/production_completion/delivery_forecast/response_plan/compare_response_plan은 현재 계약 밖. 접수는 공통 도구만 사용하고 나머지 6개 등록이 이 도메인 경계에 걸린다. B subclass로 검증 우회하지 않는다. 이 계약 검토가 해소될 때까지 full live readiness는 실패한다.
- 기존 승인문 '입력·금액 별도상한 없음'은 최신 main 공통 비용 정책과 맞지 않는다. 새 UI에서 사용하지 않으며 정책 충족을 실제 실행의 전제 조건으로 표시한다.
