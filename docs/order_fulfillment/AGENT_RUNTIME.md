# Order Fulfillment — B Agent 실행 지침

공통 문서 [docs/AGENT_RUNTIME.md](../AGENT_RUNTIME.md)의 메인 기준(`7c5e2d9`)과 비교해 B 개발 중 추가·변경된 내용을 분리했다. 공통 문서는 해당 메인 원본으로 복원했다.

아래 테스트 수·실제 모델 실행·수락 여부는 원문에 기록된 당시 결과다. 현재 폴더 분리 이후의 전체 동작을 새로 검증했다는 뜻은 아니다. 실행 예제와 B 파일 경로는 현재 하위 폴더 위치로 표시한다. 이 문서의 과거 기록 이후 공통 Provider·Service·도구 정의를 B 하위 폴더로 분리했다. 현재 검증 상태와 남은 모델 연결 문제는 [B 작업 기록](PROJECT_STATUS.md)을 따른다.

## 추가·변경 1

> 원문 위치: 문서 시작 (1–3행)

### 서비스 Agent 실행 지침 v0.4.0

B-CQ02의 공통 `fulfillment_status(delivery_round_id, as_of, version)`를 `examples/order_fulfillment/b_fulfillment_agent.py`의 읽기 전용 현황 역할에 연결한다. 명시한 데이터 버전·정확한 현황 시점을 사용하며 확인 범위가 부족하면 합계를 미확인으로 남긴다. 기존 Product·Quality·B 접수/약속 역할의 모델 도구 목록과 승인·반영 경계는 유지한다. 실제/대역 실행과 별도 자유문 검토의 완료 근거는 PROJECT_STATUS 기록을 따른다.

## 추가·변경 2

> 원문 위치: 서비스 Agent 실행 지침 v0.4.0 / 모든 역할의 공통 규칙 (17–17행)

- B-CQ01의 공통 `delivery_round` 도구는 호스트가 노출한 경우에만 명시한 데이터 버전으로 납품 약속을 읽는다. `fulfillment_assessed=false`와 경고를 유지하며 약속 수량을 현재 재고·납품 가능 수량으로 설명하지 않는다. 기존 Product/Quality 예제의 도구 목록은 유지하며 B 연결은 `examples/order_fulfillment/b_agent.py`에 별도로 둔다.

## 추가·변경 3

> 원문 위치: 서비스 Agent 실행 지침 v0.4.0 / 책임별 시작 지침 (28–33행)

| 역할 | 목표·권장 도구 순서 | 종료·인계 기준 |
| --- | --- | --- |
| B (B-CQ01) | intake: 원문과 source/evidence, 기존 모델·개정·구성 lookup → 계약 후보 prepare_draft/validate_draft. promise: 정확한 버전 delivery_round → Evidence/SourceDocument lookup | intake는 사람 검토 대기 또는 초안 없이 확인 필요/입력 오류. promise는 약속만 설명하고 재고·배정·생산·운송·실제 도착은 미확인 |
| B 현황 (B-CQ02) | 입력 회차·현황 시각·버전으로 fulfillment_status → snapshot 근거 하나 lookup → 그 SourceDocument lookup → 간결한 JSON과 한국어 설명 | 6상태·잔여 약속·미배정·근거·미확인을 설명. 예정/실제·잔여/추가 생산·현황/예측 구별. 초안·승인·반영·발송 금지 |

현황 역할은 `fulfillment-agent`의 read 권한과 B_FULFILLMENT_TOOLS 두 도구만 사용한다. 최대4단계·단계당 모델 시도1회·도구1개, 실제 provider의 출력1024토큰·reasoning none을 코드로 적용한다. 타임아웃이나 실패 후 자동 유료 재실행을 하지 않는다. snapshot이 없는 미확인은 근거 조회 단계를 생략하고 null과 필요한 확인을 설명한다. 개발용 호스트가 모델 시작 전에 합성 자료를 검토·반영하며 모델이 승인하는 과정이 아니다. 전체 근거 사슬은 호스트가 같은 버전에서 로컬로 검증한다. 모델은 각 항목을 뒷받침하는 근거와 확인 범위 근거를 답에 연결한다.

평가기의 수량/권한/근거 검사는 자유문 의미 검토를 대신하지 않는다. `narrative_review`가 미검토이면 모델의 자연어 표현을 확인했다고 주장하지 않는다. 한 대표 사례 성공으로 다양한 입력·반복 안정성·운영 준비 완료를 선언하지 않는다.

## 추가·변경 4

> 원문 위치: 서비스 Agent 실행 지침 v0.4.0 / 책임별 시작 지침 (44–45행)

B의 새 호스트 연결 예제는 `examples/order_fulfillment/b_host_agent.py`다. 기존 지침·도구 목록을 b_intake/b_promise/b_fulfillment로 등록하고 CommonHost가 고정한 사용자로 실행한다. 기존 파서·독립 정답·평가기·CQ02 ProvenanceProvider를 재사용하며 고정 Agent 신원으로 돌아가지 않는다. b_intake만 사용자 draft 권한 안에서 미승인 초안을 만들고 나머지 두 역할은 읽기 전용이다. 사용자에게 검토 권한이 있어도 Agent의 도구 목록에는 승인·반영이 없다. 대역 CLI 성공은 웹 인계·실제 인증·병합 후 실모델 품질 검증을 뜻하지 않는다.
