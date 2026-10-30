# B 검증 책임과 저장·승인 경계

## 현재 확정한 리팩터링 책임 대응표 — 2026-10-29

현재 목표는 main 공통 코드를 고정한 B 분리다. 시연 준비나 공통 저장/승인 연결은
구현하지 않는다. 아래 표는 실제 책임과 기존 접점의 제약을 확정한 것이며, 검증기
합성이 가능하다거나 model 원복이 안전하다는 증거는 아니다. 이전 절의 저장 경계
검사 결과는 그때의 범위로 보존한다.

main 행 번호는 `git show ca7c391:ontologics/model.py` 원문 기준이며 현재 로컬
model.py 행 번호와 다르다.

| 실제 검사 | 재사용 가능한 공통 부분 | B 소유 추가분 | 남은 연결/판정 |
| --- | --- | --- | --- |
| 목록·크기·노드 형식·고유 ID | main validate_graph 72–85의 단일 함수 내부 검사 | 별도 추출 없음 | index를 만들기 전 검사 필요. dict 생성 후에는 중복 ID를 복원할 수 없다. |
| 타입·필수/선택/허용/스칼라 필드 | main 88–110, SCHEMA/ALLOWED_FIELDS에 결합 | schema.py의 B 10개 타입·필드·structured 정의 | 정의는 추출됐으나 검사를 주입할 인자가 없다. generic 검사 복제로 해결하지 않는다. |
| 실제/가상 origin | main 94–99, 타입 집합과 결합 | schema.SYNTHETIC의 B 타입 분류 | validate_domain은 enforcement를 하지 않는다. origin='real'인 B 노드를 단독으로 거부하지 못한다. |
| 직접/선택/목록 typed 참조 | main 111–114의 공통 타입 참조 | schema_validation.validate_references, schema의 B 참조 정의 | B→공통 참조를 검사하려면 완성된 전체 index가 필요하다. |
| 근거 ID·origin·출처 관계 | main 115–147의 전체 index 검사 | B는 기존 Evidence ID를 유지 | B 업무 규칙만으로 필수 근거·타입·origin 일치를 보장하지 못한다. |
| 원본 경로·SHA·Git blob·시각 | main SourceDocument 검사, instant 재사용 | B loader의 고정 원본/manifest 해시 확인 | 원본 무결성 검사 블록은 별도 주입 함수가 아니다. 공통 ROOT를 변경해서는 안 된다. |
| 제품 식별·사양·BOM·시간 이력 | main 148 이후 및 기존 temporal 함수 | 없음 | 공통 책임을 B에 복제하지 않는다. 공통 부분만 통과시켜 전체 유효로 표시하지 않는다. |
| 계약 수량·단위·도착 납기·회차 합계 | instant, _string_list 등 기존 callable | schema_validation.validate_contracts | B 규칙 추출 완료. 전체 구조 검증과의 합성은 미입증이다. |
| 이행·생산 관측·배송 조건 | 시간 해석 등 기존 유틸리티 | fulfillment_validation / production_validation / delivery_validation | validate_domain은 참조→계약→이행→생산→배송만 묶는다. 전체 validator가 아니다. |
| 계산 전 검증 | 현재 fulfillment/production/delivery_forecast의 공통 validate_graph 호출 | 기존 B 계산·수량·시간·미확인 처리 | 이번에는 참조를 교체하지 않는다. CQ05·06도 이 경로를 사용한다. |
| 조회 메타데이터·그래프 선택·출력 | 공통 타입 정의·exporter | schema.lookup_schema, B 대표 그래프 선택, schema_export | 추출됐지만 도메인 등록·저장 승인·공통 검증 확장의 의미는 없다. |

재사용 가능한 유틸리티 호출과 monolithic 검사 블록을 구분한다. 함수가 없는 검사
블록을 B에 그대로 복사하거나, B 검사를 생략한 채 validate_domain만 호출하는 것을
참조 분리로 취급하지 않는다. 다음 검증기 설계에서는 각 블록의 보존 방법부터
입증해야 하며, 그 전까지 계산 함수 import는 유지한다.

### 기존 공통 접점의 선행 조건

| 경로 | 확인한 코드와 제약 |
| --- | --- |
| B 조회 도구 | B Service는 공통 Service를 상속하고 _nodes(version)로 실제 snapshots를 읽는다. 사례 파일과 숫자 version만으로 기존 조회 계약을 대신할 수 없다. |
| typed 역할 등록 | 공통 host.AgentRegistration은 기존 공통 Gateway.ALLOWED의 부분집합만 받는다. B AgentRegistration도 spec이 있으면 이 공통 검사를 통과해야 한다. |
| run_task | 공통 host.py 345–348은 실제 ProductModel snapshot 읽기를 선행한다. 현재 CQ01만 typed 연결이며 다른 역할의 전체 typed 전환 완료를 주장하지 않는다. |
| 확정 근거 | host._TaskReads.CONFIRMED_READS는 공통 도구 목록이다. B 고유 도구는 자동으로 이 목록에 포함되지 않는다. EvidenceRef는 실제 확정 snapshot에서 해석된다. |
| 공통 한도·권한 | B request는 공통 identity/request_budget을 재사용한다. B 도구 등록·개인 실행 상한이 공통 권한이나 누적 비용 정책을 확장하지 않는다. |
| 사례 setup·저장/승인 | 기존 setup과 b_run_workflow는 B 데이터 저장/승인/반영을 사용한다. 현재 구현 범위에서는 그대로 보존하고 main 원복 영향으로 기록한다. |
| 전역 상태 | b_run_workflow._input_roots는 model.ROOT도 변경한다. 파일 불변과 실행 중 공통 전역 불변은 별도 확인 항목이다. 이번에는 수정하지 않는다. |

### 회귀 비교 기준과 증거의 범위

추출·대표 그래프·출력 테스트 3개는 `tests/order_fulfillment/model_oracle.py`로
고정된 B 커밋 `4539c904ca05f4529f8da099eff5e6be8c160c50`의 model과 validator
3개를 읽는다. 4개 원문의 SHA-256을 각각 확인하고 private module에서 실행한다.
상대 import도 그 고정 원문만 사용하며 실행 중인 공통/B validator에 위임하지 않는다.
sys.modules 등록·공통 전역 변경·과거 코드의 실행 경로 도입은 없다.

main 모델 비교 기준도 `ca7c391a9e813b6ab9e6fddfb48ab639c09a764b`와 원문 해시로
고정한다. 모델 정의는 과거 기준이지만 SourceDocument 검사는 현재 보존된 fixture
바이트를 읽는다. 과거 실행 DB·실제 모델 답변·사람 승인을 재생한 것이 아니다.
로컬 Git 객체가 없거나 해시가 다르면 실패하며 자동 fetch나 live-model fallback은 없다.

임시 소스 복사본에서 main model/__init__ 원본과 현재 B를 실제 import하는 검사는
대상 테스트 3개의 비교 기준이 활성 B 모델에 의존하지 않는지만 입증한다. 실제 B
계산 함수의 validator 참조나 DB/Agent 연결이 분리됐음을 뜻하지 않는다. 기존 생산/
배송 업무 검증 등 다른 테스트의 활성 model 의존과 버전 실패는 별도 후속 범위다.

---

기준: 통합 main `ca7c391a9e813b6ab9e6fddfb48ab639c09a764b`, 현재 B 로컬 코드.
이번에는 책임표와 무료 단위 검사만 추가했다. 공통 코드·model.py·버전·기존 그래프를
변경하거나 실제 저장/승인 연결을 구현하지 않았다.

README의 「실제 Agent 담당 폴더와 연결」·「동료의 확장 Agent 연결」 및
`docs/AGENT_RUNTIME.md`의 경계를 따른다. 고유 업무 지침/검증은 B 소유이고,
신원·공통 그래프 계약·권한·승인·확정 저장·공유 비용 정책은 공통 경로를 재사용한다.
공개 도메인 변경은 담당자 검토 대상이다.

## 현재 상태를 구분하는 기준

- main 원본: graph-0.1, 공통 타입 12개. B 타입 10개를 모른다.
- 현재 로컬 model.py: 이전 B 확장과 graph-0.5가 남아 있다. 이번에 수정하지 않았다.
- B schema.py: B 타입 10개의 정의를 추출했다. 조회 메타데이터와 출력 합성은 분리했다.
  이는 공통 모델에 타입을 등록하거나 승인/저장 권한을 부여한 것이 아니다.
- B schema_validation.validate_domain: 완성된 전체 index를 전제로 B 규칙을 검사한다.
  입력 구조·필수/허용 필드·origin·근거 검사를 포함한 전체 validator가 아니다.

따라서 현재 로컬에서 통과하는 결과를 main 원본만으로 B 저장이 가능하다는 증거로
사용하지 않는다. model.py를 원복하기 전에 아래 공통 접점 문제를 해결해야 한다.

## 검증 책임표

| 검사/책임 | B 소유 정의·함수 | 재사용할 공통 책임 | 현재 연결/제한 |
| --- | --- | --- | --- |
| 그래프 크기·노드 형식·고유 ID | 별도 구현 없음 | model.validate_graph의 전체 입력/중복 검사 | B 함수는 index를 받으므로 중복 ID 손실을 복구할 수 없음 |
| B 타입·필수/선택/허용 필드 | schema.SCHEMA, OPTIONAL_FIELDS, ALLOWED_FIELDS, STRUCTURED_FIELDS | model의 타입 등록·필수/스칼라/허용 필드 검사 | B 정의는 추출됐지만 main에는 이를 주입할 접점이 없음 |
| B 직접/선택/목록 참조의 대상 타입 | schema_validation.validate_references | 공통 노드의 참조 검사, 전체 index 및 입력 구조 보장 | B→B와 B→Configuration 등 공통 타입 참조를 B 함수에서 검사. optional은 없을 때만 생략 |
| origin·실제/가상 구분 | schema.SYNTHETIC은 B 타입 분류 정의 | model.validate_graph의 origin/근거 origin 일치 검사 | B 업무 함수만으로는 검사되지 않음. main의 B 타입 수용과 함께 연결 필요 |
| 필수 근거·Evidence 대상·출처 일치 | B는 원본 Evidence ID를 유지 | model.validate_graph의 evidence/SourceDocument/Evidence 검사 | 출력 URN 변환이나 B 참조 검사는 이 검사를 대체하지 않음 |
| 원본 위치·SHA-256·Git blob·수집 시각 | B는 새 원본/근거를 입력으로 준비 | model.validate_graph의 경로/해시/시각 검사 | 공통 ROOT를 바꾸거나 원본 무결성 검사를 우회하지 않음 |
| 공통 제품 식별·BOM·시간 이력 규칙 | B에서 복제하지 않음 | model.validate_graph, 공통 temporal 규칙 | 공통 제품/이력의 의미를 B 데이터 저장 용도로 바꾸지 않음 |
| 계약/회차 수량·단위·도착 납기·회차 합계 | schema_validation.validate_contracts | 공통 instant 등 기본 유틸리티 | B 도메인 규칙으로 추출됨 |
| 스냅샷·재고·생산 상태·배정·출하 정합성 | fulfillment_validation.validate_fulfillment | 공통 시간 해석, 전체 구조/근거 검사 | 회사/구성/스냅샷 일치, 수량 한도·배정 출처·출하 시각 등을 B에서 검사 |
| 누적 생산 관측·양품/불량 합계·순서·관계 | production_validation.validate_production_observations | 공통 시간 해석·원본/근거 검사 | 관측 없음/미확인을 성공한 완료 예측으로 바꾸지 않음 |
| 준비/운송 시간·분할/일괄 조건 | delivery_validation.validate_delivery_conditions | 전체 구조·근거 검사 | 음이 아닌 정수 초·허용 dispatch mode·범위/중복 등을 B에서 검사 |
| B 업무 규칙 묶음 | schema_validation.validate_domain | 전체 구조 검사 후 호출할 수 있는 공통 연결 | 참조→계약→이행→생산 관측→배송 조건 순서. 공통 Service에는 아직 연결되지 않음 |
| CQ02/03/04 계산 전 전체 검증 | fulfillment.fulfillment_status, production.production_completion, delivery_forecast.delivery_forecast | 현재 각 함수의 공통 validate_graph 호출 | 현재는 B 확장 로컬 model에 의존. import만 B 함수로 바꾸면 전체 검증이 빠지므로 금지 |
| 조회 타입·JSON-LD/T-box 출력 | schema.lookup_schema, schema_export | 공통 노드 exporter·공통 ontology/version | 합성 성공은 도메인 등록이나 저장 허가가 아님 |
| 작성자·검토자 신원/권한·도구 범위 | B Host/Gateway는 필요한 도구를 선택 | IdentityProvider, Service._require, AgentGateway | 등록 자체가 권한을 늘리지 않음. Agent에 review/apply 노출 금지 |
| 초안·정확한 버전/hash·검토 이력 | B에서 구현/복제하지 않음 | Service._save_draft/_draft/validate_draft/review | 승인 시 후보 전체 재검증. 자기 승인·과거 초안·hash 불일치·오래된 base 차단 |
| 확정 반영·현재 검토자 권한·감사/멱등성 | B에서 직접 DB에 쓰지 않음 | CommonHost.apply_approved, Service.apply/_run | 확정 직전 전체 재검증. 실제 DB의 원자성/감사/중복 반영은 이번 단위 검사 대상 밖 |
| 모델 응답 의미 검토·업무 승인 | B Agent 결과/자동 검사와 별도 사람 의미 검토 | 사람 review 및 호스트 확정 반영 | CQ01 의미 검토 통과가 정확한 초안 버전의 업무 승인을 대신하지 않음 |
| 공유 비용·요청 상한 | B 실행의 더 엄격한 제한 유지 | 공통 CostPolicy·공유 budget | 이번에는 모델/API 실행 0. 이 단위 검사로 비용 통합 통과를 주장하지 않음 |

## main 원본에서 확인한 저장·승인 경계

`ontologics/service.py`의 `_save_draft`, `validate_draft`, `review`, `apply`는
모듈의 validate_graph를 직접 호출한다. 생성자 인자는 database/principals/schema이며
B schema/validator를 주입하는 인자가 없다. B Service는 이 네 경로를 상속한다.
공통 Host.apply_approved도 공통 Service를 생성하며 B Host가 이를 상속한다.

| 동작 | main 원본 + B 후보의 결과 | 잘못 해석하면 안 되는 점 |
| --- | --- | --- |
| B 업무 규칙만 검사 | 정상 고정 사례는 통과 | 필수 필드/근거/origin까지 유효하다는 뜻이 아님 |
| main 전체 그래프 검사 | B 타입 10개 모두 UNKNOWN_TYPE | 버전 문자열만 바꾸거나 exporter를 바꿔도 수용되지 않음 |
| prepare_draft | ok=true, status=needs_changes, validation.valid=false | main은 잘못된 후보도 보완용 초안으로 남길 수 있음. 모든 초안 생성이 거절되는 것은 아님 |
| validate_draft | ok=true, validation.valid=false | 호출 성공과 후보 데이터 유효성을 구분해야 함 |
| review(decision=approve) | VALIDATION_FAILED, 승인 쓰기 전 중단 | 별도 한국어 의미 검토나 이전 상태가 전체 검증을 생략시키지 않음 |
| review(decision=request_changes) | needs_changes 피드백 분기 가능 | 검토 의견 기록은 승인이나 확정 반영이 아님 |
| apply(approved 상태의 테스트 입력) | VALIDATION_FAILED, snapshot 쓰기 전 중단 | 과거 승인 상태가 있어도 후보 전체 재검증이 필요함 |
| Host.apply_approved | 공통 Service 생성, 검토자 권한 갱신 후 위 apply 검사 | B request에서 B Service를 사용해도 확정 경로가 자동 전환되지 않음 |

공통 타입의 정상 catalog는 동일 main validator를 통과하며, 같은 Service 경로의
정상 분기를 SQL 기록 대역까지 도달하는 대조군으로 사용했다. 공통 ProductModel에
임의 b_extension 필드를 넣는 방식도 UNKNOWN_FIELD로 거절됨을 확인했다.
이 검사 하나가 가능한 모든 표현 방식을 배제하는 증거는 아니다. B 의미·관계·근거를
손실 없이 표현할 기존 계약이 있는지부터 검토해야 하며, 단순 재명명/임의 필드 삽입을
연결 해결책으로 채택하지 않는다.

## 무료 검사 방법과 증명의 범위

검사 파일: `tests/order_fulfillment/test_storage_boundary.py`.

- Git에 있는 통합 main의 model 원문을 별도 namespace에 읽는다. Service 원문은
  model import만 이 모델로 결합하고 나머지 실제 함수와 _run/_require를 실행한다.
  공통 파일·모듈 전역·sys.modules를 교체하지 않는다. 실행 코드에 복제 구현을 추가하지 않는다.
- 실제 DB 연결 생성자는 호출하지 않는다. SQLRecorder는 알려진 쿼리 분기와 인자를
  기록하고 정해진 행을 반환할 뿐 SQL을 실행하거나 데이터베이스를 구현하지 않는다.
  snapshot 기준 행은 대역으로 공급하지만 _nodes와 _candidate는 실제 main 함수를
  사용한다. 후보 계산 spy는 wraps로 실제 함수에 전달한다. 저장된 _draft 입력과
  transaction/lock 접점은 대역이며, 버전/hash 검사는 별도로 실제 _draft를 실행한다.
- fixture는 공통 계약의 put 묶음이다. 공통 대조군도 유효한 기준 snapshot에 없는
  노드를 추가하므로 실제 변경이 생긴다. 없는 버전·잘못된 작업·삭제/upsert·중복 작업·
  타입/origin 변경·불변 원본 변경 및 동일 put의 NO_CHANGES를 확인한다.
- B 고정 사례 5종은 실제 후보 조립 후 전체 검증을 실행한다. 원본/Evidence만 수용되는
  후보와 B 전체 후보의 needs_changes를 구분한다. 원본만 검증/승인 가능하다는 사실을
  B 계약/수량/납기/배정/출하 전체가 확정됐다는 뜻으로 사용하지 않는다.
- 자기 승인·비사람 검토자·권한 없음/회수·오래된 base·미승인 반영·요청 키 누락을
  실제 main Service 함수로 확인한다. Host의 검토자 현재 권한 재조회와 서비스 종료도
  확인한다. 사람 이름/의견/approved 상태는 단위 테스트 입력이며 실제 승인이 아니다.
- B AgentGateway의 승인/반영/seed 미노출도 확인한다. 이 검사의 거부 동작은 실제
  Gateway를 사용하지만, 서비스 _run은 예외를 확인하는 대역이며 DB 감사 검사는 아니다.
- 네트워크/psycopg 접속을 차단한다. 실행 결과와 보존 해시는 B 추출 보고서 및
  새 Git 제외 증거 폴더에 기록한다.

이번 검사는 PostgreSQL 저장·잠금·트랜잭션·권한 영속성·멱등 재실행·장애 복구·실제
인증·Agent 실행·유료 LLM 성공을 입증하지 않는다. 실제 PostgreSQL 통합 검사는
연결 방식 검토 후 새 격리 환경/별도 schema에서 수행해야 한다.

## 보류할 공통 연결과 다음 검토

필요한 접점은 B 타입/필드/가상 origin 정의를 공통 전체 검증에서 수용하고, 그 전체
검증을 초안·승인·확정 반영에서 일관되게 호출하는 방법이다. 현재 main에서 이 접점은
확인되지 않았다. B 파일만 수정해서 모든 공통 저장 경로를 연결할 수 있다고 약속하지 않는다.

1. 기존 공통 타입/도구로 B 의미·관계·근거를 보존하는 방법부터 검토한다.
2. 불가능한 부분은 업무 질문·재사용 불가 이유·추가 타입/필드/참조·원본과 실제/가상
   의미·미확인 처리·기존 데이터/Agent 영향·검증 결과를 모아 공통 담당자 검토로 남긴다.
   이번에 검토 요청을 전송하거나 별도 브랜치/PR을 만들지는 않았다.
3. 그 결정 전에는 공통 SCHEMA update, validator monkeypatch, B 노드 필터링 후
   유효 판정, 공통 승인 함수 복제, 직접 DB 쓰기나 저장된 승인 상태 강제 변경을 하지 않는다.
4. 공통 연결 방식이 정해지기 전까지 model.py 원복·기존 그래프 재생성·버전 정리는
   보류한다. B 입력 준비 중 공통 ROOT를 바꾸는 부분도 별도 후속 검토 대상이다.


## 현재 진행 위치와 남은 큰 단계 — 2026-10-29

| 구분 | 상태 |
| --- | --- |
| B 타입·업무 규칙 추출, B helper/접수 참조 전환 | 완료. 공통 전체 검증을 대체한 것은 아님 |
| 조회 메타데이터·대표 그래프·출력 의존성 분리 | 완료. 메모리 출력 전후 동일 |
| 검증 책임·저장/승인 경계 및 put/실제 후보 조립 회귀 | 완료. 실제 저장 연결은 미구현 |
| 공통 계약의 B 수용 방식 결정과 B 연결 | 남음. 현재 main에 확장 접점이 없어 먼저 검토 필요 |
| model/T-box의 main 기준 복원·버전/기존 데이터 정리 | 보류. 연결 방식 결정 및 사용자 승인 후 범위 확정 |
| 새 격리 DB에서 저장/승인·권한·중복·공통 Agent/비용 정책 통합 | 남음. 이번 SQL 기록 대역 검사로 대체하지 않음 |
| CQ01~06 노트북 최종 정리·무료 실행 검증 | 남음. 기존 출력 보존 및 승인/검토/중복/실패/종료 흐름 확인 필요 |

B 추출·출력 분리 단계는 마무리됐지만 전체 통합이 끝난 상태는 아니다. 큰 후속 작업은
위 표의 마지막 네 항목이다. 첫 항목의 연결 방식이 정해지지 않아 잔여 시간이나 완료율을
신뢰할 수 있게 계산하지 않는다. 실제 유료 시연은 이번 개발 작업에 포함하지 않고,
이후 사용자가 이번 실행을 명시적으로 승인하고 직접 수행하는 단계로 남긴다.
