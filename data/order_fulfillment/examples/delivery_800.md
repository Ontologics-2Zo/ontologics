# PO-261104-018 — 계약 및 운영 현황

## 계약 표

| 항목 | 원문 값 |
| --- | --- |
| 계약 번호 | PO-261104-018 |
| 우리 회사 식별 | 온결테크 |
| 고객사 | 서린제어시스템 |
| 제품 모델 | ESP32-POE |
| 설계 개정 | L |
| 구성 | WROOM |
| 계약 항목 번호 | 1 |
| 계약 수량 | 800 |
| 수량 단위 | piece |
| 납품 회차 | 1 |
| 회차 약속 수량 | 800 |
| 납품 기한 | 2026-11-05T16:00:00+09:00 |
| 기한 기준 | 고객 도착 |
| 납품 장소 | 서린제어시스템 제2수령창고 |

## 운영 현황

11월 5일 10:00 기준 배정 및 누적 실적이며, 10:05 조회 자료다. 시연 실행 시각으로
자동 이동하지 않는다. 수량은 완성 보드 개수, 생산 관측값은 해당 작업의 누적값이다.

계약 800개는 도착 100개, 운송 중 100개, 창고 200개, 생산 배정 400개로 구성한다.
생산 배정 400개에는 관측 양품 100개와 남은 생산 300개가 포함된다. 관측 양품을
창고 200개에 중복 합산하지 않는다. 새 출고 검토 대상은 미출고 배정 600개다.

계산 전제: 최근 양품 100개/시간 추세 지속, 양품의 해당 회차 사용 가능,
출고 준비 30분, 미출고분 운송 2시간, 준비되는 물량의 분할 출고 가능.
운송 변경 검토는 미출고분의 운송 시간만 3시간 30분으로 바꾼다.
이미 운송 중인 100개의 도착 예상 12:00는 그대로 유지한다.
confirmations는 이 입력에서 명시한 완전성·중복 없음의 계산 전제다.
작업 교대·휴무·차량 용량은 모델링하지 않으며 계산값은 조건부 예상이다.

```json
{
  "as_of": "2026-11-05T10:00:00+09:00",
  "queried_at": "2026-11-05T10:05:00+09:00",
  "confirmations": {
    "allocations_complete": true,
    "source_allocations_complete": true,
    "allocations_disjoint": true,
    "stock_events_complete": true,
    "stock_observations_before_departures": true,
    "shipment_events_complete": true,
    "production_state_complete": true,
    "material_disjoint": true,
    "no_reallocation": true
  },
  "warehouse": {
    "quantity": 200,
    "stock_observed_at": "2026-11-05T10:00:00+09:00",
    "location": "온결테크 완제품창고"
  },
  "production": {
    "started_at": "2026-11-05T09:00:00+09:00",
    "observed_at": "2026-11-05T10:00:00+09:00",
    "target_quantity": 400,
    "completed_quantity": 100,
    "production_state": "in_progress",
    "observations": [
      {
        "observed_at": "2026-11-05T09:00:00+09:00",
        "produced_quantity": 0,
        "good_quantity": 0,
        "defective_quantity": 0,
        "unit": "piece"
      },
      {
        "observed_at": "2026-11-05T10:00:00+09:00",
        "produced_quantity": 120,
        "good_quantity": 100,
        "defective_quantity": 20,
        "unit": "piece"
      }
    ]
  },
  "delivered": {
    "quantity": 100,
    "stock_observed_at": "2026-11-05T08:00:00+09:00",
    "location": "온결테크 출하장 1",
    "departed_at": "2026-11-05T08:30:00+09:00",
    "arrived_at": "2026-11-05T09:00:00+09:00"
  },
  "in_transit": {
    "quantity": 100,
    "stock_observed_at": "2026-11-05T08:00:00+09:00",
    "location": "온결테크 출하장 2",
    "departed_at": "2026-11-05T09:30:00+09:00",
    "expected_arrival_at": "2026-11-05T12:00:00+09:00"
  },
  "condition": {
    "preparation_seconds": 1800,
    "transport_seconds": 7200,
    "dispatch_mode": "split_available"
  },
  "comparison_transport_seconds": 12600
}
```
