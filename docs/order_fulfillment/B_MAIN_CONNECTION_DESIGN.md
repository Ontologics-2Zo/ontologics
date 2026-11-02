# B 리팩터링 연결 설계

작성일: 2026-10-29. 기준: 로컬 `feature/b-first-mvp`, HEAD `f264955`, 병합한 main `ca7c391`.

**현재 연결 방향은 아래 설계다. B 입력·계산 경계, CQ01 약속·CQ02 현황·CQ03 생산·CQ04 정상/혼합 납기·CQ05 정상/혼합 대응·CQ06 불량/운송 변경 비교의 공통 실행 연결을 무료로 검증했다(8~21절). main 모델 원복·버전 정리는 아직 하지 않았다.** 기존 목표였던 10대표 시연 절차·전체 B 그래프의 공통 저장·승인 연결은 이번 리팩터링의 필수 조건에서 제외한다. 아래 보관한 과거 설계보다 이 절을 우선한다. main 공통 코드를 원본으로 유지하면서 B Agent와 기본 예제를 정상 동작시키고, 이후 승인된 실제 LLM 실행까지 이어지는 것이 목표다.

현재 로컬 `model.py`에는 B 확장이 남아 있다. 무료 통과가 main 원복이나 전체 Agent 연결 완료를 뜻하지 않는다. 현재 범위는 8~19절이며 노트북·기존 결과·기존 DB는 그대로다. 각 실행 검증은 새 PostgreSQL cluster/schema를 사용하고 종료했다.

## 1. 선택한 연결 방식

```text
B 기본 예제: 사례·입력 revision·원문 hash·기준 시각 고정
 → B 입력 검증 / 기존 B 업무 계산
 → B AgentSpec + AgentTask
 → 기존 공통 호스트 등록 → request.run_task
 → 공통 provider + 허용된 공통 제품/구성 조회
 → B 결과 검증 → AgentResult + 한국어 답변 + 입력 출처
```

B의 계약·재고·생산·운송 자료는 B 소유 입력으로 처리한다. 공통 DB에는 main이 아는 제품·구성·설계 근거만 두며, B 타입을 공통 DB에 저장하지 않는다. 공통 조회의 제품/구성 식별자와 B 입력의 대상이 일치하는지도 B에서 검사한다. Agent가 수행한 공통 조회와, 신뢰된 B 코드가 수행한 계산을 실행 기록에서 구분한다.

[README의 확장 Agent 연결](../../README.md#동료의-확장-agent-연결)과 [공통 실행 경계](../AGENT_RUNTIME.md#확장-agent의-수정-경계)를 따른다. `AgentSpec`은 역할의 입출력·지침·요청 도구·한도를 선언하는 계약이며, 새 그래프 타입을 등록하거나 권한을 부여하는 장치가 아니다. 호스트 등록과 `request.run_task`를 사용하되, B 계산을 공통 `lookup`인 것처럼 등록하거나 도구 허용 목록을 우회하지 않는다.

| 책임 | 사용할 위치 / 방식 |
| --- | --- |
| B 스키마·입력 검증·업무 계산 | `ontologics/order_fulfillment/`의 기존 분리 코드. 공통 schema에 주입하지 않고 B 입력을 검증 |
| 역할 지침·payload·결과 검증·예제 조립 | `examples/order_fulfillment/`의 B 모듈과 기존 `b_task_agent.py` 우선 재사용 |
| 실행·신원·허용 도구·고정 context·누적 예산·provider | main 공통 코드 그대로 재사용 |
| 확정 근거 | 실제 공통 스냅샷에서 성공적으로 조회한 설계 근거만 공통 `EvidenceRef`로 반환 |
| B 원문·관측 자료 출처 | B 결과 내부의 입력 출처로 기록. 미등록 자료를 공통 확정 근거로 표시하지 않음 |
| 전체 B 그래프의 초안·승인·확정 저장 | 이번 목표에서 제외. Agent가 직접 저장·승인하거나 별도 승인 체계를 만들지 않음 |

공통 A/B 판정·신원·승인 의미는 재정의하지 않는다. B의 수량·납기 계산과 CQ06 입력 변화 비교는 B 업무 계산이며, 공통 A/B 판정을 대체하지 않는다.

## 2. CQ별 재사용과 변경 범위

| 업무 | 재사용할 B 기능 | 이번 설계에서 맞출 부분 |
| --- | --- | --- |
| CQ01 접수 | `contracts.extract_contract`와 추출·정합성 검사 | 모델이 원문에서 접수 후보를 만들고 B 검사가 대조. 공통 제품/구성을 실제 조회. 결과는 **미등록 접수 후보**이며 공통 저장 초안/업무 승인 완료가 아님 |
| CQ01 약속 조회 | `service.Service.delivery_round`의 조회 내용 | DB 접근에서 B 입력의 약속 정보 투영을 분리. 입력에 명시된 약속이라는 출처 표시 |
| CQ02 현황 | `fulfillment.fulfillment_status` | B 입력 검증 진입점으로 전환. 입력 확인 상태를 사람 승인으로 해석하지 않음 |
| CQ03 생산 | `production.production_completion`, `calculate_completion` | 기존 계산·미확인 처리는 유지하고 B 스키마 검증으로 연결 |
| CQ04 납기 | `delivery.delivery_forecast` | 정상/혼합 자료의 계산·대상·시간·근거 검증 유지 |
| CQ05 대응 | `response_plan.response_plan` | 미발송·미등록 대응 후보로 반환. 외부 발송/약속 변경 없음 |
| CQ06 변화 비교 | `response_comparison`의 양쪽 계산·비교 가능성·차이 계산 | **두 고정 B 입력 revision 비교** 진입점 필요. 확정 DB 버전 전제의 진입점을 그대로 재사용하지 않고 순수 계산·비교 규칙을 분리. 가짜 DB 버전을 넣지 않음 |

CQ01의 의미 검토는 후보에 대한 검토다. 정확한 공통 저장 초안의 업무 승인과 같다고 표시하지 않는다. 공통 승인 기능을 다시 쓰게 되는 경우 작성자/검토자 분리와 정확한 초안 버전 조건을 그대로 적용한다.

CQ06은 입력 revision·hash·as_of를 양쪽에 기록하고 공통 카탈로그의 `data_version`과 분리한다. 기존 `compare_response_plan`은 승인된 DB 버전 의미를 검사하므로 메타데이터 전제를 B 내부에서 분리해야 한다. 현재 재계산 fixture 로더가 주는 것은 전체 after 그래프가 아니라 변경 노드 묶음이다. 동일 기준 입력을 복사하고 ID별로 교체·추가해 각 after 입력을 독립적으로 구성하며, 여러 변화 사례를 연속 누적하지 않는다.

## 3. 입력·답변·근거 계약

- `PinnedContext.data_version` 및 공통 결과의 `data_version`은 실제 공통 카탈로그 스냅샷을 가리킨다. B 스키마 추출 버전, Agent 역할 버전, 입력 revision과 섞지 않는다.
- B 입력에는 사례 ID, 업무 대상, 기준 시각, 입력 revision, 원문 위치·hash·가상/실제 구분을 둔다. 현재 고정 예제 자료는 가상 자료다. 신원·검토자·승인 여부를 모델 payload로 부여하지 않는다.
- 모델 답변은 한국어 답변과 검증 가능한 업무 값·미확인·후보 상태를 반환한다. B 입력 출처는 결과 내부 `input_sources`에 두며 CQ06은 양쪽 출처를 구분한다. 이는 제안 필드이며 실제 payload 계약은 구현 전에 확정한다.
- 공통 최상위 `evidence_ids`/`EvidenceRef`에는 실제 성공한 공통 조회의 근거만 둔다. 로컬 입력 출처는 별도이며 카탈로그 근거가 B 재고·납기 전체를 입증한다고 표시하지 않는다.
- B 결과 검증기는 고정 입력·기존 계산 값·실제 관찰된 공통 조회와 모델 출력을 대조한다. 숫자나 출처가 틀리면 실패 처리하며 모델 답을 자동 교정해 성공으로 만들지 않는다. 구조 디코딩과 업무 검증을 구분한다.
- 공통 schema 검사는 중첩 업무 객체의 의미까지 보증하지 않는다. B 입력 검사에 필수값·타입·ID 중복·단일/목록 참조·원문 hash·출처·시간·미확인 규칙을 포함해야 한다. 현재 추출된 `validate_domain`만으로 완결됐다고 보지 않는다.
- 공통 Task 및 payload는 canonical UTF-8 기준 각각 100,000바이트 제한이 있다. 현재 전체 catalog JSON은 약 151,090바이트이므로 통째로 payload에 넣지 않는다. 대상 사례와 필요한 업무 값만 전달하고 상세 원문/기록은 B 로컬에서 확인한다. 최종 payload 크기는 구현 후 측정한다.

## 4. 대안과 선택 이유

| 대안 | 판단 |
| --- | --- |
| 공통 조회 + 신뢰된 B 계산 + typed Agent | **현재 선택.** main 원본 유지와 저장 연결 제외라는 최신 목표에 맞음. 입력 출처와 확정 근거의 구분 필요 |
| 기존 B 전용 호스트·gateway·`run_agent` 유지 | 임시 호환 경로는 될 수 있으나 main 원복 후 의존성이 남고 전체 typed 연결을 증명하지 못함 |
| B 타입·도구를 공통 등록 | 기존 확정 저장 의미를 유지하는 대안이지만 공통 도메인/도구 계약 검토가 필요. 현재 구현 범위 밖 |
| 공통 전역 schema 변경·허용 목록 우회·공통 도구명으로 B 기능 위장 | 제외. main 고정 및 공통 권한 계약에 어긋남 |

과거에는 기존 B 도구 trace·정확한 검사 순서·10대표 시연 절차를 최대한 보존하기 위해 두 번째 방향을 권장했다. 현재는 업무 계산·미확인·권한 보호를 보존하고, 불필요한 실행 구조와 테스트는 정리하는 목표이므로 선택이 달라졌다. 과거 실제 성공 기록은 새 구조의 성공으로 승계하지 않는다.

## 5. 후속 구현 순서와 파일 경계

1. **B 입력·계산 경계 정리:** `schema.py`, `schema_validation.py`, CQ별 계산/검증 모듈을 사용해 공통 `model.SCHEMA` 확장에 기대지 않는 진입점을 완성한다. CQ01 약속 조회의 입력 투영과 CQ06 입력 revision 비교를 분리한다. 기존 고정 사례의 수량·납기·미확인 검사를 재사용한다.
2. **B typed 역할 연결:** `b_task_agent.py`와 B 예제 조립부에서 `AgentSpec`·등록·`request.run_task`를 연결한다. 우선 한 사례로 입력→실제 공통 조회의 모의 응답→B 검증→결과 경로를 무료 검증한 뒤 CQ01~06으로 넓힌다. 7역할 유지나 새 프레임워크 도입을 전제하지 않는다.
3. **불필요한 경로 정리:** `b_run_workflow.py` 등 사용처를 조사해 B의 schema 전역 변경·공통 host 대체·중복 provider를 제거/격리한다. 시연 전용 파일은 핵심 보호장치 이전과 사용처 확인 후 정리한다. 기존 출력/실제 실행 근거는 코드 정리와 별도로 보존한다.
4. **main 원복 및 최종 무료 검증:** B 의존성을 끊은 뒤에만 공통 `model.py` 및 B가 바꾼 다른 공통 파일의 원본 복원 범위를 확정한다. 기준은 위 main 커밋이다. 공통에 새 동작을 추가하지 않는다. 새 격리 환경·별도 schema에서 기본 예제와 공통 회귀를 검사한다.

완료한 구현은 8~15절에 기록한다. 공통 파일의 원본 복원과 B 분리 구현을 동시에 섞어서 원인을 불명확하게 만들지 않는다. 루트 `PROJECT_STATUS.md` 및 기존 노트북은 수정 대상이 아니다.

## 6. B에서 가능한 일과 공통 검토 사항

**B 소유 영역에서 진행 가능한 일:** 입력 로딩·B 검증·계산 분리, 고유 지침/역할 payload 설계, 기존 공개 조회만 요청하는 Spec, 호스트 등록 조립, 기본 예제 및 무료 회귀 검사. 공통 코드 수정이 필요하다는 근거는 현재 선택안에서 발견하지 못했지만 전체 동작을 실행으로 입증한 것은 아니다.

**공통 담당자 검토가 필요한 부분:** B의 새 도메인 의미·외부에 공개할 Task/Result 필드 및 역할 도구 허용 등록. 특히 미등록 입력 출처와 공통 확정 근거의 구분, CQ01 후보/CQ06 입력 비교의 의미를 명시한다. 고유 payload를 정의할 수 있다는 사실이 공개 계약 변경 권한을 뜻하지 않는다. 팀 통합 전 검토 대상으로 남기며, 이 문서만으로 컨펌됐다고 표시하지 않는다.

**현재 제외한 부분:** B 전체 그래프의 공통 확정 저장, 새 공통 도구·도메인 타입 등록, 공통 A/B 판정·승인 정책 변경, 외부 발송. 이 기능을 다시 요구하면 별도 범위 검토가 필요하며 B 코드에서 우회 구현하지 않는다.

## 7. 무료 검증 기준과 향후 실제 호출 경계

- main 원본 상태에서도 B 입력 검증·계산·Agent 기본 예제가 동작하고, 공통 전역 값 변경이나 B 타입 `put` 없이 완료되어야 한다.
- CQ별 업무 정답·불량/운송 변화·미확인·범위/시간 오류를 검사한다. 오류 문구·import alias·옛 도구 호출 순서를 같게 만드는 테스트는 기능 보호 목적을 재검토한다.
- 공통 조회 미실행, 다른 제품/버전 근거, 변경된 입력 hash/revision, 틀린 업무 값, 누락된 출처, 과대 payload를 거부해야 한다. 잘못된 모델 응답과 실패는 무료 대역으로 검증한다.
- 무료 검증 성공과 실제 LLM 성공을 분리한다. DB 검증이 필요하면 기존 실행 환경을 재사용하지 않고 새 격리 서버·schema를 쓰며 사용 후 종료를 확인한다.
- 향후 실제 호출은 공통 provider·누적 비용 정책을 사용한다. B 실행 설정은 고정 모델 `gpt-5.4-mini-2026-03-17`, 출력 최대 1,024토큰, 요청당 최대 30초, `max_attempts=1`(자동 재시도 0), `max_repairs=0`, 대표당 최대 4요청/전체 최대 40요청으로 제한한다.
- 대표를 별도 공통 요청으로 실행하면 전체 40요청이 자동 집계되지는 않는다. 대표 간 누적 요청 집계·중복 시작 방지·실패 후 후속 시작 차단은 신뢰된 B 실행 조립부가 유지하며, 각 호출의 공통 예산·권한·비용 검사를 대체하지 않는다. 역할이 새 예산을 만들어 한도를 초기화하지 않는다.
- 실제 호출 전 무료 사전검사와 해당 실행에 결합된 명시적 승인이 필요하다. 운영자가 설정한 공통 `CostPolicy`의 가격·한도가 필요하며 B가 가격을 추정하거나 별도 예산을 만들어 우회하지 않는다. 실패 후 후속 유료 호출 중단·중복 시작 방지·요청 기록·사람의 의미 검토는 유지한다.

**증거 범위:** 설계는 README·공통 계약·B 소스의 정적 대조와 검토에 근거한다. B 입력 계산은 9절, CQ01~05 Agent/DB 무료 통합 검증은 11·13·15·17·19절을 따른다. 새 격리 서버만 사용하고 종료했다. 실제 유료 요청 수는 0이다.

## 8. 승인된 다음 작업의 한정 구현 계획

이번 구현은 위 5절의 첫 단계인 **B 입력 검증·계산 경계**에 한정한다. 공통 `model.py`와 실행·저장 코드는 수정하지 않으며 AgentSpec/호스트 전환·시연 정리·원복은 뒤 단계다.

- 제거할 결합: CQ02/03/04 계산이 B 확장까지 들어 있는 공통 `validate_graph`를 직접 요구하는 구조. 공통 타입은 기존 공통 검증기에, B 타입은 B 입력 검증기에 맡긴다. 공통 validator/schema의 전역 변경이나 검증 실패 fallback은 도입하지 않는다.
- 변경 범위: B `schema_validation.py`, `fulfillment.py`, `production.py`, `delivery_forecast.py`, CQ01 `contracts.py`, CQ06 `response_comparison.py`와 필요시 재계산 fixture 합성 helper. 해당 B 회귀 검사만 보완한다.
- 동작 보호: 기존 CQ01 추출, CQ02 수량/미확인, CQ03 생산 정수·시간 계산, CQ04 도착 수량, CQ05 미발송 후보, CQ06 비교 산술을 먼저 무료 검사한다. 공통 함수와 같은 객체여야 한다는 과거 import 단언은 새 B 진입점의 실제 동작 검사로 바꾼다.
- 새 검사: B 필수값/타입/ID/참조/근거/origin, 공통 원문 hash/식별/시간 검사의 재사용, 입력 불변성, main 원본 validator를 사용한 B 계산. CQ01은 저장 없는 약속 투영, CQ06은 입력 revision/hash 비교를 검사한다.
- 순서: 기존 무료 회귀 → B 검증 및 계산 진입점 → CQ01/CQ06 분리 → 영향 회귀와 독립 검토. 기존 결과와 공통 파일의 시작 해시를 대조한다.
- 발견한 fallback: 이번 계산/검증 범위에는 검증 실패를 성공으로 대체하는 별도 경로가 없다. 기존 unknown 분기는 입력 부족을 드러내는 업무 동작이므로 유지한다.

검증은 네트워크/DB 접속 없이 수행한다. 전체 DB 회귀·공통 저장 승인·실모델 동작의 완료로 보고하지 않는다.

## 9. B 입력·계산 경계 구현 결과

**완료 범위:** 공통 모델 확장이 없어도 동작하는 B 입력 검증·약속 투영·CQ02~06 계산. AgentSpec/호스트 등록 전환과 실제 예제 실행부 연결은 다음 단계다. 공통 원본 복원·그래프 파일 재생성·시연 파일 삭제·버전 정리는 하지 않았다.

| 변경 파일 | 내용 |
| --- | --- |
| `ontologics/order_fulfillment/schema_validation.py` | `validate_input_graph`: 공통 타입은 기존 공통 검증기로 원문 hash·식별·시간 검사를 재사용하고, B 10타입의 필수값·타입·ID·참조·origin·근거·업무 규칙을 별도로 검증. 공통 schema에 등록하지 않음 |
| `ontologics/order_fulfillment/fulfillment.py`, `production.py`, `delivery_forecast.py` | 전체 B 입력 검증 진입점으로 전환. 수량/시간 산술·미확인 정책 유지 |
| `ontologics/order_fulfillment/contracts.py` | 저장 없이 입력의 약속을 반환하는 `delivery_promise`. 미등록 상태를 명시하고 원본과 분리된 결과 반환 |
| `ontologics/order_fulfillment/response_comparison.py` | 산술 조립을 재사용하는 `compare_input_response_plan`. 실제 입력 hash·opaque revision·시각을 기록하며 DB 버전으로 표시하지 않음. 기존 DB 비교 진입점 계약 유지 |
| `ontologics/order_fulfillment/recalculation_demo.py` | `load_demo_recalculation_input`로 사례마다 baseline을 새로 읽어 변경 노드를 ID별 합성. 대안 누적·중복 ID 차단 |
| `tests/order_fulfillment/test_input_validation.py`, `test_input_comparison.py` | 새 입력 경계·main 원본·CQ01/CQ06 의미와 기존 계산 회귀 19개 |
| `tests/order_fulfillment/test_schema_extraction.py` | 오래된 공통 validator import 동일성 단언 한 곳을 새 B 입력 진입점 참조로 전환 |
| B 연결 설계서 및 B `PROJECT_STATUS.md` | 한정 계획·검증·남은 작업 기록. 루트 공통 상태 문서 변경 없음 |

무료 검증:

- 변경 전 핵심 8모듈 **189개 통과**. 변경 후 신규 19개를 포함한 핵심 10모듈 **208개 통과**.
- 확장 16모듈 **302개 중 300개 통과, 2개 기존 버전 단언 실패**. 핵심 10모듈은 `test_schema_extraction`, `test_contracts`, `test_fulfillment`, `test_production`, `test_delivery_forecast`, `test_response_plan`, `test_response_comparison`, `test_recalculation_demo`, `test_input_validation`, `test_input_comparison`이다. 추가 6모듈은 `test_production_validation`, `test_delivery_validation`, `test_schema_export`, `test_representative_graph`, `test_model_oracle`, `test_storage_boundary`이며 모두 `tests.order_fulfillment` 아래에 있다.
- 남은 두 실패는 `test_production_validation.py:256`, `test_delivery_validation.py:170`의 `graph-0.1` 기대값과 현재 `model.py`의 `graph-0.5` 불일치다. 이 세 파일은 이번 시작 해시와 같다. 버전 정리를 보류한 상태이므로 기대값을 임시 변경하거나 공통 버전을 수정하지 않았다. 전체 호환 통과로 보고하지 않는다.
- 별도 Python 프로세스에서 고정 main `ca7c391` 원본 모델을 테스트에만 로딩해 CQ01 약속·CQ02~05 계산과 CQ06 불량/운송 입력 비교를 독립 정답과 대조했다. 이때 공통 검증기는 B 타입을 계속 거부하고 공통 schema는 변하지 않았다. 실제 소스 파일이나 실행 코드의 schema를 치환하는 방식이 아니다.
- 독립 리뷰에서 B 10타입의 기존 95필드 × 10종 값, **950개 형태 변형**을 확인했다. 예외 0개·거부 947개이며 허용 3개는 빈 확인 객체와 준비/운송 시간 0초다. 미확인을 오류나 0으로 바꾸지 않았다.
- 변경 Python 10개 파일의 AST·공백 검사와 `git diff --check`를 통과했다. 별도 lint/typecheck 도구(`ruff`, `mypy`, `pyright`)는 설치돼 있지 않으며 새 의존성은 추가하지 않는다.

전체 `unittest discover`와 DB/실제 Docker·서버 검사는 실행하지 않았다. 해당 테스트들은 기존 환경 설정/서버를 사용할 수 있어 이번 순수 입력 계산 검증 범위와 분리했다. 새 서버도 시작하지 않았으므로 이번에 종료할 서버는 없다. 실제 유료 API 요청 0회·자동 재실행 0회다. 파일 보존 확인은 이번 시작 해시 기준으로 수행하며 기존 실제 성공/실패를 새 구조의 실모델 성공으로 재분류하지 않는다.

9절 완료 당시의 다음 작업은 **B typed 역할 한 사례의 공통 연결**이었다. 후속 수행 결과는 아래 10~11절에 기록한다.

---

## 10. CQ01 약속 한 사례의 공통 실행 연결 — 수정 전 계획

이번 승인 범위는 새 B 입력 경계로 계산한 CQ01 약속 하나를 main의 `AgentSpec → AgentRegistration.from_spec → request.run_task`로 실행하는 것이다. 기존 접수/7역할 실행기를 교체하지 않고 `examples/order_fulfillment/b_promise_agent.py`에 역할·입출력·검증기·무료 기본 예제를 함께 둔다. B 입력 계산은 신뢰된 코드가 수행하고 모델은 실제 공통 lookup으로 제품/개정/구성을 확인한다. 공통 도구 추가·우회·fallback은 없다.

회귀 기준은 기존 입력/비교 검사, 새 약속의 입력 고정·실제 조회 대조·수량/납기/대상 변조 거부·권한/도구 제한·실패 중단·공통 runtime 감사 기록이다. 먼저 기존 입력/비교 검사를 실행하고 구현 후 새 PostgreSQL cluster와 별도 schema에서 대역 provider로 공통 호스트를 실행한다. B 계약 노드는 DB에 저장하지 않으며 공통 `data_version`은 카탈로그 버전이다. 미등록 B 원문은 내부 `input_sources`에만 기록한다. 한국어 자유 문장의 의미 평가는 이후 실제 모델 검토로 남긴다.

기존 코드/결과 해시를 보존하고 이번 변경은 새 B 예제·해당 회귀·이 문서와 B 작업 기록으로 제한한다. `model.py`·공통 코드·루트 상태·기존 그래프·노트북을 변경하지 않는다. 실제 provider/키 로드/유료 호출0, push/PR0. main 원복·나머지 역할·비용 승인 실행 연결은 다음 단계다.

## 11. CQ01 약속 공통 연결 결과와 실행 방법

이번 변경 파일은 네 개다.

| 파일 | 내용 |
| --- | --- |
| `examples/order_fulfillment/b_promise_agent.py` | B 약속 입력 계산·고정 Task·AgentSpec·조회 대조·한국어 결과·무료 provider·CLI. main의 contracts/host/runtime/identity를 직접 재사용 |
| `tests/order_fulfillment/test_b_promise_task.py` | 입력 고정, 원문/확정 근거 분리, 실제 공통 조회, 업무 값/대상/출처 변조 거부, 권한/도구 실패 중단, 순차 4요청, 감사·미저장 검사 10개 |
| 이 문서 | 수정 전 계획·연결 계약·검증·제한 |
| B `PROJECT_STATUS.md` | 해당 단계 작업 기록 |

새 예제는 `b_promise`를 별도 공통 호스트에 등록한다. 기존 `b_host_agent`의 레거시 7역할/접수 저장 경로와 합쳐 등록하거나 교체하지 않았다. 전체 카탈로그를 모델 입력에 넣지 않고, 신뢰된 B 함수가 만든 약속 요약·대상 제품/개정/구성 3노드·입력 hash/revision·원문 locator만 전달한다. 세 실제 lookup의 노드가 입력과 정확히 일치해야 하며, 수량·기한·대상·미등록 상태·출처를 바꾼 출력은 보류된다. 공통 evidence에는 성공한 제품 조회의 확정 근거만 포함된다.

무료 검증 결과:

- 수정 전 입력/비교 19개 통과. 수정 후 확장 회귀 331개: **330개 통과, 기존 버전 단언 1개 실패, 오류/skip0**. 새 약속 10개와 B 입력/계산·기존 typed 접수·공통 Agent 계약/확장/harness를 포함한다.
- 확장 실패는 `test_b_typed_workflow.py:47`: 기존 시연 검사 기대 `graph-0.1`, 실제 로컬 B 모델 `graph-0.5`. 테스트 파일과 모델은 시작 상태 그대로이며 새 약속 모듈을 사용하지 않는 경로다. 이전 생산/납품 검사 2개의 버전 실패와 같은 보류 범주다. 이번 331개에 그 두 검사 모듈은 포함하지 않았고 전체 `unittest discover`도 수행하지 않았다. 전체 호환 통과로 표현하지 않는다.
- 별도 테스트 프로세스에 hash 고정 main `ca7c391` 원본 모델(12타입/graph-0.1)을 로드한 상태에서도 새 약속 **10개 모두 통과**. 실제 공통 파일이나 실행 예제의 모델을 치환하지 않았다.
- 새 DB의 실제 CLI 실행: `runtime.ok=true`, `error=null`, 공통 EvidenceRef2건, B 약속1000 piece·도착 기준 기한/장소 유지, DB 버전1 유지. 기본 대역은 모델2/도구4/repair0. 세 순차 조회 대역은 모델4회로 완료한다. `max_attempts=1`, repair0이며 실패 후 모델을 재호출하지 않는다.
- 새 입력과 예제의 Python3.10 AST/컴파일 및 `git diff --check` 통과. 별도 lint/typecheck 도구는 미설치, 의존성 추가0.

초기 환경 검사에서는 샌드박스의 PostgreSQL 공유 메모리/Unix socket 제한이 있었다. 새 cluster의 해당 명령만 제한 밖에서 실행했다. 기존 시연 테스트의 첫 확장 실행은 macOS `/var`→`/private/var` 임시 경로 차이로 원문 경계 검사가 실패했으며, `TMPDIR=/private/tmp`로 새 임시 입력 경로를 고정한 재검사 결과가 위 331개다. 공통 코드나 검증 기대값으로 우회하지 않았다. 첫 신규 검사에서는 공통 근거 오류가 B 의미 오류보다 먼저 발생하는 계약을 확인해, 잘못된 근거 테스트의 기대 코드를 실제 공통 `AGENT_EVIDENCE_MISMATCH`에 맞췄다.

새 cluster `/private/tmp/b-promise-pg.MEsoa6/pg`, 전용 Unix socket `s`, port54873만 사용했고 TCP listen은 껐다. 검증 후 남은 업무 schema0, pg_ctl 종료0/status3, PID 파일·socket 부재를 확인했다. 회귀 실행의 다른 DB 접속·Python 네트워크·실제 환경 파일 읽기 guard 위반0, 실제 유료 API0, push/PR0이다. 증거는 Git 제외 `artifacts/local/order_fulfillment/b_promise_connection_20261029_MEsoa6/`에 보존한다.

실행 순서는 **새 개발 DB/schema 준비 → 공통 카탈로그만 개발 seed → 아래 무료 예제 → 결과/근거 확인 → 서버 종료**다. 자동으로 기존 DB/환경 파일을 탐색하지 않으며 DB 설정을 생략하면 거부한다.

```bash
python -m examples.order_fulfillment.b_promise_agent \
  --database-url '새_개발_PostgreSQL_URI' --schema '새_카탈로그_schema' --version 1
```

이 CLI에는 유료 provider가 없다. 이후 실제 모델 실행은 공통 provider·고정 모델·출력1024/timeout30·CostPolicy·이번 실행 승인과 한국어 의미 검토를 별도로 연결해야 한다. 현재 한국어 문장은 대역 출력이며 자유 문장 의미를 자동 보증하지 않는다. 나머지 B 역할·기존 실행기 전환과 main 모델 원복은 다음 단계다. 전체 B 그래프 저장·승인은 계속 제외한다.

## 12. CQ02 현황 공통 연결 — 수정 전 계획 (2026-10-30)

이번 승인 범위는 CQ02 한 업무의 입력 계산과 공통 Agent 실행 연결이다. 기존 `fulfillment_status`의 수량·시각·미확인 규칙을 변경하지 않는다. 정상/관측 누락/다른 관측 시각/coverage 부족 입력을 무료로 확인하며, 상태 `complete`를 납품 완료나 미래 납품 가능 판정으로 바꾸지 않는다. 잔여 약속은 추가 생산 필요량과 구분한다.

1. 기존 파일·노트북·결과의 시작 해시를 보존하고 CQ02 계산·CQ01 입력 회귀를 먼저 실행한다.
2. CQ01의 입력 hash 고정·출처 조립·공통 조회 대조를 B 전용 `b_input_task.py`로 옮겨 CQ01/CQ02가 재사용한다. 공통 코드의 복제나 B 계산 도구 신규 등록은 없다. 실패를 성공으로 대체하는 fallback도 추가하지 않는다.
3. `b_fulfillment_task.py`에 CQ02 Spec/Task/검증기/무료 기본 예제를 둔다. 신뢰된 B 코드가 계산한 상태를 고정하고 모델이 제품/개정/구성을 공통 lookup으로 실제 확인한다. 공통 DB는 제품 카탈로그만 보유하고 B 자료는 미등록 입력이다. Task에는 전체 출처 locator, 출력에는 문서별 hash·경로·origin을 기록해 긴 원문 목록이 답변을 가리지 않게 한다.
4. 새 CQ02 회귀와 기존 CQ01·B 계산·공통 계약을 새 cluster/schema에서 무료 실행한다. 변조된 수량/대상/시각/미확인/출처, 조회 생략/불일치, 권한/도구 실패 중단과 순차 최대4요청을 확인한다. 별도 테스트 프로세스의 고정 main 원본 모델도 확인한다.
5. CLI 예제 확인·서버 종료·파일 보존 대조와 결과 기록을 완료한다. 이번 변경은 위 B 모듈3개·CQ02 테스트·이 문서·B 작업 기록에 한정한다. `model.py`·공통 코드·기존 시연/그래프/노트북·루트 상태를 변경하지 않는다. 실제 LLM/키 로드/유료 API/원격 push/PR0이다. 나머지 역할과 원본 복원·버전 정리는 후속 범위다.

## 13. CQ02 현황 공통 연결 결과 (2026-10-30)

변경은 B 소유 여섯 파일이다. 공통 `model.py`·호스트·실행기·저장·승인 코드와 기존 실행 결과는 변경하지 않았다.

| 파일 | 변경 |
| --- | --- |
| `examples/order_fulfillment/b_fulfillment_task.py` | 기존 `fulfillment_status` 결과를 고정한 Spec/Task, 공통 호스트 등록과 `request.run_task`, 무료 provider/CLI |
| `examples/order_fulfillment/b_input_task.py` | payload hash·입력 출처·실제 카탈로그 조회 대조를 CQ01에서 추출 |
| `examples/order_fulfillment/b_promise_agent.py` | 같은 B helper 재사용. 추출 전후 payload와 PinnedContext 동일 확인 |
| `tests/order_fulfillment/test_b_fulfillment_task.py` | 정상·미확인·시간·변조·권한·허용 도구·4요청 경계의 무료 검사 12개 |
| 이 설계서 및 B `PROJECT_STATUS.md` | 수정 전 계획과 결과 기록 |

B 수량·미확인·시간 계산은 기존 함수를 사용한다. 미확인은 `null`로 유지하고 확인된 빈 배정만 0이다. 잔여 약속은 추가 생산 필요량으로 해석하지 않는다. 입력은 미등록 가상 자료이며 공통 조회는 제품/구성 근거만 제공한다. 전체 B 그래프 저장·업무 승인·향후 납기 가능성은 완료된 것으로 표시하지 않는다.

B 입력 출처는 문서 2개와 locator 25개로 고정하며 공통 EvidenceRef와 구분한다. Task payload는 UTF-8 7,220바이트였다. 검증기는 구조화된 업무 값과 출처 전체, 실제 성공한 공통 lookup을 대조한다. 한국어 자유 서술의 의미 검토는 별도이며 무료 검증으로 보증하지 않는다.

| 검증 | 결과 |
| --- | --- |
| 수정 전 입력·현황·CQ01 기본 검사 | 40개 통과 |
| CQ01 약속 10개 + CQ02 현황 12개 | 22개 통과, 제외 0 |
| 별도 프로세스에 main 원본 모델을 로드한 같은 검사 | 22개 통과, 제외 0 |
| 관련 B·공통 확장 회귀 | 343개 중 342개 통과, 기존 버전 단언 1개 실패, 오류·제외 0 |
| 새 카탈로그 schema에서 실제 CLI | 성공. 모델 대역 2회·공통 도구 4회·repair 0 |
| CQ01 helper 추출 전후 비교 | 원본 백업 hash 일치, payload·PinnedContext 동일 |

기존 실패는 `test_b_typed_workflow.TypedWorkflowTests.test_cq01_typed_result_preserves_raw_answer_refs_and_one_shared_cost_ledger`의 `graph-0.1` 기대와 현재 로컬 모델 `graph-0.5` 불일치다. 새 CQ02 연결 검사와 분리하며 기대값을 바꾸지 않았다. 이전 생산·운송 검증의 버전 실패 2개는 이번 회귀 범위에 포함하지 않았다. 전체 테스트 통과나 main 원복 완료로 보고하지 않는다. 네 Python 파일의 Python 3.10 구문·메모리 compile 및 diff 공백 검사를 수행했다. 별도 lint/typecheck 도구는 설치되어 있지 않다.

CLI 정상 결과는 납품 100·운송 150·창고 250·진행 300·준비 100·대기 100, 잔여 약속 900·미배정 0이다. 예제 실행은 명시적으로 지정한 새 DB와 별도 schema를 요구하며 기존 settings/environment의 DB로 자동 연결하지 않는다. 실행 방법은 `python -m examples.order_fulfillment.b_fulfillment_task --database-url <새 격리 DB URL> --schema <새 카탈로그 schema> --version 1`이다. 이는 무료 scripted 예제이며 유료 provider를 연결하지 않았다.

이번 검증은 새 `/private/tmp/b-cq02-ntc8qfkz` cluster와 별도 schema만 사용했다. TCP listen 없이 전용 Unix socket을 사용하고 다른 DB URL·외부 socket·실제 환경파일 읽기를 차단한 검증기에서 수행했다. 남은 업무 schema 0개를 확인하고 서버를 종료했다. `pg_ctl status` 종료 코드 3과 PID/socket 부재를 확인했다. 실제 API 요청·키 로드·push·PR은 모두 0이다.

원문 로그와 보존 대조는 Git 제외 `artifacts/local/order_fulfillment/b_cq02_connection_20261030_ntc8qfkz/`에 보관한다. 다음은 CQ03 생산의 같은 연결이며 이번에는 진행하지 않았다. 실제 모델·공통 비용 정책·출력 1,024토큰·30초 제한·이번 실행 승인·한국어 의미 검토는 후속 실제 실행 연결에서 검증해야 한다. 현재 무료 예제 성공을 실제 LLM 성공으로 승계하지 않는다.

## 14. CQ03 생산 공통 연결 — 수정 전 계획 (2026-10-30)

이번 승인 범위는 CQ03 한 업무의 공통 실행 연결이다. 기존 `production_completion`·`calculate_completion`의 계산/검증을 그대로 재사용하며 CQ01·CQ02 연결과 공통 코드는 변경하지 않는다. 기존 파일·노트북·산출물 106,392개의 시작 hash와 문서 원문을 새 `/private/tmp/b-cq03-olgzdm1i`에 보존했다.

1. 수정 전 생산 계산·B 입력·기존 CQ01/CQ02 회귀를 무료로 실행한다. 입력은 카탈로그 + 계약 + CQ03 fixture이며 CQ02 snapshot을 섞지 않는다.
2. 새 `examples/order_fulfillment/b_production_task.py`에 CQ03 Spec/Task·검증기·무료 provider/CLI를 둔다. 기존 `b_input_task.py`의 고정 hash·출처·실제 카탈로그 조회 검사를 재사용한다. 신뢰된 B 계산의 구조화 값·가정·미확인을 고정하고 공통 제품/개정/구성 lookup만 허용한다. B 운영 자료는 미등록 가상 입력이며 공통 EvidenceRef로 표시하지 않는다.
3. 새 `tests/order_fulfillment/test_b_production_task.py`에서 정상 golden, 이전/최신 관측 누락, 다른 기준 시각, coverage 부족, 증가율 0, 목표 도달, 수량/시간/불량/가정/출처 변조, 권한·도구 실패, 순차 4요청 한도를 검사한다. 조건부 예상·목표 도달·최초 실제 완료·납기 가능을 구분한다.
4. 새 PostgreSQL cluster/별도 schema에서 CQ01~03 연결·관련 B/공통 회귀와 CLI를 실행한다. 별도 프로세스에 hash 검증한 main 원본 모델을 로드해 새 연결을 확인한다. 마지막 전체 discover는 외부 통신·기존 DB/환경파일을 차단하고 새로 생성한 로컬 시험 endpoint만 허용한다. 실제 Docker는 기존 opt-in 설정을 승계하지 않는다. 기존 실패와 환경상 제외를 새 회귀 실패와 구분한다.
5. 새 서버 종료/PID/socket 부재, 시작 파일 hash·HEAD·staging 보존과 정적 검사를 확인하고 결과를 이 문서와 B 상태에 기록한다. 수정 범위는 새 B 예제·새 검사·문서 2개뿐이다. 실제 API/키 로드/push/PR0이며 main 원복·버전 정리·기존 그래프/노트북/저장·승인 연결·CQ04 이후는 진행하지 않는다.

## 15. CQ03 생산 공통 연결 결과 (2026-10-30)

수정 전 14절 계획에 따라 아래 네 파일만 추가/수정했다. 공통 코드·`model.py`·CQ01/CQ02 예제와 helper·기존 계산·그래프·노트북은 변경하지 않았다.

| 파일 | 변경 |
| --- | --- |
| `examples/order_fulfillment/b_production_task.py` | 기존 생산 계산을 고정한 CQ03 Spec/Task·결과 검증·공통 등록/run_task·무료 provider/CLI |
| `tests/order_fulfillment/test_b_production_task.py` | CQ03 입력/공통 호스트 검사 12개 |
| 이 설계서·B `PROJECT_STATUS.md` | 계획·결과·남은 경계 기록 |

`production_completion`과 `calculate_completion`은 수정 없이 재사용한다. 새 역할은 기존 B helper의 hash·출처·카탈로그 대조를 사용하며 공통 `lookup`만 요청한다. 공통 DB에는 카탈로그만 있고 B 생산 기록은 미등록 가상 입력이다. 원문 문서 2개·locator 9개와 실제 공통 EvidenceRef를 구분하며 Task payload는 UTF-8 5,480바이트다. 선택 관측·계산 값·가정/경고·미확인과 입력 출처를 모델이 바꾸면 결과를 보류한다.

정상 결과는 목표 1,000·관측 양품 600·잔여 400개, 양품 90/시간·생산 100/시간·최근 불량률 0.1, 잔여 16,000초·조건부 완료 예상 `2026-10-27T14:26:40+09:00`이다. 최근 추세 지속·양품 사용 가능 가정이 있으며 실제 완료·출하·도착·납기 가능/업무 승인은 판정하지 않는다. 관측 누락/coverage/기준 시각/증가율 0의 미확인과 목표 도달을 각각 유지한다. 조회 시각을 늦춰도 관측/예상을 이동시키지 않는다. 목표 양품 도달도 최초 실제 완료 시각을 만들어내지 않는다.

| 무료 검증 | 결과 |
| --- | --- |
| 수정 전 생산·입력·CQ01/CQ02 회귀 | 61개 통과 |
| CQ01 10개 + CQ02 12개 + CQ03 12개 | 34개 통과, 오류/제외 0 |
| 별도 프로세스의 hash 검증한 main 원본 모델로 같은 연결 | 34개 통과, 오류/제외 0 |
| 관련 B·공통 확장 회귀 | 355개 중 354개 통과·기존 버전 실패 1개, 오류/제외 0 |
| 전체 `unittest discover` | 973개 중 964개 통과·기존 버전 실패 3개·Docker opt-in 6개 제외, 오류 0 (209.398초) |
| 새 카탈로그 schema에서 실제 CLI | 성공, 모델 대역 2회·도구 4회·repair 0 |
| 정적 검사 | 새 Python 2개의 Python 3.10 구문·메모리 compile, diff 공백 검사 통과 |

전체 실패는 기존 typed CQ01(`test_b_typed_workflow.py:47`), 납기 schema(`test_delivery_validation.py:170`), 생산 schema(`test_production_validation.py:256`)의 `graph-0.1` 기대/현재 모델 `graph-0.5` 차이 3개다. 이전 기록과 같은 실패이며 이번 테스트 기대값이나 모델을 바꾸지 않았다. 모델 원복 단계에서 정리할 대상으로 남긴다. 실제 Docker 4개와 Docker+접수 통합 2개는 기존 서버/controller 설정을 승계하지 않아 제외됐다. 전체 통과로 표현하지 않는다. 별도 ruff/mypy/pyright는 설치되어 있지 않으며 새 의존성을 추가하지 않았다.

이번 검증은 새 `/private/tmp/b-cq03-olgzdm1i` cluster와 테스트별 별도 schema를 사용했다. 기존 DB URL·실제 환경파일·외부 socket을 차단했다. 전체 검사는 이 프로세스가 새로 bind한 loopback/전용 임시 Unix socket만 허용했고 차단 위반은 0이었다. 카탈로그 CLI schema를 정리한 뒤 남은 업무 schema 0개를 확인했다. 서버 종료 후 `pg_ctl status` 종료 코드 3과 PID/DB socket 부재, 남은 시험 socket 0개를 확인했다. 실제 API·키 로드·push·PR0이다.

CLI는 새 카탈로그 전용 개발 DB를 지정해 `python -m examples.order_fulfillment.b_production_task --database-url <새 격리 DB URL> --schema <새 카탈로그 schema> --version 1`로 실행한다. DB 기본값/환경 fallback이나 B 자동 저장·승인은 없다. 원문 로그·시작 hash·보존 대조·종료 증거는 Git 제외 `artifacts/local/order_fulfillment/b_cq03_connection_20261030_olgzdm1i/`에 보관한다.

무료 대역 성공과 실제 LLM 성공은 별개다. 실제 provider·공통 CostPolicy·출력 전체 1,024토큰·요청당 30초·이번 실행 승인·별도 한국어 의미 검토는 후속 연결에서 확인해야 한다. 현재 출력은 2,965바이트지만 토큰 적합성을 검증한 것은 아니다. 다음은 CQ04 납기 연결이며 아직 진행하지 않았다. main 모델 원복·버전 정리·나머지 역할 전환도 남아 있다.

## 16. CQ04 납기 공통 연결 — 수정 전 계획 (2026-10-30)

이번 승인 범위는 CQ04 정상·혼합 납기 Agent 연결이다. 기존 `delivery_forecast`와 두 fixture/golden을 재사용하며 공통 코드·`model.py`·기존 CQ01~03·계산을 수정하지 않는다. 시작 파일 106,417개의 hash와 문서 원문은 새 `/private/tmp/b-cq04-u61r5pgc`에 보존했다.

1. 수정 전 기존 납기 계산·입력·CQ01~03 검사를 무료 실행한다. 카탈로그 + 계약 + normal 또는 mixed 납기 fixture만 조립하고 서로 다른 업무 snapshot을 섞지 않는다.
2. 새 `examples/order_fulfillment/b_delivery_task.py`에 Spec/Task·결과 검증·무료 provider/CLI를 둔다. B 입력 helper를 재사용하고 공통 제품/개정/구성 lookup만 요청한다. 핵심 수량·기한·시각·출고/준비/운송 조건·가정/경고 코드·미확인·출처는 출력에 고정한다. 전체 breakdown과 가정/경고 원문은 Task의 `calculation_details`에 고정해 원래 계산과 대조하며 모델에게 출력 복사를 요구하지 않는다. 전체 출력 1,024토큰 적합성은 아직 검증했다고 주장하지 않는다.
3. 새 `tests/order_fulfillment/test_b_delivery_task.py`에서 normal 960/40, mixed 1000/0, 기한/운송/일괄 출고 변화, 미확인 전체 합계 null·확인된 0의 차이, 과거 기한 실제 도착, 대상/수량/시간/조건/가정/출처 변조·권한/도구 거부·순차 4요청을 검사한다. `actual_arrival_assessed`를 항상 false로 바꾸지 않으며 생산·도착 사건을 새로 만들지 않는다.
4. 새 PostgreSQL cluster와 테스트별 별도 schema에서 CQ01~04 연결, 별도 프로세스의 hash 검증한 main 원본 모델, 관련 회귀·CLI·최종 discover를 무료 실행한다. 기존 DB/환경파일·외부 통신을 차단하고 전체 검사의 새 시험 endpoint만 허용한다. Docker 환경 부재로 실제 Docker 6개는 미검증 항목을 유지한다.
5. 새 서버 종료/PID/socket 부재, 파일 hash·HEAD/staging 보존, 정적 검사를 확인한다. 수정 파일은 새 B 예제·새 검사·이 문서·B 상태 4개뿐이다. 유료 API/키 로드/원격 push/PR0이다. main 원복·버전 정리·기존 그래프/노트북·전체 B 저장/승인·CQ05 이후는 진행하지 않는다.

## 17. CQ04 정상·혼합 납기 공통 연결 결과 (2026-10-30)

16절 계획에 따라 새 `examples/order_fulfillment/b_delivery_task.py`와 `tests/order_fulfillment/test_b_delivery_task.py`, 이 설계서와 B `PROJECT_STATUS.md`만 추가/수정했다. 공통 코드·`model.py`·기존 CQ01~03·계산·그래프·노트북·실행 결과는 변경하지 않았다.

기존 `delivery_forecast`를 그대로 호출해 입력을 고정하고, `AgentSpec → AgentRegistration.from_spec → request.run_task`와 공통 신원·실행 한도·실제 제품/개정/구성 `lookup`을 재사용한다. B 계약·생산·운송은 미등록 가상 입력이며 카탈로그 조회의 실제 EvidenceRef와 분리한다. 공통 DB에 B 그래프를 저장하거나 업무 승인을 추가하지 않는다. 수량·조건·시각·가정/경고·미확인·입력 출처를 바꾼 응답과 누락/다른 버전의 제품 조회를 보류한다.

| 사례 | 기한 내 예상 | 부족 | 전량 도착 예상 | Task 크기 / 출처 / locator |
| --- | --- | --- | --- | --- |
| 정상 | 960개 | 40개 | `2026-10-27T15:26:40+09:00` | 6,942바이트 / 2개 / 10개 |
| 혼합 | 1,000개 | 0개 | `2026-10-27T14:20:00+09:00` | 9,198바이트 / 2개 / 15개 |

두 사례 모두 기준 시각 10:00·약속 기한 15:00, 준비 0초·운송 3,600초·준비 물량별 출고의 조건부 계산이다. 상세 배정·가정/경고 원문은 고정 Task의 `calculation_details`에서 보존하고 답변에는 핵심 평가와 출처를 반환한다. 정상/혼합 출력은 각각 UTF-8 2,287바이트다. 바이트 크기는 전체 출력 1,024토큰 적합성을 증명하지 않는다.

기한/운송/일괄 출고 변화, 조건·관측·coverage 누락과 증가율 0, 도착 예정 시각 경과의 미확인을 검사했다. 미확인 전체 합계는 `null`로 유지하며 확인된 무배정의 0과 구분한다. 과거 기한은 확인된 도착 사건 100개·부족 900개와 `actual_arrival_assessed=true`를 유지하고 현재 생산량을 역산하지 않는다. 기존 계산기는 이때도 `assessment_scope=conditional_arrival_forecast`를 유지한다. 첫 새 검사에서 다른 scope 이름을 기대한 1개 실패가 발생해 원래 계약에 맞춰 새 테스트만 수정했으며 최초 실패 로그도 보존했다. 계산·공통 코드는 고치지 않았다.

| 무료 검증 | 결과 |
| --- | --- |
| 수정 전 납기·입력·CQ01~03 | 76개 통과 |
| CQ01~04 연결 (새 CQ04 12개 포함) | 46개 통과, 오류/제외 0 |
| 별도 프로세스의 hash 검증한 main 원본 모델로 같은 연결 | 46개 통과, 오류/제외 0 |
| 관련 B·공통 확장 회귀 | 367개 중 366개 통과·기존 버전 실패 1개, 오류/제외 0 |
| 전체 `unittest discover` | 985개 중 976개 통과·기존 버전 실패 3개·Docker opt-in 6개 제외, 오류 0 (186.861초) |
| 정상·혼합 실제 CLI | 둘 다 성공, 각각 모델 대역 2회·도구 4회·repair 0 |
| 정적/읽기 전용 코드 검토 | 새 Python 2개 Python 3.10 구문·메모리 compile·diff 공백 통과, 추가 차단 사항 없음 |

기존 실패 3개는 typed CQ01(`test_b_typed_workflow.py:47`), 납기 schema(`test_delivery_validation.py:170`), 생산 schema(`test_production_validation.py:256`)의 `graph-0.1` 기대/현재 모델 `graph-0.5` 차이다. 이번 단계에서는 모델이나 해당 기대값을 바꾸지 않았다. 전체 통과로 표현하지 않는다. ruff/mypy/pyright는 설치되어 있지 않으며 의존성을 추가하지 않았다.

README의 PostgreSQL용 Compose와 파일 파싱용 Docker 격리 실행은 용도가 다르다. 현재 컴퓨터에서 Docker 명령·앱·socket을 찾지 못했다. 이 CQ04의 고정 입력/계산/제품 조회는 Docker 없이 새 PostgreSQL로 검증했고, 실제 Docker 4개·Docker 접수 통합 2개는 미검증이다. 설정된 controller 실패 시 비격리 파서로 우회하지 않는 공통 정책을 유지한다.

새 `/private/tmp/b-cq04-u61r5pgc`의 Unix socket 전용 cluster와 테스트별 schema만 사용했다. 기존 환경파일·DB·외부 통신을 차단하고 전체 검사에서 새 시험 endpoint만 허용했으며 위반 0이다. CLI schema 두 개 정리 후 업무 schema 0개를 확인했다. 서버 종료 후 `pg_ctl status` 종료 코드 3, PID/DB socket 부재와 남은 시험 socket 0개를 확인했다. 시작 파일 106,417개를 hash 대조해 문서 2개 외 기존 내용이 그대로인지 확인하고 HEAD/staging도 유지한다. 원문 로그·보존/종료 증거는 Git 제외 `artifacts/local/order_fulfillment/b_cq04_connection_20261030_u61r5pgc/`에 둔다.

CLI 실행은 새 카탈로그 전용 개발 DB를 준비한 뒤 `python -m examples.order_fulfillment.b_delivery_task --database-url <새 격리 DB URL> --schema <새 카탈로그 schema> --version 1 --case normal`을 사용한다. 혼합은 `--case mixed`다. 환경 fallback·유료 API·B 자동 저장·승인은 없다. 이번 실제 유료 API/키 로드/push/PR은 0이다.

무료 대역 성공은 실제 LLM 성공과 별개다. 실제 provider·공통 CostPolicy·출력 전체 1,024토큰·요청당 30초·이번 실행 승인·별도 한국어 의미 검토는 후속 단계다. 다음은 CQ05 대응 Agent 연결이며 아직 시작하지 않았다. main 모델 원복·버전 정리·CQ06도 남아 있다.

## 18. CQ05 대응 공통 연결 — 수정 전 계획 (2026-10-30)

이번 범위는 CQ05 정상·혼합 대응 Agent의 공통 연결이다. 기존 `response_plan`과 `delivery_forecast`를 그대로 재사용하고 기존 CQ01~04·공통 코드·`model.py`는 수정하지 않는다. 새 `/private/tmp/b-cq05-xkxsppys`에 시작 파일 hash·문서 원문을 보존한다.

1. 수정 전 대응/납기/입력·CQ01~04 검사로 기존 동작을 확인한다. 정상·혼합 독립 golden, 혼합 기존 도착100·운송150 제외와 새 출고750(현재450+미래300)을 보호한다.
2. 새 `examples/order_fulfillment/b_response_task.py`에 Spec/Task·공통 등록/run_task·검증·무료 provider/CLI를 둔다. 실제 제품/개정/구성 lookup만 허용하고 B 자료는 미등록 가상 입력으로 유지한다. 기존 B helper로 전체 payload·원문 hash/locator와 실제 카탈로그 조회를 대조한다.
3. 핵심 forecast·새 출고 수량·물류 미확인·가정/경고·상태·초안/검토/미실행 flags를 출력에 고정한다. 상세 배정·기존 고객 회신·actions/confirmations 원문은 고정 `review_material`과 그 hash로 보존한다. CLI는 기존 Python 검토 초안을 별도 `review_material`에, Agent 설명은 `result.payload.answer_ko`에 표시한다. 고정 원문 초안을 새 모델 작성물로 표시하지 않는다. 출력 1,024토큰 검증은 실제 provider 후속 단계다.
4. 새 `tests/order_fulfillment/test_b_response_task.py`에서 정상/혼합·기한/일괄 출고·미확인 null/확인된0·과거+미확인 우선순위·계획 출발/실제 출발 구분을 검사한다. 수량/시각/flags/조건/출처/물류정보 변조·조회 누락·권한 거부·쓰기 도구 차단·순차4요청과 업무 저장 미발생을 확인한다.
5. 새 cluster/테스트별 schema로 연결·main 원본 모델 별도 프로세스·확장 회귀·전체 discover·정상/혼합 CLI를 무료 검증한다. 기존 DB/환경파일·외부 통신은 차단하고 전체 검사에서 새 시험 endpoint만 허용한다. 기존 버전 실패3개/Docker 미검증6개를 구분한다. schema 정리와 서버 종료, 시작 파일 hash/HEAD/staging 보존을 확인한다.

추가/수정은 새 B 예제·새 테스트·이 설계서·B 상태 4개다. 유료 API/키 로드/push/PR0이며 main 모델 원복·버전 정리·기존 그래프/노트북·공통 저장/승인·CQ06은 진행하지 않는다.

## 19. CQ05 정상·혼합 대응 공통 연결 결과 (2026-10-30)

18절 계획에 따라 새 `examples/order_fulfillment/b_response_task.py`와 `tests/order_fulfillment/test_b_response_task.py`, 이 설계서와 B `PROJECT_STATUS.md`만 추가/수정했다. 기존 `response_plan`·`delivery_forecast`·CQ01~04·공통 코드·`model.py`·원본 데이터·그래프·노트북·기존 실행 결과는 변경하지 않았다.

기존 계산을 고정한 Task를 `AgentSpec → AgentRegistration.from_spec → request.run_task`로 연결한다. 공통 신원·누적 한도·실제 제품/개정/구성 `lookup`을 재사용하며 B 업무 노드·초안·검토/승인 이력을 공통 DB에 생성하지 않는다. 전체 입력과 출처 hash/locator, 별도 `review_material` hash를 유지하고 수량·시각·상태·조건·가정·물류 미확인·출처 변조를 보류한다. 카탈로그의 실제 EvidenceRef와 미등록 B 가상 자료는 구분한다.

| 출고 검토 수량 | 정상 | 혼합 |
| --- | --- | --- |
| 이미 도착 / 실제 운송 중 | 0 / 0 | 100 / 150 |
| 새 출고 검토 | 1,000 | 750 |
| 현재 확보 / 미래 생산 예상 | 600 / 400 | 450 / 300 |
| 새 출고 중 기한 내 / 기한 후 | 960 / 40 | 750 / 0 |

혼합의 기존 도착·운송 250개는 새 출고에서 제외한다. 혼합 기한14시의 전체 기한 내970과 새 출고 중720도 구분한다. 일괄 출고 조건·계획 출발/실제 출발 차이를 유지한다. 미확인 합계는 null이고 확인된 무배정의 새 출고0과 다르다. 과거 기한은 coverage 부족보다 `historical_review`가 우선하며 당시 부족을 현재 재출고로 만들지 않는다. 정상·혼합·met도 `draft_only/review_required=true`, `execution_performed=false`다.

상세 신청 항목·고객 회신·actions/confirmations·예측 상세 원문은 고정 `review_material`에 보존한다. CLI의 `review_material.kind=deterministic_input_draft`는 기존 Python 검토 초안이고, `result.payload.answer_ko`는 별도 Agent 설명이다. 원문 초안을 모델이 새로 작성했다고 표시하지 않으며 모델 출력에 반복 복사하지 않는다. 실제 발송·출고·승인·저장·일정 합의를 수행하지 않는다.

읽기 전용 검토에서 기존 설명 검사의 누락을 발견해 B 검증기에 가상/초안/미발송/미등록 표기와 명백한 실행·보증·반대 출고 방식 주장 거부를 유지했다. 해당 문장 변조 회귀와 부정문 허용을 검사했다. 조회 누락 후보 문장도 표기 검사를 만족하도록 보강해 조회 검사 자체를 증명했다. 전체 한국어 의미 검토를 대신하는 검사는 아니다. 보완 전 중간 검증 로그는 최종 로그와 구분해 보존한다.

| 무료 검증 | 결과 |
| --- | --- |
| 수정 전 대응/납기/입력·CQ01~04 | 105개 통과 |
| CQ01~05 연결 (새 CQ05 14개 포함) | 60개 통과, 오류/제외 0 |
| 별도 프로세스의 hash 검증한 main 원본 모델로 같은 연결 | 60개 통과, 오류/제외 0 |
| 최종 관련 B·공통 확장 회귀 | 381개 중 380개 통과·기존 버전 실패 1개, 오류/제외 0 |
| 최종 전체 `unittest discover` | 999개 중 990개 통과·기존 버전 실패 3개·Docker opt-in 6개 제외, 오류 0 (194.059초) |
| 정상·혼합 실제 CLI | 둘 다 성공, 각각 모델 대역 2회·도구 4회·repair 0, 초안 hash 일치 |
| 정적/코드 검토 | 새 Python 2개 Python 3.10 구문·메모리 compile·diff 공백 통과, 지적 보완 후 추가 구현 차단 사항 없음 |

전체 실패는 기존 typed CQ01(`test_b_typed_workflow.py:47`), 납기 schema(`test_delivery_validation.py:170`), 생산 schema(`test_production_validation.py:256`)의 `graph-0.1` 기대/현재 모델 `graph-0.5` 차이 3개다. 모델·기대값을 바꾸지 않았고 전체 통과로 표현하지 않는다. Docker 실행 환경은 여전히 없으며 실제 Docker4개·접수 통합2개는 미검증이다. ruff/mypy/pyright는 미설치이고 의존성을 추가하지 않았다. 최종 전체 실행 시작 뒤 구현 변경은 없으며, 조회 누락 시험의 문구 보강 후 연결/main 60개씩을 다시 확인했다.

정상/혼합 Task는 UTF-8 12,907/15,970바이트, 출처 문서는 각각2개·locator10/15개다. 모델 출력은 3,183/3,191바이트이며 전체 출력1,024토큰 적합성을 검증한 수치는 아니다. 실제 provider·공통 CostPolicy·요청당30초·이번 실행 승인·별도 한국어 의미 검토는 후속 단계로 남긴다. 무료 대역 성공을 실제 LLM 성공으로 표현하지 않는다.

새 `/private/tmp/b-cq05-xkxsppys` cluster와 테스트별 schema만 사용했다. 기존 DB·환경파일·외부 통신을 차단하고 전체 검사의 새 시험 endpoint만 허용했으며 위반0이다. CLI schema2개 정리 후 업무 schema0개, 서버 종료 뒤 status종료코드3·PID/DB socket 부재·남은 시험 socket0개를 확인했다. 시작 파일106,449개를 대조해 문서2개 외 기존 내용과 HEAD/staging을 보존한다. 증거는 Git 제외 `artifacts/local/order_fulfillment/b_cq05_connection_20261030_xkxsppys/`에 둔다. 실제 유료 API·키 로드·push·PR0이다.

CLI는 새 카탈로그 전용 DB를 준비하고 `python -m examples.order_fulfillment.b_response_task --database-url <새 격리 DB URL> --schema <새 카탈로그 schema> --version 1 --case normal`로 실행한다. 혼합은 `--case mixed`다. 환경 fallback이나 B 자동 저장/승인은 없다. 다음은 CQ06 변경 전후 비교 Agent 연결이며 아직 시작하지 않았다. main 모델 원복·버전 정리도 미진행이다.

## 20. CQ06 입력 변경 전후 비교 공통 연결 — 수정 전 계획 (2026-10-30)

이번 범위는 CQ06 불량 증가/운송2시간 대표의 공통 Agent 연결이다. 기존 `compare_input_response_plan`·독립 사례 loader·Python `build_explanation`을 재사용하고 승인 DB 버전 비교/저장 경로는 사용하지 않는다. 시작 파일과 문서 원문은 새 `/private/tmp/b-cq06-ih9f5za7`에 보존한다.

1. 수정 전 CQ06 계산/입력 비교·CQ01~05 연결을 무료 검사한다. 다섯 독립 golden을 보호하며 대표는 defects11·transport2h다. continued11/defects11의 잔여 기간 감소와 절대 완료 동일/지연을 구분한다.
2. 새 `examples/order_fulfillment/b_comparison_task.py`에 Spec/Task·공통 등록/run_task·결과 대조·무료 provider/CLI를 둔다. before/after의 opaque revision·실제 입력 hash·기준 시각을 고정하고 공통 data_version은 제품 조회 카탈로그 버전으로만 사용한다. 두 쪽의 카탈로그 노드가 같을 때만 실제 lookup3회와 최종 응답을 허용한다.
3. 양쪽 핵심 값과 후−전 차이를 출력에, 전체 비교·기존 회신/신청/변경 근거·Python reasons/basic_text는 hash가 고정된 `review_material`에 보존한다. 출처/hash/locator는 before/after별로 유지하고 같은 ID라도 쪽 사이에서 합치지 않는다. 기존 Python 기본 문안을 전달하는 답변임을 표시하며 새 모델 작성물로 표현하지 않는다. 전체 출력1,024토큰 적합성은 후속 provider 단계다.
4. 새 `tests/order_fulfillment/test_b_comparison_task.py`에서 다섯 golden·독립 로딩·null·같은 revision의 다른 내용/시각·역전 시각·다른 약속/계획 거부, 양쪽 출처/수치/차이/flags/문안 변조, 실제 조회 누락/불일치·권한/쓰기 거부·순차4요청·업무 저장/승인 미발생을 검사한다.
5. 새 cluster/schema에서 연결·main 원본 모델 별도 프로세스·관련 회귀·전체 discover·대표 두 CLI를 무료 검증한다. 기존 DB/환경·외부 통신을 차단하고 전체 검사의 새 시험 endpoint만 허용한다. 기존 버전 실패3개/Docker 미검증6개를 구분하며 schema/서버 종료와 hash·HEAD/staging 보존을 확인한다.

변경은 새 B 예제·새 검사·이 설계서·B 상태 4개다. 공통 코드·model.py·기존 CQ01~05·계산/문안·원본·그래프/노트북은 수정하지 않는다. 유료 API/키 로드/push/PR0이며 main 원복·버전 정리·실제 provider·공통 저장/승인 연결은 진행하지 않는다.

## 21. CQ06 입력 변경 전후 비교 연결 결과 (2026-10-30)

`b_comparison_task.py`에서 기존 B 입력 비교 → AgentSpec/Task → 공통 등록/run_task → 실제 제품/개정/구성 lookup3회 → 한국어 검토 문안·구조화 결과 대조를 연결했다. 공통 코드와 기존 계산·문안은 바꾸지 않았다. 승인 DB 버전 비교가 아니라 **양쪽 미등록 입력 revision·hash·as_of 비교**다. 바깥 `data_version`은 공통 카탈로그 버전이며 입력 revision이나 schema 버전을 덮어쓰지 않는다.

| 변경 파일 | 책임 |
| --- | --- |
| `examples/order_fulfillment/b_comparison_task.py` | 새 B 계약·입력 투영·양쪽 출처·hash pin·결과 대조·읽기 전용 공통 연결·무료 provider/CLI |
| `tests/order_fulfillment/test_b_comparison_task.py` | 새15개: 독립 golden·입력 불변·시간 의미·미확인·revision/time/scope·양쪽 출처/locator·카탈로그 차이·변조/누락 조회·권한/쓰기·순차4요청 |
| 이 문서, B `PROJECT_STATUS.md` | 수정 전 계획·검증·보존·한계·다음 단계 |

불량 증가 `defects11`은 기한 내920/부족80, 운송2시간 `transport2h`는870/130이다. 다섯 독립 사례를 기존 손계산 정답과 대조했다. 불량 증가의 잔여 기간은−1,600초지만 절대 완료·전량 도착 예상은+2,000초다. 계속 생산 사례는 잔여 기간−3,600초·절대 완료 변화0이다. 누계 양품과 최근 속도, 미확인 null과 확인된0을 구분한다. 각 사례는 같은 baseline에서 독립 구성한다.

전체 비교·전후 상세 초안·변경 입력·선택/무결성 근거와 기존 Python `reasons/basic_text`를 `review_material`에 보존하고 hash를 고정했다. before/after의 출처/hash/locator를 별도 입력 필드에 둔다. 공통 제품 근거는 실제 조회 결과와 완전히 같은 경우만 인정하고 미등록 B 근거를 확정 `evidence_refs`에 섞지 않는다. 두 입력의 제품 노드가 근거까지 동일하지 않으면 호출 전에 거부한다. 답변은 기존 Python 기본 문안을 정확히 전달한다. 새 LLM이 작성한 설명이나 사람 의미 검토 통과로 표시하지 않는다.

| 무료 검사 | 최종 결과 |
| --- | --- |
| 수정 전 CQ06 계산/입력 비교·CQ01~05 연결 |106/106통과 |
| CQ01~06 공통 연결(새 CQ06 15개 포함) |75/75통과 |
| 별도 프로세스의 hash 고정 main `ca7c391` 원본 모델 |75/75통과; 실제 model.py 교체 없음 |
| 관련 B·공통 계약 회귀 |396개 중395통과·기존 버전 실패1·오류0 |
| 전체 discover |1,014개 중1,005통과·기존 버전 실패3·Docker6제외·오류0(195.166초) |
| 불량/운송 대표 CLI |각 새 schema에서 성공; 대역 모델2회·도구4회·repair0 |
| 요청 상한·실패 차단 |lookup3회 순차 호출+최종 응답4요청 이내; 권한·도구·결과 실패 후 자동 수정/재시도 없음 |
| 정적 검사 |Python3.10 AST·메모리 compile·의존 방향·`git diff --check`통과; ruff/mypy/pyright 미설치 |
| 보호·정리 |실제 API0·guard 위반0·schema정리 후0·서버 종료·PID/DB/시험 socket부재 |

전체의 실패3개는 기존 typed CQ01/납기 schema/생산 schema의 `graph-0.1` 기대와 현재 `graph-0.5` 차이다. 기대값만 변경하지 않았다. 최초 새 검사에서 호출 횟수/출처 집합 단언과 변수 위치 오류를 보완했으며 이전 실패/오류 로그도 최종 성공과 분리 보존했다. 독립 읽기 전용 검토에서 필수 수정급 결함은 발견하지 못했다. Docker 명령·앱·socket이 없어 실제 Docker6개는 미검증이다.

새 `/private/tmp/b-cq06-ih9f5za7` cluster만 사용했다(TCP 미노출). 테스트는 매번 새 schema, CLI 두 대표는 별도 카탈로그 schema다. 기존 환경 파일 로드·기존 DB 연결·외부 통신을 차단하고 전체 회귀의 새 시험 endpoint만 허용했다. 과거 DB/실행/controller를 재사용하거나 이전 유료 실행을 재개하지 않았다. 카탈로그 확정 버전1·초안0·승인 이벤트0·B 업무 타입 저장 없음은 새 연결 검사에서 확인했다(기존 승인 기능 회귀는 별도 새 시험 schema에서 실행).

시작 파일106,492개 hash·HEAD/staging을 대조한다. 변경은 위4개뿐이며 기존 공통 코드·model.py·루트 상태·CQ01~05·계산/문안·그래프·노트북·실제 성공/실패 산출물을 보존한다. 원문은 Git 제외 `artifacts/local/order_fulfillment/b_cq06_connection_20261030_ih9f5za7/`의 `report.json`·검사/CLI 로그·`preservation.json`·`cleanup.json`·`shutdown.json`에 둔다. 강제 추가·commit·push·PR은 하지 않았다.

CLI는 새 카탈로그 전용 개발 schema를 준비한 후 `python -m examples.order_fulfillment.b_comparison_task --database-url <새 격리 DB URL> --schema <새 카탈로그 schema> --version 1 --case defects11`로 실행한다. 운송 대표는 `--case transport2h`다. 명시한 DB 외 환경 fallback과 B 업무 자동 저장/승인은 없다.

이번 단계는 CQ06 무료 연결까지다. main 모델 원복·버전 정리, 실제 provider/공통 CostPolicy 결합·출력 전체1,024토큰·요청당30초·이번 실행 승인·별도 한국어 의미 검토는 후속 단계다. 대표 출력은 약5.5KB지만 토큰 적합성은 검증하지 않았으며 제한을 완화하지 않는다. 무료 대역 성공은 실제 LLM 성공이 아니다.

<details>
<summary>과거 설계 보관 — 아래 내용은 현재 구현 지침이 아님</summary>

이하 원문은 과거 판단과 검증 이력 보존용이다. “최신 사용자 결정”, “권장안”, “미완료” 등 시점 표현은 당시 상태이며 현재 단계의 결정을 대체하지 않는다.

# B/main 연결 설계안 — 단계 3

버전: `0.1-design`, 작성일: 2026-10-29. 기준: `feature/b-first-mvp`, HEAD `f264955`, main `ca7c391`과 앞선 미커밋 보완을 포함한 작업 트리.

**이 문서는 설계 제안이다. 이번 단계에서 코드·노트북 수정, 테스트·DB·서버·API 실행은 하지 않는다.** 단계 4 구현이나 실제 호출 승인으로 사용할 수 없다. [단계 2 조사](B_MAIN_CONNECTION_AUDIT.md)와 사용자가 제공한 Discord 스크린샷, [README](../../README.md#동료의-확장-agent-연결), [AGENT_RUNTIME](../AGENT_RUNTIME.md#확장-agent의-수정-경계)를 근거로 작성했다.

**최신 사용자 결정: B가 변경했던 그래프 버전 행만 main의 `graph-0.1`로 돌리고, B 스키마 확장은 보존한다.** `PinnedContext` 등 main 공통 실행 코드는 수정하지 않는다. 수정 전 계획과 검증 결과는 아래 **12절**을 따른다. 11절의 graph-0.5 유지 및 공통 코드 수정 후보는 이번 요청으로 대체된 과거 계획이다. CQ01 접수의 typed 연결 및 후속 실패 보완은 이미 별도 보고서의 범위에서 구현됐고, 나머지 여섯 역할과 전체 실행 진입점은 미완료다.

## 1. 결정과 범위

**권장안: 기존 B 업무·도구·평가기를 유지하는 얇은 typed 어댑터를 만든다. CQ01 접수는 현 계약 안에서 먼저 연결하고, 나머지 6역할은 공통 도구 등록·근거·조건 계약 검토 뒤 연결한다.** 공통 담당자에게 보낼 내용은 이 문서에만 준비하며 실제 메시지를 보내지 않는다.

- 화면은 B 노트북과 기존 보호 CLI다. 기본 네 역할이나 공통 웹 라우팅을 이번 범위에 추가하지 않는다.
- 기존 7역할, 고정 10대표, 기존 B 계산 함수와 독립 정답을 재사용한다. 새 Agent, 프레임워크, 의존성, 비용 장부를 추가하지 않는다.
- 실제 호출은 향후 사용자가 새 무료 게이트를 확인하고 명시적으로 승인한 뒤 직접 시연한다. 현재 실제 호출 허용 수는 0이다.
- 기존 3노트북의 사용자 편집·출력은 보존한다. UI 작업은 별도 간소화 사본과 B helper에 한정한다.
- 루트 PROJECT_STATUS.md·공통 계약·schema는 이번 설계 단계에서 수정하지 않는다. 앞선 미커밋 수정과 과거 성공/실패 기록을 되돌리거나 새 성공으로 재분류하지 않는다.

### TaskContract

| 계약 | 설계 기준 |
| --- | --- |
| 사용자·목적 | 시연자가 고정 가상 사례를 입력해 새 LLM 응답과 실제 도구 실행 근거를 확인 |
| 입력 | 고정 대표 ID, 업무 대상, 명시한 확정 데이터 버전·조회 시각, 원본·소스 hash. 신원·권한·승인은 호스트 소유 |
| 출력 | 원본 모델 응답과 hash, 공통 AgentResult, 기존 업무 자동 검사, 별도 한국어 의미 검토, 종료 기록. Python 문안은 별도 표시 |
| 허용 변경 | CQ01의 미승인 초안 준비. 사람이 정확 초안을 별도 승인한 뒤 공통 workflow에서만 확정 반영 |
| 금지 변경 | Agent 승인·발송·실제 출하·약속 변경, 이전 실행 재개, 실패 자동 재실행, 오래된 결과 fallback |
| 호출 정책 | 고정 gpt-5.4-mini-2026-03-17, 대표당 최대 4/전체 최대 40, 출력 1024, 호출 timeout 최대 30초, 자동 재시도·형식 repair 모두 0 |
| 진행 조건 | 현재 대표의 실제 응답·자동 검사·별도 의미 검토 모두 통과. CQ01 접수는 별도 업무 승인·반영도 완료해야 약속 조회로 진행 |
| 중단 조건 | 실패·검토 거절·한도 부족·정책/소스 변경·중단 요청이면 후속 유료 호출 중단. 원문·실패·부분 초안 보존 후 소유 서버 종료 |

### DecisionIntegrityContract

```yaml
schema_version: b-connection-design-1
owner: 사용자(설계 확인), 공통 담당자(공통 계약), B 구현 담당자(로컬 구현·무료 검증)
trigger: main 병합 뒤 기존 B 흐름과 새 typed 확장 계약의 차이 확인
decision: 업무 의미와 기존 제한을 유지하는 B 연결 방식 선택
changed_action: 다음 단계에서 허용할 수정 파일·검증 순서를 결정; 이번 실제 업무 실행 없음
error_costs:
  false_positive: 근거·비용·승인이 미완성인데 실행 준비 완료라고 보고하는 것이 가장 큰 위험
  false_negative: 재사용 가능한 B 구현을 불필요하게 전면 재작성
  delay: 공동 계약 결정을 기다리는 시간
  abstention: 전체 typed 전환 미완료를 명시하고 호출 차단 유지
contract_assessments:
  - {contract: DECISION, status: PASS, scope: 이번 설계 범위와 책임자 명시}
  - {contract: EVIDENCE, status: PASS, scope: 코드·문서 대조에 한함; 새 구현 실행 증거 없음}
  - {contract: METHOD, status: PENDING, scope: 6역할의 도구·두 버전 근거 계약 검토 필요}
  - {contract: SYSTEM, status: PENDING, scope: 구현·무료 회귀 미실행}
  - {contract: AUTHORITY, status: PENDING, scope: 공통 계약 결정 및 향후 실제 실행 승인 없음}
  - {contract: ADOPTION_VALUE, status: PENDING, scope: 새 노트북의 실제 사용자 시연 미완료}
first_broken_contract: METHOD
evidence_state: ANALYSIS_READY
product_state: USE_CASE_READY
claim_ceiling: 설계와 확인한 코드 차이만 보고; 신규 실제 LLM 성공·전체 호환 완료·운영 준비 주장 금지
candidate_policy: 모델 응답은 자동 검사와 사람 검토 전까지 검토 대상 후보
next_best_information: 공통 계약 검토와 CQ01 접수의 무료 typed 회귀; 담당자는 공통/B 구현 담당자
invalidation: source/schema/prompt/model/tool/cost/approval 변경 시 관련 게이트는 STALE_REVALIDATION_REQUIRED
loops: 실행·근거 검증 loop는 설계 중; 운영 배포·가치 검증 loop는 이번 범위 밖
```

선택한 구현 형태는 기존의 고정된 LLM 업무 흐름이다. 추가적인 자율 계획·검색·복구·멀티 Agent 도입 근거는 없다. 이번 설계 자체에는 A0 실제 시연 증거도 새로 생기지 않았다. 과거 실행 증거의 범위를 이 설계에 자동 승계하지 않는다.

## 2. 공통 계약 안의 대안 비교

| 대안 | 코드에서 가능한 점 | 제약과 결정 |
| --- | --- | --- |
| A. 공통 lookup/export_graph → 신뢰된 B 계산 → 모델 설명 | 공통 lookup은 명시 버전의 노드/종류 조회를 지원. 기존 B 계산 함수도 노드 목록을 받음 | 기술적 후보다. 다만 현재 6역할의 provider·evaluate는 B 계산 도구 호출과 정확한 단계/근거 조회를 요구한다. 호스트가 계산을 먼저 주입하면 시연 trace와 검증 계약이 달라진다. 넓은 그래프 전달의 바이트/권한 범위, 4요청 내 근거 확보도 미검증. 즉시 교체안으로 채택하지 않음 |
| B. 기존 B 도구 유지 + 고유 Spec/어댑터 + 공통 계약 검토 | 기존 지침·계산·4단계·평가기·실제 성공 증거의 의미를 가장 적게 바꿈 | 권장. CQ01 접수 먼저 구현 가능. 나머지 역할은 공통 계약 결정 전 등록/전체 완료 처리 불가 |
| C. Spec 검증 subclass·공통 도구 이름으로 B 기능 위장 | 공통 검사를 피할 수 있음 | 제외. 권한·도메인·근거 계약을 우회함 |
| D. CQ06 버전별 별도 LLM task 후 합성 | 각 단일 버전 task는 공통 조회 조건에 맞출 여지가 있음 | 두 모델 결과와 최종 비교를 합치면 기존 4요청/평가기/시연 의미가 달라질 수 있음. 이번 권장안에서 제외. 한도를 늘리지 않음 |

A가 원천적으로 불가능하다고 입증한 것은 아니다. B를 권장하는 이유는 기존 업무 계약과 검증기를 보존하는 변경량이 작기 때문이다. 공통 담당자가 새 도구 연결을 채택하지 않으면 A의 범위·trace·근거·한도를 별도 설계해 사용자와 다시 결정한다. 유료 경로로 A/B를 실험하지 않는다.

제거 실험은 향후 무료 대역에서 typed 어댑터 전후 동일 입력의 계산 결과·미확인·초안·검토 상태를 대조하는 것으로 잡는다. 어댑터가 업무 값을 바꾸거나 기존 평가를 약화하면 해당 설계를 채택하지 않고 원인을 수정한다. 호환 경로의 무료 성공을 typed 성공으로 대신하지 않는다.

## 3. 어댑터 구조와 역할 계약

신규 코드는 B 전용 `examples/order_fulfillment/b_task_agent.py` 한 모듈에서 시작한다(아직 생성하지 않음). 기존 예제 모듈을 import하는 흐름이므로 공통/서비스 패키지가 examples를 역으로 import하지 않는다. 현재 `ontologics/order_fulfillment/contracts.py`는 계약서 추출 모듈이므로 AgentSpec 용도로 덮어쓰지 않는다.

```text
노트북 → 기존 보호 CLI/새 격리 환경 → 기존 fixture/task
  → B 어댑터: 업무 입력 검사 + AgentTask(PinnedContext)
  → 신뢰된 호스트: AgentRegistration.from_spec
  → request.run_task(고정 provider, 명시한 한도)
  → 원본 runtime.text + payload + 확정 evidence_refs
  → 기존 evaluate(runtime 원본 전달) + 별도 의미 검토
  → CQ01 정확 초안 업무 검토/공통 반영 → 다음 대표 또는 종료
```

| 역할 | 고정할 입력 | 재사용할 업무 검증/도구 | 전환 조건 |
| --- | --- | --- | --- |
| b_intake | 원본 source/evidence, 후보 조회 버전, request_key | b_agent 평가 + b_cq01_bounded 7조회→초안→검사→최종답 | 기존 공통 도구로 먼저 연결 |
| b_promise | round ID, 접수 반영 후 확정 버전 | b_agent 평가 + delivery_round/lookup | 도구 계약 검토 뒤 연결 |
| b_fulfillment | round ID, as_of, version | b_fulfillment_agent + fulfillment_status/lookup | 위와 같음 |
| b_production | work ID, round ID, as_of, version | b_production_agent + production_completion/lookup | 위와 같음 |
| b_delivery | round ID, as_of, version | b_delivery_agent + delivery_forecast/lookup_many | 정상·혼합 2대표 모두 검증 |
| b_response | round ID, as_of, version | b_response_agent + response_plan/lookup_many | 정상·혼합, 미발송·미등록 유지 |
| b_comparison | round ID, before/after 버전·시각 | b_comparison_agent + compare_response_plan/lookup_many | 불량/운송 2대표, 양쪽 근거 계약 필요 |

각 역할의 AgentSpec 버전은 최초 `1.0.0`으로 제안한다. 기존 실행 증거에 새 버전을 소급 표기하지 않는다. 등록은 호스트 시작 코드에서만 수행하고 미지원 6역할의 Spec을 import 시 억지 생성하거나 공통 검사를 우회하지 않는다. 계약이 결정되기 전에는 정의 초안만 보관하고 접수 Spec만 생성한다.

- 입력 PayloadSchema는 `case_id:string`, `question:string`, `business_input:object`를 사용한다. 내부 업무 입력은 역할별 정확한 키·타입·대상으로 추가 검사한다. actor/reviewer/approved 등 권한을 입력에서 받지 않는다. request_key는 신뢰된 실행기가 만든 값을 고정하며 모델이 바꾸면 거부한다.
- 출력 PayloadSchema는 `data_version:integer`, `answer:object`를 사용한다. 기존 원본 응답을 JSON으로 읽어 answer에 담는 `decode_result`만 둔다. 기존 모델 응답·한국어·hash는 수정하지 않는다. 원본에 있는 잘못된 버전을 호스트 값으로 덮어 고치지 않는다. CQ06 최상위 data_version은 호스트가 고정한 after 버전이라는 메타데이터이며 모델 주장으로 표시하지 않는다.
- 출력 중첩 필드는 기존 정확 필드·타입·미확인/null·초안 상태 검사를 재사용한다. 공통 PayloadSchema가 object를 인정한다는 이유로 내부 값을 무검증 통과시키지 않는다.
- 최종 validator는 관찰된 성공 도구 응답과 모델 답을 비교하는 순수 검사로 둔다. DB Service를 validator에 주입하지 않는다. 기존 evaluate의 DB·독립 정답·불변성 검사는 실행 후 별도로 유지한다. 공유 가능한 순수 검사만 추출하며 기존 검사 삭제·완화는 하지 않는다.
- validator 입력에 정답 fixture나 새로 계산한 정답을 넣어 모델 응답을 생성·교정하지 않는다. 실패하면 최종 typed 결과를 실패 처리하고 후속 호출을 중단한다.
- AgentResult의 공개 envelope나 EvidenceRef 필드는 바꾸지 않는다. 최상위 evidence_ids로 양쪽 버전 ID를 평탄화하지 않고, 기존 answer의 evidence/evidence_by_side와 공통 `(data_version, evidence_id)` 참조를 검증한다.

**호출 한도 구분:** 현재 ModelConfig.max_tool_calls=8은 모델 응답 한 번의 도구 배치 상한이다. 전체 누적 도구 상한은 공통 ExecutionBudget이 별도로 관리한다. 접수는 7조회+초안1+검사1로 모델 요청 4회/도구 총 9회이며, typed 경로의 호스트 사전 버전 조회 1회도 요청 예산에 포함한다. 새 Spec은 max_model_calls=4, max_repairs=0을 명시하고, 누적 도구 상한은 기존 공통 상한 안에서 실제 전체 trace를 계산해 제한한다. 8을 전체 도구 횟수라고 간주해 필요한 검사를 삭제하지 않는다. 기본 120초 요청 deadline은 유지하고 각 HTTP timeout은 잔여 시간과 30초 중 작은 값이다. 사람 검토는 모델 요청 context 종료 후 기다린다.

## 4. 공동 검토 요청 초안 — 아직 전송하지 않음

공통 담당자가 선택할 구현 API를 B에서 먼저 만들어 고정하지 않는다. 아래는 필요한 동작과 수락 조건이다.

| 번호 | 필요한 계약 지원 | 수락 조건 / 책임 |
| --- | --- | --- |
| R1 | 7개 B 도구를 신뢰된 호스트가 선택적으로 typed 역할에 연결하는 방법 | 기존 공통 allowlist를 전역 확장하는 것이 필수는 아님. 사용자 권한·역할별 최소 도구 교집합 유지, review/apply/seed/index 미노출. 공통 담당자가 확장 접점을 결정 |
| R2 | B 단일/비교 도구의 실제 인자 검사 | 버전, as_of, round/work ID, 조회 kind/ID 집합을 task 및 직전 성공 결과와 대조. 모델의 잘못된 호출을 실행 전에 거부. 읽기 단계 변경도 실패 중단 |
| R3 | B 도구와 CQ06의 확정 근거 수집 | 기존 공통 resolver로 확정 스냅샷의 SourceDocument/Evidence/hash/locator 확인. B에서 private _TaskReads를 복제하거나 합성 refs를 사후 주입하지 않음 |
| R4 | 기존 B 도메인 schema의 공통 수용 범위 | 기존 4539c90의 루트 model.py 확장 합의/버전/회귀 확인. 이번에 schema를 추가 변경하지 않음 |

단계 4 정적 확인에서 R4의 구체적인 버전 차이도 확인했다. 현재 B `model.SCHEMA_VERSION`은 `graph-0.5`지만 공통 `PinnedContext`는 `graph-0.1`만 허용한다. B 어댑터에서 값을 강제로 바꾸면 공통 계약 생성이 거부되며 우회하지 않는다. 접수의 무료 typed 실행 결과에는 두 버전을 함께 표시하고 전체 호환성 미확인으로 남긴다. 공통 담당자의 버전 의미/지원 범위 결정 전에는 CQ01 실행 성공도 도메인 계약 정합성 완료로 확대하지 않는다.

### 2026-10-29 버전 선택 기록 — main 포함 여부 재확인으로 적용 보류

사용자의 “0.1로 가자”에 따라 후속 연결의 기준은 main의 `graph-0.1`로 정한다. 이 선택은 기존 B의 `graph-0.5` 문자열만 `graph-0.1`로 바꾸거나 B 업무 구조를 삭제한다는 뜻으로 적용하지 않는다. 현재 코드의 버전 값과 기존 실제 실행 기록은 이번 결정 기록에서 변경하지 않는다.

**이후 사용자가 B 확장이 main에 이미 포함됐는지 먼저 확인하도록 요청하여 위 선택의 적용과 아래 후속 수정 계획은 보류했다.** 실제 병합의 양쪽 부모를 다시 대조했다. B `4539c90`은 graph-0.5/22개 타입, 병합한 main `ca7c391`은 graph-0.1/12개 타입이며 B 추가 10개 타입은 해당 main에 없다. 병합 결과 `f264955`의 model.py는 기존 B `4539c90`의 파일과 바이트 단위로 동일하다. 따라서 현재 확장은 이번 main에서 새로 받은 것이 아니라 병합 전 B 변경이 보존된 것이다. main 전체 이력에서도 model.py의 변경은 공통 초기 커밋만 확인된다. 팀장님의 다른 브랜치·미반영 작업 포함 여부를 판단한 것은 아니다. 버전 번호 차이만으로 graph-0.5를 낮추거나 전체 호환성 완료로 표시하지 않는다.

로컬 `origin/main`과 현재 `model.py`의 AST를 대조한 결과, 기존 12개 타입의 필수 필드·관계 정의는 동일하고 B에 10개 타입이 추가돼 있다. 추가 타입은 Contract, ContractItem, DeliveryRound, FulfillmentSnapshot, FinishedGoodsStock, ProductionWork, ProductionObservation, Allocation, Shipment, DeliveryCondition이다. 기존 함수 중 `validate_graph`, `graph_jsonld`, `tbox_turtle`도 다르므로 타입 정의가 같다는 사실만으로 전체 호환성을 확정하지 않는다.

후속 수정 전 계획은 다음 순서다.

1. 위 세 함수의 B 추가 검증·직렬화와 공통 동작의 차이를 대조하고 기존 업무 회귀 범위를 고정한다.
2. 공통 `graph-0.1` 계약을 기준으로 B 확장 구조를 연결할 기존 접점부터 확인한다. 기존 확정 저장·근거·권한 경로를 복제하거나 schema 검사를 우회하지 않는다.
3. 기존 접점으로 해결되지 않는 도메인 변경은 R4의 공통 검토 대상으로 구체적인 최소 diff와 함께 남긴다. R1~R3도 별도 수락 조건으로 유지한다.
4. 구현 후 새 격리 환경의 계약·B 업무 회귀를 무료로 확인한다. 기존 출력·실행 기록의 `graph-0.5`는 당시 버전으로 보존하고 새 실제 LLM 성공으로 재분류하지 않는다.

이번 확인은 정적 대조와 문서 기록이다. 업무 실행 테스트·DB·서버·유료 API를 실행하지 않았으며, 공통 `PinnedContext`의 허용 버전 확대나 도메인 연결 완료를 의미하지 않는다.

검토 후보 공통 파일은 `ontologics/contracts.py`, `host.py`, `harness.py`, `service.py`, `tool_specs.py`다. 모두 수정하겠다는 목록이 아니며 공통 담당자가 선택한 최소 변경만 후속 범위로 정한다. 기존 허용 도구·오류·checkpoint·저장 계약과 기본 네 역할의 회귀를 유지해야 한다. R1만 통과해도 R2/R3가 미완성이면 전체 typed 준비 완료로 표시하지 않는다.

## 5. CQ06 두 버전과 근거

- 전체 실행기의 개발 fixture에서 before=v2, after=v3를 각각 별도 작성자/검토자 승인과 반영으로 만든다. fixture 자동 준비는 가상 입력 준비이며 모델 생성 결과의 사람 의미 검토나 운영 승인으로 표시하지 않는다.
- defects11은 10시 기준과 불량 증가가 포함된 11시 관측을 비교한다. transport2h는 같은 10시 기준의 운송 1시간→2시간 변경이다. 두 대표를 차례로 누적 변경하지 않는다.
- 한 AgentTask의 context.data_version=after_version, conditions에 before_version, 두 as_of, round ID를 고정한다. 내부 payload와 context가 다르면 모델 호출 전에 거부한다.
- 기존 4요청 흐름을 유지한다: 비교 도구 1회 → before Evidence 배치 → after Evidence 배치 → 최종 응답. 호스트가 source/hash 체인을 확인하는 일과 모델이 원문 전체를 읽었다는 주장을 구분한다.
- R2에서 비교 인자는 정확한 두 버전/시각만, 각 lookup_many는 해당 side의 직전 성공 응답에서 정해진 Evidence 전체 집합만 허용한다. 같은 Evidence ID가 두 버전에 있어도 각각 확인한다.
- R3에서 비교 응답의 양쪽 확정 근거를 각각 해석해야 한다. 최상위 envelope의 after 버전을 before 자료에 적용하지 않는다. answer의 선택 근거와 도구 계산의 integrity 근거도 구분해 기존 검사를 유지한다.
- 기존 response_comparison의 비교 가능성, 약속·배분 동일성, 후−전 차이, null 보존, 절대 완료 시각/잔여시간 구분을 재사용한다. 미발송·미등록·검토 필요 상태와 Python 기본 문안 복사 제한도 유지한다.

## 6. 비용·사용자 승인 설계

공통 [CostPolicy](../../ontologics/paid_budget.py)와 [begin_paid_call](../../ontologics/harness.py)를 그대로 사용한다. 공통 규칙/요금표를 B가 변경하지 않는다. 운영자가 지정할 실제 가격·금액은 이번에 읽거나 생성하지 않았고 미확정 상태다.

### 설정과 적용 범위

1. 키와 분리된 비밀값 없는 정책 자료에 고정 모델 가격/입력 예약 상한/대표별 상한 P를 제공한다. 공통 `cost_policy_from_environment`의 기존 두 설정 키로 파싱한다. B가 별도 가격 파서·CostLedger를 만들지 않는다.
2. 선택된 대표 수 N(최대 10)에 대해 같은 P를 대표마다 배정하고, 승인 화면에는 `대표별 P USD / 전체 배정 N×P USD`를 표시한다. 사용자 전체 예산 C를 별도로 받으면 N×P≤C만 허용한다. 남은 대표의 상한을 늘리거나 실패 대표의 예산을 이전하지 않는다.
3. 각 대표는 하나의 비용 정책을 가진 호스트 요청에서 최대 4회 호출한다. 하위 역할은 공통 비용 장부를 공유하며 추가 유료 호스트 요청으로 예산을 초기화하지 않는다. 사람 검토 뒤 다음 대표만 다음 배정으로 진행한다. 검토·fixture·승인 호스트는 유료 provider를 호출하지 않는다.
4. 이 구조는 장시간 사람 검토를 기다리기 위해 캠페인 전체에 하나의 ExecutionBudget deadline을 늘리지 않는다. 요청별 공통 정책과 기존 영속 40요청 원장을 함께 사용한다. N×P는 설정 가격과 예약 전제에 따른 보수적 배정 합계이며 실제 청구액·계정 지출의 절대 상한 보장은 아니다.
5. 공통 provider의 입력 예약 기준은 현재 최소 400,000토큰이다. 작은 실제 prompt 길이로 임의 축소하지 않는다. 이는 예약 계산 기준이며 새로 구현한 입력 길이 차단 기능을 뜻하지 않는다. 가격·예약 설정이 이를 만족하지 않거나 남은 P가 다음 예약을 감당하지 못하면 전송 전 중단한다. usage 누락은 예약액 유지, 예약 초과는 기존 공통 오류와 캠페인 중단을 따른다.

### 설정 전달과 승인 결합

```text
비밀값 없는 정책 계획 → 기존 공통 파서/정책 fingerprint
  → 무료 gate(소스·입력·대표·고정 제한·정책 fingerprint·종료 증거 결합)
  → 승인 화면에서 전체 계획 확인 + 사람이 이번 실행 승인 입력
  → 승인 자료/현재 정책/gate 일치 확인
  → 그 뒤에만 기존 허용 창에서 API 키 로드
  → provider settings와 CostPolicy를 별도로 host.request까지 전달
```

- 기존 load_authorized_settings가 키만 남기는 부분을 수정 대상으로 잡는다. provider settings, 공통 CostPolicy, 공개 정책 계획을 분리해 전달한다. API 키를 authorization/settings/report에 기록하지 않는다.
- B 승인 형식의 새 버전을 제안한다. 현재 실행 ID·순서 있는 대표 목록·무료 게이트/소스 hash·모델/endpoint·4/40 제한·1024/30/재시도0에 정책 fingerprint와 P/N×P를 추가 결합한다. 기존 `입력·금액 별도상한 없음` 동의 필드는 새 정책 승인으로 인정하지 않는다.
- 정책은 신뢰된 운영자 설정에서만 오며 모델 답·업무 원문·검토 메모에서 가격이나 권한을 받지 않는다. 승인 파일에 적힌 값으로 신뢰된 정책을 덮어쓰지 않고 현재 정책과 동일한지 검사한다.
- 무료 검사는 실제 정책 자료의 구조·예약 가능성을 무통신으로 검사한다. HTTP 대역에 쓰는 가상 가격은 `synthetic`으로 별도 표시하며 live 승인 자료로 재사용하지 않는다. 실제 정책이 미확정이면 일반 무료 업무 검사는 가능해도 유료 준비 게이트는 통과하지 못한다.
- 현재 고정 live_readiness 차단은 이번 단계에서 유지한다. 후속 구현에서 계약 검토 기록·실제 연결 회귀·정책 준비를 확인한 뒤에만 준비 판정으로 대체한다. 사용자가 값을 넣어 readiness를 강제로 True로 만들 수 없게 한다.

## 7. 보호할 상태 전이와 화면

```text
고정 사례 확인 → 무료 사전검사 → 무료 서버 종료 확인
  → 계약·정책 준비 확인 → 새 실행 승인 대기 → 이번 실행 시작
  → 현재 대표 LLM(최대4) → 자동 검사 → 한국어 의미 검토 대기
  → [CQ01 접수: 정확 초안의 다른 검토자 업무 승인·반영]
  → 다음 대표 … → 전체 결과 + 소유 서버 종료 확인
실패/거절/중단 → 후속 유료 호출 금지 → 원문·부분 결과 보존 → 소유 서버 종료
```

현재 대표·실제/최대 요청 수·상태·다음 행동을 표시한다. 모델 답과 검토할 초안은 검토 입력 바로 위, source_text/Python 문안/자동 검사와 상세 JSON은 구분한다. 현재 선택 사례/정책이 변경되면 gate·승인·시작 토큰을 새로 결합한다. 승인과 검토의 문장·판정을 자동 작성하지 않는다. 기본 셀은 비활성이고 같은 셀/메뉴를 다시 실행해도 기존 실행을 새로 시작하지 않는다. 종료와 부분 실패도 성공 화면으로 대체하지 않는다.

## 8. 후속 수정 파일과 책임 경계

아래는 단계 4/5의 제안이며 이번 단계에서 수정한 파일 목록이 아니다. 필요성이 확인된 파일만 실제 수정한다.

| 파일/영역 | 변경 목적 | 시작 조건 |
| --- | --- | --- |
| examples/order_fulfillment/b_task_agent.py (신규 제안) | 고유 Spec/input/output/messages/decode/순수 결과 검증 | CQ01 접수부터. 6역할은 R1~R3 뒤 활성화 |
| examples/order_fulfillment/b_host_agent.py | typed 등록을 기존 호환 등록과 구분 | 기존 예제 호환 유지 |
| examples/order_fulfillment/b_run_workflow.py | AgentTask/run_task 연결, runtime/AgentResult 동시 보존, CostPolicy 주입 | 접수 선행, 전체 완료는 10대표 전환 후 |
| examples/order_fulfillment/b_agent.py 및 b_*_agent.py | 필요한 순수 검증 재사용 부분만 추출 | 기존 evaluate·정답·단계 검증을 먼저 회귀로 보호 |
| examples/order_fulfillment/b_run_transport.py | 새 승인 버전/정책 계획/키 분리, 준비 판정 | 공통 비용 파서 재사용, 임시 차단 단순 삭제 금지 |
| examples/order_fulfillment/b_run_isolation.py, b_run.py | 공개 정책 전달·gate 결합·새 자원·종료 보호 | 키 읽기 전 실패, 기존 sandbox 보호 유지 |
| examples/order_fulfillment/b_notebook.py, b_notebook_ui.py | 정책 포함 승인 계획·상태·검토 연결 | UI 간소화는 단계 5. 기존 검토 조건 유지 |
| 새 간소화 ipynb (기존 ignore 유지) | helper를 사용하는 별도 시연 사본 | 기존 3노트북 덮어쓰기/강제 Git 추가 금지 |
| tests/order_fulfillment/test_main_task_connection.py 및 test_b_* | 공통 예제 성공과 실제 B typed 성공을 분리, 업무 회귀 보호 | 무료, 새 격리 DB가 필요한 검사는 별도 환경 |
| tests/order_fulfillment/test_b_task_agent.py, test_b_cost_authorization.py (신규 제안) | 실제 B 계약·정책/승인 경계 회귀 | 새 의존성 없이 기존 unittest 패턴 사용 |
| ontologics/order_fulfillment/{host,runtime,openai_provider}.py | 필요할 때만 기존 전달 경계 보완 | 공통 실행 코드 복제 금지 |
| 공통 contracts/host/harness/service/tool_specs, model.py | R1~R4 검토 대상 | B가 선행 수정하지 않음. 공통 담당자가 범위 결정 |
| docs/order_fulfillment/ | 설계·구현·검증·제한 기록 | 루트 PROJECT_STATUS.md 수정 금지 |

## 9. 무료 검증 설계 — 이번에는 실행하지 않음

구현 전에 기존 evaluate·bounded provider·승인 경계 회귀를 먼저 실행하고 기존 동작을 보호한다. 새 검사는 구현 구조를 복제하기보다 허용/거부 결과와 보존 상태를 확인한다.

| 검사 | 통과 기준 |
| --- | --- |
| 실제 B typed 연결 | B Spec/from_spec/AgentTask/run_task, agent-result-1/context/refs/오류 계약 확인. model_reader만 통과한 결과로 대체 금지 |
| 10대표 업무 동등성 | 기존 독립 정답·계산·미확인·초안·도구 단계 검사 유지. 결과가 하나의 연속 업무 상태라는 설명 금지 |
| 인자·권한 공격 | 다른 버전/시각/대상/추가 ID/권한 필드/미등록 도구/신원 변경 거부, 잘못된 batch는 도구 실행 전 거부 |
| CQ06 | 양쪽 버전 Evidence ID·source/hash 대조. 같은 ID의 버전 혼동·한쪽 자료 누락·null→0·약속/배분 변경을 거부 |
| 최종 답 오류 | 숫자/상태/근거/버전 변조 시 실패. 원본 응답 보존, 보정 재호출/후속 대표 호출 0 |
| 비용 예약 | 정책/가격 누락·400000 예약 기준 미달·잔액 부족 시 HTTP 대역 전송 0. 사용량 정상/누락/초과의 공통 정산 유지 |
| 배정 범위 | N/P/전체 배정 일치, 대표별 1유료 요청 context, 자식 장부 공유. 재호스트/재시작으로 예산 초기화 금지 |
| 새 승인 | 미승인/옛 승인/다른 run/source/gate/가격/P/N/모델/대표 순서면 키 읽기·서버·전송 전 차단 가능한 가장 이른 경계에서 거부 |
| 횟수·재시도 | 실제 backend 대역 dispatch 기준 대표5번째/전체41번째 전송 0, 1024/30초/재시도0/repair0 유지. 호스트 사전 DB 조회는 모델 요청 수로 계산하지 않음 |
| 사람 검토 | 모델 답/hash에 결합된 수동 의미 검토, CQ01 정확 draft/version/content_hash·다른 작성자/검토자. 미제출·거절·오래된 검토로 다음 대표 진행 금지 |
| 화면 재실행·실패 | 기본 셀의 키/DB/worker/API 0. 반복 셀/메뉴/중복 프로세스는 추가 캠페인 시작 거부. timeout/오류/중단에서 후속 호출 0 |
| 보존·종료 | 기존 노트북/입력/성공·실패 근거 hash 보존. 새 소유 cluster/schema만 사용, 실행·중단 양쪽에서 소유 PID/socket/pidfile·pg_ctl로 종료 확인 |

향후 실행 순서는 기존 B 경계 회귀 → CQ01 typed/비용 단위 검사 → 공동 계약 결정 후 10대표 무료 대역 → 공통 계약·업무 회귀 → 노트북 기본 커널 → 전체 무료 회귀/예제와 보존·종료 확인이다. 실제 Docker 환경이 없으면 해당 skip을 명시한다. lint/typecheck는 저장소 설정과 설치된 도구를 확인해 수행하고 미설치 검사를 통과로 보고하지 않는다. 어떤 무료 검사도 새 실제 LLM 성공으로 계산하지 않는다.

## 10. 단계 3의 결론과 다음 경계

설계 범위는 노트북 시연, 권장 방식은 기존 B 도구/검증을 보존한 typed 어댑터, 비용은 기존 공통 정책의 대표별 균등 배정이다. 미정인 것은 R1~R4 공통 계약 수용 방식과 운영자가 지정할 실제 가격·금액이다. B 사용자 설계 확인은 공통 계약 검토나 실제 지출 승인을 대신하지 않는다.

사용자와 이 설계안을 확인한 후 단계 4에서 CQ01 접수의 typed 연결·비용/승인 무통신 검증부터 시작할 수 있다. 6역할의 계약이 해결되지 않으면 전체 typed 전환·전체 시연 준비를 완료 처리하지 않고 해당 지점을 남긴다. 단계 5/6이나 실제 LLM 시연은 이번 승인 범위에 포함하지 않는다.

단계 3의 정적 확인: 시작 시점 255파일의 SHA 대조에서 B 작업 기록 외 기존 파일은 동일하고 설계 문서 1개가 추가됐다. 기존 노트북도 대조 대상에 포함했다. 루트 PROJECT_STATUS.md는 HEAD와 바이트 단위로 동일하다. 이 문서의 로컬 링크 5개와 Markdown 코드 블록 짝, git diff --check를 확인했다. 이는 문서 검증이며 업무/계약 테스트 실행 결과가 아니다.

## 11. graph-0.5 유지 후 공통 기반 연결 수정 계획

2026-10-29 사용자 요청에 따른 최신 계획이다. 이번 범위는 코드 조사와 B 문서 갱신이며 구현·업무 테스트·서버·DB·실제 API 호출을 하지 않는다. 실제 비교 대상은 병합 전 B `4539c90`, 병합한 main `ca7c391`, 병합 결과 `f264955`와 현재 미커밋 연결 보완이다. 원격을 새로 가져오거나 다른 브랜치의 작업을 포함했다고 가정하지 않는다.

### 목표와 보존 기준

고정된 10대표의 기존 B 입력·계산·답변 계약·독립 evaluator를 유지하면서 `고유 schema/지침/흐름 → AgentSpec → 호스트 등록 → request.run_task → 계약·업무 검사`에 연결한다. B의 `model.SCHEMA_VERSION=graph-0.5`, 22개 타입, 가상/실제 구분과 원본 근거는 유지한다. 기본 네 Agent·신원·승인·확정 저장·A/B 계산·공유 비용 장부·격리 파서를 복제하지 않는다.

현재 정적 조사로 확인된 첫 미해결 연결 계약은 공통 문맥 버전과 B 도구의 typed 수용 범위다. CQ01 무료 성공은 이 두 계약 및 전체 B 호환성 완료의 근거로 확대하지 않는다. 사용자와 B 구현자가 수정 순서를 확인하고, 공개 계약 접점은 README/AGENT_RUNTIME의 공통 검토 경계에 따라 검토한다. 이 문서는 공통 담당자의 승인이나 실제 지출 승인을 대신하지 않는다.

### 코드로 확인한 차이와 최소 수정 후보

| 순서 / 책임 | 확인한 현재 동작 | 수정 후보와 통과 기준 |
| --- | --- | --- |
| 1. 그래프 버전 / 공통 검토 | `harness.PinnedContext` 기본값과 guard가 graph-0.1 고정이다. B model은 graph-0.5이고 intake도 기본 문맥 값 0.1을 사용한다 | `harness.py`에서 실제 `model.SCHEMA_VERSION`을 기본값·검증의 단일 기준으로 사용하는 최소안을 우선 검토한다. B에서는 0.5, main 단독에서는 0.1을 기록해야 한다. 구버전 문맥을 무조건 함께 허용하거나 기록을 재작성하지 않는다. run_task 진입의 실제 버전 일치와 기존 checkpoint 불일치 거부도 회귀 범위다. context-1은 문맥 형식 버전이므로 graph 번호와 함께 바꾸지 않는다 |
| 2. 도구 등록 / 공통 검토 | `contracts.AgentSpec`과 `host.AgentRegistration`이 공통 AgentGateway.ALLOWED만 검사한다. B subclass도 spec이 있으면 공통 검사를 다시 호출한다 | `contracts.py`·`host.py`의 신뢰된 호스트 등록 접점에서 검토된 B 도구를 역할별로 수용하는 최소 방식을 검토한다. 도구 목록·gateway·조건 검사·근거 처리가 함께 맞아야 하며 전역 ALLOWED 확대, Spec 검사 생략, 모델/요청의 동적 등록은 하지 않는다. 구체적인 registry API는 이 계획에서 미리 확정하지 않는다 |
| 3. 조건 고정·근거 / 공통 검토 + B 계약 | `PinnedContext.validate_call`과 `host._TaskReads`는 기존 공통 도구를 기준으로 한다. B 읽기 및 lookup_many는 확정 근거 확인 목록에 없고 비교 특별 처리는 compare_investigation뿐이다 | 공통 batch 사전 검증과 기존 확정 근거 resolver를 재사용할 확장 접점을 2번과 함께 검토한다. B의 version/as_of/회차/작업/kind/허용 ID를 실행 전에 검사한다. CQ06 before/after를 각각의 확정 버전으로 해석하고 `(version,evidence_id)`를 보존한다. private resolver 복제·합성 근거 사후 주입은 하지 않는다 |
| 4. B typed 연결 / B 수정 | `b_task_agent.py`에는 INTAKE_SPEC 하나만 있고 workflow는 접수만 run_task, 여섯 역할은 run_agent다 | `b_task_agent.py`, `b_host_agent.py`, `b_run_workflow.py`에 역할별 Spec/Task/검증/등록을 연결한다. 기존 지침·bounded provider·계산·evaluator를 재사용한다. promise 하나를 먼저 통과시킨 뒤 fulfillment → production → delivery → response → comparison 순서로 진행한다. legacy 예제 호환은 보존한다 |
| 5. 비용 승인 진입점 / B 수정 | `b_cost_authorization.py`에 새 승인 helper가 있으나 CLI/격리 worker/키 로더/노트북은 옛 승인을 사용한다. isolation에서 campaign으로 cost_plan 전달도 빠져 있다 | `b_run_transport.py`, `b_run_isolation.py`, `b_run.py`, `b_notebook.py`에 공개 정책 → 새 미승인 승인 자료 → 사람 승인 → 현재 정책 재검증 → 키 로드 → 같은 CostPolicy의 요청 전달을 연결한다. helper 자체와 공통 예약/정산/장부를 재사용하며 별도 비용 정책이나 가격을 만들지 않는다 |
| 6. 화면·최종 검증 / B 수정 | 간소화 helper/별도 노트북은 있으나 전체 typed·새 비용 승인과 연결 완료가 아니다 | 위 연결 후 `b_notebook_ui.py`와 별도 간소화 노트북의 상태·안내를 맞춘다. 기존 사용자 편집/저장 출력이 있는 원본은 덮어쓰지 않는다. 기본 실행은 키·유료 backend를 사용하지 않아야 한다 |

B 고유 도구 7개는 `delivery_round`, `fulfillment_status`, `production_completion`, `delivery_forecast`, `response_plan`, `compare_response_plan`, `lookup_many`다. 역할 도구 범위는 기존 `order_fulfillment/tool_specs.py` 그대로 사용하며 승인·반영 도구를 추가하지 않는다. 공통 `service.py`·`tool_specs.py`의 변경이 필요한지는 2번의 접점 선택 후 판단한다. 미리 공통 파일 전체 수정으로 확대하지 않는다.

기존 공통 lookup/export_graph만으로 B 계산을 호스트에서 먼저 수행하는 대안도 검토했다. 다만 현재 모델의 실제 B 도구 호출 순서·provider·독립 evaluator를 바꾸므로 단순 연결 수정으로 채택하지 않는다. 현재 공개 훅만으로 도구/버전 제한을 해결할 수 있다는 근거는 없으며, 이를 B subclass나 문맥 conditions의 별도 버전 표기로 우회하지 않는다.

### 단계별 무료 검증과 중단 기준

1. **수정 전 회귀 고정:** 기존 CQ01 실패 보류·원본 보존·대체 보존 실패 시 해당 schema 삭제 보류, 기존 승인·한도 검사를 먼저 확인한다. 버전 불일치와 B 도구 등록 거부는 현재 코드로 재현하는 작은 회귀를 먼저 작성한다. 이번 계획 단계에서는 실행하지 않는다.
2. **공통 접점 수락:** graph-0.5의 정상 문맥·AgentSpec/Task/Result와 기본 네 역할의 기존 동작을 검사한다. 지원하지 않는 그래프 버전·구 문맥·구 checkpoint·다른 신원·미등록 도구·권한 확대를 거부해야 한다. 검토된 확장 계약/검증기 변경도 fingerprint에서 감지하는지 확인한다. 스키마 삭제나 graph-0.1로의 재표기는 수락 기준이 아니다.
3. **대표 하나씩 연결:** promise 단독 무료 성공 뒤 역할을 순차 전환한다. typed 전후 같은 입력의 수량·시각·미확인·초안과 독립 정답을 대조한다. 잘못된 version/as_of/회차/작업/조회 kind·ID는 도구 batch 실행 전에 거부하고 후속 모델 호출이 없어야 한다. CQ06은 양쪽 버전 근거 교환·한쪽 누락·null→0·약속/배분 변경을 거부한다.
4. **비용·승인 무료 검사:** 가짜 키 로더와 HTTP 대역으로 실제 진입점을 검사한다. AuthorizedBackend 생성과 각 요청 시에도 새 승인·현재 정책 결합을 재검증해야 한다. 옛 승인·미승인·다른 실행/gate/소스/정책·synthetic 정책은 키 로드/전송 전에 거부한다. 실제 운영자가 줄 가격·대표별/전체 금액은 미확정이며 이번에 임의 지정하지 않는다. 공통 입력 예약 기준 400000과 기존 예약·정산·미보고 사용량 처리를 완화하지 않는다.
5. **노트북·전체 무료 회귀:** 고정 10대표에서 대표당4/전체40요청, 고정 모델 gpt-5.4-mini-2026-03-17, 출력1024토큰, 요청당30초, 재시도/repair0을 검사한다. 별도 한국어 의미 검토 및 CQ01 정확 초안 버전의 다른 검토자 업무 승인을 자동 작성/통과시키지 않는다. 반복 실행·중복 시작·거절·실패 후 진행 차단을 확인한다. 기본 커널·관련 계약/업무/전체 회귀와 대표 예제를 무료로 실행한다.
6. **보존·종료:** 구현 검증 때마다 새 격리 cluster/schema만 사용한다. 성공/실패·저장 장애의 결과와 원본을 보존하고 소유 서버 PID/socket/pidfile·종료 명령으로 종료를 확인한다. 기존 실제 성공/실패·노트북·원본 해시를 대조한다. 무료 대역 통과는 새 실제 LLM 성공이나 운영 완료로 보고하지 않는다.

도구/버전·근거·비용 중 하나라도 미해결이면 `live_readiness` 차단을 유지한다. 모든 검토·무료 회귀가 완료되더라도 실제 호출은 별도 새 실행의 무료 gate와 사람 승인 뒤 사용자가 직접 시연한다. 이번 작업의 실제 API 요청 수는 0이다.

### 첫 구현 범위와 이번 조사 결과

첫 구현 대상으로 권장하는 범위는 **1~3번 공통 접점의 최소 변경안 검토와 회귀 기준 확정**이다. 그 다음 B promise 하나의 typed 연결과 무료 회귀를 별도 범위로 진행한다. 여섯 역할·비용·노트북을 한꺼번에 수정하는 계획이 아니다.

이번 조사에서 수정한 파일은 본 문서와 B 전용 PROJECT_STATUS.md뿐이다. 코드·스키마·ontology/tbox.ttl·노트북·루트 PROJECT_STATUS.md를 변경하지 않았다. 정적 조사와 문서 보존 확인만 했으며 새 업무 테스트·DB·서버·키 로드·API·commit/push/PR·외부 메시지 전송은 없다. 이전 무료 검증 성공을 이번 계획의 새 실행 증거로 사용하지 않는다.

## 12. B 버전 행 원복 — 수정 전 계획

사용자가 2026-10-29 명시적으로 승인한 이번 범위는 B 개발 중 변경했던 `model.py`의 SCHEMA_VERSION만 graph-0.5에서 graph-0.1로 돌리는 것이다. B 확장 타입 10개를 포함한 전체 22개 타입, 필드·참조·검증 함수와 Python 계산은 유지한다. graph-0.1 표기 일치가 원래 main 스키마와 내용까지 동일하거나 전체 B 연결 완료라는 뜻은 아니다.

1. 수정 전 파일 해시와 Git 상태를 보존한다. 기존 실행 DB·설정·controller·과거 실행 디렉터리를 실행 대상으로 재사용하지 않는다. 실행 DB 디렉터리와 비밀 환경 파일은 해시 수집에서 제외한다. 새 PostgreSQL cluster/socket 및 테스트별 별도 schema만 사용한다.
2. 기존 B의 생산 관측·납품 조건 검증 및 typed 접수 실행기 회귀를 먼저 통과시킨다. 모델/임베딩 전송은 대역만 사용하고 실제 네트워크는 loopback 시험만 허용한다. 실제 키/환경 파일과 기존 DB 접속은 차단한다.
3. `ontologics/model.py`의 버전 행, `ontology/tbox.ttl`의 versionInfo, B 테스트 세 파일의 graph 기대값만 맞춘다. AST 대조에서 model의 나머지 구조와 실행 코드가 동일하고 T-box도 버전 행 외 동일해야 한다. main의 harness/host/contracts/runtime/provider 및 루트 PROJECT_STATUS.md는 수정하지 않는다.
4. 영향 회귀 후 전체 무료 회귀·공통 demo/CQ/도구 예제를 확인한다. 대표당4/전체40 대역 요청, 승인 대기·중복 차단·실패 중단·보존 장애 회귀를 기존대로 유지한다. 실제 Docker 등 미설정 검사는 skip으로 구분한다.
5. B 현재 안내와 전용 작업 기록에 결과와 남은 한계를 남긴다. 과거 실제 성공/실패와 당시 graph-0.5 표기·노트북 출력은 재작성하지 않는다. 새 소유 서버 종료와 기존 파일 해시를 확인한다. 실제 API·키 로드·push·PR 생성은 0이다.

이 단계는 버전 표기 원복과 회귀 확인이다. B 전용 도구의 AgentSpec 수용, 나머지 역할의 typed 연결 및 새 비용 승인의 실행 진입점 연결은 여전히 별도 미완료다. `live_readiness` 차단과 전체 연결 미완료 판정은 유지한다.


### 적용 결과와 무료 검증

사용자 승인 범위만 적용했다. 이번 변경 파일은 다음 8개이며, 시작 시 이미 존재하던 다른 미커밋 B 작업은 유지했다.

| 파일 | 이번 변경 |
| --- | --- |
| `ontologics/model.py` | SCHEMA_VERSION 한 행만 graph-0.1로 원복 |
| `ontology/tbox.ttl` | 생성기의 버전과 일치하도록 owl:versionInfo 한 행 변경 |
| `tests/order_fulfillment/test_delivery_validation.py` | 그래프 버전 기대값 한 행 변경 |
| `tests/order_fulfillment/test_production_validation.py` | 그래프 버전 기대값 한 행 변경 |
| `tests/order_fulfillment/test_b_typed_workflow.py` | B 도메인 버전 기대값 한 행 변경. 전체 연결 미완료 검사는 유지 |
| `docs/order_fulfillment/README.md` | 현재 표기와 과거 기록 구분 안내 |
| `docs/order_fulfillment/B_MAIN_CONNECTION_DESIGN.md` | 수정 전 계획·이번 결과·남은 경계 |
| `docs/order_fulfillment/PROJECT_STATUS.md` | B 전용 작업 기록 |

model의 버전 할당만 정규화한 AST 대조가 동일하고, T-box도 버전 행 외 바이트 단위로 동일하다. B 추가 10개를 포함한 22개 타입과 모든 필드·검증·계산을 유지했다. 현재 model과 PinnedContext 기본 표기는 모두 graph-0.1이다. `harness.py`, `host.py`, `contracts.py`, `runtime.py`, 공통 `openai_provider.py`, `service.py`, `tool_specs.py` 및 루트 `PROJECT_STATUS.md`는 시작 해시와 동일하다. 독립 정적 검토 결과는 CLEAR다.

| 새 무료 검증 | 결과 |
| --- | --- |
| 수정 전 B 회귀 | 59개 통과 |
| 수정 후 B·공통 영향 회귀 | 99개 통과 |
| 전체 unittest | 838개 수집·실행, 832개 통과, 6개 skip, 실패/오류0, 156.019초 |
| 공통 예제 | demo v1→v2→v3, CQ15항목, typed model/history, agent_tools 통과 |
| 보호 회귀 | 10대표/40 HTTP 대역, 대표당4/전체40 한도, 승인 대기·별도 의미 검토·정확 초안 승인·작성자/검토자 분리·중복 차단·실패 중단·원본 보존 및 보존 실패 정리 보류 통과 |
| 정적 검사 | Python3.10 문법 AST·메모리 컴파일·T-box 생성 일치·git diff --check 통과. ruff/mypy 미설치로 미실행, 새 의존성0 |

6개 skip은 검토된 고정 Docker image ID/실제 Docker 통합 환경을 요구하는 격리 검사다. 무료 대역과 실제 LLM 성공은 구분하며 이번 실제 유료 요청·실제 비밀 환경 파일 읽기·기존 DB 재사용·commit/push/PR 생성은 0이다. 최초 서버 초기화의 샌드박스 공유 메모리 제한 및 서버 미준비 상태의 회귀 오류는 환경 오류로 별도 보존했고, 새 소유 서버 준비 뒤 위 회귀를 다시 실행해 통과했다.

새 cluster는 `/private/tmp/bg01-rz0yyzwk/pg`, socket은 같은 소유 디렉터리의 `s`, port54783이다. 테스트별 schema와 예제 schema만 사용했고 남은 업무 schema0을 확인했다. 소유 PID20743만 종료했으며 pg_ctl 종료0·status3·PID/pidfile/socket 부재로 확인했다. 실행 중 외부 통신·실제 환경 파일 읽기 차단 위반0이다. 실행 DB·비밀 환경 파일·캐시를 제외한 기존 18,653개 파일을 해시 대조 대상으로 보존했고 노트북7개·과거 artifacts18,288개를 포함한다. 최종 해시 대조 원문은 아래 preservation.json에 남긴다. 기존 Task10/Task11 및 후속 실제 성공·실패 원문과 graph-0.5 기록은 재작성하지 않았다.

근거는 Git에서 제외된 `artifacts/local/order_fulfillment/graph_version_reset_20261029T093952_bg01-rz0yyzwk/`의 baseline/impact/full/examples JSON·로그, structural_proof.json, static_checks.json, cleanup.json, shutdown.json, preservation.json에 보존한다. 무시된 파일을 강제로 추가하지 않았다.

이번 결과는 graph 표기 불일치 해소와 기존 동작 보존의 증거다. B22타입과 main12타입이 같은 graph-0.1 표기를 사용하므로 버전 문자열만으로 스키마 내용 동일성을 판단할 수 없다. B 고유 도구의 공통 AgentSpec 수용, 나머지6역할 typed 연결, 새 CostPolicy 승인의 실행 진입점 연결은 별도 미완료이며 `live_readiness`는 계속 차단된다. 과거 checkpoint를 재개하거나 변환하지 않았고 버전 문자열 변경만으로 모든 과거 checkpoint가 자동 거부된다고 주장하지 않는다. 노트북 수정·새 커널 실행·새 실제 시연은 이번 좁은 변경 범위에 포함하지 않았다. 이번 무료 회귀 통과가 실제 호출 승인을 대신하지 않는다.


최종 보존 대조: 삭제 파일0, 노트북7개와 과거 artifacts18,288개 모두 동일하다. 의도한 위8개 외에 `.DS_Store`, `JunhwaDocs/.DS_Store`, `JunhwaDocs/03_B개발/.DS_Store`의 macOS 폴더 메타데이터3개가 시작 해시와 달랐다. 변경 주체는 확정하지 않았으며 기존 상태를 추측해 복원하지 않았다. 이3개를 별도 기록하고 업무 소스·노트북·실제 실행 근거의 예기치 않은 변경과 구분한다. 최초 엄격 대조의 메타데이터 차이와 최종 분류 결과를 각각 보존한다.

</details>

## 22. CQ01 미등록 접수 후보 연결 — 수정 전 계획 (2026-10-30)

이번 승인 범위는 저장형 접수와 독립된 B 접수 후보 Agent를 추가하는 것이다. README의 확장 Agent 절과 AGENT_RUNTIME에 따라 고유 INPUT/OUTPUT → AgentSpec → from_spec 등록 → run_task를 사용한다. 공통 model.py/호스트/도구/저장·승인 코드는 변경하지 않는다. 기존 b_task_agent·bounded provider·실행기·노트북·결과도 유지한다. 기존 저장형 진입점을 대체하거나 main 원복을 완료했다고 표시하지 않는다.

- `examples/order_fulfillment/b_intake_task.py`: 가상 원문·SourceDocument/Evidence·명시한 입력 revision/hash를 고정한다. 제품/개정/구성 세 종류를 공통 lookup으로 실제 조회한다. 모델이 제출한 3개 B 후보 또는 미확인/오류를 기존 순수 extract_contract와 B 입력 검증으로 대조한다. 원문은 자료이며 정답 후보를 모델 입력에 미리 넣지 않는다. 미등록·가상·사람 검토 필요를 출력에 고정하고 저장/승인 도구를 요청하지 않는다. 한국어 상태 문안은 검증 가능한 고정 문안으로 제한한다.
- `tests/order_fulfillment/test_b_intake_task.py`: 원문/해시/locator 결합, 입력 변조, 정상 1000개·arrival·정확 구성, 누락·중복·수량/기한 오류·구성 모호성, 거짓 후보/미확인/출처/확정 주장 거부, 실제 조회 누락/카탈로그 불일치, 권한·쓰기 도구 차단·4요청/repair0 및 DB 업무 상태 무변경을 보호한다.
- 수정 전 기존 접수·계약·입력/새 6역할 회귀를 실행한다. 수정 후 관련/전체 무료 회귀, 별도 프로세스의 pinned main 원본 모델 실행, CLI 예제를 확인한다. 기존 graph 기대값 실패3개와 Docker 미검증6개는 분리한다.
- 시작 파일 hash/HEAD/staging을 새 외부 임시 작업 폴더에 보존했다. 새 PostgreSQL cluster/테스트별 schema만 사용하고 외부 통신·실제 환경 파일 접근을 차단한다. 종료 전 schema 정리·서버/PID/socket 부재와 기존 파일 보존을 확인한다. 유료 API/키 로드/push/PR은 0이다.

이 단계의 지원 범위는 기존 고정 Markdown 1계약·1품목·1회차 형식이다. 임의 계약 문서 해석·공통 등록/승인·실제 LLM/provider/비용/1024토큰 적합성과 30초 제한은 이번 무료 연결 검증의 완료 주장에 포함하지 않는다. 다음 단계의 legacy 진입점 정리와 main 모델 원복은 별도다.


## 23. CQ01 미등록 접수 후보 적용·무료 검증 (2026-10-30)

이번 변경은 새 `examples/order_fulfillment/b_intake_task.py`, 새 `tests/order_fulfillment/test_b_intake_task.py`, 본 설계서와 B 전용 PROJECT_STATUS.md의 네 파일이다. 기존 저장형 `b_task_agent.py`·provider·실행기와 공통 코드/model.py·루트 상태·그래프·원본·노트북은 수정하지 않았다.

원문을 고정된 입력으로 전달하고 제품/개정/구성을 실제 공통 조회한 뒤, 모델 제출 후보와 기존 extract_contract의 독립 추출 결과 및 B 입력 검증을 대조한다. 1,000개·piece·arrival·원문 시간대·고객사/우리회사 분리를 유지한다. 미확인/오류는 후보 없이 issue/locator를 반환한다. 상태별 한국어 고정 문안으로 허위 저장/승인 주장을 거부한다. B 원문과 후보는 미등록 가상 자료이며 공통 evidence_refs에는 실제 카탈로그 근거만 남긴다. 업무 저장/승인은 없고 공통 실행 감사 기록만 남긴다.

| 이번 무료 검증 | 결과 |
| --- | --- |
| 기존 접수·계약·입력·기존 연결 기준 회귀 | 102개 통과 |
| 새 접수15개 + 기존 6역할75개 | 90개 통과 |
| 파일 변경 없이 별도 프로세스의 pinned main 원본 모델 | 같은 90개 통과 |
| 관련 B/공통 계약 회귀 | 411개 중410개 통과, 기존 typed 접수 graph 버전 실패1개 |
| 전체 unittest discover | 1,029개 중1,020통과·기존 버전 실패3·Docker6제외·오류0, 188.195초 |
| 새 카탈로그 전용 schema의 B 접수 CLI | ready·미등록·사람 검토필요, 대역 모델2/도구4/repair0; API0 |
| 보호 검사 | 변조/거짓 후보/조회 누락·불일치/미확인 숨김/권한/쓰기 차단·순차4요청·업무 저장 무변경 통과 |
| 정적 검사 | Python3.10 AST·메모리 compile·git diff --check 통과; ruff/mypy/pyright 미설치 |

기존 실패는 `test_b_typed_workflow.py:47`, `test_delivery_validation.py:170`, `test_production_validation.py:256`의 graph-0.1 기대와 현재 graph-0.5 차이다. 기대값만 바꾸지 않았으며 전체 통과로 표현하지 않는다. 처음 샌드박스 socket 거부와 새 테스트 fixture 오류7건은 최종 성공과 분리 보존했다. fixture는 기존 원본을 바꾸지 않는 data/ 내부 임시 원본으로 정리했고, 모호한 중복 구성은 공통 DUPLICATE_IDENTITY의 선행 거부를 유지했다.

독립 읽기 검토는 APPROVE이며 필수 수정 사항은 없었다. lookup 권한은 도구 단위이고 필요한 세 kind의 실제 조회 일치는 최종 결과 검증에서 강제한다. 다른 kind의 조회를 실행 전에 차단한다고 주장하지 않는다. 새 접수의 한국어 답변은 고정 상태 문안이며 실제 LLM 의미 이해를 검증한 것이 아니다.

추가 공통 demo 단독 실행은 자동 승인 검토가 기존 그래프/T-box 재생성 위험을 이유로 거부하여 실행하지 않았다. 소스상 재생성은 --export-graph 조건부이나 재시도·우회 실행은 하지 않았다. 공통 계약은 관련/전체 회귀로, 새 예제는 B CLI로 검증했다. 이 선택 실행을 통과했다고 보고하지 않는다.

새 cluster `/private/tmp/b-intake-m3mkxh8i/pg`와 테스트별 schema만 사용했다. 남은 업무 schema0, pg_ctl 종료0/status3·소유 PID61464/DB pidfile/socket 부재를 확인했다. 외부 통신·기존 환경 파일 차단 위반0, 실제 API·키 로드·push/PR0이다. 실행 DB/캐시/비밀 환경 파일을 제외한 시작89,087개 파일의 hash와 HEAD/staging을 대조한다. Git 제외 근거는 `artifacts/local/order_fulfillment/b_intake_connection_20261030_m3mkxh8i/`에 보존한다.

일곱 역할의 독립된 새 공통 실행 예제가 준비됐다. 기존 B 통합 실행기/노트북은 아직 legacy 저장형 접수를 사용하며 기본 진입점이 자동 전환된 것은 아니다. 다음 단계는 B 실행기 참조 정리·불필요 legacy 경로 정리 후 main model.py 등 원본 복구/버전 정리다. 실제 provider/CostPolicy/1,024토큰 적합성·요청당30초·이번 실행 승인·별도 의미 검토는 이후 범위이며 유료 호출 준비 완료가 아니다. B README의 이전 버전 정리 문구도 해당 후속 단계에서 바로잡아야 한다.

## 24. B 10대표 통합 입력 Task 경로 — 수정 전 계획 (2026-10-30)

승인 범위는 새 7역할을 10대표 순서로 실행하는 B 통합 경로와 무료 회귀다. 기존 free/live 및 노트북이 저장형 workflow·옛 gate에 결합되어 있어, b_run에 무료 전용 `tasks` 명령을 추가하고 기존 경로는 보존한다. 기본 free나 기존 유료 승인 의미를 이번 단계에서 바꾸지 않는다. 공통 코드/model.py·노트북·과거 실행/원본/그래프는 수정하지 않는다.

- 새 `b_task_workflow.py`가 10대표를 기존 Spec/Task/Agent/ScriptedProvider에 매핑한다. 계산·검증을 복제하지 않고 공통 from_spec/run_task를 사용한다. 대표별 공통 요청 한도4·repair0, 전체 모델 합계40을 확인한다. 카탈로그만 개발 seed하고 B 업무 입력은 저장하지 않는다. CQ01 needs_confirmation/invalid 또는 실행/결과 실패면 후속 대표를 중단한다. 한국어 의미 검토·사람 승인 완료로 표시하지 않는다.
- b_run.py/b_run_isolation.py의 B 소유 진입·분기만 수정한다. 기존 새 output·격리 서버·비밀 환경/외부 통신 차단·동일 실행 중복 거부·결과 보존·서버 종료를 재사용한다. 신규 검증 완료 파일은 `task_validation.json`이며 유료 승인용 free_gate.json을 발급하지 않는다. tasks는 live로 실행할 수 없고 기존 live 차단도 유지한다.
- 시작 점검에서 기존 SAFE_INPUTS의 ontology/tbox.ttl hash 불일치를 발견했다. 기존 manifest/hash를 갱신하거나 graph를 재생성하지 않는다. 새 입력 Task는 그래프/T-box/정답 파일을 읽지 않으므로 기존 검토 해시 중 필요한 원본·카탈로그·업무 fixture만 별도 고정 allowlist로 사용한다. 옛 free 경로의 검증 실패를 우회한 성공으로 표시하지 않는다. 공통 ROOT 전역을 바꾸는 legacy _input_roots도 새 경로에 복제하지 않는다.
- 원문/입력과 Agent 결과는 분리해 저장한다. CQ04 calculation_details 및 CQ05/06의 정확한 Python 검토 자료/hash, CQ06 양쪽 입력 provenance와 answer_producer=python을 보존한다. 실패 결과도 먼저 보존하고 새 DB를 자동 삭제하지 않아 보존 장애 시에도 소유 서버 DB를 유지한다.
- 회귀는 수정 전 기존 CLI/workflow/노트북/차단 및 기존 새 역할90개를 기준으로 한다. 신규 테스트로 전체10대표 매핑·실제 공통 조회·카탈로그만 저장·대역 호출/한도·첫 실패/미확인 중단·raw/검토자료 보존·새 output 재사용 거부·tasks paid 인자 거부·옛 gate 분리를 확인한다. 관련/전체 회귀, 별도 pinned main 모델 프로세스, 가능한 실제 격리 CLI를 무료 실행한다.
- 변경 파일은 위 B 소스3개(신규1)·신규 테스트1개와 B 문서3개로 한정한다. 새 격리 cluster/schema를 사용하고 과거 DB/controller를 재사용하지 않는다. 시작89,131개 파일 hash/HEAD/staging을 보존했다. 종료·기존 결과 보존을 확인하고 유료 API/키 로드/push/PR0을 유지한다. main 원본 복구/버전 정리와 legacy 삭제는 다음 단계다.

## 25. B 10대표 통합 입력 Task 적용·무료 검증 (2026-10-30)

새 `b_task_workflow.py`가 기존 일곱 역할의 Task 생성·AgentSpec·검증기·ScriptedProvider를 재사용한다. B CLI의 `tasks` 분기에서 카탈로그만 개발 seed하고, 대표별 새 공통 request와 `AgentRegistration.from_spec`/`run_task`로 실행한다. 별도 스키마 확장·승인·B 업무 저장 없이 10대표가 완료된다. 기존 default/free/live·노트북·과거 실행은 유지했다. main 공통 코드/model.py/T-box는 이번에 수정하지 않았다.

원문 입력과 Agent 응답을 별도 파일로 보존하고 CQ04 상세 계산·CQ05/06 검토 자료와 정확한 해시를 함께 남긴다. CQ06 답변은 기존 Python 문안임을 표시한다. CQ01 미확인/오류 또는 대표 실패 시 후속 대표를 중단한다. 동일 output은 원자적으로 거부하며 자동 재시도·재개는 없다. `task_validation.json`은 서버 종료까지 확인된 무료 연결 검증 기록이며 유료 승인용 free_gate를 발급하지 않는다. 사람 검토를 수행하거나 승인했다고 기록하지 않는다.

| 검증 | 결과 |
| --- | --- |
| 기존 실행기·노트북·차단 및 일곱 역할 회귀 | 155개 통과 |
| 새 통합13개 + 일곱 역할90개 | 103개 통과 |
| 파일 수정 없이 별도 프로세스의 pinned main 원본 모델 | 동일 103개 통과 |
| 관련 B/공통 계약 회귀 | 489개 중488통과·기존 버전 실패1·오류0, 56.290초 |
| 전체 unittest discover | 1,042개 중1,033통과·기존 버전 실패3·Docker6제외·오류0, 188.823초 |
| 실제 새 격리 tasks CLI | 10대표 통과, 대역 모델20/도구40/repair0·실제 API0, 완료 marker와 서버 종료 확인 |
| CLI status·동일 output 재실행 | status0·재실행 거부1, 새 CLI 파일73개 내용 보존·추가 실행 없음 |
| 보호·저장 경계 | 첫 실패 결과 보존/후속 중단·CQ01 미확인 중단·기존 DB 거부·정확 검토 자료·paid 인자 거부·카탈로그 외 업무 상태 무변경 통과 |
| 정적·독립 검토 | Python3.10 AST·메모리 compile·git diff --check 통과; 읽기 검토에서 필수 수정0, ruff/mypy/pyright 미설치 |

기존 실패3개는 `test_b_typed_workflow.py:47`, `test_delivery_validation.py:170`, `test_production_validation.py:256`의 graph-0.1 기대와 현재 graph-0.5 차이다. 기대값만 고치지 않았다. 처음 새 테스트의 fixture/API 사용 오류2개와 최종 성공은 각각 로그로 남겼다. 업무 구현 오류나 새 실제 LLM 실패로 분류하지 않는다. Docker는 사용 불가이고 추가 의존성 설치0이다.

검사용 새 cluster `/private/tmp/b-integrate-yzv1wdvr/pg`의 잔여 사용자 schema0, 종료0/status3 및 PID81515/pidfile/socket 부재를 확인했다. 별도 CLI 소유 cluster의 PID83553도 종료됐고 shutdown 기록은 PID/socket 부재와 pgdata 보존을 확인한다. CLI의 새 카탈로그·감사 schema는 종료된 새 pgdata 안에 보존한다. 기존 DB·컨트롤러·환경·과거 실행은 재사용하지 않았다. 외부 통신/키 읽기 허용0, 보호 위반0, 실제 유료 API·commit/push/PR0이다.

이번 수정은 B 소스3개(신규1), 신규 테스트1개, B 문서3개로 총7개다. 시작89,131개 파일과 HEAD/staging을 최종 대조한다. 근거는 Git 제외 `artifacts/local/order_fulfillment/b_task_integration_20261030_yzv1wdvr/`의 report/preservation·각 검사 로그·새 CLI 입력/결과/보호/종료 기록에 남긴다. DB 파일은 해당 소유 임시 폴더에 보존하고 Git에 추가하지 않는다.

남은 범위는 legacy 의존성·기본 진입점/노트북 정리, main 모델 원복/버전 정리, legacy T-box 입력 해시 불일치, 실제 provider/공통 CostPolicy·1024토큰 적합성·30초 제한·이번 실행 승인·별도 한국어 의미 검토다. 새 입력 경로의 무료 성공을 기존 저장형 실행·유료 실행 준비·전체 리팩터링 완료로 표현하지 않는다.

## 26. Legacy 의존성과 main 원복 전 정리 계획 (2026-10-30)

이번 단계는 README 두 연결 절·AGENT_RUNTIME을 다시 읽고 남은 의존성을 조사한 사전 점검이다. **소스·테스트·model.py·공통 코드·T-box·노트북·과거 결과를 수정하지 않았다.** 변경은 본 설계서와 B 작업 기록뿐이다. Git HEAD/index·기존 파일 hash를 대조하며 기존 DB·환경 파일은 실행/연결 대상으로 사용하지 않는다.

### 확인된 경계

- 새 7역할과 `b_task_workflow`는 공통 host/from_spec/run_task 및 B 입력 검증·계산을 사용한다. `schema_validation.validate_input_graph`는 공통 노드만 공통 검증기에 보내고 B 업무 노드는 B 정의로 검증한다. 원본 main에도 필요한 ROOT/COMMON_FIELDS/문자열·시간·canonical/digest helper가 있다. 이전 단계의 main 원본 모델 103개 통과는 유지된 기존 증거이며 이번에 새 DB로 재실행한 결과가 아니다.
- `b_run.py:27`의 기본값은 free다. `b_run_isolation.py:419`는 legacy workflow를, `b_run_workflow.py:18~19`는 기존 host/저장형 접수를 호출한다. `b_notebook.py:157`은 명시 free를 실행하고 182~259행은 free_gate·reports 형식과 유료 승인 준비에 결합돼 있다. 기본값 한 줄 변경만으로 노트북이 전환되지 않는다.
- 기존 `b_contract/b_fulfillment/b_production/b_delivery/b_response/b_recalculation` standalone 예제도 B 초안·검토·반영을 수행한다. B service는 공통 Service를 상속하고 저장 검증을 확장하지 않는다. 공통 `_candidate`는 put 구조와 변경 후보를 구성할 뿐이다. main 원본에서도 후보 구성은 가능하지만, 뒤의 공통 validate_graph가 B 타입을 UNKNOWN_TYPE으로 판단한다. 실제 초안은 needs_changes가 될 수 있으며 승인·확정 저장이 정상 통과한다는 뜻이 아니다. 이번 실험에서는 DB와 draft를 만들지 않았다.
- 순수 업무 테스트9파일도 아직 공통 validate_graph/graph_jsonld/T-box가 B 정의를 포함한다고 가정한다. 저장형 테스트와 분리해서 참조를 정리해야 한다. `test_cq03_recovery.py`의 공통 검사 참조는 별도의 legacy 저장형 검사이므로 일괄 치환 대상에서 제외한다.
- 현재 model은 graph-0.5/22타입, pinned main은 graph-0.1/12타입이고 B 정의는 별도10타입이다. 현재 T-box의 표기는 graph-0.1이지만 내용은 main 원본과 다르다. 현재 생성기 출력도 저장 T-box와 일치하지 않는다. legacy manifest가 기대하는 T-box hash, 현재 파일 hash, pinned main 파일 hash는 모두 다르다. 숫자 변경이나 manifest 재해시로 해결했다고 표시하지 않는다. `schema.EXTRACTED_GRAPH_VERSION='graph-0.5'`는 추출 출처 기록이므로 main 활성 버전과 구분한다.

### 파일을 바꾸지 않은 무료 비교 실험

새 임시 폴더의 별도 프로세스에서 pinned main 원본 모델을 사용했다. 후보 실험은 선택한 테스트 모듈의 함수 참조만 프로세스 안에서 B 구현으로 바꿨다. 운영 모듈 전역 등록·monkey patch를 해결책으로 도입한 것이 아니다. DB/소켓/DNS/환경 파일 접근을 차단했고 실제 연결·API·보호 위반은0이다.

| 선택한 순수 업무203개 | 통과 | 실패 | 오류 | 의미 |
| --- | --- | --- | --- | --- |
| 현재 소스 그대로 | 201 | 2 | 0 | 기존 생산/납기 graph 버전 기대 불일치 |
| main 모델만 별도 적용 | 2 | 198 | 3 | 기존 테스트 참조의 B 타입/출력 가정이 드러남; 실제 파일 원복 아님 |
| main + B 입력 검증/JSON-LD 참조 | 199 | 4 | 0 | 남은4개는 공통 T-box에 B 클래스가 있어야 한다는 가정 |
| 위 후보 + B 합성 T-box 출력기 참조 | 201 | 2 | 0 | 남은2개는 합성 B 출력과 현재 공통 T-box 파일의 동일성 가정 |

진단 실패는 모델 원복 영향 확인용이며 현재 tree에 새 회귀가 생긴 것이 아니다. 위203개는 전체 검사가 아니고, 프로세스의 exit0도 테스트 전체 통과를 뜻하지 않는다. 네 모드의 원래 실패/오류 로그와 조건을 모두 보존한다.

### 다음 수정 순서와 회귀 범위

1. **B 순수 검사부터 분리한다.** `tests/order_fulfillment/test_delivery.py`의 DeliveryGraphTests, `test_fulfillment.py`, `test_production.py`, `test_delivery_validation.py`, `test_production_validation.py`, `test_delivery_forecast.py`, `test_response_plan.py`, `test_response_comparison.py`, `test_recalculation_demo.py`가 범위다. B 입력은 기존 validate_input_graph, B JSON-LD는 기존 schema_export.graph_jsonld, B 용어/합성 출력은 기존 tbox_terms/tbox_turtle로 검증한다. 공통 helper는 재사용한다. 공통 파일과 생성기 동일성·main 버전/타입 검사는 B 합성 출력 검사와 분리해 유지한다. 기존 기대값을0.5로 바꾸거나 실패 검사를 삭제해서 통과시키지 않는다. 동일203개·기존 schema/extraction/export·새103개를 현재/별도 main 모델에서 확인한다. 원본 파일 동일성 검사의 최종 통과는 실제 main 원복 단계까지 보류한다.
2. **지원 실행 경로를 새 tasks로 정리한다.** B CLI 기본값·설명을 tasks 중심으로 바꾸고 기존 free/live·host·standalone의 저장형 실행 진입은 자원 준비 전에 명시적으로 중단/보관하도록 한다. CLI뿐 아니라 prepare/worker의 직접 진입과 notebook 연결도 조사한 소비자 기준으로 함께 처리한다. 기존 노트북 파일·저장 출력은 유지하고, 새 결과 조회 화면은 별도 범위로 다룬다. 새 task_validation을 paid free_gate로 바꾸지 않는다. 기본 명령·명시 legacy·직접 진입·동일 output·중간 실패·종료 및 기존 승인/의미 검토 보호를 회귀한다.
3. **저장형 성공 검사의 목적을 새 경계로 옮긴다.** `test_b_run_workflow/test_b_typed_workflow`, B service/host/provider 및 recovery 검사는 순수 계산·새 Task 성공과 공통 저장 거부/공통 계약 검사로 나눈다. 작성자/검토자 분리·정확 초안 버전·누적 예산 같은 공통 보호는 유지한다. 옛 저장형 실행을 살리려고 공통 Service/model에 B 타입을 주입하거나 테스트용 역사 모델을 운영 validator로 사용하지 않는다. 소비자가 제거된 legacy 내부 파일만 이후 삭제 후보로 판단한다.
4. **마지막으로 main 원본 복구를 수행한다.** 앞 단계 정리가 확인된 뒤 model.py와 공통 T-box를 pinned main 원문과 바이트 단위로 맞춘다. 이 단계 전에는 두 파일을 수정하지 않는다. B 스키마/업무 규칙은 하위 폴더에 유지하며 과거 생성 그래프와 결과는 재작성하지 않는다. 새10대표·공통 계약·전체 회귀·별도 schema·서버 종료를 확인하고 기존 graph 버전 실패도 이때 최종 정리한다. 실제 provider/CostPolicy 연결은 이후 별도 범위다.

남겨야 할 공통 연결은 AgentSpec/PinnedContext·공통 identity/host/harness/provider·비용/권한 경계다. B schema/validator/계산/fixture loader/새7역할/b_input_task/b_comparison_narrative와 격리 실행기는 삭제 대상이 아니다. schema_export도 새 순수 출력 검사가 사용하며, b_run_transport의 CASES와 비용/승인 보호는 아직 직접 소비자가 있으므로 legacy라는 이유만으로 삭제하지 않는다. 정적 import·문자열 소비자 조사는 외부 호출이나 동적 사용 전체의 미사용 증명이 아니다.

이 방향은 B 업무 입력을 공통 그래프에 저장하지 않는 기존 합의 안에서 진행할 수 있다. B 전체 그래프의 공통 저장을 다시 요구하거나 공개 입출력/도구/권한/도메인/공통 비용 정책을 바꿔야 할 경우만 별도 공통 검토 대상으로 남긴다. 이번에는 해당 공통 변경을 제안하거나 실행하지 않았다.

사전 점검 근거는 Git 제외 `artifacts/local/order_fulfillment/b_legacy_preflight_20261030_u8zila4m/`에 보존한다. 소스 변경이 없어 전체 DB 회귀는 반복하지 않았다. 새 서버 시작0·DB 연결0·실제 유료 API0·commit/push/PR0이다. 최초 파일 목록 수집의 기존 DB socket 일반파일 처리 오류와 진단 helper 이름 오류는 임시 조사 스크립트 문제였고, 기존 DB/환경/비일반 파일을 제외하고 올바른 B 출력 helper로 수정해 비교를 완료했다. 기존 DB 연결이나 파일 수정은 없었다.


## 27. B 순수 테스트 참조 분리 변경 계획 (2026-10-30)

사용자가 승인한 이번 범위는 순수 업무 테스트9파일의 B 검증/JSON-LD/용어 출력을 기존 B 함수로 전환하는 것이다. 새 운영 추상화나 의존성은 추가하지 않는다. test_delivery는 DeliveryGraphTests만 대상이며 저장형 Service 검사는 유지한다. 공통 ROOT/시간 helper는 재사용한다.

공통 graph-0.1 버전/스키마 및 ontology/tbox.ttl의 공통 생성기·pinned main 출력 일치 검사는 기존 test_schema_export.py의 별도 경계 클래스로 옮겨 유지한다. B 합성 T-box와 공통 파일을 같은 것으로 비교하지 않는다. 버전 기대값 완화·검사 삭제·skip은 하지 않으며, 현재 원본 미복구 불일치는 명시적으로 남긴다.

변경 전203개 업무 검사 결과와10개 테스트 파일·2개 B 문서의 원문, 기존 산출물 hash를 새 격리 폴더에 보존한다. 변경 후203개를 현재 모델/테스트 프로세스의 pinned main 모델에서 실행하고, schema/export/extraction 및7역할·10대표 연결 검사, 새 PG cluster/schema의 관련·전체 회귀를 수행한다. 외부 API·기존 환경 파일 접근을 차단하고 새 서버 종료를 확인한다. model.py·공통 코드·공통 T-box·운영 B 코드·노트북·기존 그래프와 결과는 수정하지 않는다. legacy 실행 경로 정리·실제 main 원복·실제 LLM 호출은 후속 범위다.


## 28. B 순수 테스트 참조 분리 결과 (2026-10-30)

이번에는 계획27의 테스트 참조 분리만 적용했다. 운영 코드와 model.py·공통 파일·공통 T-box는 그대로다. B 검증은 schema_validation.validate_input_graph, B JSON-LD/T-box 출력은 schema_export의 기존 함수를 사용한다. 공통 ROOT/instant와 합성 출력의 활성 공통 버전 표시는 재사용한다. test_delivery.py의 저장형 DeliveryServiceTests는 수정하지 않았다.

변경 테스트는 tests/order_fulfillment/ 아래 test_delivery.py, test_fulfillment.py, test_production.py, test_delivery_validation.py, test_production_validation.py, test_delivery_forecast.py, test_response_plan.py, test_response_comparison.py, test_recalculation_demo.py 및 test_schema_export.py의 총10개다. 문서는 본 설계서와 B PROJECT_STATUS만 갱신했다. 순수 업무 검사에서 분리한 공통 graph 버전·T-box 파일 의무는 CommonArtifactBoundaryTests 2개로 유지하며 pinned main의 SCHEMA 전체도 확인한다. skip/기대값 완화는 없다.

| 무료 검사 | 결과 |
| --- | --- |
| 변경 전 순수 업무203개 | 201통과·기존 버전2실패·오류0 |
| 변경 후 같은203개, 현재 모델 | 203통과·실패/오류/skip0 |
| 변경 후 같은203개, 별도 프로세스 pinned main 모델 | 203통과·실패/오류/skip0 |
| 새7역할·10대표 연결103개, 현재/별도 main | 각각103통과·실패/오류/skip0 |
| 관련552개 | 549통과·실패 메서드3·오류0 |
| 전체 unittest discover1044개 | 1035통과·실패 메서드3·Docker opt-in skip6·오류0 |

실패 메서드3개는 ① 공통 모델의 graph-0.5/22타입과 pinned main graph-0.1/12타입 불일치 ② 현재 공통 T-box 파일과 활성/원본 main 생성기 불일치 ③ 기존 typed workflow의 graph-0.1 기대 불일치다. unittest 원문에는 subTest로 구분한5개 실패 항목(새 경계4항목+기존1항목)이 기록된다. 실패 메서드 수와 하위 assertion 수를 혼동하지 않는다. 원본 main 모델을 테스트 프로세스에만 적용하면 첫 번째 경계는 통과하지만, 실제 T-box 파일의 원복은 아직 하지 않아 두 생성기 비교가 실패한다. 전체 통과나 실제 모델/저장 연결 완료로 보고하지 않는다.

원래 업무/설정 메서드는 AST 비교로 함수 참조 전환과 문서화한 공통 assertion 이동 외 동일함을 확인했다. 10개 파일의 Python3.10 AST·메모리 compile 및 git diff --check를 확인했다. ruff/mypy/pyright는 설치되지 않아 실행하지 않았으며 의존성을 추가하지 않았다. 독립 읽기 검토에서도 누락·업무 검사 약화·범위 위반은 발견되지 않았다.

검증은 새 /private/tmp/b-test-refs-eth9yy2q/pg 클러스터·소켓과 테스트별 새 schema를 사용했다. 기존 DB·settings/controller·환경 파일·과거 실행을 재사용하지 않았다. 일반 sandbox의 최초 initdb는 shared memory 권한 오류로 실패 후 자동 정리됐고, 동일 신규 경로를 승인된 로컬 권한으로 초기화해 검증했다. 남은 사용자 schema0, 신규 서버 stop/status·PID·socket 부재를 확인했다. 기존 실행 재개/재시도0·실제 유료 API0·외부 API/기존 환경 접근 보호 위반0·commit/push/PR0이다.

시작 시 전체 파일 hash와 변경 전 테스트/문서 원문을 보존했다. 최종 보존 대조·전체 로그·실패 원문·정적 검사·서버 종료 근거는 Git 제외 artifacts/local/order_fulfillment/b_test_refs_20261030_eth9yy2q/에 기록한다. model.py·공통 코드·T-box·루트 PROJECT_STATUS·노트북 저장 출력·기존 그래프와 결과 보존을 검증한다. 기존 성공 증거를 이번 무료 실행의 실제 LLM 성공으로 재분류하지 않는다.

다음 범위는 legacy CLI/default/notebook 및 저장형 검사 소비자를 새 tasks/명시적 경계로 정리하는 것이다. 이번에는 진행하지 않았다. 이후 model.py/T-box를 원본 main으로 복구할 수 있으며, 현재 버전 숫자만 바꾸거나 manifest hash를 갱신해 통과시키지 않는다. 실제 provider·공통 CostPolicy/도메인 승인·1024토큰/30초·실제 한국어 의미 검토는 별도 미검증이다.


## 29. B 공식 실행 진입 정리 계획 (2026-10-30)

이번 승인 범위는 CLI 기본값을 input tasks로 전환하고, 기존 저장형 CLI·격리 owner/worker·노트북의 새 legacy 실행을 자원 준비 전에 중단하는 것이다. standalone main9개(기존 agent/fulfillment_agent/host_agent와 계약·물량·생산·납기·회신·재계산)도 같은 안내로 중단한다. 내부 계산·provider·평가·저장형 역사 회귀 helper는 이 단계에서 삭제하지 않는다. 모든 Python 내부 호출을 차단했다고 주장하지 않는다. model.py·main 공통 코드·공통 T-box와 기존 노트북/그래프/실행 결과는 변경하지 않는다.

기존 무료 CLI/노트북/유료 게이트 검사를 먼저 실행한다. 새 거부 검사뿐 아니라 tasks 명령의 순서/중복·보호 경로·새 output·소유 프로세스 정리·깨끗한 환경 회귀를 유지한다. legacy 거부로 기존 경로 검사가 거짓 통과하지 않도록 기존 검사도 tasks 진입을 명시한다. 노트북 연결부에는 start_tasks 및 task_validation/새 결과 reader를 최소 추가하고, 기존 free/live 시작은 명시 거부한다. task_validation을 paid free_gate로 승격하거나 승인/검토 내용을 자동 작성하지 않는다.

새 PG cluster와 테스트별 schema에서7역할/10대표·관련·전체 회귀, 원본 main 모델을 테스트 프로세스에서만 사용하는 연결 회귀, 신규 보호 CLI의 실제 무료 실행·중복 거부·종료를 확인한다. 기존 DB/settings/controller/과거 실행 재사용·실제 API·원격 push는0이다. 최초 전체 hash·변경 전 원문과 기존 성공/실패 결과를 보존하고, 작업 후 공통 파일·노트북·기존 결과 보존을 다시 대조한다. 이후 저장형 내부 소비자 정리와 실제 main 원복은 다음 단계다.


## 30. B 공식 실행 진입 정리 결과 (2026-10-30)

지원하는 새 실행은 b_run의 기본 tasks 경로다. 명령 생략/명시 tasks 모두 input_tasks·free scripted provider로 실행한다. 기존 free/live는 인자 파일·PostgreSQL 경로 조회·prepare 이전에 B_LEGACY_EXECUTION_RETIRED 안내로 중단한다. list/status의 조회 기능은 유지한다. 공통 코드·model.py·공통 T-box는 변경하지 않았다.

B isolation의 require_input_tasks를 prepare·launch·own_run·guarded_campaign에 재사용한다. legacy/누락 workflow·live·유료 인자가 있으면 해시 계산·파일 생성·환경 구성·process/audit hook 설정 전에 차단한다(owner/worker는 명시 settings 파일을 읽은 직후 검사). 기존 worker의 legacy campaign 및 유료 backend/검토 대기 분기는 삭제했고, 성공 marker는 task_validation만 발급한다. 도달 불가능한 기존 live sandbox/audit 조건은 후속 단순화 대상으로 남는다.

저장형 standalone main9개(계약·물량·생산·납기·회신·재계산·b_agent·b_fulfillment_agent·b_host_agent)는 같은 안내로 종료한다. 기존 계산·provider·평가 함수/클래스는 AST 비교로 모두 동일함을 확인했다. 모듈 import 자체는 차단하지 않는다. b_run_workflow.run_campaign·run_example·run_mode·run_scripted_example 같은 내부 저장형 Python helper와 역사 회귀는 다음 정리 단계까지 남겼으므로 모든 legacy Python 호출을 차단한 것은 아니다.

노트북 파일은 수정하지 않았다. 연결부 Demo에는 별도 start_tasks 모드·task_validation 상태·campaign/<대표>/result.json reader를 추가했다. 기존 generic _start도 tasks만 허용하고 추가 인자로 우회할 수 없다. 새 결과는 Agent 이름·case_task의 context·무료 대역/실제 API0·미수행 의미 검토 표기를 대조한다. 부분 JSON은 실행 중에만 대기하며 symlink·다른 대표·paid 응답 재분류는 거부한다. 기존 free/live 시작은 승인 파일 생성/Popen 전에 중단한다. 과거 결과 조회·검토 보호는 보존하며 task_validation을 유료 free_gate로 승격하지 않는다. 옛 Console 메뉴는 tasks UI로 바꾸지 않아 기존 무료/유료 시작 메뉴를 사용하면 명시 중단된다. 새 CLI/start_tasks 사용법은 B README에 안내하고 아래 과거 명령은 이력이라고 구분했다.

### 변경 파일

운영 B 파일12개: examples/order_fulfillment/b_run.py, b_run_isolation.py, b_notebook.py, b_contract.py, b_fulfillment.py, b_production.py, b_delivery.py, b_response.py, b_recalculation.py, b_agent.py, b_fulfillment_agent.py, b_host_agent.py. 기존 테스트4개: test_b_run.py, test_b_task_workflow.py, test_b_notebook.py, test_b_fulfillment_agent.py. 새 테스트1개: test_b_entry_boundary.py. 문서3개: B README·본 설계서·B PROJECT_STATUS. 루트 README/PROJECT_STATUS는 수정하지 않았다.

### 무료 검증

| 검사 | 결과 |
| --- | --- |
| 변경 전 CLI/노트북/기존 유료 차단 | 59/59통과 |
| 변경 후 위 범위+새 진입 경계10개 | 69/69통과 |
| 7역할·10대표 연결, 현재/테스트용 pinned main 모델 | 각각103/103통과 |
| 관련596개 | 593통과·실패 메서드3·오류0 |
| 전체 unittest discover1054개 | 1045통과·실패 메서드3·Docker opt-in skip6·오류0 |

실패3메서드는 기존 공통 모델 버전/22타입 대 main12타입 경계, 공통 T-box 파일 대 두 생성기 경계, 기존 typed workflow graph-0.1 기대다. raw unittest에는 subTest4항목+typed1항목=실패5항목으로 기록된다. 기대값 변경·검사 삭제·skip으로 감추지 않았고 이번에 새 회귀 실패를 추가하지 않았다. Docker6개는 실제 검증 성공으로 세지 않는다.

새 임시 폴더에서 명령 생략 기본 CLI를 실제 보호 실행하여10대표/20대역 모델 호출/실제 API0/사람 의미 검토 not_performed를 확인했다. 새 task_validation만 발급하며 paid gate는 없고, 서버 stop·PID/socket 부재·안전 guard 위반0을 확인했다. 같은 output으로 기본 명령을 다시 호출하면 새 DB/호출 없이 거부하고 결과73파일 hash가 유지됐다. 같은 신규 실행의10개 결과를 노트북 reader로 읽어 답변·context·실행 표기와 상태를 확인했다. 이것은 실제 LLM 성공 증거가 아니다.

회귀는 별도 신규 PG cluster/테스트별 schema에서 실행했다. 잔여 사용자 schema0·서버 stop/status·PID/socket 부재를 확인했다. CLI 자체 신규 cluster도 별도로 종료됐으며 기존 DB/settings/controller/과거 실행 재사용0·실제 유료 API0·commit/push/PR0이다. AST Python3.10·메모리 compile·git diff --check 및 독립 읽기 검토를 수행했다. ruff/mypy/pyright는 미설치로 실행하지 않았고 새 의존성은 없다.

변경 전 전체 파일 hash와 원문을 보존했다. 최종 보존 대조·모든 로그·실패 원문·새 CLI 결과/서버 종료 근거는 Git 제외 artifacts/local/order_fulfillment/b_entry_refactor_20261030_7du02umo/에 보존한다(DB 디렉터리는 복사하지 않는다). model.py·공통 코드·공통 T-box·루트 상태 문서·기존 노트북 저장 출력·원본/그래프·기존 성공/실패 결과는 그대로다.

남은 다음 범위는 내부 저장형 helper/테스트 소비자와 공통 모델 의존성 정리다. 이를 확인한 뒤 model.py·공통 T-box를 pinned main 원본으로 복구한다. 이번에는 두 단계 모두 진행하지 않았다. 실제 provider·공통 비용 정책·실행 승인/한국어 의미 검토 연결도 별도 미검증이다.


## 31. 내부 저장 예제 정리·잔여 의존성 진단 계획 (2026-10-30)

원본 main 모델을 테스트 프로세스에만 적용한 전체 진단에서 새7역할·10대표는 통과하며 저장형 구기능의 의존성이 남아 있음을 확인했다. 이번 작은 변경은 실행 소비자가 없는 CQ02~06의 standalone run_example 5개 본문과 그곳에서만 쓰던 import·private helper를 삭제하고, 기존 함수 서명은 새 tasks 명령을 안내하는 명시적 중단으로 유지한다. CQ01 b_contract.run_example은 b_agent.setup_promise가 재사용하므로 이번에 변경하지 않는다. 옛 Agent의 setup/run_mode/legacy campaign과 B adapter는 후속 정리 대상이며 이 단계에서 전체 분리가 끝났다고 하지 않는다.

먼저 변경 전 핵심 회귀와 원본 main 전체 진단을 보존한다. 기존 순수 promise_values·representative_graph·loader·새7역할 함수는 AST로 보호하고, 새 거부 검사는 파일/환경/DB/provider를 열기 전에 중단되는지 확인한다. 이제 사용하지 않는 CQ02/03 모듈의 import를 검사하는 테스트는 실제 사용처인 CQ04 대표 그래프 함수의 B schema/output 연결 검사로 바꾸며, 기존 독립 그래프 결과 비교는 그대로 둔다. 저장 경계 검사의 B 구조 검증은 B validate_input_graph를 사용하고 원본 main UNKNOWN_TYPE 거부는 따로 유지한다. 조회 도구 kind enum은 공통 SCHEMA가 아닌 B lookup_schema 조합과 비교하고 공통 SCHEMA 불변도 확인한다. 기대값을0.5로 바꾸거나 공통 validator에 B 타입을 주입하지 않는다.

변경 후 핵심·현재 모델 전체 회귀와 원본 main 전체 진단을 재실행하고, 새 격리 CLI10대표의 실제 무료 실행·중복 거부·결과 보존·서버 종료를 확인한다. model.py·공통 코드·공통 T-box·루트 문서·기존 노트북/그래프/실행 결과는 수정하지 않는다. 기존 파일 해시·원문, 새 로그·이전 실패를 보존하고 별도 새 PG와 schema만 쓴다. 실제 유료 API/과거 실행 재개/commit/push/PR0.


## 32. 내부 저장 예제 정리·진단 결과 (2026-10-30)

실행 소비자가 없는 CQ02~06 저장형 예제5개의 run_example 본문을 명시적 중단으로 바꾸고 전용 import·미사용 private helper를 제거했다. 같은 함수 서명과 CLI 안내를 유지하되 입력·DB·환경·출력·process 접근 전에 RuntimeError(B_LEGACY_EXECUTION_RETIRED)를 낸다. 예제5개는633줄에서111줄로, 순522줄 줄었다. CQ04 representative_graph와 나머지 main은 AST가 동일하며 CQ01 계약 예제·기존 provider·setup·legacy campaign·B adapter는 이번에 변경하지 않았다. 새7역할/10대표와 순수 loader·업무 계산·출력은 유지한다.

변경 파일은 examples/order_fulfillment/{b_fulfillment,b_production,b_delivery,b_response,b_recalculation}.py 5개, tests/order_fulfillment/{test_b_entry_boundary,test_representative_graph,test_storage_boundary,test_tool_specs}.py 4개, B README·본 설계서·B PROJECT_STATUS 3개다. 대표 그래프 회귀의 독립 결과/순서/참조/원본 불변 검사는 유지하고, 실행이 삭제된 모듈에 대한 import-only 주장만 정리했다. 저장 경계는 B 구조 검증과 원본 main의 UNKNOWN_TYPE 거부를 구분하며, 조회 enum은 B lookup_schema 조합을 검증하고 공통 registry/tool 불변을 확인한다. 테스트 삭제·skip·공통 validator 주입·graph 버전 숫자 완화는 없다.

| 무료 검사 | 결과 |
| --- | --- |
| 변경 전 핵심 | 150/150 통과 |
| 변경 후 핵심·새 직접 진입 거부, 현재/테스트용 원본 main | 각각151/151 통과 |
| 현재 모델 전체 | 1055개: 1046통과·기존 실패 메서드3·Docker opt-in skip6·오류0 |
| 테스트용 원본 main 전체 진단 | 변경 전1054개 raw실패93/오류155 → 변경 후1055개 raw실패91/오류155; skip6 |
| 새 보호 CLI10대표 | 10통과·대역 모델20회·실제 API0회·의미 검토 not_performed |

현재 전체의 raw실패5항목은 공통 모델 graph-0.5/22타입과 pinned main graph-0.1/12타입 불일치2, 공통 T-box 대 활성/원본 생성기 불일치2, 기존 typed workflow 버전 기대1이며, 실패 메서드는3개다. 기존 실패를 지우거나 전체 통과로 보고하지 않는다.

원본 main 전체 진단에서는 잘못된 B 검증/조회 기대값2개만 해결됐다. 남은 raw실패·오류246기록은 구 저장형 경로244기록과 공통 T-box2 subtest다(실패/오류 메서드208개). 이는 실제 코드 변경 후 새로 생긴 현재 회귀가 아니라 공통 모델을 원본으로 되돌렸을 때 드러나는 잔여 의존성이다. 새7역할·10대표 실패는0이다. 따라서 main 원복 준비나 전체 호환성 완료로 해석하면 안 된다. 예전 Agent setup/run_mode·legacy campaign/typed intake·저장형 service/복구 테스트 소비자의 후속 정리가 남았다. 순수 provider·권한·비용·도구 계약 회귀는 일괄 삭제하지 않는다.

새 CLI는 task_validation만 발급하고 paid free_gate가 없다. 같은 신규 output 재실행은 DB/호출 없이 거부하고73개 결과 파일 해시가 같다. CLI 서버와 회귀 서버 모두 소유 PID·socket 부재/종료를 확인했고 회귀 DB의 남은 사용자 schema0이다. 회귀 서버는 /private/tmp/b-storage-retire-bvxf1rp_/pg, 새 Unix socket/port54883만 사용했다. 기존 DB/settings/controller/과거 실행 재사용0·실제 API0·commit/push/PR0.

Python3.10 AST·메모리 compile은 B Python123개, git diff --check, 변경하지 않은 함수 AST 비교와 독립 읽기 검토를 통과했다. ruff/mypy/pyright는 미설치로 실행하지 않았으며 새 의존성은 없다. model.py·공통 코드8개·T-box·루트 문서·기존 노트북/그래프/실제 성공·실패 결과는 시작 시 해시와 다시 대조해 보존한다. 원문·모든 진단 로그·분류·새 CLI 결과·종료·보존 근거는 Git 제외 artifacts/local/order_fulfillment/b_storage_retire_20261030_bvxf1rp_/에 보존한다. 실제 LLM/provider·공통 CostPolicy·실행 승인 연결은 별도 미완료다.


## 33. B 리팩터링 최종 정리 계획 (2026-10-30)

사용자는 잔여 저장형 의존성 정리부터 원본 main 복구·최종 무료 검사까지 완료하도록 승인했다. 목표는 기존 main 파일을 고정한 상태에서 새7역할·CQ01~06 10대표가 AgentSpec→호스트→run_task로 정상 실행되는 것이다. 시작 시 전체65519파일 해시와 B 코드/테스트/문서·복구 후보5파일 원문을 /private/tmp/b-final-refactor-2gj5jyuu/before에 보존했다. 과거 실제 성공·실패 결과와 노트북 저장 출력은 재분류하거나 재생성하지 않는다.

1. 옛 Agent setup_intake/setup_promise/setup·run_mode·run_example·legacy host/campaign의 저장형 본문을 제거하고 자원 접근 전 명시 중단한다. 새 tasks와 공유하는 계산·loader·대표 그래프·순수 provider/검증/권한/비용 검사에 쓰이는 API는 유지한다. campaign은 OfflineStub/승인/보존 DB 복구 흐름을 제거한 거부 호환 진입으로 축소한다. B의 pure typed adapter는 남은 순수 schema/해석 소비자를 위해 미지원 과거 계약으로 구분하며 새 실행에 등록하지 않는다.
2. B를 공통 저장에 성공시키는 것이 전제인 DB 회귀는 현 지원 범위에서 퇴역한다. 테스트를 실패명단만 보고 일괄 삭제하지 않고 fixture/호출 책임별로 조사한다. 공통 승인·신원·정확한 버전·누적 예산·권한·provider 순수검사는 보존하고, Agent 동작은 새7역할/10대표 검사, 업무 계산은 순수 도메인 검사, 공통 B 저장 거부는 기존 _candidate/put 경계 검사로 대체 책임을 명시한다. 공통 카탈로그만 필요한 호환 검사 fixture는 공통 bootstrap으로 바꾼다. 새 직접 내부 진입 거부·retired import collection·결과 보존/실패 중단을 검증한다.
3. ca7c391 대비 main 기존 파일의 B 수정분은 .gitignore 외 model.py/T-box 및 패키지 버전3파일(__init__.py, pyproject.toml, data/catalog.json)뿐임을 확인했다. 사용자 의도 .gitignore는 유지하고 나머지5개를 원본 Git bytes로 복구한다. catalog nodes는 전후 동일한지 확인하고, B SAFE_INPUTS의 catalog hash만 원본 bytes와 대조해 갱신한다. 가격/권한/도메인 계약을 바꾸거나 공유 validator에 B 타입을 주입하지 않는다. 기존 그래프·과거 결과는 다시 생성하지 않는다.
4. 새 PG cluster/Unix socket/각 test schema를 생성한다. 관련 및 전체 unittest·Python3.10 AST/compile·diff 검사, 공통 데모와 새 기본 CLI10대표·중복 거부·결과/원본 보존·서버 종료를 검증한다. 원본 main 공통 파일 비교·B10타입 입력 계산/출력 검사·기존 실제 성공 증거 해시 대조로 완료를 확인한다. main 코드8개는 복구 대상도 아니며 수정하지 않는다. Docker 검사는 환경이 갖춰진 경우만 실행하고 미실행을 통과로 세지 않는다. 실제 유료 API·기존 환경/DB/controller/실행 재사용·commit/push/PR0이다.


## 34. B 리팩터링 최종 결과 (2026-10-30)

원본 main 위에서 B Agent·예제를 실행하는 리팩터링을 완료했다. main `ca7c391`의 추적 파일124개를 전부 대조해 의도된 `.gitignore`를 제외한123개가 원본과 바이트 단위로 같음을 확인했다. Git 병합 무충돌 여부와 별도로, 실제 복구한 코드에서 아래 무료 기능 검증을 수행했다. 실제 LLM·유료 시연 연결을 완료했다는 뜻은 아니다.

### 최종 구조와 정리 범위

- `ontologics/model.py`와 `ontology/tbox.ttl`을 main 원본으로 복구했다. 공통 모델은 `graph-0.1`/12타입이며 B 추가10타입은 B의 `schema.py`·검증·출력·계산에서 관리한다. `EXTRACTED_GRAPH_VERSION=graph-0.5`는 추출 원본 이력이다. `PinnedContext`·공통 validator에 B 타입을 추가하지 않았다.
- 공용 버전이 B 값0.8.0으로 남아 있던 `ontologics/__init__.py`, `pyproject.toml`, `data/catalog.json`도 main 원본0.3.0으로 복구했다. catalog의 업무 nodes는 전후 동일하다. B SAFE_INPUTS에서는 이 catalog의 정확한 새 해시만 갱신했다. 공통 host/runtime/contracts/harness/service/identity/database/openai_provider와 루트 README/PROJECT_STATUS는 이번 작업에서 변경하지 않았다.
- 옛 저장형 Agent·계약·host·campaign9파일의 내부 진입12개는 자원 접근 전에 `B_LEGACY_EXECUTION_RETIRED`로 중단한다. 사용하지 않는 저장·승인·복구 본문과 전용 import를 제거해 순1014줄 줄였다. `b_run_workflow`의 OfflineStub 및 campaign 본문도 제거했다. 순수 provider/계산/검증/loader·대표 그래프 등 나머지 함수/클래스는 AST로 동일함을 확인했다.
- 지원하는 실행은 새7역할의 AgentSpec→공통 호스트→`request.run_task`와 `b_run tasks`다. B 입력은 미등록 업무 자료로 계산·검증하며 공통 DB에 B 전체 그래프를 저장·승인하는 옛 기능은 제공하지 않는다. pure typed adapter와 B Service/Host/provider는 순수 계약·호환 회귀용으로 남았고 새 tasks 실행에는 사용하지 않는다.
- 옛 저장 성공을 전제로 하는 Agent/provider 검사149개와 service/recovery 검사63개의 이름·퇴역 이유·대체 책임을 각 retirement JSON에 기록했다. 순수 provider·예산·권한·도구 계약 검사는 유지하고 새 Agent/입력 검증 및 직접 진입 거부 검사로 책임을 옮겼다. DB lookup8개는 공통 catalog만 seed하도록 수정해 실제 조회·감사·권한·버전·원자성을 계속 검사한다. 공통 `_candidate`/`put`·정확한 승인 버전·작성자/검토자 분리·UNKNOWN_TYPE 거부 검사도 유지한다. 실패 목록만 보고 일괄 삭제한 것이 아니다.

### 이번 단계 변경 파일

이 목록은 작업 시작 해시와 비교한 이번 단계 범위다. 앞선 단계부터 남아 있던 Git 수정 전체와 구분한다.

| 구분 | 파일 |
| --- | --- |
| main 원본 복구5개 | `ontologics/model.py`, `ontology/tbox.ttl`, `ontologics/__init__.py`, `pyproject.toml`, `data/catalog.json` |
| B 소스10개 | `examples/order_fulfillment/`의 `b_agent.py`, `b_contract.py`, `b_fulfillment_agent.py`, `b_production_agent.py`, `b_delivery_agent.py`, `b_response_agent.py`, `b_comparison_agent.py`, `b_host_agent.py`, `b_run_workflow.py`, `b_run_isolation.py` |
| Agent 검사11개 | `tests/order_fulfillment/`의 `test_b_agent.py`, `test_b_host_agent.py`, `test_b_fulfillment_agent.py`, `test_b_production_agent.py`, `test_b_delivery_agent.py`, `test_b_response_agent.py`, `test_b_comparison_agent.py`, `test_b_response_provider.py`, `test_b_comparison_provider.py`, `test_b_run_workflow.py`, `test_b_task_agent.py` |
| service 검사8개 | 같은 폴더의 `test_cq03_recovery.py`, `test_delivery.py`, `test_delivery_forecast_service.py`, `test_fulfillment_service.py`, `test_lookup_many.py`, `test_production_service.py`, `test_response_comparison_service.py`, `test_response_plan_service.py` |
| 연결·경계·추출 검사3개 | 같은 폴더의 `test_main_task_connection.py`, `test_b_entry_boundary.py`, `test_schema_extraction.py` |
| 퇴역 검사 파일6개 삭제 | 같은 폴더의 `test_b_typed_workflow.py`, `test_cq01_recovery.py`, `test_cq02_recovery.py`, `test_cq04_recovery.py`, `test_cq05_recovery.py`, `test_cq06_recovery.py` |
| B 문서3개 | 본 설계서, B README, B PROJECT_STATUS |

기존 파일40개 수정·퇴역 검사6개 삭제이며 신규 비무시 파일은 없다. 삭제 전 원문도 before에 보존했다. 기존 노트북·그래프·실제 성공/실패 결과는 재생성하거나 삭제하지 않았다.

### 최종 무료 검증

| 검사 | 결과 |
| --- | --- |
| 실제 복구한 main에서 새7역할·10대표 | 103/103 통과 |
| 최종 전체 unittest discover | 850개: 844통과·실패0·오류0·Docker opt-in 미실행6 |
| 신규 보호 CLI 실제 실행 | 10대표 통과·scripted 모델20호출·실제 API0 |
| 같은 신규 output 중복 실행 | 새 실행 거부·결과73파일 해시 유지 |
| 공통 데모 | v1→v2→v3 승인 흐름 통과 |
| 공통 typed 확장·CQ | model/history 두 예제 및 CQ/SQL15검사 통과 |
| 정적 검사 | Python3.10 AST·메모리 compile203파일 및 git diff --check 통과 |
| 종료·안전 경계 | 소유 CLI/회귀 서버 모두 종료·PID/socket 부재·잔여 사용자 schema0·guard 위반0 |

최초 전체 검사에서 옛 접수 준비 검사의 제거된 Service 참조1개를 발견해 현재 `b_intake_task.make_demo_task` 검증으로 교체했다. 후속 검사에서 B_SCHEMA 속성명 오타1개를 고쳤고 마지막 전체850개는 오류0이다. 중간 실패 로그도 보존했다. 앞 단계의 graph/T-box/옛 typed 버전 실패는 현재 최종 전체 검사에 남아 있지 않다.

Docker 실행 파일과 검토된 immutable image 설정이 없어6개는 실행하지 않았다. ruff/mypy/pyright는 미설치로 실행하지 않았고 의존성을 추가하지 않았다. 새 CLI 결과는 scripted이며 사람 의미 검토 `not_performed`, `live_authorization_eligible=false`, task_validation만 발급한다. paid free_gate·실제 LLM 성공·유료 승인 증거로 사용할 수 없다. 실제 provider·공통 CostPolicy·이번 실행 승인과 사람 검토를 연결하는 작업은 별도 미완료이며 이번 리팩터링의 무료 통과로 대체하지 않는다.

### 보존·종료·실행 안내

시작65519파일 해시와 대조해 위 변경/삭제 허용 목록 밖의 기존 파일이 모두 동일한지 확인한다. 기존 노트북 저장 출력·실제 성공/실패 결과와 개인 자료를 그대로 보존하고, HEAD/index·`.gitignore`·루트 문서를 변경하지 않는다. 새 회귀 cluster는 `/private/tmp/b-final-refactor-2gj5jyuu/pg`, 새 socket/port54884만 사용했으며 종료 코드0·상태 코드3·소유PID60099 부재·socket 부재를 확인했다. CLI가 만든 별도 새 cluster도 종료됐다. 기존 DB/settings/controller/과거 실행 재사용·실제 유료 API·commit/push/PR은0이다.

근거는 Git 제외 `artifacts/local/order_fulfillment/b_final_refactor_20261030_2gj5jyuu/`의 report/preservation·원문 before·테스트 로그·retirement 책임표·신규 CLI 결과·shutdown에 보존한다. DB data directory는 근거 폴더에 복사하지 않는다. 사용법은 B README의 현재 tasks 명령을 따른다. 이미 존재하는 output을 재사용하지 않는다.

## 35. 공통 실제 모델 연결·무료 사전검사 계획 (2026-10-30)

이번 승인 범위는 main README 예제의 고정 모델/출력4096/30초/논리 모델12단계/일시 장애 단계당 최초 포함2시도를 B 실행부에서 조립하고 공통 CostPolicy를 연결한 뒤, 실제 OpenAIProvider에 모의 HTTP를 주입해 무료 검사하는 것이다. 실제 API·키 로드·이번 유료 실행 승인·사람 의미 검토는 수행하지 않는다. 모델12단계와 HTTP최대24시도는 구분한다. 정책/예산이 허용하는 범위에서만 재시도하고 형식·의미·권한 오류는 자동 수정하지 않는다. 기존4/40/1024/retry0 승인은 새 프로필에 사용할 수 없다.

시작 시 main 원본·기존 B 코드/노트북/결과를 포함한65778파일 해시와 B 원문을 /private/tmp/b-model-prep-u5f6j_ix/before에 보존했다. main 공통124파일·모델/T-box·루트 문서·기존 노트북·실제 성공/실패 결과는 변경하지 않는다. 새 격리 PG cluster/별도 test schema만 사용한다.

1. B 전용 연결 helper에서 등록용 SPEC 한도와 공통 OpenAIProvider/ModelConfig/CommonHost/ExecutionBudget/CostPolicy를 조립한다. 기존 무료 역할 SPEC은 바꾸지 않는다. 역할별 최대24모델시도·도구6회·120초를 사용하고, 실행 전체 root는 공통 허용 범위(model64/tool128/deadline600)를 넘지 않는 집계 한도다. 같은 root를 대표 간 공유해 비용·횟수를 초기화하지 않는다.
2. 기본 tasks/legacy 조기 거부는 보존한다. 별도 preflight는 명시적인 비밀 없는 비용 JSON을 검증하고 새 출력에 고정한다. free-only 보호 owner/worker·네트워크 차단·입력/코드 봉인·중복 방지·서버 종료를 재사용한다. 실제 provider를 모의 HTTP로 통과시키되 대역/미수행 의미 검토를 표시한다. 기존 task_validation을 유료 gate로 승격하지 않는다.
3. 성공 summary·서버 종료 뒤에만 새 프로필/정책/입력/코드/summary/shutdown 해시에 묶인 approved:false 준비 기록을 만든다. 이후 실제 실행에는 별도 명시 승인과 사람 검토 연결이 필요하다. 첫 시험 대상은 CQ01 접수 한 대표이며 preflight는 기본 이를 선택한다.
4. 변경 전 기존 연결 회귀를 먼저 실행한다. 새 검사는 실제 Responses payload/도구 이력/모델 설정, timeout·429·5xx 최초 포함2시도, 인증·형식 오류 무재시도, 공통 예약 비용·미보고 유지·정책 누락/부족/불일치의 전송 전 차단, 대표 간 누적, 승인 대기·중복·첫 실패 후 중단·결과/서버 보존을 확인한다. 관련·전체 회귀, 신규 보호 CLI, 정적 검사·독립 읽기 검토를 수행한다. 코드/테스트/실행 결과를 API 성공이나 의미 검토 통과로 설명하지 않는다.

## 36. 공통 실제 모델 연결·무료 사전검사 결과 (2026-10-30)

승인한1~3번 범위를 완료했다. B 전용 연결 helper에서 실제 공통 OpenAIProvider/CommonHost/run_task/ExecutionBudget/CostPolicy를 조립하고, 별도 보호 preflight에서 로컬 Responses 대역으로 검증한다. main 공통 코드/모델/T-box/루트 문서는 이번 단계에서 변경하지 않았다. 실제 호출 명령·유료 실행 승인·대표별 사람 의미 검토 진행은 다음 단계이며 이번 실행은 실제 API0이다.

### 변경 파일과 계약

- 신규 `examples/order_fulfillment/b_model_connection.py`: main 고정 모델/4096출력/30초·논리12단계·일시 장애 최초 포함2시도 설정. 기존 무료 SPEC은 유지하고 새 등록용 limits를 조립한다. host와 모든 대표가 같은 root 비용/호출 예산을 공유한다. 부족한 예약 금액을 설정 변경으로 보정하지 않고 공통 오류로 중단한다. 저수준 execute_case는 신뢰된 호스트 소유자용 연결 함수이며 실제 호출 시 승인/입력 결합/수명 관리는 상위 실행부 책임이다. 이번 CLI는 그 함수에 로컬 opener를 항상 명시한다.
- 신규 `examples/order_fulfillment/b_model_preflight.py`: 정확한 공개 비용2필드·해시/크기/별칭 검사, 실제 provider를 통한 순차 lookup/최종 응답 모의, 미등록 B 입력·카탈로그 DB 불변 확인, 실패/결과 보존. source_text·업무 입력과 모의 모델 결과를 별도 파일에 둔다. CQ06 기본 문안은 Python 작성으로 표시한다. 새 실행이 완료돼도 approved:false 준비 기록만 만든다.
- 기존 B `b_run.py`, `b_run_isolation.py`: preflight 명령과 명시 비용 JSON·free-only 설정 스냅샷을 연결했다. 기본 대상은 CQ01 접수다. 기존 protected owner/worker·네트워크 차단·코드/입력 봉인·새cluster·신규output·실패 중단·서버 종료를 재사용한다. 기존 tasks/legacy 거부는 유지한다. 설정에 승인/gate를 주입해 유료 모드로 바꿀 수 없다. 성공 summary와 소유 서버 종료 뒤에만 preflight_validation·authorization_pending을 생성한다. 후자는 코드/입력/프로필/정책/summary/shutdown 해시에 결합되고 approved:false/live_authorization_eligible:false다. paid free_gate를 만들지 않는다.
- 신규 검사 `tests/order_fulfillment/test_b_model_connection.py` 11개, `test_b_model_preflight.py`13개. 기존 검사는 삭제하거나 기대값을 바꾸지 않았다. B 문서3개(README/본 설계서/PROJECT_STATUS)를 갱신했다. 이번 단계 총 기존5파일 수정·신규4파일이다.

### 무료 검증 결과

| 범위 | 결과 |
| --- | --- |
| 기존 B7역할·10대표 핵심 | 103/103 통과 |
| 새 연결/정책/재시도 및 관련 회귀 | 176/176 통과 |
| 최종 전체 unittest discover | 874개: 868통과·실패0·오류0·Docker opt-in 미실행6, 71.781초 |
| 새 보호 preflight CLI10대표 | 실제 공통 provider·로컬 HTTP40시도·실제 API0·실제 도구 조회/근거 통과 |
| 금액 상한 부족 CLI | 첫 HTTP 대역도0회·COST_BUDGET_EXCEEDED 결과 보존·성공/승인 대기 marker 미발급·서버 종료 |
| 동일 output 중복 CLI | 새 실행 거부·기존74결과 파일 해시 유지 |
| 기존 기본 tasks CLI | scripted 경로·task_validation 유지·승인 대기 marker 없음 |
| 정적/독립 검토 | Python3.10 AST/메모리 compile207파일·git diff --check 통과·독립 읽기 검토 차단 사항 없음 |

timeout·429·503는 최초 포함2시도, 반복 장애는2시도 후 중단, 401·형식/최종 응답 오류는 재시도 없음, 키/비용/정책/설정 불일치는 opener 이전 차단을 검사했다. 단순 요청 수뿐 아니라 미보고 예약 유지·재시도 비용·대표 간 같은 ledger의 누계·허용 도구/근거·업무 그래프/승인 불변을 확인했다. common ledger의 paid_calls/report cost는 합성 응답으로 검사한 내부 카운터이며 실제 청구가 아니다.

기본 sandbox가 PostgreSQL 공유 메모리/Unix socket까지 차단해 최초 시작/검사 명령이 환경 오류를 냈다. 허용된 새 cluster만 대상으로, 외부 네트워크/실제 .env 읽기를 코드에서 차단한 검증기를 사용해 다시 검사했고 위 최종 검사는 모두 통과했다. 초기 환경 오류 로그도 보존한다. 기존 DB나 환경을 대체 사용하지 않았다. 이번 실제API·키 로드·운영 승인·사람 의미 검토·commit/push/PR0이다.

### 보존과 한계

새 회귀 cluster `/private/tmp/b-model-prep-u5f6j_ix/pg`·port54885·별도 test schema만 사용했다. 보호 preflight 정상/금액 부족/기존tasks 세 실행의 신규 서버와 회귀 서버는 모두 종료하고 소유PID/socket 부재·잔여 회귀 사용자 schema0을 확인한다. 시작65778파일 해시와 대조하여 위 변경 허용 목록 외 기존 파일/노트북/과거 실제 성공·실패 결과와 HEAD/index가 그대로인지 확인한다. main 추적124파일은 의도된 .gitignore를 제외한123파일이 계속 원본과 같다.

대역은 실제 모델이 JSON을 준수하거나4096토큰 안에서 응답을 마치거나 한국어 답변을 정확하게 작성한다는 증거가 아니다. 사람 검토를 자동 작성/통과하지 않는다. 원래 CQ01 미등록 후보를 업무 승인 완료나 확정 저장으로 표시하지 않는다. role24시도·전체 공통 상한64시도/128도구·시간·명시 USD 한도가 먼저 소진되면12단계 전에 중단할 수 있다. 모의 검사에 사용한 가격/금액은 합성 시험 설정이며 운영자 예산으로 승인된 값이 아니다. Docker6 및 미설치 ruff/mypy/pyright는 미실행이고 의존성을 추가하지 않았다.

근거는 Git 제외 `artifacts/local/order_fulfillment/b_model_preflight_20261030_u5f6j_ix/`에 원문 before·회귀 로그·정상/실패 CLI 결과·승인 대기·정책 스냅샷·종료/보존/report를 보존한다. DB data directory는 복사하지 않는다. 다음 범위는 현재 준비 기록을 실제 유료 승인으로 재사용하지 않고, 운영자 금액 상한·새 실행별 명시 승인·대표별 의미 검토를 결합한 CQ01 한 대표 실제 시험이다.

## 37. 실제 실행 승인·대표별 검토 연결 계획 (2026-10-30)

이번 범위는 B 실행부 구현과 무료 통합 검증(1~2번)이다. 실제 API·실제 키 읽기·실제 사람 검토 성공은0으로 유지한다. main 공통/model/T-box/루트 문서·기존 노트북과 성공/실패 결과는 수정하지 않는다. 시작 상태와 기존 B 수정 대상5파일은 새 `/private/tmp/b-model-run-e3wgz6p1/`에 보존한다.

1. 기존 공통 모델 연결/CostPolicy/무료 preflight를 재사용한다. B 전용 plan-model/run-model 명령을 추가하고 source/input/policy/profile/cases/새 output/완료된 무료 근거에 실행 승인을 결합한다. 실행 계획 생성은 무료이며 승인 기본값은false다. 모의 실행은 항상 로컬 opener이고 실제 모드는 승인 후에만 제한된 키/전송 창을 연다. 옛 저장형 live/free 진입점은 되살리지 않는다.
2. 대표마다 실제 결과·핵심 근거·원본 자료 구분을 검토 입력 바로 위에 보여준다. 상세 JSON은 파일로 분리한다. 자동 검사 실패/검토 거절/입력 종료/예산 소진은 후속 호출을 중단한다. 같은 프로세스의 공통 ledger/deadline을 유지하며 검토 대기 중에도 예산을 초기화하지 않는다. 중복 launch는 원자적 claim으로 막고 실패한 실행은 재개하지 않는다.
3. CQ01 의미 검토와 정확한 미등록 후보 버전/hash에 대한 별도 업무 검토를 구분한다. 작성자와 검토자를 분리한다. 이 기록은 로컬 개발 예제의 미등록 후보 검토이며 공통 DB 초안 승인/확정 저장을 만들거나 대체하지 않는다. CQ06 Python 문안도 그대로 표시한다. 실제 순서는 CQ01접수1개 → 통과한 실제 결과/사람 검토/서버 종료 확인 → 나머지9개이며 모의 증거를 실제 CQ01 통과로 사용할 수 없다.
4. 변경 전 기존 관련 검사를 보호하고 새 테스트는 승인 누락/변경/교차 모드 거부, 무료 근거 변조, 실제 키/네트워크0, 10대표 모의 실행과 대표 사이 검토, CQ01 stale/self 승인 거부, 실패/검토 거부/EOF 후 중단, 공유비용 누적, 중복 결과 보존, 신규 서버 종료를 확인한다. 전체 회귀와 정적 검사를 마친다. 모의 검토 입력은 test fixture로 표시하며 사람 의미 검토로 보고하지 않는다. 추가 의존성/자동 PR/커밋/푸시 없음.

## 38. 실제 실행부 구현·무료 통합 검증 결과 (2026-10-30)

승인한1번 구현과2번 무료 검증을 완료했다. 실제 유료 실행(3번)은 수행하지 않았다. 실제 API/키 읽기는0이며 모의 검토 입력은 모두 합성 시험 자료다. main 공통 코드·model.py·T-box·루트 PROJECT_STATUS·기존 노트북/출력은 이번에도 변경하지 않았다.

### 변경과 실행 흐름

- 신규 `examples/order_fulfillment/b_model_run.py`: 현재 source/input/정책/고정 프로필/사례/새 실행 경로/완료된 무료 근거를 plan fingerprint에 묶는다. 준비는 approved:false이며 별도 콘솔에서 이번 실행 설정과 금액을 보여준 뒤 명시 승인한다. 승인·검토 내용을 모델 출력이나 과거 실행에서 가져오지 않는다. 기본 mock_http와 실제 openai 승인은 교환할 수 없다.
- 기존 `b_run.py`·`b_run_isolation.py`: B 전용 plan-model/run-model을 추가했다. legacy 거부와 tasks/preflight 경로는 유지한다. 준비 단계는 서버·키·호출이 없고, 승인 후 원자적 launch claim을 남긴 뒤 새 cluster/별도 schema/제한된 owner·worker를 사용한다. 실제 경로의 키 로드는 고정 `.env.local`만, 전송은 승인된 endpoint/IP/443 창에서만 허용한다. 모의 경로는 환경/네트워크를 열지 않는다. subprocess는 비밀 없는 최소 환경을 사용한다.
- 대표마다 input/result/review_packet/semantic_review를 분리한다. 자동 검사 뒤 답변·작성 주체·핵심 근거·자료·누적 사용량을 표시하고 직접 검토를 받는다. CQ05 초안/CQ06 비교는 상세 JSON 대신 핵심 자료와 원문 파일 위치를 보여준다. CQ01은 의미 검토와 정확한 미등록 후보 버전/hash의 업무 검토를 나누며 자기 승인을 거부한다. 공통 DB 저장형 승인을 복제하거나 도입하지 않았다.
- 실제 선택 순서는 CQ01접수1개 → 사람 검토·서버 종료 → 나머지9개다. 이전 CQ01의 계획/승인/결과/검토 hash·mode·synthetic flags를 함께 확인하므로 모의 결과나 표시만 바꾼 결과를 실제 선행 근거로 사용할 수 없다. 승인 거절은 서버 시작 전, 자동 검사/대표 검토 실패·EOF는 다음 호출 전 중단한다. 모든 대표에서 같은 원장·deadline을 쓰고 실패 실행은 자동 재개하지 않는다.
- 신규 `tests/order_fulfillment/test_b_model_run.py`19개. 공통 provider/정책 기존 검사는 그대로 재사용했다. B README·본 설계서·B PROJECT_STATUS를 갱신했다. 이번 총 기존5파일 수정·신규2파일이며 추가 의존성은0이다.

### 무료 검증

| 범위 | 결과 |
| --- | --- |
| 변경 전 B 핵심 회귀 | 103/103 통과 |
| 최종 연결·승인·검토 관련 | 195/195 통과 |
| 최종 전체 unittest discover | 893개: 887통과·실패0·오류0·Docker opt-in 미실행6, 94.059초 |
| 보호 CLI10대표+검토 | 모의 HTTP40회·의미 검토10건/별도 CQ01업무 검토1건 모두 합성 입력·실제 API0 |
| 보호 CLI CQ01→나머지9 | 각각 모의4/36회·앞 실행 종료 뒤 후속 계획/검토 통과 |
| 승인 거절·교차 모드 | 실제 모드 계획에서 승인 취소 시 키/서버/호출0; 모의 CQ01은 실제9개 선행 근거로 거부 |
| 검토 거절·EOF | 첫 결과 보존·다음 대표 호출0·완료 marker 없음·서버 종료 |
| 중복 실행 | 시작 전 거부·기존 비DB 결과137파일 해시 유지 |
| 정적·독립 검토 | Python3.10 AST/메모리 compile209파일·diff check 통과·최종 독립 검토 차단 사항 없음 |

초기 검토에서 발견한 profile tuple/list 직렬화 비교, 이전 CQ01 근거의 결합 검증 부족, 부모 input 버퍼의 자식 검토 입력 선취를 수정했다. 마지막 관련/전체/보호 CLI는 모두 위 최종 코드에서 통과했다. 첫 sandbox Unix socket/shared-memory 제한과 `/var` 임시 경로 별칭 오류는 환경 로그로 보존하고 새 전용 cluster·정규 임시 경로/가드로 재검증했다. 외부 API/기존 환경·DB로 우회하지 않았다.

### 보존·종료·남은 한계

시작45464파일을 해시로 대조한다. 범위는 노트북5·기존 artifacts 비DB20923·JunhwaDocs104와 코드/자료이며 `.git/.venv/.omx/node_modules/bytecode/환경파일/pgdata*/symlink`는 제외했다. 실제 성공·실패 기록을 합치거나 삭제/새 성공으로 승격하지 않았다. 위 수정5파일 외 기존 파일은 동일하며 main 추적124개 중 의도된 .gitignore를 제외한123개는 원본과 동일하다. HEAD/index·루트 문서·기존 노트북은 그대로다.

새 회귀 cluster `/private/tmp/b-model-run-e3wgz6p1/pgdata`·port54887·PID91352는 잔여 사용자 schema0 확인 뒤 종료했다. 새 보호 CLI 서버6개와 합쳐7개 모두 소유 PID/postmaster/socket 부재·종료를 확인했다. 기존 서버·DB·settings/controller는 실행 대상으로 재사용하지 않았다. commit/push/PR0이다.

실제 HTTP·실제 키 로드·실제 모델의 JSON/출력 토큰 적합성·한국어 품질은 미검증이다. 공통 ledger의 모의 paid_calls/토큰/USD 값은 합성 회계이며 실제 청구가 아니다. 로컬 검토자 ID는 운영 인증이 아니다. 공통600초 deadline은 사람 검토 대기까지 포함하므로 시간 부족 시 새 호출을 중단하며 한도를 늘리거나 초기화하지 않는다. Docker6와 미설치 ruff/mypy/pyright는 미실행이다. 기존 노트북 UI를 새 유료 UI로 덮어쓰지 않았으며 현재 진입점은 B CLI다.

근거는 Git 제외 `artifacts/local/order_fulfillment/b_model_run_20261030_e3wgz6p1/`에 원문 before·회귀/CLI 로그·계획/모의 승인/검토·실패/보존/종료/report를 보존한다. raw DB 파일은 복사하지 않는다. 다음은 운영자가 확인한 비용 정책과 이번 실행 명시 승인으로 CQ01접수1개를 실제 시험하고, 자동 검사와 별도 사람 검토를 확인하는3번 단계다.
# CQ01 첫 실제 호출 이후 보완 계획·결과 (2026-10-30)

- 이전 실제 실행 `b_cq01_live_20261030_nwe4hg10`은 API1회/HTTP200 뒤 `PINNED_CONTEXT_MISMATCH`로 중단했다. 추정 비용은 $0.00181725이며 원래 결과와 종료 증거를 보존한다. 당시 정확한 조회 인자는 기록되지 않아 누락/다른 값 중 어느 것인지는 미확정이다.
- B 접수 지침에 호스트의 실제 `data_version`으로 만든 순차 lookup 인자를 전달한다. 고정 숫자1로 대체하지 않으며 공통 문맥/검증/도구 스키마는 수정하지 않는다. B 출력 필드·후보 필드도 명시해 기존 검증 계약을 모델이 읽을 수 있게 한다. 정답 후보는 전달하지 않는다.
- B 연결부는 기존 공통 provider를 그대로 호출하고 반환된 도구 인자만 분리 기록한다. 인자 교정/주입/자동 재시도/승인은 추가하지 않는다. 비밀키·헤더·원시 HTTP 응답은 기록하지 않는다.
- 회귀 범위: 실제 문맥의 다른 번호도 전달, 입력 변조 거부, 정상 lookup3회+최종 응답, 누락/null/문자열/다른 번호 거부와 정확한 실패 인자 기록, 기존 검토·중복·실패 중단·한도·종료 흐름.
- 무료 회귀와 새 보호 preflight가 통과하면 사용자가 승인한 새 CQ01 접수1대표를 총 $0.50 상한으로 실행한다. 이전 실패 실행/DB는 재개하지 않는다. 의미 검토·정확 후보 업무 승인은 사람이 직접 한다. main 공통 코드·model.py·T-box·기존 노트북은 수정하지 않는다.

결과: B 역할1.0.1과 반환 인자 기록을 보완했다. 관련197개 통과. 전체895개 최초 검사에서 원문 메시지를 위치0으로 가정한 B 검사1개가 실패해 사용자 역할의 유일한 원문으로 대조하도록 보완했다. 새 격리 서버에서 최종 전체895개는889통과·실패/오류0·Docker6제외이며 두 회귀 서버 모두 종료됐다. 새 보호 preflight도 통과/종료했다. main123개는 원본과 같고 이전 실제 증거134개도 동일하다.

새 실제 실행은 API4회 모두HTTP200이다. ProductModel/ProductRevision/Configuration 조회3회가 모두 version1로 통과해 이전 조회 버전 문제는 해소됐다. 마지막 응답은 JSON/필드·data_version 검사 뒤 공통 근거 선택 검사 `AGENT_EVIDENCE_MISMATCH`로 중단했다. 이 검사는 근거 목록의 자료형/중복/미조회 ID를 거부하지만 반환된 최종 목록 자체는 공통 기록에 남지 않아 상세 원인을 특정하지 않는다. B 후보 내용 대조·사람 의미/업무 검토에는 도달하지 않았다. 반환 조회 인자는 기록됐으며 다음 관측 보완은 거부된 최종 답변/근거 목록이다. 추가 유료 재실행은 하지 않았다.

이번 비캐시 단가 추정$0.01224150, 직전$0.00181725와 합계$0.01405875다. 새 실행 한도는 대표당$0.50에서 직전 비용을 뺀$0.49818275였다. 사용량 미보고 예약/잔여0이며 서버 종료·PID/socket 부재를 확인했다. 무료 성공과 실제 업무 성공을 구분한다. 변경 파일은 B 지침/연결2·B 테스트3·B 문서2이며 원문은 Git 제외 `artifacts/local/order_fulfillment/b_cq01_fix_20261030_iyz9xyiu/`에 보존한다.


## 39. CQ01 근거 구분·최종 답변 관측 보완 — 수정 전 계획 (2026-10-30)

- 사용자 승인 범위는 B 연결부의 거절된 최종 응답 기록, CQ01 근거 지침 보완, 무료 회귀 검증이다. 유료 호출·실패 실행 재개·공통 코드 수정은 하지 않는다. 시작 HEAD f264955와 기존 미커밋 변경을 보존한다.
- `b_model_connection.py`는 공통 provider가 반환한 정규화 text를 도구 인자와 함께 분리 기록한다. JSON 파싱 전 원문을 보존하며 공통 검증 결과를 바꾸거나 근거를 자동 교정하지 않는다. 키·헤더·원시 HTTP 본문은 수집하지 않는다. 기존 실행 파일은 덮어쓰지 않는다.
- `b_intake_task.py`의 역할 지침/메시지만 보완하고 역할 버전을 갱신한다. 후보 노드의 evidence는 입력의 evidence_ids_by_part, 최종 evidence_ids는 성공한 실제 lookup 응답 evidence의 문자열·중복 없는 정렬 합집합으로 구분한다. 공통 envelope·OUTPUT 필드·도구 권한·고정 버전·B 대조 검증기는 유지한다.
- 기존 회귀에 최종 원문 보존, 미조회/가상/중복/비문자열 근거 거부, 실제 근거 일부 누락에 대한 B 거부를 추가한다. 정상 응답의 후보 근거와 공통 EvidenceRef가 분리되는지 확인한다. 먼저 기존 기록 누락 검사의 실패를 확인한 뒤 수정한다.
- 새 PostgreSQL cluster와 매 검사별 별도 schema에서 HTTP 대역만 사용한다. 기존 DB·환경 파일·키·외부 통신 접근을 차단한다. 관련/전체 unittest와 Python 3.10 문법·diff 검사를 수행하고, main 원본·기존 결과/노트북 hash 보존과 새 서버 종료를 대조한다. 무료 통과를 실제 LLM 성공으로 보고하지 않는다.

結果: B 接수 역할을1.0.2로 갱신하고 후보 원문/실제 조회 근거의 출처·배열 형식·중복 제거 규칙을 분리했다. 공통 provider가 반환한 정규화 text를 B 기록의 model_text에 보존하며 JSON/근거 검증 전에 기록한다. 원시 HTTP·키·헤더는 수집하지 않고 응답/근거를 자동 교정하지 않는다. OUTPUT·도구·공통/B 검증기는 유지했다.

수정 전199개 검사에서 새 안내/기록 필드의 누락8건(서브케이스 포함)을 확인했다. 수정 후 입력9개·관련199개가 통과했고, 전체897개는891통과·실패/오류0·Docker opt-in6제외다. 미조회/가상 계약/중복/비문자열 근거는 공통 AGENT_EVIDENCE_MISMATCH, 읽은 근거 일부 누락은 B AGENT_RESULT_REJECTED로 차단되며 payload=null과 실패 원문 기록·추가 호출 없음·repair0을 확인했다. 정상 후보의 가상 원문 근거와 공통 EvidenceRef 분리도 확인했다. 기존 대표별 검토·중복 실행·실패 후 중단·보존·종료 검사도 통과했다.

Python3.10 문법/compile과 공백 검사 통과. main123개 원본·기존 증거273개·노트북4개는 그대로이며 새 회귀 서버2개는 모두종료/PID·socket 부재를 확인했다. 기본 sandbox의 최초 initdb 시도는 공유 메모리 권한 부족으로 서버 시작 전에 실패했고, 새 별도 디렉터리에서 격리 검사를 수행했다. 기존 DB/환경·키·외부 통신 접근 차단 위반0, 실제 유료 API0이다. 변경 파일은 B 코드2·테스트2·문서2이며 기록은 Git 제외 `artifacts/local/order_fulfillment/b_cq01_evidence_20261030_uxw40tfj/`에 둔다.

한계: 기존 실제 실패의 최종 원문은 복구되지 않는다. model_text는 공통 provider 정규화 반환에 도달한 응답을 기록하며 그 이전 전송/파싱 실패의 원시 응답을 수집하지 않는다. 무료 HTTP 대역 통과는 실제 LLM 성공이 아니다. 새 실제 CQ01 시험·사람 의미 검토는 이번 범위에서 수행하지 않았다. 코드가 바뀌었으므로 후속 실제 시험에는 새 무료 사전검사·계획·이번 실행 승인 결합이 필요하다.

## 40. CQ01 접수 새 실제 시험 — 근거 통과·출처 해시 오복사 중단 (2026-10-30)

사용자의 새 실행 승인으로 새 무료 preflight를 통과/종료한 뒤 새 CQ01 접수1대표를 실행했다. 기존 실패/DB/계획을 재개하지 않았다. 대표 누적$0.50에서 이전 추정$0.01405875를 뺀$0.48594125를 이번 실행 한도로 고정했다. 모델/4096토큰/30초/공통 비용·권한·검증 정책은 유지했다.

실제 API4회 모두HTTP200. ProductModel/ProductRevision/Configuration의 version1 조회와 최종 JSON·고정 버전·공통 근거 선택 검사를 통과했다. B validate_result가 AGENT_RESULT_REJECTED를 반환해 후속 호출 없이 중단했다. 이번에는 model_text로 실제 최종 답변을 보존했다. 입력·deterministic 추출·성공한 실제 도구 기록으로 만든 기대 payload와 전 필드를 대조한 유일한 차이는 input_sources[0].source_sha256 한 글자 오복사다. 계약 후보·수량·납기·한국어 답변·실제 조회 근거는 대조상 일치한다. 잘못된 해시를 자동 교정하거나 사람 검토를 통과시키지 않았다.

이번 단가 기반 추정$0.01257750, 앞선 두 시험과 누적$0.02663625다. invoice가 아니며 캐시 할인을 적용하지 않은 공통 설정 단가 계산이다. 사용량 미보고/진행 중 예약0, reservation_exceeded=false다. 공통/B 코드 변경과 추가 유료 재시험은 하지 않았다. 의미 검토·정확 후보 업무 승인에 도달하지 않았으므로 CQ01 전체 성공으로 표현하지 않는다.

새 preflight/live 서버2개 모두 pg_ctl status3·소유 PID 부재·socket 부재·종료 기록을 확인했다. 실행 전 기존591파일·증거299개·노트북4개와 main123개는 동일했다. 보고 문서2개만 추가 기록한다. 키는 승인된 보호 실행부에서만 읽었고 비밀/원시 HTTP는 수집하지 않았다. 로그·필드 차이·원문·종료/보존 기록은 Git 제외 `artifacts/local/order_fulfillment/b_cq01_citations_live_20261030_48lq_d_8/`에 둔다.

다음 검토 대상은 B에서 LLM이 업무 후보를 작성하는 책임과, 보존된 입력 해시·출처를 실행부가 연결하는 책임의 분리다. 기존 common run_task/근거 검증을 유지하고, 잘못된 모델 응답을 자동 교정하는 방식과 구분해야 한다. 구체적 계약/무료 회귀 검토 전에는 이 연결을 바꾸지 않는다.

## 41. CQ01 불필요한 복사·문구·근거 배열 제약 축소 — 수정 전 계획 (2026-10-30)

사용자 지침은 main 필수 기준 이외의 B 제약을 과도하게 적용하지 않는 것이다. 이번 수정은 CQ01의 실패 원인과 직접 관련된 세 조건으로 한정하며 실제 유료 호출은 하지 않는다. 공통 코드·공개 AgentResult/OUTPUT·원본/과거 실제 증거를 유지한다.

1. 기존 공통 run_task의 역할 decode_result 훅에서 B 입력 provenance(input_revision/input_sha256/input_sources)를 검증된 고정 Task에서 결합한다. 모델의 업무 출력 schema는 이 세 필드를 제외하고 기존 B 공개 OUTPUT에는 계속 포함한다. 옛 형식의 모델 provenance가 있어도 권위 있는 출처로 사용하지 않는다고 명시하고, 원래 모델 text는 기존 B 기록에 보존한다. 업무 후보·근거 ID·버전·승인 상태는 자동 교정하지 않는다. 역할 버전을1.1.0으로 갱신해 옛 실행의 자동 재개를 막는다.
2. answer_ko의 고정 문장 완전 일치 대신 비어 있지 않은 설명을 받으며 별도 사람 의미 검토를 유지한다. 조회한 모든 근거의 정렬 합집합 강제를 제거하고 공통 호스트의 실제 읽은 근거 내 선택/자료형/중복 검증을 재사용한다. 실제 필요한 카탈로그 조회 대조와 수량·납기·가상/미등록/검토 필요 상태 검사는 유지한다. 다른 CQ는 이번에 일괄 변경하지 않는다.
3. 새 회귀로 입력 provenance의 모델 복사 불필요·옛 오복사 값의 비권위 처리·모델 원문 보존·자연스러운 답변 변형·공통 허용 근거 부분집합/순서 변경 통과를 확인한다. 잘못된 업무 후보/미조회·중복·가상 근거 거부와 사람 검토·실패 중단·중복·보존·종료를 유지한다. 수정 전에 새 decoder 회귀의 실패를 확인하고 새 cluster/매 검사별 schema로 관련·전체 무료 회귀와 정적·원본/증거 보존·서버 종료를 확인한다.

결과: B 접수 역할1.1.0에 모델 업무용 MODEL_OUTPUT과 기존 공개 OUTPUT의 책임을 분리했다. 기존 공통 decode_result 훅에서 고정 입력의 provenance3필드를 결합하며 모델의 옛 복사값은 출처 정보로 쓰지 않는다. raw model_text는 그대로 기록한다. 업무 후보/버전/근거/상태를 자동 교정하지 않는다. 고정 한국어 문장·전체 근거 정렬 합집합 조건을 제거했다. 사실상 수량·납기·가상/미등록·검토 필요 상태 대조와 공통 근거/권한/한도 검사는 유지한다. 다른 CQ를 일괄 완화하지 않았다.

수정 전 새 decoder 회귀1개에서 미구현 오류2건(서브케이스)을 확인했다. 수정 후 입력10개·관련202개 통과, 전체900개는894통과·실패/오류0·Docker opt-in6제외다. 모델 provenance 생략/옛 잘못된 복사값 비권위 처리와 원문 보존, 자연스러운 한국어 문장, 공통 허용 근거의 부분 선택/순서 변경을 통과시켰다. 미조회/가상/중복/비문자열 근거·잘못된 수량/납기·미확인 은폐·거짓 확정 상태는 계속 거부하며 사람 검토·중복·실패 중단·저장 보존 회귀도 통과했다.

Python3.10 문법·compile·공백 검사 통과. main123파일·기존 증거412개·노트북4개 동일이며 새 cluster/개별 schema만 사용했다. 검증 서버1개 종료·pg_ctl status3·PID/socket 부재, 기존 환경/키/외부 통신 차단 위반0·실제 유료 API0이다. 변경은 B 코드1·테스트2·문서3이며 무료 원문/보존 대조는 Git 제외 `artifacts/local/order_fulfillment/b_cq01_relax_20261030_fq24wabq/`에 둔다.

기존 실제 실패를 성공으로 다시 표시하지 않았고 새 실제 호출도 하지 않았다. 자유 한국어 설명은 사람 의미 검토 전 사실성 통과로 간주하지 않는다. 전체 B 출력의 형식 제약을 모두 검토/변경한 것은 아니며 이번 범위는 CQ01의 세 추가 제약이다. 다음 실제 실행은 변경된 코드에 맞춘 새 무료 사전검사·계획·이번 실행 승인으로 시작한다.

## 42. 나머지 CQ01 약속~CQ06의 B 추가 제약 축소 — 수정 전 계획 (2026-10-30)

사용자가 나머지 CQ도 main에 맞추면서 불필요한 제약을 완화하도록 요청했다. CQ01 약속/CQ02~06 여섯 역할을 조사한 결과 모델 provenance 재복사·전체 근거 정렬 합집합 강제, CQ01 약속/CQ02~04 한국어 regex/2000자, CQ02~05 옛1024토큰 프롬프트, CQ05 고정 단어/문구 regex, CQ06 Python 문안 정확 재복사와 중첩 해시 복사가 남아 있다.

- B 기존 input helper에서 고정 입력 필드 결합과 모델용 messages를 재사용한다. 여섯 역할을1.1.0으로 갱신하고 모델용 schema에서 상위 provenance를 제외한다. 공개 OUTPUT/envelope·공통 모델/신원/권한/도구/예산/승인 코드는 유지한다. 실제 문맥의 lookup 인자·모델 출력 필드를 명시한다.
- CQ05 assessment.review_material_sha256, CQ06 assessment.review_material_sha256와 before/after의 input_revision/input_sha256는 고정 Task에서 결합한다. 업무 수량·시각·조건·delta·미확인은 모델이 바꾸면 계속 거부한다. CQ06 answer_ko는 Python 원본에서 직접 조립하여 기존 answer_producer=python을 유지한다. 모델의 임의 문구를 Python 작성으로 표시하지 않는다. 원래 모델 text와 기존 실제 성공/실패 기록은 보존한다.
- 비어 있지 않은 설명을 허용하고 글자 수/한국어 regex/고정 단어/문구 일치·옛1024 지침을 제거한다. 한국어 의미와 근거의 해석은 기존 별도 사람 검토를 유지한다. 읽은 근거 중 선택/순서는 공통 selected 검사를 재사용하며 미조회·중복·다른 버전/가상 근거를 승격하지 않는다.
- 기존 business 회귀를 먼저 보존하고 provenance/문구 검사만 새 책임에 맞춰 수정한다. 중앙 B HTTP 대역 검사에서 나머지9대표의 metadata 생략/잘못된 옛 복사값·중첩 해시·자연스러운/긴 문장·근거 부분집합/순서·모델 원문 보존을 확인한다. 잘못된 수량/납기/조건/미확인/실행 상태·버전·권한/예산·중복·실패 후 후속 호출 중단·사람 검토·종료는 유지한다.
- 새 cluster와 검사별 schema에서 관련·전체 무료 회귀를 실행한다. 기존 DB/환경/키/외부 통신은 차단하고 main123개 원본·보존 파일/노트북 hash와 새 서버 종료를 확인한다. 실제 유료 API·기존 실행 재개·원격 push/PR0이며 무료 결과를 새 실제 성공으로 표시하지 않는다.


결과: 나머지6역할/9대표의 모델 업무 출력과 B 메타데이터 연결 책임을 분리했다. 공개 INPUT/OUTPUT과 공통 코드는 유지하고 역할 버전1.1.0을 적용했다. B 공용 입력 helper를 재사용해 고정 입력의 출처와 CQ05/06 중첩 메타데이터를 결합한다. CQ06 Python 문안은 B가 원본에서 직접 연결한다. 한국어 regex·2000자·특정 단어·전체 근거 정렬/합집합·옛1024토큰 지침을 제거했으며 비어 있지 않은 설명과 기존 업무 값 대조는 유지했다. 잘못된 수량/시각/조건/미확인/실행 상태를 고치거나 승인하지 않는다. 자유 문장의 의미는 기존 별도 사람 검토 대상이다.

검증: 새 PostgreSQL cluster와 검사별 격리 schema에서 관련205/205통과, 전체903 중897통과·Docker opt-in6 skip·실패/오류0. 중앙 HTTP 대역에서 나머지9대표의 잘못된 옛 복사값·중첩 해시를 B 입력으로 연결하고 긴 설명·일부/역순 근거 선택을 허용하는 한편 미조회 근거를 공통 검사가 거부함을 확인했다. 모델 원문 기록, 업무 불변/권한/비용/승인 대기/검토/중복/실패 중단 회귀를 함께 실행했다. 변경 Python14파일의 Python3.10 AST/compile 검사와 변경분 공백 검사를 수행했다. 전용 lint/typecheck 도구는 설치·설정되어 있지 않아 추가하지 않았다.

main123파일 원본 일치, 기존 결과426파일·노트북4개 hash 보존, 새 서버1개 종료(status3·pid/socket 없음)를 확인했다. 외부 통신·이전 DB·실제 환경파일 접근 위반0, 실제 유료 API0. 기존 실제 실패는 그대로이며 이 결과는 무료 대역 검증이다. 자료: Git 제외 `artifacts/local/order_fulfillment/b_remaining_cq_relax_20261030_jcr6yk9k/verification.json`과 관련/전체 검사 로그.


## 43. B 제약 완화 후 CQ01 접수 새 실제 시험 — 자동 검사 통과·사람 검토 미완료 (2026-10-30)

사용자가 새 CQ01 실제 시험 계획 뒤 `ㄱㄱ`로 승인했다. 이전 실패 실행은 재개하지 않고 새 무료 preflight·새 계획·단회 launch를 사용했다. 누적 $0.50에서 이전 추정 $0.02663625를 빼 이번 요청 CostPolicy 상한을 $0.47336375로 고정했다. 모델 gpt-5.4-mini-2026-03-17·출력4096·요청30초·공통12모델 단계/일시 장애 최대2시도·repair0 설정이며 실제 재시도는 없었다. 공통 코드나 B 실행 코드는 바꾸지 않았다.

결과: 무료 사전검사 통과·서버 종료 후 실제 HTTP4회(전부200)를 실행했다. 제품/개정/구성 조회는 확정 카탈로그 버전1을 사용했고 최종 `runtime.ok=true`, `error=null`, B 후보 `status=ready`였다. 한국어 답변과 실제 공통 근거를 저장했다. 모델은 input_revision/input_sha256/input_sources를 출력하지 않았고, B가 입력의 정확한 값을 연결했다. 이전 출처 해시 오복사 문제를 재현하지 않았다. 계약 B-01·DEMO-B·가상고객사 A·ESP32-POE L WROOM·1000 piece·고객 도착 기한2026-10-27T15:00:00+09:00의 미등록 후보3노드다.

이번 추정 비용 $0.01193400, 이전 포함 $0.03857025/$0.50이며 configured uncached token rates 기준으로 실제 청구서 금액이 아니다. 반환 토큰 사용량4건을 모두 기록했고 미보고/예약 초과0이다. 추가 대표 호출·유료 자동 재실행·자동 사람 검토/승인0이다.

자동 검사 통과 뒤 실제 답변·원문·정확한 후보v1/hash를 제시하고 한국어 의미 검토와 별도 후보 업무 승인 의견을 요청했다. 실행600초 한도까지 사람이 작성한 입력을 받지 못해 EOF로 입력을 닫고 소유 서버를 종료했다. `campaign/stopped.json`의 EOFError는 검토 입력 미완료에 의한 종료이며 LLM/자동 검사 오류로 분류하지 않는다. 두 사람 검토 기록·전체 `model_validation.json`은 생성하지 않았다. 따라서 실제 연결·자동 검사 통과이며 전체 업무/사람 검토 완료 또는 나머지9대표 실제 성공은 아니다. 과거 실제 실패 기록도 그대로 유지했다.

새 preflight/live 서버2개 모두 pg_ctl status3·소유pid 없음·postmaster.pid/socket 없음·cleanup 오류0을 재확인했다. main123개 원본·기존 결과457파일·노트북4개를 보존했다. 점검한 기존749파일 중 B 문서3개만 변경했으며 나머지746개는 hash가 같다. 변경은 B 전용 기록/안내뿐이며 모델/공통 코드·환경·기존 노트북·과거 결과는 수정하지 않았다. Git 제외 원문/사용량/승인/중단/종료 근거: `artifacts/local/order_fulfillment/b_cq01_relaxed_live_20261030_g_fhtdpo/`.


## 44. 나머지9대표 실제 연결 시험·별도 AI 의미 검토 — 실행 전 계획 (2026-10-30)

사용자가 AI 의미 검토/사람 승인 미완료 방식 제안 뒤 나머지9대표 실행을 재확인했다. main은 그대로 두고 B 계획에 선택적인 review_mode=ai_semantic을 결합한다. 기본 사람 검토 경로는 유지한다. 이전 실제 CQ01은 자동 통과·승인·입력/결과 hash·실제 요청·소유 서버 종료를 기술 선행 증거로 확인하고, 없는 사람 검토·완료 파일은 만들지 않는다. runner 수정으로 현재 코드 hash가 달라지므로 옛 계획 자체 hash와 보존된 실제 결과를 확인하고 새 코드용 무료 preflight를 다시 실행한다. 이전 파일을 수정하거나 CQ01을 다시 호출하지 않는다.

모델/공통 공개 계약·권한·실제 조회/업무 값 검증·공통 비용 ledger·격리 키/네트워크 창·새 PG/schema/종료를 재사용한다. 대표당$0.50, 공유 전체$4.50이며 각 실제 send 직전 공통 ledger의 보고/미보고/진행 중 예약 노출액에서 해당 대표 시작액을 빼 상한을 확인한다. main의 예약/가격 계산은 복제하거나 낮추지 않는다. 자동 실패/AI 검토 거부/EOF는 후속 호출을 중단한다.

각 실제 답변을 확인한 Codex의 별도 검토 의견을 ai_semantic_review.json에 기록한다. 사람 검토/후보 업무 승인은 not_performed, validation_scope=technical, live_quality_verified=false로 표시한다. 사람 semantic_review/business_review 파일은 생성하지 않는다. 새 모드와 기본 경로의 무료 회귀를 실행한 뒤 새 preflight·이번 계획에 결합된 사용자 승인으로9대표를 실행한다. 기존 결과/노트북·main 원본은 보존한다.


## 45. 나머지9대표 실제 시험 중단·B lookup 필터 호환 수정 (2026-10-30)

44절 계획의 명시 AI 의미 검토 모드를 B 실행부에 추가하고 기존 직접 사람 검토 모드는 기본값으로 유지했다. 새 모드는 승인 계획에 결합되며 AI 검토와 사람/업무 승인을 구분한다. main123개 원본은 그대로다. 관련212개·전체910개 중904통과/6 Docker skip·기술 선행 증거2개 무료 검사 후 새9대표 preflight와 사용자 승인으로 실행했다. 공유 $4.50과 대표당 $0.50을 적용했고 실제 응답을 확인한 의견만 별도 AI 검토로 받도록 했다.

첫 cq01_promise가 실제 HTTP4회(모두200)를 실행한 뒤 AGENT_RESULT_REJECTED로 중단되어 뒤8대표는 시작하지 않았다. 모델의 promise 전 필드가 고정 입력과 일치했고 한국어 답변도 1000 piece·ESP32-POE L WROOM·고객 도착 기한·가상 미등록/미확정·승인/이행 아님을 정확히 설명했다. 원인은 main이 허용하는 lookup(kind,node_id,version)을 B catalog_evidence가 lookup(node_id,version) 인자 딕셔너리 완전 일치로만 인정한 것이다. 업무 값 오류나 main 계약 거부가 아니다. 앞선 완화 점검에서 이 필터 조합을 빠뜨렸다.

B helper만 수정해 main의 kind/node_id 선택자 의미를 따르되, 고정 버전과 조회한 각 고정 노드의 실제 내용 일치를 계속 확인한다. 더 넓은 정상 조회도 해당 노드가 정확히 한 번 포함되어야 통과한다. 잘못된 kind/node_id/version·없는 노드·다른 내용·실패한 읽기는 계속 거부한다. 순수 필터 회귀2개와9대표의 kind+node_id 공통 provider/host 통합 회귀1개를 추가했다. 저장된 실제 원문/도구 trace의 오프라인 재검증은 True였지만 당시 result.json의 false/error/null payload와 stopped.json을 고치거나 실제 성공으로 재분류하지 않았다.

수정 후 새 DB/schema에서 관련217/217통과, 전체915 중909통과·Docker opt-in6 skip·실패/오류0, 새9대표 무료 preflight 통과·서버 종료를 확인했다. 변경 Python7파일의 Python3.10 AST/compile 및 추가 줄 공백 검사를 수행했다. 전용 lint/typecheck 도구는 설치/설정되어 있지 않아 추가하지 않았다. 실제 자동 재호출0, 실제 AI 검토/사람 승인 완료0, B 업무 저장0이다.

이번 실제 추정 $0.00745500, cq01_promise 대표 누적 $0.00745500/$0.50(재시도 시 남은 $0.49254500), 이전 cq01_intake 포함 전체 시험 누적 $0.04602525이다. 구성한 uncached token rates 기준이며 청구서 금액이 아니다. 새 검사 cluster2개·preflight2개·실제 cluster1개 모두 종료됐다. 기존 성공/실패 증거·노트북·main 원본은 보존했다. 현 상태는 유료 실패 중단 후 무료 수정 검증 완료이며, 수정 후 새 유료 시험은 하지 않았다. 기록: Git 제외 `artifacts/local/order_fulfillment/b_nine_ai_live_20261030_q861qc2n/`.


## 46. 나머지9대표 새 실제 시험 — 약속·현황 통과, CQ03 근거 복사 누락 중단 (2026-10-30)

사용자의 `ㅇㅋ 시도 ㄱㄱ`로 새 실행을 승인받았다. 코드 변경 없이 이전 CQ01 약속 지출 $0.00745500을 보수적으로 차감해 이번9대표 공유 전체 상한을 $0.49254500으로 낮췄다. 대표 누적 $0.50을 재설정하지 않았다. 첫 무료 preflight 시작은 외부 sandbox의 sandbox_apply 권한으로 자원 생성 전에 종료(exit71)됐고, 새 preflight-ready 경로에서 필요한 실행 권한으로9대표 무료 검사와 소유 서버 종료를 확인했다. 과거 DB·실행 재개는 하지 않았다.

새 실제 실행에서 cq01_promise와 cq02_normal은 각각 실제HTTP4회와 공통/B 자동 검사 및 실제 답변을 읽은 별도 AI 의미 검토를 통과했다. 약속은 제품/개정/구성·1000 piece·고객 도착 기한을 정확히 설명했고, CQ02는 완료100·운송150·창고250·진행300·준비100·대기100과 남은 약속900, 관측10시/조회10시10분을 구분했다. AI 기록은 ai_semantic_review.json이며 사람 검토/업무 승인과 구분한다.

세 번째 cq03_normal은 실제HTTP4회 뒤 AGENT_RESULT_REJECTED로 중단됐다. 모델의 양품600·잔여400·양품90/시간·생산100/시간·불량10%·조건부완료14:26:40·실제완료미확인 등 계산값과 상태는 입력과 일치했다. 유일한 assessment 차이는 assumptions 두 항목의 evidence 배열 누락이다. B ProductionAgent가 전체 계산 객체 일치를 요구해 거절한 것이며 main의 근거 검증을 변경하지 않았다. 오프라인 진단 사본에 입력의 두 evidence 배열만 넣으면 기존 B 검증이 True지만, 이는 원래 실제 실패를 성공으로 바꾸는 것이 아니다. 원본 result/stopped/provider 응답은 그대로 보존했다. 이후 CQ04 두 대표·CQ05 두 대표·CQ06 두 대표는 미호출이며 자동 재호출0이다.

이번 실제 요청12회 전부HTTP200, 추정 $0.03202425(약속 $0.00754725·현황 $0.01189050·생산 $0.01258650), CQ01약속 누적 $0.01500225, 과거 모든 시험 포함 $0.07804950이다. 구성한 uncached token rates 기반이며 청구서 금액이 아니다. 새 정상 서버2개 모두 status3·소유pid/postmaster/socket없음·정리오류0을 확인했다. 기록 전 스냅샷1179파일/기존결과/노트북 및 main123개가 동일했다. 이번 변경은 B 문서3개뿐이다. 사람 검토·업무 승인 미수행, 전체 완료 마커 없음, 새 실제 전체 성공 아님. 기록은 Git 제외 artifacts/local/order_fulfillment/b_nine_retry_20261030_ewp05rf0/에 보존한다. 다음 검토는 B 계산 결과의 고정 근거 메타데이터 복사 책임이며 main 공통 코드는 그대로 유지한다.


## 47. CQ01~06 고정 근거 메타데이터 연결 책임 정리 — 수정 전 계획 (2026-10-30)

사용자는 main 필수 조건 외에 남은 B 메타데이터 복사 조건을 다른 CQ까지 조사해 함께 수정하도록 요청했다. 목표는 불필요한 복사 책임만 B decoder로 옮기며 실제 업무 값·미확인·판정과 main 공통 계약 검증을 보존하는 것이다. 유료 API 요청은0이며 기존 결과/노트북을 덮어쓰지 않는다.

10대표 task 전체를 조사했다. CQ01 접수 extraction.nodes[].evidence와 CQ03 assessment.assumptions[].evidence가 아직 모델의 완전 복사에 의존한다. CQ01 약속/CQ02에는 추가 대상이 없고 CQ04의 가정 근거는 출력이 아닌 calculation_details에 있다. CQ05 review hash, CQ06 양쪽 입력 revision/hash·출처·review hash·Python 작성 답변은 이미 B가 연결하므로 그대로 유지한다. main의 실제 조회 근거 evidence_ids는 이 작업 대상이 아니다.

최소 수정은 B 입력 helper에 식별 키 기반 evidence 연결을 추가하고 CQ01은 후보id, CQ03은 가정code가 일치한 객체의 evidence만 고정 입력에서 연결한다. 리스트 위치만으로 연결하거나 수량·시각·제품/후보id·가정code/kind/detail·unknowns/warnings를 덮어쓰지 않는다. 모델 지침에서 두 경로를 B 담당 필드로 명시하고 해당 두 역할 patch버전을1.1.1로 올린다. 공개 INPUT/OUTPUT·공통 코드·예산·승인 규칙은 바꾸지 않는다. 실패 뒤 무조건True 반환/업무 객체 전체 교체/광범위 예외 무시 같은 우회 분기는 도입하지 않는다.

회귀는 수정 전 모델의 근거 누락 재현을 먼저 실패로 확인하고, 수정 후10대표 공통 provider/host 연결에서 메타데이터 생략·잘못된 복사값을 입력에서 연결하는지 확인한다. 원래 provider model_text는 그대로 유지한다. 반대로 후보id/type·수량·시각·가정code/kind/detail/누락·미확인·상태·공통 실제조회근거/버전 오류는 계속 거절되는지 확인한다. 새 PostgreSQL cluster·매 테스트별 별도 schema에서 관련 검사와 전체 회귀,10대표 새 무료 preflight, 원래 CQ03 실패 응답의 오프라인 재검증을 수행한다. 실제 성공과 무료 재검증은 분리하고 서버 종료·main 원본/기존 결과/노트북 hash를 검증한다. 새 의존성은 추가하지 않는다.


## 48. CQ01~06 고정 근거 메타데이터 연결 수정·무료 검증 완료 (2026-10-30)

47절 범위대로 B helper에 식별 키로 고정 evidence만 연결하는 작은 함수를 추가했다. CQ01 접수는 후보id, CQ03 생산은 가정code로 연결하며 두 역할 지침과 patch버전을1.1.1로 갱신했다. 모델에 반환을 요구하는 후보 필드 목록에서는 evidence를 제외하고 두 경로를 host_bound_fields로 명시했다. 공개 INPUT/OUTPUT와 업무 계산/판정 검증은 그대로다. 다른 CQ는 추가 복사 의존성이 없음을 전체10대표 검사로 확인했으며 기존 CQ05/06 연결을 재작성하지 않았다.

후보/가정의 business 필드를 덮어쓰거나 전체 업무 객체를 입력으로 대체하지 않는다. 누락·잘못된 모델 근거 복사값만 고정 입력의 근거로 연결하고 원래 provider_calls[].model_text는 보존한다. 공통 evidence_ids는 실제 조회 결과 선택으로 유지한다. 후보id/type·수량·시각·null·가정code/kind/detail/목록 누락·미확인/판정·공통 실제 조회·고정버전·권한·한도 오류는 계속 거절한다. 기존 intake 회귀에서 모델 evidence=[]의 거절 기대는 새 책임과 맞지 않아 후보id/type 변경 거절로 바꿨고 근거의 실제 연결/원문 보존은 새10대표 통합 회귀로 확인했다. 오류 후 무조건 통과·예외 무시·별도 우회 분기는 추가하지 않았다.

수정 전219검사에서는 새 회귀의 CQ01·CQ03 누락/오복사4개 subcase만 실패하는 것을 재현했다. 수정 후 새 DB/schema에서 관련219/219통과, 전체917 중911통과/6 Docker opt-in skip, 실패/오류/guard위반0이었다.10대표 새 무료 preflight도 통과했다. Python5파일의 Python3.10 AST/compile과 git diff --check를 확인했다. 전용 lint/typecheck 도구는 설정/설치되지 않아 추가하지 않았다. 현재 실제 호출은0이며 무료 테스트의 mock usage를 실제 과금이나 성공으로 세지 않는다.

이전 CQ03 실제 실패의 원문과 실제 tool_trace를 새 decoder/검증기로 오프라인 대조하면 True다. 원래 runtime.ok=false/error/stopped/provider 응답의 hash는 동일하며 새 실제 성공으로 바꾸지 않았다. 기존 CQ01 실제 성공의7개 기술 선행 증거는 새 역할patch버전에서도 유효하다. main123파일·기존 산출물1061파일·노트북4개가 동일하다. 새 red/green/preflight 서버3개는 현재 status3·postmaster.pid/socket 없음으로 종료를 확인했다. 실제 유료 재시험·사람 검토·업무 승인은 미수행이다.

변경 파일은 B source3개(b_input_task/b_intake_task/b_production_task), 회귀2개(test_b_intake_task/test_b_model_connection), B 문서3개뿐이다. 기록은 Git 제외 artifacts/local/order_fulfillment/b_metadata_fix_20261030_baksgutb/report.json과 수정 전후 로그에 있다. 모델 자유 설명의 범용 품질을 자동 검사로 보증하지 않으며 변경 코드로 새 실제 시험을 할 때 새 preflight/이번 실행 승인/누적 대표 한도를 다시 결합해야 한다.


## 49. CQ03 이후7대표 새 실제 시험 — 반복 호출 방지 실행 계획 (2026-10-30)

사용자가 메타데이터 수정 후 다음 실제 시험 진행을 요청했다. 이미 실제 통과한 CQ01 접수/약속·CQ02를 다시 호출하지 않고 CQ03→CQ04정상/혼합→CQ05정상/혼합→CQ06두 대표의7개를 새 실행으로 시험한다. 기존 실패/성공 폴더나 DB를 재개하지 않는다.

기존 B AI 기술 실행은 나머지9개 전체만 허용하므로 최소한으로 고정 순서의 남은 suffix도 명시적으로 선택하게 한다. 기본 사람 모드의 기존 순서와 CQ01 선행 증거 확인은 유지한다. AI 기술 모드의 대표 상한은 기본$0.50을 유지하되 문자열의 유한한0초과/$0.50이하 하향값만 계획에 결합한다. CQ03 이전 지출$0.01258650을 차감해 이번 각 대표 상한을 보수적으로$0.48741350, 공유 전체는$3.50으로 지정한다. 공통 ledger/예약/가격/권한/격리/오류/승인 계약은 그대로 재사용하며 모든 기존 결과는 보존한다.

구현 전 suffix선택·비정상순서/상한·계획변조 회귀를 추가하고 무료 재현한다. 수정 후 관련/전체 무료 검사와 새7대표 preflight를 완료하고 현재 사용자 승인으로 새 계획에 결합된 실행 승인을 기록한 뒤 실제 호출한다. 모델은 기존gpt-5.4-mini-2026-03-17,4096출력토큰,30초,main 설정에 맞춘12단계/일시 장애최초포함2시도/형식수정0을 유지한다. 각 실제 응답 자동 검사 후 실제 답변과 원문/근거를 읽은 별도 AI 의미 검토만 기록하며 사람/업무 승인을 만들지 않는다. 실패하면 이후 유료 호출을 중단하고 자동 재호출하지 않는다.

## 50. CQ03·CQ04 실제 통과·CQ05 업무 객체 누락 중단 (2026-10-30)

49절의 실행부 수정은 B 두 파일과 회귀 한 파일이다. AI 기술 모드에서 남은 대표의 고정 순서 suffix와$0.50이하 유한한 문자열 상한만 허용하고 계획 hash에 결합했다. 기본 사람 모드·실제 CQ01 선행 증거·공통 ledger/예약·권한/도구/승인·중복/실패 중단은 그대로다. 회귀3개를 수정 전 오류로 재현하고 수정 후 관련222개를 모두 통과했다. 새7대표 무료 preflight도 통과했으며 이 두 결과를 확인한 뒤 실제 호출했다. 전체 회귀를 매 호출의 필수 조건으로 추가하지 않았다. 별도 전체 검사는920 중914통과·Docker opt-in6 skip·실패/오류/guard위반0·실제 API0으로 종료됐다. Python3.10 AST/compile과 변경분 공백 검사를 확인했고 별도 lint/typecheck 도구는 설치하지 않았다.

새 계획의 대표 상한은 이전 CQ03 지출$0.01258650을 차감한$0.48741350, 공유 상한$3.50이다. 사용자 승인에 근거한 실행 승인자/이번 계획 승인을 입력해 새 격리 DB/schema에서 실제 시험했다. CQ01 접수/약속과 CQ02는 재호출하지 않았다. CQ03은 관측600·잔여400·시간당양품90·조건부완료14:26:40, CQ04 정상은 기한내960/부족40·전량15:26:40, 혼합은 기한내1000/부족0·전량14:20을 실제 한국어 답변으로 설명했다. 세 대표의 자동 검사와 실제 답변·입력·계산 자료를 읽은 별도 AI 의미 검토를 통과했다. 실제 도착/생산 완료 확정이나 납기 보증으로 표시하지 않았다.

CQ05 정상은 실제HTTP4회·제품/개정/구성 lookup3개가 성공했으나 B의 validate_result가 assessment 전체 객체 비교에서 거절했다. 실제 모델은 answer_ko와 draft_only/review_required/execution_performed/response_state/input_state만 반환했다. 부족한 키는 action_codes, assessment_scope, confirmation_codes, dispatch_request, forecast, response_rule_version, synthetic의7개다. 이미 반환한 값은 입력과 같고 설명에는 현재600·미래400·기한내960·기한후40·미발송/미등록·물류 미확인·고객 동의 필요를 구분했다. 같은 원문을 오프라인 decoder와 Spec.validate_payload에 넣으면 공개 스키마는 통과하고 B validate_result는False다. 따라서 runtime의 일반적인 거절 문구와 달리 실제 신원·비용 거절이 아니며, 앞서 수정한 근거 메타데이터 누락도 아니다. 누락된 업무 객체를 입력으로 자동 채우거나 검사를 우회하지 않았다. 원인만 cq05_diagnosis.json에 별도 기록했다.

실제 지출은 CQ03$0.01243200·CQ04정상$0.01347225·CQ04혼합$0.01583925·CQ05정상$0.01681275, 합계$0.05855625다.16요청 모두HTTP200이며 미보고/진행중예약/초과/형식수정0이다. CQ03 누적$0.02501850/$0.50, 과거 전체 포함$0.13660575다. 설정한 uncached token rates 추정이며 청구서 금액이 아니다. 실패 후 CQ05혼합/CQ06불량/CQ06운송은 호출하지 않았고 자동 유료 재실행0이다. 사람/업무 승인은not_performed, 전체 완료 마커 없음, 이번7대표 전체 성공은 아니다.

새 red/tests/preflight/live 서버4개 모두 현재pg_ctl status3·postmaster.pid없음·기록된socket부재를 확인했다. 소유 PID가 기록된 preflight/live는 PID부재도 재확인했다. main123파일은 고정ca7c391 원본과 동일하며 기존 산출물1169파일·노트북4개 hash를 보존했다. 기존1469파일 중 이번 변경은 B 소스2·테스트1·문서3개뿐이다. 실제 응답/검토/승인/중단/종료·무료 검사·원인 대조는 Git 제외 artifacts/local/order_fulfillment/b_seven_live_20261030_vkxxahgt/에 보존한다. 추가 코드 수정이나 새 유료 재시도는 하지 않았다.

## 51. B 계산 결과와 모델 설명의 책임 분리 — 수정 전 계획 (2026-10-30)

사용자가 승인한 범위는 접수를 제외한6역할·9대표다. 납품 약속 promise와 CQ02~06 assessment는 이미 B/Python이 계산한 권위 있는 자료이므로 모델에 전체 객체 복사를 요구하지 않는다. 기존 bind_input_fields로 검증된 고정 입력에서 매번 결합한 뒤 공개 OUTPUT와 기존 최종 검증을 적용한다. 실패 후 누락 값을 보충하는 재시도나 공통 검증 우회는 만들지 않는다. 과거 형식으로 모델이 보낸 B 소유 필드는 공식 계산값으로 사용하지 않으며 원래 model_text에는 남긴다.

모델 지침과 MODEL_OUTPUT만 책임에 맞추고 역할 버전을1.2.0으로 올린다. 공개 INPUT/OUTPUT, CQ01 접수 extraction 검증, main 공통 코드·도구/근거/버전/권한/비용 계약은 유지한다. CQ06 한국어 답변은 기존대로 Python 작성이며 나머지 역할의 한국어 설명은 모델 작성이다. CQ03 근거와 CQ05 검토 hash, CQ06 양쪽 출처는 전체 B 계산 자료 결합으로 보존한다. 중복 수동 결합만 제거하고 계산 함수는 바꾸지 않는다.

먼저 최소 모델 출력의 공통 provider/host 회귀가 현재 코드에서 실패함을 확인한다. 이후9대표에서 계산 객체 생략·잘못된 과거 복사값 무시·공식 계산/미확인 값 유지·원문 보존을 검사한다. 기존 역할 회귀의 모델 복사값 변경 거절 기대는 새 소유권에 맞춰 공식 결과 보존 검사로 바꾸되 실제 입력/공식 결과 변조, 잘못된 버전·조회 근거·권한·한도, 접수 추출 오류의 거절은 유지한다. 승인·검토·중복·실패 중단은 관련/전체 무료 회귀로 확인한다.

새 보존 스냅샷을 기록했고 새 격리 PostgreSQL cluster와 별도 schema에서 수정 전후 검사를 수행한다. 기존 DB·settings·과거 실행은 재사용하지 않는다. 유료 요청0, 기존 성공/실패 결과·노트북 보존, 새 서버 종료와 main 원본 동일성을 최종 확인한다. 무료 통과를 실제 LLM 재시험 성공으로 표현하지 않는다.


## 52. B 계산 결과 연결·무료 검증 완료 (2026-10-30)

51절 범위대로6역할·9대표의 promise/assessment를 기존 bind_input_fields로 고정 Task에서 연결했다. MODEL_OUTPUT에서 계산 객체를 제외하고 지침/역할 버전을1.2.0으로 갱신했다. CQ03 가정 근거·CQ05 검토 hash·CQ06 양쪽 revision/hash의 중복 수동 결합은 제거했다. CQ06 Python 답변은 유지했다. 공개 INPUT/OUTPUT와 계산 함수·CQ01 접수 추출/근거 검증·공통 host/provider/비용/권한/근거/버전/승인 흐름은 그대로다. 모델의 과거 계산 객체 복사값은 결합 전에 명시적으로 제외하며 실패 후 보충이나 자동 유료 재시도는 없다. 원래 model_text는 보존한다.

수정 전 계산 객체 생략 회귀의9대표가 모두 실패했다. 수정 후 최소 출력·null/배열/부분 객체의 잘못된 과거 복사값·원문 보존·B 공식 계산 유지·공식 결과 변조 거절을 확인했다. 초기 관련225검사 중 새 버전 거절 검사9 subcase는 테스트 기대 오류 코드가 공통 코드와 달라 실패했다. AGENT_RESULT_VERSION_MISMATCH로 기대값을 바로잡았고 제품 코드는 바꾸지 않았다. 최종 새 격리 DB/schema의 전체923검사 중917통과·Docker opt-in6 skip·실패/오류/외부접근 guard 위반0이다. 이 전체 검사는 관련225검사와10대표 공통 provider/host·무료 preflight·실행 승인/검토·중복·실패 중단·공통 demo/예제도 포함한다. Python13파일의3.10 AST/compile 및 git diff --check도 통과했다. 전용 lint/typecheck 도구는 설정/설치되어 있지 않아 새 의존성을 추가하지 않았다.

보존된 CQ05 정상 실제 원문과 실제 조회 trace를 새 decoder/공개 스키마/B validator로 무료 오프라인 대조하면 통과한다. 당시 runtime.ok=false와 원문/중단 기록은 그대로이며 새 실제 LLM 성공이 아니다. 이번 실제 API0, 사람/업무 승인 추가0, 기존 실행 재개0이다. main123파일과 기존 코드·산출물·노트북 스냅샷 중 승인한 B16파일 외 바이트 변경/삭제는 없다. 새 red/초기green/final 서버3개 모두 종료했다. 변경은 B 소스6·회귀7·B 문서3개이며 기록은 Git 제외 artifacts/local/order_fulfillment/b_business_binding_20261030_88254d6a/report.json에 있다. 한국어 설명의 의미 검토와 새 실제 실행의 preflight/이번 실행 승인/누적 대표 비용 차감은 계속 필요하며 이번에 유료 재시험하지 않았다.

## 53. CQ05 정상1대표 실제 재시험 계획 (2026-10-30)

사용자가 수정 후 CQ05 실제 호출을 요청했다. main123파일을 고정ca7c391과 다시 대조한 뒤 CQ05 정상만 새 격리 환경에서 시험한다. 현재 B AI 기술 모드의 suffix 전용 선택에 단일 대표 선택만 추가한다. 중간을 생략한 묶음/역순/중복/접수 포함은 계속 거절하고 기존 사람 모드·CQ01 실제 선행 증거·이번 계획 승인·검토·공통 비용/권한/근거 계약은 유지한다. 관련 무료 회귀와 새 CQ05 무료 preflight 이후에만 실제 호출한다. 매 호출마다 전체 회귀를 추가 필수 조건으로 두지 않는다.

이전 CQ05 정상 지출$0.01681275를 대표당 누적$0.50에서 차감해 이번 대표/공유 실행 상한을$0.48318725로 낮춘다. 기존 공개 모델 단가를 유지하고 고정gpt-5.4-mini-2026-03-17·4096출력토큰·30초·12논리단계/일시 장애 최초 포함2시도/형식수정0 프로필을 사용한다. 다른 대표는 이번 계획에 넣지 않는다. 응답의 자동 검사 후 실제 한국어 답변/자료/근거를 대조한 별도 AI 기술 의미 검토를 기록하며 사람 검토나 업무 승인을 만들지 않는다. 실패하면 중단하고 자동 유료 재실행하지 않는다. 기존 결과·노트북 보존과 새 서버 종료를 확인한다.


## 54. CQ05 정상 실제 재시험 통과 (2026-10-30)

53절 계획대로 B AI 기술 모드에 단일 대표 선택만 추가했다. 새 단일 대표 회귀는 수정 전 선택 거절로 재현했고 수정 후 관련32검사를 모두 통과했다. CQ05 정상만 지정한 새 무료 preflight도 통과하고 서버가 종료됐다. 이후 현재 사용자 요청을 이번 새 계획에 결합해 실행 승인자 junhwa로 승인했으며 보존된 실제 CQ01 선행 증거7파일을 검증했다. 옛 DB/settings/실행을 재개하지 않았다.

새 CQ05 정상은 실제 API4회 모두HTTP200, 공통 제품/개정/구성 lookup3개와 버전1/실제근거/권한/비용 검사, B 공식 계산 결과 연결을 통과했다. 모델 원문 키는 data_version/answer_ko/evidence_ids뿐이며 assessment 복사 없이 B가 고정 계산 결과를 정확히 연결했다. 실제 한국어 답변은 새 출고 검토1000·현재600/미래400·기한내960/부족40과 도착/운송 각0을 구분했고 미등록 검토 초안·분할 출고 조건·물류5항목 미확인·고객 일정 미합의·실행/승인/보증 아님을 설명했다. 실제 답변과 입력/계산/검토 자료/조회 근거를 읽은 별도 AI 의미 검토를 통과했다. 사람 검토·업무 승인은 not_performed이며 기술 검증 범위다. 실제 출고/발송/확정 저장을 하지 않았다.

이번 추정비용$0.01729350, CQ05 정상 누적$0.03410625/$0.50, 과거 모든 시험 포함$0.15389925다. 공통 구성의 비캐시 단가 추정이며 청구서 금액은 아니다. 미보고/진행중 예약/한도초과/형식수정0, 자동 유료 재실행0, 다른 CQ 및 CQ05혼합 호출0이다. 선택한 정상1대표의 summary/model_validation과 새 실제 서버 종료(status3·소유pid/postmaster/socket없음·정리오류0)를 확인했다. 이전 CQ05 실패는 보존하며 이번 새 성공과 구분한다. 실제 기록은 Git 제외 artifacts/local/order_fulfillment/b_cq05_retry_20261030_6wfjksf2/live/campaign/cq05_normal/result.json에 있다.


최종 무료 전체 검사는924 중918통과·Docker opt-in6 skip·실패/오류/외부접근 guard위반0이다. 관련32검사와 전체 검사를 유료 요청으로 세지 않으며 전체 검사는 이번 유료 호출의 새 의무 gate로 추가하지 않았다. Python 변경2파일의3.10 AST/compile·git diff --check를 확인했고 새 의존성을 추가하지 않았다. main123파일·기존 코드/결과/노트북 스냅샷에서 승인한 B5파일 외 변경/삭제0이다. 새 red/검사/preflight/live 서버4개 모두 현재status3·postmaster/socket없음이며 소유PID도 부재다. 이번 변경 파일은 B 실행부1·회귀1·B 문서3개다. 실제 결과·검토·승인·요청·종료와 무료 로그는 원본 경로 정보를 유지한 채 Git 제외 b_cq05_retry_20261030_6wfjksf2/에 보관했다. CQ05혼합·CQ06 두 대표와 사람/업무 승인은 이번 범위 밖이다.


## 55. CQ06 두 대표 직접 실제 실행 (2026-10-30)

사용자가 새 무료 사전검사 없이 CQ06 실제 호출을 요청했다. 기존 사전검사를 현재 코드의 성공으로 재사용하지 않는다. B AI 기술 실행 계획에만 명시적인 skip_preflight 선택을 추가하고, 선택과 preflight=None을 계획 해시·이번 실행 승인·표시에 결합한다. 기본 사전검사 경로와 공통 코드는 유지한다. 비용·권한·고정 입력·실제 근거·버전·실패 중단·중복 시작 금지는 그대로다. 순수 실행 계획 회귀로 선택의 범위와 승인 결합만 확인하며 새 무료 preflight/DB 검사를 실행하지 않는다.

CQ06 불량11·운송2시간을 새 격리 서버/schema에서 순서대로 실제 호출한다. 대표당$0.50·공유$1.00, 기존 고정 모델/4096출력/30초/12단계/일시장애2시도/형식수정0 프로필을 사용한다. 각 실제 응답·도구 기록을 읽고 별도 AI 기술 의미 검토 후 다음 대표로 이동한다. 사람 검토·업무 승인·확정 저장은 수행하지 않는다. CQ06 한국어 문안은 Python 생성임을 유지하고 LLM 조회·근거 연결과 구분한다. 기존 결과·노트북 스냅샷을 보존했고 종료·main123파일 동일성을 확인한다. 실패 시 자동 유료 재실행하지 않는다.

실행 승인 전 자동 승인 검토가 전체48요청 표시를 기존 전체40요청 상한 위반으로 거절했다. 승인 입력/HTTP 요청/서버 시작은 수행되지 않았고 첫 계획은 취소된 상태로 보존한다. 안전한 대안으로 B new_budget의 root max_model_calls만 min(40,24*대표수)로 낮춰 공통64 이하를 지킨다. 역할12논리단계·일시장애2시도·비용 상한은 유지하고 새 계획을 생성한다. 회귀는 순수 계획/프로필 검사에 한정한다.


## 56. CQ06 두 대표 실제 호출 통과 (2026-10-30)

사용자 요청대로 새 무료 preflight 없이 실행했다. B AI 기술 계획의 --skip-preflight는 preflight_skipped=true/preflight=null과 계획 hash에 결합되어 승인 화면에 표시된다. 기본 사람 모드에서는 생략할 수 없으며 이번 생략을 과거 무료 성공으로 위장하지 않았다. 초기48요청 계획의 승인 입력은 자동 검토에서 거절되어 서버/API/승인 기록 없이 취소됐고, B 전체40요청으로 낮춘 새 live40 계획으로만 실행했다. 모델·공통 CostPolicy/권한/근거/버전 코드는 그대로다.

CQ06 불량11·운송2시간은 각 실제4요청 모두HTTP200, 공통lookup3개(제품·개정·구성)의 버전1·실제 근거2개와 B 자동 검사를 통과했다. 최종 모델 원문은 data_version/evidence_ids이며 공식 비교 결과·출처·한국어 문안은 고정 Task에서 B/Python이 연결했다. 한국어 문안을 LLM이 작성했다는 성공이 아니다. 불량11은 속도90→80·기한내960→920/부족40→80·전량도착15:26:40→16:00, 운송2시간은3600→7200초·기한내960→870/부족40→130·전량도착15:26:40→16:26:40이다. 각 실제 응답·검토 자료·도구 기록을 읽고 별도 AI 기술 의미 검토 후 다음 대표로 이동했다. 가상·조건부·미등록·미발송 초안이며 사람 검토/업무 승인/확정 저장은 수행하지 않았다.

이번 비용은 불량11 $0.07022250, 운송2시간 $0.06250650, 합계 $0.13272900/$1.00이다. 각 대표$0.50 이내이며 과거 모든 시험 포함 추정$0.28662825다. 기존 공통 구성의 비캐시 단가 추정으로 청구서와 다르다. 실제8요청·형식수정/장애재시도/유료 재실행0, 미보고/진행중 예약/한도초과0이다. 선택한2대표의 summary/model_validation 통과와 실제 서버status3·소유PID/postmaster/socket부재·정리오류0을 확인했다. 실제 자료는 Git 제외 b_cq06_direct_20261030_pkc8n8dt/live40/campaign/에 보존한다. CQ05 혼합은 이번에 호출하지 않았다.

호출 후 최종 무료 회귀는925검사 중919통과·Docker opt-in6 skip·실패/오류/외부접근 guard위반0이다. 이는 실제 호출 전 사전검사나 새 실제 성공을 뜻하지 않는다. 순수 계획/프로필16검사도 통과했다. 새 실제/최종검사 서버2개는 모두 종료됐다. main123파일과 기존19919파일 스냅샷에서 이번 B 소스3·회귀2·문서3개 외 변경/삭제0이며 노트북/옛 실제 성공·실패 증거는 그대로다. Python5파일의3.10 AST/compile·git diff --check를 통과했고 새 의존성·원격 푸시·커밋은 없다.


## 57. 일반 실행과 시연 분리·CQ06 LLM 설명 — 수정 전 계획 (2026-10-30)

사용자가 일반 실행 단순화·시연 선택화·main 기준 제한 통일을 승인했다. 목표는 CQ 선택→공통 run_task→결과/도구 기록의 한 명령 실행이다. 기본 일반 실행에서는 preflight 증거, CQ01 선행 성공, 고정 suffix/단일 대표 제약, 대표별 검토 대기와 별도 계획/승인 입력을 요구하지 않는다. 명시적 실제 실행 명령과 표시되는 비용 설정을 이번 실행 의사로 기록하며 대화의 기존 유료 실행 승인을 새 계획에 결합한다. 실제 CQ01 업무 승인/확정 저장은 추가하지 않고 기존 업무 승인 계약은 유지한다.

범위는 examples/order_fulfillment의 B 실행/연결 모듈, b_comparison_task.py 및 영향받는 B 테스트/문서다. 기존 common host/provider/model/T-box/README/root PROJECT_STATUS/.gitignore는 고정ca7c391 원본과 동일하게 보존한다. 새 공통 계약/도구/의존성은 추가하지 않는다. 보존 스냅샷을 작성했으며 직전 전체925검사(919통과·Docker6 skip)가 변경 전 회귀 근거다.

불필요한 결합은 일반 실행에 남아 있는 시연 preflight/CQ01 선행·검토 대기·40요청 평가 상한이다. 이들은 기본 실행에서 제거하고 명시적 시연 경로에서만 유지한다. 공통 버전/근거/권한/오류/비용 한도·명시적 실제 전송·중복 금지·새 격리 환경·기록/종료는 근거 있는 보호 경계로 유지하며 실패 후 값 보충/무료 성공 위장/자동 유료 재실행 fallback은 추가하지 않는다. 지침과 계산값 대조는 역할 내부 업무 책임으로 유지한다.

main 고정 모델·4096출력·30초·12논리단계/일시장애 최대2시도/repair0, 대표 누적$0.50 이내를 유지한다. 일반 실행의 요청 예산은 main 공통 허용 범위 안에서 지정하며 옛 시연 전체40은 시연 설정에 한정한다. 대표별 이전 비용은 공개 실행 설정에서 차감하고 공통 CostLedger 예약/미보고 사용량을 재사용한다.

CQ06은 Python 업무 계산과 공식 assessment/출처 연결을 유지하되 answer_ko를 모델 OUTPUT으로 전환한다. 기존 Python 설명은 검토 참고 자료로만 남기고 모델 응답을 조용히 대체하는 fallback을 두지 않는다. 역할 버전과 회귀를 갱신한다. 실제 응답/한국어 설명은 별도 기술 검토로 확인하며 사람/업무 승인으로 표현하지 않는다.

회귀 범위: CQ01~06 각 독립 선택·정상/혼합 자유 조합·실제 전송 명시·모의 기본·사전검사/CQ01 선행 없이 시작·검토 대기 없음·중복 차단·실패 즉시 중단·결과 저장/서버 종료·대표/공유 비용·공통 버전/미조회 근거 거절·CQ06 모델 설명 보존/빈 설명 거절/공식 계산 불변. 새 격리 DB에서 관련/전체 무료 검사 후 CQ05 혼합과 CQ06 두 대표만 새 실제 실행한다. 기존 성공 CQ01~04·CQ05정상은 반복 호출하지 않는다. 모든 새 서버 종료와 옛 코드/결과/노트북 및 main123파일 보존을 최종 확인한다.


## 58. 일반 실행 분리·CQ06 LLM 설명 전환 완료 (2026-10-30)

사용자가 승인한57절과 캡처의4개 항목을 B 파일에서 완료했다. `b_run run --case ... --cost-policy ...`는 CQ01~06을 독립 선택하며 mock_http가 기본이다. `--transport openai`가 이번 실제 실행 지시이며 별도 승인 문구·preflight 증거·CQ01 선행·대표별 검토 대기를 요구하지 않는다. 명령과 정확한 입력/모델/비용/코드 해시를 결합하고 기존 once claim·새 DB/schema·키 읽기 창·공통 run_task·결과/요청 기록·실패 중단·서버 종료를 재사용한다. 일반 실행에는 사람/업무/AI 검토 완료를 자동 기록하지 않는다. CQ01의 유효한 needs_clarification 답변도 허용하며 업무 저장·승인은 수행하지 않는다.

시연은 `preflight/plan-model/run-model`의 명시적 선택으로 남겼다. 시연40요청, CQ01 선행 증거 및 사람 의미/정확한 후보 버전 업무 검토 흐름은 일반 실행과 분리했다. 일반 예산은 공통 허용 범위의 min(64,24×대표수)이며 main 기본값64라는 뜻은 아니다. 모델·출력4096·30초·12논리단계·일시장애 최초 포함2시도·repair0·공통 비용/근거/권한/버전 검증은 유지한다. 일반/AI기술 실행의 대표 cap은0.50 이하이며 기존 지출을 차감한 cap을 새 실행에 결합한다. 여러 실행의 계정 전체 비용을 자동 통합하는 기능은 추가하지 않았다.

CQ06 역할1.3.0은 answer_ko를 실제 모델 OUTPUT으로 전환했다. Python은 공식 비교 계산과 출처를 연결하며 설명 누락 시 문안을 보충하지 않는다. Python 설명은 참고 자료와 무료 scripted 대역에만 사용한다. CQ01 접수1.1.1·기타 역할1.2.0·공개 payload·공통 계약은 그대로다. 최종 실제 CQ06 result.payload.answer_ko가 원래 provider_calls[-1].model_text의 answer_ko와 동일함을 확인했다.

| 새 실제 대표 | 실제 요청 | 핵심 입력/답변 대조 | 이번 추정USD | 이전 포함 대표 누적USD |
| --- | --- | --- | --- | --- |
| cq05_mixed | 4, HTTP200 | 도착100/운송150 제외·현재450/미래300·새출고750·기한내1000/부족0·전량14:20 | 0.01980975 | 0.01980975 |
| cq06_defects11 | 4, HTTP200 | 누계양품600→680·속도90→80·기한내960→920·부족40→80·전량15:26:40→16:00 | 0.07211100 | 0.14233350 |
| cq06_transport2h | 4, HTTP200 | 운송3600→7200초·기한내960→870·부족40→130·전량도착1시간 지연 | 0.06416775 | 0.12667425 |

새 실제12요청, 추정합계$0.15608850, 과거 시험 포함$0.44271675다. 구성 비캐시 단가 추정이며 청구서가 아니다. 모든 요청HTTP200·공통 제품/개정/구성 버전1 lookup3개·근거2개 및 B 자동 검사를 통과했다. 실제 원문·공식 계산·검토 자료를 읽은 별도 AI 기술 의견은 ai_technical_review.json에 남겼다. runtime의 사람/업무/AI검토 not_performed를 덮어쓰지 않았으며 사람 승인·범용 LLM 품질·실제 납품/저장 성공을 주장하지 않는다. 성공한 이전 CQ01~04·CQ05정상은 호출하지 않았다. 자동 유료 재실행·장애 재시도·형식 수정0이다.

무료 전체932검사:926통과·Docker opt-in6 skip·실패/오류/guard위반0. 별도 검토에서 발견한 일반 모의 run.json의 synthetic_reviews 표시 한 줄을 수정하고, 관련234검사를 새 DB에서 다시 통과했다. 일반 CLI10대표는 preflight/이전 결과/검토 입력 없이 완료됐고 실제API0·mockHTTP40·검토 미수행을 확인했다. 중복 실행 차단·실패 후 후속 CQ 중단·원문 보존·모델 설명 누락 거절·공식 계산/출처 불변을 회귀로 확인했다. Python12파일3.10 AST/compile·git diff --check 통과다. 별도 lint/typecheck 도구는 저장소에 설정돼 있지 않아 새 의존성을 추가하지 않았다.

새 서버6개(전체/관련검사2·일반mock1·실제3)는 모두 종료했다. pg_ctl status3, postmaster/socket부재와 로그의 소유PID부재를 확인했다. main123파일은 고정ca7c391 원본과 byte 동일하며 root README/PROJECT_STATUS/.gitignore와 기존20031파일 중 허용 B 소스7·회귀5·B문서3개 외 변경/삭제0, 기존 노트북7개/실제 성공·실패 산출물은 그대로다. git HEAD 대비 model.py/T-box의 차이는 앞선 main 원복 결과이며 이번 수정이 아니다. 새 의존성·커밋·원격 푸시·PR은 없다.

이번 변경 파일: examples/order_fulfillment/{b_run,b_run_isolation,b_model_run,b_model_connection,b_model_preflight,b_task_workflow,b_comparison_task}.py, tests/order_fulfillment/{test_b_model_run,test_b_model_connection,test_b_model_preflight,test_b_task_workflow,test_b_comparison_task}.py, docs/order_fulfillment/{README,PROJECT_STATUS,B_MAIN_CONNECTION_DESIGN}.md. B README의 중복된 옛 실행 안내를 현재 일반 실행·선택형 시연 안내로 줄였으며 원래 실제 결과는 변경하지 않았다.

기록은 Git 제외 artifacts/local/order_fulfillment/b_simple_run_20261030_x3t7u5d7/report.json, 각 새 실행의 campaign/*/result.json·ai_technical_review.json·shutdown.json, preservation.json 및 checks*/에 보존한다. 명령은 B README를 따른다. 실제 전송을 생략하면 무료모의이며, 이전 대표 재시험에는 이미 지출한 비용을 뺀 cap과 새 output을 사용한다.


## 59. 추가 일반 실제 실행 CQ01 중단 (2026-10-30)

사용자의 실제 LLM 실행 요청으로 일반 run/openai를 새 격리 DB/schema에서 실행했다. CQ01~05 8대표를 먼저 선택하고 CQ06은 후속으로 분리하는 범위였으나 첫 CQ01 접수에서 중단돼 뒤9대표는 호출하지 않았다. 실제4요청 모두HTTP200·버전1 조회/공통 공개 출력 필드는 통과했지만, 모델은 원문에 명시된 L/WROOM을 선택하지 않고 카탈로그의 L/L1 공존을 식별 불가로 해석했다. 기대 extraction은 ready·후보3개, 실제는 needs_confirmation·후보0개로 B 업무 검증에서 거절됐다. 비용/권한/API 장애가 아니다.

추정$0.01126125, 자동 유료 재실행0·후속CQ0, 서버종료(status3·소유PID/postmaster/socket부재·정리오류0)를 확인했다. 무료 오프라인 원인 대조만 수행했고 모델 응답·실패 원문을 보존했다. main123파일·코드·노트북은 변경하지 않았다. 이전 성공은 그 실행의 증거이며 이번 새 전체 실행 성공을 뜻하지 않는다. 현재 실제 LLM 실행이 매번 전체 통과한다고 단정할 수 없다. 코드/지침 수정이나 새 유료 재시험은 하지 않았다. Git 제외 b_live_all_20261030_0kldad7a/report.json 및 live/campaign/cq01_intake/result.json 참조.



## 60. B 최소 실행 검증과 품질 평가 분리 — 변경 전 계획 (2026-10-30)

사용자가 승인한 범위는 B 전용 실행 검사 축소와 CQ01 식별 지침 보완이다. main 공통123파일·model/T-box·루트문서·비용/승인/권한/근거/버전·기존 결과/노트북은 보존한다. 기존932 무료 회귀와 실제 CQ01 실패 원문을 변경 전 근거로 사용한다. 새 의존성/검증 프레임워크/공통 변경/자동 유료 재시도는 추가하지 않는다.

CQ01 extraction 전체 정답 비교를 실행 훅에서 제거한다. ready 후보의 수량/구성/참조/원문 필드와 필수 구조·미등록/가상/사람검토 상태는 검사하되 노드 순서/동등한 시각 표현·확인 사유 문구는 정답 일치를 강제하지 않는다. needs_confirmation/invalid는 빈 후보·의미 있는 사유를 가진 업무 결과로 반환하며 ready와 구분한다. 정확한 상태/정답·불필요한 확인 요청은 기존 테스트의 별도 품질 평가로 확인한다. 원문의 모델→개정→구성으로 좁힌 뒤 유일성을 판단하도록 지침을 명확히 한다. 후보를 Python으로 대체하거나 원래 실패를 성공으로 바꾸지 않는다.

CQ01약속~CQ06은 이미 B가 공식 계산/출처를 연결하므로 그 무결성과 빈 답변 방지는 유지한다. catalog_evidence의 모든 노드3개 조회/특정 arguments 형태 강제를 제거하고 최소 실제 카탈로그 조회와 관측한 입력 일치만 확인한다. 최종 근거의 실제 조회/버전/권한 검증은 공통 호스트를 재사용한다. 전체3조회/순서/정답 불변은 평가 사례로 남긴다.

일반 실행 기록에 CQ01의 업무 상태를 별도로 표시해 실행 완료와 접수 준비 완료를 혼동하지 않는다. 선택형 사람 시연·CQ01 정확 버전 업무 승인 조건은 유지한다. 영향 B 역할7개/공유입력도우미/실행표시와 관련 테스트를 수정하고 새 격리 DB/schema에서 관련/전체 무료 회귀를 수행한다. 마지막에 CQ01 접수 새 실제1대표만 기존 누적0.50 이내에서 호출하고 원문/후속상태/종료를 확인한다. CQ06 반복 실제 호출은 이번 기본 범위가 아니다.


## 61. B 최소 검증 정리·무료 회귀 및 새 CQ01 실제 결과

60절 계획을 B 하위 파일에서 완료했다. 공통 validate_result 접점은 그대로이며 공용 payload·허용 도구·비용 정책을 변경하지 않았다.

| 책임 | 실행 시 유지 | 별도 회귀/품질 평가 |
| --- | --- | --- |
| 공통 호스트 | schema·고정 data_version·실제로 읽은 선택 근거·권한·누적 비용 | main 기존 계약 검사 |
| B 카탈로그 | 실제 성공 조회 중 관련 노드 최소1개·그 노드 내용/버전 일치 | 전체 읽기 예시·인자 표현·조회 순서 |
| CQ01 접수 | ready의 원문 필수 값/3개 후보 연결·미등록/가상/검토 필요 표시 | 후보 배열 순서·동일 시각 문자열 표현·사유 문구·고정 사례의 ready 품질 |
| CQ01 약속~CQ06 | 한국어 답변/선택 근거 존재·공식 계산/출처 보존 | 자유문 의미 정확성과 실제 답변 평가 |

유효한 needs_confirmation/invalid는 nodes=[] 및 의미 있는 사유로 반환한다. 정상 실행의 intake_status/candidate_ready를 통해 ready와 구분하고 다음 독립 CQ를 실행할 수 있다. 기술 통과를 후보 완료·사람 승인으로 표시하지 않는다. 이전 실패 원문을 새 검사로 오프라인 대조하면 유효한 clarification 계약은 통과하지만 고정 원문 기대 ready에는 불일치한다. 원래 실패 실행의 상태/증거는 수정하지 않았다.

테스트를 먼저 추가해 새 상태 필드 누락을 재현했고 수정 후 경계1·관련247검사 모두 통과했다. 전체945검사(939통과·Docker6 skip), 실패/오류/외부API0이다. 필수 ready 값 위조·미조회 근거·관측 노드 내용/버전 변조·빈 답변/근거·잘못된 공식 계산 거절, 단일/넓은/순서 변경 조회 허용, 중복 시작 차단·실패 중단·기록 보존을 확인했다. 별도 읽기 검토에서 차단 결함은 없었다. Python14파일 AST3.10/compile·git diff --check 통과; 별도 lint/typecheck 설정이 없어 새 도구/의존성을 추가하지 않았다.

새 CQ01 실제 호출은4요청 HTTP200, ready/후보3개·원문 L/WROOM/수량1000/납기/납품처 일치다. raw model_text의 extraction/한국어 설명과 최종 payload가 같아 후보를 Python으로 대체하지 않았다. 독립 AI 기술 검토 통과이며 사람/업무 승인·확정 저장은 미수행이다. 추정 비용$0.01223250, CQ01 과거 포함$0.06206400, 전체 과거 시험 포함$0.46621050이다. 구성 비캐시 단가 추정이며 청구서가 아니다. 이번 상한은 과거 CQ01 지출을 뺀$0.45016850, 공통 비용 예약 정책/모델4096출력/30초/12단계/일시장애 최초포함2시도는 변경하지 않았다. 실제 장애재시도/형식수정/자동 유료 재실행/나머지9대표 추가호출0이다.

변경 파일: examples/order_fulfillment/{b_input_task,b_intake_task,b_promise_agent,b_fulfillment_task,b_production_task,b_delivery_task,b_response_task,b_comparison_task,b_model_run}.py; tests/order_fulfillment/{test_b_intake_task,test_b_model_connection,test_b_catalog_reads,test_b_comparison_task,test_b_model_run}.py; docs/order_fulfillment/{README,PROJECT_STATUS,B_MAIN_CONNECTION_DESIGN}.md. 의미 변경에 맞춰 SPEC intake1.2.0/중간5개1.3.0/비교1.4.0으로 올렸다.

새 서버3개 종료, main ca7c391의123파일 byte동일·model.py/Tbox/루트README/PROJECT_STATUS/의도된.gitignore 불변, 기존 산출물106685파일·notebooks하위4개 해시 변경/삭제0이다. 기존 성공·실패는 해당 실행의 증거로 보존한다. 커밋·푸시·PR·기존 그래프 재생성은 없다. 상세는 Git 제외 artifacts/local/order_fulfillment/b_min_validation_20261030_7_233r2n/report.json 및 live/ai_technical_review.json, preserved-results.json, checks/ 참조. 임의 입력/향후 모든 실제 실행의 성공은 보장하지 않으며 Docker6검사는 이번에도 미실행이다.


## 62. 대표당 비용 상한 $1.00 변경 계획 (2026-10-30)

사용자 지시에 따라 B 실행의 기본값·허용 최대값을 $0.50에서 $1.00으로 변경한다. 공통 CostPolicy와 기존 공개 요청 상한 $1.00, 최대 비용 예약, 모델·토큰·시간·시도 설정은 유지한다. 기존 실행·봉인된 계획·성공/실패 결과를 수정하거나 재개하지 않는다. 더 낮은 대표별 상한 지정은 계속 허용한다. B 계획·CLI 기본값·상한 경계·계획 해시 회귀 검사로 검증하며 실제 API는 호출하지 않는다. 한국어 답변 개선은 남은 CQ 실행 후 별도 작업한다.

결과: B 실행부 2파일·테스트 2파일·B 문서 3파일만 변경했다. 기존 $0.50 기본값의 실패를 먼저 확인한 뒤 $1.00 기본/명시값·낮은 상한 유지·$1 초과 거절·계획 해시 변조 거절·CLI 중복 실행 차단을 무료로 검증했다. 관련 20검사 통과, Python4파일 AST3.10/compile 통과. main123파일 원본 동일·공통 공개 요청 상한1.00 불변, 실제 호출0·새 서버0이다. 이 변경의 사전 사본과 확인 결과: /private/tmp/b-cap-1usd-tlkl5o1o/. 과거 비용을 자동 합산하는 기능은 추가하지 않았다.


## 63. CQ06 미조회 근거 거절 수정 계획 (2026-10-30)

실제 CQ06 불량 실행에서 모델이 lookup 없이 최종 근거 ID를 제출해 main 호스트의 AGENT_EVIDENCE_MISMATCH로 거절됐다. 기존 실패는 보존한다. B 모델 연결만 기존 B provider의 요청 옵션 확장을 재사용해 초기 lookup을 요구하고 실제 도구 결과 이후 auto 선택을 허용한다. 조회 인자·횟수·순서는 강제하지 않으며 main 근거/권한/고정 버전/비용 검증은 그대로 유지한다. 기존 provider 기본 동작과 legacy 역할 hook은 변경하지 않는다. 최종 결과 검증 실패 시 안전한 오류 코드·대표·기록 경로를 터미널에 표시하고 실패 중단은 유지한다. 무료 HTTP 대역으로 최초 required→조회 후 auto·일시 장애 재시도에도 required·공통 미조회 근거 거절·원문 보존·후속 대표 중단을 검사한다. 공통 코드·기존 결과·모델/비용 정책은 변경하지 않으며 실제 유료 재호출은 하지 않는다.

검증 결과: 관련83검사 통과, 보완 후 provider/공통 비용 호환19검사 통과, 최종 전체951검사 중945통과·Docker opt-in6제외·실패/오류/외부 접근 위반0. 10대표 각각 실제 공통 lookup 1회 후 답변 수용, CQ06 미조회 답변의 공통 거절·원문 보존·추가 호출 없음, 실패 코드 표시·후속 대표 중단을 확인했다. Python6파일 AST3.10/compile·git diff --check 통과, 독립 읽기 검토 차단 문제 없음. 새 격리 서버2개 모두 종료했고 최종 서버 PID50349·pidfile·socket 부재/status3을 확인했다. 최초 기본 sandbox initdb는 공유 메모리 권한으로 서버 시작 전 실패·자동 정리됐으며, 이후 새 디렉터리에서 로컬 검증했다.
main ca7c391의123파일 byte 동일·이번 공통 코드 변경0, CQ06 기존 실패/노트북11파일 hash 변경0. B 코드3·테스트3·문서3파일만 수정했다. 대표당 최대$1.00 및 기존 모델/토큰/시간/시도 설정 유지, 이번 실제 API 호출0·기존 실행 재개0·커밋/푸시/PR0이다. 실제 수정본 LLM 재시험과 한국어 문안 개선은 미수행이다. 상세: artifacts/local/order_fulfillment/b_initial_lookup_20261030_op1cwg9k/report.json.



## 64. 실제 10대표 답변 품질 보완 계획 (2026-10-30)

10대표 실제 실행의 수량 계산과 한국어 설명을 구분해 검토했다. CQ04 혼합의 기한 내 전체1000을 현재 출고 가능량으로 오해하는 설명, CQ05의 요약만 있고 요청/회신 본문이 없는 점, 혼합에서 기존 납품/운송 사실과 이번 Agent의 미실행을 혼동하는 점, 초/소수 비율/ISO 시각·내부 코드 노출을 B 지침으로 보완한다. 공통 JSON/필드·계산·출처·검증·권한·비용·초기 lookup 연결은 유지한다. answer_ko 내 문단 구성을 개선하며 제목/고정 문구/숫자 전사 일치로 새 유료 실행 차단을 추가하지 않는다.

범위: B delivery/response/comparison task의 역할 지침과 역할 버전, 해당 무료 회귀 및 B 문서. 기존 계산 입력의 수량·가정·출처와 미확인/historical 상태는 그대로 유지한다. 문안의 현재/미래/기납품/운송 구분·복사 가능한 출고 요청/고객 회신 본문·변경 일정 협의의 조건성·가상 자료 표시는 지침에 명시한다. 모의 계약 검증은 실제 새 LLM 문안 평가로 설명하지 않는다. 기존10대표 성공과 CQ06 실패·노트북을 먼저 해시 보존하고, 필요한 테스트부터 보완 후 격리 무료 회귀를 수행한다. main123파일·model.py/T-box·루트 문서는 변경하지 않는다. 실제 유료 재호출 없이 수정 후 사용자가 선택한 대표로 평가하도록 준비한다.

검증 결과:

실제10대표 답변에서 확인한 CQ04 혼합 물량 의미 오류와 CQ05 본문 누락을 B 역할 지침에서 보완했다. CQ04의 전체 도착 예상에는 기납품·운송 중·미래 생산이 포함될 수 있으며 현재 새 출고 확보분과 구분한다. CQ05는 기존 answer_ko 안에 업무 요약·출고 요청·고객 회신·담당자 확인을 구분해 LLM이 본문을 작성하도록 변경했다. 기존 납품/운송 사실과 이번 Agent의 미실행을 구분하며 부족 또는 일정 변경 제안이 있을 때 고객 협의를 설명한다. 미확인/과거 검토는 확인용 문안으로 유지한다. CQ06은 백분율·시분초·날짜와 원래 시간대/정밀도를 유지한 전후 비교로 안내한다. 제목/고정 문구에 대한 새 실행 차단 검사는 추가하지 않았다.

역할 버전 CQ04/05 1.4.0, CQ06 1.5.0. 세 코드 파일의 AST를 비교해 SPEC 지침/버전 외 함수·입출력 필드·계산·검증기·대역은 동일함을 확인했다. 터미널은 기존 answer_ko를 그대로 출력하므로 표시 코드를 추가하지 않았다. 공통 등록/request.run_task·초기 실제 lookup·근거/권한/비용 정책과 대표당 최대$1.00은 그대로다.

무료 관련119검사 통과, 최종 전체955검사 중949통과·Docker opt-in6제외·실패/오류/보호 위반0. 모델 지침 전달·혼합100+150+450+300의 의미 구분·새 출고750·자연스러운 제목 없는 문안 허용·4내용의 합성 초안 원문 전달·공식 계산/출처 보존·기존 미확인/과거 상태 회귀를 확인했다. Python7파일 AST3.10/compile·git diff --check 통과, 독립 읽기 검토 차단 문제 없음. 새 격리 서버1개 정상 종료/status3·PID65498 부재·pidfile/socket 부재를 확인했다.

main ca7c391의123파일 byte 동일·실제10대표 성공/CQ06 이전 실패/노트북113파일 hash 변경0. 변경은 B 역할3·관련 테스트4·B 문서3파일이다. 이번 유료 API0·키/기존 환경/DB 사용0·커밋/푸시/PR0. 무료 대역 통과는 새 지침의 실제 LLM 문안 품질 성공이 아니다. 다음 실제 평가는 CQ05 혼합을 우선해 두 초안 본문,450/300/750 및 기한 충족 시 불필요한 일정 변경 요구가 없는지 확인한다. 참고 Python 문안의 변경 일정 표현은 기존 그대로이므로 새 조건부 작성 지침이 실제로 반영되는지도 평가한다. CQ01~03 지침은 이번에 변경하지 않았다. 상세: artifacts/local/order_fulfillment/b_answer_quality_20261030_4e78_uus/report.json.

# 터미널 새 입력 사례 추가 계획 (2026-10-30)

- 목적: 기존 고정 예제를 덮어쓰지 않고 새 800개 가상 납품 사례를 CQ01 접수/약속, CQ02~05, CQ06 운송 변경에서 사용자가 하나씩 실행한다. 자동 업무 파이프라인이 아니다.
- B `b_run run --input <절대 Markdown 경로>`를 추가한다. 입력은 계약 표와 명시적인 운영 JSON 하나이며, 공통 원본 무결성 계약에 맞게 `data/order_fulfillment/` 아래 둔다. 임의 자연어 운영 기록 추출은 이번 범위가 아니다.
- B task builder와 계산을 재사용하고 공통 `run_task`, lookup, 권한, 근거, 비용, 서버 격리/종료를 유지한다. model.py, T-box, main 공통 파일과 기존 입력/결과는 변경하지 않는다.
- 실행 계획에 원문·경로·해시와 CQ 입력 해시를 결합한다. 워커는 해당 원문 파일만 추가로 읽으며 원문 복사본과 입력/결과를 새 실행 디렉터리에 보존한다. 유료 호출은 사용자가 `--transport openai` 명령으로 실행한다.
- 회귀 범위: 새 입력의 수량·생산/납기/변경 계산, 기존 대표 입력 불변, CQ별 공통 호스트 연결, 원문 변경/중복 실행/실패 중단/비용/서버 종료 경계. 로컬 대역만 사용하고 유료 API는 0회. 새로운 한국어 LLM 성공으로 보고하지 않는다.
- 완료 근거: 새 B `b_terminal_input.py`가 원문 계약 표와 명시적 운영 JSON을 기존 task builder로 연결한다. B CLI/model plan/isolation 3파일에 선택 입력만 추가했고, 새 원문 1파일·무료 검사 1파일 및 B 문서만 추가/갱신했다. 공통 schema·도구·승인·비용 계약 변경은 없다.
- 관련 무료 90/90, 전체 963개=957통과·기존 Docker 선택 검사 6 skip·실패/오류/외부접근 위반 0. Python 3.10 AST/compile 5파일·diff check·독립 읽기 검토 통과. lint/typecheck 도구/설정은 기존처럼 없으며 새 의존성을 추가하지 않았다.
- 실제 CLI 무료 대역으로 새 7역할·28 HTTP 대역 요청·실제 API 0회, 입력/결과/원문 보존·공통 저장 불변·서버 종료를 확인했다. 회귀용 서버도 별도 새 cluster/schema만 사용 후 종료했고 두 PID/socket 모두 부재다.
- 기존 main ca7c391 123파일·실제 성공/실패/노트북 증거 113파일과 직전 수동 재실행 결과는 해시가 같다. 새 입력의 실제 LLM 한국어 출력은 아직 실행하지 않았다.
- 발견한 기능 제한: 혼합 사례 CQ02는 부분 완료 생산 집계 미지원, CQ03은 단일 작업 전체 배정만 지원한다. 해당 `needs_confirmation`을 정상적인 업무 미확인으로 보존했고 지원 범위를 확장하거나 성공 수량으로 대체하지 않았다. 이는 후속 B 기능 개선 대상으로 남긴다.
- 로컬 검증 근거: `/private/tmp/b-terminal-input-3z3imnvq/`와 Git 제외 `artifacts/local/order_fulfillment/b_terminal_input_20261030_3z3imnvq/report.json`. 사용자 실행 순서·명령은 B README의 새 입력 절을 따른다. commit/push/PR·기존 실행 재개·유료 API 호출은 모두 0이다.
