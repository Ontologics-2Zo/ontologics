# B 1차 MVP — 납품 관리 담당자의 세 상황

기준은 `main` e4d57cd의 기존 B 입력·CQ 구현이다. 샘플만 추가하며 애플리케이션,
스키마, 화면, DB와 실행 권한은 변경하지 않는다. 모든 거래·운영 기록은 가상이다.

각 계약은 제품 구성 1개, 계약 항목 1개, 납품 회차 1개, 전량 800개의 약속이다.
여러 계약상 납품 회차를 관리하는 예제가 아니다. 하나의 약속을 여러 출하로 이행한다.
기존 도착·운송 기록과 앞으로의 조건부 분할 출하 예상을 구분한다.

## 상황과 입력 선택

| 상황 | 담당자의 질문 | 입력 파일 | 기존 실행 case |
| --- | --- | --- | --- |
| 정상 납품 예상 | 16시까지 800개를 받을 수 있는가? 물류와 고객에게 어떻게 안내할까? | `demo_normal_800.md` | CQ01~05 |
| 일부 지연 예상 | 창고 확보분이 적고 생산이 느린 상황에서 15시까지 얼마나 도착할까? | `demo_risk_800.md` | CQ01~05 |
| 운송 조건 변경 | 두 번째 상황에서 운송이 2시간 30분에서 3시간 30분이 되면 기존 설명을 유지할 수 있는가? | **같은 `demo_risk_800.md`** | `cq06_transport2h` |

두 입력 파일로 세 상황을 만든다. 세 번째는 새 계약이 아니라 두 번째 계약의 전후 비교다.
기존 CQ06 입력 처리기가 `condition.transport_seconds=9000`을 변경 전으로,
`comparison_transport_seconds=12600`을 변경 후로 사용한다. 기준 시각과 생산 관측,
재고, 기납품, 운송 중 사건은 바꾸지 않는다. 별도 변경 후 파일이나 새 비교 기능은 필요 없다.

## 공통 원문·운영 현황

- 고객: 가상고객사 B, 제품: ESP32-POE / L / WROOM, 납품처: 가상고객사 B 제2수령창고.
- 관측 기준: 2026-10-30 10:00 KST, 조회 시각: 같은 날 10:05 KST. 오늘 시각으로 바꾸지 않는다.
- 도착 100개, 운송 중 100개는 두 입력에서 동일하다. 생산 완료분을 창고에 다시 더하지 않는다.
- 준비 30분, 준비되는 물량부터 분할 출하하는 조건이다.
- 최근 추세 지속·양품 사용 가능·충분한 분할 출하 처리와 입력 완전성은 가상 사례의 가정이다.
- 운송 중 100개의 기존 도착 예상은 12시다. 세 번째 상황에서도 이미 출발한 이 사건을 임의 수정하지 않는다.

정상 계약 번호는 `B-DEMO-NORMAL-800`, 지연·변경 계약 번호는 `B-DEMO-RISK-800`이다.
두 파일은 납품 기한뿐 아니라 창고 물량·생산 배정량·양품 관측·운송 조건이 다르다.

| 입력 조건 | 정상 계약 | 지연 계약 |
| --- | --- | --- |
| 창고 확보분 | 200개 | 100개 |
| 생산 작업 목표 | 400개 | 500개 |
| 09시→10시 양품 누계 | 0→100개 | 0→50개 |
| 10시 생산 완료·미출고 / 미완료 | 100 / 300개 | 50 / 450개 |
| 최근 양품 속도 | 100개/시간 | 50개/시간 |
| 기본 운송 시간 | 2시간 | 2시간 30분 |

2026-11-04에 지연 입력을 위 조건으로 개정했다. 이전 지연 입력으로 실행한
750/50개·전량 15:30 등의 LLM 결과는 과거 기록이며 이번 입력의 검증 결과로 재사용하지 않는다.
기존 입력 처리기는 원문 해시로 SourceDocument/Evidence ID를 생성하므로 개정 입력은 별도 근거로 식별된다.

## 사람이 미리 정한 기대 결과

| 확인 항목 | 정상: 16시 약속·운송 2시간 | 지연: 15시 약속·운송 2시간 30분 | 변경 후: 15시 약속·운송 3시간 30분 |
| --- | --- | --- | --- |
| CQ01 계약 추출 상태 | ready, 항목 1·회차 1·800개 | ready, 항목 1·회차 1·800개 | 같은 계약의 약속 유지 |
| CQ02 도착/운송/창고/생산 완료/미완료 | 100 / 100 / 200 / 100 / 300개 | 100 / 100 / 100 / 50 / 450개 | 지연 입력과 동일 |
| 고객 미도착 잔량 | 700개 | 700개 | 700개 |
| CQ03 남은 양품 생산량 | 300개 | 450개 | 450개 |
| 해당 작업 생산 완료 예상 | 13:00 | 19:00 | 19:00 |
| CQ04 기한 내 도착 예상 | 800개 | 450개 | 400개 |
| 기한 내 부족 예상 | 0개 | 350개 | 400개 |
| 전체 도착 예상 | 15:30 | 22:00 | 23:00 |
| CQ05 새 출고 검토량 | 600개 | 600개 | 600개 |
| 현재 확보분 / 미래 생산분 | 300 / 300개 | 150 / 450개 | 150 / 450개 |
| 새 출고분 중 기한 내 / 기한 후 | 600 / 0개 | 250 / 350개 | 200 / 400개 |

정상 계산: 남은 양품 300개 ÷ 시간당 100개 = 3시간이므로 13시 생산 완료 예상,
준비 30분과 운송 2시간을 더하면 전량 도착 예상은 15:30이다. 16시 약속을 충족한다.

지연 계산: 남은 양품 450개 ÷ 시간당 50개 = 9시간이므로 19시 생산 완료 예상이다.
준비 30분과 운송 2시간 30분을 더하면 전량 도착 예상은 22시다.
15시까지 도착하려면 12시까지 확보되어야 하므로 작업 양품 150개(관측 50 + 이후 100)와
도착·운송·창고 300개를 합쳐 450개다. 운송 3시간 30분으로 바뀌면 확보 마감은 11시,
작업 양품 100개 + 다른 물량 300개 = 400개가 된다. 야간 작업·출고 가능 여부나
차량·휴무 조건은 계산에 포함하지 않으므로 담당자 확인이 필요하다.

### 설명·초안의 검토 기준

- 정상: 부족 0개와 조건부 전량 도착을 설명한다. 불필요한 지연·추가 생산·변경 일정 협의를 만들지 않는다.
- 지연: 부족 350개는 기한 후 도착 예상이다. 이를 새 추가 생산량이나 확정된 납품 실패로 단정하지 않는다.
- 물류 요청: 기납품 100개·운송 중 100개는 새 출고에서 제외한다. 정상은 확보 300개/미래 300개,
  지연은 확보 150개/미래 450개로 구분한다.
- 고객 안내: 약속 수량·기한, 기한 내/후 예상, 전체 도착 시각과 계산 조건을 포함한다. 아직 일정에 합의한 것으로 쓰지 않는다.
- 변경: 기한 내 50개 감소·부족 50개 증가·전체 도착 1시간 지연을 설명한다. 생산 완료 19시와 새 출고량 600개는 유지한다.
- 세 상황 모두 미등록 가상 입력의 검토다. 실제 출고·고객 발송·승인·저장·도착 보증을 수행했다고 표현하지 않는다.

## 예제를 넣어보는 방법 1 — 무료 입력·계산 확인

새 worktree에서 기존 가상환경을 사용한다. DB·서버·모델 키가 필요 없으며 파일을 읽고
기존 함수가 만든 추출·계산 자료를 출력한다. **실제 LLM 답변이나 공통 Host 실행 결과는 아니다.**

```bash
cd /Users/junhwa/Desktop/Ontol/ontologics-b-delivery-ui-ab

B_DEMO_INPUT="data/order_fulfillment/examples/demo_normal_800.md"
B_DEMO_CASE="cq04_mixed"

../ontologics/.venv/bin/python -B - "$B_DEMO_INPUT" "$B_DEMO_CASE" <<'PY'
import json
from pathlib import Path
import sys
from examples.order_fulfillment.b_terminal_input import load_input, case_task
from ontologics.order_fulfillment.contracts import extract_contract

source = load_input(Path(sys.argv[1]).resolve())
case = sys.argv[2]
_, task = case_task(case, source)
data = task.data()
if case == 'cq01_intake':
    result = extract_contract(data['source_text'], data['catalog_nodes'],
                              evidence_ids=data['evidence_ids_by_part'])
elif case == 'cq01_promise':
    result = data['promise']
else:
    result = data['assessment']
print(json.dumps(result, ensure_ascii=False, indent=2))
PY
```

두 변수만 바꾼 뒤 같은 명령을 실행한다.

| 확인할 내용 | `B_DEMO_INPUT` 파일 이름 | `B_DEMO_CASE` |
| --- | --- | --- |
| 계약 원문 추출 | 정상 또는 지연 | `cq01_intake` |
| 계약의 약속 | 정상 또는 지연 | `cq01_promise` |
| 물량 상태 | 정상 또는 지연 | `cq02_normal` |
| 해당 작업 생산 완료 예상 | 정상 또는 지연 | `cq03_normal` |
| 정상 도착 예상 | `demo_normal_800.md` | `cq04_mixed` |
| 지연 도착 예상 | `demo_risk_800.md` | `cq04_mixed` |
| 대응 초안 계산 자료 | 정상 또는 지연 | `cq05_mixed` |
| 세 번째 상황: 운송 2시간 30분→3시간 30분 전후 비교 | **`demo_risk_800.md`** | **`cq06_transport2h`** |

`cq02_normal`·`cq04_mixed` 등은 기존 CLI 이름이다. 정상/지연 여부는 이름이 아니라
선택한 파일의 기한·물량·조건으로 결정된다. CQ06을 정상 파일에 실행하면 16시 약속을
비교하므로 세 번째 상황과 결과가 다르다. 각 CQ는 선택한 입력으로 독립 실행한다.

## 예제를 넣어보는 방법 2 — 기존 실행기로 실제 LLM 확인

이 단계는 **유료 API와 격리 PostgreSQL 실행 환경이 필요하다. 이번 샘플 작성에서는 실행하지 않았다.**
기존에 사용한 공개 비용정책 JSON의 실제 절대 경로를 `B_DEMO_COST_POLICY`에 설정하고,
기존 모델·키·실행 환경 구성을 사용한다. 모델 응답의 한국어 의미 품질은 위 기준으로 별도 검토한다.

새 worktree의 격리 실행기는 **그 worktree의 `.venv` 의존성과 `.env.local`**을 사용한다.
앞의 무료 확인처럼 다른 worktree의 Python을 실행하는 것만으로 격리 실행 환경까지 연결되지는 않는다.
아직 준비하지 않았다면 다음을 한 번 실행한다. 기존 저장소의 환경 파일을 새 worktree에
로컬 복사하며 기존 파일은 덮어쓰지 않는다. `.venv/`와 `.env.local`은 Git 제외 대상이다.

```bash
cd /Users/junhwa/Desktop/Ontol/ontologics-b-delivery-ui-ab
python3 -m venv .venv
.venv/bin/python -m pip install -e .
cp -n ../ontologics/.env.local .env.local
```

```bash
cd /Users/junhwa/Desktop/Ontol/ontologics-b-delivery-ui-ab

B_DEMO_INPUT="data/order_fulfillment/examples/demo_normal_800.md"
B_DEMO_CASE="cq04_mixed"
B_DEMO_OUT="/private/tmp/b-demo-$(date +%Y%m%d-%H%M%S)-$RANDOM"

# B_DEMO_COST_POLICY를 기존 공개 비용정책 JSON의 실제 절대 경로로 설정한 뒤 실행한다.
.venv/bin/python -B -m examples.order_fulfillment.b_run run \
  --output "$B_DEMO_OUT" \
  --input "$PWD/$B_DEMO_INPUT" \
  --case "$B_DEMO_CASE" \
  --cost-policy "${B_DEMO_COST_POLICY:?기존 공개 비용정책 JSON의 절대 경로를 먼저 설정하세요}" \
  --transport openai \
  --representative-max-cost-usd 1.00

.venv/bin/python -B -m examples.order_fulfillment.b_run status \
  --output "$B_DEMO_OUT"
```

`--output`은 매번 새 경로를 사용한다. 1.00달러는 기존 실행기의 대표 실행당 상한 설정이며
전체 시연 비용이나 성공을 보장하는 값이 아니다. 정상 도착 예상부터 한 건씩 확인한 후,
지연·대응 초안·변경 비교를 위 표의 파일과 case로 선택한다. 실패한 출력 경로를 재사용하지 않는다.
이 입력을 만든 것만으로 기존 웹 DB나 화면에 샘플이 등록되지 않는다.
