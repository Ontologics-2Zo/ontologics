# PROJECT_STATUS

기준일: **2026-10-23, Asia/Seoul**. 공통 코드·도구 계약 **v0.2.0**, 그래프 `graph-0.1`, 시간 규칙 `temporal-0.1`.

## 현재 단계

**동료의 Agent 개발 기반을 PostgreSQL + pgvector 전용으로 전환했다.** 그래프 스냅샷·초안·승인·실행·모델 호출 기록과 벡터를 같은 PostgreSQL에 저장하며 SQLite·메모리 fallback은 없다. PostgreSQL 17.11 + pgvector 0.8.7에서 공통 CQ와 Product/Quality 최소 실모델 예제를 확인했다. 별도 Agent 서버·MCP·모델 라우터는 추가하지 않았다.

S1 문제 조사·생산/사용 이력 보완과 S2 기존 구성 기준 등록·검토·승인 전체 범위를 유지한다. O01~O08, 실제/가상 경계, A/B 의미, 작성·승인 분리와 Agent 승인 금지는 그대로다. v0.2.0은 저장소 생성자·필수 DB 설정의 변경을 알리는 계약 버전이며 제품 의미·그래프 클래스가 바뀐 것은 아니다.

## 완료 근거

| 업무 결과 | 완료 범위 | 확인 근거 |
| --- | --- | --- |
| PostgreSQL 전용 저장 | JSONB 그래프/초안/요청 재생, 승인·실행·runtime 기록, 스키마 분리, 영속 재접속 | `ontologics/database.py`, `service.py`, `runtime.py`, `compose.yaml` |
| 원자 반영·동시 요청 | DB/schema별 transaction advisory lock. 같은 요청 동시 실행은 한 초안·한 반영, 서로 다른 승인안의 동시 반영은 한 성공·한 STALE_BASE. 실패 주입 후 전체 롤백·같은 키 재시도 | `tests/test_postgres.py`, `tests/test_service.py` |
| 시간·근거·권한 | 정확한 A/B 조건·제품 집합·승인된 데이터 버전 비교, Evidence/SourceDocument 연결·해시, 자기 승인·Agent 승인 차단 | 기존 model/temporal/service 테스트, `examples/cq_check.py` |
| pgvector 저장·검색 | 모델·차원·근거 내용 hash·데이터 버전별 불변 벡터 캐시, real/synthetic 필터, 원본·locator 포함 근거 후보 반환. 확정 그래프 변경 없음 | `ontologics/retrieval.py`, PostgreSQL 실제 cosine SQL 시험, [CQ 검증 결과](artifacts/cq_postgres_verification.json) |
| 동료 실행 환경 | psycopg 3, 고정 DB 이미지·localhost 포트·볼륨·healthcheck, DB/모델 환경 예시. 테스트별 독립 schema, 실제 DB CI workflow | `README.md`, `.env.example`, `.github/workflows/tests.yml` |
| 기존 공개 자료·온톨로지 | L/L1 WROOM 각각 74행·128위치. 실제 근거 노드 460개, 데모 최종 그래프 474개. T-box·대표 그래프의 의미와 원본은 유지 | `data/catalog.json`, `data/initial_graph.jsonld`, `ontology/tbox.ttl`, `data/sources/manifest.json` |
| 실제 모델 호출 | 동일 고정 모델에서 Product 대표 2행 초안, Quality U104 A/B 보완 전후 설명. PG에 실행 기록 저장, 확정 데이터 불변 | [실모델 검증 요약](artifacts/llm_verification.json) |

전체 **96개 테스트 통과**(로컬 Python 3.13.5). 결과는 [artifacts/test_results.txt](artifacts/test_results.txt). `python -m ontologics.demo`, `python -m examples.agent_tools`, `python -m examples.cq_check`도 실제 DB에서 성공했다. CI는 Python 3.10과 동일 PostgreSQL/pgvector 이미지로 전체 테스트·CQ·호출 예제를 실행하도록 구성했다. 원격 CI 실행 결과는 PR의 Checks에서 별도로 확인한다.

`artifacts/demo_result.json`의 기존 로컬 변경은 이번 작업에서 덮어쓰거나 커밋하지 않았다. 새 일반 실행 결과는 ignored `artifacts/local/`에 생성한다. 원본 파일과 해시는 변경하지 않았다. 시각화·용어 사전·개인 인계 자료는 Git에 추가하지 않았다.

## CQ 판단 결과

[검증 결과](artifacts/cq_postgres_verification.json)의 `checks`는 모두 통과했다. CQ09는 제안/미구현으로 남겼으며, CQ10은 정확한 원본 범위 부품 ID의 사용처만 검증했다.

- CQ01~03: 모델·개정·변형, 74행·128위치, 실장 미확인 L 11행/L1 12행, 구성 차이 R50·R51·R6·U2와 근거 유지.
- CQ04: 중복 등록 NO_CHANGES와 승인 반영 요청 재생, 별도 통합 테스트에서 동일 키 충돌·동시 요청·재접속 확인.
- CQ05~08: 아래 제품별 판정, 정확한 질문·시간대·반개구간·버전 2→3·보완 근거 유지. 미확인 정보를 채워 넣지 않음.
- CQ10: L LAN8710 근거 55행과 L1 54행의 별도 부품 ID·U4 사용처 유지. 개정 간 동일성·대체 가능성을 자동으로 확정하지 않음.

A=`2026-09-20T10:00:00+09:00`, B=`[2026-09-01T00:00:00+09:00, 2026-09-21T00:00:00+09:00)`, 로트=`lot:demo:lan8710:L07`, 대상 U104~U107이다. 응답의 UTC 정상화 시각도 직접 확인했다.

| 보드 | 보완 전 A/B | 보완 후 A | 보완 후 B |
| --- | --- | --- | --- |
| U104 | 추가 확인 / 추가 확인 | 조건상 제외 | 포함 |
| U105 | 포함 / 포함 | 포함 | 포함 |
| U106 | 추가 확인 / 추가 확인 | 추가 확인 | 추가 확인 |
| U107 | 조건상 제외 / 조건상 제외 | 조건상 제외 | 조건상 제외 |

U104는 제거·완전 이력의 새 가상 근거로 판정이 바뀐다. U105의 나머지 미확인 이력 설명은 유지하며 U106에 존재하지 않는 근거를 붙이지 않는다. 포함은 고장 원인·실제 가동을 뜻하지 않고 제외는 안전 판정이 아니다. 데모의 1→2→3 버전은 개발 시드와 사람 역할 대역의 승인 경로 시험이며 실제 운영 승인이 아니다.

## 실모델 재검증과 별도 검토

최종 성공 사례는 Product 1개·Quality 1개, API 응답 총 12회·모델 선택 도구 총 10회다. Product는 U4 populated와 R50 unknown을 정확한 원문·ID·행 근거로 초안화하고 `awaiting_review`, 확정 데이터 버전 1에서 멈췄다. Quality는 U104/L07의 A/B·버전 2→3을 정확히 호출하고 실제 BOM과 가상 운영 근거를 구분했으며 변경 도구를 호출하지 않았다.

최종 성공 전 **역할 실행 4개가 실패**했다. 초안 ID 혼동, 한 번에 너무 많은 도구 호출, 조회 버전 0 추정, 확인된 U4 실장 상태를 unknown으로 바꾼 문제다. 실패에서도 확정 데이터는 유지됐다. provider의 순차 호출, 새 초안 ID 생략 지침, 입력 data_version·파서 population 명시로 수정했다. 잘못 추가한 미확인 주장도 평가기가 거부하도록 보강했다. 실패를 성공률 계산에서 숨기지 않으며, 반복 실행 신뢰도·범용 추출·긴 대화 품질은 아직 검증하지 않았다.

별도 읽기 전용 검토자가 코드와 최종 두 답의 도구 trace를 대조했다. 발견한 큰 정수 벡터 오류 처리와 외부 트랜잭션 침범 문제를 수정하고 회귀검사를 추가했다. 재검토에서 추가 차단 문제는 없었다. Quality 자유문 B는 날짜 단위로 축약하므로 정확한 시간·끝 제외 조건은 task/trace와 함께 보여줘야 한다. 원본 URL/hash/locator는 호스트 resolver가 붙이며 Quality의 직접 Evidence 열람 성공으로 계산하지 않는다.

## 남은 제한·결정

- pgvector는 **실제 DB 저장·수치 벡터 cosine 검색**까지 확인했다. 임베딩 API 연결, 자연어 의미 검색, RAG 응답 품질은 아직 구현·평가하지 않았다. 검색 결과는 `candidate_only`이며 정확한 ID·시간·승인 도구를 대체하지 않는다.
- 원본의 NA·NS·Opened 조립 정책, 개정 간 부품 동일성/호환성, PDF 148행 전체 시각 대조는 미확인이다. ODS 전체 구조·수량·위치와 PDF 표제/U6 대상 식별 검증 범위는 유지한다.
- 임의 PDF/OCR 추출, 전체 Agent·UI·운영 인증·실제 사람 승인 연결은 미완료다. 기존 호스트의 테스트 신원을 외부 API 입력으로 받지 않는다.
- 과거 SQLite 파일은 삭제하지 않았고 실행 로그·미승인 초안을 PostgreSQL로 자동 이전하지 않았다. 대표 데이터는 보존 원본·기존 시드/승인 데모로 새 PG schema에 생성했다.
- Docker의 DB 소유자 설정은 로컬 개발용이다. 운영 최소권한 DB 계정·백업/복구·배포는 별도 통합 과제다. DB 자격증명이나 Python 객체를 가진 개발자까지 격리하지 않는다.
- 한 schema의 호출을 직렬화하는 작은 기준 데이터 구조다. 고급 OWL 추론·SPARQL·ANN 인덱스·대규모 성능 검증은 없다. DB 전체 장애·강제 종료 시 실행 기록의 완전성/자동 복구를 보장하지 않는다.
- **이번 PostgreSQL 전환·CQ 확인·PR 제출을 막는 사용자 결정은 없다.** 다음 의미 검색 업무에서 임베딩 모델·검색 평가 사례를 정하고 기존 호스트 검색 접점에 연결하면 된다. CQ09 채택과 실제 승인자 인증 연결은 이후 결정이다.

## 공유 상태와 다음 작업

저장소: 비공개 [Ontologics-2Zo/ontologics](https://github.com/Ontologics-2Zo/ontologics). 전환 브랜치는 `codex/postgresql-pgvector`, PR 대상은 `main`이다. 전환 전 기준은 `70700775d271f95732ffb9d73363aa480aebf608`. PR 병합 전 main은 기존 v0.1.0 기반을 유지한다. 현재 체크아웃 기준 커밋은 `git rev-parse HEAD`로 확인한다.

동료는 README의 DB 시작→테스트→데모→CQ 확인 순서로 실행한다. Product/Quality 담당은 `examples/llm_agents.py`의 역할 prompt·task·평가를 확장하고, 검색 담당은 `EvidenceIndex.documents/index/search`에 임베딩 provider를 연결한다. ID·시간·검증·승인 로직은 공통 코드를 재사용한다. 다음 공통 통합은 인증된 사람의 검토·보완·승인 UI이며, 추가 Agent·OpenRouter·자동 모델 라우팅을 선행 조건으로 만들지 않는다.
