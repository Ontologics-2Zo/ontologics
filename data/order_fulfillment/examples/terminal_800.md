# 새 터미널 입력 사례 — B-TERMINAL-800

개발자가 새로 작성한 가상 계약·운영 관측·계산 가정이다. 실제 회사의 거래,
생산, 검사, 출하 기록이 아니다. 제품 정의만 기존 OLIMEX 공개 카탈로그에 연결한다.
이 입력을 검토해도 계약 등록·업무 승인·실제 출고는 이루어지지 않는다.

## 계약 표

| 항목 | 원문 값 |
| --- | --- |
| 계약 번호 | B-TERMINAL-800 |
| 우리 회사 식별 | DEMO-B |
| 고객사 | 가상고객사 B |
| 제품 모델 | ESP32-POE |
| 설계 개정 | L |
| 구성 | WROOM |
| 계약 항목 번호 | 1 |
| 계약 수량 | 800 |
| 수량 단위 | piece |
| 납품 회차 | 1 |
| 회차 약속 수량 | 800 |
| 납품 기한 | 2026-10-30T15:00:00+09:00 |
| 기한 기준 | 고객 도착 |
| 납품 장소 | 가상고객사 B 제2수령창고 |

## 운영 현황

아래 JSON은 새 가상 입력의 명시적인 관측값이다. 수량 단위는 완성 보드 개수,
생산 관측은 누적값, 시간은 한국 시간이다. confirmations의 true는 이 가상
사례에서만 완전성·물량 중복 없음 등을 가정한다는 뜻이며 실제 조사 결과가 아니다.
최근 시간당 양품 100개 추세가 계속되고, 양품이 출고에 사용 가능하며, 준비 30분과
운송 2시간이 유지되고, 물량이 준비되는 대로 충분한 분할 출고가 가능하다고 가정한다.
CQ06은 기준 시각과 다른 조건을 고정하고 운송 가정만 3시간으로 바꾼다.

```json
{
  "as_of": "2026-10-30T10:00:00+09:00",
  "queried_at": "2026-10-30T10:05:00+09:00",
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
    "stock_observed_at": "2026-10-30T10:00:00+09:00",
    "location": "가상 완제품창고 B"
  },
  "production": {
    "started_at": "2026-10-30T09:00:00+09:00",
    "observed_at": "2026-10-30T10:00:00+09:00",
    "target_quantity": 400,
    "completed_quantity": 100,
    "production_state": "in_progress",
    "observations": [
      {"observed_at": "2026-10-30T09:00:00+09:00", "produced_quantity": 0, "good_quantity": 0, "defective_quantity": 0, "unit": "piece"},
      {"observed_at": "2026-10-30T10:00:00+09:00", "produced_quantity": 120, "good_quantity": 100, "defective_quantity": 20, "unit": "piece"}
    ]
  },
  "delivered": {
    "quantity": 100,
    "stock_observed_at": "2026-10-30T08:00:00+09:00",
    "location": "가상 출하 전 창고 D",
    "departed_at": "2026-10-30T08:30:00+09:00",
    "arrived_at": "2026-10-30T09:00:00+09:00"
  },
  "in_transit": {
    "quantity": 100,
    "stock_observed_at": "2026-10-30T08:00:00+09:00",
    "location": "가상 출하 전 창고 T",
    "departed_at": "2026-10-30T09:30:00+09:00",
    "expected_arrival_at": "2026-10-30T12:00:00+09:00"
  },
  "condition": {
    "preparation_seconds": 1800,
    "transport_seconds": 7200,
    "dispatch_mode": "split_available"
  },
  "comparison_transport_seconds": 10800
}
```
