# 생산계획 입력·등록 그래프 연결과 계약 수요 후보 — Task 01-C·02

> 공통 통합 이후 기준: `graph-0.2`에서 A/B 신규 타입을 공통 저장·조회·RDF·Neo4j에 연결했다. 아래 Task별 미등록·분리 기록은 당시 이력이다. 현재 계약과 실행 방법은 [TOOLS](TOOLS.md#ab-신규-업무-타입의-공통-저장조회), 완료 근거는 [PROJECT_STATUS](../PROJECT_STATUS.md)를 따른다. A 전용 업무 테이블의 승인 fact 자동 편입을 뜻하지 않는다.

현재 전체 업무 흐름은 [A 생산계획 흐름](MATERIALS_FLOW.md)을 기준으로 한다. 업무 단계·분기·저장 시점 변경 시 해당 문서도 함께 갱신한다.

## 2026-11-01: A 스키마와 공유 T-box 생성

후속 A 내부 연결: `graph_validation.py`가 호스트 제공 참조 그래프의 A 노드를 검증하고
`contracts.py`가 이를 호출한다. 공통 노드·SourceDocument 원본 무결성은 기존 공통
검증기에 위임하며, A 노드는 필수값·자료형·관계·근거·가상 출처·업무 규칙을 검사한다.
알 수 없는 타입/잘못된 노드를 제거하지 않고 오류로 처리하며 전체 ID 중복도 검사한다.
이 로컬 보고서는 공통 Service의 검증/승인/저장 영수증이 아니다. 공통 코드·전역 SCHEMA는
변경하지 않았다. 2026-10-30 분리 당시에는 관련 53개 통과, 전체 622개 중
failures=128/errors=11, skip0을 기록했다. 이는 당시 이력이며 이후 A 전용 저장·웹
연결 상태는 PROJECT_STATUS와 MATERIALS_FLOW의 후속 기록을 따른다.

`model.py`는 main 기준이며 A의 네 타입과 추가 규칙은
`ontologics/materials/schema.py`에 있다. 저장소 루트에서 아래 명령을
명시적으로 실행하면 공통+A+B 정의를 함께 보존한 `ontology/tbox.ttl`을 다시 쓴다.

```bash
python3 -m ontologics.tbox
python3 -m ontologics.tbox --check
```

기존 `python3 -m ontologics.materials.export_tbox`도 같은 공통 생성기로
위임한다. 별도 출력은 `--output /tmp/planning-tbox.ttl`을 사용한다. 동일 계약 타입은
필드·참조 메타데이터가 같을 때 한 번 선언하고, 불일치는 기존 출력 작성 전에 거부한다.
`demo --export-graph`도 같은 T-box와 기존 인계 context 보존 경로를 사용한다.
생성·검사 기준은 [공통 도구 계약](TOOLS.md#공유-t-box-생성-계약)을 따른다.

A 고유 스키마·검증은 담당 폴더에 유지한다. 공유 파일 생성은 A/B 타입의 공통
검증·승인·저장 또는 RDF/SHACL/Neo4j 편입이 아니다. 공통 runtime 버전과
기존 승인·시간·원본 규칙은 변경하지 않는다.

확인일: 2026-10-28, Asia/Seoul. 패키지: `ontologics.materials`.
이 문서는 팀원이 호출할 인터페이스와 현재 상태의 기준이다. 학습 설명은
[`sungsoo/task_01.md`](../../sungsoo/task_01.md), 전체 요구는
[`A_기능_1차_MVP.md`](../../sungsoo/A_기능_1차_MVP.md)를 참고한다.
`sungsoo/`는 이 Git 저장소 밖의 별도 작업 자료이므로 저장소만 받은 팀원은 이 문서와 테스트를 먼저 읽는다.

## 1. 책임과 현재 상태

Task 01은 입력 검사·정규화, 실제 catalog와의 참조 대조, 최소 공통 온톨로지 확장과 등록 그래프 대응을 제공한다.
Task 02의 계약 추출·근거 대응·정확 버전 구성 후보 조회는 9절에 추가했다. 가용 재고·부족·생산량·금액·일정 계산, 신규 Agent 실행·화면은 구현하지 않았다.
후보 검사 함수와 기존 공통 그래프/승인 경로를 읽는 adapter를 제공한다. model.py의 공통 타입4개를 최소 확장했고 별도 승인 시스템은 만들지 않았다.

최초 Task 01 구현에서는 업무 패키지 분리를 선택했다. 아래 기존 파일 변경 기록은 당시 범위다.
기존 파일의 변경은 `pyproject.toml`의 패키지 목록에
`ontologics.materials`을 추가하는 것뿐이다.
기존 Service·Gateway·Runtime·provider·tool_specs·model·TOOLS·SERVICE_SPEC·PROJECT_STATUS는 유지했다.
최초 구현은 스키마를 바꾸지 않았고, 이번 01-C에서만 아래 7절 범위로 공통 타입을 확장했다. 신규 Agent 도구는 등록하지 않았다. 가정 계약/재고 후보는 별도 파일에 두며 대표 그래프나 DB에 자동 적재하지 않는다.

**현재 최소 구현:** 기존 입력 검사·규칙 식별자, B 계약3타입+부품 재고 관측, 부분 후보 자료, 정확 초안/조회 버전·원문 대응 adapter와 테스트.
**문서 기준 PG 검증 완료:** Compose·CI에 고정된 PostgreSQL **17.11**·pgvector **0.8.7** 이미지의 전용 일회용 DB에서 실제 승인/반영/재접속 및 전체207개 검사를 통과했다(8절 최종 결과). PG16 결과는 보조 이력이다. **미완료:** 현재 계약의 정확 시각·배송지 보완, B/C 전체 통합. Task 01 전체 통합 완료가 아니다. 패키지 빌드/격리 import는 과거 기록이며 이번에는 설치·빌드를 수행하지 않았다.

**01-B 당시 계획 기록(현재 결과는 7절):** 담당자가 Task 01-A(기존 이해) → 01-B(직접 확장 정의) → 01-C(최소 공통 연결·재검증)를 진행한다. 현재 공통 변경 계획은 model.py의 신규 정의·검증·직렬화·스키마 버전과 T-box·대표 JSON-LD 동기화다. 업무 contracts·테스트는 실제 대응에 맞춘다. receipts·Service·tool_specs·host·Runtime·provider·identity·database·web와 공통 명세·PROJECT_STATUS는 현재 수정 기본안에서 제외한다. 이번 개정에서는 이 문서와 sungsoo 계획·학습 문서만 바꾸고 기능·스키마·자료·생성 산출물을 변경하지 않았다.

폴더 위치가 기능 제한을 만들지는 않는다. 기존 공개 API로 가능한 부분을 먼저 구현하고 실제 부족 접점만 별도 검토한다. 신규 타입은 기존 초안·다른 사람 검토·정확 버전 반영을 재사용할 수 있으며, 신규 계산 도구 등록과는 다른 작업이다. 기존 SYNTHETIC 집합을 보존하고 신규 origin 규칙을 model에서 명시해 기존 이력 접수 범위가 넓어지지 않는 안을 검증한다.

## 2. 공개 API

```python
from ontologics.materials import validate_planning_context

report = validate_planning_context(
    context,
    reference=reference,                 # 선택: 신뢰된 호스트가 제공
    external_contents=external_contents, # 선택: 신뢰된 호스트가 제공
)
```

```python
validate_planning_context(
    context: dict,
    *,
    reference: dict | None = None,
    external_contents: dict | None = None,
) -> dict
```

`context`만으로 기본 형식을 검사할 수 있다. 참조 그래프·원문 바이트가 없으면 해당 검사는 미확인이다.
`reference`와 `external_contents`는 향후 Agent 도구 스키마의 입력으로 노출하지 말고 호스트에서 주입한다.
이 함수 자체는 호출자를 인증하지 않는다. 호스트가 인증한 승인 버전인지도 입력 주장만으로 입증하지 않는다.

`reference = {'data_version': int, 'nodes': 전체 그래프 목록}`이다.
실제 호스트에서는 기존 `lookup(version=...)` 응답을 다음처럼 대응시킨다.

```python
# host_service와 actor는 기존 호스트에서 고정한 객체·신원이다.
response = host_service.lookup(actor, version=context['data_version'])
if not response['ok']:
    # 기존 오류·run_id를 보존해 호스트의 실패 처리 경로로 전달한다.
    raise RuntimeError(response['errors'])
reference = {'data_version': response['data_version'], 'nodes': response['data']}
```

부분 조회 결과 대신 전체 그래프가 필요하다. 기존 `model.validate_graph()`로 원본 해시·필드·참조를
검사한 후 ID 색인을 만든다. 요청과 참조의 `data_version`이 다르면 오류다.
이는 파일 기반 참조를 실제 승인 DB 스냅샷으로 승격시키는 절차가 아니다.
`external_contents`는 `source_ref -> bytes` 사전이다. 함수는 입력 경로를 열거나 URL을 요청하지 않는다.
해시 일치는 원문 바이트의 일치이며 추출·해석의 정확성이나 사람 승인의 증거는 아니다.

## 3. 입력 필드와 필수 여부

아래 표가 현재 Python 요청 계약이다. 온톨로지 클래스 정의를 대신하지 않는다.

| 필드 | 필수·형식 | 근거·의미 |
| --- | --- | --- |
| `contract_item` | 객체, 필수 | 확장 전달안의 공통 ContractItem 후보에 대응 |
| `contract_item.id` | 공백 없는 문자열, 필수 | 현재는 미등록 항목 접수 참조. 기존 다른 타입의 ID를 재사용하면 오류 |
| `contract_item.configuration_id` | 문자열, 필수 | 기존 Configuration ID. 참조 그래프 제공 시 존재·타입 검사 |
| `contract_item.ordered_quantity` | 양의 정수, 필수 | A 명세의 주문 보드 수량. 등록 adapter가 B ContractItem.quantity에 대응 |
| `contract_item.fulfilled_quantity`, `allocated_quantity` | 각각 0 이상의 정수, 필수 | 기존 이행·배정을 임의 0으로 채우지 않기 위한 명시 입력. 최종 공통 필드명 미확정 |
| `contract_item.unit` | `piece`, 필수 | 확장 전달안 5절의 보드·부품 단위 재사용 |
| `contract_item.delivery_due` | 선택, ISO 날짜 또는 시간대가 있는 시각 | 고객 도착 납기. 없음·null이면 미확인. 원문 정밀도·offset·소수 초 보존 |
| `contract_item.delivery_due_precision` | 선택, `date` 또는 `datetime` | 함수가 원문에서 산출. 입력으로 주면 납기 값이 존재하고 원문과 일치해야 함 |
| `contract_item.constraints_state` | 선택, `unspecified` 또는 `requires_review` | 명시 없음은 허용이 아님. 확인된 허용 상태를 입력만으로 받지 않음 |
| `planned_input_quantity` | 양의 정수, 필수 | 신규 생산 투입 검토 수량. 주문 수량·정상 완료 목표와 구분 |
| `unit` | `piece`, 필수 | 위 수량의 단위. 보드와 부품은 서로 다른 필드에 유지 |
| `data_version` | 0 이상의 정수, 필수 | 기존 공통 응답 기준. 기존 조회에는 `version`으로 전달 |
| `stock_scope.company`, `location` | 비어 있지 않은 문자열, 필수 | 기존 회사 의미와 확장 재고 장소. 바깥 공백만 정리 |
| `stock_scope.as_of` | 시간대가 있는 시각, 필수 | 날짜만으로 재고 기준 시각을 만들지 않음 |
| `stocks` | 비어 있지 않은 목록, 필수 | 일부 재고 목록은 전체 BOM 재고 확인을 뜻하지 않음 |
| `external_inputs` | 비어 있지 않은 목록, 필수 | 각 항목의 `source_ref`, SHA-256 `sha256` 필수. 중복 참조 거절, 해시는 소문자로 정규화 |
| `confirmation_ref` | 선택, 문자열 또는 null | 호스트 확인 기록의 후보 참조. 이 단계에서는 항상 미확인 |
| `production_conditions` | 선택, 있으면 아래 필드 모두 필수 | Task 01의 가정 운영 조건. 없으면 미확인 |

`stocks[]`의 필드는 다음과 같다.

| 필드 | 계약 |
| --- | --- |
| `id`, `lot_id`, `component_id`, `bom_item_id` | 공백 없는 참조, 모두 필수. stock ID 중복 금지 |
| `unit` | `piece`, 필수 |
| `availability_basis` | `on_hand_less_reserved_and_held` 또는 `reported_available`, 필수 |
| `on_hand_quantity`, `reserved_quantity`, `held_quantity` | 첫 산정 방식이면 모두 필수 |
| `available_quantity` | `reported_available`이면 필수. 이미 가용량인 값을 다시 차감하지 않음 |
| 수량 공통 | 0 이상의 정수. `quantity_state='unknown'`일 때만 명시적 null 허용 |
| `quantity_state` | 선택, `known` 또는 `unknown`. null을 0으로 바꾸지 않음 |
| `deductions_disjoint` | 선택, bool 또는 null. 첫 산정 방식에서 true가 아니면 차감 범위 미확인 |

stock ID에 기존 다른 그래프 타입의 ID를 재사용하면 오류다.
기존 BOMItem이 같은 구성·정확한 부품에 연결되는지 검사한다. `ComponentLot`이 실제 참조 그래프에
있으면 타입·부품·회사도 검사한다. 가정 파일의 미등록 로트 참조는 `REFERENCE_NOT_LINKED`로 남긴다.
이 참조로 로트를 새로 생성하지 않는다. 실장 `unknown`도 유지한다.

`production_conditions`의 필수 필드:
`resource_ref`, `available_at`, `setup_minutes`, `boards_per_hour`, `loss_quantity`,
`defect_quantity`, `rework_quantity`, `interruption_policy`, `origin`.
현재 조건은 `origin='synthetic'`, `interruption_policy='none_assumed'`이다.
`available_at`은 시간대가 필요하다. 처리 속도는 양의 정수 대/시간, 준비 시간은 0 이상의 정수 분,
나머지 수량은 0 이상의 정수다. 소수 시간·속도나 다른 중단 정책은 후속 일정 계약에서 확장한다.

모든 객체에서 알려지지 않은 필드를 거절한다. bool은 Python에서 int의 하위 타입이지만 수량으로 받지 않는다.
`100.0`도 정수 JSON 값이 아니므로 거절한다. 수요 잔량 정합성·재고 잔량·예약 중복·가용량·부족량·생산 시간 계산은
후속 Task 02/03/04/07의 책임이다. 이 검사만으로 업무 실행 가능성을 판단하지 않는다.

## 4. 반환과 실패 처리

| 반환 필드 | 의미 |
| --- | --- |
| `valid` | 이 단계에서 검사한 형식·참조에 오류가 없음. 승인·생산 가능 여부가 아님 |
| `status` | `invalid` 또는 `needs_confirmation`. 현재 구현은 확정 가능 상태를 반환하지 않음 |
| `normalized` | 입력의 별도 복사본. 오류가 있으면 후속 업무 입력으로 사용하지 말 것 |
| `errors` | `path`, `code`, `message`. 그래프 오류에는 기존 검증 결과 `details` 포함 |
| `unresolved` | 같은 경로·코드·메시지 구조로 추가 확인·미연결 항목 표시 |
| `warnings` | 기존 그래프 검증기의 경고 보존 |
| `verified_references` | 검증된 참조 그래프에서 존재·타입을 확인한 기존 ID 목록 |
| `verified_external_inputs` | 호스트가 제공한 원문 바이트와 해시가 일치한 입력 참조 |
| `confirmation_state` | `missing` 또는 `unverified`. 문자열 참조만으로 confirmed가 되지 않음 |
| `graph_integration_verified` | 이 일반 함수는 항상 false. 별도 등록 adapter의 엄격한 대응 검사를 거쳐야 true 가능 |

예: `planned_input_quantity=true`는 `INVALID_QUANTITY`, 참조 구성 부재는 `INVALID_REFERENCE`,
원문 변조는 `HASH_MISMATCH`, 버전 불일치는 `REFERENCE_VERSION_MISMATCH`다.
재고의 명시적 null은 `UNKNOWN_QUANTITY`, 사람 확인 참조 부재는 `HUMAN_CONFIRMATION_REQUIRED`,
참조 문자열만 있으면 `HUMAN_CONFIRMATION_UNVERIFIED`다.
자료 부족을 성공이나 0으로 바꾸지 않고 오류와 별도로 전달한다.

## 5. 규칙 버전과 기존 공통 기반

`calculation_rule_version(domain)`은 `materials`, `procurement`, `production`에 대해 각각
`materials-0.1`, `procurement-0.1`, `production-0.1`을 반환한다.
그 밖의 입력은 ValueError다. **식별자 예약만 구현했으며 해당 계산 도구는 없다.**

공통 응답의 필드·감사 기록·오류는 기존 Service의 책임이다. 이 패키지는 별도 envelope를 만들거나
기존 temporal 응답의 `rule_version`을 덮어쓰지 않는다. 기존 기본값은 `temporal-0.1`이다.
일반 계산 결과에는 calculation.rule_version 등으로 실제 계산 규칙을 구분하고 기존 공통 응답을 보존하는 안을 사용한다. Service의 성공·오류·감사 경로 연결은 신규 Agent 도구가 필요한 단계의 별도 검토이며 현재 수정 기본안이 아니다.
따라서 현재 API 반환은 입력 검사 payload이며 AgentGateway/provider에 등록된 도구 응답이 아니다.

## 6. 기준 자료와 실행

`data/materials/`의 계약·재고·생산 조건은 가정 운영 자료다.
`request.json`은 사람이 작성한 구조화 기준 입력과 세 원문의 해시를 보존한다. 자동 계약 추출물이 아니다.
기준 시각은 `2026-10-26T09:00:00+09:00`, 주문·신규 생산 100대, 이행·배정·손실·불량·재작업 0이다.
설계는 실제 `config:esp32-poe:L:WROOM`이다.

| 실제 BOM 행 | 부품 | 한 대당 실제 수량 | 가정 보유 / 예약 / 제한 |
| --- | --- | ---: | --- |
| 55, U4 | LAN8710A-EZC(QFN32) | 1 | 100 / 30 / 0 |
| 9 | 2.2k/R0402 | 8 | 600 / 40 / 0 |
| 14 | 10k/R0402 | 11 | 1200 / 100 / 0 |

부품 ID·BOM·Evidence는 catalog를 사용하고 재고 파일에 BOM 수량을 복제하지 않는다.
로트·재고를 기존 운영 fixture에 합산하지 않는다. 나머지 재고와 미확인 실장 정책은 확인하지 않았다.

저장소 루트에서 실행한다.

```bash
python3 -m unittest tests.materials.test_contracts tests.materials.test_rules tests.materials.test_reference_inputs -v
python3 -m examples.materials_inputs
python3 -m unittest discover -v
```

새 예제는 DB·모델 키 없이 실행한다. `valid=true`, `needs_confirmation`, 외부 입력 3개·기존 참조 7개 확인,
catalog 경고 23개, 미확인 7개, `graph_integration_verified=false`가 기준 결과다.
예제의 `data_version=1`은 로컬 시험 라벨이며 실제 PostgreSQL 승인 버전의 증거가 아니다.
전체 테스트에는 기존 README의 PostgreSQL·psycopg 설정이 필요하다.

최초 구현 당시 검증 기록(기준 189500e): 신규 검사 24개, 기존 Service/Runtime/provider 47개, 전체 120개 통과. 이번 7c5e2d9 기준 문서 개정에서는 재실행하지 않았다.
`python3 -m ontologics.demo`, `python3 -m examples.agent_tools`,
`python3 -m examples.cq_check` 모두 종료 코드 0이다.
wheel 빌드·패키지 포함·소스 트리 밖 격리 import도 확인했다.
이 설치 검사는 패키지 포함·import·일반 입력 검사에 대한 것이다. wheel에는 원본 자료가 없으므로
설치한 코드만으로 원본 파일을 포함한 전체 참조 그래프 검증까지 확인한 것은 아니다.
참조 그래프 검증은 기존 model의 자료 경로 계약을 유지하며 원본 자료가 있는 개발 체크아웃에서 시험했다.
기본 Python 3.11에는 psycopg가 없어 최초 기존 테스트 import가 실패했다.
캐시의 psycopg/binary 3.3.6 및 typing_extensions를 Python 3.12에 연결하고
별도 일회용 PostgreSQL 16·pgvector 0.8.7에서 회귀검사를 실행했다.
저장소 Compose의 PostgreSQL 17 이미지는 이번 검증에 사용하지 않았다.
실제 모델 호출·운영 인증·사람 확인·공통 확장 그래프 통합은 미검증이다.


## 7. Task 01-C 최소 확장·등록 adapter — 2026-10-27

공통 스키마 버전은 `graph-0.5-a-planning-1`, 16타입이다. B 수신 model.py 내부에서 확인한 Contract·ContractItem·DeliveryRound 규칙을 재사용하고 ComponentStockSnapshot만 추가했다. B의 미수신 fulfillment_validation.py·production_validation.py·delivery_validation.py의 추가 제약 동등성은 확인하지 못했다. B 운영7타입·C 품질6타입 전체 병합이 아니다.

| 타입 | 필수 업무 필드·규칙 |
| --- | --- |
| Contract | company/customer_name nonempty text. 계약번호 필드 없음 |
| ContractItem | contract_id→Contract, configuration_id→Configuration, quantity=양의 JSON 정수, unit=piece |
| DeliveryRound | contract_item_id→ContractItem, quantity=양의 JSON 정수, unit=piece, due_at=offset datetime, due_basis=arrival, destination=nonempty text. 공통 모델은 다회차 합계≤주문량 지원 |
| ComponentStockSnapshot | lot_id→ComponentLot, location, as_of(초 필수/소수초 최대6자리/offset), unit=piece, availability_basis, quantity_state=known/unknown. 차감 방식의 보유/예약/제한3키 또는 reported_available의 available_quantity 필수 |

모두 기존 id/type/origin/evidence 요건을 따르고 이 신규4타입은 synthetic만 허용한다. 원문/직접 Evidence도 synthetic, 기존 실제 구성·부품·BOM과 그 근거는 real이다. 로컬 SYNTHETIC 집합은 기존 history_intake의 5타입 목록 그대로다. 계약·항목·회차·관측을 그 접수 경로에 추가하지 않았다.

관측 수량4종은 0이상 JSON 정수, bool/float 거절, unknown일 때만 명시적 null 허용이다. unknown이어도 방식별 필수 키는 필요하다. deductions_disjoint는 선택 bool/null이고 부재/null/false는 차감 범위 미확인 경고다. 동일 lot/location/순간의 별도 ID 관측은 중복이다. 보고 가용량 재차감·가용량·부족·생산 시간 계산은 없다. ContractConstraint도 추가하지 않았다.

기존 `validate_planning_context`의 공개 서명과 요청 필드는 동일하다. 요청 ordered_quantity→그래프 quantity이며 customer/contract_number/ordered_quantity/delivery_due/delivery_due_precision을 그래프에 추가하지 않았다. 일반 함수는 확장 타입이 있어도 EXTENSION_INTEGRATION_PENDING과 graph_integration_verified=false를 반환한다.

```python
from ontologics.materials.reference_adapter import validate_registered_context

# 기존 CommonHost가 신원을 고정한 RequestSession, 원문 bytes, 정확 반영 참조.
result = validate_registered_context(
    session, context,
    external_contents=contents,
    registration_ref={'draft_id': applied_draft_id, 'draft_version': exact_draft_version},
)
```

이 일반 함수는 Agent 도구가 아니다. session·원문·registration_ref·코드의 고정 매핑은 신뢰된 호스트 전용이다. `AgentRegistration`은 기존 허용 도구를 고르는 역할 등록이며 계산 도구 등록과 다르다. Runtime/provider/호스트/DB 구조/공통 도구 정의는 변경하지 않았다.

처리는 정확 get_draft의 id/version/status=applied/applied_version/operations/content_hash를 확인한 뒤 그 applied_version과 같은 요청 data_version으로 필터 없는 전체 lookup을 호출한다. get_draft envelope의 최신 data_version으로 applied_version을 대신하지 않는다. 전체 그래프를 기존 validate_graph로 검증하고 요청·부모·구성·주문량·단일 전량 회차·로트/관측 모든 값과 원문 bytes/hash/Evidence source/locator를 비교한다. 해당 신규 사실·직접 Evidence·SourceDocument의 put이 같은 초안 operations와 조회값에서 모두 일치해야 한다. 기존 real 설계는 묶음에 재등록할 필요가 없다. 무관한 applied 초안·타입 존재·lookup 성공만으로 연결을 입증하지 않는다.

반환은 `{'registration_response': 원래 공통 get_draft 응답 또는 None, 'lookup_response': 원래 공통 lookup 응답 또는 None, 'report': 지역 검사 보고서 또는 None}`이다. 응답의 ok/data/evidence/data_version/schema_version/rule_version/contract_version/warnings/conflicts/errors/run_id를 수정하지 않는다. get_draft/lookup 실패는 원래 실패 응답을 보존하고 report=None로 끝낸다. 입력/등록 상태 오류는 report.errors, 자료/매핑 부족은 report.unresolved다.

고정 매핑은 기존 계약/재고 원문과 새 로트 원문을 SourceDocument/Evidence에 대응한다. 계약 parties=`lines 4-5`, item=`lines 3, 8-13`, stock=`/stocks/N, /stock_scope`, lot=`/lots/N`이다. 계약 필드의 원문 해석은 확인한 정확 bytes SHA에 고정했으며 hash가 같다는 사실을 범용 의미 해석 성공으로 주장하지 않는다. production 원문은 요청 근거만 검사한다. 새 업무 원문은 검토된 호스트 매핑 확장이 필요하다.

stock의 선택 수량/disjoint 키 존재 여부도 비교한다. 누락과 null을 같게 취급하지 않고 quantity_state 생략만 기존 known 의미로 대응한다. 요청·그래프·원문이 서로 맞아야 하며 수량/선택값은 JSON 자료형도 비교한다. 등록 adapter의 datetime은 초·소수초최대6자리·offset을 요구하고 같은 순간의 다른 offset은 허용한다. 이 범위는 기존 후보 입력이나 B 공통 instant를 소급 강화하지 않는다.

A 첫 시나리오는 항목당 한 회차·전량만 지원한다. 복수/누락 회차·부분량·선택값 대응 부족은 REGISTERED_CONTEXT_INCOMPLETE, 값 불일치는 REGISTERED_VALUE_MISMATCH, 승인 묶음 오류는 REGISTRATION_SCOPE_MISMATCH, 원문 대응 부족은 SOURCE_MAPPING_UNCONFIRMED다. 일반 함수의 형식/참조/hash 오류도 유지한다.

현재 contract.txt는 2026-11-10 날짜뿐이고 배송지가 없다. 새 graph_nodes_v1.json은 회차 없는 부분 후보19노드, request_graph_v1.json은 기존 완전 요청에 로트 source_ref/hash를 더한 별도 파일이다. 그 data_version=1은 로컬 시험 라벨이다. lot_reference_v1.json은 사용자 확정 회사/공급처/3코드와 정확 부품 대응·가정 고지·실제 작성 시각을 가진다. SourceDocument 수집 시각은 별도 기록했고 실제 bytes에서 SHA를 산출했다. 원본4파일을 변경하지 않았다.

contract_delivery_v1.json은 만들지 않았다. 코드의 _DELIVERY_SOURCE는 근거 보완을 실제 확보한 이후 사용할 매핑 자리이며 현재 값을 자동 생성하지 않는다. 현재 사례는 승인을 거치더라도 전체 연결 false다. 정상 단위/PG 시험은 별도 임시 합성 원문과 시험 호스트 매핑을 만들고 정리한다. 이를 현재 계약 확정값이나 승인된 운영 자료로 취급하지 않는다.

연결 true는 나열한 등록 대응 범위만 뜻한다. unknown 수량·차감 범위·전체 BOM 재고·제약·업무 사람 확인 미확인은 남고 status=needs_confirmation이다. 이행/배정·투입·생산 조건도 이 연결로 운영 검증됐다고 주장하지 않는다.

기존 graph_jsonld/tbox_turtle로 T-box와 JSON-LD context를 동기화했다. 대표474노드의 사실/ID/관계는 그대로이며 새 가정 노드를 합산하지 않았다. JSON-LD context의 contract_id/contract_item_id만 추가했다.

## 8. 이번 검증과 미실행

아래는 같은 날의 **직전 DB 없는 실행 이력**이다. 최신 결과는 이 절 끝의 「PostgreSQL17.11 기준 최종 검증」을 우선한다.

2026-10-27 직전 실행 결과다. 패키지 설치·DB 변경·git add/commit/push·PR 생성 없이 실행했다. DB 없는 검사는 기본 Python3.11에서도 가능하고, 전체 import/접수 경계 검사는 기존 캐시의 Python3.12 + psycopg3.3.6/typing_extensions를 PYTHONPATH로 연결했다. PostgreSQL URI와 .env/.env.local은 준비되어 있지 않았다. 다른 서비스의 실행 중 DB는 사용하지 않았다.

| 명령 | 이번 결과 |
| --- | --- |
| `python3 -m unittest tests.materials.test_ontology_extension tests.test_model tests.test_ingest tests.test_temporal -v` | 52 통과 |
| `python3 -m unittest discover -s tests/materials -t . -v` | 63개: 59 통과, 전용 PG4 skip(캐시 의존성 환경) |
| `python3 -m unittest tests.test_model tests.test_ingest tests.test_temporal tests.test_service tests.test_host tests.test_intake tests.test_agent_extensions -v` | 85개: 42 통과, 43 DB URI 미설정 오류 |
| `python3 -m unittest discover -v` | 207개: 116 통과, 87 DB URI 미설정 오류, 4 skip. 전체 통과 아님 |
| `python3 -m examples.materials_inputs` | 종료0, valid=true/needs_confirmation/graph_integration_verified=false; 참조7·원문3·미확인7·경고23 |

전체87오류는 DatabaseSettings가 PostgreSQL URI 없음으로 차단한 것이며, 그 테스트 본문을 완료한 결과가 아니다. 모델·시간·수신 근거·생성물의 DB 없는 검사는 통과했다. 정확한 실패 테스트 이름과 출력은 이번 로컬 `/tmp/task01c_full_tests.log`에 남겼다. 관련 회귀는 `/tmp/task01c_regression.log`, 신규 planning은 `/tmp/task01c_planning.log`다. 로그는 임시 실행 자료이며 저장소 산출물이 아니다.

새 PG4개는 tests.db의 격리된 test_<uuid> schema에서 기존 공개 CommonHost 경로의 초안/다른 검토자/정확 반영/재접속·재시도/구버전/stale base/권한·기존5타입 접수를 검증하도록 작성했다. TEST_DATABASE_URL이 없으면 명시적으로 skip하며 대체 DB는 만들지 않는다. 신규4타입의 실제 history_intake 거절은 DB 접근 전 검사로 별도 통과했다.

전용 PG가 필요한 demo·cq_check·agent_tools·host_workflow의 실제 실행, 실제 인증·LLM·화면·운영 승인 검증은 미실행이다. 정상 adapter 단위 시험의 public 응답 stub은 실제 DB 승인 증거가 아니다. B/C 전체 병합·미수신 검증3모듈과 팀 ID 정책도 후속이다. Task02 이후 기능은 진행하지 않았다. 기존 pyproject 스테이징·수신 원본과 범위 밖 파일 보존 여부는 마지막 hash/상태 검사로 확인한다.


unknown 상태는 입력된 모든 수량이 정수여도 UNKNOWN_QUANTITY 미확인으로 남긴다. 이 경계를 실패 테스트로 재현한 뒤 보완했으며 최종 전체207개 결과는 116통과/87DB설정오류/4skip이다. 읽기 전용 별도 코드 검토에서 이 보완점 외 중대 결함은 발견되지 않았다. 보호 공통 파일 hash와 기존474사실 보존도 독립 확인했다.

변경 파일은 아래17개로 제한했다. 기존 pyproject.toml staged patch는 착수 전 bytes와 같고, 수신 B/C·기존4입력·catalog·demo_records·data/sources·그 외 범위 밖 파일 hash도 그대로다.

| 구분 | 파일 |
| --- | --- |
| 공통/업무 코드 | ontologics/model.py, ontologics/materials/contracts.py, ontologics/materials/reference_adapter.py(신규) |
| 기존 테스트 | tests/materials/test_contracts.py, test_reference_inputs.py |
| 신규 테스트 | tests/materials/test_ontology_extension.py, test_reference_adapter.py, test_graph_integration.py |
| 신규 자료 | data/materials/lot_reference_v1.json, graph_nodes_v1.json, request_graph_v1.json |
| 생성물 | ontology/tbox.ttl, data/initial_graph.jsonld |
| 문서 | docs/MATERIALS.md, 저장소 밖 sungsoo/task_01_plan.md, A_온톨로지_스키마_확장_전달안.md, task_01_learning/05_최신_코드와_변경_계획.md |

추가 회귀 `python3 -m unittest tests.materials.test_graph_integration tests.test_host tests.test_intake tests.test_agent_extensions -v`는 27개 중 4통과/19DB설정오류/4skip이었다. 테스트명 선택을 잘못한 일회 진단 호출의 loader오류2개는 최종 올바른 명령 실행 결과와 구분하며, 기능 회귀 실패로 집계하지 않았다. 최종 `git diff --check`와 문서 상대 파일 링크 검사도 통과했다.


### PostgreSQL16.15 보조 검증 이력 — 2026-10-27 Asia/Seoul

사용자 지정 후속 프롬프트에 따라 현재 코드를 수정 없이 검증했다. 브랜치 `feature/a-first-mvp`, HEAD `7c5e2d99d5a12c5b1ccf2bcc3da8ea5acfe2f271`, 모델 `graph-0.5-a-planning-1`·16타입이다. 위 직전 DB URI 오류/skip 기록을 이번 결과로 재사용하지 않았다.

전용 DB 설정과 환경 파일이 없어서 이미 로컬에 있던 `pgvector/pgvector:0.8.7-pg16-bookworm` 이미지(ID `sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a`)를 `--pull=never`로 사용했다. 실측 PostgreSQL **16.15 (Debian 16.15-1.pgdg12+2)**, pgvector **0.8.7**이다. 저장소 Compose 기준 PostgreSQL 17.11/pgvector 0.8.7의 고정 이미지는 로컬에 없어 사용하지 않았다. PostgreSQL 주버전 차이가 있으며 17에서의 동일 결과까지 주장하지 않는다.

새 컨테이너 `ontologics-task01c-5631914012ce`와 그 안의 빈 전용 DB를 사용했다. 포트는 Docker가 고른 빈 포트를 127.0.0.1에만 연결했다. PGDATA는 tmpfs이며 기존 컨테이너·DB·볼륨·호스트 데이터 경로를 재사용하거나 변경하지 않았다. Python **3.12.10**, 캐시된 psycopg/psycopg_binary **3.3.6**, typing_extensions **4.16.0**을 사용했다. 이미지 다운로드·패키지 설치는 없었다. `TEST_DATABASE_URL`과 `DATABASE_URL`은 같은 새 DB를 가리키도록 실행 프로세스 환경에만 설정했고 URI·비밀번호를 파일/채팅/결과에 기록하지 않았다.

| 명령(저장소 루트, Python 3.12.10) | 종료 코드 | 이번 결과 |
| --- | ---: | --- |
| `python -m unittest discover -s tests/materials -t . -v` | 0 | 63 통과 / 실패0 / 오류0 / skip0. 신규 PG4개 모두 실제 실행 |
| `python -m unittest tests.test_model tests.test_ingest tests.test_temporal tests.test_service tests.test_host tests.test_intake tests.test_agent_extensions -v` | 0 | 85 통과 / 실패0 / 오류0 / skip0 |
| `python -m unittest discover -v` | 0 | 207 통과 / 실패0 / 오류0 / skip0 |
| `python -m examples.materials_inputs` | 0 | valid=true, needs_confirmation, graph_integration_verified=false |
| `python -m ontologics.demo --output <임시 경로>/demo_result.json` | 0 | 새 schema, 데이터 버전1→2→3 |
| `python -m examples.cq_check --output <임시 경로>/cq_result.json` | 0 | CQ/실제 pgvector SQL 등 checks15개 모두 true |
| `python -m examples.agent_tools` | 0 | 새 tools schema의 기존 공개 도구 예제 성공 |
| `python -m examples.host_workflow` | 0 | 새 host_demo schema, 자기 승인/미승인/구버전 차단, 정확 v2 검토 및 재시도 성공 |

신규 PG4개는 기존 tests.db의 `test_<uuid>` schema와 CommonHost/RequestSession/Service를 그대로 사용했다. 계약·항목·로트·관측·직접 SourceDocument/Evidence를 한 put 묶음으로 준비해 승인 전 확정 그래프 불변, 개발 작성자의 자기 승인 금지, 다른 개발 검토자의 정확 버전 승인, 호스트 반영, 재시도, 새 RequestSession의 adapter 조회를 확인했다. adapter는 공개 get_draft의 정확 id/version/status=applied/applied_version/content_hash/operations와 동일 버전의 필터 없는 전체 lookup을 검사하며 기존 envelope를 보존한다. 요청 ordered_quantity/등록 quantity·부모·구성·회차·로트·관측 및 원문 hash/Evidence locator 대조가 이 실제 경로에서 수행됐다.

현재 날짜-only/배송지 없음 묶음은 승인·반영·재접속 후에도 **valid=true / graph_integration_verified=false / needs_confirmation**이다. 별도 임시 합성 시각·배송지 근거를 사용한 정상 묶음은 **valid=true / graph_integration_verified=true / needs_confirmation**이다. true는 대응 범위만 뜻하며 사람 확인·생산 가능·실재고 확정이 아니다. 현재 계약에는 시각·배송지를 보충하지 않았고 contract_delivery_v1.json은 생성하지 않았다.

무관한 applied 묶음, 누락/변조 operations, 버전/값/필수값 불일치, 조회 실패 envelope, bool/float/null, unknown, 선택 키 부재, 정밀도, 복수/부분 회차는 기존 adapter/모델 단위검사에서 통과했다. 이 음성 응답·변조 사례의 public response stub을 실제 PG 장애/저장 변조 시험으로 확대 해석하지 않는다. 기존5타입 history_intake 실제 접수 성공과 신규4타입 거절, 구버전·stale base·권한·재시도·재접속은 PG 검사에서 통과했다. 개발 신원 시험이며 실제 사람 인증·LLM·운영 승인 검증은 아니다.

세 테스트 명령 종료 직후 잔여 사용자 schema는 각각0개였다. 예제4개는 각각 새 schema를 사용했고 이번 생성 schema만 DROP한 뒤 잔여0개를 확인했다. 새 컨테이너 삭제는 종료0, 이후 inspect로 부재를 확인했다. tmpfs 데이터와 임시 합성 원문도 정리됐다. 전체 검사에서 생성된 synthetic 접수 파일1개는 착수 전 부재·내용·SHA-256과 tests.test_agent_workflows 출처를 확인해 해당 파일만 제거했다. 증거 로그/결과 JSON/실행 스크립트는 `/tmp/task01c-pg-20261027-soud38h3/`에 남겼으며 영구 문서 산출물이나 비밀 설정 파일이 아니다. demo export·catalog 재생성은 하지 않았다.

이번 실행에서 발견된 코드 결함과 코드/테스트 수정은 없다. 수신본은 실행/import하지 않고 AST/텍스트로 대조했다. model.py SHA-256은 `35ec51bf11df3ba0cbe7c68d6d7875ae461512d9089397b7af0e8da8f2a61f05`, B는 `c06753223ac23aa3a7cf83d0f12e27dbabbc3c29a5fcd71cd79d60bf19d32c76`, C는 `c8f676eff5137ef4517d81f18dcda90851e757342efe4b04280e4c82fac1f7c5`다. 기존12타입 required/refs와 B 계약3타입 정의가 일치했다. B의 검증3모듈은 여전히 없으며 가짜 모듈·우회를 만들지 않았다.

착수 전 파일 SHA-256 및 staged/unstaged patch와 대조해 이번 변경은 이 문서와 지정된 sungsoo 계획·설계·학습05 문서3개뿐임을 확인했다. 기존 pyproject.toml staged patch, 기존 미추적 파일, 학습06/프롬프트, 수신 B/C 원본, 입력4파일, catalog/demo_records/data/sources, 공통 코드, T-box와 대표 JSON-LD474노드의 bytes·사실·ID·관계를 보존했다. git add/commit/push·PR·다른 채팅/자동화는 수행하지 않았다. Docker 최초 접근은 sandbox의 socket 접근 제한이었고 허용된 권한 실행으로 해결했으며 자동 승인 검토 거절은 없었다.

**PG16 당시 판정(보조 결과):** Task 01-C 코드의 PG 연결은 위 환경에서 확인했으나 문서 기준 검증 완료로 계산하지 않는다. 현재 실제 계약 자료의 정확 offset 시각·배송지는 부족해 완전 연결은 미완료다. B/C 전체 통합·B 미수신 검증모듈·팀 ID/접수 정책, PostgreSQL 17 재검증, 실제 인증/운영 수용은 남는다. Task02 이후 기능은 진행하지 않았다.


**PG16 완료 판정 정정 이력 — 2026-10-27:** PostgreSQL16.15의 결과는 보조 검증으로만 보존하고 문서 기준 PostgreSQL17.11/pgvector0.8.7에서의 재검증을 완료 조건으로 정정했다. 당시에는 고정 이미지가 없어 재실행 대기였으며, 이후 사용자가 지정 이미지 다운로드와 전용 일회용 DB 재검증을 승인했다. 아래 PostgreSQL17.11 최종 결과로 이 대기 항목을 해소했다.


### PostgreSQL17.11 기준 최종 검증 — 2026-10-27 Asia/Seoul

**현재 코드의 Task 01-C 검증은 문서 기준 환경에서 완료했다.** 사용자 승인으로 Compose·CI의 고정 이미지 `pgvector/pgvector:0.8.7-pg17@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d` 한 개를 다운로드했고 digest 일치를 확인했다. 실측은 **PostgreSQL 17.11 (Debian 17.11-1.pgdg12+2) / pgvector 0.8.7**이다. Python3.12.10·기존 캐시 psycopg/binary3.3.6·typing_extensions4.16.0을 사용했으며 패키지 설치는 없었다. 아래 수치는 이번 PG17 실행 결과이며 PG16의 과거 수치를 재사용하지 않았다.

| 명령 | 종료 | 이번 결과 |
| --- | ---: | --- |
| `python -m unittest discover -s tests/materials -t . -v` | 0 | 63 통과, 신규 PG4 모두 실행, 실패0/오류0/skip0 |
| `python -m unittest tests.test_model tests.test_ingest tests.test_temporal tests.test_service tests.test_host tests.test_intake tests.test_agent_extensions -v` | 0 | 85 통과, 실패0/오류0/skip0 |
| `python -m unittest discover -v` | 0 | 207 통과, 실패0/오류0/skip0 |
| `python -m examples.materials_inputs` | 0 | valid=true / needs_confirmation / graph_integration_verified=false |
| `python -m ontologics.demo --output <임시 경로>/demo_result.json` | 0 | 새 schema, 데이터 버전1→2→3 |
| `python -m examples.cq_check --output <임시 경로>/cq_result.json` | 0 | checks15개 모두 true, 실제 pgvector SQL 포함 |
| `python -m examples.agent_tools` | 0 | 새 schema의 기존 공개 도구 예제 성공 |
| `python -m examples.host_workflow` | 0 | 새 schema, 정확 버전 승인/반영·재시도·권한 경계 성공 |

기존 CommonHost/RequestSession/Service와 tests.db의 test_<uuid> schema를 재사용했다. 계약/항목·로트/관측·직접 SourceDocument/Evidence의 한 put 묶음, 승인 전 미반영·자기 승인/조회 전용 쓰기 차단, 다른 개발 검토자의 정확 버전 승인, 호스트 반영, 새 RequestSession의 공개 get_draft/동일 버전 전체 lookup 및 adapter 실제 대조가 통과했다. 기존5타입의 실제 history_intake 접수 허용·신규4타입 거절과 구버전/stale base/권한/재시도도 통과했다. operations/원문 hash/Evidence locator/값·버전/필수값·bool/float/null·unknown·선택키 부재·정밀도·복수/부분 회차·조회 실패 envelope 경계도 기존 테스트에서 통과했다. 변조 응답/조회 실패에 쓰는 단위시험 stub을 실제 DB 장애 검증으로 확대하지 않는다.

실제 승인·반영·재접속 후 **현재 날짜-only·배송지 없음 사례는 valid=true / graph_integration_verified=false / needs_confirmation**을 유지했다. **별도 임시 합성 시각·배송지 원문을 사용하는 정상 사례만 valid=true / graph_integration_verified=true / needs_confirmation**이다. 현재 계약의 정확 시각·시간대·배송지를 임의로 채우지 않았고 contract_delivery_v1.json도 생성하지 않았다. 개발 신원 검증이며 실제 사람 인증·운영 승인·생산 가능·실재고 확정은 아니다.

기존 컨테이너/볼륨/DB를 건드리지 않고 새 전용 DB·127.0.0.1 동적 포트·PGDATA tmpfs만 사용했다. 테스트별 schema는 세 명령 종료 때 각각 잔여0개, 예제4개의 새 schema도 해당 자원만 삭제해 최종 잔여0개를 확인했다. 최종 시험 컨테이너 `ontologics-task01c-a3ea5b896ebf`는 삭제 종료0·inspect 부재를 확인했다. 초기 접속 시도 컨테이너도 삭제했다. 임시 합성 원문과 이번 tests.test_agent_workflows의 synthetic 접수파일1개도 식별해 정리했다. 다운로드한 고정 이미지는 이후 동일 환경 재검증을 위해 로컬 캐시에 보존했다.

처음의 실행 준비 시도는 이미지 초기화 중 임시 Unix socket 서버를 pg_isready가 준비 완료로 판단해 TCP 접속이 끊겨 **테스트0개 실행** 상태로 종료됐다. 이미지 entrypoint가 TCP를 열지 않는 임시 서버를 시작·종료한 뒤 최종 서버를 시작하는 것을 확인했고, 저장소 밖 임시 실행 스크립트의 준비 확인에 `-h 127.0.0.1`을 지정해 최종 TCP 서버를 기다리도록 했다. 이후 위 전체 명령이 통과했다. 최초 준비 실패를 제품 코드 결함이나 테스트 assertion 실패로 집계하지 않는다.

이번에 발견한 저장소 코드 결함과 코드/테스트 수정은 없다. model.py와 수신 B/C hash는 이전 기록과 동일하며 B/C를 실행/import하거나 가짜 검증 모듈을 만들지 않았다. B의 fulfillment_validation.py·production_validation.py·delivery_validation.py는 여전히 미수신이다. 착수 전 파일 hash·staged/unstaged patch·Git 상태와 대조해 지정 결과 문서4개만 변경했고, 기존 pyproject 스테이징·미추적 자료·학습06/프롬프트·수신본·입력4개·catalog/demo_records/data/sources·공통 코드와 대표474노드의 사실/ID/관계 및 생성물 bytes를 보존했다. git add/commit/push·PR·다른 채팅/자동화·demo export·catalog 재생성은 하지 않았다.

이번 로그·결과 JSON·보존 증거·실행 스크립트는 `/tmp/task01c-pg17-prepared-20261027/`에 있다. URI/비밀번호는 실행 프로세스 환경에만 두었으며 문서·채팅·로그·.env에 저장하지 않았다. `TEST_DATABASE_URL`과 `DATABASE_URL`은 같은 새 시험 DB를 가리켰다. 자동 승인 검토 거절은 없었다.

**남은 범위:** 현재 코드의 Task 01-C PostgreSQL 연결 검증은 완료다. 현재 계약의 정확 offset 시각·배송지 원문 확보에 따른 완전 연결, B/C 전체 통합·미수신 검증3모듈·팀 ID/접수 정책, 실제 인증/운영 수용은 별도 미완료다. PostgreSQL17 재검증 대기는 이번 결과로 해소했다. Task02 이후 기능은 추가하지 않았다.


## 9. Task 02 계약 추출·해석 후보·정확 버전 구성 조회 — 2026-10-28

**상태: 부분 완료.** 텍스트/텍스트 PDF 추출, 해석 입력 근거 대조·수요 후보·질문·수정 전후, 기존 공개 조회와 PG 검증·학습 문서는 구현했다. 실제 모델 호출과 사람의 생산 수요 확인을 검증하는 공통 접점은 미연결이다. 확인된 수요를 다음 계산에 전달하는 Task02 전체 완료를 뜻하지 않는다. Task01-C 코드 검증 완료, 현재 계약의 정확 납기 시각·offset·배송지 부족, B/C 전체 통합 미완료를 각각 유지한다.

### 공개 Python 계약 (호스트 내부용)

```python
from ontologics.materials import (
    extract_contract_text, prepare_contract_demand, read_contract_demand)
from ontologics.materials.configuration_lookup import lookup_contract_configurations

extract_contract_text(path: Path) -> dict
prepare_contract_demand(extracted: dict, interpreted: dict,
                        configurations: dict, *, previous: dict | None = None) -> dict
lookup_contract_configurations(session, *, data_version: int) -> dict
read_contract_demand(session, context: dict, extracted: dict,
                     interpreted: dict, *, previous: dict | None = None) -> dict
```

파일 경로·원문 bytes·context·구성 묶음·session은 신뢰된 호스트가 제공한다. 모델/일반 요청에 임의 경로 읽기 도구를 등록하지 않았다. 추출/조회 실패를 정상 수요로 반환하지 않는다. 기존 receipts.source_intake는 ODS 전용이며 계약 receipt·승인 사실을 새로 발급하지 않았다.

- 추출 결과: `supported/status/source/source_bytes/text/segments/page_count/errors`. source는 path·원문 SHA-256·byte_length·format이다. UTF-8 본문의 줄바꿈/bytes를 보존한다. segments는 id·text·전체 추출 본문의 문자 start/end·line·paragraph·page를 갖는다. line/page는 1부터, end는 제외다. page는 텍스트에서는 None이다. 파일 접근 오류는 호스트로 전달한다.
- 해석 입력: `source_sha256`, `data_version`, `items[].fields`. 각 field는 `[{value, evidence:[{segment_id,start,end,quote}]}]` 주장 목록이다. evidence offset은 해당 segment 내부 Unicode 문자 기준이다. 여러 값의 상충은 남겨 둔다. 내용 추출은 자연어 의미 해석을 수행하지 않는다.
- 지원 field: `contract_item_id/company/customer/manufacturer/product_model/revision/variant/configuration_id/ordered_quantity/fulfilled_quantity/allocated_quantity/planned_input_quantity/unit/delivery_due/constraints`. 문자열 값과 정확한 인용을 대조한다. unit은 현재 literal `piece`만 지원하며 단위 번역을 하지 않는다. 선택 입력 `items[].proposed_configuration_id`는 조회 후보와의 일치만 검사하고 사람 선택 완료를 뜻하지 않는다.
- configurations는 원래 계획의 목록에서 `data_version/candidates`를 가진 묶음으로 최소 보완했다. 순수 함수에서 버전 없는 목록을 받으면 오류다. 묶음은 호스트 입력이며 그 자체로 실제 DB 조회/인증 증거는 아니다.
- 조회 adapter는 필터 없는 `lookup(version=context.data_version)`과 각 기존 구성의 `configuration(configuration_id=..., version=context.data_version)`만 호출한다. 입력/반환 버전·config→revision→product·BOM/부품의 관계와 전체 snapshot 일치를 검사한다. 기존 그래프/메모리 DB/호스트/공통 검증기를 복제하지 않았다.
- 조회 반환은 `lookup_response/configuration_responses/configurations/errors`. 원 공통 envelope를 변경하지 않는다. 실패/버전 불일치/관계 오류면 configurations=None이며 `read_contract_demand`의 report=None다. 순수 함수의 자료 오류는 valid=false/status=invalid다. 공개 호출 exception은 비밀값 없는 PUBLIC_QUERY_EXCEPTION으로 구별한다.
- 후보 결과: `source/original/interpretation/items/errors/unresolved/questions/binding/binding_sha256/revision/confirmation/valid/status`. original에 본문/segments, fields에 원 주장과 지원 상태가 남는다. `contract_item_candidate.ordered_quantity`와 `common_item_candidate.quantity`를 대응한다. common_item_candidate는 그래프 등록 노드가 아니다. planned_input_quantity도 별도 근거이며 계산하지 않는다.
- 원문 hash/추출 본문/위치, 해석 hash, 정확 int 수량·단위·납기 형식을 검사한다. 근거 없음(EVIDENCE_MISSING), 틀린 구간(EVIDENCE_LOCATION_MISMATCH), 인용과 다른 값/잘린 숫자(EVIDENCE_VALUE_MISMATCH), 상충(CONFLICTING_VALUES), 누락(MISSING_VALUE)을 구별한다. 이 검사는 원문의 미해석 문장까지 전수 해석하거나 의미상 완전성을 보증하지 않는다.
- 날짜-only는 date 정밀도를 유지한다. 제약 미기재는 unspecified, 제약 주장이 있으면 requires_review이며 변경/대체 permission은 항상 None이다. 이행/배정/신규 검토량을 문서 누락에서 0/주문량으로 만들지 않는다. 이름만 보고 L/L1·WROOM을 확정하지 않는다.
- binding은 원문 SHA·입력 버전·선택 구성·후보 값/근거를 결합한다. previous는 표시 이력일 뿐 인증 자료가 아니다. 수정 전후 before/after와 binding_changed·confirmation.invalidated를 반환한다. hash는 서명/확인 토큰이 아니다. `confirmed=true`·임의 confirmation_ref·모델 승인 주장은 supplied_claims에 보존하지만 항상 verified=false/state=unavailable다.

`confirm_contract_demand`는 가짜 성공 API로 구현하지 않았다. 공개 `RequestSession.review`/`CommonHost.apply_approved`는 그래프 초안 검토/반영이며 수요 업무 확인의 영속 기록을 조회하지 못한다. 추가 공통 수정 후보는 **host.py RequestSession의 명시적 사람 확인/조회 접점과 service.py의 결합별 영속 확인 기록**이다. 최소 저장/검증 정보는 원문 SHA·정확 입력 버전·항목별 구성/수량/근거·수정 차수·안정된 확인자 신원·시각·무효화 상태다. 현재 공개 API로는 이 결합의 업무 확인을 읽을 수 없다. 팀 신원/권한·스키마·감사/재시도·수정 무효화 계약에 영향이 있으므로 이번에는 적용하지 않았다. 후속 통합 시 원문/버전/수량/선택 변경 후 이전 확인 거절, 재접속/같은 키 재시도/동시성·권한, 기존 graph review/apply 불변을 검증해야 한다. 그 전 추출·후보·조회·검증·학습은 실행 가능하다.

### PDF와 실행 예제

optional pypdf가 실행 환경에 있으면 텍스트 PDF의 페이지별 본문/근거를 지원한다. 없으면 PDF_EXTRACTOR_UNAVAILABLE, 텍스트 없는 페이지가 하나라도 있으면 NO_TEXT_OR_OCR_REQUIRED다. 빈 정상 페이지도 최소 지원 범위에서는 거절될 수 있다. 암호화·손상 PDF는 unsupported이며 스캔/OCR·복잡 표의 읽기 순서·범용 의미 해석은 미지원이다. PDF의 위치는 추출 텍스트 좌표이며 화면 bounding box나 PDF bytes offset이 아니다.

현재 한글 contract.txt와 같은 내용을 임시 PDF로 만들어 비어 있지 않은 17줄 전체·페이지1·주문100·date 정밀도·원문 hash 보존을 대조했다. bundled reportlab4.4.9/pypdf6.10.0과 Poppler 렌더의 육안 검사를 사용했다. PDF 추출기가 빈 줄을 생략하므로 원문의 줄 번호를 PDF에 그대로 복사하지 않고 내용별로 위치를 대조했다. 생성 PDF/PNG는 시험 후 정리하고 원본 자료로 등록하지 않았다. 단위시험은 기존 캐시 pypdf6.12.2에서 같은 합성 계약·다중 페이지·텍스트 없는 페이지·암호화·손상 입력을 확인했다. 패키지 설치는 없다.

```bash
python -m unittest tests.materials.test_contract_intake -v
python -m examples.materials_contract_intake
```

예제는 명시적으로 준비한 TEST_DATABASE_URL 없이는 종료2로 멈추고 DB를 자동 선택하지 않는다. tests.db.isolated_database의 새 test_<uuid> schema에 기존 catalog를 개발 시드하고 조회 전용 신원의 공개 RequestSession으로 구성 두 건을 읽고 schema를 정리한다. `reference_interpretation`은 현재 contract.txt의 고정 hash에만 대응하는 수작업 해석 입력이다. 문자열 시연을 범용 파서/실제 LLM 성공으로 설명하지 않는다.

현재 예제는 valid=true / needs_confirmation / confirmation.verified=false이며 L/WROOM100 후보·이행0/배정0/신규검토100의 명시적 시연 가정·date 납기·제약 검토를 보존한다. 계약 사실/DeliveryRound를 새로 등록하지 않았다. 기존 materials_inputs는 여전히 valid=true / graph_integration_verified=false / needs_confirmation이다. contract_delivery_v1.json은 만들지 않았다.

### 이번 최종 검증

최종 실행 환경은 Python **3.12.10**, 기존 캐시 psycopg/binary **3.3.6**·typing_extensions **4.16.0**·optional pypdf **6.12.2**다. 패키지를 설치하지 않았다. DB는 지정 digest의 새 전용 컨테이너에서 실제 SELECT로 **PostgreSQL 17.11 (Debian 17.11-1.pgdg12+2) / pgvector 0.8.7**을 확인했다. TEST_DATABASE_URL과 DATABASE_URL은 프로세스 환경에서 같은 전용 DB를 가리켰다. 아래는 이번 최종 수정본 실행 결과다.

| 명령 | 종료 | 통과 / 실패 / 오류 / skip 또는 실제 결과 |
| --- | ---: | --- |
| `python -m unittest tests.materials.test_contract_intake -v` | 0 | 36 / 0 / 0 / 0 |
| `python -m unittest discover -s tests/materials -t . -v` | 0 | 99 / 0 / 0 / 0 |
| `python -m unittest tests.test_model tests.test_ingest tests.test_temporal tests.test_service tests.test_host tests.test_intake tests.test_agent_extensions -v` | 0 | 85 / 0 / 0 / 0 |
| `python -m unittest discover -v` | 0 | 243 / 0 / 0 / 0 |
| `python -m examples.materials_inputs` | 0 | valid=true / needs_confirmation / graph_integration_verified=false (기존 로컬 입력 예제) |
| `python -m examples.materials_contract_intake` | 0 | 실제 공개 PG 조회 v1·구성2건, valid=true / needs_confirmation / 업무 확인 false |
| `python -m ontologics.demo --output <임시 경로>/demo_result.json` | 0 | 새 schema, 데이터 버전1→2→3 |
| `python -m examples.cq_check --output <임시 경로>/cq_result.json` | 0 | checks15개 true, 실제 pgvector SQL |
| `python -m examples.agent_tools` | 0 | 새 schema의 기존 공개 도구/개발 호스트 예제 성공 |
| `python -m examples.host_workflow` | 0 | 새 schema의 기존 공개 도구/개발 호스트 예제 성공 |

그룹 간 동일 테스트가 포함되므로 수치를 합산하지 않는다. 새 Task02 PG1개와 기존 Task01-C PG4개가 planning/전체에서 실제 실행됐다. skip0이며 mock을 PG 성공으로 계산하지 않았다.


신규 PG1개는 tests.db에서 catalog bootstrap 후 실제 RequestSession의 정확 v1 lookup/configuration·새 요청 재접속·없는 v999 실패를 실행했다. DB 응답의 틀린 버전/관계·조회 실패/잘못된 BOM 모양은 별도 단위시험 대역이며 실제 DB 장애로 집계하지 않는다. 개발 신원이고 실제 사람 인증/운영 승인/전체 Agent 통합은 아니다.

독립 읽기 전용 코드 리뷰에서 숫자의 부호·소수·쉼표 부분 인용과 비유한 수량의 hash 직렬화 오류를 발견해 RED→GREEN으로 보완했다. 추가 입력 경계 검토의 BOM id 누락도 재현 후 오류 반환으로 보완했다. 최초 시험 도우미의 0 인용 위치 오류는 올바른 이행/배정 줄로 수정했다. 최종 수치는 이 보완이 모두 반영된 재실행 결과이며 직전 Task01-C207개나 중간 Task02240/242개 기록을 재사용하지 않았다.

고정 이미지의 새 컨테이너·DB, 127.0.0.1 동적 포트, PGDATA tmpfs를 사용했으며 기존 공유/운영 DB·컨테이너·볼륨·호스트 데이터 경로를 재사용/마운트하지 않았다. 최종 TCP 준비를 `pg_isready -h 127.0.0.1`로 확인했다. 각 테스트 그룹 뒤 새 schema 잔여0, 예제의 새 schema4개도 식별해 삭제했고 Task02 예제 schema는 context manager로 자체 정리했다. 최종 컨테이너 `ontologics-task02-5049c88b2f31` 삭제 종료0/inspect 부재, 사용자 schema 잔여0, 새 접수 JSON1개 삭제와 기존 접수파일 보존을 확인했다. 이전 중간 실행 컨테이너도 모두 정리했다. 고정 이미지는 로컬 캐시에 유지한다. Docker 최초 sandbox 접근 제한은 승인된 권한 실행으로 해결했으며 자동 승인 검토 거절은 없었다.

기록은 `/tmp/task02-validation-20261028/`의 results.json·명령별 로그·pdf_crosscheck.json·실행 스크립트와 `/tmp/task02-baseline-20261028/`의 착수 전 hash/Git 상태·patch·실패/성공 로그다. URI·비밀번호는 실행 프로세스 환경에만 두었으며 문서·채팅·로그·.env에 저장하지 않았다. PDF/원문 임시 시험 파일은 정리하고 결과만 남긴다.

학습 문서는 [`sungsoo/task_02.md`](../../sungsoo/task_02.md), 계획 보완은 [`task_02_plan.md`](../../sungsoo/task_02_plan.md)다. 원본/공통 코드·기존 변경 보존 결과는 아래 최종 대조 기록을 따른다. B/C 수신 hash는 이전 기록과 같으며 최신 팀 기준·B 검증3모듈은 미확인/미수신이다. 기존 최소 Task01-C 의미·대표474노드·납기 자료 부족을 바꾸지 않았다. 계산·신규 Agent/화면·새 provider·공통 명세 변경은 범위 밖으로 유지했다.


### 최종 보존 대조

착수 전 135개 기존 파일의 SHA-256과 대조해 허용된 기존 파일3개(`materials/__init__.py`, `docs/MATERIALS.md`, `sungsoo/task_02_plan.md`)만 변경했으며 나머지132개는 동일했다. 새 파일은 contract_intake.py·configuration_lookup.py·test_contract_intake.py·materials_contract_intake.py·task_02.md의 허용된5개뿐이고 삭제된 기존 파일은 없다. pyproject.toml staged patch와 기존 tracked unstaged patch는 byte 단위로 같고, 모든 기존 Git 상태 항목도 보존했다. 브랜치 feature/a-first-mvp·HEAD 7c5e2d99d5a12c5b1ccf2bcc3da8ea5acfe2f271은 그대로다. 수신 B/C·원본 입력·catalog/demo_records/data/sources·T-box·대표 JSON-LD·기존 학습06/07도 이 대조에 포함된다. 시험 접수파일·schema·컨테이너·임시 PDF/PNG는 정리했으며 고정 이미지는 보존했다. 보존 근거는 `/tmp/task02-validation-20261028/preservation.json`이다.

## 10. Task 02 후속 검토 준비 — 2026-10-28

이번 후속 준비는 **구현·실제 PG 검증·연결안 작성 완료**이며 Task02 전체는 계속 부분 완료다. 사람 확인·실제 LLM 해석·정확 납기 자료·B/C 전체 통합과 Task03 이후 계산은 각각 남아 있다. 8/9절의 과거 검증 수치를 이번 실행 결과로 재사용하지 않았다.

### 표현 함수와 입출력

```python
from ontologics.materials.contract_intake import read_contract_demand
from ontologics.materials.demand_review import build_demand_review

# 신뢰된 호스트의 session/context/원문/해석 입력. 모델 도구로 등록하지 않음.
result = read_contract_demand(session, context, extracted, interpreted)
review = build_demand_review(result, previous=previous_result)  # previous는 선택
```

`build_demand_review(result: dict, *, previous: dict | None = None) -> dict`는 기존 전체 조회 결과를 소비한다. 원문/후보/근거 대조와 공개 조회는 기존 함수를 재사용하고, 작은 demand_review.py에서 항목별 검토 범위와 실제 값/근거 변경 목록만 만든다. 원문·보고서·공통 envelope를 수정하지 않으며 별도 모델/검증기/호스트/저장소를 만들지 않았다. 명시적 모듈 import로 충분해 package export는 바꾸지 않았다.

| 출력 | 의미 |
| --- | --- |
| purpose | host_recomputed_demand_presentation_only. 신뢰된 호스트가 재계산한 결과의 표현이며 자체 인증은 하지 않음 |
| status | query_failed / invalid / needs_confirmation. 입력의 confirmed 상태를 업무 확인으로 채택하지 않음 |
| data_version | 보고서 binding의 정확 데이터 버전. 보고서 없는 조회 실패는 None, 실제 공통 오류/버전은 query에서 열람 |
| query | lookup_response/configuration_responses/configurations/errors의 독립 복사. 원 envelope/run_id/오류 유지 |
| items | 기존 항목의 fields/전체 claims·근거·후보·선택 제안·permissions를 복사하고 review_scopes 추가 |
| items[].review_scopes | configuration, 네 수량, delivery_value, delivery_arrival, constraints의 경로·검토 목적·자료 상태. confirmation은 모두 not_recorded |
| errors/unresolved/questions | 기존 보고서 진단·질문 복사. 공개 조회 오류는 query에도 원형 보존 |
| confirmation | 항상 verified=false / state=unavailable. 출력 생성은 사람 확인 기록이 아님 |
| revision.before / after | previous.report / 현재 report 전체 복사. 원문·해석 원형·미채택/오류/unknown/malformed 주장·근거와 후보 실제값까지 보존 |
| revision.changes | 경로별 before/after 실제값 및 before_present/after_present. 삭제와 명시적 null·bool/int 변경을 구별 |
| revision.history_verified | 항상 false. 항목/주장 목록은 위치 index 비교이며 동일 항목을 자동 추론하거나 영속 수정 차수를 발급하지 않음 |

availability는 candidate_for_review(대조 재료 있음), needs_material(누락·상충·근거/선택 부족), blocked_by_report_errors(관련 오류), needs_business_decision(제약 업무 판단)이다. 자료 있음은 승인/계산 가능 판정이 아니다. 다른 항목에 오류가 있어도 해당 항목의 대조 재료는 보일 수 있으며 전체 status=invalid는 유지한다. 미확인도 항상 남긴다.

변조된 현재/이전 dict도 인증하지 않는다. 실제 호스트가 재계산한 결과만 정상 사용해야 한다. valid/provenance/confirmed/ref/후보 hash로 확인 권한을 발급하지 않으며 raw 보고서의 주장과 최상위 확인 상태를 구분한다. 조회 report=None, configurations=None, query 오류 또는 실패 envelope는 query_failed와 items=[]로 표시하고 오류 자료는 보존한다. 빈 후보 정상 성공으로 바꾸지 않는다.

### 현재 계약과 수정 예제

`python -m examples.materials_demand_review`는 TEST_DATABASE_URL을 명시해야 실행한다. tests.db의 새 schema에 기존 catalog를 개발 시드한 뒤, 고정 원문과 기존 reference_interpretation을 실제 공개 RequestSession에 연결한다. 모델/API 해석이나 운영 승인 시연이 아니다.

이번 실제 결과는 정확 v1·공개 구성 조회2건, L/WROOM·주문100·이행0·배정0·신규검토100·2026-11-10 date이며 **needs_confirmation / verified=false**다. 이행/배정/신규 검토량은 원문10/11줄의 시연 가정이다. 날짜를 시각으로 확장하거나 제약 미기재를 변경/대체 허용으로 바꾸지 않았다.

예제는 파일을 고치지 않고 해석 주장만 100→120으로 바꿔 후보100→None·EVIDENCE_VALUE_MISMATCH를 보인다. 120을 원문 `100` 인용과 함께 보존하고, 다시100으로 고치면 후보None→100이지만 확인 상태는 false다. 별도 사례에서 원문은 그대로 두고 해석의 개정/변형/구성 ID를 의도적으로 생략한다. 실제 catalog의 L/L1 WROOM 두 후보와 L1 선택 제안을 보여 주되, 원문 명시 L/WROOM이나 실제 사람 선택 완료로 설명하지 않는다.

### 공통 연결 조사·미적용 권고

공통 RequestSession.call에는 조회/초안 접점이 있고 RequestSession.review·CommonHost.apply_approved는 그래프 operations의 정확 초안 버전을 승인·반영한다. `record_agent_check`는 AI 답변 대조의 감사이며 수요 확인 저장소가 아니다. 현재 공개 API/DDL에는 수요 원문·수정 차수·항목별 scope·확인 사건을 저장/조회하는 경로가 없어 기존 판단을 유지한다.

권고는 기존 CommonHost·IdentityProvider·Service._run·같은 PG/schema의 잠금/재시도/감사를 재사용해 불변 수요 수정본과 항목별 확인 사건만 추가하는 안이다. 호출명/DDL/업무 정책은 제안이고 이번에는 적용하지 않았다. 그래프 review의 note를 업무 확인으로 대체하지 않는다. 전체/부분 확인의 필수 범위, 작성/확인 분리, 운영 이행/배정 기준시점, 변경 시 승계·권한 철회의 효력은 팀 판단 대상이다.

상세 입력/출력·보관 후보·신원 주입·재접속·변경 무효화·동시성·감사·시험안은 [`업무 확인 연결안`](../../sungsoo/task_02_업무_확인_연결안.md)에 있다. 저장소만 공유받은 팀원은 위 API 부재/권고와 아래 검증 결과로 현재 범위를 판단할 수 있다.

### 이번 후속 실행 검증

Python3.12.10·기존 캐시 psycopg/binary3.3.6·typing_extensions4.16.0·pypdf6.12.2를 재확인했고 설치하지 않았다. 고정 이미지 `pgvector/pgvector:0.8.7-pg17@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d`를 사용했다. 새 컨테이너·새 DB·127.0.0.1 동적 포트·PGDATA tmpfs만 사용했다. 최종 TCP pg_isready와 실제 SELECT로 PostgreSQL17.11 / pgvector0.8.7을 확인했다. 두 DB 환경변수는 실행 프로세스에서만 같은 새 전용 DB를 가리켰다.

| 명령 | 종료 | 통과 / 실패 / 오류 / skip |
| --- | ---: | --- |
| `python -m unittest tests.materials.test_demand_review -v` | 0 | 21 / 0 / 0 / 0 |
| `python -m unittest discover -s tests/materials -t . -v` | 0 | 120 / 0 / 0 / 0 |
| `python -m unittest tests.test_model tests.test_ingest tests.test_temporal tests.test_service tests.test_host tests.test_intake tests.test_agent_extensions -v` | 0 | 85 / 0 / 0 / 0 |
| `python -m unittest discover -v` | 0 | 264 / 0 / 0 / 0 |
| `python -m examples.materials_demand_review` | 0 | 정확 v1/구성2·오류 수정 전후·미확인 제안 |
| `python -m examples.materials_contract_intake` | 0 | valid=true / needs_confirmation / verified=false |
| `python -m examples.materials_inputs` | 0 | valid=true / needs_confirmation / graph_integration_verified=false |

그룹 수는 중복되므로 합산하지 않는다. 신규21개는 단위20+실제 PG1이며, planning에는 기존 intake PG1·Task01-C PG4도 실행됐다. 신규 PG는 원문과 정확 v1 공개 조회를 연결하고 새 요청 재접속·v999 실패도 확인했다. 조회 오류/변조 응답 사례의 단위 대역을 실제 DB 장애 시험으로 세지 않았다. 사람이 확인한 성공 시험은 없고 개발 신원이다.

TDD 최초는 함수 부재로16실패/skip1, 이후 오류 경로(`items[i].field`와 반환 `items[i].fields.field`) 대응을 바로잡았다. 모델명만+선택 제안 사례의 추가 RED→GREEN도 확인했다. 최종 별도 읽기 전용 리뷰는 새 코드3개를 대조하고 단위20개를 독립 실행했으며 Critical/Important/Minor 발견0이었다. PDF의 새 기능/시각 검증은 추가하지 않았고 기존 pypdf 회귀만 이번 전체 시험에서 재실행했다.

실행 로그·스크립트·새 results.json과 baseline은 `/tmp/task02-followup-20261028/`다. 이전 `/tmp/task02-validation-20261028/run_validation.py`는 내용 확인 후 참고만 했고 실행 결과를 재사용하지 않았다. URI/비밀번호/키는 출력·문서·.env에 기록하지 않았다. Docker의 sandbox socket 제한은 승인된 권한 실행으로 해결됐으며 자동 승인 검토 거절은 없었다.

시험별 schema 잔여0, 최종 사용자 schema0, 컨테이너 `ontologics-task02-followup-1ad6a4e19de4` 제거 종료0·inspect 부재를 확인했다. 회귀시험이 만든 새 접수 JSON1개만 제거했고 기존 접수파일은 보존했다. 임시 원문/PDF는 이번에 새로 만들지 않았으며 단위시험 임시 원문은 TemporaryDirectory로 정리했다. demo export/catalog 재생성·Task03 계산·git add/commit/push·PR·다른 채팅/새 채팅/자동화는 하지 않았다.

후속 최종 보존 대조는 baseline142개 중 허용 문서2개만 변경·나머지140개 동일·삭제0·허용 신규5개다. package export와 기존 계약/조회/adapter·공통 코드는 그대로다. staged patch·기존 tracked unstaged patch·기존 상태 항목·브랜치/HEAD도 보존했다. 사용자가 읽는 현재 설명·기존 task02 학습·학습06/07·프롬프트, B/C 수신 자료·원본/JSON 입력·catalog·demo_records·data/sources·T-box·대표 JSON-LD도 포함된다. 근거는 `/tmp/task02-followup-20261028/preservation.json`이고 전체 읽기용 결과는 [`후속 작업 결과`](../../sungsoo/task_02_후속_작업_결과.md)다.

## 11. 공통 코드 무변경의 별도 확인 요청 모듈

사용자 후속 지시에 따라 `materials/demand_confirmation.py`·시험·실행 예제를 추가했다. 기존 공통 코드·공개 API·DDL은 수정하지 않았다. 현재는 **확인 대상 준비와 권한/자료/부분 범위 재대조 구현**, **영속 저장·재접속/최종 확인 검증 미연결**이다. Task02 전체 완료가 아니다.

```python
from ontologics.materials.demand_confirmation import DemandConfirmationWorkflow

# 동일 IdentityProvider로 만든 기존 CommonHost와 공급자. 호스트 전용 객체다.
workflow = DemandConfirmationWorkflow(host, identities)
prepared = workflow.prepare(author_context, host_selected_path, interpreted,
                            data_version=exact_version)
target = prepared['target']  # 조회 실패/invalid면 None

# 명시적인 사람 동작에서만 호출. 현재 persistence 미연결로 예외 발생.
workflow.submit(reviewer_context, target, expected_target_sha256=target.sha256,
    scopes=[{'item_id': 'contract-item:planning:001', 'scope': 'ordered_quantity'}],
    note='원문 수량 확인 요청', request_key='explicit-request-key')
```

공개 `CommonHost.request`/`session.context`·`read_contract_demand`를 사용한다. 공급자의 `lookup`과 기존 `UserIdentity.permissions`로 현재 read/review 권한을 제출 전후에 확인한다. 사용자/권한 목록을 복제하거나 모델 인자로 신원·권한을 받지 않는다. 공통 canonical/digest·Service.now도 재사용했다. 조회의 read 검사는 기존 Service 경로이고 확인 요청의 gate는 기존 권한 값에 대한 업무 진입 검사다. 저장 트랜잭션 안의 최종 권한 검증까지 연결됐다고 설명하지 않는다.

prepare는 작성자·identity mode·원문/정확 버전·전체 보고서/해석·근거/미채택 주장을 `PreparedDemandTarget`에 결합한다. frozen dataclass와 JSON 문자열은 출력 dict의 변경으로부터 원 target을 분리한다. target/hash는 서명·인증 토큰이 아니며 신뢰된 호스트가 보유하는 내부 객체다. 임의 dict의 confirmed/ref로 대체할 수 없다.

submit은 자기 확인을 막고 파일 재추출·정확 공개 조회·전체 target hash를 재대조한다. 명시한 항목별 scope만 처리하며 날짜 자체와 정확 도착 시각/배송지·제약을 구분한다. 준비 자료가 바뀌면 재준비가 필요하다. 작성/확인 분리·변경 시 전체 target 재준비는 이번 모듈의 보수적 동작이며 팀 전체 정책 확정은 아니다.

`ConfirmationPersistence.submit(intent)`는 **새 호스트 주입 연결점**이다. 실제 공통 API나 구현된 저장소가 아니다. 기본 None이면 PERSISTENCE_UNAVAILABLE로 중단한다. 구현체가 ok를 줘도 status=submitted는 전달 응답이고 confirmation.verified는 항상 false다. 실패 envelope는 persistence_failed로 원형 보존하며 예외는 비밀값 없는 오류로 전달한다. 실제 예제는 저장 대역을 연결하지 않는다.

intent의 요청 hash는 대상 hash·범위·메모를 결합한다. 현재 시도의 requested_at은 확인 사건의 recorded_at이 아니며 재생 hash에서 제외한다. actor/key/hash 재생·target 영속 ID/revision·CAS·감사·현재 권한/유효성 조회는 미구현이다. `_run`/`_require`·private DB 연결·상속/monkeypatch·직접 SQL·파일/메모리 승인 저장소로 우회하지 않았다. target 등록/차수와 get/current-validity 계약도 추가로 필요하므로 submit adapter 하나로 모든 연결이 완료되는 것은 아니다.

실제 PG 예제는 현재 계약/수작업 해석·exact v1을 사용한다. 작성자→SELF_APPROVAL_FORBIDDEN, read 전용→FORBIDDEN, review 사용자→PERSISTENCE_UNAVAILABLE를 확인했다. 계약은 L/WROOM100·date 납기·제약 미확인·verified=false다. 예제 종료0은 이 경계를 검증한 것이며 실제 업무 확인/저장 성공이 아니다.

### 이번 별도 모듈 검증

재확인한 Python3.12.10·캐시 psycopg/binary3.3.6·typing_extensions4.16.0·pypdf6.12.2를 재사용했다. 지정 PG17 고정 digest의 새 컨테이너·DB·127.0.0.1 동적 포트·PGDATA tmpfs를 사용했다. 최종 TCP 준비와 실제 SELECT는 PostgreSQL17.11 / pgvector0.8.7이었다. 기존 DB/볼륨·호스트 데이터 경로는 사용/마운트하지 않았다. 비밀값은 두 DB 환경변수의 실행 프로세스 주입에만 사용했다.

| 명령 | 종료 | 통과 / 실패 / 오류 / skip |
| --- | ---: | --- |
| `python -m unittest tests.materials.test_demand_confirmation -v` | 0 | 23 / 0 / 0 / 0 |
| `python -m unittest discover -s tests/materials -t . -v` | 0 | 143 / 0 / 0 / 0 |
| `python -m unittest tests.test_model tests.test_ingest tests.test_temporal tests.test_service tests.test_host tests.test_intake tests.test_agent_extensions -v` | 0 | 85 / 0 / 0 / 0 |
| `python -m unittest discover -v` | 0 | 287 / 0 / 0 / 0 |
| `python -m examples.materials_demand_confirmation` | 0 | 공통 신원·조회/자기 확인·권한 부족·저장 미연결 |
| `python -m examples.materials_demand_review` | 0 | 기존 검토 예제 |
| `python -m examples.materials_contract_intake` | 0 | 기존 접수 예제 |
| `python -m examples.materials_inputs` | 0 | 기존 입력 예제 |

신규23은 단위20+실제 PG3이다. PG3은 기존 CommonHost의 exact 조회·신원/권한·새 연결 재계산과 Agent 도구 차단을 시험했다. payload 재계산의 동일성을 확인 기록의 영속 재조회 성공으로 세지 않았다. 단위 저장 대역도 실제 확인으로 세지 않는다. 그룹 수는 중복되므로 합산하지 않는다.

최초 RED20실패/PG3 skip→최종23통과/skip0이었다. 별도 읽기 전용 리뷰는 단위20개를 독립 실행하고 Critical/Important 발견0, 복수 scope의 순서/교체별 요청 hash에 대한 추가 시험 제안1개는 후속으로 기록했다.

새 로그/runner·results/baseline/preservation은 `/tmp/task02-module-20261028/`다. 컨테이너 `ontologics-task02-module-6ea0dd12ac4d` 삭제 종료0·inspect 부재, 최종 사용자 schema0·새 접수 JSON1개 정리를 확인했다. 기존 접수파일·실제 계약·원문/스테이징을 보존했고 패키지 설치·실모델·demo export/catalog 재생성·commit/push/PR·새 채팅/자동화는 하지 않았다.

현재 사용법과 실제 결과·남은 연결은 [`별도 모듈 구현 결과`](../../sungsoo/task_02_별도모듈_구현결과.md), 이전 대안과 현재 선택은 [`연결안 8절`](../../sungsoo/task_02_업무_확인_연결안.md#8-현재-선택-별도-모듈로-구현하고-공통-파일은-보존)에 있다. 기존11절 이전 기록은 당시 근거로 유지한다.

이번 baseline147개 중 문서3개만 변경·나머지144개 동일·삭제0·신규4개를 확인했다. 기존 공통/업무 코드·시험·이전 예제/학습/후속 결과·원본·기존staged/unstaged patch와 상태/branch/HEAD를 보존했다. package export도 유지했다. 자세한 최종 hash/상태는 `/tmp/task02-module-20261028/preservation.json`이다.

## 12. 영속 확인·재조회·수정 이력 구현 — 공통 파일 무변경

사용자의 최신 지시로 이전11절의 미연결 저장 부분을 구현했다. 새 `materials/demand_confirmation_store.py`는 기존 `retrieval.EvidenceIndex`/`receipts`와 같은 **서버 내부 확장 패턴**으로 실제 Service._run을 재사용한다. 공통 권한·schema lock·트랜잭션·요청 재생·감사·롤백을 복제하지 않는다. 기존 Service/Host/Identity/Database/공통 DDL/API는 수정하지 않았다. 이전 단계 기록은 당시 상태다.

```python
from ontologics.materials.demand_confirmation_store import PersistentDemandConfirmationWorkflow

# 동일 host/identity provider/database settings. 호스트 전용이며 Agent 도구 아님.
workflow = PersistentDemandConfirmationWorkflow(host, identities, settings)
saved = workflow.save(author_context, prepared_target, request_key='save-1')
ref = saved['data']  # 기존 common envelope의 ok/errors 확인 후 사용
recorded = workflow.confirm(reviewer_context, ref['demand_id'], ref['revision'],
    expected_target_sha256=ref['target_sha256'],
    scopes=[{'item_id': 'contract-item:planning:001', 'scope': 'ordered_quantity'}],
    note='원문 수량 확인', request_key='confirm-1')
result = workflow.get(reader_context, ref['demand_id'], revision=ref['revision'])
```

| 호출 | 입력/권한·보증 |
| --- | --- |
| save(context, target, request_key, demand_id?, expected_revision?) | 기존 preflight가 준비한 호스트 target·read/draft 권한. 원문/정확 버전/전체 payload 재대조, 원문 bytes와 추출·보고서·작성자/identity mode·hash를 불변 revision으로 저장 |
| confirm(context, demand_id, revision, expected_target_sha256, scopes, note, request_key) | read/review·다른 확인자·현재 exact revision/hash·명시 scope·원문 재대조. 저장 사건/event_id/recorded_at 반환. 성공 ack는 현재 효력 증명이 아님 |
| get(context, demand_id, revision?) | read 권한. 생략 시 현재 저장 revision, 지정 시 정확 차수. 저장 원문 bytes/hash·정확 공개 조회·현재 확인자 권한·현재 수정본을 대조하고 사건/유효 범위/미확인 범위 반환 |

동일 PG/schema에 `demand_review_revisions`와 `demand_review_events`2개 테이블을 추가한다. 기존 테이블·그래프 스냅샷은 변경하지 않는다. 최초 save의 공통 draft 권한 트랜잭션에서 새 테이블을 준비한다. 공통 requests/runs는 기존 실행 계층이 동일하게 기록한다. 별도 DB/권한 목록/Service 클래스·Agent 쓰기 도구를 만들지 않는다.

저장·조회 직전 현재 권한을 재조회하고 공통 _require로 다시 검사한다. revision 수정은 expected_revision CAS이며 이전 확인은 supersede 사건과 함께 남기고 새 차수에 승계하지 않는다. 같은 키/내용은 공통 재생으로 복구하고 다른 내용은 IDEMPOTENCY_CONFLICT다. 완료된 확인의 재생은 수정 이후에도 과거 ack만 반환하며 현재 효력은 항상 get으로 재확인한다. 원자 실패·같은 키 동시 요청·동시 revision 수정은 공통 _run/schema lock에 맡긴다.

get 결과의 status는 needs_confirmation / partially_confirmed / confirmed / superseded다. confirmation.verified는 현재 수정본의 모든 항목/8개 범위가 유효할 때만 true이고, 확인한 일부는 verified_scopes로 구별한다. 항목별 review_scopes.confirmation도 verified/invalidated/not_recorded로 표시한다. 원 intake 보고서는 intake_report로 보존하고 과거 HUMAN_CONFIRMATION_UNAVAILABLE 진단을 현재 상태와 섞지 않는다.

현재 계약은 개발 신원의 예제에서6개 범위 확인을 실제 저장·재접속 조회했다. 정확 도착 시각/offset/배송지와 변경/대체 제약2개는 자료 부족으로 남아 **전체 확인은 false**다. 선택 제안을 수정해 revision2를 저장하자 revision1은 superseded, 과거 confirm+supersede 사건2개 보존, revision2는 needs_confirmation으로 돌아갔다. 원문 파일 편집만으로 저장 head를 바꾸지는 않으며 새 확인은 파일이 바뀌면 거절하고 명시적 새 save로 정정한다. 조회는 함께 보관한 원문 bytes를 사용하므로 원래 파일이 없어도 저장본을 검증할 수 있다.

새 저장 모듈은 _run/_require/_nodes/_envelope 등 내부 서버 함수에 의존한다. 공식 공개 Agent 확장 API가 아니며 공통 내부 계약 변경 시 회귀가 필요하다. 기존 공통 private 객체를 꺼내 권한/트랜잭션을 우회하는 구현과 구별한다. 기존 업무 모듈 demand_confirmation.py에서 build_submission을 분리한 것 외에는 이전 업무 코드를 보존했다.

### 최종 실제 검증

재확인한 Python3.12.10·캐시 psycopg/binary3.3.6·typing_extensions4.16.0·pypdf6.12.2를 사용했다. 지정 digest PG17 이미지의 새 일회용 컨테이너·DB·127.0.0.1 동적 포트·PGDATA tmpfs만 사용했고 TCP 준비/실제 SELECT에서 PostgreSQL17.11 / pgvector0.8.7을 확인했다. 두 DB 환경변수는 같은 새 DB를 프로세스에서만 지정했다. 설치/기존 DB 재사용/호스트 자료 경로 마운트는 없다.

| 명령 | 종료 | 통과 / 실패 / 오류 / skip |
| --- | ---: | --- |
| `python -m unittest tests.materials.test_demand_confirmation_store -v` | 0 | 21 / 0 / 0 / 0 |
| `python -m unittest discover -s tests/materials -t . -v` | 0 | 164 / 0 / 0 / 0 |
| `python -m unittest tests.test_model tests.test_ingest tests.test_temporal tests.test_service tests.test_host tests.test_intake tests.test_agent_extensions -v` | 0 | 85 / 0 / 0 / 0 |
| `python -m unittest discover -v` | 0 | 308 / 0 / 0 / 0 |
| `python -m examples.materials_demand_confirmation_store` | 0 | 실제 저장/부분 확인/재접속/수정 무효화 |
| `python -m examples.materials_demand_confirmation` | 0 | 기존 preflight 예제 |
| `python -m examples.materials_demand_review` | 0 | 기존 검토표 예제 |
| `python -m examples.materials_contract_intake` | 0 | 기존 접수 예제 |
| `python -m examples.materials_inputs` | 0 | 기존 입력 예제 |

신규21 중 실제 PG20개이며 mock을 영속 검증으로 세지 않았다. 그룹은 중복되므로 합산하지 않는다. 최초 RED17개와 추가 권한/표시/진단 RED4건을 거쳐 최종 결과를 얻었다. 독립 리뷰의 Important2건(조회자 현재 read, 저장 시 현재 read/review)을 실제 회귀 시험에서 보완했다. Minor 추가 시험(서로 다른 payload의 동시 수정 경쟁)은 후속 기록으로 남겼다. 같은 변경안의 동시 CAS와 복수 scope 순서/내용별 재생은 실제 실행했다.

근거는 `/tmp/task02-persistence-20261028/`의 baseline/patch/RED 기록과 `final/`의 results·로그·runner다. 최종 컨테이너 `ontologics-task02-persistence-04d861a5e5a7` 삭제 종료0/inspect 부재, schema 잔여0·최종 사용자 schema0·회귀 생성 접수 JSON1개 정리를 확인했다. 초기/중간 컨테이너도 정리했다. 비밀값 출력/저장·실모델·demo export/catalog 재생성·git add/commit/push/PR·새 채팅/자동화는 없다. 실제 운영 DB에는 설치하지 않았다.

현재 저장·재조회·이력 코드는 구현했으나 실제 사람/운영 인증·현재 계약의 정확 납기/제약 보완·실제 모델 해석·B/C 전체 통합·Task03은 별도다. 상세 사용법·현재 예제·내부 의존·보존 기록은 [`영속 확인 구현 결과`](../../sungsoo/task_02_영속_확인_구현결과.md)에 있다.

최종 baseline151개 중 기존 업무 코드1개·문서4개만 변경·나머지146개 동일·삭제0·신규4개다. 공통 파일·원문·학습·기존staged/unstaged patch·기존 상태/branch/HEAD를 보존했다. 새 코드의 문법·공백과 변경 문서의 로컬 링크를 대조했다. 근거는 `/tmp/task02-persistence-20261028/preservation.json`이다.

## 13. Task02 모델 해석 연결 (2026-10-28)

`ontologics/materials/contract_interpretation.py`는 기존 `AgentRegistration` / `RequestSession.run_agent` / `ModelRuntime`을 사용한다. 실제 공급자는 기존 `OpenAISettings` / `OpenAIProvider`다. 공통 코드·수요 검토/확인/영속 모듈은 변경하지 않았다.

- 입력: 호스트가 추출한 원문 segment의 본문·ID·위치, 고정 source_sha256/data_version, 필드 계약. 경로·source_bytes·신원·권한·DB URI·환경 설정은 모델 메시지에 넣지 않는다.
- 출력: `items[].fields.FIELD[] = {value, evidence:[{segment_id, quote, start?, end?}]}`. 메타데이터를 모델이 생략하면 호스트값을 결합하고, 모델이 출력하면 반드시 일치해야 한다. 이는 의미 추출과 별개다.
- 코드펜스/깨진·중복 키·비유한·과대 JSON, 잘못된 유니코드, 추가 필드·가짜 확인을 거절한다. 누락 값·수량 타입·명시 위치/인용 오류·상충은 기존 intake의 진단과 원 주장을 유지한다. 누락 offset은 같은 segment에서 인용이 유일할 때만 보완한다. 위치를 결정할 수 없으면 `EVIDENCE_LOCATION_AMBIGUOUS`와 안전한 필드 경로/이유를 반환한다.
- 역할은 공급자/Gateway 모두 lookup만 노출한다. 이번 최소 실행은 max_steps=1로 제한하여 도구 결과를 두 번째 모델 입력으로 전송하지 않는다. 모델이 도구를 요청하면 성공 해석으로 취급하지 않는다. 원문 지시는 사용자 데이터이고 확인·반영 권한이 아니다.
- 성공 처리도 `runtime.ok`와 업무 유효성을 분리한다. `runtime_failed`, `output_rejected`, `query_failed`, `invalid`, `needs_confirmation`을 구별하며 기존 validation 감사와 run_id를 연결한다.

예제 `python -m examples.materials_contract_interpretation`은 기본 수작업 fixture를 반환하는 **모델 대역**이다. 명시적 `--live --env-file <로컬 설정 파일>`만 실제 모델을 호출하며 실패 후 대역으로 대체하지 않는다. 프로세스의 TEST_DATABASE_URL과 DATABASE_URL은 동일한 새 전용 시험 DB여야 한다. 예제는 tests.db의 새 schema에서 공개 catalog를 개발 시드로 넣고 모델 결과 → read_contract_demand → 검토표 → 기존 prepare → 별도 호스트 save → 재접속 get을 수행한다. 모델 예제에는 confirm/apply 호출이 없다. 저장된 후보·원문은 시험 schema 정리 시 함께 삭제된다.

실행 요약은 원문/모델 응답 전체 없이 모델·source hash·정확 버전·run_id·안전한 provider 호출 정보·근거 위치·상태를 제공한다. 모델 원 응답은 실행 중 메모리에서만 유지한다. 실제 실패 응답을 저장하지 않았으면 이후 구체 내용을 추측하지 않는다.

실제 호출 결과·최종 검증 수와 남은 차단은 저장소 밖 `sungsoo/task_02_모델_해석_연결결과.md`에 기록한다. Task02 전체는 여전히 부분 완료다. delivery_arrival=needs_material, constraints=needs_business_decision 정책, 실제 사용자 업무/HTTP·B/C 통합·Task03 범위는 그대로 남는다.

최종 검증은 신규23(순수12/실제 PG11), 업무187, 모델 관련26, 지정 회귀85, 전체331 통과(실패/오류/skip0), 대역/기존 예제3개 종료0이다. 실제 고정 모델 호출1회는 HTTP200/runtime_ok=true였으나 `EVIDENCE_LOCATION_AMBIGUOUS`로 업무 검사에서 거절됐고 예제 종료1·후보 저장0이다. 모델이 어느 인용을 반환했는지는 원 응답을 보관하지 않아 단정하지 않는다. 실패 후 안전한 필드 경로/이유 진단을 추가했다. 사용자가 추가 호출 없이 정리를 선택하여 재호출하지 않았다. 연결 코드와 대역 PG 흐름은 검증됐지만 실모델 유효 해석→저장 성공은 남아 있다. 새 시험 자원은 모두 정리했다.

### 후속 재검증: 반복 인용 위치 보완

사용자가 추가 API 최대2회를 승인했다. 첫 요청은 fulfilled_quantity의 중복 인용에서 multiple_matches를 재현했다. 모델 근거의 선택 형식 `{segment_id, quote, occurrence}`를 추가했다. occurrence는 정확 substring의 등장 순서(0부터, 겹치는 일치도 계산)이며 호스트는 모델의 명시 선택을 기존 start/end로 변환한다. 모호한 생략·범위 이탈·잘못된 타입·offset 혼용은 거절한다. 기존 위치 검사·값 검사·확인 정책은 유지한다. 예제는 자동 재시도를 없애 max_attempts=1로 요청 수를 제한한다.

두 번째 실제 요청은 이행0/배정0의 서로 다른 위치를 통과해 모델→기존 검토→target→PG 저장·재접속까지 성공했다. 다만 제품명에 제조사가 포함되고 constraints에 다른 종류의 문장을 함께 넣어 구성 미선택/상충이 남았다. **candidate_saved_extraction_incomplete/종료1/확인 사건0**이다. 이 후보 저장을 올바른 전체 추출 성공으로 해석하지 않는다.

이후 필드별 의미/타입/제외 범위/제약 범위를 입력에 추가하고 오분류 회귀를 보강했다. 허용2회를 모두 소진했으므로 이 최종 의미 지시의 실제 모델 재검증은 하지 않았다. 상세 기록은 `sungsoo/task_02_모델_해석_재검증결과.md`를 따른다. 이전 실패 기록은 당시 근거로 남긴다. 정확 납기/제약 확인 판정, 운영 사용자, B/C 통합, Task03은 여전히 별도다.

마지막 의미 설명 보완 후 최종 검증: 모듈30(순수17/PG13)·업무194·모델 관련26·지정 회귀85·전체338 통과, 실패/오류/skip0, 예제3개 종료0. 이 수치는 실제 모델의 의미 품질 성공 횟수가 아니다. 허용된 추가 API2회는 모두 사용했고, 최종 의미 보완의 실모델 재검증은 남아 있다.

### 필드 의미 보완 후 추가 실제1회

사용자 추가 승인1회로 보완된 입력을 검증했다. 제약 표현·중복 인용·수량·날짜는 통과하고 PG 후보 저장/재접속/확인 사건0을 확인했다. 제품명을 여전히 제조사 포함 `OLIMEX ESP32-POE`로 추출하여 catalog의 name=`ESP32-POE`, manufacturer=`OLIMEX`와 일치하지 않았다. CONFIGURATION_NOT_FOUND가 남아 candidate_saved_extraction_incomplete/종료1이다. 이번은 코드 변경 없이 실제 검증만 수행했으며, 직전 전체338 통과를 이번 새 시험 결과로 재사용하지 않는다. 원문·공통·스테이징 보존 및 새 시험 자원 정리를 확인했다. 모델 해석의 필수값 전체 성공과 Task02 전체 완료는 아직 아니다.

## 14. 최신 모델 해석 연결 상태 — 제품 식별 해결

기존 lookup_contract_configurations를 모델 실행 전에 호출해 정확 버전·권한·구성 관계를 대조한다. 성공 후보의 manufacturer/product_model/revision/variant/configuration_id만 reference_configurations에 제공하며 전체 그래프/BOM/근거 원문/신원은 전송하지 않는다. 호스트가 외부 전송을 허용한 공개 참조만 사용해야 한다. 조회 실패는 query_failed/runtime=None으로 종료하며 API를 호출하지 않는다. 모델 출력은 기존 read_contract_demand와 prepare/save/get으로 다시 검사한다. 자동 값 교정·fixture fallback·확인/반영 없음.

실제 고정 모델1회에서 제품명 ESP32-POE를 원문 line6의14..23으로 정확히 인용하고 L/WROOM 구성 선택, 100/0/0/100 수량, 날짜 납기, 제약 미확인 문구, ID·단위의14개 대조를 모두 통과했다. 예제 종료0, candidate_saved_needs_human_review, 실제 PG 저장/재접속 성공, 확인 사건0/verified=false. 이번 허용2회 중1회만 사용했다. run_id=6065d7b0-3f83-4cbb-aef4-f484cabcaa8a. 새 시험 DB/컨테이너는 정리했다.

최종 모듈34(순수18/PG16)·업무198·모델 관련26·지정회귀85·전체342 통과, 실패/오류/skip0, 대역/기존 예제3개 종료0. 실제 모델 실행은 이 시험들과 별도의 최소1회 검증이다. 이전 절의 제품명 오류 기록은 당시 결과로 보존하며 이번 실행에서 해결됐다. 한 개발 계약의 해석 연결 성공을 범용 품질 또는 운영 확인 완료로 일반화하지 않는다.

**Task02 모델 해석 연결 범위는 완료.** 정확 납기/제약의 입력·확인 판정, 실제 인증·사람 업무/HTTP, B/C 통합, Task03은 별도이며 Task02 전체는 부분 완료다. 상세 결과: 저장소 밖 `sungsoo/task_02_제품_식별_보완결과.md`.

## 14. Task03 자재 계산과 미확인 수요 연결 (2026-10-28)

최신 `sungsoo/task_03_시작_프롬프트.txt` 기준의 계산 범위를 구현했다. 앞 절의 Task03 미착수 표기는 당시 상태다. Task02의 정확 도착 정보·제약 확인은 여전히 미완료이며 운영 생산 확정을 뜻하지 않는다.

신규 `ontologics/materials/materials.py`는 다음 함수를 제공한다. Service/Agent/HTTP 도구로 등록하지 않았다.

| API | 입력과 반환 의미 |
| --- | --- |
| `available_stock(snapshots, scope)` | snapshots는 `source_ref/sha256/stock_scope/stocks` 문서 목록. 회사·장소·시점은 행에 명시된 값이 없을 때 문서에서 상속한다. scope는 company/location/as_of/unit와 선택적 component_lots/coverage_basis. 최신 과거 관측, 배제 이유·근거·경과초, 부품별 가용/부분합·완전성·오류/충돌/미확인을 반환한다. |
| `calculate_material_requirements(items, stock, planned_input_quantity, *, configuration_id, data_version, demand_source, bom_complete=False)` | 정확 구성의 BOM 행과 available_stock 결과를 받아 q/D/A/S, known_q/known_D, 조건부 평가 상한과 전체 BOM 자재 한계를 분리한다. N은 명시적 정수0 이상이며 기존 validate_planning_context의 N>0 조건은 바꾸지 않았다. |
| `calculate_saved_demand(workflow, host, context, *, demand_id, item_id, data_version, snapshots, scope, expected_revision=None, expected_target_sha256=None)` | 기존 PersistentDemandConfirmationWorkflow.get으로 현재 후보·권한·확인 상태 재조회, 기존 lookup_contract_configurations로 정확 공개 버전/관계 대조, 선택 로트의 회사/부품 대응, 순수 계산. 실패 envelope를 보존하며 계산은 None이다. 일반 confirmed dict를 입력받지 않는다. |

`scope.component_lots={정확부품ID:[정확로트ID,...]}`는 해당 회사·장소·기준시점 안의 로트 목록 완전성을 **호스트가 명시적으로 선언**하는 입력이다. 비어 있으면 완전성을 추정하지 않는다. 선언 시 `coverage_basis` 설명이 필요하다. 누락 관측·추가 로트·unknown이면 A는 None이고 확인된 관측 부분합만 남는다. 부분합을 전체 생산 상한에 넣지 않는다. 순수 함수의 source hash는 출처 연결 정보이며 실제 원문 bytes 대조는 기존 입력/등록 검증에 맡긴다.

관측은 offset-aware로 비교하며 동등한 offset 표기를 같은 순간으로 처리한다. 동일 ID/동일 내용은 중복 제외, 다른 내용·같은 순간의 상충·같은 로트의 다른 부품 연결은 충돌이다. 잘못된 단위/시각을 가진 행도 식별 가능한 ID/로트 충돌부터 검사한다. 충돌 부품의 A와 S는 미정이다. 같은 순간의 상충 관측은 `conflicting_observation`, 내용이 일치하는 다른 ID 관측은 `corroborating_observation`으로 구분한다. 선택은 마지막 확인 관측일 뿐 현재 실재고 보증이 아니다.

보유−예약−제한은 `deductions_disjoint=true`와 명시된 수량이 있어야 계산한다. 차감 초과/겹침은 오류이고 clamp하지 않는다. `reported_available`은 available_quantity를 그대로 사용하며 보조 예약 수량을 다시 빼지 않는다. bool/float/음수/비유한 수량과 단위 혼용은 오류다. null/누락/unknown은 미확인이다.

BOM은 populated만 필수 합산하고 not_populated는 제외 사유, unknown은 미확인으로 유지한다. 정확히 같은 component_id의 서로 다른 행 수량만 더하며 positions를 곱하지 않는다. 같은 행은 중복 제외, 같은 ID의 다른 내용은 충돌이다. unknown 행이 같은 부품에 있으면 전체 q/D/S는 None이며 known_q/known_D만 별도로 제공한다. 계산 가능한 필수 부품이 없으면 P도 None이다.

`calculation.rule_version=materials-0.1`은 기존 예약 함수를 재사용한다. 기존 공개 응답의 temporal-0.1/run_id/data_version은 유지한다. 결과의 valid는 산술 오류가 없다는 뜻이고 confirmed가 아니다. `conditional_upper_bound`는 `evaluated_component_ids`와 명시 로트 범위에만 적용된다. `whole_bom_material_limit`은 전체 BOM 선언·모든 필수 q/A 확인·오류/미확인 부재가 있어야 계산하며, 이 값도 일정·설비·업무 확정은 아니다.

### 대표 계산과 전체 BOM

회사 `Planning Reference Company`, 장소 `warehouse:reference`, 관측/평가 시각 `2026-10-26T09:00:00+09:00`, 단위 piece, 경과0초. 원본 재고3행은 synthetic이고 차감형이며 deductions_disjoint=true다. 대표3부품의 로트 범위 완전성만 예제/시험 가정으로 선언한다. 원본 coverage는 수정하지 않았다.

| BOM 행 | q | D(N=100) | A | S |
| --- | ---: | ---: | ---: | ---: |
| row-55 | 1 | 100 | 70 | 30 |
| row-9 | 8 | 800 | 560 | 240 |
| row-14 | 11 | 1100 | 1100 | 0 |

확인3항목의 조건부 상한70대를 실제 BOM quantity와 기존 재고에서 계산했다. 전체 L/WROOM BOM은74행(실장 확인63/unknown11), 재고 미확인71개 부품이다. 전체 BOM 자재 한계는 None이며 현재 전체70대 생산 가능이라고 표시하지 않는다.

새 `examples/materials_materials.py`는 일회용 tests.db schema에서 catalog 개발 시드 → 기존 가정 등록 묶음의 prepare/review/apply → validate_registered_context → 기존 수작업 계약 fixture/prepare/save → 새 workflow 인스턴스의 get → 호스트 계산을 실행한다. 등록 대응은 valid=true지만 날짜-only 납기·배송지 부족으로 graph_integration_verified=false다. 이를 계산 성공으로 바꾸지 않는다. 새 수요 후보는 확인 사건0, verified=false이며 source SHA·data_version·demand_id/revision/target_sha256·수량 인용·미해결 확인 범위를 유지한다. 그래프 등록의 개발 검토와 수요 확인은 별개다.

### 이번 실제 검증

최종 실행 근거 `/tmp/task03-20261028/verify-21a76e3d/results.json`. Python3.12.10, psycopg/binary3.3.6, typing_extensions4.16.0, pypdf6.12.2 기존 캐시를 사용했다. 지정 digest 이미지의 새 DB에서 실제 SELECT로 PostgreSQL17.11/pgvector0.8.7을 확인했다. 설치·기존 DB 재사용·실제 모델/API 호출은 없다.

| 명령 | 종료 | 통과 / 실패 / 오류 / skip |
| --- | ---: | --- |
| `python -m unittest tests.materials.test_materials -v` | 0 | 26 / 0 / 0 / 0 (순수19, 실제 PG7) |
| `python -m unittest discover -s tests/materials -t . -v` | 0 | 224 / 0 / 0 / 0 |
| `python -m unittest tests.test_runtime tests.test_openai_provider tests.test_agent_extensions -v` | 0 | 26 / 0 / 0 / 0 |
| `python -m unittest tests.test_model tests.test_ingest tests.test_temporal tests.test_service tests.test_host tests.test_intake tests.test_agent_extensions -v` | 0 | 85 / 0 / 0 / 0 |
| `python -m unittest discover -v` | 0 | 368 / 0 / 0 / 0 |
| `python -m examples.materials_materials` | 0 | 실제 PG·수작업 해석·가정 재고 |
| `python -m examples.materials_inputs` | 0 | 로컬 입력 검증 |
| `python -m examples.materials_contract_intake` | 0 | 실제 PG·수작업 해석 |
| `python -m examples.materials_contract_interpretation` | 0 | 기본 모델 대역·로컬 검증 (`--live` 없음) |
| `python -m examples.materials_demand_confirmation_store` | 0 | 기존 개발 신원의 부분 확인 저장·재접속 회귀 |

그룹은 중복되므로 합산하지 않는다. 기존 persistence 예제의 개발 확인 사건은 새 자재 예제의 확인 사건0과 구별한다. 실제 운영 사람 확인은 어느 쪽도 아니다. 최종 schema 잔여0, 생성 컨테이너 삭제 종료0/부재 확인, 생성 receipt만 정리했다. 환경 비밀값을 출력/저장하지 않았다.

학습/손계산/실습 답은 [Task03 학습](../../sungsoo/task_03.md), 변경 파일·실패 과정·보존·전체 기록은 [Task03 결과](../../sungsoo/task_03_구현결과.md)다. Task04~07, 새 화면/HTTP/Agent 도구, 생산 확정·예약·출고·소비·발주·대체 허용은 이번 범위에 없다.

## 15. Task04 생산 검토·로트별 가상 사용·잔여 계산 (2026-10-28)

Task04는 검토용 계산이며 운영 승인/예약/출고/소비/작업 생성이 아니다. 기존 Task03·공통 모델/Service/Host/신원/규칙 정의는 변경하지 않았다.

| API | 입력과 반환 |
| --- | --- |
| `build_production_plan(demand, materials, initial_quantity, *, review_state='comparison')` | demand는 configuration_id/data_version/planned_input_quantity/unit/demand_source를 명시하고 materials와 정확히 연결한다. target_quantity는 선택이며 ordered_quantity 근거를 명시해야 한다. 반환은 전량/부분/0대 분류, 별도 확인/조건부 상태, 로트 사용·가상 잔여·원본/진단 복사본이다. |
| `build_saved_production_plan(workflow, host, context, *, initial_quantity, review_state='comparison', **material_arguments)` | material_arguments는 기존 calculate_saved_demand의 인자다. 매 호출 기존 get/공개 조회로 현재 권한/후보 수정본/정확 버전/로트 관계를 검증한다. demand_response/query/calculation을 보존하고 plan을 추가한다. 계산 실패는 plan=None이다. |

N과 I는 bool/float/음수/비유한을 거절하는 정수다. 순수 N=0은 허용하지만 기존 양수 입력 검증기는 변경하지 않는다. I>N 또는 확인 범위의 I>P는 거절하며 자동 clamp/자동 선택하지 않는다. 필수량 없음·평가 불가 P=None을0/N으로 바꾸지 않는다. 누락 이행/배정량을0으로 채우거나 주문량에서 N을 만들지 않는다.

Task03의 선택 관측만 기존 available_stock으로 일관성 검사하며 제외 관측을 배정에 재도입하지 않는다. BOM 소요 계산도 기존 calculate_material_requirements를 재사용하고 파생 q/D/A/S/P뿐 아니라 재계산 오류·충돌·미확인/질문을 대조한다. 순수 dict의 source/hash는 연결 근거이지 파일 진위나 현재 사용자 권한 증명이 아니다. 실제 저장 후보의 상태는 반드시 호스트 경로로 재조회한다.

확인된 같은 부품의 적격 로트를 lot_id 순서로 배정한다. FIFO/품질/유효기간 최적화가 아니다. C=I×q, R=N−I, A′=A−C, D′=R×q, S′=max(0,D′−A′)이며 q 또는 A가 미확인이면 소비/잔여재고/순 부족을 확정하지 않는다. reported_available에 예약/제한을 재차 빼지 않는다. 로트 사용합=C·개별 가용량 이하·부품 합계 일치를 검사한다.

remaining_materials는 purpose=virtual_material_review_only이며 virtual stock에 원본 관측 original_stock과 별도 virtual_deductions를 보존한다. 원본 ID/hash 아래 관측을 고쳐 저장하지 않는다. 가상 결과를 새 원본으로 넣으면 거절한다. comparison/selected는 호출자가 명시한 비영속 검토 상태다. 비교안은 각각 원본을 deepcopy하여 계산하므로 반복 호출·결과 변경이 다른 안에 영향을 주지 않는다.

대표3항목만 완결된 시험에서 N/I/R=100/70/30, q=[1,8,11], 초기 A=[70,560,1100], C=[70,560,770], A′=[0,0,330], D′=[30,240,330], S′=[30,240,0]이다(row-55/9/14 순서). 같은 확인 범위에서 I≤P이면 D′−A′=D−A라서 최초 S와 S′가 같다. 실제 손실/불량/재작업0을 보증하지 않는다.

실제 전체 BOM74행·74부품(populated63/unknown11)에서는 재고 미확인71개·whole_bom_material_limit=None을 유지한다. 범위 내 P=70은 전체 생산70대 준비 완료가 아니다. 순수 함수의 확인 상태는 unverified이며 confirmed dict로 권한을 발급하지 않는다. 호스트 경로는 현재 확인 결과만 별도 표시한다. 모든 계획의 production_authorized=False다.

`examples.materials_plan`은 지정 일회용 PG에서 실제 catalog/기존 가정 그래프 개발 시드 → 수작업 해석 fixture → 기존 prepare/save → 새 workflow 조회 → Task03/04 비교0/30/70 → 선택70을 실행한다. contract.txt 실제 bytes SHA를 request.json 및 인계 hash와 대조한다. 이 예제의 수요 확인 사건0·verified=false, 실제 모델/API 요청0이다. 기존 persistence 예제의 개발 확인 사건과 구분한다. 계산 전후 공개 그래프/수요 값 불변을 확인한다. 시드는 테스트 준비이며 계획 계산의 부작용이 아니다.

규칙은 production-0.1, 입력/잔여 산술은 materials-0.1이다. 공통 query의 temporal-0.1/run_id/data_version은 변경하지 않는다. Task02의 납기·배송지·제약 확인, Task05~07, 화면/HTTP/Agent 도구/새 계획 저장은 후속이다.

실제 최종 명령별 통과/실패/오류/skip과 보존·정리는 [Task04 구현 결과](../../sungsoo/task_04_구현결과.md), 코드 지도·손계산·실습과 답은 [Task04 학습](../../sungsoo/task_04.md)에 기록한다.

## 16. Task05 실제 근거의 대체 검토·전체 구성 재계산 연결 (2026-10-28)

Task05의 검토·조건부 재계산 연결·검증 범위를 구현했다. **실제 L→L1 적용 승인은 미확보이며 실자료 재계산은 보류**한다. 기존 Task02~04/공통 코드·규칙·BOM·실장 상태·원본 manifest는 변경하지 않았다. 앞 절의 Task05 후속 표기는 해당 절 작성 당시 상태다.

| 업무 함수 | 입력과 결과 |
| --- | --- |
| `load_revision_evidence(*, root=ROOT, metadata=None)` | 새 전용 메타데이터와 고정 원본 bytes/hash/커밋/URL/절/라이선스 근거를 대조. acquired 또는 unavailable 및 진단. 자동 다운로드/원본 교체 없음 |
| `query_substitution(session, *, configuration_id, component_id, candidate_configuration_id, data_version)` | 기존 `lookup_contract_configurations`의 lookup/configuration 및 `component_usage`/`compare_configurations` 호출. 전체 응답과 오류·정확 ID/버전·preview 유지 |
| `build_substitution_review(shortage, comparisons, evidence, constraints)` | Task03 원본 또는 Task04 가상 잔여 자료. 후보·관련 변경·상태 분리·미확인·원 부품 구매 검토. 가상 잔여는 별도 shortage_scope 표시 |
| `review_same_component_lots(snapshots, scope, *, component_id)` | 기존 available_stock 재사용. 정확 부품의 적격 로트·범위·시각·완전성 결과, 가용량/부분합 구별. 기존 재고와 중복 합산하지 않음 |
| `recalculate_configuration_review(workflow, host, context, review, *, candidate_configuration_id, demand_id, item_id, data_version, snapshots, scope, initial_quantity, expected_revision, expected_target_sha256, applicability_reader=None)` | 호스트 전용. 현재 공개 조회·등록 근거·수요 수정본/hash/확인/허용·원래 N·명시 I·원본 재고를 확인. 미결이면 blockers와 기존 envelope, 허용되면 build_saved_production_plan의 전체 결과 |

후보 발견, 원본 확보, 그래프 근거 등록, 기술 적합, 계약 허용, 사람 검토, 자재 계산, 생산 실행은 `states`의 별도 값이다. 순수 입력의 approved/confirmed/valid나 등록 승인으로 기술/계약 권한을 만들지 않는다. `production_authorized=False`다. 새 substitution 공통 규칙 키를 추가하지 않는다. Task03 materials-0.1 / Task04 production-0.1 / 공개 envelope temporal-0.1을 보존한다.

새 자료는 `data/sources/OLIMEX-Revision-changes-task05.txt`, 업무 연결 메타데이터는 `data/materials/substitution_evidence_v1.json`이다. 고정 커밋 `deb38a377d50eadb04ddc7118a17a5ae58b1ee53`, SHA-256 `c8ee91c74caa752e5ffdd8f5a18faef3dacc2c8339b1e6a7e49f4aa1939952db`, 수집 시각 `2026-10-28T08:51:27.905275+09:00`. L1 절25~34행(관련 변경33~34행), L 절38~47행을 참조한다. OLIMEX README13~16행의 documentation CC BY-SA 3.0 / hardware Apache-2.0 구분과 고정 LICENSE 원본 hash를 메타데이터에 유지한다. 변경 이력 텍스트는 문서로 귀속하며 파일별 별도 라이선스 문구를 발명하지 않는다.

U2 L row-57 TPS2376D→L1 row-56 TPS2375D는 **기존 L1 전체 구성 전환 후보**다. R6 2.2k→10k/충전 전류455→100mA, R50/R51 관련 변경을 함께 표시한다. R6는 각 BOM 행의 여러 위치 중 하나다. L1 R50 row-69와 R16/R51 row-73은 unknown을 유지한다. 단독 U2 교환, 이름·패키지에 의한 ID 병합·L 재고의 L1 전용을 허용하지 않는다.

기본 stock.json row-55/9/14의 부족30/240/0과 상한70, N100/I70/R30, 전체 L BOM74·실장 unknown11·재고 미확인71·전체 한계None은 보존된다. 별도 U2 개발 fixture N100/A0/S100에서만 U2 후보를 연결한다. 이는 실제 재고/공급처/부족이 아니다. 기본 부족에는 확인한 범위에서 근거 있는 대안이 없어 정확 원 부품 구매 검토로 넘긴다. 시장 전체 부재를 단정하지 않는다.

전용 예제는 원문 계약 bytes hash를 기존 request와 대조한다. 일회용 PG에서 source1/evidence2의 새 ID를 기존 `prepare_draft`→별도 개발 검토자→정확 차수 `apply_approved`→lookup으로 연결한다. 공개 v1→v2 이후 같은 계약의 수작업 fixture를 v2로 재준비·revision2 저장·재접속한다. L→L1로 수요 dict를 고치지 않는다. 현재 확인 사건0·verified=false, 미해결 질문·permissions None을 유지하며 계산 전후 그래프/수요/재고는 불변이다. 초기 시드/후보 저장/근거 등록 준비는 계산 부작용과 구분한다. 기존 ODS 전용 원본 접수는 확장하지 않았다.

실제 Task02 `constraints`는 needs_business_decision이며 permissions는 None이다. 전기적 적합성·계약 허용을 확인할 기존 접점이 없으므로 기본 재계산은 차단된다. `applicability_reader.read_current(context, binding)`은 호스트의 향후 신뢰된 기존 시스템 연결 접점이며 이번에 실제 구현/새 저장소/서명 체계를 만들지 않았다. dict를 reader로 전달할 수 없다. 명시적인 시험용 호스트 대역만 현재 수요/기술/계약/사람 확인을 제공해 전체 L1 BOM74행·unknown12·재고 미확인74의 Task03~04 계산을 검증한다. 테스트 대역은 기술 적합·운영 승인 성공을 의미하지 않는다.

재계산은 fresh public query와 saved demand를 다시 읽고 후보 버전·수정본·target hash·현재 권한·원래 N/I를 묶는다. 한 행 혼합 BOM을 받는 인자가 없다. 가상 재고 state/purpose/original_stock/virtual_deductions와 원본 내부 표식도 검사하므로 바깥 shortage_scope만 바꿔 우회할 수 없다. 순수 가상 잔여 검토는 가능하지만 전환 비교는 원본 시나리오에서 다시 시작해야 한다.

명령별 검증·실패 재현·대역/실제 PG 구분·보존/정리는 [Task05 결과](../../sungsoo/task_05_구현결과.md), 코드 지도·변경표·실습과 답은 [Task05 학습](../../sungsoo/task_05.md)에 기록했다. 실제 모델/임베딩 API0회. 생산 실행·예약/소비·이행/배정 갱신·구매/일정·화면/HTTP/Agent 도구는 수행하지 않았다.

## 17. Task06 구매 수량 조건·예상 구매비 (2026-10-28)

새 `materials/procurement.py`의 일반 Python 계산이다. 도구/HTTP/화면/승인/저장을 추가하지 않는다. 예상 구매비는 제조원가·계약금액·재고가치가 아니다. 실제 견적을 확보하지 못했으므로 실자료 예제는 정확 원 부품의 부족30/240과 견적 필요를 반환한다. SupplierOffer 그래프 타입이나 가상 견적을 만들지 않았다.

| 함수 | 계약 |
| --- | --- |
| `purchase_quantity(shortage, minimum_order, order_multiple)` | S와 MOQ는 정수0 이상, 배수는 정수1 이상. bool/float/음수/None/비유한 거절. S=0이어도 조건 검증 후0, 양수는 max(S,MOQ)를 배수로 올림. 정수 산술 자체 상한 없음. |
| `estimate_purchase_cost(requirements, offers, cost_context)` | 한 시나리오의 정확 부품별 목록을 계산. 잘못된 요구/컨텍스트는 ValueError, 잘못된 견적은 diagnostics. 전체 견적을 먼저 검사하므로 부족0/미선택 견적 오류도 숨기지 않음. |
| `build_procurement_review(materials_or_plan, offers, cost_context, *, scenario, substitution_review=None)` | original이면 전체 Task03 결과, virtual_remaining이면 전체 Task04 계획. 기존 Task04 재계산 검증을 재사용. Task05 전달 시 original_materials/구성/버전/범위/purchase_review를 정확 대조. 미확인/오류/충돌 원문 보존. |
| `build_saved_procurement_review(workflow, host, context, *, offers, cost_context, scenario, initial_quantity=None, **material_arguments)` | material_arguments는 기존 calculate_saved_demand 인자. original은 Task03, virtual_remaining은 Task04의 현재 수요/공개 조회를 호출. 조회 실패는 procurement=None, envelope 보존. preview와 가상 관측 재입력 거절. |

### 일반 함수 입력

requirements 각 행은 `component_id`, `shortage_quantity`(정수 또는 None), `unit='piece'`다. 정확 ID 중복은 거절하며 이름/패키지/다른 개정으로 견적을 연결하지 않는다. 추가 provenance 필드는 보존된다. None/누락 부족은0으로 바꾸지 않는다.

cost_context는 `evaluated_at`(offset 필수), `data_version`(정수0 이상), `configuration_id`, `scenario`(original 또는 virtual_remaining), `coverage_complete`(bool), `rounding` 통화→소수 문자열 단위가 필수다. 선택 `selected_offers={component_id:offer_id}`는 정확 견적 하나를 고른다. 견적 하나는 자동 연결 가능하나 복수 견적은 선택 없이는 계산하지 않는다. 최저가/합산/분할 발주가 아니다. 독립 비교는 같은 원래 요구에 다른 선택으로 별도 호출한다. `exchange_rates`는 미지원으로 거절한다.

견적 각 행의 필수 필드는 다음과 같다.

- 정확 `component_id`, 비어 있지 않은 `supplier_id`/`offer_id`.
- `source={source_ref,sha256,locator}`. hash는 소문자16진64자리. 순수 함수는 caller가 준 근거로 표시하며 원본 진위·공급처 인증을 보증하지 않는다.
- `data_version`: 이 구매 검토가 연결한 정확 공개 버전. 견적 발행 차수 대신 쓰지 않으며 원 견적 차수는 source_ref/locator에 보존한다.
- `currency`: 명시3자리 대문자 코드와 caller의 rounding 정책. `unit='piece'`, `price_basis_quantity`: 양의 정수. 100개 기준 금액을 개당 단가로 오독하지 않는다.
- `minimum_order`/`order_multiple`: 없거나 None이면 구매량 미정. 값이 있으면 각각0 이상/1 이상 정수.
- `pricing_method='all_units'`, `tiers=[{minimum,maximum,amount},...]`. minimum 포함·maximum 제외, 마지막 maximum=None 허용. 구매량으로 구간을 선택하고 전량에 해당 구간 가격을 적용한다. 겹침/중복은 오류, 선택 구매량의 공백은 price_tier_gap. 누진 방식은 미지원이다.
- `valid_from`/`valid_until`: offset 필수, 시작 포함·종료 제외. 평가시점은 호스트 현재 시각으로 채우지 않는다. 미래/만료 시 금액 계산 보류.
- `available_quantity`: 정수0 이상 또는 미확인(None/누락). 부족/미확인이어도 유효 단가의 산술 소계는 별도 표시한다. reported_sufficient도 실제 확보 보증이 아니다.
- `cost_inclusion={shipping,tax}`: 각각 included/excluded/unknown을 명시한다.

계산은 호출자 설정과 독립적인 Decimal Context(precision=80, Emin=−999999, Emax=999999, InvalidOperation/DivisionByZero/Overflow trap)를 사용한다. 가격·명시 비용은 decimal 문자열/int/Decimal만 허용한다. float/bool·비유한·음수 거절, 명시0은 허용한다. 유효숫자24자리·지수−12..12 범위다. 가격 계산의 구매량/기준 수량은10^18 이하로 한정하며 초과는 수량 결과와 별도로 진단한다. 이 한도는 실제 견적 정책이 아닌 계산기의 지원 범위다. 결과 money는 float 변환 없이 문자열로 직렬화한다.

rounding은 caller가 통화별 `1`, `0.1`, `0.01`, `0.001`, `0.0001` 중 하나를 명시한다. 국가 법정 반올림 정책을 자동 추정하지 않는다. ROUND_HALF_UP으로 **각 품목 소계와 각 명시 추가 비용을 반올림한 뒤 합산**한다. 가격 기준 수량은 비례 가격 기준이며 포장 단위 강제 조건은 order_multiple로 별도 표현한다.

### 비용과 미정

선택 costs는 `costs[currency][shipping|tax|other]={state,amount?}`다. 누락은 unknown이다. separate에만 유한0 이상 amount를 명시한다. included는 선택 견적 전부의 해당 비용 included와 일치할 때0을 추가하며 이중 합산하지 않는다. separate는 견적 전부 excluded일 때만 합산한다. unknown/서로 다른 포함 조건은 총액 미정이다. other는 별도 명시 금액(0 가능) 또는 unknown이며 임의 포함으로 완료하지 않는다. 한 통화 전체의 해당 비용을 한 번 주는 계약이며 여러 개 기타 비용은 caller가 근거 있는 합계를 명시해야 한다.

`items`는 순 부족/구매량/초과량·이유/가격 기준·구간/소계/공급 상태/진단을 분리한다. 초과량은 재고에 더하지 않는다. `known_item_totals`는 통화별 알려진 소계만, `additional_costs`는 실제 입력과 추가 금액, `unresolved_items`는 아직 산술 계산 못 한 부품이다. 전체 품목/범위/비용이 확인되어야 `total_by_currency`가 나온다. 전부 견적 없음은 `{}`와 총액None이다. `grand_total`은 환산을 지원하지 않아 항상None이다. 모든 항목이 부족0인 경우 각 구매량0·not_required를 반환하되 통화 없는 총액0을 만들지 않는다.

### 시나리오·권한·근거

연결 함수는 양의 purchase_review뿐 아니라 원본 materials 전체를 보존한다. original/virtual_remaining을 더하지 않는다. 가상 잔여의 purpose/state/original_stock/virtual_deductions를 유지하며 새 실제 관측으로 받지 않는다. 오류/충돌 입력은 원문을 보존하고 구매 수량을 미정으로 둔다. 깨끗한 입력의 파생 수량은 기존 Task04 검증으로 대조한다. dict 검사는 caller 근거 일관성 검사이며 인증이 아니다. `approved/confirmed/valid`로 권한을 발급하지 않고 항상 purchase_authorized=False다.

호스트 경로는 기존 Task02 get, Task03 calculate_saved_demand, Task04 build_saved_production_plan을 재사용한다. 현재 수정본·target hash·공개 버전·read 권한·원문/수량 근거·permissions·미해결 질문을 보존한다. Task05 build_substitution_review의 정확 원 부품 목록을 재사용하며 실제 L→L1 전환 보류를 바꾸지 않는다. calculation.rule_version=procurement-0.1, 입력 materials-0.1/production-0.1 및 공통 temporal-0.1/run_id는 그대로다.

전용 예제는 실제 원문 bytes SHA를 request.json 및 인계 hash와 대조하고, 새 일회용 PG의 개발 시드·수작업 해석 fixture·save·새 workflow get을 사용한다. 실모델 출력/운영 사람 확인이 아니다. N/I/R=100/70/30, 원래와 잔여 S=30/240/0, 전체74행·unknown 실장11·재고 미확인71·whole_bom_material_limit=None이다. 수요 확인 사건0·verified=false를 유지하며 계산 전후 수요/그래프 불변을 검사한다. 모델/API0회, 발주/예약/소비/생산 작업 생성0회다.

정확 시험 결과와 보존 기록은 [Task06 결과](../../sungsoo/task_06_구현결과.md), 설명·실습은 [학습 문서](../../sungsoo/task_06.md), 빠른 안내는 [한눈에 보기](../../sungsoo/task_06_작업_한눈에_보기.md)에 있다. 실제 견적·MOQ·배수·공급량·포함 비용·수요 확인은 후속 확보 대상이며 Task07 일정·Task08 문서는 별도다.

## 18. Task07 자재 사용 가능·선행/잔여 생산 시간 예상 (2026-10-28)

[일정 모듈](../ontologics/materials/scheduling.py)은 일반 Python 읽기 전용 계산이다. 공통 temporal/model/host/도구를 변경하지 않는다. 규칙은 모듈 내부 `scheduling-0.1`, 입력은 materials-0.1/production-0.1/procurement-0.1이다. 생산·구매 권한과 자원 예약은 항상 False, 고객 도착 준수와 대체안 순위는 None이다. 완료된 시간 산술은 실제 입고·생산 사건이 아니다.

### 공개 함수와 연결

| 함수 | 입력과 반환 |
| --- | --- |
| `estimate_material_ready(materials_or_plan, schedules, context, *, scenario, offers=None, cost_context=None)` | original은 Task03 전체 자재, virtual_remaining은 Task04 전체 계획. Task06 `build_procurement_review`로 정확 시나리오와 파생 수량을 검증한 후 부품별 `items[].ready_at`, 부분 최후 `known_latest_at`, 전체 `ready_at`를 구한다. |
| `estimate_production_schedule(plan, resource, material_ready, *, initial_conditions=None, remaining_setup_minutes=None)` | 전체 Task04 계획, 자원, 그 계획의 virtual_remaining 준비 결과. 초기 별도 가정 및 잔여 작업별 work_start/processing_start/completion 또는 None. 입력 준비 결과를 원 입력에서 다시 계산해 변조·다른 시나리오 혼입을 거부한다. |
| `build_saved_schedule_review(workflow, host, context, *, initial_quantity, resource, schedules, schedule_context, offers=None, cost_context=None, initial_conditions=None, remaining_setup_minutes=None, **material_arguments)` | Task04 `build_saved_production_plan` → Task03 `calculate_saved_demand` → Task02 현재 get/공개 구성 조회를 재사용. 일정·자재 결과와 원 demand_response/query/plan/errors를 함께 반환. 실패·preview는 schedule=None. |

Task06이 Task05 `build_substitution_review`의 원 부품 구매 검토 목록을 연결한다. 새 가격 산술·수요 저장·권한 체계는 없다. `material_arguments`는 기존 demand_id/item_id/data_version/snapshots/scope/expected_revision/expected_target_sha256이다. snapshots는 실제 관측 형식만 받고 가상 state/purpose/original_stock/virtual_deductions 표식은 거절한다. 순수 dict 함수는 일관성 검사이며 인증이 아니다. 저장 수요 진위·현재 권한은 기존 호스트 경로에서 검사한다.

### 일정 근거 입력

context에는 정확 configuration_id/data_version/scenario, 명시 evaluated_at, selected_schedules(부품ID→일정근거ID), milestones(이정표ID→사건)가 필요하다. cost_context를 주면 이 네 연결 필드가 동일해야 한다. 생략하면 빈 가격 조건으로 Task06을 호출한다. 견적·가격이 없다는 이유로 독립 일정 근거를 지우지 않는다. 가격 미확보는 procurement에 별도로 남는다.

schedules는 정확 부품별 단일 선택 근거 목록이다. 각 레코드의 계약:

- `schedule_id`, `component_id`, `configuration_id`, `data_version`, `scenario`, `board_quantity`, `demand_source`: 대상 전체 자재 계산과 정확히 일치. `board_quantity`는 해당 시나리오의 보드 수, `quantity`는 부품 개수다.
- `source={source_ref,sha256,locator}`는 근거 ID/소문자 SHA-256/원문 위치. caller 근거를 보존하며 이 함수가 원문 bytes를 다운로드·인증하지 않는다.
- `unit='piece'`, 정수 `quantity`, `coverage='full_requirement'|'shortage'`. full_requirement는 총 소요 D 이상이며 확인된 구매량보다 작을 수 없다. shortage는 max(순 부족 S, 알려진 구매량) 이상이다. 알려진 구매량이 없으면 S 기준의 조건부 결과와 MOQ/배수 미확보를 함께 남긴다.
- shortage 근거에서 D>S라면 `existing_usable={state,at,source}`가 추가로 필요하다. 재고 관측 시각이나 부족0을 투입 가능으로 바꾸지 않는다. full_requirement는 기존분까지 포함한 전체 사용 가능 근거다. 과잉 구매량은 보드 수/현재 재고에 더하지 않는다.
- `valid_from`, `valid_until`: 평가시점의 유효 구간(시작 포함, 종료 제외). `state='actual'|'assumed'|'promised'`는 caller 보고이며 실제 여부를 인증하지 않는다. 미래 actual 사건은 거절한다. 모든 출력은 검토용 estimated이다.
- `required_milestones`는 명시적 ID 목록이다. 빈 목록은 이 근거에 별도 선행 이정표가 없다는 caller의 선언이며 실제 검토·송부·확약을 의미하지 않는다. 각 이정표는 `{state,at,source}`. 누락/unknown이면 이 부품은 미정이다. 구매 검토·송부·발주/확약·기술 검토·고객 합의·구성 확인을 서로 다른 ID로 연결한다.
- 선택 `offer_id`를 쓰면 `supplier_id`와 함께 Task06이 선택한 정확 견적에 연결하고 부품·버전·unit·source와 유효기간도 확인한다. 가격 필드 오류와 일정 식별 오류를 구별한다. 독립 일정 근거는 이 필드 없이 사용할 수 있다.
- `kind='usable_at'`: at는 입고 검사·내부 이송까지 끝나 투입 가능한 시각이다.
- `kind='arrival_at'`: at는 도착 시각이며 `inspection={value,unit}`를 더해야 한다. 검사·이송 포함 후처리 시간을 명시한다. 빠뜨리면 None, 명시0은 허용한다.
- `kind='lead_time'`: `start_milestone`의 시각 + `lead_time={value,unit}` + 별도 운송 + 검사/이송. state는 assumed 또는 promised다. `transport_included` bool이 필수이며 True면 별도 transport를 다시 가산하지 않는다. False면 `transport={value,unit}` 필수. inspection은 항상 명시한다. 선행 이정표가 기산점/직접 사용 가능 약속보다 늦으면 상충으로 보류한다.
- stock_observed/reported_sufficient/dispatch를 사용 가능으로 해석하지 않는다. 여러 견적·일정은 정확 선택이 필요하다. ID 중복·상충은 진단하고 자동 최단 납기 선택·합산/분할 발주는 하지 않는다.

입력 전체·선택된 근거·upstream_diagnostics는 반환의 inputs/procurement/items에 보존한다. 부품별 실패는 items[].issues, 목록 밖 근거는 diagnostics에 남긴다. 오류/충돌/미확인 전체 자재 범위를 은폐하지 않는다. 모든 항목이 알려져도 BOM 미완전·unresolved가 남으면 전체 ready_at=None이다. 필수 목록이 비어 있어도 전체 완료가 아니다. 알려진 항목만의 max는 known_latest_at이다.

### 시간·수치·생산 범위

시각은 `YYYY-MM-DDTHH:MM:SS[.ffffff](Z|±HH:MM)` 문자열, 최대 소수6자리다. 날짜-only/naive/잘못된 offset은 거절한다. 서로 다른 offset은 절대 시각으로 비교하고 생산 출력은 자원 available_at의 고정 offset을 사용한다. 고객 날짜 `2026-11-10`은 별도 원문으로 보존하며 계산 시각에 넣지 않는다.

수량은 bool/float를 제외한 정수0..10^18. 시간/속도는 int/Decimal/decimal 문자열, 유한0 이상·유효숫자24자리·지수−12..12이다. 속도는 반드시 양수다. float/NaN/Infinity/음수는 거절한다. Decimal→정확 유리수(Fraction)로 바꿔 호출자의 Decimal precision/trap에 의존하지 않는다. 각 준비/처리/리드타임/운송/검사 기간을 **초 단위 올림** 후 더한다. 작은 소수 기간도 누락되지 않는다. datetime 범위를 넘으면 time_range_exceeded·완료 None이다.

시간 단위는 seconds/minutes/hours/days이며 days=24시간 연속 경과다. 기본 calendar는 continuous_elapsed_24h. 영업일/휴일/교대/다중 자원 최적화는 지원하지 않는다. 다른 calendar/calendar_policy(기간·일정·context·자원 포함), shifts/shift_calendar/holidays, resources, remaining_resource_ref는 진단한다. 자원에는 resource_ref/available_at/setup_minutes/boards_per_hour와 loss_quantity/defect_quantity/rework_quantity=0, interruption_policy=none_assumed를 명시한다. 다른 손실·중단 조건은 재계산 필요로 반환한다.

초기 작업은 별도의 `initial_conditions={state:'assumed', scope:'all_required_materials_for_initial_quantity', quantity:I, ready_at, source, prerequisites:[사건...]}`를 받는다. 이 경로는 전체 필수 자재를 별도로 가정한 시간 산술만 지원한다. 실제 자재 검증을 대신하지 않으며 whole_bom_material_limit과 남은 미확인을 그대로 표시한다. 가정이 없거나 선행 조건이 미정이면 초기 완료도 None이다.

```
작업 시작 = max(자원 가용, 초기 가정 자재 준비, 명시 선행 조건 완료)
가공 시작 = 작업 시작 + 준비 시간
완료 = 가공 시작 + 보드 수 / 시간당 보드 처리속도
잔여 시작 = max(같은 자원 가용, 선행 완료, 잔여 자재 사용 가능)
```

잔여 `remaining_setup_minutes`는 명시 정책(0 허용), 누락 시 미정이다. 조달 시간과 선행 처리 시간을 직렬 합산하지 않는다. 초기 작업이0이면 선행 완료 사건 없이 잔여를 계산하고, 잔여0이면 준비/완료 사건을 만들지 않는다. N=0은 두 작업 모두 no_work다. 별도 자원이나 무제한 병렬 능력을 만들지 않는다.

### 실제 예제와 후속 범위

[전용 예제](../examples/materials_schedule.py)는 새 일회용 PG에서 원문 계약 SHA/request 참조·production SHA를 확인한다. 기존 수작업 해석 fixture→prepare/save→새 workflow 재접속 get을 쓰며 실모델 출력/사람 확인이 아니다. 동일 내용 재저장의 NO_CHANGES도 검사한다.

N/I/R=100/70/30, 실제 공개 L BOM74행·실장 unknown11·개발 재고 미확인71은 유지한다. row55/9/14의 q=1/8/11, 원래 A=70/560/1100, D=100/800/1100, S=30/240/0이다. 가상 사용70/560/770, 잔여 A=0/0/330, D=30/240/330, S=30/240/0. positions를 다시 곱하지 않는다. U2 별도 시험100은 이 구매 목록에 섞지 않는다.

resource:planning:001의 과거 개발 가용 시각 2026-10-26T10:00:00+09:00, 준비60분, 20대/시간과 별도 초기 자재 가정을 쓰면 10:00→11:00→14:30이다. 실제 전체 준비, 잔여30대 준비·완료, 고객 도착 준수는 None이다. 원 부품 MOQ/배수/견적/확약·사용 가능 시점은 미확보다. L→L1 TPS2376D→TPS2375D 후보의 기술/고객 합의/구성 검토도 미확인으로 남으며 더 빠르다고 순위를 매기지 않는다.

Task02 delivery_arrival=needs_material, constraints=needs_business_decision, revision_change/substitution=None을 유지한다. 수요/확인 사건/재고/공개 그래프를 변경하지 않으며 기존 조회 감사는 업무 상태 변경과 구분한다. 새 견적·조달 긍정 입력은 tests에만 있다. 모델/임베딩 호출, 운영 인증·운영 생산, 새 화면·도구·그래프, Task08 문서는 범위 밖이다.

실행 명령·새 검증 수·보존/정리 기록은 [Task07 구현 결과](../../sungsoo/task_07_구현결과.md), 설명과 실습은 [학습](../../sungsoo/task_07.md), 요약은 [한눈에 보기](../../sungsoo/task_07_작업_한눈에_보기.md)를 따른다.

## 19. Task08 업무 문서 초안·편집·재계산·Markdown export (2026-10-28)

`materials/documents.py`는 세 업무 문서의 **임시 초안**을 만든다. Task08은 부분 완료다. 같은 PostgreSQL의 문서 저장·불변 버전·버전별 사람 검토·사람의 외부 송부 사실 기록은 공개 접점이 없어 미구현이다. Markdown 파일은 검토 산출물이며 저장소나 웹 다운로드 기능이 아니다. 문서 검토·송부·발주·입고·생산 실행 사실을 생성하지 않는다.

| 실제 함수 | 계약 |
| --- | --- |
| `build_business_documents(context, results)` | `results={schedule, query?}`. Task07 전체 일정/자재를 기존 함수로 재검증한다. context의 configuration_id/data_version/demand_source가 계획과 정확히 같아야 한다. scenario는 original 또는 virtual_remaining. 세 초안 list를 반환한다. |
| `build_saved_business_documents(workflow, host, context, *, scenario='virtual_remaining', editorial=None, **calculation_arguments)` | Task07 saved → Task04 → Task03 → Task02 현재 get/공개 조회를 재사용한다. L 구성의 양의 부족에는 기존 Task05 공개 L/L1 비교와 보관 원본 검증을 추가한다. 기존 envelope와 errors를 보존하며 실패/preview이면 documents=None이다. |
| `edit_document_text(document, edits)` | note/request_message만 허용. 제목·수량·금액·시간·근거·권한·상태·ID 수정은 거절한다. 원 객체를 보존하고 새 임시 내용 hash를 만든다. |
| `recalculate_business_documents(previous, workflow, host, context, *, calculation_arguments, edits)` | 세 이전 초안의 payload 일치·무결성을 확인하고 이전 demand_id/item_id/data_version/revision/target hash를 강제한다. 현재 수요·권한·공개 버전으로 다시 계산한다. 실패는 documents=None/errors/original_preserved=True. |
| `export_document_markdown(document, output_directory, filename, *, expected_sha256)` | 정확 임시 payload/body/hash를 대조한 후 기존 신뢰된 출력 디렉터리 안에 UTF-8 .md 한 파일을 배타 생성한다. 경로 탈출·중첩 경로·기존 파일/심볼릭 링크 덮어쓰기를 거절한다. |

순수 context 선택 필드는 comparisons/evidence/constraints(Task05 입력), editorial이다. 임의 후보 결과/approved/문서 상태를 주입하지 않는다. 순수 dict 근거의 일관성 검사이지 인증·현재 상태 확인이 아니다. 저장 수요와 연결하려면 saved 진입점을 사용한다. 추가 공급처 인증, 원문 다운로드, 모델 호출은 없다.

구조화 edits 허용 목록은 initial_quantity/resource/schedules/schedule_context/offers/cost_context/initial_conditions/remaining_setup_minutes다. 각 객체는 완전 교체 입력이며 Task03~07의 기존 검증을 통과해야 한다. N·확정 구성·수요 수정은 이 API로 우회할 수 없다. 선행 수량만 바꿔 초기 가정 quantity와 맞지 않으면 옛 성공 시간을 유지하지 않고 초기 미정/진단을 반환한다. 가격·기간 산술을 복제하지 않는다. 다른 수요를 비교하려면 별도 수요/새 초안을 준비한다. 신원 객체는 기존 호스트의 trusted argument이며 caller dict의 reviewer를 받지 않는다.

각 초안은 kind/title/payload/editorial/state/content_sha256/draft_id/markdown을 가진다. document_ref/document_version=None, draft_id=`temporary:<내용 hash>`이며 DB ID/버전/전자서명이 아니다. data_version, 수요 revision, target/source hash, materials-0.1/production-0.1/procurement-0.1/scheduling-0.1/documents-0.1을 구별한다. 원 수요·권한·미해결 질문, BOM 행·positions·원문 표기·입력 Evidence, q/D/A/S·로트별 가상 사용/잔여, 전체 미확인·가격/시간/진단·조회 run_id가 payload와 Markdown 부록에 남는다. 공통 temporal-0.1은 변경하지 않는다.

상태는 review=unreviewed, dispatch=unsent, purchasing_received/ordered/received=unknown이다. production_authorized/purchase_authorized/resource_reserved=False, customer_arrival_compliance/alternative_ranking=None이다. 그래프 awaiting_review/approved/applied를 문서 상태로 사용하지 않는다. 자유 의견은 미검증 문구란에만 표시되며 잠긴 표·payload를 바꾸지 않는다. 내용 hash는 우발적 변조/잘못된 출력 선택 방지이며 신뢰된 서명이나 영속 버전 검증이 아니다.

현재 자료는 **견적·납기 확인 요청**이다. `purchase_review`는 원 부품 양의 부족30/240이며 unknown71개를 부족0으로 바꾸지 않는다. 구매 검토 요청 제목은 양의 목록, 전체 범위·유효 견적/MOQ/배수/공급량·가격·포함 비용과 적격 전체 자재 일정이 모두 충족될 때만 사용한다. 이 조건도 구매 승인이 아니다. 양의 부족이 없으면 완전한 전부0은 구매 신청 불필요, 빈/미확인 범위는 구매 대상 미정이다. 혼합 통화/부분 소계/전체 미정과 문자열 금액 정밀도를 보존한다.

original 선택은 전체 Task03에서 별도로 계산하며 잔여 일정 근거를 original로 이름만 바꾸지 않는다. 필요량이 같다는 이유로 원래/잔여 부족을 합하지 않는다. 별도 U2 시험100을 기본 구매 목록에 넣지 않는다. L1 전환의 후보 발견·기술 검토·고객 합의·구성/실장·수요·재고 확인을 분리한다. 실자료 후보가 없으면 검토한 범위 내 미확보로 표시한다.

현재 예제는 원문 hash/request 참조를 확인하고 실제 일회용 PG의 개발 신원·수작업 해석·가정 재고로 현재 수요를 조회한다. 전체74행/실장unknown11/재고미확인71, N100/I70/R30, 별도 초기 가정10:00→11:00→14:30, 실제 전체 자재/잔여 완료None, 날짜-only 고객 납기2026-11-10을 유지한다. 새 확인 사건0, verified=false, 동일 내용 재저장NO_CHANGES. 실모델/임베딩 API0회다.

영속 연결의 구체적 최소 변경·권한/재생/원자성/감사/동시성 시험 계획은 [Task08 구현 결과](../../sungsoo/task_08_구현결과.md)에 있다. 공통 코드와 Task01~07은 변경하지 않는다. [학습8절](../../sungsoo/task_08.md), [한눈에 보기](../../sungsoo/task_08_작업_한눈에_보기.md)를 함께 참고한다.

최종 새 검증: Task08 21개(순수/BOM14·실제 PG7), 업무344개·전체488개, 지정 예제10개 정상 종료·실패/오류/skip0. 문서 저장 접점의 시험은 미구현으로 미실행이다. 잘못된 순수 입력은 ValueError, 공개 조회 실패/preview는 saved documents=None, 구조화 재계산의 입력 오류는 DOCUMENT_RECALCULATION_FAILED로 구별한다.


### A 웹 실제 API 모드 (2026-10-30)

`python -m examples.materials_ui --direct-test-fixture --live --env-file .env.local`

명시된 일회용 PostgreSQL 연결과 공통 비용 환경 설정(`ONTOLOGICS_REQUEST_MAX_COST_USD`, `ONTOLOGICS_MODEL_PRICES_JSON`)이 필요하다. 키는 공통 환경 로더로만 읽는다. 기본 모드는 기존 scripted 회귀용이며 `--live`에서는 계약 해석 및 Agent 검토에 공통 실제 OpenAIProvider를 사용한다. 실패 시 대역 fallback은 없다. API 호출 수와 run_id, 도구 호출 ID는 화면 상세에 표시한다. `생산계획 계산하기`는 모델을 호출하지 않고 `계산하고 검토 의견 확인`은 실제 모델 검토까지 수행한다. DB는 개발 fixture를 직접 넣은 격리 schema이며 운영 등록 승인을 대신하지 않는다.

실제 웹 시나리오1개에서 계약 해석1회 + 검토2회 및 configuration 도구 조회를 확인했다. 100/70/30 수량, 미확인71부품·잔여일정null 유지, 저장 문서3개를 확인했다. 상세 실행 근거와 한계는 PROJECT_STATUS의 같은 날짜 기록을 따른다.


## A 전용 실행·저장 경로 (2026-10-31)

스키마 분리 후 A 업무는 `materials.host.MaterialsHost`와
`materials.persistence.MaterialsService`를 명시적으로 사용한다.
공통 `model.py`, `service.py`, `host.py`, `database.py`는 수정하지 않는다.
공통 Service로 A 타입을 저장하는 경로는 여전히 유효성 오류를 반환한다.

- A 호스트 → 공통 RequestSession/AgentGateway → A 서비스 → A 소유 PostgreSQL 스키마.
- A 검증기는 공통 노드를 공통 검증기로 검사하고 A 노드의 필드·관계·근거·수량·시간을 별도로 검사한다.
- 공통 `_run`의 권한 검사·DB 잠금·트랜잭션·요청 해시/재실행·실행 기록을 상속한다.
- 스키마에 직접 묶인 bootstrap, draft 검증, review, apply, preview, JSON-LD export 메서드만 A 파일에 명시적으로 소유한다. 자기 승인 금지, 정확한 승인 버전/해시, 승인자 권한 재확인, 최신 기준 버전 검사 및 저장 직전 재검증을 유지한다.
- 이 메서드는 현재 공통 라이프사이클에서 분리한 코드이므로 공통 승인 절차가 바뀌면 함께 검토해야 한다. 런타임 코드 생성·전역 monkey patch·SCHEMA 변경은 하지 않는다.
- 계약보관함 원문/해석/검토/회사 재고 테이블은 보존한다. 기존 A 업무의 버전별 그래프·승인·수요 확인·문서 저장 경로도 같은 명시적 A 호스트/서비스를 사용한다. 기존 사용자 계약을 임의로 승인된 그래프로 변환하거나 테스트 시드를 사용자 DB에 삽입하지 않는다.
- A JSON-LD는 `graph_export.graph_jsonld`, 단일 T-box는 `export_tbox.build_tbox`를 사용한다. 공통 JSON-LD 변환기에 A 노드를 넘기지 않는다.
- 개발용 bootstrap과 실제 사용자 승인 절차는 별개다. examples와 격리 DB 테스트는 명시적 개발 시드를 사용하며, LLM 대역 테스트 통과를 실제 API 호출 증거로 취급하지 않는다.
