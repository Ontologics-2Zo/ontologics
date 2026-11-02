"""Business calendar arithmetic; no model-inferred production capacity."""
import unittest
from ontologics.materials.library_schedule import schedule_work

class WorkCalendarTests(unittest.TestCase):
    def test_breaks_bookings_staff_and_overnight_are_respected(self):
        route=dict(setup_minutes=30,unit_seconds=60,inspection_minutes=30,required_staff=2,pausable=True)
        shifts=[dict(start_at='2026-11-02T09:00:00+09:00',end_at='2026-11-02T12:00:00+09:00',staff_available=2),dict(start_at='2026-11-02T13:00:00+09:00',end_at='2026-11-02T17:00:00+09:00',staff_available=1),dict(start_at='2026-11-03T09:00:00+09:00',end_at='2026-11-03T12:00:00+09:00',staff_available=2)]
        blocks=[dict(start_at='2026-11-02T10:00:00+09:00',end_at='2026-11-02T11:00:00+09:00')]
        result=schedule_work(route,shifts,blocks,120,'2026-11-02T09:00:00+09:00')
        self.assertEqual(180,result['work_minutes'])
        self.assertEqual('2026-11-03T10:00:00+09:00',result['completion_at'])
        self.assertEqual(3,len(result['segments']))

    def test_non_pausable_work_needs_one_contiguous_slot(self):
        r=dict(setup_minutes=30,unit_seconds=60,inspection_minutes=30,required_staff=2,pausable=False)
        s=[dict(start_at='2026-11-02T09:00:00+09:00',end_at='2026-11-02T11:00:00+09:00',staff_available=2)]
        self.assertIsNone(schedule_work(r,s,[],120,'2026-11-02T09:00:00+09:00'))

    def test_overlapping_calendar_rows_do_not_double_capacity(self):
        r=dict(setup_minutes=0,unit_seconds=60,inspection_minutes=0,required_staff=2,pausable=True)
        s=[dict(start_at='2026-11-02T09:00:00+09:00',end_at='2026-11-02T10:00:00+09:00',staff_available=2)]*2
        self.assertIsNone(schedule_work(r,s,[],90,'2026-11-02T09:00:00+09:00'))
