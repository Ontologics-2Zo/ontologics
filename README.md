# Ontologics 공통 기반 v0.1.0

실제 OLIMEX 사양·BOM을 제품·구성으로 등록하고, 가상 개별 보드·로트 이력과 연결해 조사·보완하는 공통 라이브러리다. S1은 문제 상황 분석 및 생산·사용 이력 보완, S2는 기존 구성 기준에 연결한 제품 등록이다. 현재는 동료의 Agent 개발을 위한 실행 가능한 기준 버전이며 운영 서비스 전체의 완성본은 아니다.

## 시작

Python 3.10 이상에서 저장소 루트를 작업 디렉터리로 사용한다. 다음 오프라인 실행은 외부 패키지·모델 키·DB 서버·MCP 서버가 필요 없다.

```bash
python3 -m unittest discover -v
python3 -m ontologics.demo
python3 -m examples.agent_tools
```

첫 명령은 원본·시간·권한·승인·실패·중복·모델 호출부 계약을 검증한다. 두 번째는 **실제 ODS 파싱 → L1 등록안 → 개발용 사람 역할 검토 → 승인 반영 → 조사 → 보완 요청·수정 → 재승인 → 동일 조건 재조회**를 실행한다. 예제의 승인자는 테스트용 신원이며 실제 업무 승인을 받았다는 뜻은 아니다.

기본 DB는 메모리다. 영속 데이터로 확인하려면 새 파일 경로로 `python3 -m ontologics.demo --db /tmp/ontologics-demo.sqlite`를 실행한다. 이미 다른 자료로 초기화한 DB는 다시 시드하지 않는다. 전체 예제 재실행에는 새 경로를 사용하고, 개별 변경의 재시도에는 같은 `request_key`를 사용한다.

## 확인할 결과

- 실제 L·L1 WROOM BOM: 각각 74행, 위치 128개. 비교되는 원문 위치는 R50, R51, R6, U2다.
- U104의 제거 기록 보완 전: A와 B 모두 `needs_confirmation`.
- 제거 및 완전 이력 근거를 승인한 후: A=`excluded`, B=`included`.
- U106은 계속 `needs_confirmation`, 충분한 부재 근거가 있는 U107은 `excluded`.
- 데이터 버전은 1(개발 시드) → 2(L1 승인 등록) → 3(U104 승인 보완).

결과는 `artifacts/demo_result.json`, T-box는 `ontology/tbox.ttl`, 실행 마지막 시점 A-box는 `data/initial_graph.jsonld`에 기록된다. 마지막 파일은 초기 인계용 그래프이며 시나리오 실행 후의 버전 3이다. 실행마다 ID·실행 시각은 달라질 수 있지만 업무 판정은 같다. 원본에서 `data/catalog.json`을 다시 만들려면 `python3 -m ontologics.ingest`를 실행한다.

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

한 명령만 골라 실행하면 된다. 설정 우선순위는 프로세스 환경변수 → 선택한 파일이다. 기본 파일은 `.env.local`, 없으면 `.env`이며 `--env-file 경로`로 지정할 수 있다. 파일은 위 네 항목의 `KEY=value`만 읽고 셸로 실행하지 않는다. 모델명을 다르게 지정하면 명시적으로 실패한다.

매 실행은 `artifacts/llm_runs/run-<시각>-<ID>/`에 역할별 새 SQLite와 `report.json`을 남긴다. 보고서에는 모델 호출 상태·토큰 수·실제 도구 입력/출력·최종 답·자동검사·원본 URL/위치가 있다. 키와 HTTP 오류 원문은 기록하지 않는다. 로컬 실행 DB·보고서·환경 파일은 Git에서 제외한다. 재실행은 별개의 시드/업무 예제이며 운영 변경의 자동 재시도가 아니다.

| 실행 | 기대 결과 | 동료가 수정할 위치 |
| --- | --- | --- |
| Product | 이미 파싱한 L1 U4·R50 두 행의 `awaiting_review` 초안과 검증 결과. R50은 `population=unknown`. 확정 데이터 버전 1 유지 | [examples/llm_agents.py](examples/llm_agents.py)의 `PRODUCT_PROMPT`, `product_task`, `evaluate_product` |
| Quality | U104, L07, 버전 2→3 비교. A 미확인→제외, B 미확인→포함. 실제 BOM 근거와 가상 이력 근거 분리 | 같은 파일의 `QUALITY_PROMPT`, `quality_task`, `evaluate_quality` |
| 공통 연결 | 신원 고정·도구 검증·실행 기록·제한된 재시도 | [runtime.py](ontologics/runtime.py), [openai_provider.py](ontologics/openai_provider.py), [tool_specs.py](ontologics/tool_specs.py). 역할별 복제 없이 재사용 |

Quality 버전 2/3은 **모델 호출 전 호스트가 기존 데모 승인 경로로 준비한 시험 데이터**다. LLM 작성 초안을 자동 승인하는 기능이 아니다. Product 예제는 대표 두 행의 초안 작성까지이며, 전체 S2 등록·보완·승인 흐름은 위 오프라인 데모에서 확인한다.

실제 연결은 역할별 1회, API 응답 총 7회·모델 선택 도구 총 10회로 확인했다. [검증 요약](artifacts/llm_verification.json)에 원문 최종 답·도구 조건·근거와 제한을 보존한다. 자동검사는 조건·상태·근거 ID·미확인 필드·확정 데이터 불변을 검사한다. 자유문은 별도 검토 에이전트가 해당 2개 답만 대조했다. 광범위한 추출·대화·공격 대응 품질 평가는 아니다. Quality 원본 URL/행 위치는 호스트가 인용 ID로 찾아 붙였으며 모델이 PDF 원문을 직접 검토한 결과가 아니다.

오류가 나면 `report.json`의 `roles[].runtime.error`와 `roles[].provider_calls`를 확인한다. 실행 전 설정 오류는 최상위 `error`에 있다. 키 누락·인증·모델 접근·입력 오류는 즉시 종료하고, timeout·연결 실패·429·5xx만 기존 런타임에서 최대 2회 시도한다. 단계당 최대 4도구·실행당 최대 8모델 단계다. 성공한 초안은 이후 실패해도 미승인 상태로 남는다. 오류 코드·구체적 입출력은 [TOOLS](docs/TOOLS.md#실모델-예제의-입출력)에서 확인한다.

## 저장소 공유 상태

비공개 저장소는 [Ontologics-2Zo/ontologics](https://github.com/Ontologics-2Zo/ontologics), clone 주소는 `https://github.com/Ontologics-2Zo/ontologics.git`이다. 2026-10-22에 `main`을 push했고 `origin/main`을 추적한다. 공통 기반 구현 기준 커밋은 `07eddb82a8c15dcde9965c3de4345db2a932605a`, 초기 v0.1.0 기준은 `2fdc0159c8cf5ba694e5d8bd51136d77b1bc42b4`다. 공유 상태 문서 갱신까지 포함한 커밋은 `git rev-parse HEAD`로 확인한다. 비공개 저장소 접근 권한이 있는 동료가 clone할 수 있다.

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
