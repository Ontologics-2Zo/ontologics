"""Evidence-aware point/period installation queries, with no storage side effects.

All intervals are half-open. ``confirmed_until`` means persistence is supported
*before* that instant; it never means removal or indefinite persistence. Complete
coverage must attest the full installation/removal history of a unit, including
its state at the coverage start. This module does not infer operation or defects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import combinations
from typing import Any

RULE_VERSION = "temporal-0.1"


def parse_time(value: str) -> datetime:
    """Parse a full, timezone-aware ISO instant; never invent a missing time."""
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("TIME_REQUIRES_EXPLICIT_INSTANT: use ISO datetime and timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("INVALID_TIME: " + value) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("TIME_REQUIRES_TIMEZONE: " + value)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _index(items: Any, name: str) -> dict[str, dict]:
    if isinstance(items, dict):
        items = list(items.values())
    if not isinstance(items, (list, tuple)):
        raise ValueError("INVALID_COLLECTION: " + name)
    result = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise ValueError("MISSING_ID: " + name)
        if item["id"] in result:
            raise ValueError("DUPLICATE_ID: " + item["id"])
        result[item["id"]] = item
    return result


def _evidence(item: dict) -> list[str]:
    refs = item.get("evidence")
    if not isinstance(refs, list) or not refs or any(not isinstance(x, str) or not x for x in refs):
        raise ValueError("MISSING_EVIDENCE: " + str(item.get("id", "unknown")))
    return refs


def _normalize_query(query: dict, unit_index: dict, lot_index: dict) -> tuple[dict, datetime, datetime]:
    if not isinstance(query, dict) or query.get("mode") not in ("A", "B"):
        raise ValueError("INVALID_QUERY_MODE: expected A or B")
    if query.get("lot_id") not in lot_index:
        raise ValueError("UNKNOWN_LOT: " + str(query.get("lot_id")))
    unit_ids = query.get("unit_ids")
    if not isinstance(unit_ids, list) or not unit_ids or any(not isinstance(x, str) for x in unit_ids):
        raise ValueError("EXPLICIT_UNIT_SCOPE_REQUIRED")
    if len(set(unit_ids)) != len(unit_ids):
        raise ValueError("DUPLICATE_UNIT_SCOPE")
    if any(x not in unit_index for x in unit_ids):
        raise ValueError("UNKNOWN_UNIT_IN_SCOPE")
    excluded = query.get("exclude_unit_id")
    if excluded is not None and excluded not in unit_ids:
        raise ValueError("EXCLUDED_UNIT_OUTSIDE_SCOPE")
    normalized = {"mode": query["mode"], "lot_id": query["lot_id"], "unit_ids": list(unit_ids)}
    if excluded is not None:
        normalized["exclude_unit_id"] = excluded
    if query["mode"] == "A":
        if "start" in query or "end" in query:
            raise ValueError("POINT_QUERY_HAS_PERIOD_FIELDS")
        start = end = parse_time(query.get("at"))
        normalized["at"] = _iso(start)
    else:
        if "at" in query:
            raise ValueError("PERIOD_QUERY_HAS_POINT_FIELD")
        if query.get("period_meaning", "installation_overlap") != "installation_overlap":
            raise ValueError("UNSUPPORTED_PERIOD_MEANING")
        start, end = parse_time(query.get("start")), parse_time(query.get("end"))
        if start >= end:
            raise ValueError("EMPTY_OR_REVERSED_PERIOD")
        normalized.update(start=_iso(start), end=_iso(end), period_meaning="installation_overlap")
    return normalized, start, end


def _prepare_installations(items: Any, units: dict, lots: dict) -> list[dict]:
    prepared = []
    for record in _index(items, "installations").values():
        if record.get("unit_id") not in units or record.get("lot_id") not in lots:
            raise ValueError("UNKNOWN_INSTALLATION_REFERENCE: " + record["id"])
        if not isinstance(record.get("position"), str) or not record["position"]:
            raise ValueError("INSTALLATION_POSITION_REQUIRED: " + record["id"])
        evidence = _evidence(record)
        start = parse_time(record.get("installed_at"))
        removed = parse_time(record["removed_at"]) if record.get("removed_at") is not None else None
        confirmed = parse_time(record["confirmed_until"]) if record.get("confirmed_until") is not None else None
        if removed is not None and removed <= start:
            raise ValueError("INVALID_REMOVAL_ORDER: " + record["id"])
        if confirmed is not None and confirmed <= start:
            raise ValueError("INVALID_CONFIRMATION_ORDER: " + record["id"])
        if removed is not None and confirmed is not None and confirmed > removed:
            raise ValueError("CONFIRMATION_AFTER_REMOVAL: " + record["id"])
        if not isinstance(record.get("conflict", False), bool):
            raise ValueError("INVALID_CONFLICT_FLAG: " + record["id"])
        prepared.append({"record": record, "start": start, "removed": removed,
                         "known_end": removed or confirmed, "evidence": evidence,
                         "conflict_ids": [record["id"]] if record.get("conflict") else []})
    # Different lots cannot simultaneously occupy one individual designator.
    # Only demonstrated overlap creates a conflict: an unknown end is not infinity.
    for left, right in combinations(prepared, 2):
        a, b = left["record"], right["record"]
        if (a["unit_id"], a["position"]) != (b["unit_id"], b["position"]) or a["lot_id"] == b["lot_id"]:
            continue
        if _known_overlap(left, right):
            left["conflict_ids"].append(b["id"])
            right["conflict_ids"].append(a["id"])
    return prepared


def _known_overlap(left: dict, right: dict) -> bool:
    ls, rs, le, re = left["start"], right["start"], left["known_end"], right["known_end"]
    if ls == rs:
        return True
    if ls < rs:
        return le is not None and rs < le
    return re is not None and ls < re


def _prepare_coverage(items: Any, units: dict) -> list[dict]:
    prepared = []
    for record in _index(items, "coverage").values():
        if record.get("unit_id") not in units:
            raise ValueError("UNKNOWN_COVERAGE_UNIT: " + record["id"])
        if not isinstance(record.get("complete"), bool):
            raise ValueError("INVALID_COVERAGE_COMPLETENESS: " + record["id"])
        evidence = _evidence(record)
        start, end = parse_time(record.get("start")), parse_time(record.get("end"))
        if start >= end:
            raise ValueError("EMPTY_OR_REVERSED_COVERAGE: " + record["id"])
        prepared.append({"record": record, "start": start, "end": end, "evidence": evidence})
    return prepared


def _coverage_for(unit_id: str, coverages: list[dict], mode: str, start: datetime, end: datetime) -> tuple[bool, list[str]]:
    relevant = [c for c in coverages if c["record"]["unit_id"] == unit_id and c["record"]["complete"]
                and c["start"] <= (start if mode == "A" else end) and c["end"] > start]
    refs = sorted({ref for c in relevant for ref in c["evidence"]})
    if mode == "A":
        return any(c["start"] <= start < c["end"] for c in relevant), refs
    cursor = start
    for c in sorted(relevant, key=lambda item: item["start"]):
        if c["start"] > cursor:
            break
        cursor = max(cursor, c["end"])
        if cursor >= end:
            return True, refs
    return False, refs


def investigate(query: dict, units: Any, lots: Any, installations: Any, coverage: Any) -> dict:
    """Classify an explicit unit universe for one scoped component lot.

    ``included`` proves the requested installation condition, not a defect or
    operating state. ``excluded`` additionally requires complete history across
    the query. Conflicting or unsupported persistence remains unresolved.
    """
    unit_index, lot_index = _index(units, "units"), _index(lots, "lots")
    normalized, start, end = _normalize_query(query, unit_index, lot_index)
    prepared = _prepare_installations(installations, unit_index, lot_index)
    coverages = _prepare_coverage(coverage, unit_index)
    by_id = {i["record"]["id"]: i for i in prepared}
    results = []
    for unit_id in normalized["unit_ids"]:
        if unit_id == normalized.get("exclude_unit_id"):
            continue
        complete, coverage_refs = _coverage_for(unit_id, coverages, normalized["mode"], start, end)
        matched, unresolved, reasons = [], [], []
        refs = set(coverage_refs)
        records = [i for i in prepared if i["record"]["unit_id"] == unit_id and i["record"]["lot_id"] == normalized["lot_id"]]
        for item in records:
            record, installed, removed, known_end = item["record"], item["start"], item["removed"], item["known_end"]
            refs.update(item["evidence"])
            if normalized["mode"] == "A":
                possible = installed <= start and (removed is None or start < removed)
                proven = installed <= start and ((known_end is not None and start < known_end) or start == installed)
                tail_unknown = possible and not proven
            else:
                possible = installed < end and (removed is None or removed > start)
                proven = installed < end and ((known_end is not None and known_end > start) or start <= installed < end)
                tail_unknown = possible and removed is None and (known_end is None or known_end < end)
            if not possible:
                continue
            if item["conflict_ids"]:
                unresolved.append(record["id"])
                reasons.append("conflicting_installation_evidence")
                for conflict_id in item["conflict_ids"]:
                    refs.update(by_id[conflict_id]["evidence"])
                continue
            if proven:
                matched.append({"installation_id": record["id"], "start": _iso(installed),
                                "end": _iso(known_end) if known_end else None,
                                "end_status": "removed" if removed else ("confirmed_until" if known_end else "unknown"),
                                "proof": "confirmed_interval" if known_end else "installation_event"})
            if tail_unknown:
                unresolved.append(record["id"])
                reasons.append("removal_or_later_installation_state_unknown")
        if matched:
            status = "included"
            reasons.insert(0, "installation_condition_confirmed")
        elif unresolved:
            status = "needs_confirmation"
        elif complete:
            status = "excluded"
            reasons.append("no_matching_installation_in_complete_history")
        else:
            status = "needs_confirmation"
        if not complete:
            reasons.append("history_scope_incomplete")
        results.append({"unit_id": unit_id, "status": status,
                        "reasons": list(dict.fromkeys(reasons)), "evidence": sorted(refs),
                        "matched_intervals": matched, "unresolved_intervals": sorted(set(unresolved)),
                        "coverage_complete": complete})
    return {"query": normalized, "rule_version": RULE_VERSION, "results": results}
