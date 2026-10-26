# Ontologics 공통 기반 v0.4.0 — PostgreSQL + pgvector

실제 OLIMEX 사양·BOM을 제품·구성으로 등록하고, 가상 개별 보드·로트 이력과 연결해 조사·보완하는 공통 라이브러리다. S1은 문제 상황 분석 및 생산·사용 이력 보완, S2는 기존 구성 기준에 연결한 제품 등록이다. 현재는 동료의 Agent 개발을 위한 실행 가능한 기준 버전이며 운영 서비스 전체의 완성본은 아니다.

v0.4.0은 B-CQ01의 계약·회차에 B-CQ02의 완성 보드 재고·생산 작업·배정·운송과 확인 범위를 연결해, 명시한 버전·관측 시점의 6상태 물량 현황을 조회한다(`graph-0.3`). B-CQ01과 별도로 B-CQ02의 읽기 전용 현황 역할도 기존 런타임에 연결한다. 생산·도착 예측과 납품 가능성 판단은 후속 범위다. 기존 장착 이력·승인·PostgreSQL 계약은 유지한다.

## B-CQ02 물량 현황 예제

```bash
python -m examples.b_fulfillment --output artifacts/local/b_cq02_task08/example.json
```

LLM 없이 새 PostgreSQL 스키마에서 초안·검증·개발용 사람 검토 대역·반영·버전별 조회를 실행한다. 가상 B-01 1회차의 10시 현황은 납품 완료100·운송 중150·창고250·생산 중300·준비 완료100·대기100대다. 잔여 약속900대와 미배정0대를 구분한다. 자료가 없으면 0 대신 미확인으로 반환한다. [자료 규칙·도구·제한](docs/TOOLS.md#b-cq02-물량-현황-조회)을 따른다. 이 예제는 실제 업무 승인이나 Agent 모델 검증이 아니다.

현황 Agent 연결은 기본적으로 API 없는 대역 실행이다.

```bash
python -m examples.b_fulfillment_agent --provider scripted --output artifacts/local/b_cq02_task09/scripted-new.json
```

실제 호출은 같은 명령에 `--provider openai`와 새 출력 경로를 명시한다. 고정 모델·정상 사례 하나·최대 API4회·재시도 없음·요청당 출력1024토큰으로 제한한다. 원문/근거를 읽는 모델 설명을 검사하며 수량은 공통 도구가 계산한다. 이전 보고서는 덮어쓰지 않는다. [연결·평가 계약](docs/TOOLS.md#b-cq02-agent-연결과-평가)을 따른다.

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

## B-CQ01 계약 약속 확인

```bash
python -m examples.b_contract
```

새 `b_cq01_<UUID>` PostgreSQL 스키마에서 기존 실제 설계 시드 → 가정 계약 초안·검증 → 승인 전 반영 차단 → 개발용 사람 역할 검토 → 버전 2 등록 → 약속·근거 조회를 실행한다. 모델 키 없이 공통 함수를 직접 호출하는 예제이며 실제 운영 승인이나 LLM 실행이 아니다.

기대 답은 가상고객사 A에 ESP32-POE Rev L WROOM 보드 1000대를 `2026-10-27T15:00:00+09:00`까지 고객 창고에 도착시키는 약속이다. 현재 이행 가능성은 `fulfillment_assessed=false`다. 해시가 고정된 원문과 근거 메타데이터, 독립 정답은 [TOOLS](docs/TOOLS.md#b-cq01-계약-접수와-약속-조회)에 연결한다. 수량·기한 누락/모호함은 보완, 잘못된 숫자/참조는 검증 실패로 처리한다.

보고서는 Git 제외 `artifacts/local/task08/b_cq01.json`에 남는다. 필요할 때만 `python -m examples.b_contract --export-graph`로 [B 대표 그래프](data/b_contract_graph.jsonld)와 공통 T-box를 재생성한다. 기존 `data/initial_graph.jsonld`·OLIMEX 원본·부품 입출고 예제는 보존한다. 첫 추출 형식은 고정된 Markdown 표의 항목 1개/회차 1개이며 범용 계약서 파서는 아니다.

B 역할 연결 예제는 다음과 같다. 기존 런타임·provider를 재사용하며 별도 서버가 필요하지 않다.

```bash
python -m examples.b_agent --provider scripted
python -m examples.b_agent --provider openai --output artifacts/local/task09/b_agent_live.json
```

기본 `scripted`는 연결 시험용 대역이다. 실제 LLM 품질을 검증하지 않는다. `openai`는 로컬 키를 검증한 뒤 기존 고정 모델을 실제 호출한다. `--mode intake`는 원문·기존 구성·근거 조회 → 계약 3노드 초안 → 검증 → 사람 검토 대기, `--mode promise`는 개발용 호스트가 미리 확정한 자료의 회차 → 약속·근거·미확인 설명이다. `both`가 기본이며 두 모드는 별도 새 DB 스키마를 사용한다. 모델 초안을 자동 승인하지 않는다.

결과는 Git 제외 `artifacts/local/task09/b_agent.json`에 기록된다(`--output`으로 변경 가능). 실모델 실행에는 별도 경로를 권장한다. 키 누락은 DB 접수·API 호출 전에 `OPENAI_KEY_MISSING`으로 중단하며 대역 통과와 실모델 완료를 구별한다. 자동 평가는 원문 파서·독립 정답·실제 도구 기록·저장된 초안을 대조한다. 자유문 설명 전체와 임의 계약 PDF/OCR의 품질을 보증하지 않는다. 모드별 도구 권한과 입력/출력은 [TOOLS](docs/TOOLS.md#b-agent-연결-예제-task-09)를 따른다.

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

`python -m examples.cq_check`에 실행 가능한 `documents → index → search` 예제가 있다. 이 예제의 3차원 벡터는 SQL 동작을 확인하는 시험값이다. **임베딩 API, 자연어 의미 검색, RAG 답변 품질까지 검증한 것은 아니다.** 동료는 같은 임베딩 모델로 문서와 질문을 벡터화해 이 접점에 연결할 수 있다. LLM에게 벡터를 직접 만들어 내도록 시키지 않는다.

`index`는 호스트의 별도 `index` 권한이며 AgentGateway에 공개하지 않는다. `search`도 호스트 retriever가 신원을 고정해 감싼 뒤 Agent에 연결한다. 기존 Product/Quality 도구 목록은 그대로다. 검색 결과의 `candidate_only=true`는 근거 후보임을 뜻하며, ID 동일성·A/B 판정·승인을 대체하지 않는다. 작은 데이터에 맞춘 정확 검색이며 ANN 성능 최적화는 하지 않았다.

## 저장소 공유 상태

비공개 저장소는 [Ontologics-2Zo/ontologics](https://github.com/Ontologics-2Zo/ontologics), clone 주소는 `https://github.com/Ontologics-2Zo/ontologics.git`이다. 2026-10-22에 `main`을 push했고 `origin/main`을 추적한다. 공통 기반 구현 기준 커밋은 `07eddb82a8c15dcde9965c3de4345db2a932605a`, 초기 v0.1.0 기준은 `2fdc0159c8cf5ba694e5d8bd51136d77b1bc42b4`다. PostgreSQL 전환은 `codex/postgresql-pgvector` 브랜치의 PR로 제공한다. 검토·병합 전 main은 기존 기준을 유지한다. 사용 중인 기준 커밋은 `git rev-parse HEAD`로 확인한다. 비공개 저장소 접근 권한이 있는 동료가 clone할 수 있다.

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

실제 LLM의 최소 도구 호출 예제는 연결했다. 전체 Agent의 추출·대화 품질, 업무 UI·운영 사용자 인증·실제 사람 승인은 미완료다. 파일·Python 객체에 접근할 수 있는 로컬 개발자를 보안적으로 격리하는 시스템도 아니다.
