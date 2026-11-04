# B 스키마 추출 — 2단계 결과

## 최신 결과 — 검증 책임 대응표·회귀 기준 독립 (2026-10-29)

승인된 범위인 B 테스트 5개와 B 문서 3개만 변경했다. B 실행 코드·model.py·공통
코드·버전·노트북·저장/승인 연결·기존 그래프는 변경하지 않았다. 리팩터링을 위한
비교 기준 분리 단계이며 시연이나 전체 main 연결 완료가 아니다.

### 변경과 보존

- B_VALIDATION_RESPONSIBILITIES.md: 실제 검사별 공통 callable/단일 함수 내부 블록/
  B 추가 책임/미연결 경계와 snapshot·도구 등록·확정 근거 전제를 확정했다.
- model_oracle.py: 테스트 전용으로 B 커밋 4539c904ca05f4529f8da099eff5e6be8c160c50의
  model과 validator 3개를 각각 SHA-256 확인 후 읽는다. 상대 import도 고정된 원문만
  사용한다. 활성 공통/B 검증 함수에 위임하거나 sys.modules에 등록하지 않는다.
- test_schema_extraction.py / test_representative_graph.py / test_schema_export.py:
  과거 B 정의·오류·출력을 위 고정 기준과 비교한다. 활성 공통 정의의 불변성·객체
  비공유 검사도 유지한다. T-box는 공통 원문/버전 보존과 과거 B 선언 보존을 구분하고,
  현재와 같은 legacy 정의에서는 과거 출력의 바이트 동일성도 유지한다.
- test_model_oracle.py: 원문/상대 의존 변조 거부, 현재 B validator와의 독립성,
  가변 상태·공통 전역 격리, B 규칙 통과가 origin/근거/허용 필드/중복 ID 검사를
  대신하지 않는다는 회귀 7개를 추가했다.
- B_SCHEMA_EXTRACTION_PLAN.md: 이번 좁은 실행 범위와 보존/무료 검증 절차를 기록했다.

새 비교 모듈은 테스트에서만 사용한다. 공통 모델/검증기를 실행 경로에 복제하거나
교체한 것이 아니다. Git 이력의 정의와 현재 보존된 원본 fixture를 함께 검사한다.
과거 실행 결과·승인·LLM 호출을 재생한 것이 아니며, 로컬 Git 객체가 없거나 해시가
다르면 실패한다. 자동 fetch나 활성 model로의 fallback은 없다.

### 무료 검증 결과

| 검사 | 결과 | 증명의 범위 |
| --- | --- | --- |
| 수정 전 선택 회귀 | 305개 중 303개 통과, 기존 버전 실패 2개 | 현재 작업 시작 기준. 오류/skip/접속 시도 0 |
| 수정 후 선택 회귀 | 312개 중 310개 통과, 동일한 기존 실패 2개 | 신규 7개 통과, 신규 실패·오류·skip·접속 시도 0 |
| 수정 전 임시 main 원본 import | 기존 대상 43개 중 29개 테스트에서 실패/오류 발생 | subtest를 포함한 상세 결과는 별도 로그. 활성 model의 B 정의에 의존하는 비교 기준 재현 |
| 수정 후 임시 main 원본 import | 기존 대상 43개 + 신규 7개, 50개 전부 통과 | 추출/대표 그래프/출력 비교 기준의 독립성. 전체 B 실행/DB 통합 성공이 아님 |
| 메모리 그래프 5종·T-box | 변경 전후 바이트 동일 | 기존 파일을 재생성하거나 덮어쓰지 않음 |
| 정적 확인 | AST/compile·공백·Git diff 확인 통과 | ruff/mypy/pyflakes는 설치돼 있지 않아 미실행. 새 의존성 설치 없음 |
| 독립 변경 검토 | 기존 객체 비공유 검사의 약화 1건 보완 후 추가 지적 없음 | 활성 model에 항목이 존재하면 활성 model과의 비공유 검사도 유지 |

임시 검증은 새 Git 제외 소스 복사본에 main model/__init__ 원문을 두고 일반 import를
수행했다. 실제 checkout·공통 모듈 전역은 변경하지 않았다. 테스트에서만 바꾼 B
대역은 각 검사의 context 종료 때 복구된다. 네트워크·psycopg 접속을 차단했다.

기존 실패는 생산 test_jsonld_tbox_and_schema_include_observation_relationship과
배송 test_schema_tbox_and_jsonld_expose_typed_condition_relationships다. 로컬 graph-0.5와
기대/저장 T-box graph-0.1 불일치로, 기대값 완화·skip·버전 변경으로 숨기지 않았다.

### 남은 경계와 다음 단계

검사/접점 대응표와 위 3개 회귀 모듈의 비교 기준 분리는 완료했다. 다른 업무 테스트의
활성 model 의존, 세 계산 함수의 validate_graph 호출, B Service/Agent의 snapshot
의존은 남아 있다. main의 generic 검사에 schema 주입 접점이 없으므로 합성 가능성은
아직 입증하지 못했다. 다음은 3-1의 **기존 검사를 복제/생략하지 않는 합성 방법 검토**다.
그 전에는 검증기 import 교체·model 원복을 진행하지 않는다. 현재 범위 밖인 기존
저장/승인·setup 경로는 삭제하지 않고 원복 영향 목록에 유지했다.

유료 호출·DB 연결·새 서버 시작은 모두 0이다. 전체 discover/실제 PostgreSQL/전체
Agent 업무/노트북은 실행하지 않았다. 과거 실제 성공·실패 근거와 실행 archive는
보존 대상으로 유지하며 이번 무료 결과를 새 실제 LLM 성공으로 표시하지 않는다.

증거 위치: Git 제외
`artifacts/local/order_fulfillment/b_oracle_independence_20261029T173300+0900/`.
before/green-tests.json·log, red/verified-main-tests.json·log, 메모리 출력,
before-hashes.json, static-check.json, preservation-check.json, changes/를 구분한다.
아래 내용은 이전 단계의 기록이다.

---

## 최신 결과 — put 계약·실제 _candidate 경계 회귀 보완 (2026-10-29)

B 테스트와 문서만 수정했다. 이전 테스트 fixture의 upsert 표기를 공통 put으로 고치고,
후보/변경 목록을 미리 반환하던 대역을 실제 main _candidate를 호출하는 spy로 바꿨다.
실제 main _nodes가 SQLRecorder에서 지정한 기준 snapshot 행을 읽는다.
공통 코드·model.py·B 실행 코드·기존 그래프·저장 연결·버전은 변경하지 않았다.
아래 20개 경계 검사 결과는 보완 전 시점의 이력이며 현재 검사는 27개다.

### 변경 파일

- `tests/order_fulfillment/test_storage_boundary.py`: put 기반 helper, snapshot 행 공급,
  실제 _nodes/_candidate 실행, 7개 회귀 추가 및 고정 사례 5종의 실제 후보 조립 검사.
- `docs/order_fulfillment/B_VALIDATION_RESPONSIBILITIES.md`: 실제 함수/남은 대역의 범위,
  원본만 수용되는 경우와 B 전체 수용의 차이, 현재 진행 위치와 큰 후속 네 단계를 기록했다.
- `B_SCHEMA_EXTRACTION_PLAN.md`, 이 보고서: 변경 전 계획과 결과를 기록했다.

기존 B 파일 4개만 변경했으며 새 파일·운영 코드 변경은 없다.

### 실제 후보 조립으로 확인한 내용

1. fixture의 정상 operations는 모두 put이다. CQ01은 원본/Evidence 4개와 B 업무
   노드 3개를 실제 기준 catalog에 추가한다. main의 후보 조립은 되지만 전체 검증은
   B 타입을 UNKNOWN_TYPE으로 거절하여 초안은 needs_changes, 승인/반영은 차단된다.
2. CQ01·CQ02·CQ03·CQ04 일반/혼합의 5개 고정 사례 모두 실제 후보/변경 목록을 만든 뒤
   전체 검증한다. B 타입 10개 전체의 거부와 B 업무 검증 통과를 구분한다.
3. 정상 공통 대조군은 유효한 기준 catalog에서 마지막 노드 하나를 제외한 뒤 해당 노드를
   put한다. 이 기준도 main 전체 검증으로 유효함을 확인한다. 가짜 changes 값을 넣지 않고
   같은 초안/검토/반영 함수의 정상 분기를 SQL 기록 대역까지 확인했다.
4. 삭제/upsert·잘못된 작업 형식·2000개 초과/빈 작업·중복 ID 작업, 기존 타입/origin 변경,
   불변 원본 변경·잘못된 ID를 실제 _candidate가 거부한다. 명시한 없는 기준 버전은
   VERSION_NOT_FOUND, bool/음수 버전은 INVALID_VERSION으로 거부한다.
5. 동일한 노드를 put하면 새 초안을 만드는 대신 NO_CHANGES로 중단한다.
   전체 후보·변경 전후 값·입력 보존 및 정확한 기준 snapshot 조회도 확인했다.
6. 원본/Evidence만 담은 put 후보는 유효하며 awaiting_review 초안 분기에 도달한다.
   이것을 B 계약·수량·납기·배정·출하 전체가 확정/승인됐다는 뜻으로 해석하지 않는다.

### 무료 검증

| 검사 | 결과 |
| --- | --- |
| 변경 전 기존 선택 무료 회귀 | 298개 중 296 통과, 기존 실패 2, 오류/skip 0 |
| helper 보완 전 새 회귀 | 새 7개 중 6개에서 옛 대역/표기 문제 재현; 초기 로그 보존 |
| 변경 후 B 경계 검사 | 기존 20개+추가 7개, 총 27개 모두 통과 |
| 변경 후 전체 선택 무료 회귀 | 305개 중 303 통과, 동일한 기존 실패 2, 오류/skip 0 |
| 신규 실패 / 네트워크·DB 접속 시도 | 각각 0 |
| 메모리 대표 그래프 5개·생성 T-box 전후 비교 | 바이트 일치 |
| AST·컴파일·공백·diff, 파일 보존 | 최종 static-check.json/preservation-check.json 참조 |

기존 생산/배송 schema·T-box 실패 두 개는 model graph-0.5/기대 graph-0.1 및
생성/저장 T-box 불일치와 관련된 기존 상태다. 기대값·버전·T-box를 고치지 않았다.
새 회귀에서의 보완 전 실패는 테스트 helper의 결과 대역이 실제 계약 위반을 놓쳤음을
보여준다. 이번 수정은 이 테스트의 정확성을 높인 것이며 운영 저장 연결 수정이 아니다.

### 검사 한계와 보존

SQLRecorder는 SELECT에 정해진 행을 반환하고 SQL 분기·인자를 기록할 뿐 SQL을
실행하지 않는다. _nodes/_candidate는 실제 main 코드이지만 snapshot 행·transaction/
lock·저장된 _draft 업무 상태는 대역이다. 실제 _draft의 버전/hash 검사는 별도 유지한다.
따라서 PostgreSQL의 실제 저장·잠금·원자성·감사·멱등성·장애 복구·인증 성공을
입증한 것이 아니다. DB·전체 discover·Agent/노트북·실제 LLM 호출은 실행하지 않았다.

수정 전 15,313개 기존 파일의 해시와 대상 사본을 보존했다. 대상 외 코드·공통 코드·
model.py·루트 PROJECT_STATUS·기존 그래프/T-box·노트북·과거 결과·최초 archive의
최종 해시 대조 결과는 preservation-check.json에 기록한다. 이번에는 snapshot 완료 후
계획/테스트를 수정했으며 before 사본을 사후 재구성하지 않았다.

유료 API 요청·DB 접속·새 서버 시작은 모두 0이며 종료할 새 서버가 없다.
기존 DB/settings/controller·과거 실행 재사용/재개, 커밋·푸시·PR 생성은 하지 않았다.
HEAD·브랜치·staged 상태를 유지한다. 의존성도 추가하지 않는다.

### 현재 어디까지 왔는가

B 타입/업무 규칙 추출과 조회·대표 그래프·출력 참조 분리, 검증 책임 및 무료 경계
검사는 완료했다. 공통 model의 기존 B 확장은 아직 남아 있으므로 공통 원본 복원이나
전체 B 독립 저장/승인까지 완료됐다고 표현하지 않는다.

큰 후속 작업은 (1) 공통 계약의 B 수용 방식 결정과 B 연결, (2) 승인된 범위의 main 기준
복원·버전/기존 데이터 정리, (3) 새 격리 DB에서 저장·승인/권한/중복·공통 Agent/비용 정책
통합 검증, (4) CQ01~06 노트북 최종 정리·무료 실행 검증이다. 첫 연결 접점의 결정이
현재 병목이므로 남은 시간이나 완료율을 추정하지 않는다. 이번에는 어느 후속 단계도
구현하지 않았다. 실제 유료 시연은 이후 사용자의 명시적 실행 승인/직접 실행으로 남긴다.

증거: Git 제외 `artifacts/local/order_fulfillment/b_put_candidate_20261029T144740+0900/`.


## 최신 결과 — B 검증 책임표·저장/승인 경계 검사 (2026-10-29)

책임표와 무료 경계 회귀만 추가했다. B 실행 코드·model.py·공통 코드·버전·기존
그래프/노트북·저장/승인 연결은 수정하지 않았다. 아래 출력 분리 결과는 이전 단계 기록이다.

### 변경 파일

- `docs/order_fulfillment/B_VALIDATION_RESPONSIBILITIES.md`: B 스키마/업무 검증,
  공통 전체 그래프/근거/신원/권한/저장 책임과 실제 함수·미연결 접점을 표로 정리했다.
  main 원본과 현재 B 확장 로컬 model을 구분하고 후속 공통 검토 범위를 기록했다.
- `tests/order_fulfillment/test_storage_boundary.py`: 무료 경계 단위 검사 20개를 추가했다.
- 이 보고서와 `B_SCHEMA_EXTRACTION_PLAN.md`: 변경 전 계획 및 결과를 기록했다.

기존 문서 2개 수정·새 B 문서/테스트 각 1개, 총 4개다. 런타임 구현 변경은 없다.

### 확인한 저장·승인 경계

1. 통합 main ca7c391의 실제 validator는 공통 catalog를 수용하고, B 고정 사례에
   들어 있는 B 타입 10개 전부를 UNKNOWN_TYPE으로 거절한다. B 업무 검증 통과나
   조회/출력 합성 성공이 공통 타입 등록을 뜻하지 않음을 확인했다.
2. 공통 prepare_draft는 B 후보를 ok=true/needs_changes로 보완용 초안에 남기는
   분기에 도달한다. validate_draft도 ok=true이면서 validation.valid=false다.
   따라서 “main이 B 초안 생성 자체를 모두 거절한다”는 설명은 부정확하다.
3. review(approve)는 VALIDATION_FAILED로 승인 기록 전에, apply는 테스트에서
   제공한 approved 상태에서도 같은 오류로 snapshot 쓰기 전에 중단한다.
   request_changes 피드백은 needs_changes 분기로 처리될 수 있지만 확정 저장이 아니다.
4. 실제 _run/_require로 자기 승인·사람 아닌 검토자·권한 없음/회수·미승인 반영·
   오래된 base·요청 키 누락을 차단했다. 실제 _draft로 정확한 버전 조회·최신 버전·
   내용 hash 불일치를 검사했다. Host는 공통 Service를 만들고 현재 검토자 권한을
   재조회하며 검증 실패 때도 서비스를 닫는다. B Gateway는 review/apply/seed를 거부한다.
5. main Service 생성자에 B schema/validator 주입 인자가 없고 관련 네 메서드 및
   Host 확정 경로는 B에서 상속함을 확인했다. B 데이터 필터링이나 공통 함수 복제로
   해결하지 않았다. 필요한 전체 검증 연결 접점은 공통 담당자 검토 사항으로 남긴다.

### 무료 검증 결과

| 검사 | 결과 |
| --- | --- |
| 변경 전 기존 무료 회귀 | 278개 중 276 통과, 기존 실패 2, 오류/skip 0 |
| 신규 저장/승인 경계 검사 | 20개 모두 통과 |
| 변경 후 전체 선택 무료 회귀 | 298개 중 296 통과, 동일한 기존 실패 2, 오류/skip 0 |
| 신규 실패 / 네트워크·DB 접속 시도 | 각각 0 |
| 메모리 대표 그래프 5개·생성 T-box 전후 비교 | 바이트 일치 |
| AST·컴파일·공백·diff 검사 | 최종 static-check.json 참조 |
| 기존 파일·과거 결과·공통 코드 보존 | 최종 preservation-check.json 참조 |

기존 실패는 생산/배송의 schema/T-box 검사 두 개다. 현재 model graph-0.5와 테스트
기대 graph-0.1이 다르고, 생성/저장 T-box 버전도 다르다. 이번에 기대값이나 버전을
바꾸지 않았다. 처음 실행한 신규 18개에서는 Host가 _draft를 한 번 호출한다는 테스트
기대가 두 subtest에서 실패했다. 실제 흐름은 호스트 사전 조회와 apply 재조회 두 번이므로
정확한 두 호출을 확인하도록 보완했다. 이후 2개 사례를 더해 최종 20개를 통과했다.
런타임 동작을 고친 것이 아니며 초기 로그도 보존했다.

### 증거의 범위와 보존

검사는 Git의 main model 원문과 Service 원본을 독립 namespace에서 실행했다.
Service의 model import만 이 main 모델에 결합하고 공통 모듈 전역/sys.modules는
변경하지 않았다. SQLRecorder는 알려진 SQL 분기를 기록하는 대역이며 SQL 실행이나
실제 DB가 아니다. _candidate/_draft의 입력 공급과 transaction/lock 접점은 대역이고,
정확한 초안 버전/hash 검사에서는 실제 _draft를 별도로 사용했다.

같은 경로의 정상 공통 입력을 대조군으로 확인했다. 정상 분기의 SQL 기록이나
대역의 rollback/close 확인은 PostgreSQL 트랜잭션·감사·멱등성·실제 서버 종료 검증이
아니다. 사람/검토 의견/approved 상태도 단위 테스트 입력이며 실제 승인으로 보지 않는다.
DB·전체 discover·Agent/노트북·실제 LLM 호출을 실행하지 않았다.

시작 시 기존 파일 15,311개의 해시를 기록했다. 계획 문서 삽입과 snapshot 실행이
겹쳐 해당 문서의 before 사본/해시만 새 절을 정확히 제거해 복원했으며 원문과 경위를
snapshot-note.txt에 기록했다. 그 밖의 기존 파일·코드는 수정 전 해시 그대로 대조한다.
과거 실제 결과·노트북 15,047개 및 최초 보존 archive, 공통 코드와 기존 그래프/T-box의
최종 보존 결과는 preservation-check.json을 따른다.

실제 유료 API 요청·DB 연결·새 서버 시작은 모두 0이며 종료할 새 서버는 없다.
기존 DB/settings/controller·과거 실행을 재사용/재개하지 않았다. Git HEAD/브랜치/staged
상태를 유지하며 커밋·푸시·PR 생성도 하지 않았다. 의존성은 추가하지 않았다.

실제 저장 연결·기존 그래프 재생성·model.py 원복·버전 정리는 계속 보류한다.
이 결과를 실제 B 저장/승인 통합 성공이나 새로운 실제 LLM 성공으로 표현하지 않는다.

증거: Git 제외 `artifacts/local/order_fulfillment/b_storage_boundary_20261029T124959+0900/`.


## 최신 결과 — B 출력 의존성 분리 (2026-10-29)

B 출력 adapter와 예제의 출력 참조 전환을 완료했다. 공통 코드는 수정하지 않았고,
기존 그래프/T-box 파일 재생성·전체 검증/저장/승인 연결·버전 정리는 진행하지 않았다.
아래 이전 단계의 “출력 함수 유지/교체 보류”는 해당 단계 시점의 기록이다.

### 변경 파일과 연결

- `ontologics/order_fulfillment/schema_export.py`: 혼합 graph_jsonld는 공통 노드만
  기존 공통 exporter로 보내고 B 노드는 추출된 변환으로 출력한다. 입력 순서와
  중복 노드도 유지한다. context 충돌은 ValueError로 중단한다.
  tbox_turtle은 공통 exporter 원문·prefix·ontology/version을 유지하고 없는 B
  선언만 뒤에 붙인다. 같은 선언은 중복하지 않고 타입/범위 충돌은 중단한다.
- `examples/order_fulfillment/b_contract.py`, `b_fulfillment.py`, `b_production.py`,
  `b_delivery.py`: 출력 함수만 B adapter에서 가져오도록 바꿨다. ROOT는 공통을
  유지하며 import 외 AST, 탐색·업무·파일 저장 함수 본문은 보존본과 동일하다.
- `tests/order_fulfillment/test_schema_export.py`: 순수 출력 회귀 11개를 추가했다.
- `tests/order_fulfillment/test_representative_graph.py`: 승인된 출력 import를 검사하도록
  참조 회귀를 갱신했다. 기존 선택/근거/순서/오류 회귀 5개는 유지한다.
- 이 보고서와 `B_SCHEMA_EXTRACTION_PLAN.md`: 변경 전 계획과 결과를 기록했다.

기존 8개 파일 수정·새 테스트 1개, 총 9개다. 공통 exporter 구현 복제, 공통 SCHEMA
등록·변경, 별도 DB 쓰기·승인 우회는 추가하지 않았다. 현재 model.py에 남아 있는
이전 B 확장은 그대로이며 main 원본으로 복원한 상태라고 표현하지 않는다.

### 무료 검증

| 검사 | 결과 |
| --- | --- |
| 변경 전 무료 회귀 | 267개 중 265 통과, 기존 실패 2, 오류/skip 0 |
| 변경 후 무료 회귀 | 278개 중 276 통과, 동일한 기존 실패 2, 오류/skip 0 |
| 신규 출력 회귀 / 대표 그래프 회귀 | 각각 11개 / 5개 모두 통과 |
| 메모리 대표 그래프 5개 / 생성 T-box 전후 비교 | 모두 바이트 일치 |
| main 원본 출력 함수만 사용하는 별도 검사 | 대표 그래프 5개 일치, B T-box 선언 합성 통과 |
| 신규 실패 / 네트워크·DB 접속 시도 | 각각 0 |
| AST·컴파일·공백·diff 검사 | 통과 |

대표 그래프는 CQ01, CQ02, CQ03, CQ04 일반/혼합이며 노드 수는 각각 14, 59, 26,
28, 41이다. 전체 JSON-LD context/ID/순서/값/origin/근거/출처가 전후 동일하다.
새 회귀는 B 타입 10개 전체, 공통 노드 전용 호출, 빈 입력·B 전용·iterator·중복 ID,
선택/목록/근거 참조, 입력·공통 전역 무변경, context 및 T-box 선언 충돌을 검사한다.
출력 전용 최소 입력은 그래프 유효성이나 저장 승인 통과를 뜻하지 않는다.

별도 검사는 git의 통합 main `ca7c391`에서 SCHEMA/SCHEMA_VERSION 및 출력 함수
두 개의 AST만 읽어 독립 namespace에서 실행했다. 공통 타입 12개만 가진 실제 main
함수에 B 타입이 전달되지 않았고 B 타입 10개의 출력은 adapter에서 합성됐다.
공통 파일·전역을 교체하지 않고 B adapter의 함수 참조만 검사 동안 대역으로 두었다.
T-box는 main 함수의 버전/원문을 그대로 받고 B 선언 집합을 중복 없이 합쳤다.
이는 출력 계층 검사이며 main 원본의 전체 검증·DB·Host 승인 통합 검사가 아니다.

기존 실패는 생산/배송의 스키마·T-box 검사 두 개다. 현재 model.py의 graph-0.5와
테스트 기대값 graph-0.1이 다르며, 생성 T-box와 저장된 공통 T-box의 버전도 다르다.
이전 검토에서 확인한 불일치를 이번 범위에서 수정하거나 기대값을 완화하지 않았다.

변경 전 신규 검사는 adapter 부재/예전 import에서 실패함을 확인했다. 첫 변경 후
실행에는 테스트 자체의 문제가 두 종류 있었다. 접수 fixture에는 공통 근거 노드도
포함되어 B 전용 입력 검사에서 이를 걸러야 했고, 기존 추출 검사가 모듈을 reload하므로
소비자가 보관한 함수 객체와 reload 후 객체의 동일성 비교는 부적절했다. B 전용 입력을
분리하고 AST import 및 함수 출처를 확인하도록 보완했으며 초기 로그도 보존했다.
최종 회귀는 위 표와 같다. ruff/mypy/pyflakes는 미설치이며 의존성을 추가하지 않았다.

### 보존과 한계

시작 시 기존 15,310개 파일의 해시와 대상 사본을 보존했다. 최종 보존 대조는 같은
증거 폴더의 preservation-check.json에 기록한다. 공통 model.py·공통 코드·루트
PROJECT_STATUS·공통/기존 B 그래프·T-box·노트북 및 과거 결과는 수정하지 않았다.
메모리 출력 스냅샷은 새 Git 제외 증거 폴더에만 기록했고 원래 저장 파일을 쓰지 않았다.

실제 유료 API 요청·DB 연결·서버 시작은 모두 0이며 종료할 새 서버는 없다.
기존 실행 DB/settings/controller·과거 실행을 재사용하거나 재개하지 않았다.
Git HEAD·브랜치·staged 상태를 유지하며 커밋/푸시·PR 생성은 하지 않았다.

T-box 합성은 공통 exporter가 만드는 한 줄 선언 형식을 대상으로 하며 임의 Turtle을
해석하는 파서는 아니다. 입력은 사전에 검증돼야 하고 adapter는 검증/저장 권한을
부여하지 않는다. DB run_example·전체 discover·노트북·실제 LLM은 실행하지 않았다.
전체 검증·저장·승인 연결, model.py 원복, 기존 그래프 재생성, 버전 정리는 계속 보류한다.
이번 통과를 새로운 실제 LLM 성공이나 모든 main 기능 호환 통과로 표현하지 않는다.

증거: Git 제외 `artifacts/local/order_fulfillment/b_output_split_20261029T123726+0900/`.


## 최신 결과 — B 대표 그래프 참조 분리 (2026-10-29)

승인한 B 예제 네 파일의 참조 출처 전환만 완료했다. 탐색·공통 출력 함수·업무 실행·
저장 로직은 유지했으며, 기존 그래프 파일을 재생성하지 않았다. 이전 결과는 아래 이력이다.

### 이번 변경 파일

- `examples/order_fulfillment/b_contract.py`: SCHEMA를 B LOOKUP_SCHEMA의 같은 이름 alias로
  가져온다. ROOT·graph_jsonld·tbox_turtle은 공통 호출을 유지한다.
- `examples/order_fulfillment/b_fulfillment.py`, `b_production.py`, `b_delivery.py`:
  SCHEMA는 같은 B 합성 정의로, LIST_REFERENCES는 B 목록 참조 정의로 전환했다.
  기존 선택 참조 생략·근거/출처 탐색·노드 순서·중복 방문 방지는 수정하지 않았다.
- `tests/order_fulfillment/test_representative_graph.py`: 순수 회귀 5개를 추가했다.
  네 파일의 import 출처와 공통 출력 함수 유지, 독립 너비 우선 탐색과 실제 순수 함수의
  고정 사례 결과 대조, 선택/목록 참조, 공유/순환 참조, 없는 대상/타입의 오류를 검사한다.
- 이 보고서와 `B_SCHEMA_EXTRACTION_PLAN.md`: 변경 전 범위와 결과를 기록했다.

기존 파일 6개 수정·새 B 테스트 파일 1개, 총 7개가 이번 변경 대상이다.
구현 네 파일은 import를 제외한 AST가 보존본과 동일하다. 공통 그래프에 타입을 등록하거나
추가 검증/저장 권한을 부여한 것이 아니며 기존 모델의 B 확장도 그대로다.

### 무료 검증 결과

| 검사 | 결과 |
| --- | --- |
| 변경 전 기존 무료 검사 | 262개 중 260 통과, 기존 버전 실패 2, 오류/skip 0 |
| 새 회귀의 변경 전 재현 | 5개 중 참조 출처 검사 1개에서 네 파일 subtest 실패, 나머지 4개 통과 |
| 변경 후 무료 검사 | 267개 중 265 통과, 동일한 기존 버전 실패 2, 오류/skip 0 |
| 신규 회귀 | 5개 모두 통과 |
| 메모리상 대표 그래프 전후 비교 | 5개 고정 사례 모두 바이트 일치 |
| 신규 실패 / 차단된 네트워크·DB 접속 시도 | 각각 0 |
| AST·컴파일·공백·diff 검사 | 통과 |

메모리 그래프의 노드 수는 CQ01 14개, CQ02 59개, CQ03 26개, CQ04 일반 28개,
혼합 41개다. ID·순서·필드·JSON-LD context/URN·실제/가상 origin·근거/출처를 포함한
전체 결과가 전후 동일하다. 입력 및 공통 정의 무변경도 검사했다.
공유/순환 시험 입력은 탐색 종료만 확인하는 비정상 입력이며 저장 허가의 근거가 아니다.

메모리 그래프 대조는 실제 순수 함수 b_delivery.representative_graph를 고정 사례에
적용한 결과다. b_contract/b_fulfillment/b_production의 DB run_example 전체는 실행하지 않았다.
세 파일은 참조 출처 검사 및 import 외 AST 무변경으로 보호했고, 이를 실제 DB 승인·저장
통합 통과로 표현하지 않는다. 실제 DB·전체 discover·노트북·LLM 시연은 미실행이다.

기존 실패 2건은 아래 이력의 생산/배송 JSON-LD·T-box 검사가 graph-0.1을 기대하지만
현재 model.py가 graph-0.5인 차이다. 변경 전후 같은 실패이며 기대값/버전을 고치지 않았다.
ruff/mypy/pyflakes는 미설치라 실행하지 않았고 의존성도 추가하지 않았다.

### 보존과 보류

시작 시 15,309개 기존 파일의 해시와 대상 파일 사본을 보존했다. 최종 해시 대조는
`preservation-check.json`을 따른다. 기존 그래프·T-box·노트북·저장 출력·과거 실제 성공
증거와 보존 archive는 쓰기 대상으로 사용하지 않았다. 메모리 그래프 스냅샷은 새 Git 제외
검증 증거 폴더에만 기록했다. model.py·공통 코드·루트 PROJECT_STATUS는 수정하지 않았다.

실제 유료 API 요청·DB 접속·서버 시작은 모두 0이며 종료할 새 서버가 없다.
기존 DB/settings/controller·과거 실행을 재사용/재개하지 않았다. 기존 staged 변경·HEAD·
브랜치는 유지하며 Git 커밋/푸시·PR 생성은 하지 않았다.

공통 graph_jsonld/tbox_turtle은 여전히 기존 B 확장 모델에 의존한다. 출력 함수 교체·
기존 그래프 재생성·전체 검증/저장/승인 연결·model.py 원복·버전 변경은 계속 보류한다.
이 결과는 main 원본만으로 B가 독립 실행된다는 증거나 새로운 실제 LLM 성공이 아니다.

증거: Git 제외 `artifacts/local/order_fulfillment/b_representative_refs_20261029T121123+0900/`.
`before-tests.json`, `red-tests.json`, `green-tests.json`, `*-memory-graphs.json`,
`memory-graph-comparison.json`, `static-check.json`, `preservation-check.json`,
`before/`, `changes.patch`를 참조한다.

## 최신 결과 — B 조회 도구 스키마 참조 분리 (2026-10-29)

승인한 B 조회 메타데이터 분리만 완료했다. 현재 조회 도구의 공개 인자·타입 목록·권한은
동일하며, 공통 그래프에 B 타입을 등록하거나 공통 검증·저장·승인 연결을 완료한 것은 아니다.
이전 단계의 결과는 아래 이력으로 보존한다.

### 이번 변경 파일과 유지한 경계

- `ontologics/order_fulfillment/schema.py`: `lookup_schema(common_schema=None)`와 조회용
  `LOOKUP_SCHEMA` 스냅샷을 추가했다. 공통+B 중첩 정의를 깊게 복사하고 동일한 중복은
  한 번만 포함한다. 같은 타입의 필드/참조 정의가 다르면 타입명을 포함한 ValueError로
  중단한다. 기존 B SCHEMA·선택/목록 참조·필드 정의·버전 기록은 그대로 유지했다.
- `ontologics/order_fulfillment/service.py`: lookup_many 타입 확인의 출처만 LOOKUP_SCHEMA로
  전환했다. `_run`·확정 버전 조회·공통 envelope·개수/중복/원자적 실패 처리는 유지했다.
- `ontologics/order_fulfillment/tool_specs.py`: 같은 조회 스냅샷으로 kind enum을 구성한다.
  공통 도구 레지스트리·역할별 허용 목록·도구 인자·반환 형식은 변경하지 않았다.
- `tests/order_fulfillment/test_schema_extraction.py`: 합성 4개 회귀를 추가했다.
- `tests/order_fulfillment/test_lookup_many.py`: DB 없는 B 조회/Gateway 계약 검사 4개를
  별도 클래스 LookupManyContractTests에 추가했다. 기존 DB 테스트 클래스는 수정하지 않았다.
- `tests/order_fulfillment/test_tool_specs.py`: 현재 공통+B enum 순서 유지 검사 1개를 추가했다.
- 이 보고서와 `B_SCHEMA_EXTRACTION_PLAN.md`: 승인 범위·변경 전 계획·검증 결과를 기록했다.

구현 3개·테스트 3개·B 문서 2개, 총 8개 파일이 이번 변경 대상이다.
서비스/도구 명세는 정의 출처를 정규화한 AST가 보존본과 동일하며, 기존 B 정의 AST도 동일하다.
보존한 변경 전 도구 명세와 B 전체 도구 및 공통 네 역할의 전체 명세 5개 그룹을 비교해
완전 일치함을 확인했다. 현재 타입은 공통 12개+B 10개이며 순서도 유지한다.

### 무료 검증 결과

| 검사 | 결과 |
| --- | --- |
| 변경 전 무료 검사 | 253개 중 251 통과, 기존 버전 실패 2, 오류/skip 0 |
| 변경 전 새 합성 회귀 재현 | 합성 함수 부재로 4개 실패, 조회/도구 계약 9개 통과, 오류 0 |
| 변경 후 무료 검사 | 262개 중 260 통과, 동일한 기존 버전 실패 2, 오류/skip 0 |
| 신규 회귀 | 9개 모두 통과 |
| 합성·조회 대역·도구 명세 선택 검사 | 13개 모두 통과 |
| 신규 실패 / 차단된 네트워크·DB 접속 시도 | 각각 0 |
| AST·컴파일·공백·diff 검사 | 통과 |

합성 검사는 B 타입이 없는 공통 기준 사전, 명시적 빈 사전, 동일 정의 중복,
필드/참조 충돌, 중첩 복사의 독립성, 기존 공통 전역 무변경을 포함한다.
별도 합성에만 넣은 시험 타입이 실행 중인 도구 목록을 변경하지 않는지도 확인했다.
조회 검사는 현재 모든 타입, 요청 순서·버전 전달, 최대 16개 경계, 중복/잘못된 kind,
명시적 버전, 없는 ID/다른 타입의 전체 실패 및 공통 Gateway의 신원 인자·허용 도구 거부를 포함한다.

조회 메서드 검사는 `_run`/`_nodes` 대역을 사용했다. 실제 DB 권한·감사 기록·트랜잭션
통합 통과로 표현하지 않는다. DB를 사용하는 기존 LookupManyTests는 실행하지 않았다.
전체 discover·노트북·실제 LLM 실행도 하지 않았다. ruff/mypy/pyflakes는 미설치라
실행하지 않았고 의존성도 추가하지 않았다.

기존 실패 2개는 아래 생산/배송 JSON-LD·T-box 검사의 graph-0.1 기대값과 현재
model.py graph-0.5의 차이이며 변경 전후 동일하다. 기대값/모델 버전을 고치지 않았다.
무료 모의 검증을 새로운 실제 LLM 성공으로 계산하지 않는다.

### 보존과 다음 경계

시작 시 15,309개 파일 해시와 대상 파일 사본을 보존했다. 최종 보존 대조 결과는 증거
폴더의 `preservation-check.json`을 따른다. `model.py`·공통 코드·공통 T-box·루트
PROJECT_STATUS·기존 노트북/출력·대표 그래프/출력 코드·저장 연결은 이번 대상이 아니다.
기존 staged 변경·HEAD·브랜치를 유지하며 커밋/푸시/PR 생성은 하지 않았다.

실제 유료 API 요청·DB 연결·새 서버 시작은 모두 0이다. 종료할 새 서버가 없고,
기존 DB/settings/controller나 과거 실행을 재사용·재개하지 않았다.
로컬 model.py는 여전히 B 확장이 남은 graph-0.5다. 이 작업은 main 원복 후 저장 호환성을
입증하지 않으며 대표 그래프·JSON-LD/T-box·공통 검증/저장/승인 연결은 계속 보류한다.

증거: Git 제외 `artifacts/local/order_fulfillment/b_lookup_schema_split_20261029T115729+0900/`.
`before-tests.json`, `red-tests.json`, `green-tests.json`, `tool-contracts.json`,
`static-check.json`, `preservation-check.json`, `before/`, `changes.patch`를 참조한다.

## 최신 후속 결과 — B 내부 참조 전환 (2026-10-29)

사용자가 승인한 순환 import 방지·B 정수 검증 함수 참조·접수 Agent 필드 참조만
전환했다. 공통 모델 원복이나 공통 스키마 확장 연결을 완료한 것은 아니다.
이 절 이전 단계의 수치는 아래 이력에 구분해 보존한다.

### 이번 변경 파일

- `ontologics/order_fulfillment/schema_validation.py`: 기존 B 검증기 세 개의 import를
  `validate_domain()` 내부로 이동해 B helper 역참조의 순환을 방지했다.
- `ontologics/order_fulfillment/fulfillment_validation.py`, `production.py`:
  `_positive_integer`를 B 추출본에서 가져온다. 공통 `instant`, `ROOT`, `validate_graph`는 유지했다.
- `examples/order_fulfillment/b_agent.py`, `b_task_agent.py`: 접수 세 타입의 필드 정의를
  B `schema.SCHEMA`에서 가져온다. 공통 canonical/digest 및 Task/Result 계약은 유지했다.
- `tests/order_fulfillment/test_schema_extraction.py`: 새 인터프리터 진입 순서 6개에서
  B 참조·기존 공통 유틸리티 유지·정상 고정 사례 검증·생산 계산·접수 task 생성을 검사했다.
  고정 원문 접수 준비는 서비스 대역으로 검사하여 승인·실모델 호출이 없음을 확인했다.
- `tests/order_fulfillment/test_b_task_agent.py`: 접수 테스트 입력의 필드 정의도 B 참조로 전환했다.
- 이 보고서와 `B_SCHEMA_EXTRACTION_PLAN.md`: 승인 범위·회귀 계획·결과를 기록했다.

함수 계산·검증 순서·접수 지침·도구 권한을 바꾸거나 새로운 공통 로직을 복제하지 않았다.
구현 파일 5개의 import를 제외한 AST가 보존본과 동일함도 확인했다.

### 무료 검증

| 검사 | 결과 |
| --- | --- |
| 변경 전 기존 무료 검사 | 247개 중 245 통과, 기존 버전 실패 2, 오류/skip 0 |
| 새 회귀의 변경 전 재현 | 검사 2개 중 최초 import별 참조 출처 검사 6건 실패, 오류 0 |
| 변경 후 전체 선택 검사 | 249개 중 247 통과, 동일한 기존 버전 실패 2, 오류/skip 0 |
| 추출/참조 검사 | 23개 모두 통과, 새 인터프리터 진입 순서 6개 포함 |
| 접수 순수 계약·CQ01 bounded provider 대역 | 기존 14개 모두 통과 |
| 신규 실패 / 차단된 네트워크·DB 접속 시도 | 각각 0 |
| 구문·컴파일·공백·diff 검사 | 통과 |

기존 실패는 아래 이력과 동일한 생산/배송의 JSON-LD·T-box 버전 검사 2개다.
현재 model.py의 graph-0.5와 기대값 graph-0.1의 차이를 이번 범위에서 변경하지 않았다.
테스트 실행기의 최초 실행은 import 검색 경로 누락으로 수집 오류가 났고, 경로를 바로잡은
뒤 위 사전/사후 검사를 실행했다. 해당 실행기 오류도 `runner-import-path-error.*`로 보존했다.
이 오류에서 DB/API 접속 시도는 없었고 제품 코드 실패로 계산하지 않는다.

ruff/mypy/pyflakes는 명령·모듈 모두 미설치여서 실행하지 않았으며 의존성을 추가하지 않았다.
실 DB 통합·전체 discover·노트북 실행·실제 LLM 검증은 수행하지 않았다.
실제 유료 API 요청·DB 연결·서버 시작은 0이며 종료할 새 서버가 없다.
이 결과를 새로운 실제 LLM 성공으로 계산하지 않는다.

### 보존과 보류

변경 전 15,309개 파일의 해시와 대상 파일 사본을 보존했다. 최종 대상 외 파일·기존 결과
해시 대조는 아래 증거 폴더의 `preservation-check.json`에 기록한다.
`ontologics/model.py`·공통 코드·루트 PROJECT_STATUS·공통 T-box·기존 노트북과 저장 출력은
이번 수정 대상에 포함하지 않았다. 기존 staged 변경·HEAD·브랜치도 유지한다.

조회 타입 합성, JSON-LD/T-box exporter, 전체 그래프 검증·공통 Service 저장/승인 연결,
버전 표시, 기존 `_input_roots()` 처리, 다른 역할의 run_task 전환은 보류했다.
로컬 model.py는 아직 B 확장이 남은 graph-0.5이며 main 원본으로 원복하지 않았다.

증거: Git 제외 `artifacts/local/order_fulfillment/b_schema_reference_switch_20261029T114647+0900/`.
`before-tests.json`, `red-tests.json`, `green-tests.json`, `static-check.json`,
`preservation-check.json`, `before/` 및 변경 비교 patch를 참조한다.

## 최신 상태 — 참조 검증 추출 보완

검토에서 발견한 **B 참조 검증 실행 로직 누락을 보완했다.**
`schema_validation.py`에 B 전용 `validate_references(index, error)`를 추출하고
새 B `validate_domain` 내부에서만 호출한다. model.py·공통 코드와 실제 실행 연결은 그대로다.

- B 필수 참조 14개의 존재와 대상 타입, 선택 참조 2개와 목록 참조를 기존 구현과 대조했다.
- 선택 필드는 아예 없을 때만 생략한다. 명시적 null/빈 값/잘못된 형태/없는 ID/다른 타입은
  `INVALID_REFERENCE`다. Allocation의 소스가 정확히 하나여야 하는 기존 업무 검증도 유지한다.
- `round_ids`의 빈 목록·중복·잘못된 형태·없는 ID·다른 타입을 검사한다.
  공통 `_string_list`를 재사용하고 오류 코드/설명도 기존 표현을 유지한다.
- 참조 검증은 B 노드만 처리한다. 필수/스칼라 필드·origin·근거와 공통 노드 참조,
  전체 그래프 유효성 및 승인/저장을 대체하지 않는다.

회귀 6개를 추가해 보완 전 누락을 재현했다(21개 검사에서 subtest 실패 118건, 오류 0).
보완 후 추출 검사 **21개 모두 통과**했다. 기존 B 검사와 합친 **233개 중 231개 통과**,
**동일한 기존 버전 기대값 실패 2개**, 오류/skip/차단 함수 접속 시도는 각각 0이었다.
이번 보완 중 실제 API·DB 접속·서버 실행은 모두 0이다. 아래 최초 추출 때의 DB 테스트
선택 오류 기록은 당시 이력이며, 이번에는 그 DB 클래스를 처음부터 실행하지 않았다.

변경 파일은 `schema_validation.py`, `test_schema_extraction.py`, 이 보고서와 계획 문서다.
1단계의 기존 15,303개 파일은 최종 SHA-256 재대조에서 모두 일치했고,
기존 staged/unstaged patch와 Git 상태도 그대로다. schema.py/schema_export.py도 변경하지 않았다.
구문·컴파일·공백·`git diff --check`를 통과했다. ruff/mypy/pyflakes는 미설치로 미실행이며
새 의존성은 추가하지 않았다. 검증 로그는 Git 제외
`artifacts/local/order_fulfillment/schema_reference_extraction_20261029T112846+0900/`에 있다.
`red-tests.json`, `green-tests.json`, `after-tests.json`, `preservation-check.json`을 참조한다.

아래는 **최초 추출 당시의 결과 기록**이다. 참조 검증 누락에 관한 최신 상태는 이 절을 따른다.
원복·공통 연결 완료나 새로운 실제 LLM 성공을 뜻하지 않는다.

## 최초 추출 결과

이번 단계는 **추출만 완료**했다. 기존 파일은 수정하지 않았으며, 실행 경로는 아직
기존 `model.py`를 사용한다. 공통 모델 원복·B import 전환·공통 저장 연결은 미실행이다.

## 추가 파일과 내용

| 새 파일 | 내용 |
| --- | --- |
| `ontologics/order_fulfillment/schema.py` | B 타입 10개와 필드·가상 운영 구분·선택/목록 참조·구조화 필드 |
| `ontologics/order_fulfillment/schema_validation.py` | 기존 B 양의 정수·계약 수량/단위/납기·회차 합계 규칙과 기존 B 검증기 3개 연결 |
| `ontologics/order_fulfillment/schema_export.py` | 기존 표현을 유지한 B 전용 JSON-LD context/노드 변환·T-box 선언 조각 |
| `tests/order_fulfillment/test_schema_extraction.py` | 추출 단계의 legacy 대조 및 공통 전역/입력 무변경 검증 15개 |
| `docs/order_fulfillment/B_SCHEMA_EXTRACTION_PLAN.md` | 수정 전 범위·회귀 계획·이후 연결 경계 |
| 이 문서 | 실제 검증 결과와 한계 |

B 타입은 Contract, ContractItem, DeliveryRound, FulfillmentSnapshot,
FinishedGoodsStock, ProductionWork, ProductionObservation, Allocation, Shipment,
DeliveryCondition이다. 기존 재고·생산·배송 검증기는 복제하지 않고 재사용했다.
공통 시간 함수와 공통 필드 정의도 재사용하며, 공통 신원·승인·저장 코드는 복제하지 않았다.
`EXTRACTED_GRAPH_VERSION=graph-0.5`는 추출 당시 출처 버전 기록이며 새 공통 계약 버전이 아니다.

## 무료 검증 결과

| 검사 | 실제 결과 |
| --- | --- |
| 수정 전 기존 B 순수 Python 검사 | 212개 실행: 210 통과, 기존 실패 2, 오류 0 |
| 수정 후 기존 검사 + 추출 검사 | 227개 실행: 225 통과, 동일한 기존 실패 2, 오류 0 |
| 새 추출 검사 | 15개 모두 통과 |
| 수정 전후 새 실패 | 0 |
| 정정한 검사에서 socket/psycopg 차단 감지 | 0건 |
| 새 Python 파일 | AST 구문·컴파일·공백 검사 통과 |
| `git diff --check` | 통과 |
| 1단계 보존 manifest 재대조 | 기존 15,303개 파일 모두 SHA-256 일치 |
| 기존 staged/unstaged patch | 단계 시작 전과 동일 |
| 유료 API/새 서버 시작/성공한 DB 연결 | 각각 0 |

정상 계약·재고·생산·배송(기본/혼합) 고정 사례, 계약/회차의 잘못된 수량·단위·납기,
회차 수량 합계 초과, 생산/운송 오류, 확인 누락의 미확인 유지, 선택 참조의 부재와
목록 참조 URN을 대조했다. 입력 레코드와 공통 전역 변경도 검사했다.

기존 실패 2개는 다음과 같다. 두 테스트가 `graph-0.1`을 기대하지만 현재 공통 모델의
실제 값은 `graph-0.5`이다. **추출 전부터 같은 실패가 있었으며 이번 범위에서 수정하지 않았다.**

- `test_production_validation.ProductionObservationValidationTests.test_jsonld_tbox_and_schema_include_observation_relationship`
- `test_delivery_validation.DeliveryConditionValidationTests.test_schema_tbox_and_jsonld_expose_typed_condition_relationships`

처음 사전 검사 선택에 DB 테스트 클래스 5개가 포함되어 setup/cleanup 접속 오류 10건이
발생했다. 서버 접속은 모두 거부되어 SQL/등록/승인 실행으로 진행되지 않았다.
이 선택 오류 로그는 따로 보존했고, 해당 클래스를 제외한 뒤 socket 및 psycopg 접속을
차단해 위 212/227개 검사를 수행했다. 새 DB나 서버를 시작하지 않았다.

별도 ruff/mypy/pyflakes는 설치되지 않아 실행하지 않았고 의존성을 추가하지 않았다.
DB 통합 검사·전체 discover·노트북 실행·실모델 검증은 이번 추출 검증에 포함하지 않는다.
무료 대조 결과를 새로운 실제 LLM 성공으로 계산하지 않는다.

검증 로그와 JSON은 Git 제외
`artifacts/local/order_fulfillment/schema_extraction_step2_20261029T110119+0900/`에 있다.
`before-tests.json`, `after-tests.json`, `summary.json`, `preservation-check.json`을 참조한다.

## 보존과 미완료 연결

`model.py`, `ontology/tbox.ttl`, 공통 Service/Host와 기존 B 실행 파일·import,
루트 `PROJECT_STATUS.md`, 기존 문서·노트북·저장 출력·고정 입력·과거 결과는 그대로다.
과거 DB/settings/controller를 실행 대상으로 재사용하거나 이전 실행을 재개하지 않았다.
Git 강제 추가·커밋·푸시·PR 생성은 하지 않았다.

원본을 보존한 단계적 추출이므로 기존 model.py의 B 정의와 새 추출본이 일시적으로
함께 존재한다. **원복이나 연결이 끝난 상태가 아니며 전체 기능 호환성 통과도 아니다.**

새 domain 검증 함수는 전체 index를 받아 B 규칙만 확인한다. 필수 필드·origin·근거·참조와
전체 그래프 검증 및 승인/저장은 별도로 공통 경로가 담당해야 한다.
JSON-LD/T-box도 B 조각만 제공하며 공통 전체 exporter의 대체물이 아니다.

이후 단계에는 B 내부 참조 전환(기존 `_positive_integer` import 포함),
공통 모델/T-box 원복, 전체 그래프 검증·export·공통 Service/Host 연결 검토가 필요하다.
추출 테스트는 현재 legacy model.py를 대조 기준으로 사용하므로 원복 단계에서 통합 계약
테스트로 조정해야 한다. 저장·승인 우회나 공통 전역 monkey patch로 해결하지 않는다.
