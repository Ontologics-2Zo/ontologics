# Task09 후속: 생산계획 검토 에이전트와 호스트 업무 연결

2026-10-28. 기존 파일은 수정하지 않고 추가한 실행 경로다. 등록 역할은 `materials_review`이며, 계약의 명시적 주장 해석과 검증된 계획 설명을 맡는다. 생산·구매 의사결정자나 데이터 정정 담당자는 아니다.

## 실행

Python cwd는 `ontologics/`. 기존 의존성과 새 전용 PostgreSQL/pgvector가 필요하다. TEST_DATABASE_URL과 DATABASE_URL은 동일한 폐기형 DB를 프로세스 환경에 지정한다. 기존 DB에서 실행하지 않는다.

```sh
python -m unittest tests.materials.test_agent_workflow -v
python -m examples.materials_agent_workflow
python -m examples.materials_agent_workflow --live --env-file .env.example --budget-ledger /tmp/task09-followup-20261028/live-budget.json
```

기본 실행은 scripted_fixture다. `--live`는 기존 OpenAIProvider와 실제 Runtime를 사용한다. 승인 세션 내 모든 실행은 동일한 장부를 사용한다. 장부를 지우거나 경로를 바꿔 예산을 초기화하면 안 된다. 과거 승인을 새 세션에 승계하지 않는다. `--scenario`는 standard/source_conflict/stored_conflict/missing_quantity/injection/resume/suite 중 하나다. suite는 실제 모델에서 여섯 사례, 기본 대역에서는 standard만 실행한다.

## 업무 흐름과 API

1. `interpret_source(host, identity, path, data_version=..., provider=..., config=...)`: 호스트가 선택한 원문을 기존 추출기·해석 메시지·파서·접수 검사기로 검증한다. 모델 단계는 max_steps=1이다. 원문 주장과 같은 ID의 공개 ContractItem 수량·구성·단위를 대조한다. 원문 hash와 공개 버전을 다시 검사한다. 여기서는 수요 저장/사람 확인을 하지 않는다.
2. `inspect_report(report, public_items)`: 충돌·필수값 누락을 data_checks로 반환하는 순수 비교 함수다. 외부 임의 dict의 진위나 권한을 인증하는 함수가 아니다. 업무 진입점은 호스트가 가져온 값에만 적용한다.
3. 기존 DemandConfirmationWorkflow/PersistentDemandConfirmationWorkflow가 검증 대상 수요를 준비·저장한다. 실제 사람의 확인은 기존 권한 있는 경로에서만 한다.
4. `run_review(host, demands, store, identity, calculation_arguments=..., request_key=..., provider_factory=..., config=..., resume_scopes=None, cancelled=False)`: 최신 수요/공개 버전/확인 범위를 검사하고 기존 BusinessDocumentWorkflow.create_documents를 호스트가 직접 호출한다. 이어 정확 문서 버전/hash/current 상태를 조회하고 compact_task로 투영한다.
5. 새 모델 run이 lookup/configuration 중 기존 허용 조회 도구를 선택한다. 성공 trace의 대상·Evidence와 응답을 기존 evaluate_planning_result가 대조한다. 모델 응답 뒤에도 수요/문서/확인을 재검사한다.

하나의 역할이 두 단계에서 각각 새 run/provider를 사용한다. 해석 단계와 검토 단계의 출력 형식·도구 규칙은 서로 적용되지 않는다. 계산/문서 저장은 모델이 선택한 도구가 아니며 host_workflow로 표시한다. 모델 임의 명령을 실행하는 별도 실행기는 없다. 기존 agent.py의 `_snapshot` 등 내부 함수를 재사용하므로 그 계약이 바뀌면 후속 모듈 회귀가 필요하다.

## 충돌과 사람 업무

원문100/저장120이면 data_owner에게 원문·항목·개정 이력·저장 데이터를 점검하도록 요청하고 관련 계산/문서 생성을 중단한다. 사용자가100 또는120을 고르는 질문으로 바꾸지 않는다. 기존 정정 절차로 반영한 뒤 최신 상태로 새 실행을 한다. 모델이 원문의 모든 충돌을 빠짐없이 추출한다는 보장은 없다. 추출된 주장과 근거에 대해서는 엄격히 검증한다.

가격/납기/기술 검토/전체 재고 미확인은 조건부 결과에 남길 수 있다. 생산계획 담당자는 수요 확인, 기술 검토자는 실장·대체 적합성, 계약 담당자는 변경 허용·고객 도착 조건, 재고 담당자는 실제 가용 재고, 구매 담당자는 견적·MOQ·주문배수·납기를 확인한다. 특정 실명 담당자는 자동 지정하지 않는다.

결과는 요청자에게 반환하는 JSON과 내보낸 문서 파일이다. 외부 회사/기관에 제출하거나 메시지를 발송하지 않는다. review_ready는 검토 자료 준비 상태이며 승인·발주·생산 상태가 아니다. resume 예제의 확인은 별도 시험 DB의 TEST ONLY fixture다.

## 비용과 보안 경계

BudgetedOpener는 기존 provider의 opener 접점에만 결합한다. 고정 모델 gpt-5.4-mini-2026-03-17, 표준 input $0.75/M·output $4.50/M을 사용하며 할인은 계산하지 않는다. 호출 전 최대 context400000 및 출력 제한으로 비용을 예약하고 fsync한다. 응답 usage가 검증되면 정산하고, 실패/usage 누락이면 최대 예약을 유지한다. 재시도도 매 호출 차감한다. 장부는 파일 잠금·프로세스 내 잠금으로 보호하고24회/$2보다 높은 한도를 거절한다. 실제 모델 예제 함수를 직접 호출할 때도 BudgetedOpener가 없으면 거절한다.

단가는 [공식 모델 문서](https://developers.openai.com/api/docs/models/gpt-5.4-mini)의 확인 시점 기준이다. 사용량 기반 계산이며 청구서 실측은 아니다. 이후 가격 변경 시 재확인이 필요하다. 장부에는 키/헤더/원문/응답을 저장하지 않는다. 실제 모델 검증은 공개 제품 정보와 synthetic 운영 자료만 사용한다.

## 미연결 범위

기존 Gateway에 계산/저장 함수가 등록되어 있지 않아 모델의 자율 계산·저장 도구 선택은 미연결이다. 웹 Supervisor 인계와 화면도 이번 구현 범위가 아니다. 현재 실행 가능한 것은 원문 해석 → 사실 점검 → 고정 호스트 계산·저장 → 모델 조회·검토 결과 반환이다. 원래 Task09 전체 자율 도구 목표와 구분한다.
