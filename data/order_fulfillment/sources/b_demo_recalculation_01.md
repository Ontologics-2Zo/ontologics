# B-CQ06 independent synthetic update records v1

New project development source, authored 2026-10-27. Every entry is fictional; it is not an OLIMEX, supplier or real customer's production, transport, inspection or receipt record. Each case starts independently from the unchanged CQ04 production-only10:00 source. Cases are alternatives, not a sequence of multiple11:00 observations for the same work.

## A. Fixed business scope and source preservation

Company DEMO-B, customer 가상고객사 A, configuration config:esp32-poe:L:WROOM, contract contract:demo:B-01 and round delivery-round:demo:B-01:1 remain unchanged. The arrival deadline is2026-10-27T15:00:00+09:00 at 가상고객사 A 수령창고. Normal target1000piece and the whole-work allocation1000piece remain unchanged. The work is work:demo:CQ04:base, logical snapshot snapshot:demo:CQ04:base:10, allocation allocation:demo:CQ04:base and condition condition:demo:CQ04:base.

The unchanged CQ04 baseline records are09:00 produced550/good510/defective40 and10:00 produced650/good600/defective50. They remain separate original observations with their original IDs and Evidence. An11:00 observation gets a new ID. A changed logical work/snapshot/condition is put into a new approved data version; the old approved version is retained. Reusing a logical ID does not overwrite an old database snapshot, SourceDocument or Evidence.

## B. Renewed11:00 coverage and calculation assumptions

For each11:00 case below the fictional reviewer has rechecked the listed round's complete allocations and every other-round allocation from its sources. The nine confirmations are allocations_complete=true, source_allocations_complete=true, allocations_disjoint=true, stock_events_complete=true, stock_observations_before_departures=true, shipment_events_complete=true, production_state_complete=true, material_disjoint=true and no_reallocation=true. They apply at11:00 and are not inferred merely from the prior10:00 assertion.

The one work remains in_progress, started_at2026-10-27T08:00:00+09:00. There are no separate delivered, in-transit, warehouse or additional-work quantities, no shipments, no hidden overlapping physical quantities, and no reallocations. The work is assigned in full to this one round. The snapshot as_of and work observed_at both become2026-10-27T11:00:00+09:00, and the completed_quantity equals the case's latest good cumulative count. Existing09:00 and10:00 observation nodes remain unchanged.

Recent good-unit production continues without interruption at the latest observed interval rate; good units are usable for this round. Preparation is0seconds and dispatch_mode=split_available, with sufficient split dispatch capacity and no frequency, vehicle, calendar, shutdown, shift, rework or minimum-batch constraints. The original1hour transport condition continues unchanged unless a case explicitly changes it below. None of these assertions is an actual future completion, dispatch, arrival or permission to execute.

## C. continued11: production continues as expected

| Field | Value |
| --- | --- |
| as_of / work observed_at | 2026-10-27T11:00:00+09:00 |
| observation ID | observation:demo:CQ06:continued11:11 |
| produced_quantity / good_quantity / defective_quantity | 750 / 690 / 60 |
| work completed_quantity | 690 |
| preparation_seconds / transport_seconds / dispatch_mode | 0 / 3600 / split_available |

The10:00-to11:00 increments are100produced,90good and10defective in3600seconds. The conditional good rate remains90piece/hour. Remaining310pieces require12400seconds; production completion remains14:26:40 and full arrival15:26:40. Under the unchanged15:00 deadline,690+90*3=960pieces are on time and40are short. Present good units690 and future310 are distinct.

## D. defects11: latest interval has more defective units

| Field | Value |
| --- | --- |
| as_of / work observed_at | 2026-10-27T11:00:00+09:00 |
| observation ID | observation:demo:CQ06:defects11:11 |
| produced_quantity / good_quantity / defective_quantity | 750 / 680 / 70 |
| work completed_quantity | 680 |
| preparation_seconds / transport_seconds / dispatch_mode | 0 / 3600 / split_available |

The10:00-to11:00 increments are100produced,80good and20defective, so good rate80piece/hour and recent defective ratio20percent differ from the cumulative70/750ratio. Remaining320pieces require14400seconds; production completion15:00:00 and full arrival16:00:00. By the14:00 production cutoff,680+80*3=920pieces are on time and80are short. This is an observed arithmetic trend, not a diagnosis of equipment or material failure.

## E. transport2h: transport changes independently at10:00

Snapshot/work remain at2026-10-27T10:00:00+09:00 with completed600 and the original09:00/10:00 observations. The only changed business input is condition:demo:CQ04:base.transport_seconds=7200 instead of3600. Preparation remains0seconds, dispatch_mode remains split_available and all10:00 coverage/business scope from the original source remains unchanged. This is a condition update in a new data version, not an11:00 production observation.

Production completion remains14:26:40. Full arrival becomes16:26:40. At the13:00 production cutoff,600+90*3=870pieces are on time and130are short. Production rate and remaining production time do not change merely because transport changed.

## F. combined11: both latest production and transport change

| Field | Value |
| --- | --- |
| as_of / work observed_at | 2026-10-27T11:00:00+09:00 |
| observation ID | observation:demo:CQ06:combined11:11 |
| produced_quantity / good_quantity / defective_quantity | 750 / 680 / 70 |
| work completed_quantity | 680 |
| preparation_seconds / transport_seconds / dispatch_mode | 0 / 7200 / split_available |

This independent case combines80good/hour with2hour transport. Production completion15:00:00 and full arrival17:00:00. At the13:00 production cutoff,680+80*2=840pieces are on time and160are short. The comparison identifies both changes; it must not present the entire change as the isolated effect of one cause.

## G. zero_rate11: no new good units in the latest interval

| Field | Value |
| --- | --- |
| as_of / work observed_at | 2026-10-27T11:00:00+09:00 |
| observation ID | observation:demo:CQ06:zero_rate11:11 |
| produced_quantity / good_quantity / defective_quantity | 750 / 600 / 150 |
| work completed_quantity | 600 |
| preparation_seconds / transport_seconds / dispatch_mode | 0 / 3600 / split_available |

The latest interval adds100produced,0good and100defective. Normal completed600 and remaining400 are known, but the remaining target cannot be forecast at a zero normal rate. Estimated completion, full arrival, on-time aggregate and shortfall aggregate are unknown/null. An unknown-to-known comparison or known-to-unknown comparison has a null numeric delta; unknown does not mean zero.

## H. Interpretation of differences and approval limits

Differences are after-minus-before, not cumulative totals. The continued11 arrival-time difference is0seconds, defects11 is+2000seconds, transport2h is+3600seconds and combined11 is+5600seconds. Remaining production duration differences are respectively-3600,-1600,0and-1600seconds: remaining duration and absolute completion/arrival timestamp are distinct concepts.

Updated replies and dispatch-request content remain reviewer drafts. They do not send customer messages, register shipment requests, change customer agreements or record real shipments/arrivals. Current good units plus future production must not be described as entirely available for immediate dispatch. No model API call, real approval, operating reliability or manufacturing performance is established by this source.
