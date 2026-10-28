# Order Fulfillment — B 개발 안내

공통 문서 [README.md](../../README.md)의 메인 기준(`7c5e2d9`)과 비교해 B 개발 중 추가·변경된 내용을 분리했다. 공통 문서는 해당 메인 원본으로 복원했다.

아래 테스트 수·실제 모델 실행·수락 여부는 원문에 기록된 당시 결과다. 현재 폴더 분리 이후의 전체 동작을 새로 검증했다는 뜻은 아니다. 실행 예제와 B 파일 경로는 현재 하위 폴더 위치로 표시한다. 이 문서의 과거 기록 이후 공통 Provider·Service·도구 정의를 B 하위 폴더로 분리했다. 현재 검증 상태와 남은 모델 연결 문제는 [B 작업 기록](PROJECT_STATUS.md)을 따른다.

B 문서: [작업 기록](PROJECT_STATUS.md) · [서비스 명세](SERVICE_SPEC.md) · [도구 계약](TOOLS.md) · [Agent 지침](AGENT_RUNTIME.md).

## 추가·변경 1

> 원문 위치: 문서 시작 (1–24행)

### Ontologics 공통 기반 v0.8.0 — PostgreSQL + pgvector

**최신 CQ06 Task10(2026-10-27) — 개발 범위 수락:** 새 전체586개·skip0·129.653초와 무료예제34개를 통과했다. Task07 설계28개를 고유 검사83개의 새 통과 로그와 대조하고 CQ06 예제5사례를 독립 정답과 확인했다. Task09 실제 불량/운송 각27/27·별도 의미 PASS는 현재 코드/자료/원문 해시 일치로 재사용했다. 이번 추가 LLM0회·$0, 업무코드변경0. CQ06 Task07~10의 명시 승인 입력·고정 가상 자료·읽기 Agent 개발 범위를 수락했다. 반복 신뢰도·서비스 연결·운영/전체MVP 수락은 별도다. 기존 작업·개인 Git 제외·브랜치/main 보존, push0.

**이전 CQ06 Task09 실제 검증(2026-10-27) — 두 대표 통과:** 공용 pattern 제거 코드·출력1,024 한도에서 불량 증가 새4단계와 운송 변경 새4단계가 각각 자동27/27·별도 의미 검토를 통과했다. 실제8/최대8회·과거응답재사용0·자동재시도0. 입력68,489(캐시32,128)·출력2,091토큰, 캐시 반영 표준단가 추정$0.03908985(청구미확정). 고정 가상 두 대표의 Task09 검증을 완료했으며 Task10은 진행하지 않았다. 반복 신뢰도·운영 수락·전체MVP 완료를 뜻하지 않는다. 기존 업무 코드·자료·키·개인 Git 제외와 main은 보존했다.

**이전 공용 코드·무료 검증(2026-10-27):** CQ06 최종 API 스키마의 문안4개에서 `pattern`만 제거했다. Python 기본 문안의 고정 `enum`·type·description·16필드와 로컬 길이/줄바꿈/문안/사실/근거/권한 검사는 유지한다. 관련44개·전체586개(skip0)·무료 예제34개 통과. 이번 실제 LLM 호출0회, 공용 입력·모델·출력1,024 한도는 그대로다. 이전 개인 진단의2,048 한도·과거 응답3개 재사용 성공과 구분하며, 수정 공용 코드의 새 실제4단계 검증·Task09 전체·Task10은 아직 미완료다.

**이전 실제 진단(2026-10-27) — pattern만 제거한 마지막1회 통과:** 원래 입력49,705바이트·모델·2,048출력 한도·enum·description을 유지하고 문안4개 pattern만 제거한 마지막 실제1회가 completed·자동27/27·별도 의미 검토를 통과했다. 과거 성공 응답3개는 재사용했으며 추가 호출/운송 대표/Task10은 진행하지 않았다. 입력14,023(캐시13,824)·출력696토큰, 캐시 반영 표준단가 추정$0.00431805(청구미확정). 이번 비교에서 pattern 제거 후 결과가 달라졌지만 API 내부 원인·반복 신뢰도·Task09 전체 통과는 확정하지 않는다. 업무 코드와 공개 기본 스키마는 변경하지 않았다.

**이전 무료 진단 — CQ06 스키마/입력 축소 후보 준비:** 실제 API·API 키 로드·DB 사용0으로 마지막 요청을 원래 SHA 그대로 재구성했다. 16필드와 양쪽 버전의 선택 근거 메타데이터를 보존한 진단 후보에서 입력49,705→9,928바이트, 전체 요청54,514→14,321바이트로 줄였다. 고정 enum4개는 기존 pattern을 모두 만족하며 로컬 모순은 발견하지 못했다. 중복 제약과 프롬프트1,024/API2,048 불일치를 확인했고49개 동치 경계 사례·독립 검토를 통과했다. 축소본은 비전송 진단용이며 원격 실패 원인은 미확정이다. 업무 코드/기본값은 그대로이고 Task09 전체·Task10은 미완료다.

**이전 실제 기록 — CQ06 출력2,048 최종 단계 진단 실패:** 불량 증가 사례의 마지막 출력 한도만1,024→2,048로 바꿔 실제1회 진단했지만 다시 incomplete/max_output_tokens·output=[]·API 보고 사용량0으로 실패했다. 과거 성공 응답3개를 원래 호출ID/내용으로 재사용했고 최종 요청의 입력·모델·스키마는 동일함을 SHA로 검사했다. 추가 호출·운송 대표·Task10은 진행하지 않았다. 사용량0 보고는 청구0이나 실제2,048토큰 소진의 증거가 아니다. 한도 증가만으로 이번 문제가 해결되지 않았으며 API 내부 원인은 미확정이다. 업무 코드/기존 기본 한도는 변경하지 않았고 개인 진단기에만2,048을 적용했다.

**이전 기록 — 출력1,024 실제 최종 응답 미완료:** CQ06 Task09 새 실제 재검증 실패로 중단. 불량 증가 대표의 처음3회 조회는 완료했지만 마지막 API가 max_output_tokens 사유의 incomplete·빈 output을 반환해 최종 JSON을 받지 못했다. 실제4/최대8회·자동재시도0, 운송 대표0회·Task10 미진행. API 보고 토큰 기준 비캐시 단가 추정 $0.01961325(청구미확정). 기존 무료586개·예제34개는 코드/자료 해시 일치로 재사용하고 관련44개·두 HTTP 대역 사전검사는 새로 통과했다. 원본과 이전 두 실패를 보존했다.

**이전 무료 보완 기록 — 무료 보완 완료·실제 재검증 대기:** CQ06 Task09의 Python 문안 무료 보완 완료. 전체 무료586개(skip0)·예제34개 통과. 이번 실제 API 0회·비용 $0. 변화 이유·기본 문안은 Python이 만들고 LLM은 조회·근거 확인 후 그대로 전달한다. 새 코드의 실제 대표 검증은 미실행이므로 Task09 최종 통과·Task10은 미완료다. 이전 두 실제 실패는 보존했다.

**이전 실제 재검증 기록 — CQ06 Task09 재검증 실패로 중단:** 전체 무료572개(skip0)·예제34개 통과, 새 실제4/최대8API·자동재시도0·비캐시 단가 추정$0.03163350(청구미확정). 실행한 대표의 원래 응답·별도 문장 검토와 이전 실패를 보존했다. Task10·반복신뢰도·전체MVP/운영 수락은 진행하지 않았다. 첫 실제 자동26/26·설명151자는 통과했지만 속도/누계 혼동과 미완결 회신으로 의미 불합격이다. 이후 추가 호출을 중단했다. 설명 예산60/80/45/35자를 마지막 JSON pattern과 평가기에 연결하고, 검토용 표시·조건부 표현·의미 검토를 유지했다. 이전 실패는 당시 기록으로 남긴다.

**이전 기록 — CQ06 Task09 첫 시도 실패:** 기존 CommonHost에 읽기 전용 `b_comparison`을 연결했고 전체 무료569개(skip0)·실행 예제34개를 통과했다. 실제 불량 증가 대표는4 API에서 수량·시간·버전·근거가 일치했지만 설명문 합계339자로 각100자/합계220자 조건에 실패했다(자동21/24). 운송 대표와 Task10은 진행하지 않았다. 입력35,184/출력1,158토큰, 비캐시 단가 추정$0.031599이며 청구는 미확정이다. 실패 답을 수정하거나 합격 기준을 완화하지 않았다. 반복 신뢰도·전체MVP 수락·운영 실행은 별도다.


현재 공통 계약은 v0.8.0/`graph-0.5`다. CQ06 Task08은 승인 자료 두 버전의 생산·도착 예상과 대응 초안을 비교하는 읽기 도구를 추가했다. 비교 규칙은 comparison-0.1이며 기존 예측 규칙·그래프 타입을 유지한다. CQ05 Task08에서 기존CQ04 결과를 읽어 고객 회신과 미출고 물량의 출고/운송 검토 초안을 만드는 공통 도구를 추가했다. CQ05 Task09는 형식 보완 후 같은 코드의 실제 기본·혼합 각 1번과 별도 의미 검토를 통과했다. Task10에서 새 전체 회귀·예제·설계와 보존을 확인해 CQ05 개발 범위를 수락했다. CQ01~04의 과거 실제 모델 기록은 보존하며, 계약/Service 코드가 바뀐 현 버전의 실제 응답 검증으로 재사용하지 않는다. 최신 검사 결과와 남은 범위는 [PROJECT_STATUS](PROJECT_STATUS.md)를 따른다.

## 추가·변경 2

> 원문 위치: Ontologics 공통 기반 v0.8.0 — PostgreSQL + pgvector (27–119행)

v0.4.0에서 추가한 B-CQ02의 완성 보드 재고·생산 작업·배정·운송과 6상태 현황 계약은 유지한다. v0.5.0은 `ProductionObservation`의 총생산·정상 완료·불량 누계와 공통 Python 계산을 추가한다. 회차에 전량 배정한 단일 작업의 최근 정상 완료 속도를 기준으로 생산 완료만 예상한다. 고객 도착 예상은 별도 CQ04 조회에서 제공하며 CQ02의 부분 완료 작업 미확인 제한도 유지한다. 기존 장착 이력·승인·PostgreSQL 계약과 `temporal-0.1`은 그대로고 예측 규칙은 별도 `production-0.1`이다.

CQ05는 Task07의 정의를 Task08 공통 함수와 기본 문안으로 구현했다. 실제 발송·신청 등록·운영 계획 변경은 범위 밖이다. CQ05 Task09는 초기 실패 기록을 보존하고, 추가 승인된 8회 호출로 형식 보완 후 실제 기본·혼합 대표를 재검증했다. Task10 개발 범위 수락도 완료했고 CQ06 Task07은 승인된 전후 입력의 재계산·비교 질문과 독립 정답을 정의했다. CQ06 Task08은 공통 비교 기능·독립 변경 자료·무료 검증을 완료했다. CQ06 Agent와 실제 LLM 검증은 Task09에서 진행하며 기존 CQ05 실제 응답을 새 계약의 검증으로 재사용하지 않는다.

#### B-CQ06 변경 전후 비교 예제

```bash
python -m examples.order_fulfillment.b_recalculation --case defects11 --output artifacts/local/b_cq06/example-01.json
```

LLM 없이 일회용 PostgreSQL 스키마에서 새 가상 입력의 초안·개발용 사람 검토 대역·승인 반영과 두 버전 비교를 실행한다. continued11/defects11/transport2h/combined11/zero_rate11은 각각 독립 사례다. 기본960/40과 비교해 정상11시는유지,불량증가는920/80,운송2시간은870/130이다. 전후전체초안·근거와후−전차이를보존하며null은0으로계산하지않는다. 실제발송/출고/운영승인은 아니다. CQ06 Agent 연결·실제 검증 상태는 위 최신 상태를 따른다. [도구계약](TOOLS.md#b-cq06-승인-자료-변경-전후-비교)을따른다.


CQ06 읽기 역할의 무료 실행:

```bash
python -m examples.order_fulfillment.b_host_agent --mode comparison --case defects11 --output artifacts/local/cq06_host_defects_new.json
python -m examples.order_fulfillment.b_host_agent --mode comparison --case transport2h --output artifacts/local/cq06_host_transport_new.json
```

Python이 지표별 변화 이유·완결된 기본 문안을 만들고 LLM은 그대로 전달한다. 문안의 자유 작성 역할을 없애고 동적 enum·로컬 일치 검사로 수정된 문장을 거부한다. 새 코드의 실제 대표 검증은 불량 대표의 최종 API 응답 미완료로 중단했다. CLI는 모델 대역만 사용한다. 비교 조회 → 변경 전 Evidence 묶음 → 변경 후 Evidence 묶음 → 최종 JSON의4단계이며, 기존 전후 초안과 변경 목록을 전달하고 원본 전체 결과·최종 답을 보존한다. 모델은 버전별 Evidence 메타데이터를 읽고 호스트는 전체 원문 사슬/SHA를 확인한다. [CQ06 Agent 계약](TOOLS.md#b-cq06-agent-연결과-평가)을 따른다. `--mode all`은 독립 가상 사례의 역할별 검사이며 CQ01부터CQ06까지 하나의 업무로 이어지는 전체 흐름 수락은 아니다.

#### B-CQ05 대응 초안 예제

```bash
python -m examples.order_fulfillment.b_response --output artifacts/local/cq05_normal_new.json
python -m examples.order_fulfillment.b_response --mixed --output artifacts/local/cq05_mixed_new.json
```

LLM 없이 일회용 PostgreSQL 스키마에서 공유 가상 자료를 개발용 검토 역할로 승인한 뒤 명시 버전을 조회한다. [response_plan 계약](TOOLS.md#b-cq05-대응-초안-조회), [독립 기대결과](../../data/order_fulfillment/b_response_expected.json)를 사용한다. 기본 기한내960/부족40은 현재확보600과 미래생산 예상400을 포함한 조건부 계산이며 새 출고 검토량은1000대다. 혼합은 이미도착100·실제운송중150을 새 출고에서 제외해750대만 검토한다. 고객 회신·신청 내용은 미발송·미등록·담당자 검토 초안이다. 미확인은null이며 과거 기한은 이행 검토로 반환한다. 선택 인용8/13개와 전체 무결성 근거12/17개를 구분한다. 예제는 운영 승인이나 CQ05 Agent 검증이 아니다.

#### B-CQ05 Agent 연결 예제

```bash
python -m examples.order_fulfillment.b_host_agent --mode response --case normal --output artifacts/local/cq05_host_normal_new.json
python -m examples.order_fulfillment.b_host_agent --mode response --case mixed --output artifacts/local/cq05_host_mixed_new.json
```

위 CLI는 무료 대역 전용이다. b_response는 response_plan→선택Evidence묶음→SourceDocument묶음→최종JSON을4단계로 수행한다. 원래 도구 결과·최종 답을 보존한다. 마지막 출력에만19키 strict JSON 형식을 사용하며 사실/의미 검증은 별도다. [연결 계약](TOOLS.md#b-cq05-agent-연결과-평가)을 따른다. CQ05 Task07~10 개발 범위 수락: 명시 승인 버전의 고정 가상 자료에서 대응 초안의 수량·시간·조건·근거와 읽기 권한을 확인했다. 새 전체 502개·skip 0·105.625초, 관련 82개와 무료 예제 24개 통과. 설계 36개를 고유 검사 83개와 대조했으며 D26·D34의 확장 검증은 부분 확인으로 남긴다. Task09 실제 기본·혼합 각 25/25·별도 의미 검토는 현재 코드/원본 해시 일치로 재사용했다. 이번 추가 API 0회·비용 $0. 고객 문장 단독 표시·반복 신뢰도·운영 성공·전체 MVP는 미수락이다. 다음은 CQ06 Task07 정의이며 미착수다. 기존 작업·원문·정답·키·개인 Git 제외·실패 기록·브랜치/main 보존, fetch/commit/push 0.

#### B-CQ04 고객 도착 예상 예제

```bash
python -m examples.order_fulfillment.b_delivery --output artifacts/local/cq04_base_new.json
python -m examples.order_fulfillment.b_delivery --mixed --output artifacts/local/cq04_mixed_new.json
```

각각 새 PostgreSQL 스키마에서 가상 조건·자료의 초안/개발용 사람 검토 대역/반영 후 명시 승인 버전을 조회한다. API 키와 LLM 호출은 없다. 기본 분할 출고 사례는 **기한 내960대·부족40대·전량 도착 예상15:26:40**, 별도 혼합 사례는 **1,000대·부족0대·14:20**이다. 출고 준비/운송/방식과 최근 추세·처리 능력 가정에 따른 예측이며 실제 출하·고객 도착 사실을 만들지 않는다. 조건/관측/확인 범위 부족은null과 이유를 반환한다.

[공통 계약](TOOLS.md#b-cq04-조건부-고객-도착-예상-조회), [기본 정답](../../data/order_fulfillment/b_delivery_expected.json), [혼합 정답](../../data/order_fulfillment/b_delivery_mixed_expected.json)을 따른다. 기존CQ03의 단일 전량 배정 범위와CQ02의 부분 완료 미지원은 유지한다. CQ04 Task09에서 Agent 연결과 실제 기본·혼합 대표 검증을 진행했다. Task10에서 전체448개·설계36개·예제20개를 확인해 위 개발 범위를 수락했다. 실제 모델 반복 신뢰도·운영 납품 보증은 별도다.

```bash
# 대역 모델로 실행하며 API 비용이 없다. 출력 파일은 새 경로를 쓴다.
python -m examples.order_fulfillment.b_host_agent --mode delivery --case normal --output artifacts/local/b_cq04_host_normal_new.json
python -m examples.order_fulfillment.b_host_agent --mode delivery --case mixed --output artifacts/local/b_cq04_host_mixed_new.json
```

[Agent 계약](TOOLS.md#b-cq04-agent-연결과-평가)은 계산→근거 전체 묶음→원문 정보 전체 묶음→최종 JSON으로 최대4모델응답을 사용한다. 수치·조건·정확한 인용과 자유문 의미 검토를 구분한다. 실제 기본은960/40/15:26:40, 혼합은1,000/0/14:20과 정확한8개/13개 인용을 확인했다. 원문 정보 조회 누락으로 실패한 두 기록도 보존한다. 총4시도/API14회, 비캐시 단가의 토큰 기준 추정$0.04743525이며 실제 청구액은 미확정이다. 기본 답의 “전량 도착 예상은 아닙니다”는 기한 내 범위로 읽히지만 단독 인용하면 모호하다는 한계가 있다. 실제 도착 보증·실모델 예외/반복 신뢰도·웹 연결·운영 검증은 별도다.

#### B-CQ03 생산 완료 예상 예제

```bash
python -m examples.order_fulfillment.b_production --output artifacts/local/b_cq03_task08/example.json
```

LLM 없이 새 PostgreSQL 스키마에서 가정 생산 실적을 초안·검증·개발용 사람 검토 대역·반영한 뒤 명시 버전으로 조회한다.09시 정상510대→10시600대의 최근 속도90대/시간과 목표1000대로부터 잔여400대·16000초·**2026-10-27T14:26:40+09:00**를 계산한다. 자료 부족·오래된 현황·속도0은 완료 예상null로 구분한다. 목표 도달 관측은 최초 실제 완료 시각이나 창고 입고가 아니다.

[실행·자료 계약](TOOLS.md#b-cq03-생산-완료-예상-조회), [고정 원문](../../data/order_fulfillment/sources/b_demo_production_01.md), [독립 정답](../../data/order_fulfillment/b_production_expected.json)을 따른다. 예제는 승인 경계·시점·근거·기존CQ02 제한을 포함한14개 검사를 제공한다. 이 명령 자체는 공통 도구 검사다. 아래 Agent 경로와 실제 업무 승인은 구별한다.

CQ03 Agent를 기존 CommonHost에 연결한 무료 예제:

```bash
python -m examples.order_fulfillment.b_host_agent --mode production --case normal --output artifacts/local/b_cq03_host_new.json
```

`--case missing_latest/zero_rate/target_observed`도 무료 대역으로 확인한다. [역할/근거/평가 계약](TOOLS.md#b-cq03-agent-연결과-평가)은 생산 조회→Evidence묶음→SourceDocument묶음→최종 JSON의 최대4모델응답을 사용한다. 실제 모델 정상 대표1건은 실패2번 보완 뒤3차19/19·별도 의미 검토를 통과했다. 모델이 원문 전체를 읽었다는 뜻은 아니며 실제완료/도착/납품가능성을 확정하지 않는다. 공용 CLI는 유료 API를 호출하지 않는다. Task10에서24개 설계 대조·전체361개·예제10개를 확인해 위 개발 범위를 수락했다. 추가API0회다. CQ04는 별도Task08에서 도착 조건·기본/혼합 계산을 구현했으며 Task09에서 전용 읽기 역할과 기본·혼합 실제 대표 검증을 연결했으며 Task10 개발 범위 수락도 완료했다. CQ05 Task07~10을 고정 가상 자료의 개발 범위에서 수락했고 CQ06은 미착수다. CQ03 실제 모델 성공을CQ04 모델 검증으로 재사용하지 않는다.

#### B-CQ02 물량 현황 예제

```bash
python -m examples.order_fulfillment.b_fulfillment --output artifacts/local/b_cq02_task08/example.json
```

LLM 없이 새 PostgreSQL 스키마에서 초안·검증·개발용 사람 검토 대역·반영·버전별 조회를 실행한다. 가상 B-01 1회차의 10시 현황은 납품 완료100·운송 중150·창고250·생산 중300·준비 완료100·대기100대다. 잔여 약속900대와 미배정0대를 구분한다. 자료가 없으면 0 대신 미확인으로 반환한다. [자료 규칙·도구·제한](TOOLS.md#b-cq02-물량-현황-조회)을 따른다. 이 예제는 실제 업무 승인이나 Agent 모델 검증이 아니다.

현황 Agent 연결은 기본적으로 API 없는 대역 실행이다.

```bash
python -m examples.order_fulfillment.b_fulfillment_agent --provider scripted --output artifacts/local/b_cq02_task09/scripted-new.json
```

실제 호출은 같은 명령에 `--provider openai`와 새 출력 경로를 명시한다. 고정 모델·정상 사례 하나·최대 API4회·재시도 없음·요청당 출력1024토큰으로 제한한다. 원문/근거를 읽는 모델 설명을 검사하며 수량은 공통 도구가 계산한다. 이전 보고서는 덮어쓰지 않는다. [연결·평가 계약](TOOLS.md#b-cq02-agent-연결과-평가)을 따른다.

## 추가·변경 3

> 원문 위치: Ontologics 공통 기반 v0.8.0 — PostgreSQL + pgvector / 확인할 결과 (166–196행)

#### B-CQ01 계약 약속 확인

```bash
python -m examples.order_fulfillment.b_contract
```

새 `b_cq01_<UUID>` PostgreSQL 스키마에서 기존 실제 설계 시드 → 가정 계약 초안·검증 → 승인 전 반영 차단 → 개발용 사람 역할 검토 → 버전 2 등록 → 약속·근거 조회를 실행한다. 모델 키 없이 공통 함수를 직접 호출하는 예제이며 실제 운영 승인이나 LLM 실행이 아니다.

기대 답은 가상고객사 A에 ESP32-POE Rev L WROOM 보드 1000대를 `2026-10-27T15:00:00+09:00`까지 고객 창고에 도착시키는 약속이다. 현재 이행 가능성은 `fulfillment_assessed=false`다. 해시가 고정된 원문과 근거 메타데이터, 독립 정답은 [TOOLS](TOOLS.md#b-cq01-계약-접수와-약속-조회)에 연결한다. 수량·기한 누락/모호함은 보완, 잘못된 숫자/참조는 검증 실패로 처리한다.

보고서는 Git 제외 `artifacts/local/task08/b_cq01.json`에 남는다. 필요할 때만 `python -m examples.order_fulfillment.b_contract --export-graph`로 [B 대표 그래프](../../data/order_fulfillment/b_contract_graph.jsonld)와 공통 T-box를 재생성한다. 기존 `data/initial_graph.jsonld`·OLIMEX 원본·부품 입출고 예제는 보존한다. 첫 추출 형식은 고정된 Markdown 표의 항목 1개/회차 1개이며 범용 계약서 파서는 아니다.

B 역할 연결 예제는 다음과 같다. 기존 런타임·provider를 재사용하며 별도 서버가 필요하지 않다.

```bash
python -m examples.order_fulfillment.b_agent --provider scripted
python -m examples.order_fulfillment.b_agent --provider openai --output artifacts/local/task09/b_agent_live.json
```

기본 `scripted`는 연결 시험용 대역이다. 실제 LLM 품질을 검증하지 않는다. `openai`는 로컬 키를 검증한 뒤 기존 고정 모델을 실제 호출한다. `--mode intake`는 원문·기존 구성·근거 조회 → 계약 3노드 초안 → 검증 → 사람 검토 대기, `--mode promise`는 개발용 호스트가 미리 확정한 자료의 회차 → 약속·근거·미확인 설명이다. `both`가 기본이며 두 모드는 별도 새 DB 스키마를 사용한다. 모델 초안을 자동 승인하지 않는다.

결과는 Git 제외 `artifacts/local/task09/b_agent.json`에 기록된다(`--output`으로 변경 가능). 실모델 실행에는 별도 경로를 권장한다. 키 누락은 DB 접수·API 호출 전에 `OPENAI_KEY_MISSING`으로 중단하며 대역 통과와 실모델 완료를 구별한다. 자동 평가는 원문 파서·독립 정답·실제 도구 기록·저장된 초안을 대조한다. 자유문 설명 전체와 임의 계약 PDF/OCR의 품질을 보증하지 않는다. 모드별 도구 권한과 입력/출력은 [TOOLS](TOOLS.md#b-agent-연결-예제-task-09)를 따른다.

새 CommonHost를 통한 B 연결 예제는 다음과 같다. 매 실행마다 새 개발 스키마를 사용하며 보고서 경로는 새 파일이어야 한다.

```bash
python -m examples.order_fulfillment.b_host_agent --mode all --env-file .env.local --output artifacts/local/b_host_first.json
```

[b_host_agent.py](../../examples/order_fulfillment/b_host_agent.py)는 기존 지침·도구·평가기를 `b_intake`·`b_promise`·`b_fulfillment`·`b_production`·`b_delivery`·`b_response`·`b_comparison`으로 등록한다. 개발 사용자의 요청 신원으로 실행·초안 작성자를 기록하고, CQ02의 정확한 근거 인용 검사를 유지한다. 이 명령은 **대역 모델 전용이며 유료 API를 호출하지 않는다.** 기존 standalone 예제도 유지한다. 실제 로그인·B 웹 메뉴/Supervisor 인계와 예외/반복 신뢰도는 별도 확인이 필요하다. 새 호스트의 실제 정상 대표 사례는 CQ01 접수/초안 작성10회·14/14·미승인 초안, CQ01 약속 조회10회·14/14·전체5개 근거 보존, CQ02 현황 조회4회·17/17·정확11개 인용이며 각각 별도 의미 검토도 통과했다. 범용 계약 파싱/OCR·운영 승인 성공은 검증 범위 밖이다. 범위와 비용은 [PROJECT_STATUS](PROJECT_STATUS.md)를 따른다. 위 CLI는 계속 대역 전용이다. 등록 방식과 권한은 [TOOLS](TOOLS.md#b-역할의-commonhost-연결)를 따른다.

## 추가·변경 4

> 원문 위치: Ontologics 공통 기반 v0.8.0 — PostgreSQL + pgvector / 동료의 확장 Agent 연결 (302–303행)

B의 CQ01·CQ02·CQ03·CQ04는 [b_host_agent.py](../../examples/order_fulfillment/b_host_agent.py)의 `REGISTRATIONS`로 이 접점을 사용한다. 기존 기본 네 역할을 교체하지 않으며 B 웹 라우팅은 아직 연결하지 않았다.

## 추가·변경 5

> 원문 위치: Ontologics 공통 기반 v0.8.0 — PostgreSQL + pgvector / 저장소 공유 상태 (306–306행)

비공개 저장소는 [Ontologics-2Zo/ontologics](https://github.com/Ontologics-2Zo/ontologics), clone 주소는 `https://github.com/Ontologics-2Zo/ontologics.git`이다. PostgreSQL 전환 PR #1·목표/완료 기준 PR #2·S1/S2 및 핵심 Agent 통합 PR #3은 가져온 main 이력에 포함되어 있다. 2026-10-27 로컬에서 가져온 기준은 `7c5e2d9`다. 이전 `codex/s1-s2-service`의 기준은 `189500e`였으며 해당 브랜치의 공통 호스트·S1·S2·네 Agent 변경이 이번 main에 들어왔다. 원격 CI 성공 여부는 해당 PR/커밋 checks에서 별도로 확인한다. 실제 인증·운영 백업·AWS·RDF/Neo4j 완료를 의미하지 않는다.

## 추가·변경 6

> 원문 위치: Ontologics 공통 기반 v0.8.0 — PostgreSQL + pgvector / 저장소 공유 상태 (308–308행)

현재 B 작업은 `feature/b-first-mvp`의 보존 커밋 `f808b69`를 남긴 뒤 위 main을 병합했다. 기존 작업을 보존한 상태에서 공통/CQ01·CQ02 회귀와 새 호스트의 제한된 대표 검증을 수행했으며, 세 B 경로의 실제 정상 대표 검증을 갖췄다. CQ03의 정의 재점검 뒤 공통 생산 실적/예측 도구를 추가했고, Task09 전용 읽기 Agent·호스트 연결과 실제 정상 대표 검증까지 통과했으며 Task10 최종 점검과 개발 범위 수락까지 완료했다. CQ04 Task07의 도착/출고/운송 조건과 기본·별도 혼합 정답 정의도 완료했다. CQ04 Task08 공통 조건 계약과 계산 구현도 완료했다. CQ04 Task09도 읽기 Agent·근거 설명·실제 기본/혼합 대표 검증을 완료했다. CQ04 Task10도 전체448개·설계36개·예제20개와 원본 보존을 확인해 위 개발 범위를 수락했다. CQ05 Task07~10은 제한된 개발 범위에서 수락했고 CQ06 정의는 아직이다. 반복 신뢰도 검사는 후속으로 남긴다. 브랜치를 받았다는 사실이나 충돌을 해결했다는 사실을 업무 검증 통과로 계산하지 않는다. 실행 기준은 `git rev-parse HEAD`, 검증 범위와 남은 작업은 [PROJECT_STATUS](PROJECT_STATUS.md)로 확인한다.

## 2026-10-28 복구 Task11 — CQ01~06 재사용 실행 진입점

`examples.order_fulfillment.b_run`은 기존 Task10의 보호된 Host 실행 경로를 B 예제로 옮긴 진입점이다. 기존 `b_host_agent`의 대역 CLI와 역할 Provider·평가기·공통 코드·정답은 유지한다. 지원 입력은 아래 **고정 합성 10대표의 순서 있는 부분집합**이다. 임의 계약/업무 JSON, 운영 DB·사용자 인증·웹·배포·반복 신뢰도는 이번 범위가 아니다.

저장소 루트에서 PostgreSQL 17+pgvector와 기존 Python 환경을 사용한다. macOS `sandbox-exec`가 필수이며 보호 없는 대체 실행은 하지 않는다. 기본 PostgreSQL 실행 파일은 `/usr/local/opt/postgresql@17/bin`, 다른 설치 위치는 `--pg-bin /absolute/postgresql/bin`으로 지정한다.

```bash
# 지원 사례 확인: 외부 API 및 DB 호출 없음
.venv/bin/python -B -m examples.order_fulfillment.b_run list

# 전체 무료 검증: --output은 존재하지 않는 새 절대 경로
.venv/bin/python -B -m examples.order_fulfillment.b_run free \
  --output "$PWD/artifacts/local/order_fulfillment/cq_free_새실행명"

# CQ02 이후 8대표만: --case를 고정 순서로 반복한다
.venv/bin/python -B -m examples.order_fulfillment.b_run free \
  --case cq02_normal --case cq03_normal \
  --case cq04_normal --case cq04_mixed \
  --case cq05_normal --case cq05_mixed \
  --case cq06_defects11 --case cq06_transport2h \
  --output "$PWD/artifacts/local/order_fulfillment/cq02_06_free_새실행명"

.venv/bin/python -B -m examples.order_fulfillment.b_run status \
  --output "$PWD/artifacts/local/order_fulfillment/cq_free_새실행명"
```

전체 순서는 `cq01_intake → cq01_promise → cq02_normal → cq03_normal → cq04_normal/mixed → cq05_normal/mixed → cq06_defects11/transport2h`다. 무료 모드는 실제 Provider→Host→Service→평가기 경로에 명시적인 HTTP 응답 대역을 넣는다. API 호출·키 로드는 0이며 새 클러스터·DB·사용자·Unix socket·HOME·TMPDIR·UUID 스키마를 만들고 종료를 확인한다. 기존 DB/환경/인증 입력과 외부 네트워크를 차단한다. 원본 입력은 검토된 37개 파일만 해시 검사·복사하며 일부 기존 로더가 읽는 같은 원본 파일도 읽기만 허용한다.

대표당 최대 **4요청**, 전체 10대표 최대 **40요청**(CQ02~06만이면 32)이다. 고정 `gpt-5.4-mini-2026-03-17`, 출력 1,024토큰, 요청당 30초 hard deadline, 자동 재시도 0회다. 입력 토큰·금액의 별도 상한은 설정하지 않는다. 전송 전 예산을 영속 기록하고 실패/미완료/검사 불합격 후 후속 전송·재시작·이전 예산 이월을 거부한다. 키가 응답에 그대로 나오면 원문 SHA와 마스킹 표시를 남기고 중단한다.

출력의 `campaign/reports/`, `campaign/wire/`, `campaign/layers/`는 원문 응답·모델 제출·런타임·구조화 답·자동 검사를 구분해 보존한다. `campaign/http_budget.jsonl`은 요청 원장, `safety.json`은 원래 psycopg 접속의 주소·실제 서버 확인, `shutdown.json`은 소유 서버/PID/socket 종료 증거다. 성공한 무료 실행만 코드·입력·사례·요약/종료 해시에 결합한 `free_gate.json`을 남긴다. 실패 실행 폴더와 pgdata도 삭제하거나 덮어쓰지 않는다. 같은 `--output`은 다시 사용할 수 없다.

CQ01 접수 단독은 미승인 초안에서 끝난다. 접수와 약속을 함께 선택하면 무료에서는 **명시된 개발 검토 대역**으로 정확 draft/version/hash를 승인·반영한다. 향후 실제 실행에서는 모델 의미 검토와 별도로 고정된 다른 개발 검토자의 정확 승인 입력이 필요하다. 약속 단독은 승인된 개발 fixture를 새 DB에 재구성한다. CQ06은 별도 개발 검토·반영을 거친 **v2→v3**를 사용한다. Agent에는 review/apply 권한이 없다.

### 향후 실제 실행 조건 — Task11은 유료 호출을 승인하지 않음

`live --output /absolute/새폴더 --authorization /absolute/새승인.json --free-gate /absolute/무료폴더/free_gate.json`은 **새 명시적 사용자 승인 후에만** 사용한다. Task10의 승인을 재사용하지 않는다. 승인 JSON은 다음 필드가 정확히 있어야 하며 승인 전 예시는 `approved:false`로 둔다.

```json
{
  "approved": false,
  "source": "explicit_user_confirmation_with_mandatory_free_gate",
  "run_id": "/absolute/새폴더",
  "cases": ["cq01_intake", "cq01_promise", "cq02_normal", "cq03_normal", "cq04_normal", "cq04_mixed", "cq05_normal", "cq05_mixed", "cq06_defects11", "cq06_transport2h"],
  "per_case_request_cap": 4, "total_request_cap": 40, "cq01_request_cap": 8,
  "model": "gpt-5.4-mini-2026-03-17",
  "endpoint": "https://api.openai.com/v1/responses",
  "max_output_tokens": 1024, "timeout_seconds": 30, "automatic_retries": 0,
  "input_and_money_caps_unfixed_acknowledged": true,
  "free_gate_sha256": "무료 free_gate.json 파일의 SHA256",
  "source_sha256": "그 free_gate.json의 source_sha256 값"
}
```

부분집합은 cases·총한도·CQ01한도를 정확히 함께 줄여야 한다. `run_id`는 새 출력 폴더의 **전체 절대 경로**다. 무료 게이트와 현재 코드/입력/선택이 다르거나 원래 증거가 바뀌면 키 로드 이전에 거부한다. 승인 뒤에도 `.env.local`의 OPENAI_API_KEY만 기존 파서로 좁게 읽으며 그 DB 설정은 사용하지 않는다. OS는 443 포트를 허용하고 Python은 전송 중에만 고정 API 호스트·미리 고정한 IP·정확 endpoint/method를 허용한다. 프록시·redirect·자동 재시도는 없다.

각 실제 대표의 자동 검사 후 `submissions/<case>-separate_korean_semantic_review.requested.json`을 확인하고 같은 이름의 `.requested` 없는 JSON을 별도 검토자가 제출해야 다음 대표로 진행한다. 요청의 case·report_sha256·bound_output_hash를 그대로 결합하고 `kind:separate_korean_semantic_review`, `passed:true`, 근거 있는 한국어 `notes`가 필요하다. CQ01 연결은 추가 `cq01_intake-cq01_draft_review.requested.json`의 draft_id/version/content_hash/author/reviewer를 검토해 `decision:approve`·한국어 notes를 제출한다. 이는 개발용 신원이며 운영 인증을 대신하지 않는다. 리뷰 대기는 최대 1시간이고, `submissions/stop.json` 생성 또는 프로세스 중단 시 후속 호출을 멈추고 소유 서버를 종료한다. 실패 후 수정하더라도 새 유료 실행은 자동 시작하지 않는다.

Task11 최종 검증과 보존 결과는 [B 작업 기록](PROJECT_STATUS.md)의 Task11 항목을 따른다. 기존 Task10 실제 성공 10대표와 Task11 무료 대역 성공은 별개의 증거다.


## 2026-10-28 CQ01~06 발표용 노트북 초안

[시연 노트북](../../notebooks/CQ01_06_시연.ipynb)과 [사용 안내](../../notebooks/README.md)를 추가했다. Task11 무료 대역 구조화 결과를 내장해 약속·현황·생산·도착·대응 초안·변경 비교를 설명한다. 저장된 출력으로 읽을 수 있고 기본 전체 실행은 외부 API·DB·키/환경 파일과 프로세스에 접근하지 않는다. 선택 원본 확인은 명시된 JSON 14개만 해시 대조하며, 무료 재실행 셀은 기존 보호 CLI 명령만 출력한다. 실제 업무 재실행·유료 호출은 이번 노트북 작성에서 하지 않았다. 독립 설치·무료 재현 및 실제 Jupyter 커널/화면 확인은 후속 Task12 범위이며 아직 완료하지 않았다.


## 2026-10-28 CQ04 실제 실행 시연 노트북 연결·무료 검증

[새 실제 실행용 노트북](../../notebooks/CQ04_실제실행_시연.ipynb)은 고정 가상 CQ04 기본1대표를 기존 보호 CLI에 연결한다. [단계별 안내](../../notebooks/README.md)를 따른다. 기존 CQ01~06 저장 결과 설명용 파일과 별도다. 새 보고서가 없으면 없다고 표시하며 이전 답변을 대신 표시하지 않는다.

기본 전체 실행은 작업을 시작하지 않는다. 무료 사전검사와 종료 확인 뒤, 본인이 새 경로·CQ04 최대4요청·고정모델·출력1024·30초·재시도0·입력/금액 별도상한 없음을 확인하고 정확한 승인 문구를 직접 입력해야 유료 실행을 연다. 새 응답의 도구·근거·사용량·자동 검사 뒤 사람이 한국어 의미 검토를 직접 제출해야 최종 성공/종료를 확인한다. 커널 재시작 전 STOP_RUN으로 중단·서버 종료를 확인한다.

이번에는 실제 Jupyter 기본12셀, 새 DB의 CQ04 무료4대역 요청·별도 한국어 검토, 실제 중단/종료 및 전체705회귀(skip0)를 통과했다. 실제 API·키로드0이며 이 연결의 실제 유료 품질은 아직 검증하지 않았다. 새 B 소스가 추가되어 과거 무료 게이트를 새 실행에 사용하지 않으며, 새 세션의 현재 코드 무료 게이트가 필요하다. 임의 입력·다른 CQ 확장·독립 설치/패키징·배포는 이번 완료 범위가 아니다.


## 2026-10-28 CQ01~06 전체 대표 실제 실행 노트북 연결

- 고정 사례를 실제 LLM에 넣어 새 답변을 얻으려는 요청에 따라 [전체 실제 실행 노트북](../../notebooks/CQ01_06_실제실행_시연.ipynb)을 추가했다. CQ04만 선택했던 범위를 전체10대표로 확장했다. 기존 CQ04 실제 실행 노트북과 저장 결과 설명용 노트북은 보존했다.
- 순서는 CQ01 접수·약속 → CQ02 정상 → CQ03 정상 → CQ04 기본·혼합 → CQ05 기본·혼합 → CQ06 불량 증가·운송 변경이다. 대표당최대4·전체최대40·CQ01최대8 포함, 고정모델·출력1024·30초·재시도0을 유지한다. 입력·금액 별도상한 없음은 새 실행 승인에 명시한다.
- B 노트북 helper만 다중대표 선택·현재 결과 라우팅·검토 진행으로 확장했다. 기존 보호 CLI·Provider·Host·Service·평가기·원본 fixture와 독립 정답을 재사용한다. 새 무료 게이트와 본인의 실행 경로에 결합된 정확 승인 문구를 확인한 뒤에만 실제 호출한다. 기본 전체 실행은 API·DB·키·작업프로세스를 시작하지 않는다.
- 각 실제 대표의 자동 검사 후 사람이 정확 보고서·출력 해시에 결합된 한국어 의미 검토를 제출해야 다음으로 진행한다. CQ01 접수는 의미 검토 후 정확 draft/version/content_hash·작성자·변경 내용을 표시하고, 작성자와 다른 개발 검토자의 별도 승인/거절을 받는다. 개발용 identity는 실제 인증 계정을 의미하지 않는다. 자동 통과·이전 답변 fallback·자동 유료 재실행은 없다.
- 새 보호 DB에서 전체10대표·40 HTTP 대역 요청의 무료 검사를 통과했다. 별도 한국어 의미 검토10개, 서버 종료코드3·소유PID/pidfile/socket부재·cleanup오류0을 확인했다. 현재 실제 ipykernel에서 기본11셀 실행·커널 종료를 확인했다. 실제 유료 호출·키로드·승인된 유료 실행 생성은0이다. 이 노트북으로 실제 유료 성공을 새로 얻었다고 보고하지 않는다.
- 기존 실제 성공 증거235개 SHA, 원본 입력37개 및 새 helper를 제외한 기존 source digest dc1016c5248e676fa7c7312bc4b3d8d46bf3e6d0cd723a1dd6338ec02926b3f6가 동일하다. 새 helper 포함 source digest는4a5ca62714002e8eb8fd8f85c8e57b4302344dbb58820369c7f43e78fe5b44b1이다. 이전 무료 게이트를 수정 소스에 재사용하지 않는다. 새 의존성·기존 DB/settings/controller 실행·commit/push 없음.
- 커널·전체 무료·별도 의미 검토·보존 근거: artifacts/local/order_fulfillment/notebook_full_live_wiring_20261028T081315_c3a73283/. 무료 실행 원본: artifacts/local/order_fulfillment/notebook_61da7e63ca464d8e_free/.
- 최종 전체717개 수집·실행 전부 통과(노트북 경계검사29개 포함), 실패/오류/skip0. CI cq_check15항목·agent_tools도 통과했다. 새 소유 DB 실제psycopg1445연결을 대조했고 API/키읽기0·접근차단0, 서버/PID/pidfile/socket 종료를 확인했다. AST/메모리컴파일/공백 검사 통과, 별도 lint/typecheck 설정·새 의존성 없음. 전체 회귀·보호·종료 근거: artifacts/local/order_fulfillment/notebook_full_regression_20261028T081524_8c705fcc9709/.


## 2026-10-28 노트북 승인 셀의 중복 시작 안내 수정

두 실제 실행 노트북의 승인 셀에서 기존 live 시작 여부를 먼저 확인하고 현재 상태·5번 답변/6번 검토 안내를 표시한다. 최초 실행의 정확 승인·free gate 및 core 중복 실행 차단은 유지했다. 무료 검사 중·종료·비활성·새 사람 승인 등2노트북×5조건10검사 통과, 실제 API/프로세스/키읽기0. 사용자가 저장한 모든 다른 셀·출력·실행 카운터를 보존했고 B source/helper를 수정하지 않아 실행 중 캠페인과 free gate에 영향을 주지 않는다. 증거: artifacts/local/order_fulfillment/notebook_start_guard_20261028T083456_3b0cbefe/validation.json.
