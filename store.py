import os
import sqlite3
import threading
from pathlib import Path

from planner import Month

DB = os.environ.get("ROSTER_DB") or ("/tmp/roster.db" if os.environ.get("VERCEL") else str(Path(__file__).with_name("roster.db")))

SCHEMA = """
CREATE TABLE pods(id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  lead_id INTEGER REFERENCES employees(id) ON DELETE SET NULL);
CREATE TABLE employees(id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  pod_id INTEGER REFERENCES pods(id) ON DELETE SET NULL,
  offs INTEGER NOT NULL DEFAULT 1, pref_off INTEGER, max_hours INTEGER NOT NULL DEFAULT 9);
CREATE TABLE brands(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE processes(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE lines(id INTEGER PRIMARY KEY,
  brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  process_id INTEGER NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
  need TEXT NOT NULL DEFAULT '1111110', holidays INTEGER NOT NULL DEFAULT 0,
  start_h INTEGER NOT NULL DEFAULT 9, end_h INTEGER NOT NULL DEFAULT 18, UNIQUE(brand_id, process_id));
CREATE TABLE assignments(id INTEGER PRIMARY KEY,
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  line_id INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
  backup INTEGER NOT NULL DEFAULT 0, UNIQUE(employee_id, line_id));
CREATE TABLE holidays(id INTEGER PRIMARY KEY, day TEXT NOT NULL UNIQUE, name TEXT NOT NULL);
CREATE TABLE leaves(id INTEGER PRIMARY KEY,
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, date_from TEXT NOT NULL, date_to TEXT NOT NULL);
CREATE TABLE rosters(month TEXT PRIMARY KEY, published TEXT);
CREATE TABLE cells(month TEXT NOT NULL,
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  day TEXT NOT NULL, status TEXT NOT NULL,
  cover INTEGER REFERENCES lines(id) ON DELETE SET NULL,
  locked INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(employee_id, day));
CREATE TABLE changes(id INTEGER PRIMARY KEY, month TEXT NOT NULL, at TEXT NOT NULL, text TEXT NOT NULL);
"""

DEMO = "2026-12"
SIX, DAILY = "1111110", "1111111"
PODS = {"Atlas": "Meera Iyer", "Beacon": "Kabir Malhotra", "Cedar": "Ananya Gupta", "Delta": "Nikhil Bansal", "Ember": "Tara D'Souza"}
STAFF = [
    ("Meera Iyer", "Atlas", 6), ("Arjun Rao", "Atlas", None), ("Neha Kapoor", "Atlas", 5), ("Vikram Shah", "Atlas", 6),
    ("Sana Qureshi", "Atlas", None), ("Rohan Das", "Atlas", None),
    ("Kabir Malhotra", "Beacon", 6), ("Ishita Sen", "Beacon", 6), ("Aditya Kumar", "Beacon", 6), ("Farah Khan", "Beacon", 6),
    ("Priya Nair", "Beacon", 6), ("Manav Joshi", "Beacon", 6), ("Diya Menon", "Beacon", 6),
    ("Ananya Gupta", "Cedar", 6), ("Rahul Verma", "Cedar", 5), ("Pooja Pillai", "Cedar", 6), ("Karan Mehta", "Cedar", None),
    ("Tanvi Bhatt", "Cedar", 2), ("Siddharth Jain", "Cedar", 6),
    ("Nikhil Bansal", "Delta", 6), ("Lakshmi Reddy", "Delta", 6), ("Varun Chopra", "Delta", None), ("Zoya Ali", "Delta", 3),
    ("Rhea Sharma", "Delta", 6), ("Omkar Patil", "Delta", 6), ("Imran Sheikh", "Delta", 6),
    ("Tara D'Souza", "Ember", 6), ("Yash Agarwal", "Ember", 6), ("Kavya Rao", "Ember", 6), ("Harsh Vora", "Ember", 0),
    ("Gaurav Singh", "Ember", 6), ("Ritika Ghosh", "Ember", 6),
]
CS, ACC, NDR, RET = "Customer Support", "Abandoned Cart", "NDR", "Retention"
LINES = [  # brand, process, minimum staff Mon-Sun, open on public holidays, shift, staff, backups
    ("Tealeaf Co", CS, SIX, 0, 9, 18, ["Meera Iyer"], []),
    ("Tealeaf Co", ACC, SIX, 0, 9, 18, ["Meera Iyer"], []),
    ("Kora Skin", CS, DAILY, 0, 9, 18, ["Arjun Rao"], ["Sana Qureshi"]),
    ("Kora Skin", ACC, SIX, 0, 9, 18, ["Arjun Rao"], []),
    ("Kora Skin", NDR, SIX, 0, 9, 18, ["Arjun Rao"], []),
    ("Bloom Basket", CS, SIX, 0, 9, 18, ["Neha Kapoor"], []),
    ("Bloom Basket", NDR, SIX, 0, 9, 18, ["Neha Kapoor"], []),
    ("Urban Lane", CS, SIX, 0, 9, 18, ["Vikram Shah"], []),
    ("Urban Lane", ACC, SIX, 0, 9, 18, ["Vikram Shah"], []),
    ("Pawsome", CS, SIX, 0, 9, 18, ["Vikram Shah"], []),
    ("Nimbus Home", CS, DAILY, 0, 9, 18, ["Rohan Das"], ["Sana Qureshi"]),
    ("Spice Route", CS, SIX, 0, 10, 19, ["Kabir Malhotra"], []),
    ("Glow Theory", CS, DAILY, 1, 12, 21, ["Ishita Sen", "Aditya Kumar"], []),
    ("Glow Theory", ACC, SIX, 0, 9, 18, ["Farah Khan"], []),
    ("Glow Theory", NDR, SIX, 0, 9, 18, ["Farah Khan"], []),
    ("Hearth & Co", CS, SIX, 0, 9, 18, ["Priya Nair"], []),
    ("Hearth & Co", RET, SIX, 0, 9, 18, ["Priya Nair"], []),
    ("Peak Fuel", CS, SIX, 0, 9, 18, ["Manav Joshi"], []),
    ("Sole Story", CS, SIX, 0, 9, 18, ["Manav Joshi"], []),
    ("Sole Story", NDR, SIX, 0, 9, 18, ["Manav Joshi"], []),
    ("Little Sprout", CS, SIX, 0, 9, 18, ["Diya Menon"], []),
    ("Little Sprout", RET, SIX, 0, 9, 18, ["Diya Menon"], []),
    ("Velvet Vogue", CS, DAILY, 1, 9, 18, ["Ananya Gupta"], ["Tanvi Bhatt"]),
    ("Velvet Vogue", ACC, SIX, 0, 9, 18, ["Ananya Gupta"], []),
    ("Brewly", CS, "2222211", 0, 9, 18, ["Rahul Verma", "Pooja Pillai"], []),
    ("Brewly", RET, SIX, 0, 9, 18, ["Pooja Pillai"], []),
    ("Zestly", CS, DAILY, 0, 9, 18, ["Karan Mehta"], ["Tanvi Bhatt"]),
    ("Craft Corner", CS, SIX, 0, 9, 18, ["Siddharth Jain"], []),
    ("Craft Corner", NDR, SIX, 0, 9, 18, ["Siddharth Jain"], []),
    ("Hydra", CS, SIX, 0, 9, 18, ["Nikhil Bansal"], []),
    ("Fitkart", CS, "2222222", 1, 9, 18, ["Lakshmi Reddy", "Varun Chopra", "Zoya Ali"], []),
    ("Fitkart", ACC, SIX, 0, 9, 18, ["Lakshmi Reddy"], []),
    ("Fitkart", NDR, SIX, 0, 9, 18, ["Zoya Ali", "Varun Chopra"], []),
    ("Mellow Mats", CS, SIX, 0, 9, 18, ["Rhea Sharma"], []),
    ("Mellow Mats", ACC, SIX, 0, 9, 18, ["Rhea Sharma"], []),
    ("Loom & Leaf", CS, SIX, 0, 9, 18, ["Omkar Patil"], []),
    ("Loom & Leaf", RET, SIX, 0, 9, 18, ["Omkar Patil"], []),
    ("Volt Gear", CS, SIX, 0, 9, 18, ["Imran Sheikh"], []),
    ("Volt Gear", NDR, SIX, 0, 9, 18, ["Imran Sheikh"], []),
    ("Aura Candles", CS, SIX, 0, 9, 18, ["Tara D'Souza"], []),
    ("Snackhaus", CS, SIX, 0, 9, 18, ["Yash Agarwal"], []),
    ("Snackhaus", ACC, SIX, 0, 9, 18, ["Kavya Rao"], []),
    ("Snackhaus", RET, SIX, 0, 9, 18, ["Kavya Rao"], []),
    ("Moonlit Teas", CS, DAILY, 1, 10, 19, ["Harsh Vora"], []),
    ("Pebble Kids", CS, SIX, 0, 9, 18, ["Gaurav Singh"], []),
    ("Pebble Kids", NDR, SIX, 0, 9, 18, ["Gaurav Singh"], []),
    ("Kindred Knits", CS, SIX, 0, 9, 18, ["Ritika Ghosh"], []),
    ("Kindred Knits", ACC, SIX, 0, 9, 18, ["Ritika Ghosh"], []),
]
HOLIDAYS = [("2026-11-24", "Guru Nanak Jayanti"), ("2026-12-25", "Christmas"), ("2027-01-01", "New Year's Day"),
            ("2027-01-14", "Makar Sankranti"), ("2027-01-26", "Republic Day")]
LEAVES = [("Arjun Rao", "Leave", "2026-12-08", "2026-12-10"), ("Ishita Sen", "Leave", "2026-12-14", "2026-12-18"),
          ("Pooja Pillai", "Comp-off", "2026-12-04", "2026-12-04")]


def seed(con):
    ids = {}
    for pod in PODS:
        ids[pod] = con.execute("INSERT INTO pods(name) VALUES(?)", (pod,)).lastrowid
    for name, pod, pref in STAFF:
        ids[name] = con.execute("INSERT INTO employees(name, pod_id, pref_off) VALUES(?,?,?)", (name, ids[pod], pref)).lastrowid
    for pod, lead in PODS.items():
        con.execute("UPDATE pods SET lead_id=? WHERE id=?", (ids[lead], ids[pod]))
    for brand, process, need, hol, start, end, staff, backups in LINES:
        for name, table in ((brand, "brands"), (process, "processes")):
            if name not in ids:
                ids[name] = con.execute(f"INSERT INTO {table}(name) VALUES(?)", (name,)).lastrowid
        line = con.execute("INSERT INTO lines(brand_id, process_id, need, holidays, start_h, end_h) VALUES(?,?,?,?,?,?)",
                           (ids[brand], ids[process], need, hol, start, end)).lastrowid
        con.executemany("INSERT INTO assignments(employee_id, line_id, backup) VALUES(?,?,?)",
                        [(ids[n], line, 0) for n in staff] + [(ids[n], line, 1) for n in backups])
    con.executemany("INSERT INTO holidays(day, name) VALUES(?,?)", HOLIDAYS)
    con.executemany("INSERT INTO leaves(employee_id, kind, date_from, date_to) VALUES(?,?,?,?)",
                    [(ids[n], k, a, b) for n, k, a, b in LEAVES])


_init = threading.Lock()


def connect():
    with _init:
        fresh = not os.path.exists(DB)
        con = sqlite3.connect(DB, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        if fresh:
            con.executescript(SCHEMA)
            seed(con)
            m = Month(con, DEMO)
            m.generate()
            m.save("Roster generated")
    return con
