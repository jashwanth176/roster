import os
import tempfile
import unittest
from collections import Counter

os.environ["ROSTER_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

from planner import Month  # noqa: E402
from store import connect  # noqa: E402


class PlannerTest(unittest.TestCase):
    def setUp(self):
        if os.path.exists(os.environ["ROSTER_DB"]):
            os.remove(os.environ["ROSTER_DB"])
        self.con = connect()
        self.m = Month(self.con, "2026-12")
        self.m.generate()
        self.m.save()

    def tearDown(self):
        self.con.close()

    def emp(self, name):
        return next(e for e, x in self.m.emps.items() if x["name"] == name)

    def test_sample_data_matches_the_brief(self):
        m = self.m
        people = Counter(m.lines[l]["brand"] for l in m.lines for e in set(m.staff[l]) if (e, l) not in m.backup)
        brands = {b: len({e for l, x in m.lines.items() if x["brand"] == b for e in m.staff[l] if (e, l) not in m.backup}) for b in people}
        self.assertGreaterEqual(len(m.emps), 30)
        self.assertGreaterEqual(sum(n == 1 for n in brands.values()) / len(brands), 0.75)
        self.assertTrue(all(n <= 3 for n in brands.values()))
        self.assertTrue(any(len(m.staff[l]) > 1 for l in m.lines), "some process is shared")
        self.assertTrue(any(len(m.brands(e)) > 1 for e in m.emps), "someone works across brands")

    def test_hard_rules_hold(self):
        m = self.m
        for (e, d), kind in m.leave.items():
            self.assertEqual(m.st[(e, d)], kind)
        for e in m.emps:
            self.assertLessEqual(m.longest(e), 6, m.emps[e]["name"])
            for week in m.weeks:
                if len(week) == 7:
                    self.assertGreaterEqual(sum(m.st[(e, d)] == "O" for d in week), 1, (m.emps[e]["name"], week[0]))

    def test_only_unavoidable_gaps_remain(self):
        gaps = {(self.m.lines[g["line"]]["brand"], self.m.lines[g["line"]]["process"]) for g in self.m.issues()["gaps"]}
        self.assertEqual(gaps, {("Brewly", "Customer Support"), ("Brewly", "Retention"), ("Kora Skin", "Abandoned Cart"),
                                ("Kora Skin", "NDR"), ("Moonlit Teas", "Customer Support")})

    def test_new_leave_flags_the_gap_and_a_cover_closes_it(self):
        e, day = self.emp("Neha Kapoor"), "2026-12-16"
        self.con.execute("INSERT INTO leaves(employee_id, kind, date_from, date_to) VALUES(?, 'Leave', ?, ?)", (e, day, day))
        m = Month(self.con, "2026-12")
        m.sync()
        gap = next(g for g in m.issues()["gaps"] if m.lines[g["line"]]["brand"] == "Bloom Basket")
        d = next(x for x in gap["days"] if x["day"] == day)
        self.assertEqual(d["absent"], [(e, "L")])
        m.set(d["backups"][0], day, "W")
        m.cover[(d["backups"][0], day)] = gap["line"]
        self.assertGreaterEqual(m.have(gap["line"], day), m.need(gap["line"], day))

    def test_replan_keeps_manual_changes(self):
        e, day = self.emp("Ishita Sen"), "2026-12-02"
        self.m.set(e, day, "O")
        self.m.save()
        m = Month(self.con, "2026-12")
        m.generate()
        self.assertEqual(m.st[(e, day)], "O")


if __name__ == "__main__":
    unittest.main()
