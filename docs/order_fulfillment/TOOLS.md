# Order Fulfillment — B 도구 계약

공통 문서 [docs/TOOLS.md](../TOOLS.md)의 메인 기준(`7c5e2d9`)과 비교해 B 개발 중 추가·변경된 내용을 분리했다. 공통 문서는 해당 메인 원본으로 복원했다.

아래 테스트 수·실제 모델 실행·수락 여부는 원문에 기록된 당시 결과다. 현재 폴더 분리 이후의 전체 동작을 새로 검증했다는 뜻은 아니다. 실행 예제와 B 파일 경로는 현재 하위 폴더 위치로 표시한다. 이 문서의 과거 기록 이후 공통 Provider·Service·도구 정의를 B 하위 폴더로 분리했다. 현재 검증 상태와 남은 모델 연결 문제는 [B 작업 기록](PROJECT_STATUS.md)을 따른다.

## 추가·변경 1

> 원문 위치: 문서 시작 (1–13행)

### 공통 도구 계약 0.7.0

**최신 CQ06 Task10(2026-10-27) — 개발 범위 수락:** 새 전체586개·skip0·129.653초와 무료예제34개를 통과했다. Task07 설계28개를 고유 검사83개의 새 통과 로그와 대조하고 CQ06 예제5사례를 독립 정답과 확인했다. Task09 실제 불량/운송 각27/27·별도 의미 PASS는 현재 코드/자료/원문 해시 일치로 재사용했다. 이번 추가 LLM0회·$0, 업무코드변경0. CQ06 Task07~10의 명시 승인 입력·고정 가상 자료·읽기 Agent 개발 범위를 수락했다. 반복 신뢰도·서비스 연결·운영/전체MVP 수락은 별도다. 기존 작업·개인 Git 제외·브랜치/main 보존, push0.

**이전 CQ06 Task09 실제 검증(2026-10-27) — 두 대표 통과:** 공용 pattern 제거 코드·출력1,024 한도에서 불량 증가 새4단계와 운송 변경 새4단계가 각각 자동27/27·별도 의미 검토를 통과했다. 실제8/최대8회·과거응답재사용0·자동재시도0. 입력68,489(캐시32,128)·출력2,091토큰, 캐시 반영 표준단가 추정$0.03908985(청구미확정). 고정 가상 두 대표의 Task09 검증을 완료했으며 Task10은 진행하지 않았다. 반복 신뢰도·운영 수락·전체MVP 완료를 뜻하지 않는다. 기존 업무 코드·자료·키·개인 Git 제외와 main은 보존했다.

**이전 공용 코드·무료 검증(2026-10-27):** CQ06 최종 API 스키마의 문안4개에서 `pattern`만 제거했다. Python 기본 문안의 고정 `enum`·type·description·16필드와 로컬 길이/줄바꿈/문안/사실/근거/권한 검사는 유지한다. 관련44개·전체586개(skip0)·무료 예제34개 통과. 이번 실제 LLM 호출0회, 공용 입력·모델·출력1,024 한도는 그대로다. 이전 개인 진단의2,048 한도·과거 응답3개 재사용 성공과 구분하며, 수정 공용 코드의 새 실제4단계 검증·Task09 전체·Task10은 아직 미완료다.

**이전 실제 진단(2026-10-27) — pattern만 제거한 마지막1회 통과:** 원래 입력49,705바이트·모델·2,048출력 한도·enum·description을 유지하고 문안4개 pattern만 제거한 마지막 실제1회가 completed·자동27/27·별도 의미 검토를 통과했다. 과거 성공 응답3개는 재사용했으며 추가 호출/운송 대표/Task10은 진행하지 않았다. 입력14,023(캐시13,824)·출력696토큰, 캐시 반영 표준단가 추정$0.00431805(청구미확정). 이번 비교에서 pattern 제거 후 결과가 달라졌지만 API 내부 원인·반복 신뢰도·Task09 전체 통과는 확정하지 않는다. 업무 코드와 공개 기본 스키마는 변경하지 않았다.

**이전 무료 진단 — CQ06 스키마/입력 축소 후보 준비:** 실제 API·API 키 로드·DB 사용0으로 마지막 요청을 원래 SHA 그대로 재구성했다. 16필드와 양쪽 버전의 선택 근거 메타데이터를 보존한 진단 후보에서 입력49,705→9,928바이트, 전체 요청54,514→14,321바이트로 줄였다. 고정 enum4개는 기존 pattern을 모두 만족하며 로컬 모순은 발견하지 못했다. 중복 제약과 프롬프트1,024/API2,048 불일치를 확인했고49개 동치 경계 사례·독립 검토를 통과했다. 축소본은 비전송 진단용이며 원격 실패 원인은 미확정이다. 업무 코드/기본값은 그대로이고 Task09 전체·Task10은 미완료다.

**이전 실제 기록 — CQ06 출력2,048 최종 단계 진단 실패:** 불량 증가 사례의 마지막 출력 한도만1,024→2,048로 바꿔 실제1회 진단했지만 다시 incomplete/max_output_tokens·output=[]·API 보고 사용량0으로 실패했다. 과거 성공 응답3개를 원래 호출ID/내용으로 재사용했고 최종 요청의 입력·모델·스키마는 동일함을 SHA로 검사했다. 추가 호출·운송 대표·Task10은 진행하지 않았다. 사용량0 보고는 청구0이나 실제2,048토큰 소진의 증거가 아니다. 한도 증가만으로 이번 문제가 해결되지 않았으며 API 내부 원인은 미확정이다. 업무 코드/기존 기본 한도는 변경하지 않았고 개인 진단기에만2,048을 적용했다.

## 추가·변경 2

> 원문 위치: 공통 도구 계약 0.7.0 / 그래프·저장·시간 (37–45행)

| type | 주요 필드·관계 |
| --- | --- |
| Contract | company(우리 회사), customer_name(고객). synthetic 계약 근거 필수 |
| ContractItem | contract_id → Contract, configuration_id → 기존 Configuration, quantity, unit=piece |
| DeliveryRound | contract_item_id → ContractItem, quantity, unit=piece, due_at, due_basis=arrival, destination |
| FulfillmentSnapshot | company, as_of, round_ids → DeliveryRound 목록, confirmations → 아래 9개 확인 범위 |
| FinishedGoodsStock | snapshot_id, company, configuration_id, observed_at, location, quantity, unit=piece |
| ProductionWork | snapshot_id, company, configuration_id, observed_at, target_quantity, completed_quantity, unit, production_state, 선택 started_at/planned_start_at |
| ProductionObservation | production_work_id → ProductionWork, observed_at, produced_quantity, good_quantity, defective_quantity, unit=piece. 작업별 누계·synthetic 근거 |
| Allocation | snapshot_id, delivery_round_id, quantity, unit, 선택 stock_id/production_work_id 중 정확히 하나 |
| Shipment | snapshot_id, allocation_id, quantity, unit, destination, 선택 planned_departure_at/departed_at/expected_arrival_at/arrived_at |

## 추가·변경 3

> 원문 위치: 공통 도구 계약 0.7.0 / 그래프·저장·시간 (47–49행)

실제 설계 다섯 타입은 `real`, 기존 운영 다섯 타입·계약 3타입·현황 5타입과 생산 실적1타입은 `synthetic`만 허용한다. 제품 소속·로트 부품·장착 위치가 실장 확인된 BOM과 맞아야 한다. 추가 필드로 검사·고장 원인·가동 정보를 끼워 넣을 수 없다. 새 종류의 사양은 공통 계약의 명시적 확장으로 추가한다.

`graph-0.2`는 기존 필드 의미를 유지하면서 계약 3타입을 추가한다. 계약 항목·회차 수량은 양의 **int**이며 0·음수·float·문자열·불리언은 거부한다. BOM/부품 입출고의 기존 수량 규칙은 그대로다. 계약·회차는 `piece`만 지원하며 회차별 합계는 연결된 항목 수량을 초과할 수 없다. `due_at`은 UTC offset이 있는 시각, `due_basis=arrival`은 고객 도착 기준의 기한이며 그 시각 도착까지 포함한다. 실제 도착 사실이나 출고 기한으로 해석하지 않는다. 계약 유효기간은 원문에 보존하며 납품 기한으로 대신 쓰지 않는다.

## 추가·변경 4

> 원문 위치: 공통 도구 계약 0.7.0 / 그래프·저장·시간 / PostgreSQL 저장과 pgvector 검색 (69–69행)

v0.2.0에서 도입한 PostgreSQL + pgvector 전용 기준은 v0.7.0에서도 유지한다. 후속 RDF/Neo4j 통합은 아래 별도 설계이며 현재 저장 경로나 API를 바꾸지 않는다. 그래프는 계약·현황·생산 실적·출고 조건 확장을 포함한 `graph-0.5`이고 시간 규칙 `temporal-0.1`과 O01~O08의 의미는 유지한다. 생산 예측은 별도 `production-0.1`이며 기존 장착 이력 시간 규칙을 바꾸지 않는다. 생성자는 `Service(database=None, *, principals, schema=None)`이며 기존 파일 경로/`:memory:` 입력은 거부한다. `DATABASE_URL`은 필수, `ONTOLOGICS_DB_SCHEMA` 기본값은 `ontologics`다. `.env`→`.env.local`→프로세스 환경 순서로 덮어쓴다. 접속 문자열은 로그·응답에 포함하지 않는다.

## 추가·변경 5

> 원문 위치: 공통 도구 계약 0.7.0 / 공통 반환·오류 (124–138행)

```json
{
  "ok": true,
  "data": {},
  "evidence": ["evidence:..."],
  "data_version": 3,
  "schema_version": "graph-0.5",
  "rule_version": "temporal-0.1",
  "contract_version": "0.8.0",
  "warnings": [],
  "conflicts": [],
  "errors": [],
  "run_id": "실행 식별자"
}
```

## 추가·변경 6

> 원문 위치: 공통 도구 계약 0.7.0 / 공통 반환·오류 (158–353행)

#### B-CQ02 물량 현황 조회

`Service.fulfillment_status(actor, delivery_round_id, as_of, *, version=None)`의 공개 도구 인자는 `delivery_round_id`, `as_of`, `version` 모두 필수다. Python 기본값이 있더라도 버전 생략·불리언·음수는 `INVALID_VERSION`이다. 없는 회차는 `NOT_FOUND`, 잘못된 시각은 `INVALID_TIME`, 잘못된 그래프는 `VALIDATION_FAILED`다. 신뢰된 신원과 읽기 권한, 실행 감사·공통 응답 envelope를 재사용한다. 이 도구는 그래프나 초안·승인 버전을 변경하지 않는다.

`as_of`는 저장된 FulfillmentSnapshot의 정확한 현황 시점과 일치해야 한다. 같은 UTC 순간의 다른 offset은 일치하며 가장 최신 자료를 대신 고르거나 임의 시점 상태를 재구성하지 않는다. `queried_at`은 조회 실행 시각, `observed_at`은 원천 관측 시각, `as_of`는 현황 기준이다. 예정과 실제 시각을 구분한다. 이후 조회해도 원래 관측 시각을 현재 시각으로 덮어쓰지 않는다.

`graph-0.3`은 위 5개 현황 타입을 추가한다. 모든 자료는 synthetic 근거를 가지며 같은 snapshot·회사·구성·piece 단위로 연결한다. Allocation은 stock_id/production_work_id 중 정확히 하나만 가진다. 없는 선택 참조는 허용하되 null/잘못된 타입은 거부한다. round_ids는 중복 없는 비어 있지 않은 DeliveryRound 목록이며, 회사와 UTC 순간이 같은 snapshot은 하나다. JSON-LD/T-box는 선택 관계와 목록 관계도 ID 참조로 내보낸다.

현황 수량은 int만 허용하고 불리언·float·문자열은 거부한다. 재고·생산 완료 수량은 0 이상, 작업 목표·배정·운송은 양수다. 완료 수량은 작업 목표 이하이고 회차 배정 합계는 약속 이하, 모든 회차가 공유하는 원천별 배정 합계는 재고/작업 목표 이하다. **대상 회차를 걸러내기 전에 전체 원천 상한을 검사한다.** 원천과 배정의 구성·회사·단위가 일치해야 한다.

생산 상태는 in_progress/ready/waiting이며 in_progress에는 관측 시점 이전 또는 같은 실제 started_at이 필수다. 준비/대기에는 실제 시작이 없어야 한다. completed_quantity>0은 기록할 수 있지만 첫 조회 형식의 부분 완료/재고 전환이 미지원이므로 합계를 보류한다. Shipment는 재고 배정 전량에 대응하는 한 건만 지원한다. 실제 도착에는 실제 출발이 있어야 하고 출발≤도착≤현황 기준이며, 출발은 해당 재고 관측 이후 또는 같은 시각이어야 한다. 실제 미래 사건·역순·목적지 불일치·중복/부분 출고·생산 작업 직접 출고는 거부한다. 부분/취소/반품/재배정의 임의 보완은 하지 않는다.

확인 범위는 snapshot.confirmations의 알려진 키와 boolean 값으로 표현한다. 이 주장은 근거 원문을 사람이 검토해 승인하는 것이며 코드가 원문 의미의 진실성을 자동 판독하는 기능은 아니다.

| 확인 키 | 원문에서 확인할 내용 |
| --- | --- |
| allocations_complete | 조회 회차들의 배정 목록이 완전함 |
| source_allocations_complete | 공유 원천의 다른 회차 배정도 포함함 |
| allocations_disjoint | 배정 물량이 서로 겹치지 않음 |
| stock_events_complete | 관측 이후 해당 시점까지 재고 사건을 확인함 |
| stock_observations_before_departures | 과거 재고와 실제 출고의 시간 기준이 일치함 |
| shipment_events_complete | 출발·도착 사건의 확인 범위가 완전함 |
| production_state_complete | 미완성 작업의 상태를 확인함 |
| material_disjoint | 생산·완성 재고 물량이 서로 겹치지 않음 |
| no_reallocation | 첫 형식이 다루지 않는 재배정이 없음 |

누락/false는 유효한 미확인 자료로 저장할 수 있으나 최종 합계는 내지 않는다. 잘못된 키/값은 검증 실패다. 기존 장착 이력의 HistoryCoverage와 별개다. `data.status=complete`는 이 형식의 현황 확인이 끝났다는 뜻이며 납품 완료/미래 이행 가능성을 의미하지 않는다. `needs_confirmation`이면 `states`, `allocated_quantity`, `remaining_quantity`, `unallocated_quantity`는 null이고 `unknowns`로 필요한 확인을 반환한다. 확인 범위가 완전한 빈 배정 목록은 합계 0으로 구별한다. 원천/사건은 `items`·`stocks`, 같은 버전의 약속 사슬은 `promise`, 원문 추적은 `evidence`로 제공한다. 확인 부족 시 항목 상태도 classification_confirmed=false다.

완전한 현황의 `states`는 delivered/in_transit/warehouse/in_progress/ready/waiting이다. 실제 도착→납품 완료, 실제 출발만→운송 중, 아직 출발하지 않은 재고 배정→창고로 구분한다. 재고 관측량에서 실제 전량 출고를 빼므로 창고·운송을 중복 합산하지 않는다. 예정 도착이 지나도 실제 도착이 없으면 운송 중이며 `EXPECTED_ARRIVAL_ELAPSED` 경고를 준다. 잔여 약속은 약속−실제 납품 완료, 미배정은 약속−배정 총합이다. 모든 응답은 `fulfillment_assessed=false`·`FULFILLMENT_NOT_ASSESSED`로 예측 미수행을 표시한다.

| 대표 자료·실행 | 내용 |
| --- | --- |
| [원문](../../data/order_fulfillment/sources/b_demo_fulfillment_01.md) | 고정된 가상 B-01 현황. 원본 바이트 무변경 보존 |
| [후보](../../data/order_fulfillment/b_fulfillment_fixture.json) | 45개 신규 노드, 원문 SHA-256·23개 근거와 5개 확인 범위 근거 연결 |
| [독립 정답](../../data/order_fulfillment/b_fulfillment_expected.json) | 완료100/운송150/창고250/생산300/준비100/대기100, 배정1000·잔여900·미배정0 |
| [대표 그래프](../../data/order_fulfillment/b_fulfillment_graph.jsonld) | 현황의 참조·근거 의존성을 포함한 별도 JSON-LD. 기존 대표 그래프 보존 |
| [예제](../../examples/order_fulfillment/b_fulfillment.py) | `python -m examples.order_fulfillment.b_fulfillment`: 승인 전후·같은 버전·근거·14개 검사. LLM 없음 |
| [순수 검사](../../tests/order_fulfillment/test_fulfillment.py)·[DB 검사](../../tests/order_fulfillment/test_fulfillment_service.py) | 27+8개. 전체 회차 초과·중복·미확인/0·시각/버전·승인 경계 |

현행 공통 버전은 0.7.0/graph-0.5이며 graph-0.3에서 도입한CQ02 계약과 temporal-0.1은 유지한다. 기존 모델 역할별 도구 목록은 유지하고 Task 09의 별도 현황 역할만 아래 두 도구를 사용한다. 새 원본을 바꿀 때는 새 SourceDocument/Evidence ID와 해시·기대 답을 검토해야 하며 기존 원문 ID의 바이트를 덮어쓰지 않는다.

#### B-CQ03 생산 완료 예상 조회

`Service.production_completion(actor, production_work_id, delivery_round_id, as_of, *, version=None)`는 읽기 권한·실행 감사·공통 envelope를 재사용한다. Gateway/provider의 공개 입력은 `production_work_id`, `delivery_round_id`, `as_of`, `version` 네 개 모두 필수다. Python 기본값과 무관하게 누락·음수·불리언 버전은 `INVALID_VERSION`, 없는 버전은 `VERSION_NOT_FOUND`, 없는/다른 타입의 작업·회차는 `NOT_FOUND`, 빈 ID는 `INVALID_INPUT`, offset 없는 등 잘못된 시각은 `INVALID_TIME`, 모순 그래프는 `VALIDATION_FAILED`다. 다른 작업·회차·최신 버전으로 자동 대체하지 않는다.

`graph-0.4`는 합성 `ProductionObservation` 하나를 추가한다. 필수 필드는 `production_work_id`→ProductionWork, `observed_at`, `produced_quantity`, `good_quantity`, `defective_quantity`, `unit=piece`와 기존 공통id/type/origin/evidence다. 세 수량은0 이상 int이며bool·float·문자열은 거부한다. 총생산=정상+불량, 작업과 단위 일치, 시간순 세 누계 비감소를 검사한다. 같은 작업의 같은 UTC 관측 순간은 값이 같아도 중복 오류다. 시작 전·작업 상태 시각 이후·snapshot 이후 실적, 작업과 같은 시점의 정상/완료 수량 불일치는 거부한다. 과거 실적 정상 누계가 나중 작업 완료 누계보다 큰 경우도 감소 모순으로 거부한다. 준비/대기 작업의 양수 생산 누계도 모순이다. 회사·구성은 작업·계약 참조 사슬에서 확인한다.

첫 조회의 지원 범위는 **한 작업의 정상 목표 전량=회차 약속 전량=해당 작업의 회차 배정 합계**다. 그 작업을 다른 회차로 나누거나 대상 회차에 다른 작업/재고를 섞지 않는다. 무관한 다른 정상 작업이 그래프에 있다는 이유만으로는 거부하지 않는다. snapshot의 `allocations_complete`, `source_allocations_complete`, `allocations_disjoint`, `production_state_complete`, `material_disjoint` 다섯 확인 키가true여야 한다. CQ02의 재고·운송 사건 확인 키를 임의로true로 만들거나 그대로 강제하지 않는다. 확인 주장은 원문을 사람이 검토하는 자료이며 코드가 문장 진실성을 입증하지 않는다.

요청as_of=snapshot.as_of=work.observed_at=최신 실적observed_at를 같은 UTC 순간으로 맞춘다. 기존 저장상 가능한 오래된 작업·분할 배정은 이 조회에서 `needs_confirmation`으로 구분한다. 지원 밖snapshot 시각·현황 부족·배정 범위·시작 미확인·실적 부족은 예측null과 `unknowns`를 반환한다.10시 실적이 없다고 작업의10시 완료 수량을 실적 대신 넣거나09시 실적과 조합하지 않는다. 전체 그래프/실적 검증 뒤 같은 작업의 최신 두 관측을 선택하므로 오래된 모순 행을 숨길 수 없다.

공통 Python의 `calculate_completion(previous,current,target_quantity)`는 순수 구간 산술 함수이며 승인 버전·회차·근거 검증의 진입점이 아니다. 완료 예상 조회는 이 함수 앞에서 관계·범위를 검사한다. 정상 증가/경과 시간으로 속도를 구하고 `(목표−최신 정상)/정상 속도`를 계산한다. 불량을 이미 뺀 정상 수량에 구간 불량률을 다시 차감하지 않는다. `Fraction`으로 중간 비율을 보존하고 최종 잔여 정수 초만 올림한다. float로 반환하는 속도/비율은 표시용이며 잔여 시간에 재사용하지 않는다.

| 결과 | 의미 |
| --- | --- |
| `status=forecast` | 최근 두 실적·양의 정상 속도로 조건부 생산 완료 예상 가능 |
| `status=target_observed` | 최신 관측에서 목표 정상 수량 확보. `remaining_quantity/remaining_seconds=0`, `target_observed_at`=최신 관측, `estimated_completion_at/actual_completion_at=null`. 최초 실제 완료 사건은 미확인 |
| `status=needs_confirmation` | 누락·지원 밖·속도0. 필요한 확인을unknowns로 반환하며 계산하지 못한 값은null. 최신 실적1개가 유효하면 정상/잔여 수량은 알 수 있어도 속도/완료 예상은null |

최근 총생산 증가0이면 구간 불량 비율null이며 정상 속도0이면 목표 미달 예상 시각null이다. 증가분 전량 불량이면 구간 비율1·정상 속도0이다. 목표 초과 정상 수량은 현재 작업의완료≤목표 계약과 충돌하므로 수량을 잘라 맞추지 않는다. 목표 도달의 실적은 하나여도 잔여0을 확인할 수 있다.

결과 `data`는 작업·회차·snapshot·구성·단위·목표, 별도 `work_completed_quantity/work_observed_at`, 대응하는 `latest_good_quantity/observation_as_of`, `observations/selected_observation_ids`, 구간 초/증분·속도/비율·정확 속도 `good_rate_fraction`, 잔여 수량/초·예상 시각, `assumptions/unknowns/warnings/evidence`를 담는다. `forecast_rule_version=production-0.1`은 공통 envelope의 `temporal-0.1`과 별개다. `queried_at`은 조회 실행 시각이며 예측 기준 관측을 현재 시각으로 옮기지 않는다. `actual_completion_at=null`, `synthetic=true`, `fulfillment_assessed=false`를 유지한다.

최근 추세 계속·정상 완료분 사용 가능은 `kind=calculation_condition`인 가정이며 승인된 미래 실적이 아니다. 근거는 실제 선택 관측·작업·배정·snapshot·계약/구성 사슬을 연결한다. CQ02의 최종11개 인용 정책을 복사하지 않는다. 조회는 그래프·초안·승인 버전·작업 상태·입고·완료 사건을 변경하지 않는다. CQ02 `fulfillment_status`의 부분 완료 미확인 제한도 유지한다.

| 대표 자료·실행 | 내용 |
| --- | --- |
| [원문](../../data/order_fulfillment/sources/b_demo_production_01.md) | 개발용 누계·단일 작업 전량 배정·계산 가정. SHA-256 `6b18f76917db81837a4a06891d9a8331b412c19d1d62f46d0e3a681bc442c810` 고정 |
| [후보](../../data/order_fulfillment/b_production_fixture.json) | 신규12노드: Source1·Evidence6·snapshot/작업/배정·실적2. 계산 가정 근거 별도 연결 |
| [독립 정답](../../data/order_fulfillment/b_production_expected.json) | 정상90대/시간·구간 불량10%·잔여400대·16000초·2026-10-27T14:26:40+09:00 |
| [대표 그래프](../../data/order_fulfillment/b_production_graph.jsonld) | 참조/근거를 포함한26노드 별도 JSON-LD. CQ02 시나리오와 합치지 않음 |
| [예제](../../examples/order_fulfillment/b_production.py) | `python -m examples.order_fulfillment.b_production`: 새 PostgreSQL 스키마·초안/개발용 검토 대역·반영/버전 조회·14개 검사. LLM 없음 |
| [모델 검증](../../tests/order_fulfillment/test_production_validation.py)·[계산/조회 검사](../../tests/order_fulfillment/test_production.py)·[DB 검사](../../tests/order_fulfillment/test_production_service.py) | 숫자·누계·시간·범위·정밀도·근거·버전·읽기/승인 경계의 실행 검사 |

새 자료 정책은 개발용 총생산/정상/불량 누계와 구간 파생 비율에 한정한다. 일반 품질·검사 결과·고장 원인·실제 OLIMEX 품질·가동 시간은 계속 제외한다. 기존 원문·해시·CQ01/02 정답은 보존하며 새 내용은 새SourceDocument/Evidence 버전으로 관리한다. Gateway와provider에 읽기 도구를 등록했지만 기존 역할의 허용 도구 목록은 그대로다. 전용CQ03 역할의 연결·인용/평가는 아래 Task09 계약을 따른다. 실제 모델 검증의 범위·실패 기록은 PROJECT_STATUS를 따른다. 일반CQ03(구성 비교), 장착 이력 A/B와 S1/S2 의미를 바꾸지 않는다.

#### B-CQ06 승인 자료 변경 전후 비교

`Service.compare_response_plan(actor, delivery_round_id, before_as_of, after_as_of, *, before_version=None, after_version=None)`는 명시 승인 입력 두 개를 한 공통 read/감사 트랜잭션에서 읽는다. Gateway는회차·전후버전·전후as_of의5인수가필수이며추가그래프/예측JSON/actor를받지않는다. 누락/bool/음수/역순버전은INVALID_VERSION,없는버전VERSION_NOT_FOUND,누락offset/역순시점/같은버전다른시점INVALID_TIME이다. 없는회차NOT_FOUND,빈회차INVALID_INPUT,대상/약속/계획불일치INCOMPARABLE_INPUTS,모순그래프VALIDATION_FAILED,무권한FORBIDDEN을유지한다. 누락 비버전 인수나 추가Gateway인수는INVALID_TOOL_ARGUMENTS다.

0.8.0의 파생 comparison-0.1이며 graph-0.5 및 기존 temporal/production/delivery/response 규칙은 유지한다. 현재 동일한 CQ05 response_plan과 CQ03 production_completion을 전후 각각재사용한다. 과거입력을현재규칙으로계산하며과거저장예측/LLM응답복원이아니다. 같은회사/고객/계약/구성/단위/약속수량/기한/목적지/작업/배정/출하계획을검사한다. 버전동일이면UTC시점과전체입력그래프도동일해야한다. snapshot ID는 버전 번호와다르며암묵최신선택을하지않는다.

| 반환 | 의미 |
| --- | --- |
| before/after | data_version·as_of·전체response_plan·production결과. 양쪽독립보존 |
| quantity_deltas | on_time_quantity,shortfall_quantity 및 CQ05 dispatch_request의8개수량. 후−전,단위piece |
| time_deltas | remaining_seconds,estimated_completion_seconds,estimated_full_arrival_seconds. 후−전,단위초 |
| production_deltas | 정상/총생산속도·최근불량비율·잔여/확보정상수량 차이 |
| changed_inputs | 관련ID/필드의전후값·존재여부·단위·전후Evidence. 새관측/원본/근거포함 |
| response_changes | 상태·고객회신·신청내용·후보·확인사항의변화. 원래문안양쪽보존 |
| comparison_status | compared / partially_unknown. 미확인null은차이도null이며변화0아님 |
| evidence/evidence_by_side | 선택한근거의정확합집합과전후분리. 고정개수로가정하지않음 |
| integrity_evidence_by_side | 전체도착/생산조회무결성근거. envelope는중첩결과의근거도포함 |
| draft_only/review_required/execution_performed | true/true/false. 승인·발송·신청등록·생산/출고실행없음 |

새11시입력은snapshot/work시점·완료누계와새관측을맞춰공통draft→검증→별도사람review→apply로반영한다. 기존09/10시관측과모든이전Source/Evidence를덮어쓰지않는다. 새원본SHA/근거ID를추가한다. 같은11시대안들을연속관측으로쌓지않으며삭제연산도추가하지않았다.

새 읽기 묶음B_COMPARISON_TOOLS=('compare_response_plan','lookup_many')와provider명세를제공하지만기존CommonHost역할의도구허용목록은확대하지않았다. CQ06 Agent등록/지시/평가는 아래 Task09 계약에 연결했다. 실제 불량 증가 대표가 설명 길이에 실패해 Task09는 미완료다. 혼합의CQ05재출고제외는보존하지만기존CQ03의전량단일작업제약으로생산부분은미확인이며전체비교는부분확인이다. 자동재계산/캐시/알림·미승인가정미리보기·운영변경비교·실제실행은범위밖이다.

[새 원본](../../data/order_fulfillment/sources/b_demo_recalculation_01.md), [변경 후보](../../data/order_fulfillment/b_recalculation_fixture.json), [독립 정답](../../data/order_fulfillment/b_recalculation_expected.json), [순수 비교](../../ontologics/order_fulfillment/response_comparison.py), [자료 검사](../../tests/order_fulfillment/test_recalculation_demo.py), [순수 검사](../../tests/order_fulfillment/test_response_comparison.py), [DB 검사](../../tests/order_fulfillment/test_response_comparison_service.py), [무료 실행 예제](../../examples/order_fulfillment/b_recalculation.py)를제공한다. 예제의사람검토는개발용대역이며실제운영승인이아니다.

#### B-CQ05 대응 초안 조회

`Service.response_plan(actor, delivery_round_id, as_of, *, version=None)`는 명시 승인 버전의 CQ04 `delivery_forecast`를 정확히 한 번 재사용한다. Gateway 입력은 회차·offset 포함 시각·0 이상 정수 버전 세 개가 모두 필수이며 추가 forecast/신원 입력은 거부한다. 공통 read 권한·감사·envelope와 CQ04의 버전/시간/대상/검증 오류를 유지한다. 내부 기본값None은 최신 자동 선택이 아니라 `INVALID_VERSION`이다.

공통 계약0.7.0에 파생규칙 `response-0.1`을 추가하며 새 그래프 타입·DB 저장 상태는 추가하지 않는다. graph-0.5와 temporal-0.1/production-0.1/delivery-0.1은 그대로다.

| 반환 | 의미 |
| --- | --- |
| response_state | met / shortfall / needs_confirmation / historical_review. 과거 due_at은 이행 검토가 우선 |
| forecast | CQ04 전체 결과를 그대로 보존: 수치·시각·가정·미확인·원문 근거 |
| customer_reply | 조건부 수치와 담당자 확인 사항을 넣은 고정 한국어 검토 문안. 미발송·미등록·일정 미합의 |
| dispatch_request | 회사/고객/계약/회차/구성/목적지/단위/기준시점/기한/자료snapshot·분할/일괄/준비/운송 조건 |
| dispatch_request 수량 | delivered_quantity, in_transit_quantity는 사실 물량. new_dispatch_quantity는 실제 미출고 배정만. on_time_new_dispatch_quantity / later_new_dispatch_quantity, currently_available_quantity / future_quantity, unallocated_quantity를 구분 |
| items / planning_status | 미출고 배정의 출처·수량·현재 확보·기한 내/후속·ETA·근거. draft / not_assessed / historical_only. 미평가의 빈 배열은 물량이 없다는 확인이 아님 |
| actions / confirmations | 검토 후보·고객 협의·운송 추적·공급/입력 확인·과거 검토. 확인 안 된 carrier/vehicle/detailed_address/contact/request_number는null |
| evidence | 회차/snapshot/조건/배정/배정별 계산/가정에서 실제 선택한 합집합. 기본8·혼합13개. forecast와envelope의 전체 무결성 근거12/17개와 구분 |
| draft_only / review_required / execution_performed | true / true / false. 회신·신청을 등록하거나 승인·발송·출하·계약 변경하지 않음 |

기본 기한내960/부족40/전량15:26:40에서 새검토1000=현재600+미래400이다. 혼합은 도착100/운송150을 제외해 새검토750=현재450+미래300이며 기한15시는 새750, 기한14시는 새720/후속30이다. 기존250+새720=전체970을 구분한다. 일괄 조건을 분할 제안으로 바꾸지 않는다. 미배정100은 새 공급이나전량ETA로 채우지 않는다.

전체 계산 미확인 시 집계 출고 수량은 모두null이고 입력 확인을 요청한다. 확인된 빈 배정은 새검토0/미배정1000이다. 과거 기한은 숫자를 보존한 forecast와 과거 이행 검토만 반환하며 당시 부족을 현재 재출고량으로 쓰지 않는다. 조회는 그래프·drafts·review_events·자료 버전을 바꾸지 않는다(공통 실행 감사 기록은 남음).

[순수 검사](../../tests/order_fulfillment/test_response_plan.py), [Service 검사](../../tests/order_fulfillment/test_response_plan_service.py), [독립 기대결과](../../data/order_fulfillment/b_response_expected.json), [무료 예제](../../examples/order_fulfillment/b_response.py)를 제공한다. 원본은 기존 CQ04 공유 기본/혼합 자료를 그대로 재사용한다. 테스트 내부 가정 변형은 새로운 승인 사실을 뜻하지 않는다. CQ05 Task09 읽기 역할은 아래 계약을 따르며 형식 보완 후 실제 기본·혼합 대표를 각 1번 통과했다. 기존 역할별 도구 목록을 자동 확대하지 않는다.

#### B-CQ04 조건부 고객 도착 예상 조회

`Service.delivery_forecast(actor, delivery_round_id, as_of, *, version=None)`는 기존 읽기 권한·감사·공통 envelope를 재사용한다. 공개 입력 `delivery_round_id`, `as_of`, `version`은 모두 필수다. 누락/불리언/음수 버전은 `INVALID_VERSION`, 없는 버전은 `VERSION_NOT_FOUND`, 없는/다른 타입 회차는 `NOT_FOUND`, 빈 ID는 `INVALID_INPUT`, offset 없는 시각은 `INVALID_TIME`, 모순 그래프/계산 범위 초과는 `VALIDATION_FAILED`다. 실행 시각 `queried_at`으로 관측을 옮기거나 최신 버전을 자동 선택하지 않는다.

v0.6.0/`graph-0.5`는 합성 `DeliveryCondition`을 추가한다. 필수 필드는 공통id/type/origin/evidence와 snapshot_id→FulfillmentSnapshot, delivery_round_id→DeliveryRound, preparation_seconds, transport_seconds, dispatch_mode다. 초는 0 이상 정수이며 불리언/소수/문자열/음수는 거부한다. 방식은 `split_available` 또는 `all_at_once`를 명시한다. 동일snapshot/회차 조건은 하나여야 하고 회차는snapshot 목록에 있어야 한다. 조건 전체 누락은 조회의 확인 필요이며, 기록된 조건의 필드/근거 누락은 저장 검증 실패다. 회사·구성·단위·목적지는 기존 계약/배정 사슬에서 확인한다.

조회는 동일UTC의snapshot/as_of, 아홉 확인 범위, 모든 원천/타회차 배정 상한과 그래프 검증을 먼저 확인한다. 최대 한 생산 작업의 목표 전량을 해당 회차에 배정할 수 있고 작업 목표가 회차 전량보다 작아도 된다. 같은 작업의 여러 배정은 ID순 누적 경계로 현재 완료량과 미래 생산량을 분할한다. 원천별 물량은 실제 도착·실제 운송 중·창고·생산 중 하나에서만 집계한다. 출하 전 재고를 다시 더하지 않는다. 물리적 비중복/미기록 사건 부재는 근거 있는 확인 범위 선언에 의존하며 코드가 현실을 자동 증명하는 것은 아니다.

계산은 `production.calculate_completion`의 정확한 최근 정상 속도를 재사용한다. 미래 준비 경과 초는 올림, 기한에서 준비/운송 시간을 뺀 가용 경과 초와 대수는 내림한다. `delivery-0.1`은 별도 예측 규칙이며 기존`temporal-0.1`·`production-0.1`은 유지한다. 기한과 동시 도착까지 포함하고 미출고 현재 물량을 과거에 출고한 것으로 소급하지 않는다. 각 분할분의 준비/운송 시간은 일정하고 출고 빈도·용량·휴무 제약이 없다는 조건을 표시한다. 일괄 출고는 회차 전량 미출고/전량 배정 범위에서 전량이 도착 기한 이하면 전량, 아니면0대다. 이미 출하된 혼합 일괄 출고는 확인 필요다.

주요 반환: `deadline_status`/`status`=`met|shortfall|needs_confirmation`, `on_time_quantity`, `shortfall_quantity`, `estimated_full_arrival_at`, `allocated_quantity`, `unallocated_quantity`, `breakdown`, `condition`, `assumptions`, `evidence`, `unknowns`, `precision_policy`, `forecast_rule_version`, `assessment_scope=conditional_arrival_forecast`, `synthetic=true`와 명시 회차/시점/약속이다. 각breakdown은 배정·원천·상태·전체/기한 내 수량·현재 확인량·실제/예상 도착·선택 관측/속도·근거를 제공한다. `actual_arrival_assessed=true`는 **과거 기한을 완전한 실제 사건 이력으로 평가했음**만 뜻하며 미래 도착 보증이 아니다. 실제 도착의 시각을 해당 행의actual_arrival_at에 보존하고 전량 시각은 행별 실제/예상 도착 중 최댓값이다. 새 조건의 도구 권한을 기존 Agent 역할에 자동 부여하지 않았다. CQ04 전용 읽기 역할·설명·인용 검사는 아래 Task09 Agent 계약을 따른다.

미달 작업의 관측 부족/오래됨/속도0·확인 범위 부족·경과한ETA/ETA없음은 전체 정확 수량/부족/전량시각null과 확인 이유를 반환한다. 확인된 부분 행은 보일 수 있지만 전체합계로 확정하지 않는다. 현재 정상 목표 달성은 기준 시점부터 출고 가능하며 과거 실제 완료를 생성하지 않는다. 배정없음이 완전히 확인되면 현재 계획0/약속 전량 부족이다. 배정900/미배정100이 완전히 확인되고900대 기한 내라면900/100, 전량 시각null이다. 과거 기한은 완전한 실제 사건 이력으로 도착<=기한만 센다. 기한 후 실제 도착도 기한 내로 세지 않는다.

| 대표 자료/검사 | 역할 |
| --- | --- |
| [기본 원문](../../data/order_fulfillment/sources/b_demo_delivery_01.md), [혼합 원문](../../data/order_fulfillment/sources/b_demo_delivery_mixed_01.md) | 별도의 가상 생산/출고 조건과 혼합 물량. 각각 고정SHA와 새Source/Evidence ID |
| [기본 후보](../../data/order_fulfillment/b_delivery_fixture.json), [혼합 후보](../../data/order_fulfillment/b_delivery_mixed_fixture.json) | 14개/27개 신규 후보. 기존CQ02/CQ03 운영 사례와 합치지 않음 |
| [기본 독립 정답](../../data/order_fulfillment/b_delivery_expected.json), [혼합 독립 정답](../../data/order_fulfillment/b_delivery_mixed_expected.json) | 기본960/40/15:26:40, 혼합1,000/0/14:20, 혼합 기한14시970/30 |
| [기본 그래프](../../data/order_fulfillment/b_delivery_graph.jsonld), [혼합 그래프](../../data/order_fulfillment/b_delivery_mixed_graph.jsonld) | 공통 참조/근거 포함28/41노드 |
| [예제](../../examples/order_fulfillment/b_delivery.py) | `python -m examples.order_fulfillment.b_delivery` 및 `--mixed`, 일회용PostgreSQL·초안/개발용 사람 검토/반영·명시버전 조회. 각각13개 확인, LLM없음 |
| [계산 검사](../../tests/order_fulfillment/test_delivery_forecast.py), [자료 검사](../../tests/order_fulfillment/test_delivery_validation.py), [DB 검사](../../tests/order_fulfillment/test_delivery_forecast_service.py) | 독립 정답·경계·미확인·타입/시각/중복·권한/승인 버전·읽기 불변 |

기존CQ03의 전량=회차 범위와fulfillment_assessed=false, CQ02의 부분 생산 완료 미지원, Shipment의 재고 배정 실제 전량 출하 의미는 바꾸지 않는다. 현재 예측은Shipment·도착 사실·작업 상태·약속·승인 버전을 쓰지 않는다. 부품 입출고·장착 이력·실제 가동/검사와 혼동하지 않는다. 복수 작업 일정·부분 작업 목표 배정·차량/달력·실제 부분 출고/반품·운영 인증/승인·웹 연결은 별도다.

#### 동일 버전의 묶음 조회

`lookup_many(node_ids, kind, version)`는 확정된 한 버전에서 같은 타입의 노드를 한 번에 읽는다. `node_ids`는 중복 없는 비어 있지 않은 문자열 1~16개, `kind`는 공통 SCHEMA의 타입, `version`은 명시한 0 이상 정수다. 불리언 버전은 허용하지 않는다. 결과는 요청 ID 순서의 전체 노드 배열이다. 하나라도 없거나 타입이 다르면 `NOT_FOUND`와 data=null을 반환한다. 잘못된 목록/타입은 `INVALID_INPUT`, 누락/잘못된 버전은 `INVALID_VERSION`, 없는 버전은 `VERSION_NOT_FOUND`다. 일부만 성공한 결과를 돌려주지 않는다.

기존 Service의 read 권한·확정 snapshot·감사·공통 envelope를 재사용한다. 미승인 초안을 읽거나 그래프를 쓰지 않으며 요청 신원은 Gateway가 고정한다. 기존 역할에 권한을 자동 추가하지 않는다. 모델 스키마의 상한과 별개로 Service가 목록·버전·타입을 다시 검증한다. [회귀 검사](../../tests/order_fulfillment/test_lookup_many.py)에서 권한·스푸핑·과거 버전·초안 비공개·원자적 실패를 확인한다.

#### B-CQ04 Agent 연결과 평가

[examples/order_fulfillment/b_delivery_agent.py](../../examples/order_fulfillment/b_delivery_agent.py)는 기존 CommonHost에 `b_delivery` 읽기 역할을 등록한다. `B_DELIVERY_TOOLS=(delivery_forecast,lookup_many)`이며 요청 사용자 read 권한으로만 실행한다. 회차·as_of·승인 자료 버전을 고정하고 개발 시드/사람 역할의 검토·반영은 모델 호출 전에 끝낸다. 모델은 수량을 다시 계산하거나 초안·승인·출고·도착·약속을 변경하지 않는다.

최대 네 모델 응답으로 진행한다: ①정확한 조건의 delivery_forecast 한 번 ②필요한 Evidence 전체 ID 배열로 lookup_many 한 번 ③그 근거의 SourceDocument 전체 ID 배열로 lookup_many 한 번 ④최종 JSON. 단계마다 허용 도구/정확한 버전·ID 집합을 제한한다. 누락·추가·중복·다른 타입·조기 최종 답변은 실행 전에 거부한다. 동적 strict 스키마의 배열 길이·enum과 별도로 실행 전 집합 검사를 한다. `parallel_tool_calls=false`, 도구 단계는 `tool_choice=required`, 최종 단계는 `none`이다. required만으로 전체 근거 조회를 보증하지 않기 때문에 묶음 계약을 사용한다. 공통 provider 기본 auto와 runtime 기본 도구 수4/설정 상한8은 유지하며 CQ04는 응답당 도구 수1이다.

최종 인용은 계약 회차·snapshot·조건·배정·상태별 breakdown·가정의 근거 합집합이다. 기본8개/혼합13개는 해당 결과에서 추출한 값이며 공통 결과의 전체 무결성 문맥12개/17개와 구별한다. 모델은 선택 근거와 원문 정보2개를 조회하고, 호스트는 전체 사슬과 로컬 원문 해시를 검사한다. SourceDocument 조회를 모델의 원문 전체 열람이라고 설명하지 않는다.

최종25필드 중 수치·시각·상태·조건·버전·가정·인용을 공통 결과 및 독립 정답과 대조한다. bool을 숫자로 인정하거나 null을0으로 대체하지 않는다. 원래 답을 고치거나 인용을 잘라 통과시키지 않는다. summary/next_action은 원래 한국어 문장에 대해 별도 의미 검토를 수행한다. 자동 검사의 성공은 자유문 전체 품질이나 반복 신뢰도 보증이 아니다.

무료 실행: `python -m examples.order_fulfillment.b_host_agent --mode delivery --case normal --output artifacts/local/b_cq04_host_new.json`. 사례는 normal/mixed/mixed_due14/all_at_once/missing_condition/missing_latest/zero_rate/coverage_missing/elapsed_eta다. 공용 CLI는 대역 전용이다. 실제 대표 normal·mixed 검증의 시도별 결과·비용·남은 범위는 PROJECT_STATUS에 기록한다. 각 의도적 시도는 최대4 API 요청·출력1,024토큰/회·자동 재시도0, 사용자 승인 총5시도 상한이며 성공하면 멈춘다. Task10에서 전체448개·skip0·93.008초·설계36개 대조·예제20개와 실제 기본/혼합 원본·코드 해시 일치를 확인해 고정 가상 자료의 개발 범위를 수락했다(추가API0회). 원래 요약은 기한 문맥과 구조화된 전량ETA를 함께 읽어야 한다. 실모델 예외/반복·B웹/Supervisor/운영 인증·실제 제조/납품은 별도다. CQ05 Task08 공통 대응 도구와Task09 읽기 역할/평가 계약은 별도로 관리하며 현재 Task10의 새 전체 회귀·설계·예제·원본 보존 대조까지 확인해 고정 가상 자료의 개발 범위를 수락했다.

#### B-CQ03 Agent 연결과 평가

[examples/order_fulfillment/b_production_agent.py](../../examples/order_fulfillment/b_production_agent.py)는 기존 CommonHost·ModelRuntime·Service·OpenAIProvider의 고정 LLM workflow다. `b_production`을 신뢰된 시작 코드에 등록하고 `B_PRODUCTION_TOOLS=(production_completion,lookup)`만 허용한다. 입력은 작업/회차/as_of/data_version이며 호스트의 요청 사용자read 권한으로 실행한다. fixture의개발시드·검토·반영은 모델 시작 전에 끝나며 실제 업무 승인이나 Agent초안 자동승인이 아니다.

최대4모델응답: ①정확조건production_completion ②반환snapshot/work/allocations/선택observations의 Evidence전부를한번씩묶음lookup ③그Evidence의중복제거SourceDocument전부lookup ④최종JSON. 첫 정상의필요근거6개는결과이며하드코딩하지않는다. 공통조회의전체무결성evidence11개와구분하고CQ02의11개인용정책을복사하지않는다. 호스트는전체근거/문서사슬과로컬원본SHA-256을검사한다. 모델의SourceDocument조회는메타데이터이며원문파일전체열람이아니다.

최종27키: status,production_work_id,delivery_round_id,snapshot_id,forecast_rule_version,as_of,observation_as_of,unit,target_quantity,latest_good_quantity,remaining_quantity,good_rate_per_hour,produced_rate_per_hour,recent_defect_ratio,remaining_seconds,estimated_completion_at,target_observed_at,actual_completion_at,selected_observation_ids,unknowns,synthetic,fulfillment_assessed,data_version,assumption_codes,evidence,summary,next_action. 공통결과의정확수치/null/버전/가정/관측과인용집합을검사하며별도독립golden을사용한다. runtime.ok는평가통과와다르다. summary/next_action자유문전체의사실성은별도검토하고원래답을후처리로교체하지않는다.

CONFIG는max_steps4/max_attempts1/max_tool_calls8(한모델응답당)/context65536bytes/tool_result32768bytes다. CQ03 BoundedOpenAIProvider는실패시도포함최대4HTTP요청·출력1024/요청을강제한다. CQ03만parallel_tool_calls=true로묶음선택을요청하며도구는공통런타임이순서대로실행한다. 동적좁은도구스키마는strict=true·현재ID/version enum·모든필드required·additionalProperties=false다. 허용밖/중복/누락/다른버전의호출묶음은실행전에전부거부한다. 현재단계목록/개수는API입력마지막지침으로전달하되런타임이력·원래call_id재생hash는유지한다. 일반이종도구스키마와다른역할의기본값은보존한다. strict스키마만으로묶음완전성·최종답정확성을보증하지않는다.

무료실행: `python -m examples.order_fulfillment.b_host_agent --mode production --case normal --output artifacts/local/b_cq03_host_new.json`. 사례는normal/missing_latest/zero_rate/target_observed이며새파일·새개발스키마를사용한다. 공개CLI는계속대역전용이다. 실제모델정상1건은1차불필요근거추가·2차같은목록중복실패를보완한3차에서19/19·별도의미검토통과다. 각실행최대4요청·출력1024/회·자동재시도0,사용자허용총5시도중3번만사용했다. 전체361개회귀·skip0이며실행/비용/버전별원본은PROJECT_STATUS를따른다. Task10은 전체361개·24개 설계 대조·예제10개와 위 실제 정상1건의 코드/원본 해시를 확인해 개발 범위를 수락했다(추가API0회). 실제모델예외/반복·웹/Supervisor/실제인증·운영승인/납품가능성판정은별도다.

#### B-CQ02 Agent 연결과 평가

[b_fulfillment_agent.py](../../examples/order_fulfillment/b_fulfillment_agent.py)는 기존 ModelRuntime/OpenAIProvider/AgentGateway를 재사용한다. 고정된 LLM 업무 흐름이며 새 프레임워크·라우터·서버는 없다. 입력은 질문·delivery_round_id·as_of·data_version이며 정답 수량이나 기대 답 파일을 모델에 전달하지 않는다. 전용 `fulfillment-agent` 신원은 read만 가지며 B_FULFILLMENT_TOOLS=(fulfillment_status,lookup)로 런타임의 도구 목록을 제한한다. 기존 B_INTAKE_TOOLS/B_PROMISE_TOOLS/PRODUCT_TOOLS/QUALITY_TOOLS는 바꾸지 않는다.

정상 흐름은 현황 조회→snapshot 근거 하나 조회→그 원본 조회→최종 답이다. snapshot이 없으면 근거 조회를 생략하고 미확인을 답한다. 최종 JSON의 정확한 키는 status, delivery_round_id, data_version, as_of, unit, states, allocated_quantity, remaining_quantity, unallocated_quantity, fulfillment_assessed, synthetic, evidence, unknowns, summary, next_action이다. 수량·시각·상태·unknowns는 도구 결과와 같아야 한다. 수량은 int 또는 미확인의 null, synthetic=true·fulfillment_assessed=false다.

Task08 현황 도구의 전체 evidence는 원천 상한·다른 회차 배정·계약/구성 등 검증 문맥을 보존한다. Task09 최종 답의 evidence는 이 전체 목록과 구분한다. snapshot이 있으면 `snapshot.evidence ∪ {item.evidence[0] for item in items}`와 정확히 같은 중복 없는 집합이어야 한다. 목록 순서는 무관하며 추가·누락·유효한 다른 근거로의 교체도 실패다. snapshot이 없는 미확인은 조회 envelope의 전체 evidence를 유지한다. 고정 정상 예제의11개는 결과 값이며 개수나 ID를 하드코딩하지 않는다.

역할 전용 연결은 마지막 모델 요청에만 실제 도구 결과에서 추린 정확한 인용 목록을 system 데이터로 추가한다. 그 전 history prefix와 provider call ID는 보존하고 마지막 단계의 도구 호출은 거부한다. 추가 모델 요청이나 자동 인용 치환은 없다. 모델의 원래 최종 text·기대 목록·수락 여부는 `final_submission`에 남기며 목록이 다르면 런타임이 실패한다. 독립 평가기에도 `exact_answer_evidence`를 추가해 같은 명세를 별도로 대조한다. 인용 정책은 `snapshot-and-item-first-exact-v1`, 연결 guard는 `cq02-provenance-v2`로 보고서에 기록한다. 공통 Service/runtime/provider·조회 응답·그래프 계약은 바꾸지 않는다.

모델이 개별 Evidence 전체를 모두 반복 조회하지 않도록 전체 Evidence→SourceDocument 사슬은 호스트가 같은 버전에서 로컬 검증하고 보고서에 보존한다. 실제 모델은 별도로 대표 snapshot 근거와 해당 SourceDocument를 읽어야 한다. 이를 도구 호출 기록으로 검사하며, SourceDocument 노드 조회를 원문 전체 열람으로 계산하지 않는다.

기본 실행은 scripted 대역이다. normal/missing/unknown_time/coverage_missing 사례는 실제 DB에서 재현하며, 실제 OpenAI 실행은 normal 하나만 지원한다. 유료 실행 제한은 모델 snapshot `gpt-5.4-mini-2026-03-17`, 최대 요청4회, 자동 재시도 없음, 한 응답당 도구1개, 출력1024토큰, reasoning none이다. 이전 출력 경로가 존재하면 키/DB/API 실행 전에 중단한다. 비용 추정은 실행 전 입력 크기와 공식 요금으로 알리고, 실행 후 각 요청 usage를 합산한다. 검증에서 거부한 응답도 사용량을 보존하며 사용량이 누락된 요청이 있으면 usage_incomplete=true·비용 null이다. 토큰 기반 표준 요금 추정이며 청구서 확인은 아니다.

평가는 독립 정답과 같은 버전의 도구 결과, 실제 호출 인자/근거 조회, null·정수 형식, 다른 회차 제외, 확정 버전 불변과 권한을 검사한다. 자유문 summary/next_action의 의미는 별도 검토 대상이며 모델을 평가자로 다시 호출하지 않는다. 미확인 자료를 0으로, 잔여 약속을 추가 생산 필요량으로, 현황을 기한 내 납품 보장으로 설명하지 않아야 한다. [대역·적대적 검사](../../tests/order_fulfillment/test_b_fulfillment_agent.py)와 [provider 비용 회귀](../../tests/test_openai_provider.py)를 따른다. 범용 해석·실제 사람 승인 통합·반복 신뢰도·생산 예측은 이 연결의 성공 범위가 아니다.

## 추가·변경 7

> 원문 위치: 공통 도구 계약 0.7.0 / Agent에 공개하는 호출 (361–361행)

| tool | arguments | 결과 |
| --- | --- | --- |
| lookup_many | node_ids(1~16개), kind, version | 한 확정 버전·동일 타입의 전체 목록 조회; 누락·중복·일부 성공 불허 |

## 추가·변경 8

> 원문 위치: 공통 도구 계약 0.7.0 / Agent에 공개하는 호출 (363–367행)

| tool | arguments | 결과 |
| --- | --- | --- |
| delivery_round | delivery_round_id, version(필수) | 같은 버전의 회차→항목→계약/구성/개정/모델과 근거. 납품 약속 조회만 수행 |
| fulfillment_status | delivery_round_id, as_of, version(모두 필수) | 정확한 현황 시점·같은 버전의 6상태 물량·배정/잔여/미배정·근거·미확인. 예측 없음 |
| production_completion | production_work_id, delivery_round_id, as_of, version(모두 필수) | 단일 전량 배정 작업의 최근 정상 속도·잔여 수량/시간·조건부 생산 완료 예상·관측/가정/근거·미확인. 도착 판단/쓰기 없음 |
| response_plan | delivery_round_id, as_of, version(모두 필수) | CQ04 기반 고객 회신·미출고 출고/운송 검토 초안, 파생response-0.1. 미확인/과거 이행 구분; 발송·등록·승인 없음 |
| delivery_forecast | delivery_round_id, as_of, version(모두 필수) | 출고 조건을 적용한 기한 내 도착 예상·부족·전량 도착 시각·배정별 근거/가정/미확인. 실제 출하/도착/승인 쓰기 없음 |

## 추가·변경 9

> 원문 위치: 공통 도구 계약 0.7.0 / Agent에 공개하는 호출 (381–413행)

##### B-CQ01 계약 접수와 약속 조회

호스트용 `contracts.extract_contract(text, configurations, *, evidence_ids)`는 한 개의 고정 형식 Markdown 계약 표를 읽는다. configurations에는 같은 승인 스냅샷의 모델·개정·구성 노드를 전달한다. 정확한 모델/개정/variant 하나에 연결하며 유사 이름으로 대체하지 않는다. evidence_ids는 호스트가 준비한 계약/항목/회차 Evidence ID다. 파일 시스템·DB 쓰기·승인을 수행하지 않으며 Agent 공개 도구도 아니다.

결과는 `status/nodes/issues`다. `ready`만 신규 3개 노드 후보를 제공한다. `needs_confirmation`(필수 정보 누락·제품/기한 모호·원문 충돌·첫 범위 밖)과 `invalid`(수량/단위/표 형식 오류 등)는 `nodes=[]`이고 각 issue에 field/code/message/locator를 제공한다. 후보가 준비돼도 공통 그래프 검증·초안·사람 검토·반영이 필요하다. 첫 표 형식은 항목 1·회차 1·수량 전량을 지원한다. 공통 그래프 검증은 여러 회차의 합계 상한도 검사하지만 이 파서가 다회차 계약을 지원한다는 뜻은 아니다.

`delivery_round`는 read 권한과 명시적인 nonnegative integer 버전을 요구한다(불리언 제외). 없는 버전은 `VERSION_NOT_FOUND`, 없는/다른 타입의 회차 ID는 `NOT_FOUND`, 잘못된 ID 형식은 `INVALID_INPUT`, 누락/잘못된 버전은 `INVALID_VERSION`이다. 다른 회차·최신 버전으로 자동 대체하지 않는다. 실패 응답의 data_version은 기존 공통 계약에 따라 실패 기록 시 현재 버전일 수 있으며 요청 버전의 조회 성공을 뜻하지 않는다.

성공 `data`는 `contract`, `contract_item`, `delivery_round`, `configuration`, `revision`, `product`, `fulfillment_assessed=false`다. 각 노드의 evidence를 공통 envelope가 수집하며 계약 근거와 실제 설계 근거를 분리해서 조회할 수 있다. `FULFILLMENT_NOT_ASSESSED` 경고는 재고·생산·운송 판단을 아직 하지 않았다는 뜻이다. 운영 관측 시각·예상 완료/도착 시각은 반환하지 않는다.

공유 원문은 [b_demo_contract_01.md](../../data/order_fulfillment/sources/b_demo_contract_01.md), 해시·SourceDocument/Evidence는 [b_contract_fixture.json](../../data/order_fulfillment/b_contract_fixture.json), 독립 정답은 [b_contract_expected.json](../../data/order_fulfillment/b_contract_expected.json)이다. `load_demo_contract()`는 고정 해시를 검증하고 후보 7개를 만든다. 기존 OLIMEX manifest/원본은 유지하며 새 원문 내용은 새 SourceDocument/Evidence 버전으로 관리한다. [B 대표 JSON-LD](../../data/order_fulfillment/b_contract_graph.jsonld)는 새 계약과 필요한 실제 설계 근거의 최소 부분 그래프이며 기존 전체 이력 그래프와 별개다.

실행은 `python -m examples.order_fulfillment.b_contract`다. 개발용 구성 시드 버전 1 → 계약 초안/검증 → 승인 전 반영 차단 → 개발용 사람 역할 검토 → 버전 2 반영 → 같은 버전 약속/근거 조회를 확인한다. 이 예제는 공통 Gateway를 직접 호출하며 실제 모델을 호출하지 않는다. 기존 PRODUCT_TOOLS/QUALITY_TOOLS 목록은 유지한다.

##### B Agent 연결 예제 (Task 09)

`examples/order_fulfillment/b_agent.py`는 B 역할 하나의 두 모드를 기존 `ModelRuntime`·`OpenAIProvider`에 연결한다. 새 공개 Service 함수·그래프 타입·별도 라우터는 추가하지 않는다. provider와 runtime에 동일한 도구 목록을 지정한다.

| 모드 | 호스트 입력·시드 | 모델 도구 | 최종 결과 |
| --- | --- | --- | --- |
| intake | source_text 원문, 등록된 SourceDocument ID/hash, 3개 Evidence ID, data_version, request_key, 노드 필드 계약. 확정 계약은 없는 개발 시드 | B_INTAKE_TOOLS: lookup, prepare_draft, get_draft, validate_draft | status, draft_id/draft_version(또는 null), data_version, evidence, unknowns(field/code), needs_human_review=true, synthetic_contract=true, summary, next_action |
| promise | delivery_round_id, data_version, not_assessed. 모델 시작 전에 기존 승인 경로로 준비한 개발용 약속 자료 | B_PROMISE_TOOLS: lookup, delivery_round | status=promise_only, data_version, promise(기존 예제 promise_values 필드), evidence, unknowns, synthetic_contract=true, summary, next_action |

intake는 추출 정답 노드를 모델에 주지 않고 원문을 제공한다. 공통 모델·개정·구성 lookup으로 정확한 기존 구성을 연결하고 새 계약 3노드만 미승인 초안화한다. 사람 검토 전 확정 버전은 1이다. 누락·모호함·충돌이면 초안 없이 확인 필요다. `extract_contract`는 호스트 평가의 결정적 기준이며 모델 공개 도구가 아니다. 이 첫 입력 범위는 고정 Markdown 표이고 임의 PDF/OCR 또는 여러 항목·회차의 계약을 지원한다는 뜻은 아니다.

promise는 버전 2 약속에서 고객·구성·계약/회차 수량·단위·도착 기한·장소를 읽고 근거 및 SourceDocument를 조회한다. `fulfillment_assessed=false`, 미확인 inventory/allocation/production/transport/actual_arrival을 유지한다. 운영 관측 시각은 없고 due_at은 약속 기한이다. 두 모드는 독립 DB 스키마를 쓰며 모델의 intake 초안을 자동 승인해서 promise로 넘기지 않는다. 예제의 scripted reviewer는 실제 업무 승인이 아니다.

`ModelRuntime(..., allowed_tools=도구목록)`는 호스트가 지정한 비어 있지 않은 고유 list/tuple/set/frozenset을 Gateway의 동결 집합으로 고정한다. 전체 호출 묶음을 실행 전에 검사하고 역할 밖 도구는 `TOOL_NOT_ALLOWED`로 중단한다. 생략 시 기존 Gateway 전체 허용 도구를 사용해 기존 호출자를 보존한다. 모드별 권한 목록을 실행 설정에도 기록한다. Agent 신원 변경·review/apply는 계속 금지한다. ModelConfig는 B 예제에서 max_steps=16, 기본 step당 max_tool_calls=4, max_attempts=2 및 기존 byte 제한을 사용한다. API 시간/토큰 한도는 기존 provider 설정이며 총 실행 시간/비용 강제 상한은 없다.

자동 평가는 성공 trace·저장된 초안·원문 해시·정확한 버전과 구성·독립 정답을 대조한다. `runtime.ok`는 최종 텍스트 생성일 뿐 업무 평가 통과를 뜻하지 않는다. 잘못된 JSON/근거/수량/기한/승인 상태는 평가 실패다. 자유문 전체의 의미 검토는 별도로 남기며 도구 경로의 대역 성공을 실제 모델 해석 품질로 계산하지 않는다. `--provider openai` 설정 실패는 DB 시드·API 호출 전에 구조화한 실패 보고서를 남긴다. 실행 로그는 Git 제외 `artifacts/local/task09/`에 둔다.

##### 기존 A/B 조사와 공통 변경 초안

## 추가·변경 10

> 원문 위치: 공통 도구 계약 0.7.0 / 개발용 사용자 호스트 (485–505행)

확장 역할은 `AgentRegistration(name, instructions, allowed_tools)`를 서버 시작 코드의 `CommonHost(..., agent_registrations=(...))`에만 등록한다. 내장 이름 덮어쓰기·중복·미등록 도구·Agent 금지 도구는 거부한다. 등록 지침은 system 메시지에 연결되고 요청 사용자의 권한을 추가하지 않는다. 읽기 계약 예제는 [examples/extension_agent.py](../../examples/extension_agent.py), 경계 검증은 `tests/test_agent_extensions.py`다. 역할 등록과 웹 Supervisor 라우팅 연결은 별도이며 자동 라우팅은 하지 않는다. B-CQ01·CQ02·CQ03의 Python 호스트 연결은 아래 예제로 추가했고 웹 메뉴 연결은 미구현이다.

##### B 역할의 CommonHost 연결

[examples/order_fulfillment/b_host_agent.py](../../examples/order_fulfillment/b_host_agent.py)의 `REGISTRATIONS`는 기존 B 지침과 도구 목록을 참조하는 신뢰된 시작 코드다. `CommonHost(identities, database, agent_registrations=REGISTRATIONS)`를 구성하고 `host.request(DevelopmentContext(subject))` 안에서 `request.run_agent(role, provider, [{'role':'user','content':canonical(task)}], config=..., capture_tool_trace=True)`로 실행한다. CommonHost가 역할의 system 지침을 붙이므로 같은 지침을 별도로 복제하지 않는다.

| 등록 역할 | 기존 도구 목록 | 필요한 사용자 권한·종료 조건 |
| --- | --- | --- |
| b_intake | B_INTAKE_TOOLS | read/draft. 기존 구성·원문·근거와 일치하는 미승인 계약 초안 후 검증; 정보 부족이면 초안 없이 확인 필요 |
| b_promise | B_PROMISE_TOOLS | read. 확정 버전의 약속·근거·미확인 설명 |
| b_fulfillment | B_FULFILLMENT_TOOLS | read. 기존 ProvenanceProvider와 CONFIG 사용, 현황→Evidence→SourceDocument→정확 인용 응답 |
| b_production | B_PRODUCTION_TOOLS | read. 생산조회→Evidence묶음→SourceDocument묶음→정확한조건부예상; 승인/쓰기불가 |
| b_delivery | B_DELIVERY_TOOLS | read. 도착예상→근거 전체 묶음→원문정보 전체 묶음→조건부 설명; 승인/쓰기 불가 |
| b_response | B_RESPONSE_TOOLS | read. 대응초안→선택근거묶음→원문정보묶음→검토용설명; 실제 발송/등록/승인 불가 |
| b_comparison | B_COMPARISON_TOOLS | read. 전후비교→변경 전 근거묶음→변경 후 근거묶음→검토용설명; 쓰기/승인 불가 |

등록과 별개로 요청 사용자의 권한이 적용된다. 예제 기본 사용자는 `dev-b-author`(read/draft)이며 실행·초안은 `user:dev-b-author`로 남긴다. 예전 고정 `b-agent`·`fulfillment-agent`를 우회 신원으로 사용하지 않는다. 사용자가 review 권한을 갖더라도 B Agent는 review/apply를 호출할 수 없다. 기존 평가기·대역의 선택적 `actor=`는 신뢰된 호스트/검사 코드가 전달하며 모델 공개 인자가 아니다. 기본값은 보존하여 standalone 예제를 유지한다.

`run_scripted_example`은 read 권한이 있는 개발 사용자를 대상으로 한다. 별도 새 스키마의 기존 합성 시드를 먼저 준비하고 시드 연결을 닫은 뒤, 동일 사용자만 등록한 독립 검사 연결과 CommonHost로 실행한다. 접수 초안을 자동 승인해서 조회 모드에 넘기지 않는다. 원래 CQ01/CQ02 검사에 실행 기록의 사용자·허용 도구·output_hash 대조를 더한다. CQ02의 정상 답 근거11개는 고정 숫자로 생성하지 않고 기존 도구 결과 정책으로 추린다. 전체 반환 근거 사슬을 로컬 확인하는 것과 모델이 원문 파일 전체를 읽는 것은 구별한다.

CLI는 `--mode intake/promise/fulfillment/production/all`, 모드에맞는`--case`, `--env-file`, 필수`--output`을 제공하며 대역 전용이다. 보고서 파일을 배타적으로 생성하여 이전 실행 원본을 덮어쓰지 않는다. 새 사례마다 새 schema를 사용한다. 기존 실모델 예제의 과거 성공을 이 새 사용자 연결의 실모델 성공으로 계산하지 않는다. B 웹/Supervisor 라우팅·실제 인증·운영 사용자 승인·유료 모델 평가는 이번 연결 범위 밖이다.

## 추가·변경 11

> 원문 위치: 공통 도구 계약 0.7.0 / 개발용 HTTP API와 화면 (509–509행)

[web.py](../../ontologics/web.py)가 CommonHost를 감싸고 [static](../../ontologics/static/index.html)의 HTML/CSS/JavaScript를 같은 origin에서 제공한다. Python 표준 라이브러리 HTTP 서버를 사용한 **로컬 개발 통합**이다. 팀 main의 v0.3.0에서 기존 입력과 호환되는 접수 후보·문자열/의미 검색 도구를 추가했다. 팀 main 병합 당시 계약은 v0.4.0/`graph-0.3`이고 현재 B-CQ05 파생 대응 조회까지 포함한 공통 계약은 v0.7.0/`graph-0.5`다. 기존 `temporal-0.1`·DB DDL은 유지한다. `web-0.3`은 개발 사용자 선택 및 명시적 AI 초안·조건 제안·검색을 포함한 HTTP 어댑터 버전이며 `X-Ontologics-HTTP-Contract` 헤더로 제공한다. 실행은 [README](../../README.md#s1s2-웹-통합--개발-신원으로-실행)를 따른다.

## 추가·변경 12

> 원문 위치: 공통 도구 계약 0.7.0 / 모델 호출 접점 (566–566행)

`runtime.py`의 `ModelRuntime`과 기존 gateway·PostgreSQL 실행 기록을 재사용한다. `openai_provider.py`는 OpenAI Responses API 고정 endpoint와 `gpt-5.4-mini-2026-03-17`만 사용한다. 기본`parallel_tool_calls=false`이며 CQ03의 제한된묶음조회만생성시선택인자true를사용한다. 공통런타임의도구실행은계속순서대로다. 원래 API `call_id`와 출력 항목(있는 경우 암호화된 reasoning 상태)을 보존해 후속 tool 결과를 연결한다. 도구 스키마는 `tool_specs.py`에 있으며 일반optional·이종노드스키마는`strict=false`이고CQ03·CQ04의동적단계스키마는`strict=true`다. 공통코드가실제입력·권한을재검증한다. [공식 function calling 계약](https://developers.openai.com/api/docs/guides/function-calling)을 따른다.

## 추가·변경 13

> 원문 위치: 공통 도구 계약 0.7.0 / 계약 변경 (607–675행)

#### B-CQ05 Agent 연결과 평가

[b_response_agent.py](../../examples/order_fulfillment/b_response_agent.py)는 B_RESPONSE_TOOLS=(response_plan,lookup_many)를 기존 CommonHost b_response에 등록한다. 동일 요청 사용자read 권한·회차/as_of/명시승인버전으로만 조회하며 기존 역할 권한과 공통0.7.0/graph-0.5·response-0.1을 보존한다.

정확한4단계는 response_plan1회→선택data.evidence전체8/13개 lookup_many(kind=Evidence)→연결source_id전체2개 lookup_many(kind=SourceDocument)→도구없는최종JSON이다. 동적strict 도구 스키마와 실행전 검사로 추가/누락/중복/다른버전·시각/조기최종을 거부한다. SourceDocument는 메타데이터이며 호스트가 전체12/17근거 사슬·로컬원문SHA를 별도로 검증한다.

최종 정확19키는 response_state,delivery_round_id,as_of,data_version,snapshot_id,response_rule_version,on_time_quantity,shortfall_quantity,estimated_full_arrival_at,condition,dispatch_quantities,draft_only,review_required,execution_performed,assumption_codes,evidence,customer_reply,dispatch_summary,confirmations다. dispatch_quantities는 delivered_quantity,in_transit_quantity,new_dispatch_quantity,on_time_new_dispatch_quantity,later_new_dispatch_quantity,currently_available_quantity,future_quantity,unallocated_quantity의 정확8키다. typed 비교로bool!=int/null!=0·조건/규칙/검토 상태/가정·정확 인용을 검사한다. 자유문 각200자,합계300자는 지시문 가이드다. 가상/초안/미발송/미등록 표시와 명백한 반대 출고조건 제안을 검사하지만 전체 의미 검토는 별도로 수행한다.

모델 입력은 중첩promise/BOM/전체무결성중복만 제외하고 사실·가정·회사/고객/목적지·현재/미래수량·null·확인사항·선택근거를 보존한다. 전체Runtimetrace와 원래제출답은 바꾸지 않는다. 원래callID·암호화reasoning 재생도 무료HTTP로 검사한다.

마지막 혼합 실제 응답이 추가version키를 반환해 실패했다. 최종 출력에만 text.format={type:json_schema,name:b_response_answer,strict:true,schema:...}를 붙이는 보완을 추가했다. 모든객체additionalProperties=false·모든필드required·미확인nullable이며 기존Provider _text_format 기본None은 요청을 바꾸지 않는다. 첫3도구단계에는 text형식을 붙이지 않는다. 이는 [공식 Structured Outputs 계약](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)에 따른 보완이다. 이후 승인된 재검증에서 같은 최종 형식을 실제 서버가 수락했고 기본·혼합 각 1번을 통과했다. 형식이 맞아도 사실·근거·문장평가를 유지하며 실패한 원래답의 키를 삭제하지 않는다.

무료CLI는 `python -m examples.order_fulfillment.b_host_agent --mode response --case normal --output artifacts/local/cq05_host_new.json`이다. mixed/mixed_due14/met_due16/all_at_once/missing_condition/missing_latest/zero_rate/coverage_missing/elapsed_eta/historical도 지원한다. 실모델은normal/mixed만 시도했다. CQ05 Task07~10 개발 범위 수락: 명시 승인 버전의 고정 가상 자료에서 대응 초안의 수량·시간·조건·근거와 읽기 권한을 확인했다. 새 전체 502개·skip 0·105.625초, 관련 82개와 무료 예제 24개 통과. 설계 36개를 고유 검사 83개와 대조했으며 D26·D34의 확장 검증은 부분 확인으로 남긴다. Task09 실제 기본·혼합 각 25/25·별도 의미 검토는 현재 코드/원본 해시 일치로 재사용했다. 이번 추가 API 0회·비용 $0. 고객 문장 단독 표시·반복 신뢰도·운영 성공·전체 MVP는 미수락이다. 다음은 CQ06 Task07 정의이며 미착수다. 기존 작업·원문·정답·키·개인 Git 제외·실패 기록·브랜치/main 보존, fetch/commit/push 0.


#### B-CQ06 Agent 연결과 평가

[전용 예제](../../examples/order_fulfillment/b_comparison_agent.py)는 `B_COMPARISON_TOOLS=(compare_response_plan,lookup_many)`를 기존 CommonHost의 `b_comparison`에 등록한다. 요청 사용자의 read 권한만 적용하며 승인/반영/발송/신청 등록은 제공하지 않는다. 기존 역할의 허용 목록·공통0.8.0/graph-0.5/comparison-0.1은 유지한다.

정확한4단계는 비교 조회 → 변경 전 선택 Evidence 전체를 before_version에서 조회 → 변경 후 선택 Evidence 전체를 after_version에서 조회 → 도구 없는 최종 strict JSON이다. 양쪽 동일 ID도 버전별로 읽고, 인용은 각 버전의 선택 집합과 정확히 일치해야 한다. 호스트는 전체 무결성 근거 사슬과 원문 SHA-256을 따로 확인한다. 모델은 Evidence 메타데이터를 읽으며 원문 전체 열람을 주장하지 않는다.

모델용 비교 결과는 필수 수량/시간/조건/가정/미확인·생산 지표·변경 입력과 기존 양쪽 customer_reply/response_changes를 보존한다. 축약은 입력에만 적용하고 원래 전체 도구 이력·제출문을 보존한다. 마지막 형식은16키 JSON이며 before/after·수량차이4개·시간차이3개·버전별근거·검토용플래그·설명4개를 포함한다. 설명은 각100자/합계220자 이내다. strict 형식 외에 사실/근거/권한/읽기불변·독립정답 검사와 별도 문장 검토를 유지한다.

무료 CLI는 `python -m examples.order_fulfillment.b_host_agent --mode comparison --case defects11 --output artifacts/local/cq06_host_new.json`이며 continued11/defects11/transport2h/combined11/zero_rate11을 지원한다. 공용 CLI는 유료 API를 호출하지 않는다. 실제 검증은 두 대표 각최대4회·전체8회·출력1,024토큰/회·자동재시도0으로 제한했으며, 첫 불량 증가 대표에서 설명 길이 실패 후4회에서 멈췄다. 운송 대표·Task10은 미실행이다. 실패 답과 판정은 원본으로 보존한다. 결과와 비용은 [PROJECT_STATUS](PROJECT_STATUS.md)의 최신 상태를 따른다. 전체MVP/운영 실행·반복 신뢰도 통과를 뜻하지 않는다.


##### CQ06 Task09 설명 길이 보완과 재검증 — 과거 구현 기록

아래 API pattern 적용은 당시 기록이다. 현재는 뒤의 최신 계약대로 API pattern을 제거하고 Python 로컬 검사는 유지한다.

설명4필드에 각각60/80/45/35자의 단일행 `pattern`을 마지막 strict JSON에 적용한다. 공식 지원 문자열 제약인pattern을 사용하고 같은패턴·strip·합계220자를 로컬 평가기에서도 검사한다. 기존각100/합계220자 기준을 완화하지 않는다. 가상/초안/미발송/미등록·금지 주장 검사는 길이와 별도로 평가하며 정확한 고객 회신 첫 문구와 조건부 예상 표현도 요구한다. 수량·시간·버전·근거·읽기 권한은 유지하고 원래 답을 수정/절삭하지 않는다.

CQ06 Task09 재검증 실패로 중단. 전체 무료572개(skip0)·예제34개 통과, 새 실제4/최대8API·자동재시도0·비캐시 단가 추정$0.03163350(청구미확정). 실행한 대표의 원래 응답·별도 문장 검토와 이전 실패를 보존했다. Task10·반복신뢰도·전체MVP/운영 수락은 진행하지 않았다. 첫 실제 자동26/26·설명151자는 통과했지만 속도/누계 혼동과 미완결 회신으로 의미 불합격이다. 이후 추가 호출을 중단했다. [현재 실행 상태](PROJECT_STATUS.md)를 따른다. 이전 절의4API 실패는 보완 전 기록이다.


##### CQ06 Python 기본 문안과 축소된 모델 역할 — 최신 계약

[Python 문안 생성기](../../examples/order_fulfillment/b_comparison_narrative.py)는 승인 비교 결과로 지표별 전후 값·단위·증감과 설명4필드의 기본 문안을 만든다. 확보 수량/관측 누계(대), 최근 정상완료 속도(대/시간), 불량 비율, 운송·준비시간, 출고방식, 예상 수량·시각, 기준시각을 구별한다. 관측과 계산의 비교이며 설비고장·책임 원인을 확정하지 않는다. 전후 전체 공통 응답·초안·원본 근거는 보존한다.

모델용 축약에 `python_explanation`(producer=python, narrative_version=cq06-narrative-0.3, reasons, basic_text, limitation)을 추가한다. 공통 도구/그래프 계약은 v0.8.0/graph-0.5/comparison-0.1 그대로다. 기본 문안은 예제 애플리케이션의 파생 표시이며 공통 계산이나 사실 그래프를 바꾸지 않는다. known/unknown은 지표·전후 쪽별로 구분하며 비교 일부 미확인 때문에 확인된 변경 후 예상 수량을 null 취급하지 않는다.

LLM은 기존4단계의 조회·버전별 근거 확인 후 사실과 Python 문안을 그대로 전달한다. 자유 문장 작성·수정 역할은 제거했다. 최종 JSON의16키와 설명60/80/45/35자·합계220자는 유지한다. 마지막 HTTP 전에 해당 비교에서 Python이 만든 각 문안 하나를 문자열 enum으로 고정한다. API의 설명4개에는 type·enum·description을 보내고 pattern은 보내지 않는다. 같은 정규식과 길이/단일행 제한은 로컬 검사에 유지한다. 로컬 guard와 독립 평가기도 다시 계산해 완전 일치를 검사한다. 변경·미완결 응답은 거부하고 원래 제출문을 보존한다. 실패 답을 나중에 Python 문안으로 고쳐서 모델 합격으로 처리하지 않는다. 모델 원문과 공통 도구 원문은 별도 보존한다.

이 변경은 모델의 자유 설명 능력 검증을 통과시킨 것이 아니라 모델이 맡는 업무를 줄인 것이다. 기존 두 실제 실패는 당시 기록이다. 새 코드의 실제 대표 검증은 아직이며, 무료 통과가 실모델/반복 신뢰도/Task10/운영 수락을 뜻하지 않는다.


##### CQ06 Python 문안 계약의 실제 재검증 결과

CQ06 Task09 새 실제 재검증 실패로 중단. 불량 증가 대표의 처음3회 조회는 완료했지만 마지막 API가 max_output_tokens 사유의 incomplete·빈 output을 반환해 최종 JSON을 받지 못했다. 실제4/최대8회·자동재시도0, 운송 대표0회·Task10 미진행. API 보고 토큰 기준 비캐시 단가 추정 $0.01961325(청구미확정). 기존 무료586개·예제34개는 코드/자료 해시 일치로 재사용하고 관련44개·두 HTTP 대역 사전검사는 새로 통과했다. 원본과 이전 두 실패를 보존했다.

최종 JSON의 enum/길이/문안 일치 검사에 진입하기 전에 API가 미완료를 반환했다. 도구 조회는 완료됐지만 최종 문안이 생성됐다고 인정하지 않는다. 원래 답을 Python 기본 문안으로 대체하거나 토큰 한도를 올려 자동 재시도하지 않는다. 이 실행의 Task09 필수 대표는 실패이며 [현재 상태](PROJECT_STATUS.md)를 따른다.


##### CQ06 공용 pattern 제거 코드 — 새 실제 두 대표 검증 완료

공용1,024 한도·원래입력·고정모델에서 불량 증가4단계의 자동27/27·독립 의미 검토 후 운송 변경4단계도 동일하게 통과했다. 실제8/최대8회·과거응답재생0·자동재시도0. 네 문안의 singleton enum·로컬 정규식/길이/원문일치와 사실/버전별근거/권한검사는 유지한다. 이번 업무코드수정0, 직전 무료586개/예제34개는 코드·자료해시일치로 재사용했고 관련44개/대역/중단상한검사는 새로 통과했다. Task09 두 대표 검증은 완료했으며 Task10·반복신뢰도·운영수락은 별도다. 원문/사용량/청구미확정 비용은 [PROJECT_STATUS](PROJECT_STATUS.md)를 따른다.


##### CQ06 Task10 — 개발 범위 수락

새 전체586개 skip0·129.653초, 예제34개와 독립 정답5사례 대조를 통과했다. 설계28개는 고유 검사83개 모두 새 통과 로그와 연결했다. 과거 D28 빈 매핑과 CQ06-07/08/12/14 미확인 항목을 현재 보강 검사와 대조했으며, 혼합 생산은 기존 단일 작업 제약의 부분미확인을 유지했다. 전체 전후 초안은 공통 결과/trace에 보존하고 최종16필드 답은 요약임을 명시했다. 버전 고정은 순차 검사이며 동시성 부하 보장이 아니다.

Task09 실제 두 대표의 코드·자료·원문·판정 바인딩을 확인해 재사용했다. 각27/27·별도 의미 PASS, 원래 live_result의 pending 표시는 고치지 않고 별도 검토/SHA로 결합했다. 이번 추가API0회·$0·업무코드수정0이다. 실제 OpenAI 통신 차단을 적용했고 시도0이었다.

명시 승인 입력·고정 가상 자료·공통 계산·읽기 Agent의 CQ06 Task07~10 개발 범위를 수락했다. CQ01~05와 S1/S2 전체 회귀는 통과했으나 각 CQ의 기존 한계를 해제하거나 전체MVP·반복 신뢰도·운영을 수락하지 않는다. 새CQ/유료반복/웹·Supervisor/실제 승인·발송·출고는 시작하지 않았다.

근거는 `artifacts/local/b_cq06_task10_20261027/`의 verification, design_verified, golden_examples_verified, live_evidence_verified, independent_acceptance_review, acceptance다. 학습용 Task10·설계 대응표는 JunhwaDocs CQ06 폴더에 추가했다. 기존 업무 코드·자료·환경 파일·이전 실행 원문과 개인 Git 제외·브랜치/main을 보존했고 fetch/commit/push0이다.
