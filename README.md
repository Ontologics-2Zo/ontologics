# Ontologics 공통 기반 v0.1.0

실제 OLIMEX 사양·BOM을 제품·구성으로 등록하고, 가상 개별 보드·로트 이력과 연결해 조사·보완하는 공통 라이브러리다. S1은 문제 상황 분석 및 생산·사용 이력 보완, S2는 기존 구성 기준에 연결한 제품 등록이다. 현재는 동료의 Agent 개발을 위한 실행 가능한 기준 버전이며 운영 서비스 전체의 완성본은 아니다.

## 시작

Python 3.10 이상에서 저장소 루트를 작업 디렉터리로 사용한다. 외부 패키지·모델 키·DB 서버·MCP 서버가 필요 없다.

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

실제 LLM·업무 UI·운영 사용자 인증은 아직 연결하지 않았다. 고정 모델 호출부는 주입형 provider 계약만 제공하며 테스트 대역의 성공을 실모델 성능으로 해석하지 않는다. 파일·Python 객체에 접근할 수 있는 로컬 개발자를 보안적으로 격리하는 시스템도 아니다.
