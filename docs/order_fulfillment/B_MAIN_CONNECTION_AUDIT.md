# B/main 연결 대조 — 단계 2 (2026-10-29)

## 범위와 기준

이번 단계는 **기존 구현을 읽고 연결 차이를 정리하는 작업**이다. B 업무 구현·공통 계약·노트북을 수정하지 않고, 테스트·서버·DB·모델 API를 실행하지 않는다. 다음 단계의 설계나 구현 완료를 뜻하지 않는다.

- 브랜치 `feature/b-first-mvp`, HEAD `f264955`, 통합 main `ca7c391`, 기존 B `4539c90`. 현재 원격 B보다 6커밋 앞이다. 앞선 작업의 미커밋 수정은 그대로 둔다. 처음 병합 직후 clean 상태와 이번 단계 시작 시 수정된 상태를 구분한다.
- 기준은 사용자가 제공한 Discord 스크린샷, [README의 실제 역할 연결](../../README.md#실제-agent-담당-폴더와-연결), [확장 연결](../../README.md#동료의-확장-agent-연결), [AGENT_RUNTIME](../AGENT_RUNTIME.md#확장-agent의-수정-경계)다. Discord 서버 원문을 직접 조회한 결과는 아니다.
- 비교 대상은 병합된 코드와 앞선 로컬 보완을 포함한 현재 작업 트리다. Git 무충돌 병합, 기존 무료 실행 통과, B의 새 typed 연결 완료는 각각 다른 주장이다.
- 루트 `PROJECT_STATUS.md`는 수정하지 않는다. B 기록은 이 폴더에만 남긴다. 기존 노트북·출력·실행 증거·ignore 설정도 수정하지 않는다.

## 실제 흐름과 목표 흐름

현재 전체 대표 실행은 다음 순서다.

```text
노트북 Demo → b_run CLI → 새 격리 환경 → run_campaign
  → B CommonHost의 요청 신원 고정
  → 기존 7개 AgentRegistration → request.run_agent
  → B runtime/provider → B 또는 공통 도구 → 공통 Service 기반 권한·저장
  → 기존 역할 evaluate → 별도 한국어 의미 검토
  → CQ01 접수만 정확 초안의 별도 업무 승인·공통 반영
  → 다음 대표 또는 실패 중단 → 서버 종료
```

근거: [노트북 연결부](../../examples/order_fulfillment/b_notebook.py), [대표 실행기](../../examples/order_fulfillment/b_run_workflow.py), [B 호스트](../../ontologics/order_fulfillment/host.py), [기존 등록](../../examples/order_fulfillment/b_host_agent.py).

팀 연결 규칙의 목표는 다음과 같다.

```text
기존 B 지침·흐름·입출력·업무 검증 재사용
  → 버전이 있는 AgentSpec / messages / 결과 검증기
  → AgentRegistration.from_spec + AgentTask(PinnedContext)
  → request.run_task
  → 공통 AgentResult / 확정 근거 / 요청 누적 한도
  → 계약·B 업무 테스트 → 명시적인 화면 연결
```

README는 기존 `run_agent`도 호환 경로로 지원한다. 따라서 현재 호출 방식 자체를 금지된 구현이라고 판정하지 않는다. 다만 B 전체가 위 새 연결 순서를 완료한 상태는 아니다. 기본 네 Agent의 폴더·역할을 대체할 필요도 없다.

## 10개 대표와 실제 담당 구현

대표 목록은 실행기의 `SPECS`, 역할·도구는 `REGISTRATIONS`와 [B 도구 정의](../../ontologics/order_fulfillment/tool_specs.py)를 대조했다. 모든 행은 현재 `run_agent` 경로이며, 아직 B 고유 `AgentSpec`/`run_task` 연결이 없다.

| 대표 | 역할 | 기존 업무 모듈 (`examples/order_fulfillment/`) | 허용 도구 | 공통 AgentSpec의 도구 조건 |
| --- | --- | --- | --- | --- |
| cq01_intake | b_intake | b_agent.py / b_cq01_bounded.py | lookup, prepare_draft, get_draft, validate_draft | 기존 공통 도구만 사용. 도구 조건 충족과 업무 전환 완료는 다름 |
| cq01_promise | b_promise | b_agent.py / b_cq01_bounded.py | lookup, delivery_round | delivery_round가 공통 목록 밖 |
| cq02_normal | b_fulfillment | b_fulfillment_agent.py | fulfillment_status, lookup | fulfillment_status가 목록 밖 |
| cq03_normal | b_production | b_production_agent.py | production_completion, lookup | production_completion이 목록 밖 |
| cq04_normal | b_delivery | b_delivery_agent.py | delivery_forecast, lookup_many | 두 도구가 목록 밖 |
| cq04_mixed | b_delivery | b_delivery_agent.py | delivery_forecast, lookup_many | 위와 같음. mixed 사례 |
| cq05_normal | b_response | b_response_agent.py | response_plan, lookup_many | 두 도구가 목록 밖 |
| cq05_mixed | b_response | b_response_agent.py | response_plan, lookup_many | 위와 같음. mixed 사례 |
| cq06_defects11 | b_comparison | b_comparison_agent.py | compare_response_plan, lookup_many | 두 도구가 목록 밖 |
| cq06_transport2h | b_comparison | b_comparison_agent.py | compare_response_plan, lookup_many | 위와 같음. transport2h 사례 |

전체 실행은 CQ01의 기존 독립 예제 단계를 그대로 호출하지 않고 bounded provider와 전용 지침을 사용한다. 설정은 대표당 최대 4모델 단계, 자동 재시도 0회(`max_attempts=1`), 도구 호출 최대 8회다. 이를 새 어댑터에서도 보호해야 한다.

CQ01 약속은 앞선 접수 초안의 별도 승인·반영 결과를 사용한다. 다른 대표는 독립 고정 fixture를 구성하며 CQ06은 별도 승인된 두 버전을 사용한다. 10개가 모두 같은 업무 DB의 연속 변화라는 설명은 맞지 않는다.

## 공통 재사용 / B 내부 보완 / 공동 검토

| 항목 | 확인한 현재 구현 | 분류 및 후속 |
| --- | --- | --- |
| 신원·권한 | B Host가 공통 IdentityProvider와 요청 신원 경로를 사용. B Service는 CommonService를 상속하고 B 도구도 공통 `_run`을 사용 | 공통 경로 유지. 역할 등록은 사용자 권한을 늘리지 않음 |
| 승인·확정 저장 | 공통 review/apply 경로. CQ01은 의미 검토 뒤 draft_id/version/content_hash·작성자와 다른 검토자·검토 내용을 별도 확인 | 재사용. 의미 검토를 업무 승인으로 바꾸거나 Agent 도구에 review/apply 추가 금지 |
| 공통 A/B 계산 | B Service는 공통 기반을 상속. B 생산·납기·대응 계산은 기존 B 함수에 있음 | 공통 A/B를 복제하지 않고 기존 B 계산 재사용. 새 LLM 계산으로 대체하지 않음 |
| 누적 한도·sandbox·도구 관찰 | 앞선 로컬 B 보완에서 공통 budget/cost/sandbox/identity 및 task 인자 전달을 복구 | 현재 코드의 출처를 구분. 이번 단계에서 새 수정·실행 검증 없음 |
| 고유 입력·출력·지침·검증 | 역할 지침, provider 구조화 출력, provenance 검사, 사후 evaluate가 이미 존재 | B 내부 어댑터 설계 대상. 기존 판정·미확인·초안 상태를 보존하며 Spec/messages/결과 검증기로 연결 |
| 도구 등록 | B Gateway는 공통 목록에 B 도구를 합침. 공통 AgentSpec은 공통 Gateway 목록만 인정 | 7역할 중 6역할의 typed 등록 차이. 기존 공통 도구만으로 구성 가능한지 먼저 검토. 공통 도구/권한 계약 확대가 필요하면 공동 검토 |
| 확정 근거·버전 | 공통 run_task는 실제 도구 응답과 확정 스냅샷으로 AgentResult 근거를 조립 | B 도구를 허용하는 것만으로 해결되지 않음. 아래 근거·고정 조건 설계 필요 |
| 비용 | B provider는 앞선 보완으로 공통 begin_paid_call 사용. 실제 실행기의 설정·승인 연결은 미완성 | 기존 CostPolicy 전달로 해결 가능한 B 설정 문제부터 설계. 공통 비용 규칙 변경이 필요하다고 미리 단정하지 않음 |
| 화면 | B 노트북·CLI 진입점은 존재. 공통 web/agents 경로에서 B 역할 라우팅은 찾지 못함 | 노트북을 이번 시연 화면으로 삼을지, 별도 웹 연결도 필요한지 단계 3에서 범위 결정. 기본 네 화면을 임의 변경하지 않음 |
| 도메인 schema | 루트 model.py에 B 도메인 확장이 이미 들어 있음 | 기존 변경의 공동 검토 여부 확인 필요. 이번 작업이 새로 변경한 공통 계약으로 보고하지 않음 |

## 새 연결 설계에서 놓치면 안 되는 차이

1. **결과 계약**: 현재 실행기는 `run_agent` 뒤 각 모듈의 `evaluate`를 호출한다. 공통 [run_task](../../ontologics/host.py)는 등록된 schema, 필요하면 `decode_result`와 `validate_result(value, task, tool_results)`로 검증하고 `agent-result-1`을 만든다. 기존 모델 답을 임의의 새 답으로 바꾸지 않으면서 구조·업무 검증을 연결해야 한다. 예제 model_reader 성공은 B 전환 성공이 아니다.
2. **근거 수집**: 공통 `_TaskReads.CONFIRMED_READS`에는 B 전용 도구가 없다. B 응답에 Evidence ID가 들어 있다는 사실만으로 공통 EvidenceRef가 만들어지지는 않는다. 확정 버전·SourceDocument·locator·hash를 실제 읽은 근거와 연결해야 한다. 초안·접수 자료는 확정 근거로 승격하지 않는다.
3. **조건 고정**: [PinnedContext.validate_call](../../ontologics/harness.py)은 공통 조회의 version과 공통 investigate 조건을 검사한다. B 도구의 version/as_of/대상과 CQ06의 before/after 조건은 이 공통 메서드에서 검사하지 않는다. 기존 B provenance 검사와 별개로 typed task에서 실제 호출 인자까지 고정되는지 확인해야 한다.
4. **CQ06 두 버전**: 단일 고정 버전에서 공통 lookup을 다른 버전으로 호출하면 기존 검사가 거부한다. 공통 compare_investigation의 두 버전 지원은 B 대응안 비교를 그대로 대신하지 않는다. before/after 각각의 확정 근거, 계산 결과, 같은 약속·배분 조건을 보존할 방안을 정해야 한다.
5. **비용 설정 전달**: [load_authorized_settings](../../examples/order_fulfillment/b_run_transport.py)는 현재 키와 고정 모델/timeout/출력만 남긴다. 공통 [비용 정책](../../ontologics/paid_budget.py)의 `ONTOLOGICS_REQUEST_MAX_COST_USD`, `ONTOLOGICS_MODEL_PRICES_JSON` 설정이 이 경로에 전달되지 않는다. 전체 실행기의 live 호스트도 현재 cost_policy=None이다. 정책을 전달해도 이전 ‘입력·금액 별도상한 없음’ 승인이 새 유한 상한 승인이 되지는 않는다.
6. **비용 범위**: 대표마다 새 요청/호스트를 만들므로 요청별 공통 비용 상한과 캠페인 전체 비용 상한을 혼동하면 안 된다. 기존 전체 40요청 원장은 요청 수 제한이지 전체 금액 제한이 아니다. 가격·상한·적용 범위와 이번 실행 승인 결합을 설계해야 하며, 무료 모형의 가상 가격(1/2)과 상한(100)은 실제 값으로 사용할 수 없다.
7. **현재 유료 차단**: `live_readiness()`는 두 검토 코드를 반환하는 고정 차단이다. 자동으로 준비 상태를 판정하는 완성된 기능이 아니다. 비용 설정만 추가하거나 차단 코드를 삭제하는 것으로 B typed 연결까지 완료되지는 않는다. 이번 단계에서는 유지한다.
8. **기존 schema 출처**: `git diff ca7c391 -- ontologics/model.py`는 86추가/4삭제이며 최신 관련 커밋은 기존 B `4539c90`이다. Contract/ContractItem/DeliveryRound, 이행·재고·생산·배분·출하·관측·납품 조건 등의 B schema 확장을 포함한다. 이번 작업에서 공통 파일을 수정하지 않았다는 사실과 main 대비 공통 파일 차이가 없다는 주장은 다르다. 과거 공동 합의 유무는 코드만으로 확정할 수 없다.

## 기존 계약 안에서 먼저 검토할 선택지

- CQ01 접수는 기존 공통 도구 범위 안에서 typed 어댑터를 설계할 수 있다. 기존 초안 준비·검증 흐름을 보존하고 실제 완료 여부는 향후 회귀로 판정한다.
- 나머지 역할은 공통 조회로 읽은 확정 자료와 기존 B의 신뢰된 계산 함수를 조합하는 방안을 먼저 비교한다. 계산을 모델에게 맡기거나 B 도구를 다른 이름으로 숨기면 안 된다. 모든 필요한 자료·버전·근거를 대표당 4요청 안에 확보할 수 있는지는 아직 입증하지 않았다.
- CQ06을 버전별 task와 신뢰된 비교로 나누는 선택지도 있지만 모델 요청 수·근거 결합·시연 의미가 바뀔 수 있다. 이를 바로 채택하거나 한도를 늘리지 않는다.
- 기존 도구만으로 요구를 지킬 수 없으면 필요한 B 도구·typed 근거·조건 검증만 구체적으로 적어 공통 계약 검토 대상으로 남긴다. 새 권한 체계나 별도 예산 체계를 만들지 않는다.
- 비용은 운영자가 정한 기존 CostPolicy를 B 호스트까지 전달하는 설계부터 시작한다. 실제 가격·상한을 추정하거나 키/환경 파일을 열 필요는 없다. 정책 자체 변경이 필요한 경우에만 그 변경을 별도로 공동 검토한다.

## 검증 범위와 다음 단계

이번 단계에서는 소스·문서·기존 테스트 정의만 읽었다. 기존 `test_main_task_connection.py`의 한 검사는 공통 도구의 허용 여부를, 다른 검사는 model_reader의 B 호스트 통과를 확인한다. 어느 것도 실제 B 7역할의 typed 업무 전환을 검증하는 테스트는 아니다. 이전 무료 실행·전체 회귀의 결과와 한계는 [앞선 보고](B_MAIN_COMPAT_REPORT.md)에 남아 있으며 이번 점검으로 재실행하거나 새 성공으로 계산하지 않는다.

단계 3에서 확정할 내용:

1. 이번 화면 연결의 범위: 기존 B 노트북을 대상으로 할지, 별도 웹 라우팅까지 포함할지.
2. 기존 공통 도구를 사용하는 B 어댑터 구성과 그 방식으로 해결되지 않는 도구·CQ06 근거/조건 계약의 최소 검토 목록.
3. 실제 호출 없이 비용 설정 전달·요청/캠페인 상한 범위·실행 승인 결합 방안.
4. 보존할 기존 평가기와 새 계약 회귀 목록: 권한·고정 조건·확정 근거·정확 초안 승인·작성자 분리·4/40요청·중복 차단·실패 중단·보존·종료.

이 문서는 단계 3의 설계와 후속 구현을 자동 승인하지 않는다. 단계 2 결과를 사용자와 확인한 뒤 다음 단계를 진행한다.
