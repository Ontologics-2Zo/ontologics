import copy
import unittest

from ontologics.temporal import investigate, parse_time


class TemporalTests(unittest.TestCase):
    def setUp(self):
        self.units = [{"id": "U104"}, {"id": "U105"}]
        self.lots = [{"id": "supplier:part:L07"}, {"id": "supplier:part:L08"}]
        self.record = {"id": "I104", "unit_id": "U104", "lot_id": self.lots[0]["id"],
                       "position": "U1", "installed_at": "2026-08-20T09:00:00+09:00",
                       "evidence": ["E-install"]}
        self.point = {"mode": "A", "lot_id": self.lots[0]["id"], "unit_ids": ["U104"],
                      "at": "2026-09-20T10:00:00+09:00"}
        self.period = {"mode": "B", "lot_id": self.lots[0]["id"], "unit_ids": ["U104"],
                       "start": "2026-09-01T00:00:00+09:00", "end": "2026-09-21T00:00:00+09:00"}
        self.coverage = {"id": "C104", "unit_id": "U104", "complete": True,
                         "start": "2026-08-01T00:00:00+09:00", "end": "2026-09-21T00:00:00+09:00",
                         "evidence": ["E-complete-history"]}

    def run_query(self, query=None, records=None, coverage=None):
        return investigate(query or self.point, self.units, self.lots,
                           [self.record] if records is None else records,
                           [] if coverage is None else coverage)["results"]

    def test_u104_before_and_after_supplement(self):
        self.assertEqual("needs_confirmation", self.run_query()[0]["status"])
        self.assertEqual("needs_confirmation", self.run_query(self.period)[0]["status"])
        self.record.update(removed_at="2026-09-12T09:00:00+09:00", evidence=["E-install", "E-remove"])
        self.assertEqual("excluded", self.run_query(coverage=[self.coverage])[0]["status"])
        result = self.run_query(self.period, coverage=[self.coverage])[0]
        self.assertEqual("included", result["status"])
        self.assertIn("E-remove", result["evidence"])

    def test_unknown_end_is_not_infinity_even_with_coverage(self):
        self.assertEqual("needs_confirmation", self.run_query(coverage=[self.coverage])[0]["status"])
        self.assertEqual("needs_confirmation", self.run_query(self.period, coverage=[self.coverage])[0]["status"])

    def test_no_record_requires_full_coverage(self):
        self.assertEqual("needs_confirmation", self.run_query(records=[])[0]["status"])
        self.assertEqual("excluded", self.run_query(records=[], coverage=[self.coverage])[0]["status"])

    def test_point_half_open_boundaries(self):
        self.record["removed_at"] = "2026-09-12T09:00:00+09:00"
        query = dict(self.point, at=self.record["installed_at"])
        self.assertEqual("included", self.run_query(query)[0]["status"])
        query["at"] = self.record["removed_at"]
        self.assertEqual("excluded", self.run_query(query, coverage=[self.coverage])[0]["status"])

    def test_period_touching_boundaries_do_not_overlap(self):
        self.record["removed_at"] = self.period["start"]
        self.assertEqual("excluded", self.run_query(self.period, coverage=[self.coverage])[0]["status"])
        self.record["installed_at"] = self.period["end"]
        self.record["removed_at"] = "2026-10-01T00:00:00+09:00"
        self.assertEqual("excluded", self.run_query(self.period, coverage=[self.coverage])[0]["status"])

    def test_installation_inside_period_proves_overlap_without_infinite_end(self):
        self.record["installed_at"] = "2026-09-10T09:00:00+09:00"
        result = self.run_query(self.period)[0]
        self.assertEqual("included", result["status"])
        self.assertEqual("installation_event", result["matched_intervals"][0]["proof"])
        self.assertIsNone(result["matched_intervals"][0]["end"])
        self.assertEqual(["I104"], result["unresolved_intervals"])

    def test_finite_current_confirmation(self):
        self.record["confirmed_until"] = self.point["at"]
        self.assertEqual("needs_confirmation", self.run_query()[0]["status"])
        self.point["at"] = "2026-09-20T09:59:59+09:00"
        self.assertEqual("included", self.run_query()[0]["status"])

    def test_multiple_intervals_one_proof_suffices_with_other_unknowns_visible(self):
        known = dict(self.record, id="I105", position="U2", installed_at="2026-09-02T00:00:00+09:00",
                     removed_at="2026-09-22T00:00:00+09:00")
        result = self.run_query(records=[self.record, known])[0]
        self.assertEqual("included", result["status"])
        self.assertEqual(["I104"], result["unresolved_intervals"])

    def test_same_slot_conflicting_lots(self):
        self.record["removed_at"] = "2026-09-22T00:00:00+09:00"
        other = dict(self.record, id="I-other", lot_id=self.lots[1]["id"],
                     installed_at="2026-09-10T00:00:00+09:00", evidence=["E-other"])
        result = self.run_query(records=[self.record, other], coverage=[self.coverage])[0]
        self.assertEqual("needs_confirmation", result["status"])
        self.assertIn("conflicting_installation_evidence", result["reasons"])
        self.assertIn("E-other", result["evidence"])

    def test_unknown_overlap_not_fabricated_as_conflict(self):
        other = dict(self.record, id="I-other", lot_id=self.lots[1]["id"],
                     installed_at="2026-09-10T00:00:00+09:00", removed_at="2026-09-22T00:00:00+09:00")
        result = self.run_query(records=[self.record, other])[0]
        self.assertNotIn("conflicting_installation_evidence", result["reasons"])
        self.assertEqual("needs_confirmation", result["status"])

    def test_coverage_union_and_gap(self):
        first = dict(self.coverage, end="2026-09-10T00:00:00+09:00")
        second = dict(self.coverage, id="C2", start=first["end"])
        self.assertEqual("excluded", self.run_query(self.period, records=[], coverage=[first, second])[0]["status"])
        second["start"] = "2026-09-10T00:00:01+09:00"
        self.assertEqual("needs_confirmation", self.run_query(self.period, records=[], coverage=[first, second])[0]["status"])

    def test_incomplete_coverage_cannot_exclude(self):
        self.coverage["complete"] = False
        self.assertEqual("needs_confirmation", self.run_query(records=[], coverage=[self.coverage])[0]["status"])

    def test_timezone_equivalence_and_input_immutable(self):
        before = copy.deepcopy(self.point)
        self.record["removed_at"] = "2026-09-22T00:00:00+09:00"
        seoul = self.run_query()[0]["status"]
        utc = self.run_query(dict(self.point, at="2026-09-20T01:00:00Z"))[0]["status"]
        self.assertEqual(seoul, utc)
        self.assertEqual(before, self.point)

    def test_explicit_target_scope_and_excluded_reference_unit(self):
        query = dict(self.point, unit_ids=["U104", "U105"], exclude_unit_id="U104")
        results = self.run_query(query)
        self.assertEqual(["U105"], [r["unit_id"] for r in results])
        self.assertEqual("needs_confirmation", results[0]["status"])

    def test_invalid_dates_and_scope_rejected(self):
        for value in ("2026-09-20", "2026-09-20T10:00:00", "nonsense", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_time(value)
        for changes in ({"unit_ids": []}, {"unit_ids": ["U104", "U104"]}, {"lot_id": "L07"},
                        {"unit_ids": ["unknown"]}, {"exclude_unit_id": "U105"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_query(dict(self.point, **changes))
        with self.assertRaises(ValueError):
            self.run_query(dict(self.period, end=self.period["start"]))

    def test_missing_evidence_and_impossible_interval_rejected(self):
        with self.assertRaises(ValueError):
            self.run_query(records=[dict(self.record, evidence=[])])
        with self.assertRaises(ValueError):
            self.run_query(records=[dict(self.record, removed_at=self.record["installed_at"])])


if __name__ == "__main__":
    unittest.main()
