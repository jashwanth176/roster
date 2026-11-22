import calendar
import re
from collections import defaultdict
from datetime import date, timedelta

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
LABEL = {"W": "working", "O": "weekly off", "H": "public holiday", "L": "on leave", "C": "comp-off", "U": "unavailable"}
KIND = {"Leave": "L", "Comp-off": "C", "Unavailable": "U"}


class Month:
    """One month of the roster: the rules and people it is planned from, and who works which day."""

    def __init__(self, con, month):
        self.con, self.month = con, month
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise ValueError(f"{month} is not a month")
        y, m = map(int, month.split("-"))
        dates = [date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)]
        self.days = [d.isoformat() for d in dates]
        self.title = dates[0].strftime("%B %Y")
        self.wd = {d.isoformat(): d.weekday() for d in dates}
        weeks = defaultdict(list)
        for d in dates:
            weeks[d.isocalendar()[:2]].append(d.isoformat())
        self.weeks = list(weeks.values())
        self.week_of = {d: w for w in self.weeks for d in w}
        self.before = [(dates[0] - timedelta(days=k)).isoformat() for k in range(7, 0, -1)]
        self.after = [(dates[-1] + timedelta(days=k)).isoformat() for k in range(1, 7 - dates[-1].weekday())]
        edge = self.before + self.after
        self.edge = {(r["employee_id"], r["day"]): r["status"] for r in con.execute(
            f"SELECT employee_id, day, status FROM cells WHERE day IN ({','.join('?' * len(edge))})", edge)}
        self.emps = {r["id"]: dict(r) for r in con.execute(
            "SELECT e.*, p.name pod FROM employees e LEFT JOIN pods p ON p.id=e.pod_id ORDER BY p.name, e.name")}
        self.lines = {r["id"]: dict(r) for r in con.execute(
            "SELECT l.*, b.name brand, p.name process FROM lines l JOIN brands b ON b.id=l.brand_id "
            "JOIN processes p ON p.id=l.process_id ORDER BY b.name, p.name")}
        self.of, self.staff, self.backup = defaultdict(set), defaultdict(list), set()
        for a in con.execute("SELECT * FROM assignments ORDER BY backup, employee_id"):
            self.of[a["employee_id"]].add(a["line_id"])
            self.staff[a["line_id"]].append(a["employee_id"])
            if a["backup"]:
                self.backup.add((a["employee_id"], a["line_id"]))
        self.hol = {r["day"]: r["name"] for r in con.execute("SELECT * FROM holidays WHERE day LIKE ?", (month + "%",))}
        self.leave = {}
        for r in con.execute("SELECT * FROM leaves WHERE date_to >= ? AND date_from <= ?", (self.days[0], self.days[-1])):
            for d in self.days:
                if r["date_from"] <= d <= r["date_to"]:
                    self.leave[(r["employee_id"], d)] = KIND.get(r["kind"], "L")
        self.st, self.cover, self.locked = {}, {}, set()
        for r in con.execute("SELECT * FROM cells WHERE month=?", (month,)):
            k = (r["employee_id"], r["day"])
            self.st[k] = r["status"]
            if r["cover"]:
                self.cover[k] = r["cover"]
            if r["locked"]:
                self.locked.add(k)
        self.exists = con.execute("SELECT 1 FROM rosters WHERE month=?", (month,)).fetchone() is not None

    def need(self, l, d):
        line = self.lines[l]
        return 0 if d in self.hol and not line["holidays"] else int(line["need"][self.wd[d]])

    def on(self, e, d):
        return self.st.get((e, d)) == "W"

    def covering(self, e, d):
        c = self.cover.get((e, d))
        return self.of[e] | {c} if c in self.lines else self.of[e]

    def have(self, l, d):
        return sum(self.on(e, d) for e in self.staff[l]) + sum(
            1 for (e, dd), c in self.cover.items() if c == l and dd == d and e not in self.staff[l] and self.on(e, d))

    def hurts(self, e, d):
        """Lines that would drop below their minimum on day d if e were off."""
        return [l for l in self.covering(e, d) if self.need(l, d) and self.have(l, d) <= self.need(l, d)]

    def closed(self, e, d):
        return not any(self.need(l, d) for l in self.of[e])

    def span(self, e, extra=None):
        lines = [self.lines[l] for l in self.of[e] | ({extra} if extra else set())]
        return max(x["end_h"] for x in lines) - min(x["start_h"] for x in lines) if lines else 0

    def req(self, e, week):
        """Weekly offs e still needs in this week, and whether they are owed now. A week cut by the month edge owes what
        the neighbouring month has not given. If that month is not planned, the off is optional: placed only where it opens
        no gap, and at the month start only for a week of four days or more (smooth() handles shorter ones)."""
        out = (self.before[len(week) - 7:] if week[0] == self.days[0] else self.after) if len(week) < 7 else []
        full = self.emps[e]["offs"]
        if not out:
            return full, True
        if any((e, d) in self.edge for d in out):
            return max(0, full - sum(self.edge.get((e, d)) == "O" for d in out)), True
        return (round(full * len(week) / 7) if week[0] == self.days[0] else full), False

    def status(self, e, d):
        return self.st.get((e, d)) if d in self.wd else self.edge.get((e, d))

    def run_before(self, e, d):
        run = 0
        for x in reversed(self.before + self.days[:self.days.index(d)]):
            if self.status(e, x) != "W":
                break
            run += 1
        return run

    def longest(self, e):
        run, best = self.run_before(e, self.days[0]), 0
        for d in self.days:
            run = run + 1 if self.on(e, d) else 0
            best = max(best, run)
        return best

    def sync(self):
        """Apply approved leave and public holidays to the roster as it stands, without re-planning anything else."""
        for e in self.emps:
            for d in self.days:
                k, s = (e, d), self.st.get((e, d))
                if k in self.leave:
                    self.st[k] = self.leave[k]
                    self.locked.add(k)
                    self.cover.pop(k, None)
                elif s in ("L", "C", "U", None) or (s == "H" and d not in self.hol):
                    self.st[k] = "W"
                    self.locked.discard(k)
                if self.st[k] == "W" and d in self.hol and self.closed(e, d):
                    self.st[k] = "H"

    def cost(self, e, d):
        lines = [l for l in self.of[e] if self.need(l, d)]
        return (100 * len(self.hurts(e, d)) + 2 * sum(self.have(l, d) - 1 == self.need(l, d) for l in lines)
                - 5 * (not lines) - 3 * (self.wd[d] == self.emps[e]["pref_off"]) + 10 * max(0, self.run_before(e, d) - 6))

    def generate(self):
        """Plan every unlocked cell: leave and holidays first, then weekly offs where they leave coverage intact."""
        for k in [(e, d) for e in self.emps for d in self.days if (e, d) not in self.locked]:
            self.cover.pop(k, None)
            self.st[k] = "W"
        self.sync()
        granted = defaultdict(int)
        for week in self.weeks:
            todo = {}
            for e in self.emps:
                n, must = self.req(e, week)
                if (n := n - sum(self.st[(e, d)] == "O" for d in week)) > 0:
                    todo[e] = [n, must]
            while todo:
                pick = None
                for e in list(todo):
                    opts = [d for d in week if self.on(e, d) and (e, d) not in self.locked and (todo[e][1] or not self.hurts(e, d))]
                    if not opts:
                        del todo[e]
                        continue
                    key = (sum(not self.hurts(e, d) for d in opts), granted[e], -self.run_before(e, week[0]), e)
                    if pick is None or key < pick[0]:
                        pick = (key, e, opts)
                if not pick:
                    break
                _, e, opts = pick
                d = min(opts, key=lambda d: (self.cost(e, d), -self.days.index(d)))
                self.st[(e, d)] = "O"
                granted[e] += self.wd[d] == self.emps[e]["pref_off"]
                todo[e][0] -= 1
                if not todo[e][0]:
                    del todo[e]
        for d in sorted(self.hol):
            for e in self.emps:
                if self.on(e, d) and (e, d) not in self.locked and not self.hurts(e, d):
                    self.st[(e, d)] = "H"
        self.repair()
        self.smooth()

    def move_to(self, e, d):
        """Another day in the same week that e could take off without leaving any line short."""
        opts = [x for x in self.week_of[d] if x != d and self.on(e, x) and (e, x) not in self.locked and not self.hurts(e, x)]
        return min(opts, key=lambda x: (self.cost(e, x), x)) if opts else None

    def repair(self):
        for _ in range(5):
            moved = False
            for l in self.lines:
                for d in self.days:
                    if self.have(l, d) >= self.need(l, d):
                        continue
                    for e in self.staff[l]:
                        if self.st.get((e, d)) == "O" and (e, d) not in self.locked and (to := self.move_to(e, d)):
                            self.st[(e, d)], self.st[(e, to)] = "W", "O"
                            moved = True
                            break
            if not moved:
                return

    def smooth(self):
        """Break runs of more than six working days: by moving a weekly off within its week without opening a gap, by giving
        a day off when all of the person's processes are closed, or at a month edge by adding the off that week would
        otherwise take in the next month, even if it opens a gap."""
        for e in self.emps:
            while (now := self.longest(e)) > 6:
                best = (now, 0, None)
                for week in self.weeks:
                    free = [x for x in week if self.on(e, x) and (e, x) not in self.locked]
                    for d in [d for d in week if self.st.get((e, d)) == "O" and (e, d) not in self.locked]:
                        for x in [x for x in free if not self.hurts(e, x)]:
                            self.st[(e, d)], self.st[(e, x)] = "W", "O"
                            best = min(best, (self.longest(e), 0, (d, x)), key=lambda b: b[:2])
                            self.st[(e, d)], self.st[(e, x)] = "O", "W"
                    edge = len(week) < 7 and not any(self.st.get((e, d)) == "O" for d in week)
                    for x in [x for x in free if edge or self.closed(e, x)]:
                        hurt = len(self.hurts(e, x))
                        self.st[(e, x)] = "O"
                        best = min(best, (self.longest(e), hurt, (None, x)), key=lambda b: b[:2])
                        self.st[(e, x)] = "W"
                if not best[2]:
                    break
                d, x = best[2]
                if d:
                    self.st[(e, d)] = "W"
                self.st[(e, x)] = "O"

    def set(self, e, d, status):
        self.st[(e, d)] = status
        self.cover.pop((e, d), None)
        self.locked.add((e, d))

    def save(self, text=None):
        self.con.execute("DELETE FROM cells WHERE month=?", (self.month,))
        self.con.executemany("INSERT INTO cells VALUES(?,?,?,?,?,?)", [
            (self.month, e, d, self.st[(e, d)], self.cover.get((e, d)), int((e, d) in self.locked))
            for e in self.emps for d in self.days])
        self.con.execute("INSERT OR IGNORE INTO rosters(month) VALUES(?)", (self.month,))
        if text:
            self.con.execute("INSERT INTO changes(month, at, text) VALUES(?, datetime('now'), ?)", (self.month, text))
        self.con.commit()
        self.exists = True

    def moves(self, l, d):
        out = []
        for e in self.staff[l]:
            s = self.st.get((e, d))
            if s == "H":
                out.append((e, None))
            elif s == "O" and (to := self.move_to(e, d)):
                out.append((e, to))
        return out

    def backups(self, l, d):
        line, pods = self.lines[l], {self.emps[e]["pod_id"] for e in self.staff[l]}
        found = []
        for e, emp in self.emps.items():
            if e in self.staff[l] or not self.on(e, d) or (e, d) in self.cover or self.span(e, l) > emp["max_hours"]:
                continue
            same = any(self.lines[x]["process_id"] == line["process_id"] for x in self.of[e])
            found.append((not same, emp["pod_id"] not in pods, sum(bool(self.need(x, d)) for x in self.of[e]), emp["name"], e))
        return [x[-1] for x in sorted(found)[:3]]

    def issues(self):
        out = {"gaps": [], "spread": [], "conflicts": [], "rules": [], "prefs": []}
        hit = defaultdict(set)
        for l, line in self.lines.items():
            days = []
            for d in self.days:
                n, h = self.need(l, d), self.have(l, d)
                if h < n:
                    absent = [(e, self.st.get((e, d), "W")) for e in self.staff[l] if not self.on(e, d)]
                    for e, _ in absent:
                        hit[(e, d)].add(line["brand"])
                    days.append({"day": d, "need": n, "have": h, "absent": absent, "moves": self.moves(l, d), "backups": self.backups(l, d)})
            if days:
                out["gaps"].append({"line": l, "days": days})
        out["spread"] = [{"emp": e, "day": d, "brands": sorted(b)} for (e, d), b in sorted(hit.items(), key=lambda x: (x[0][1], x[0][0])) if len(b) > 1]
        for e, emp in self.emps.items():
            if self.span(e) > emp["max_hours"]:
                out["conflicts"].append({"emp": e, "day": None, "hours": self.span(e)})
            for week in self.weeks:
                n, must = self.req(e, week)
                if must and sum(self.st.get((e, d)) == "O" for d in week) < min(n, sum(self.st.get((e, d)) in ("W", "O") for d in week)):
                    out["rules"].append({"emp": e, "day": week[0], "text": "has no weekly off in the week of"})
            if (best := self.longest(e)) > 6:
                out["rules"].append({"emp": e, "day": None, "text": f"works {best} days in a row"})
            p = emp["pref_off"]
            missed = sum(1 for week in self.weeks for d in week if self.wd[d] == p and self.on(e, d))
            if p is not None and missed:
                out["prefs"].append({"emp": e, "weeks": missed})
        for (e, d), l in self.cover.items():
            if self.on(e, d) and self.span(e, l) > self.emps[e]["max_hours"]:
                out["conflicts"].append({"emp": e, "day": d, "hours": self.span(e, l)})
        return out

    def blocking(self, issues=None):
        i = issues or self.issues()
        return sum(len(g["days"]) for g in i["gaps"]) + len(i["conflicts"])

    def brands(self, e):
        return sorted({self.lines[l]["brand"] for l in self.of[e]})

    def earned(self, e):
        return [d for d in sorted(self.hol) if self.on(e, d)]
