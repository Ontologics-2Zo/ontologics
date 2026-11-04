# C MVP를 위한 온톨로지 스키마 확장 전달안

작성일: 2026년 10월 26일 Asia/Seoul  
대상: 공통 온톨로지와 스키마를 맡은 팀원  
연결 문서: [C의 기능 1차 MVP](C_기능_1차_MVP.md)  
상태: 기존 구조를 보존하는 추가 개념·관계·필드의 검토 요청

C에 필요한 흐름은 메일·엑셀·CSV의 클레임 접수와 공통 정보 변환, 관련 라인·보관 제품 추적, 사람이 검토·송부할 중단 요청서 작성, 가상 불량 테스트·조치 판정, 재생산 필요 수량 산정과 A 전달용 요청서 작성이다. 기존 제품·구성·보드·부품 로트·장착 이력에 이 업무 정보를 연결할 수 있도록 확장을 요청한다.

아래 타입·필드는 신규 후보이며 현재 구현된 스키마가 아니다. 기존 정의를 바꾸지 않고 필요한 개체와 관계를 추가한다. A에는 재생산 필요 정보만 전달하는 접점을 두며 A 내부 기능과 최종 인계 API는 정하지 않는다. 기존 B 문서는 이 요청에서 수정하지 않는다.

## 1 참조할 기존 기준

| 기준 자료 | 확인할 내용 | C 확장에 적용할 원칙 |
| --- | --- | --- |
| [제조 도메인 용어 사전](ontologics/words/%23%20제조%20도메인%20용어%20사전.md) | 제품 모델·구성·개별 보드·부품 로트·장착 이력·수량의 의미 | 기존 용어와 식별자를 같은 의미로 사용한다. 생산라인·클레임·시험 등은 신규 용어로 정리한다. |
| [팀 개발 안내 PDF](ontologics/words/Ontologics_팀_개발_안내.pdf) | 공통 기반과 역할별 책임 | 의미·ID·계산을 Agent별로 복제하지 않는다. |
| [현재 model.py](ontologics/ontologics/model.py) | `SCHEMA`, 허용 필드, 검증, JSON-LD·T-box 생성 | 현재 실행 기준을 유지하며 신규 타입·관계를 추가한다. |
| [현재 T-box](ontologics/ontology/tbox.ttl) | 클래스·참조와 타입 구분 | 공통 스키마에서 생성하는 정의와 맞춘다. |
| [대표 지식그래프](ontologics/data/initial_graph.jsonld) | 제품 정의와 가상 개별 보드·로트·장착 이력 | 기존 ID와 이력을 덮어쓰지 않는다. |
| [공통 도구 계약](ontologics/docs/TOOLS.md) | 그래프·근거·시간·자료 유형·조회 책임 | 추적과 수량 계산을 공통 기능으로 연결한다. |
| [서비스 명세](ontologics/docs/SERVICE_SPEC.md) | 공통 의미·허용 데이터·CQ·사람 검토 | 기존 장착 이력 조사와 C의 불량 조사·조치를 구분한다. |
| [Agent 실행 지침](ontologics/docs/AGENT_RUNTIME.md) | 역할별 조회와 승인·반영의 경계 | 기존 Quality의 기능을 실제 불량·제어 기능으로 확대 해석하지 않는다. |
| [프로젝트 상태](ontologics/PROJECT_STATUS.md) | 실제 구현·미구현 범위 | 새 문서나 타입 제안을 구현 완료로 취급하지 않는다. |
| [B 스키마 확장 전달안](B_온톨로지_스키마_확장_전달안.md) | 제안된 생산 작업·완성 보드 재고·출고·운송 개념 | 공통으로 필요한 개념의 이름과 의미를 맞추되 이미 구현된 타입으로 취급하지 않는다. |

현재 `graph-0.1`의 12개 타입에는 생산라인·개별 보드의 생산 출처·클레임·불량 테스트·조치 판정이 없다. `SCHEMA`가 T-box 생성 기준이며 현재 코드가 미등록 타입·필드를 거부하므로 데이터 파일 추가만으로 새 개념을 수용할 수는 없다.

## 2 보존할 기존 개념과 관계

```text
ProductRevision.model_id      → ProductModel
Configuration.revision_id     → ProductRevision
ProductUnit.configuration_id  → Configuration
BOMItem.configuration_id      → Configuration
BOMItem.component_id          → ComponentModel
ComponentLot.component_id     → ComponentModel
Installation.unit_id          → ProductUnit
Installation.lot_id           → ComponentLot
HistoryCoverage.unit_id       → ProductUnit
InventoryMovement.lot_id      → ComponentLot
사실.evidence                  → Evidence.source_id → SourceDocument
```

| 기존 개념 | C에서 재사용할 의미 |
| --- | --- |
| `ProductUnit`, `unit_id` | 고객이 신고한 실물 보드와 보관·검사·교체 대상을 식별한다. |
| `Configuration`, `configuration_id` | 같은 구성인지 확인하고 재생산 필요 수량을 구성별로 산정한다. |
| `ComponentLot`, `lot_id` | 장착된 부품 로트의 공통성·이력을 조사한다. 완성 제품의 생산 묶음으로 바꾸지 않는다. |
| `Installation`, `HistoryCoverage` | 지정 조건의 장착 이력과 확인 범위를 조회한다. 장착 확인은 불량 원인·가동 증명·품질 제한 결정이 아니다. |
| `InventoryMovement` | 부품 로트 입출고를 유지한다. 완성 보드의 보관·출고 보류로 재해석하지 않는다. |
| `company` | 기존 회사 식별 범위를 유지한다. 생산라인 이름이나 고객사 이름으로 쓰지 않는다. |
| `id`, `type`, `origin`, `evidence` | 신규 개체에서도 공통 필드와 원본 근거 연결을 사용한다. |

기존 ProductUnit의 필수 필드에 라인·불량·조치 상태를 추가하는 대신 새 생산 기록과 사건 개체가 기존 보드를 참조하게 한다. 기존 사실의 의미와 필수값·식별자·관계는 유지한다.

## 3 C가 요구하는 정보와 확장 이유

| 기능 | 현재 있는 연결 | 추가로 필요한 연결 |
| --- | --- | --- |
| 클레임 파일 변환 | 원본 문서와 근거 | 입력 형식·고객 번호·원문 일련번호·추출값·연결 후보·원본 위치를 가지는 변환 초안 |
| 클레임 대상 확인 | 개별 보드와 구성 | 고객 신고·신고 현상·납품 근거에서 보드로 연결 |
| 생산라인 추적 | 보드의 적용 구성 | 보드가 실제 어느 라인에서 언제 생산됐는지 |
| 보관 물량 추적 | 보드 식별자 | 같은 라인 생산 보드의 현재 보관 위치·상태와 기준 시점 |
| 중단 요청서 | 기존 초안·사람 검토·실행 기록의 공통 기반 | 사건·라인·보관 대상과 요청 문서·버전, 사람 검토·송부 기록의 연결 |
| 불량 테스트 | 원본·근거 | 시나리오·시험 대상·관측 결과·미검사 범위 |
| 조치 판정 | 기존 사람 검토·승인 책임 | 보드별 교체·대체·출고 허용·보류·미결 판정과 시험 근거 |
| 재생산 산정 | 구성·개별 보드 식별 | 중복 없는 대체 수요, 차감할 대체 재고, 확정·미확정 수량 |

특히 같은 구성이라는 사실만으로 같은 생산라인을 알 수 없다. 보드 생산 출처가 없으면 라인이나 영향 범위를 추정해 채우지 않도록 별도 생산 기록이 필요하다.

## 4 기존 제안과 공유할 개념

| 공통 후보 | C에서 필요한 내용 | 확장 방식 |
| --- | --- | --- |
| B안의 `ProductionWork` | 작업과 구성, 필요한 경우 생산라인과의 대응 | 최종 공통 타입을 맞춘다. C의 첫 추적은 보드 생산 기록에 실제 라인을 직접 기록해 이 타입의 구현을 선행 조건으로 두지 않을 수 있다. |
| B안의 `FinishedGoodsStock` | 위치·관측 시점·보드 또는 집계 수량 | C 첫 시연은 개별 보드 식별자가 있는 보관 기록으로 준비한다. 집계만 있으면 개별 검사·보류 대상을 모두 추적했다고 설명하지 않는다. |
| B안의 `Shipment` | 고객에게 이미 보냈다는 사실과 보드 대응 | 해당 정보가 준비되면 재사용한다. 첫 시연은 클레임 원본·납품 기록 근거에서 납품 사실을 제공할 수 있다. |

위 개념은 B 전달안에서도 아직 제안이다. C 전용으로 같은 생산 작업·재고·출하 정의를 복제하지 않고 공통 담당자가 최종 이름·관계를 맞춘다. C 때문에 B 전체 계약·회차·배정 업무를 먼저 구현해야 하는 것은 아니다.

## 5 C 신규 개체 후보

다음은 필요한 논리적 기록의 구분이다. 공통 담당자는 의미를 유지하면서 일부 타입을 합치거나 연결 개체로 분리할 수 있다.

| 개념과 타입 후보 | 주요 정보 | 참조 후보 |
| --- | --- | --- |
| 생산라인 `ProductionLine` | 회사, 라인 코드·이름 | 기존 의미의 `company` |
| 보드 생산 기록 `UnitProductionRecord` | 생산된 보드, 실제 생산라인·시점, 선택 작업 | `unit_id` → ProductUnit, `production_line_id` → ProductionLine, 선택 `production_work_id` → 공통 작업 후보 |
| 클레임 변환 초안 `ClaimIntake` | 고객 클레임 번호·원문 제품/일련번호·신고 수량·추출값·누락·연결 후보 | 공통 `evidence` → 원본 근거. 미연결 일련번호는 원문 값으로 유지 |
| 품질 사건 `QualityCase` | 사람이 확인한 클레임, 현상·시점, 신고 보드와 납품 근거 | `intake_id` → 변환 초안, `unit_ids` → 확인된 ProductUnit 목록, 공통 `evidence` |
| 영향 범위 `QualityScope` | 조사·초기 보류 기준, 라인·기간·대상 목록, 검토 상태·기준 시점 | `case_id` → 사건, `production_line_id` → 라인, `unit_ids` → 대상 ProductUnit 목록 |
| 중단 요청서 `ContainmentRequest` | 생산 중단 또는 출고·배송 중단의 요청 종류·대상·사유·작성 시점·문서 참조 | `case_id`, `scope_id`, 생산 중단 시 `production_line_id`, 호스트의 검토 초안 ID·버전 |
| 불량 시험 시나리오 `QualityTestScenario` | 신고 현상, 시험 목적·조건·판정 기준, 전수·표본 대상 선택 | `case_id` → 사건 또는 사건에 적용할 시나리오 기준 |
| 불량 시험 결과 `QualityTestResult` | 대상 보드·관측 시점·관측 내용·재현 및 통과 여부 | `case_id`, `scenario_id`, `unit_id` |
| 조치 판정 `QualityDisposition` | 고객 교체·보관 대체·출고 허용·보류·미결의 판정과 사유 | `case_id`, `unit_id`, 근거인 `test_result_ids` |
| 재생산 필요 정보 `ReproductionRequirement` | 구성별 확정 대체 수요·대체 재고·추가 정상 수량·미확정·판정 상태 | `case_id`, 기존 `configuration_id`, 근거인 판정·재고 목록 |

고객 신고와 테스트에서 관측한 현상, 원인 후보, 조치 판정은 구분한다. 같은 라인이나 부품 로트의 공통성으로 인과관계를 자동 확정하지 않는다. 원인 확정까지 필요한 연구·검증 모델은 첫 시연의 필수 확장으로 요구하지 않는다.

## 6 필드와 의미의 제안

아래 신규 이름은 현재 타입에 임의로 붙일 자유 필드가 아니라 신규 개체의 후보 필드다. 기존 `unit_id`, `configuration_id`, `company`, `quantity`, `unit`, `evidence`는 기존 의미로 재사용한다.

| 개념 | 필드 후보 | 의미 |
| --- | --- | --- |
| 생산라인 | `company`, `line_code`, `name` | 어느 회사의 어느 라인인지 식별한다. 회사 자체와 구분한다. |
| 보드 생산 기록 | `unit_id`, `production_line_id`, `produced_at`, 선택 `production_work_id` | 생산 당시의 실제 라인·시각을 보존한다. 나중에 작업의 담당 라인이 바뀌어도 과거 생산 출처가 바뀌지 않게 한다. |
| 변환 초안 | `external_claim_id`, `customer_name`, `reported_at`, `reported_symptom`, `reported_conditions`, `requested_action`, `claimed_quantity`, `reported_serials`, 제품 설명·연결 후보·변환 상태 | 고객 원문과 정규화 후보를 보존한다. 고객 번호는 내부 `id`가 아니다. |
| 품질 사건 | `intake_id`, `customer_name`, `reported_at`, `reported_symptom`, `unit_ids` | 확인된 변환 초안과 보드에 연결한다. `customer_name`은 B안의 같은 의미와 맞춘다. |
| 영향 범위 | `case_id`, `production_line_id`, `as_of`, 선택 `start`, `end`, `unit_ids`, `scope_state` | 무엇을 기준으로 어떤 보드까지 조사·보류 후보로 삼았는지 표현한다. 기간 기준은 실제로 제공됐을 때 사용한다. |
| 중단 요청서 | `case_id`, `scope_id`, `request_kind`, `prepared_at`, `document_ref`, 선택 `production_line_id`, 호스트 참조 `draft_id`·`draft_version` | 작성된 문서와 검토 버전을 연결한다. 송부·중단 완료와 구분한다. |
| 시험 시나리오 | `name`, `symptom`, `procedure`, `criterion`, 대상 선택 설명 | 첫 시연용 가정 시험 조건과 판정 기준이다. 실제 공정 검증 절차로 설명하지 않는다. |
| 시험 결과 | `case_id`, `scenario_id`, `unit_id`, `observed_at`, `observation`, `outcome` | 특정 보드에 대한 특정 시험의 관측이다. 통과·불합격·미재현 등의 최종 코드값은 공통 담당자가 정한다. |
| 조치 판정 | `case_id`, `unit_id`, `disposition`, `reason`, `test_result_ids` | 시험 결과와 별도의 업무 판정이다. 판정의 주체·검토는 기존 호스트 책임과 연결한다. |
| 재생산 필요 정보 | `case_id`, `configuration_id`, `customer_replacement_quantity`, `stock_replacement_quantity`, `available_replacement_quantity`, `required_good_quantity`, `unresolved_quantity`, `unit`, `assessment_state` | 정상 제품 기준의 필요 수량과 확정 여부를 표현한다. 근거인 판정·대체 재고 ID를 함께 연결한다. |

요청서 생성·수정·사람 검토·사람 송부와 확인한 수신·현장 반영 기록은 호스트의 초안·검토·실행 책임에 연결한다. 그래프에는 사건·대상·요청서 참조를 연결하고 문서와 검토 버전을 맞춘다. `draft_id`는 호스트 업무 기록 참조이며 새 그래프 클래스로 간주하지 않는다. 첫 MVP는 시스템 자동 송부나 모의 수신부 호출을 요구하지 않는다. 검토 완료가 송부 완료를, 송부 완료가 실제 중단을 뜻하지 않는다.

첫 C 시연의 보관 120대는 보드별 ID가 있어야 검사 대상·불합격 대상·재생산 대상의 중복을 제거할 수 있다. 재고 집계 120이라는 숫자만으로 120개의 실물 기록이나 시험 결과가 확인됐다고 생성하지 않는다.

## 7 조회할 관계 경로

다음은 제안하는 참조 방향이다. 목록 참조는 여러 대상을 가지는 관계라는 뜻이며 기존 코드가 이미 지원한다는 뜻은 아니다.

```text
ClaimIntake.evidence                         → Evidence → SourceDocument
QualityCase.intake_id                        → ClaimIntake
QualityCase.unit_ids[]                       → ProductUnit
UnitProductionRecord.unit_id                 → ProductUnit
UnitProductionRecord.production_line_id      → ProductionLine
UnitProductionRecord.production_work_id      → ProductionWork  [선택 공통 후보]

QualityScope.case_id                         → QualityCase
QualityScope.production_line_id              → ProductionLine
QualityScope.unit_ids[]                      → ProductUnit
ContainmentRequest.case_id                    → QualityCase
ContainmentRequest.scope_id                   → QualityScope
ContainmentRequest.production_line_id         → ProductionLine  [라인 요청]

QualityTestResult.scenario_id                 → QualityTestScenario
QualityTestResult.case_id                     → QualityCase
QualityTestResult.unit_id                     → ProductUnit
QualityDisposition.case_id                    → QualityCase
QualityDisposition.unit_id                    → ProductUnit
QualityDisposition.test_result_ids[]          → QualityTestResult

ReproductionRequirement.case_id              → QualityCase
ReproductionRequirement.configuration_id     → Configuration
신규 사실.evidence                           → Evidence.source_id → SourceDocument
```

생산 기록을 역조회하면 특정 라인에서 생산된 보드를 찾을 수 있다. 그 집합을 현재 보관 정보와 교차해 출고·배송 중단 대상만 구분한다. 고객 신고 5대는 이미 납품한 80대에 포함되므로 두 수량을 더하지 않는다.

기존 장착 이력의 보드·로트 관계는 필요한 조사에 재사용한다. 다만 라인별 생산 이력은 장착 이력과 다른 사건이므로 Installation을 생산 기록으로 재해석하지 않는다.

## 8 테스트와 판정 및 재생산 수량

테스트 결과는 보드별로 기록하고 같은 보드의 복수 시험은 별개의 관측으로 둔다. 조치 판정은 대상 보드에 대한 업무 결론이며 최신 적용 판정을 근거로 수량을 센다. 시험 불합격 자체로 자동 폐기·재생산 판정이 생기지는 않는다.

재생산 수량은 구성별로 중복을 제거한 고객 교체 수요와 보관 대체 수요를 합친 뒤, 같은 구성의 사건 범위 밖 사용 가능·미배정 대체 재고를 차감한다. 첫 시연에서는 보관 불합격분 모두에 유효한 납품 수요가 있다는 가정을 명시한다.

`required_good_quantity`는 추가로 확보해야 할 정상 제품 수량이며 불량을 포함한 생산 투입 수량이 아니다. `unresolved_quantity`는 이번 재생산 판정 대상으로 정한 물량 중 미결인 수량이다. 사건의 모든 영향 후보 수량이나 클레임 외 고객 보유 75대를 자동으로 뜻하지 않는다.

| 기능 문서의 독립 사례 | 확정 대체 수요 | 대체 재고 | 추가 정상 수량 | 남은 미확인 |
| --- | ---: | ---: | ---: | --- |
| 부팅 실패 전수 확인 | 고객 5 + 보관 12 = 17대 | 7대 | 10대 | 이 계산 대상의 판정은 가정 완료. 다른 고객 보유 물량은 별도 영향 확인 후보 |
| 통신 끊김 전수 확인 | 고객 5 + 보관 18 = 23대 | 7대 | 16대 | 이 계산 대상의 판정은 가정 완료. 로트 공통성은 원인 후보 |
| 증상 미재현 | 고객 교체 판정 미결 | 사용 여부 미결 | 최종 미정 | 회수 5대에 대한 추가 확인 |
| 표본 20대만 확인 | 고객 5 + 보관 4 = 9대 | 7대 | 하한 2대, 최종 미정 | 보관 미검사 100대 |

미재현 사례에서 확정 수요가 없다는 0과 최종 재생산 불필요 판정을 구분한다. 표본 사례에서는 4/20을 전체 120대에 적용하지 않는다. 첫 시연은 최종 미정인 사건의 A 확정 요청을 보류한다.

## 9 A로 전달할 최소 정보

신규 A 내부 클래스나 구매·생산 실행 모델은 정의하지 않는다. `ReproductionRequirement`로 A 전달용 요청서를 작성해 사람이 검토·다운로드하고 직접 송부할 수 있게 한다.

- 품질 사건과 `configuration_id`
- 추가 정상 수량 `required_good_quantity`와 `unit`
- 고객 교체·보관 대체·대체 재고의 산출 근거와 관련 ID
- 판정 상태와 남은 미확정
- 요구 시점이 제공된 경우 그 시점·의미·근거

첫 시연은 가정 판정이 완료된 10대·16대의 요청서 생성과 사람 검토를 보여준다. 시스템이 자동 송부하지 않으며 사람이 송부한 기록이 있을 때만 송부 사실을 표시한다. 요청서 생성·검토를 실제 A 업무·구매·재생산 실행으로 설명하지 않는다. B와의 연계는 이번 C 문서에서 추가로 정하지 않는다.

## 10 자료 정책과 구현 반영 요청

기존 제품·구성·BOM은 공개 자료에 근거한 정의를 사용하고, 신규 라인·생산 기록·클레임·테스트·판정·요청은 C용 가정 자료로 `origin=synthetic`을 사용한다. 기존 U104 등의 장착·제거 이력과 원본은 유지하고 새로운 ID·원본 근거를 추가한다.

현재 [서비스 명세의 공통 의미와 데이터 경계](ontologics/docs/SERVICE_SPEC.md)는 가상 검사 결과·고장 원인 등을 기존 허용 범위에서 제외한다. 따라서 C 확장에는 시연용 클레임·시험 결과·조치 판정과 라인 생산 이력을 허용하는 명시적인 자료 정책 보완이 필요하다. 기존 자유 필드에 시험·원인 정보를 끼워 넣는 방식으로 진행하지 않는다. 가상 기록을 실제 OLIMEX 결함 사례로 설명하지 않는다.

| 요청 작업 | 반영할 내용 |
| --- | --- |
| 공통 용어 | 클레임 변환 초안·생산라인·보드 생산 기록·품질 사건·영향 범위·중단 요청서·시험·조치 판정·재생산 필요 수량을 `words` 기준과 맞춘다. |
| 스키마와 관계 | 신규 타입·필드·참조, 자료 유형을 추가한다. 기존 타입의 의미·필수값·식별자는 유지한다. |
| 목록·값 처리 | 보드·시험 결과 목록의 타입 참조, 수량·시각·상태 필드를 명시적으로 처리한다. 현재 단일 ID 참조만 처리하는 공통 경로를 목록에도 맞추거나 별도 연결 개체로 표현한다. |
| T-box와 JSON-LD | `SCHEMA` 기준 정의와 그래프 직렬화를 함께 맞춘다. 특히 목록 참조도 문자열 값이 아니라 대상 ID 관계로 표현한다. |
| 형식 변환과 근거 | 원본 메일·엑셀·CSV를 보존하고 추출한 필드의 문단·시트/셀·행 위치와 연결한다. 정규화 후보만 수정한다. |
| 변환·조회·계산·문서 도구 | 형식별 변환, 보드에서 라인 추적, 재고 교차 조회, 시험·판정 조회, 중복 없는 계산, 두 중단 요청서와 A 요청서 작성을 제공한다. |
| 기존 역할과 호스트 | 기존 Quality·Knowledge 조회 책임을 재사용하고 신규 C 업무를 연결한다. 실제 승인·반영은 기존 사람·호스트 책임을 유지한다. |
| 공통 문서·버전 | 현재 허용 데이터·도구·스키마·구현 상태를 실제 반영 범위에 맞춰 갱신한다. |

현재 `validate_graph`는 미등록 타입·필드와 단일 ID 참조를 검사한다. 신규 타입 이름만 추가해도 숫자 필드·목록 관계·신규 시각의 검증과 JSON-LD ID 직렬화가 자동으로 모두 지원되는 것은 아니다. 관련 공통 경로를 함께 확장해야 한다.

원본·근거, 가정 자료 구분과 사실·후보·요청·관측·판정의 의미는 유지하되, 이번 MVP를 위해 복잡한 재시도나 최적화 체계를 먼저 추가할 필요는 없다.

## 11 반영 후 확인할 결과와 전달물

- 기존 제품 조회·구성 비교·장착 이력 조회가 같은 기존 데이터에서 같은 의미로 동작한다.
- 고객 보드에서 생산 당시 라인을 조회할 수 있다. 현재 작업 배치가 바뀌어도 과거 기록은 유지된다.
- 라인의 전체 생산 대상과 현재 보관 중인 대상, 고객 신고 대상을 구분한다.
- 클레임 파일을 공통 변환 초안으로 만들고 사람이 원문과 연결 후보를 확인할 수 있다.
- 중단 요청서를 사건·대상·문서·검토 버전에 연결하고 미검토·미송부·현장 반영 미확인을 구분한다.
- 시나리오별 보드 테스트와 별도 조치 판정을 연결하며 미검사·미재현·미결을 유지한다.
- 복수 시험·클레임 중복에도 보드별 대체 수요를 한 번만 센다.
- 사례의 추가 정상 수량 10대·16대, 미재현의 최종 미정, 표본의 하한 2대·미검사 100대가 재현된다.
- A 전달 정보가 근거·판정 상태를 유지하며 실제 구매·생산 실행과 구분된다.

공통 담당자는 최종 용어·타입·필드 대응표, 참조 관계, 가정 데이터 ID·근거, 변환·조회·계산·요청서 작성·사람 검토 접점과 현재 지원 범위를 C 담당자에게 전달한다. 실제 스키마·코드·자료 정책 변경은 이 전달안을 검토한 다음 구현 단계에서 진행한다.

## 12 형식별 변환과 원본 추적의 추가 요구

첫 MVP의 입력은 일반 텍스트 본문의 `.eml`, 고정 `클레임` 시트의 `.xlsx`, UTF-8 CSV다. 임의 양식·이미지 OCR·메일함 연동은 후속 범위다. 엑셀·CSV는 같은 고객 클레임 번호의 보드별 행을 묶어 신고 수량을 구한다.

`ClaimIntake`는 그래프에 확정된 품질 사건과 다르다. 원문 제품 설명·일련번호와 기존 개체의 연결 후보를 보존하며, 확인되지 않은 일련번호를 `ProductUnit` 참조로 강제하지 않는다. `QualityCase.unit_ids`에는 확인된 보드만 연결한다. 신규 가정 보드 등록은 공통 등록·검토 경로와 따로 연결한다.

원본은 기존 `SourceDocument`와 해시 기준을 재사용하고 `Evidence.locator`에 메일 문단, 엑셀 시트·셀, CSV 헤더·데이터 행을 기록한다. 기존 SourceDocument의 의미를 바꾸거나 원문을 정규화 값으로 덮어쓰지 않는다. 개별 필드의 후보·수정·원문 위치 대응은 변환 초안과 호스트 메타데이터로 유지할 수 있다.

고객 번호 `external_claim_id`는 내부 그래프 `id`와 별개다. 같은 고객의 같은 번호를 가진 메일·엑셀·CSV가 이번 예시처럼 대체 표현이면 서로 다른 품질 사건으로 합산하지 않는다. 고객·번호가 같아도 내용이 충돌하면 사람 확인 대상으로 남긴다. 복잡한 중복·재시도 플랫폼을 요구하는 것은 아니다.

예시의 신고 시각은 `2026-10-26T10:00:00+09:00`, 고객 일련번호는 C-D001부터 C-D005, 수량 합계는 5대다. 엑셀의 시각 셀은 날짜형이며 헤더에 KST를 명시했다. CSV·메일에서는 offset과 원문 표현을 보존한다. 변환 후 공통 시각을 맞추며 엑셀 셀을 UTC 시각으로 임의 해석하지 않는다.

요청서 초안은 `ContainmentRequest`로 제안한다. 이전 문서의 자동 신호 발행 모델 대신 사건·영향 범위·요청 종류·작성 문서와 사람 검토를 연결한다. 원인 미확정과 대상 범위를 명시하고 생성 시 미검토·미송부로 시작한다. 요청서·검토·송부 상태는 공통 호스트 책임에 맞춰 관리한다.

검토할 예시는 [사용 안내](outputs/01a175fb-d4cf-78c3-b927-325ece24c590/C_예시_클레임/사용안내.md)와 [기대 변환 결과](outputs/01a175fb-d4cf-78c3-b927-325ece24c590/C_예시_클레임/04_변환_결과_예시.json)에 있다. JSON은 수작업으로 정의한 기대 결과이고 현재 스키마에 등록된 객체나 변환 기능의 실제 실행 결과가 아니다. 생산라인·보관 120대·시험 결과는 고객 파일에 없으므로 별도 가정 추적 자료가 필요하다.
