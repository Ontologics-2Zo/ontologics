# Ontologics 공통 기반 v0.3.0 — PostgreSQL + pgvector

실제 OLIMEX 사양·BOM을 제품·구성으로 등록하고, 가상 개별 보드·로트 이력과 연결해 조사·보완하는 공통 라이브러리다. S1은 문제 상황 분석 및 생산·사용 이력 보완, S2는 기존 구성 기준에 연결한 제품 등록이다. 현재는 동료의 Agent 개발을 위한 실행 가능한 기준 버전이며 운영 서비스 전체의 완성본은 아니다.

## 시작

Python 3.10+와 PostgreSQL + pgvector가 필요하다. SQLite·메모리 저장 fallback은 없다. 아래 Compose는 로컬 DB를 편하게 시작하는 방법이며, 이미 pgvector가 설치된 PostgreSQL 서버가 있으면 `DATABASE_URL`만 연결해도 된다. 별도 Agent 서버·MCP는 필요 없다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
# 새 환경에서만 복사. 기존 .env.local이 있으면 아래 DB 항목만 추가한다.
cp -n .env.example .env.local
```

`.env.local`의 `POSTGRES_PASSWORD`와 `DATABASE_URL` 안의 비밀번호를 같은 로컬 값으로 바꾼다. 값은 Git·채팅에 넣지 않는다. 예시의 `replace-local-password`는 배포용 비밀값이 아니다.

```dotenv
POSTGRES_PASSWORD=replace-local-password
DATABASE_URL=postgresql://ontologics:replace-local-password@127.0.0.1:55432/ontologics
ONTOLOGICS_DB_SCHEMA=ontologics
```

```bash
docker compose --env-file .env.local up -d --wait
python -m unittest discover -v
python -m ontologics.demo
python -m examples.cq_check
python -m examples.agent_tools
```

테스트·데모·CQ 확인에는 모델 키가 필요 없다. 테스트는 `test_<UUID>` 스키마만 만들고 정리한다. 필요하면 `TEST_DATABASE_URL`로 별도 시험 DB를 지정한다. 전체 테스트는 실제 PostgreSQL을 사용하며 DB가 없을 때 성공으로 건너뛰지 않는다. CI도 같은 pgvector 이미지에서 실행한다.

데모는 **실제 ODS 파싱 → L1 등록 초안 → 개발용 사람 역할 검토 → 승인 반영 → 조사 → 보완 요청·수정 → 재승인 → 같은 조건 재조회**를 실행한다. 승인은 시험용 신원으로 실행되며 실제 운영 승인 이력이 아니다.

데모와 CQ 명령은 매번 새 `demo_<UUID>` 스키마를 만들고 이름을 출력한다. 특정 실행을 보존하려면 `python -m ontologics.demo --schema demo_team_001`처럼 아직 비어 있는 이름을 지정한다. 공통 앱은 `Service(principals=...)`로 환경의 `ONTOLOGICS_DB_SCHEMA`에 연결한다. `Service(database=DatabaseSettings(...), principals=...)`로 명시할 수도 있다. URI나 비밀번호를 출력·공유하지 않는다.

`docker compose --env-file .env.local stop`은 DB만 정지하고 데이터를 보존한다. 데이터는 Git 밖의 Docker named volume에 있다. 기존 SQLite 파일은 삭제하거나 자동 이전하지 않는다. 대표 그래프는 보존된 원본과 기존 승인 데모로 재생성하며, 과거 SQLite 실행 로그·미승인 초안은 새 DB로 복사하지 않는다.

## 확인할 결과

- 실제 L·L1 WROOM BOM: 각각 74행, 위치 128개. 비교되는 원문 위치는 R50, R51, R6, U2다.
- U104의 제거 기록 보완 전: A와 B 모두 `needs_confirmation`.
- 제거 및 완전 이력 근거를 승인한 후: A=`excluded`, B=`included`.
- U106은 계속 `needs_confirmation`, 충분한 부재 근거가 있는 U107은 `excluded`.
- 데이터 버전은 1(개발 시드) → 2(L1 승인 등록) → 3(U104 승인 보완).

일반 실행 결과는 Git에서 제외된 `artifacts/local/demo_result.json`, CQ 결과는 `artifacts/local/cq_postgres.json`에 기록된다. 공유 검증은 [CQ 결과](artifacts/cq_postgres_verification.json)에서 확인한다. T-box와 인계 A-box를 의도적으로 갱신할 때만 `python -m ontologics.demo --export-graph`를 사용한다. 대상은 `ontology/tbox.ttl`과 `data/initial_graph.jsonld`다. 마지막 파일은 초기 인계용 그래프이며 시나리오 실행 후의 버전 3이다. 실행마다 ID·실행 시각은 달라질 수 있지만 업무 판정은 같다. 원본에서 `data/catalog.json`을 다시 만들려면 `python3 -m ontologics.ingest`를 실행한다.

## S1·S2 웹 통합 — 개발 신원으로 실행

Python CommonHost와 기존 공통 도구를 같은 origin의 HTML·CSS·JavaScript 화면에 연결했다. 별도 프런트엔드 빌드가 없다. **localhost 개발 서버이며 실제 로그인·운영 배포는 미완료**다. 상단의 개발 사용자 선택 박스에서 Alice·Bob·조회 전용 사용자를 바꿀 수 있다. 서버의 허용 목록을 선택하는 기능이며 권한 자체를 편집하지 않는다. 요청마다 서버 IdentityProvider가 신원을 고정하고 도구 입력·Agent는 작성자/권한을 지정하지 않는다.

기존 DB를 초기화하지 않고 새 schema를 사용한다. `--reserve-l1`은 L과 가상 운영 기록만 등록된 기준을 준비하므로 L1은 S2 승인 경로로 등록해야 한다. 옵션을 생략한 기존 시드는 L/L1을 모두 포함하며 S2 최초 등록의 증거가 아니다.

```bash
python -m ontologics.web seed --development --reserve-l1 --schema web_s1_s2_team_001 --env-file .env.local
# 서버 한 개에서 상단 선택 박스로 사용자를 전환한다. --user는 최초 기본 사용자다.
python -m ontologics.web serve --development --user alice --port 8000 --schema web_s1_s2_team_001 --env-file .env.local
```

`http://127.0.0.1:8000`에서 Alice(조회·초안·검토), Bob(조회·검토), 조회 전용(조회)을 선택한다. Alice로 초안 작성 후 Bob으로 전환하고 업무 현황에서 같은 초안을 열어 승인할 수 있다. 작성자는 바뀌지 않고 Alice 자기 승인은 차단된다. 사용자 전환 시 홈을 다시 불러오며 이전 사용자의 초안/조사·요청 키는 사용자별로 분리해 보존한다. 개발 사용자를 자유롭게 선택할 수 있으므로 실제 로그인이나 서로 다른 실제 사람의 승인 검증은 아니다. 127.0.0.1·정확한 Host·Origin·쿠키·CSRF 검사를 사용한다. 별도 브라우저는 독립적으로 선택하며 같은 브라우저의 다른 탭은 전환 후 새로고침해야 한다. 기존 두 포트 실행 방식도 사용할 수 있다. 실제 인증은 `IdentityProvider.resolve/lookup`과 검증된 요청 문맥에 연결해야 한다.

- **S2:** 제품 구성·BOM 등록에서 `data/sources/ESP32-PoE_Rev_L1-BOM-WROOM.ods` 파일을 접수한다. 실제 바이트 SHA-256으로 개정을 식별하고 74행·128위치·실장 미확인 12행을 표시한다. L과 비교한 변경 위치는 R50·R51·R6·U2다. 등록 초안 저장 후 Bob이 업무 현황의 초안을 열어 원본·차이·검증 결과를 보고 보완 요청/반려/승인한다. 승인된 정확한 버전의 반영 결과를 확인한 뒤 L1 구성을 다시 조회한다. 파일명만 바꾸거나 새로운 부품 조합을 올려도 근거 없는 구성으로 등록하지 않는다.
- **S1:** 로트 장착 이력 조회에서 A/B 조건을 고정한다. 보존된 U104 시연 보완값 또는 개별 보드·부품 로트 이력 화면의 사용자 제출 가상 기록 JSON으로 보완안을 저장한다. Bob 승인 후 최초 조건 그대로 이전/이후 데이터 버전을 비교한다. U104는 A 추가 확인→조건상 제외, B 추가 확인→장착 이력 확인이며 U106 미확인은 유지한다. 포함은 불량 확인, 제외는 안전 판정이 아니다.
- **공통:** 업무 현황은 최근 초안/확정 버전, 이력 화면은 보드·로트·장착·제거·완전 이력, 기술 문서·근거 조회는 승인 버전의 키워드/종류/ID 탐색을 제공한다. 새 근거는 초안의 정확한 버전에서 원본 해시를 확인할 수 있다. 초안 작성·검토 승인·확정 반영 상태를 구분한다.

AI 업무 지원은 기존 `.env`/`.env.local` 또는 `--model-env-file`의 `OPENAI_API_KEY`와 고정 모델 설정을 재사용한다. **설정됨은 실제 통신 성공과 다르다.** Supervisor는 명확한 등록/장착 업무 표현을 공통 규칙으로 제한한 뒤 홈의 요청을 한 전문 역할로 인계하고, Product는 접수 후보 설명·명시적 초안 저장, Quality는 누락 조건 질문·조건 제안·A/B 조회/비교, Knowledge는 관계 조회·문자열/의미 검색을 수행한다. 직접 조회와 Agent 도구의 반환값을 대조하며, 이 일치 검사가 자유 설명 전체의 사실성 평가를 대신하지 않는다. Quality의 판정·근거 설명, 의미 검색의 검증된 근거 선택, 초안 저장 결과는 원본 필드로 서버가 구성한다.

- 원본 접수·비교 후 확인란을 선택하고 AI 패널에 “원본 근거와 미확인 상태를 유지해 등록 초안을 저장해줘”를 입력한 뒤 **AI 등록 초안 저장 요청**을 누른다. 직접 저장과 같은 요청 키·후보·검증을 사용한다. 모델 후속 처리 실패에도 저장된 정확한 초안은 표시하며 같은 키로 재시도한다. 승인·확정은 기존 사람 검토 경로다.
- 조회 전 “U104와 U106의 L07 장착 이력을 조사해줘”는 빠진 시각을 질문한다. `A 2026-09-20T10:00:00+09:00, B 2026-09-01T00:00:00+09:00 부터 2026-09-21T00:00:00+09:00`까지 명시하면 조건 제안을 검토해 **조사 조건에 적용 → 장착 이력 조회**한다. 날짜만으로 시간대를 추정하지 않는다. 이미 조회한 화면은 고정 조건·버전의 설명을 요청한다.
- 기술 문서·근거 조회에서 문자열/의미 검색을 선택하고 “L1 WROOM 보드의 이더넷 PHY LAN8710 부품 근거”처럼 요청한다. 의미 검색은 아래 색인 준비가 필요하다. 각 답에 대상·버전·원본 위치와 검색 범위가 표시된다. 지속 대화 저장·임의 PDF/OCR·범용 추출 정확도는 아직 완료하지 않았다.

일반 가상 이력 파일은 UTF-8 JSON `{origin:"synthetic",authored_at:"명시적 시각+offset",notice:"시연 근거 설명",records:[...]}`다. records는 기존 SCHEMA의 ProductUnit/ComponentLot/Installation/HistoryCoverage/InventoryMovement 필드(id/type 포함, origin/evidence 제외)를 사용한다. 호스트가 새 SourceDocument/Evidence·해시·JSON 위치를 붙인다. 실제 회사 운영 자료나 고장/안전/가동 필드는 허용하지 않는다. 원본은 Git 제외 `data/receipts/`에 무변경 보존하고 DB와 함께 별도 백업해야 한다. 파일 접수·형식 검증은 사실 확인이나 승인과 다르다.

기본 네 Agent·여섯 화면·공통 기반은 프로젝트 담당자가 구현하고 동료는 이후 추가 Agent를 같은 호스트 경계에 연결한다. HTTP 계약은 [TOOLS](docs/TOOLS.md#개발용-http-api와-화면), 실제 검증 범위·제한은 [PROJECT_STATUS](PROJECT_STATUS.md)를 따른다. 영향 검사 후 최종 `python -m unittest discover -v`를 실행한다. 모델 대역·실제 브라우저·실모델 검증은 구분한다.

## 개발용 사용자 신원과 공통 호스트

아래 Python 예제는 로그인 없이 호스트가 지정한 개발 사용자로 등록·검토·반영 경로를 실행한다. **신원 연결과 권한 규칙의 개발 검증이며 실제 인증 완료가 아니다.** 브라우저 연결은 위 웹 통합 절차를 사용하며 실제 로그인·인증 세션은 아직 제공하지 않는다.

```bash
python3 -m examples.host_workflow
# 다른 로컬 설정 파일을 사용할 때
python3 -m examples.host_workflow --env-file /path/to/.env.local
python3 -m unittest tests.test_host -v
```

예제는 모델 키 없이 새 `host_demo_<UUID>` PostgreSQL 스키마에서 실행된다. 개발 사용자 `dev-alice`의 Product 초안은 `author=user:dev-alice`로 기록된다. Alice의 자기 승인은 `SELF_APPROVAL_FORBIDDEN`, 미승인 반영은 `APPROVAL_REQUIRED`, 보완 전 버전 반영은 `SUPERSEDED_DRAFT`다. 다른 개발 사용자 Bob의 검토 뒤 정확한 버전만 반영되며 L1 74행 등록, U104 보완 후 A 제외/B 포함, U106 미확인 유지와 재시도 `IDEMPOTENT_REPLAY`를 확인한다. 예제의 개발 시드와 Bob의 검토 대역은 실제 자료 접수·사용자 인증·운영 승인 완료 근거가 아니다.

동료는 [host.py](ontologics/host.py)의 `CommonHost.request()`로 요청 신원을 한 번 고정하고 `RequestSession.agent('product'/'quality'/'knowledge'/'supervisor')` 또는 `run_agent()`를 사용한다. `review()`는 사람의 호스트 동작, `apply_approved()`는 서버 워크플로 동작이며 Agent에 노출하지 않는다. 신원 공급자는 [identity.py](ontologics/identity.py)의 `resolve(context)`·`lookup(subject)` 접점으로 교체한다. 개발 사용자 선택은 명시적 개발 HTTP 경로에만 있으며 운영 인증으로 사용하지 않는다. 호출 예제·권한·오류 경계는 [TOOLS](docs/TOOLS.md#개발용-사용자-호스트)를 따른다.

## 실제 LLM 실행과 동료 개발 시작

공통 provider는 OpenAI Responses API와 **`gpt-5.4-mini-2026-03-17` 한 모델**을 사용한다. Python 표준 라이브러리로 연결하며 추가 SDK 설치는 없다. API 호출에는 계정 사용량이 발생한다. 예제는 공개 OLIMEX 행 정보와 가상 운영 기록을 전송한다.

`.env.example`을 참고해 로컬 `.env.local`에 다음 값을 설정한다. API 키는 각자 로컬에서 입력하며 채팅·Git에 넣지 않는다.

```dotenv
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4-mini-2026-03-17
OPENAI_TIMEOUT_SECONDS=30
OPENAI_MAX_OUTPUT_TOKENS=4096
```

```bash
python3 -m examples.llm_agents --role both
python3 -m examples.llm_agents --role product
python3 -m examples.llm_agents --role quality
```

한 명령만 골라 실행하면 된다. 설정 우선순위는 프로세스 환경변수 → `.env.local` → `.env`다. `--env-file 경로`를 쓰면 해당 파일과 프로세스 환경만 사용한다. 모델·DB 설정은 `KEY=value` 데이터로 읽으며 셸로 실행하지 않는다. 모델명을 다르게 지정하면 명시적으로 실패한다.

매 실행은 PostgreSQL에 역할별 새 `llm_<role>_<UUID>` 스키마를 만들고, `artifacts/llm_runs/run-<시각>-<ID>/report.json`에 그 이름과 결과를 남긴다. 보고서에는 모델 호출 상태·토큰 수·실제 도구 입력/출력·최종 답·자동검사·원본 URL/위치가 있다. 키와 HTTP 오류 원문은 기록하지 않는다. 로컬 실행 DB·보고서·환경 파일은 Git에서 제외한다. 재실행은 별개의 시드/업무 예제이며 운영 변경의 자동 재시도가 아니다.

| 실행 | 기대 결과 | 동료가 수정할 위치 |
| --- | --- | --- |
| Product | 이미 파싱한 L1 U4·R50 두 행의 `awaiting_review` 초안과 검증 결과. R50은 `population=unknown`. 확정 데이터 버전 1 유지 | [examples/llm_agents.py](examples/llm_agents.py)의 `PRODUCT_PROMPT`, `product_task`, `evaluate_product` |
| Quality | U104, L07, 버전 2→3 비교. A 미확인→제외, B 미확인→포함. 실제 BOM 근거와 가상 이력 근거 분리 | 같은 파일의 `QUALITY_PROMPT`, `quality_task`, `evaluate_quality` |
| 공통 연결 | 신원 고정·도구 검증·실행 기록·제한된 재시도 | [runtime.py](ontologics/runtime.py), [openai_provider.py](ontologics/openai_provider.py), [tool_specs.py](ontologics/tool_specs.py). 역할별 복제 없이 재사용 |

Quality 버전 2/3은 **모델 호출 전 호스트가 기존 데모 승인 경로로 준비한 시험 데이터**다. LLM 작성 초안을 자동 승인하는 기능이 아니다. Product 예제는 대표 두 행의 초안 작성까지이며, 전체 S2 등록·보완·승인 흐름은 위 오프라인 데모에서 확인한다.

PostgreSQL 전환의 실제 API 결과와 실패·수정 이력은 [검증 요약](artifacts/llm_verification.json)에 기록한다. 자동검사는 조건·상태·근거 ID·미확인 필드·확정 데이터 불변을 검사하며 일반적인 Agent 정확도 점수가 아니다. 모델이 원본 PDF를 직접 검토한 결과도 아니다. 정확한 시각·offset은 task/tool trace를 기준으로 표시한다.

오류가 나면 `report.json`의 `roles[].runtime.error`와 `roles[].provider_calls`를 확인한다. 실행 전 설정 오류는 최상위 `error`에 있다. 키 누락·인증·모델 접근·입력 오류는 즉시 종료하고, timeout·연결 실패·429·5xx만 기존 런타임에서 최대 2회 시도한다. provider는 `parallel_tool_calls=false`로 한 번에 한 도구를 요청하며, 예제는 최대 12모델 단계다. 공통 런타임의 배치 검증·호출 제한은 유지한다. 성공한 초안은 이후 실패해도 미승인 상태로 남는다. 오류 코드·구체적 입출력은 [TOOLS](docs/TOOLS.md#실모델-예제의-입출력)에서 확인한다.

## pgvector 근거 검색을 연결하는 위치

`ontologics/retrieval.py`의 `EvidenceIndex`는 승인된 데이터 버전의 Evidence·SourceDocument·연결된 사실을 텍스트로 제공하고, 호스트가 만든 임베딩을 PostgreSQL `vector` 열에 저장·검색한다. 모델 이름·차원·내용 해시·데이터 버전을 구분하고 실제/가상 필터와 원본 locator를 반환한다. 원본은 파일로 보존하고, 그래프·초안·승인·실행 기록·벡터의 영속 저장은 PostgreSQL 한 곳에 둔다.

`python -m examples.cq_check`의 3차원 수치 벡터는 기존 SQL 검사다. 실제 의미 검색은 `OpenAIEmbeddings`의 고정 `text-embedding-3-small`/1536차원으로 문서와 질문을 변환한다. 기존 LLM과 목적이 다른 임베딩 모델이며 동적 라우팅은 없다. [공식 Embeddings API](https://developers.openai.com/api/reference/resources/embeddings/methods/create)를 따른다.

```bash
# 위 S2를 승인한 실제 데이터 버전으로 지정한다. 예시 버전 2를 무조건 사용하지 않는다.
python -m examples.index_evidence --schema web_s1_s2_team_001 --version 2 --env-file .env.local --model-env-file .env.local
```

별도 키 변수 없이 기존 `OPENAI_API_KEY`를 사용한다. 원본 text는 DB 트랜잭션 밖에서 API에 보내며 반환 모델·차원·유한값·입력 순서를 검사한다. `selection_complete`는 선택 범위, `snapshot_complete`는 전체 Evidence 범위의 색인 완료다. 8192 UTF-8 bytes 초과 근거는 자르지 않고 `unindexed`에 남긴다. 재실행은 같은 모델/버전/hash의 기존 캐시를 보존하고 누락분만 처리한다. 새 승인 버전은 따로 색인해야 하며 미색인 버전을 이전 버전으로 자동 대체하지 않는다.

의미 검색은 `semantic_search(query, origin, version)` 공통 도구로 연결된다. `index` 권한·DB·provider 객체는 Agent에 공개하지 않는다. `candidate_only=true`와 부분 색인 경고를 유지하며 유사도는 부품 동일성·장착 판정·승인 또는 사실 확률이 아니다. 작은 데이터에 맞춘 정확 cosine 검색이며 ANN 최적화·범용 RAG 품질 보장은 별도다.

## 동료의 확장 Agent 연결

기본 네 역할·여섯 화면·공통 기반은 프로젝트 담당자의 범위다. 동료는 기존 역할을 덮어쓰지 않고 `AgentRegistration(name, instructions, allowed_tools)`를 호스트 시작 코드에서 등록한다. `CommonHost(..., agent_registrations=(registration,))` → 요청 신원 고정 → `request.run_agent(registration.name, provider, messages, config=...)` 순서다. 도구는 기존 `AgentGateway.ALLOWED`의 부분집합이며 등록 자체가 사용자 권한을 늘리지 않는다. 승인·반영·seed·색인 도구는 등록할 수 없다. HTTP 요청이나 모델 출력으로 역할을 등록하지 않는다.

```bash
python -m examples.extension_agent --schema web_s1_s2_team_001 --version 2 --env-file .env.local
python -m unittest tests.test_agent_extensions -v
```

[extension_agent.py](examples/extension_agent.py)는 연결 계약을 보여주는 모델 대역 예제다. 버전 계약을 사용하는 새 역할은 [역할별 예제 폴더](examples/agents/)를 시작점으로 삼는다. `model_reader.py`는 한 번의 제품 조회, `history_reader.py`는 A→B 순차 조회이며 각 파일에 고유 INPUT/OUTPUT·INSTRUCTIONS·messages·결과 검증기를 둔다. 공통 신원·실행·승인 로직은 복제하지 않는다.

1. 고유 역할 모듈에 `AgentSpec`과 `messages(task)`, 필요하면 `validate_result(value, task, tool_results)`를 정의한다. 실제 도구 결과와 일치해야 하는 값은 검증기에서 대조한다.
2. 신뢰된 호스트 시작 코드가 `AgentRegistration.from_spec(SPEC)`으로 등록하고 `AgentTask(payload, PinnedContext(...))`를 만든다. 요청 사용자는 IdentityProvider를 통해 고정한다.
3. `request.run_task()`로 실행한다. 같은 고정 모델을 쓸 때는 기존 `OpenAIProvider(settings, SPEC.requested_tools)`를 주입한다. 기본 예제는 모델 대역이며 유료 호출이 없다.
4. 아래 계약 검사를 실행하고 담당 역할의 사례를 추가한다. 웹 라우팅은 담당자가 명시적으로 연결하며 자동 플러그인 등록은 지원하지 않는다.

```bash
python -m examples.extension_agent --typed --flow model --schema web_s1_s2_team_001 --version 2 --env-file .env.local
python -m examples.extension_agent --typed --flow history --schema web_s1_s2_team_001 --version 2 --env-file .env.local
python -m unittest tests.test_agent_contracts tests.test_agent_extensions tests.test_harness -v
```

실제 존재하는 확정 버전을 지정한다. 기대 결과는 `runtime.ok=true`, `schema_version=agent-result-1`, 고정 context와 검증된 `evidence_refs`, `error=null`이다. history의 `by_mode`는 같은 버전 공통 investigate의 A/B 결과와 같고 U106은 미확인으로 남는다. 모형/가상 이력 데이터가 없는 DB에는 위 예제를 그대로 적용하지 않는다. 고유 기능 자율 변경과 공통 검토 경계는 [Agent 지침](docs/AGENT_RUNTIME.md#확장-agent의-수정-경계), 저장된 작업의 호환 확인은 [TOOLS](docs/TOOLS.md#공개-계약과-저장된-작업의-호환성)을 따른다.

## 공통 실행 계약과 격리 파서 — 로컬 개발 보완

기존 `run_agent`와 네 화면 Agent의 검증은 유지한다. 요청·하위 Agent는 `ExecutionBudget`의 모델/도구/repair 횟수와 deadline을 공유한다. **복구 반복 기본값 0은 읽기 작업의 LLM 응답 형식 수정(`max_repairs`)**이며 자동 재검색을 뜻하지 않는다. 설정 허용 범위는 0~2회지만 공통 잔여 한도를 넘지 못한다. 권한·버전·근거·의미 오류는 수정 반복으로 통과시키지 않는다. 일시 장애 재시도·읽기 재개·멱등 처리와의 차이는 [TOOLS](docs/TOOLS.md#반복과-복구의-구분)에 정리했다. deadline/취소는 동기 호출 전후 검사이며 실행 중 네트워크 호출의 강제 종료가 아니다.

입출력 계약이 필요한 확장 Agent는 `AgentSpec`의 이름·버전·PayloadSchema·필요 도구·한도를 정의하고 `AgentRegistration.from_spec()`으로 등록한다. `AgentTask`와 `PinnedContext`를 `request.run_task()`로 전달하는 예제는 다음과 같다. 기존 등록 방식도 사용할 수 있다.

```bash
python -m examples.extension_agent --typed --schema web_s1_s2_team_001 --version 2 --env-file .env.local
```

`resume_task()`는 반환된 읽기 작업을 동일 조건으로 다시 시작하는 제한된 접점이다. 신원·현재 버전·계약·provider·프롬프트·잔여 한도를 재검사한다. 중간 강제 종료의 자동 복구나 Product 쓰기의 임의 재생은 지원하지 않는다. 상세 계약은 [TOOLS](docs/TOOLS.md#공통-harness와-버전-agent-계약)를 따른다.

파일 파싱을 격리하려면 검토된 파서 이미지와 별도 로컬 controller를 실행한다. controller만 Docker를 제어하며 웹에는 제한된 Unix socket client만 전달한다.

```bash
docker build -f deploy/docker/sandbox.Dockerfile -t ontologics-parser:dev .
mkdir -p /tmp/ontologics-parser-dev
chmod 700 /tmp/ontologics-parser-dev
docker image inspect ontologics-parser:dev --format '{{.Id}}'
# 위 명령의 sha256:... 값을 사용한다. 별도 터미널에서 controller를 유지한다.
python -m ontologics.sandbox --socket /tmp/ontologics-parser-dev/parser.sock --image-id sha256:확인한이미지ID --docker /usr/local/bin/docker
# 다른 터미널에서 기존 개발 DB에 연결한다.
python -m ontologics.web serve --development --user alice --port 8000 --schema web_s1_s2_team_001 --env-file .env.local --sandbox-socket /tmp/ontologics-parser-dev/parser.sock
```

Docker 실행 파일 위치는 설치 환경에 맞춘다. 이미지 ID·socket·mount·명령은 신뢰된 시작 설정이며 사용자가 화면/Agent로 지정할 수 없다. 설정된 controller가 없거나 실패하면 호스트 파서로 우회하지 않는다. 기존 비격리 접수는 읽을 수 있지만 격리 모드의 새 비교·초안에는 원본 재접수가 필요하다. 이미 저장된 초안의 같은 요청 키 재생은 중복 생성하지 않는다. 미설정 방식은 기존 개발 호환 경로로, ODS는 원본 manifest 해시 대조, 이력 JSON은 제출 바이트 해시 계산과 허용 스키마 검증을 사용한다. 이 경로는 격리 실행으로 표시하지 않는다.

Sandbox는 네트워크 차단·non-root·읽기 전용 루트/입력·CPU/메모리/PID/시간/출력 제한을 적용한다. Docker Desktop 검증이며 rootless·호스트 LSM·운영 프로세스 격리 완료를 의미하지 않는다. 개인 비교 평가·정답·원시 결과는 저장소 밖 `EVAL_HOME`에 두며 Git·CI artifact·외부 추적 서비스로 자동 전송하지 않는다.

## 저장소 공유 상태

비공개 저장소는 [Ontologics-2Zo/ontologics](https://github.com/Ontologics-2Zo/ontologics), clone 주소는 `https://github.com/Ontologics-2Zo/ontologics.git`이다. PostgreSQL 전환 PR #1, 목표·완료 기준 문서 PR #2와 첫 개발용 통합 PR #3은 main에 병합됐다. 2026-10-27 확인한 main은 **`7c5e2d9`**이며 개발용 호스트·S1·S2·네 Agent를 포함한다. 이번 Harness·계약·Sandbox 보완의 리뷰 브랜치는 **`codex/harness-security-eval`**이다. 실제 push·PR·CI 상태는 해당 원격 브랜치와 PR에서 확인한다. 이 기준 버전은 실제 인증·운영 백업·AWS·RDF/Neo4j 완료를 의미하지 않는다.

저장소 접근 권한이 있는 팀원은 main의 통합 기준을 받아 기존 실행 순서를 따른다. 미커밋 작업이 있으면 먼저 보존하고, 위의 신규 Harness·Sandbox 명령은 후속 변경이 공유된 뒤 사용한다. 실행한 기준 커밋은 `git rev-parse HEAD`, 검증 범위와 남은 작업은 [PROJECT_STATUS](PROJECT_STATUS.md)로 확인한다.

## 개발 기준

| 읽을 자료 | 용도 |
| --- | --- |
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | 현재 완료 근거, 제한, 동료가 시작할 범위, 다음 행동 |
| [docs/SERVICE_SPEC.md](docs/SERVICE_SPEC.md) | PRD·CQ·실제/가상 경계·확정한 O01~O08 |
| [docs/TOOLS.md](docs/TOOLS.md) | 호출 계약, 그래프 구조, 오류·승인·상태·통합 접점 |
| [docs/AGENT_RUNTIME.md](docs/AGENT_RUNTIME.md) | 서비스 Agent의 역할 지침과 권한 경계 |
| [AGENTS.md](AGENTS.md) | 저장소를 변경하는 개발용 지침 |
| [data/sources/manifest.json](data/sources/manifest.json) | 고정 커밋·정확한 URL·수집 시각·해시·원본 검증 기록 |
| [docs/reference](docs/reference) | 사용자 제공 v2 문서 원문 보존 |

## 자료 사용과 한계

OLIMEX ESP32-POE 공식 저장소 커밋 `deb38a377d50eadb04ddc7118a17a5ae58b1ee53`의 원본을 보관한다. 하드웨어·BOM의 Apache-2.0 라이선스 및 공식 README의 자료별 라이선스 설명은 `data/sources/`에서 확인한다. 문서는 CC BY-SA 3.0이며 수정하지 않은 원본과 출처를 보존했다. 현재 제품 페이지는 수집 시점 스냅샷이며 그 사양을 과거 개정에 일괄 적용하지 않는다.

ODS 시트명 오류는 대응 PDF 표제와 모듈 행으로 확인하고 경고로 보존했다. `NA`·`NS`·`Opened` 의미는 확정하지 않았으므로 L 11행, L1 12행은 실장 상태가 `unknown`이다. PDF 전체 148행을 각각 시각 대조한 것은 아니다. 부품의 개정 간 동일성·대체 호환성·설계 변경 이유를 추정하지 않는다.

S1/S2 개발 웹과 네 역할의 제한된 업무 경로, 접수 후보의 AI 초안 저장·의미 검색을 연결했다. 전체 Agent의 범용 추출/대화 품질·운영 사용자 인증·실제 운영 승인은 미완료다. 파일·Python 객체에 접근할 수 있는 로컬 개발자를 보안적으로 격리하는 시스템도 아니다.
