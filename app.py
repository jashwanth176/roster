import csv
import io
import json
import os
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from planner import DAYS, KIND, LABEL, Month, now
from store import INTEGRITY, connect

app = FastAPI()
T = Jinja2Templates(directory=Path(__file__).with_name("templates"))


def cal(x):
    """A brand process's operating calendar in words, e.g. "Mon–Sat, holidays off" or "365 days · M2 T2 W2 T2 F2 S1 S1"."""
    days = [DAYS[i] for i in range(7) if x["need"][i] != "0"]
    label = ("Daily" if len(days) == 7 else "Mon–Sat" if days == list(DAYS[:6]) else "Mon–Fri" if days == list(DAYS[:5])
             else ", ".join(days) or "Closed")
    label = "365 days" if len(days) == 7 and x["holidays"] else label + ("" if x["holidays"] else ", holidays off")
    return label + (" · " + " ".join(f"{d[0]}{n}" for d, n in zip(DAYS, x["need"])) if max(x["need"]) > "1" else "")


TABLES = {
    "employees": ("Employees", [("name", "Name", "text"), ("pod_id", "Pod", "pods"), ("offs", "Weekly offs", "int"),
                                ("pref_off", "Preferred off", "weekday"), ("max_hours", "Max hours a day", "int")]),
    "pods": ("Pods", [("name", "Name", "text"), ("lead_id", "Pod lead", "employees")]),
    "brands": ("Brands", [("name", "Name", "text")]),
    "processes": ("Processes", [("name", "Name", "text")]),
    "lines": ("Coverage rules", [("brand_id", "Brand", "brands"), ("process_id", "Process", "processes"),
                                 ("need", "Minimum staff, Mon to Sun", "week"), ("holidays", "Open on public holidays", "bool"),
                                 ("start_h", "Shift starts", "hour"), ("end_h", "Shift ends", "hour")]),
    "assignments": ("Assignments", [("employee_id", "Employee", "employees"), ("line_id", "Brand and process", "lines"),
                                    ("backup", "Backup only", "bool")]),
    "leaves": ("Leave and comp-offs", [("employee_id", "Employee", "employees"), ("kind", "Type", "kind"),
                                       ("date_from", "From", "date"), ("date_to", "To", "date")]),
    "holidays": ("Public holidays", [("day", "Date", "date"), ("name", "Name", "text")]),
}
T.env.globals.update(DAYS=DAYS, LABEL=LABEL, tables=TABLES, ai=lambda: bool(os.environ.get("NVIDIA_API_KEY")))
T.env.filters["cal"] = cal


def db():
    con = connect()
    try:
        yield con
    finally:
        con.close()


def back(path, **q):
    return RedirectResponse(f"{path}?{urlencode({k: v for k, v in q.items() if v is not None})}", status_code=303)


@app.exception_handler(KeyError)
@app.exception_handler(ValueError)
async def invalid(request, err):
    return back("/roster", msg=f"Nothing changed: {err}." if isinstance(err, ValueError) else "Nothing changed: that request was not valid.")


def latest(con):
    r = con.execute("SELECT month FROM rosters ORDER BY month DESC").fetchone()
    t = date.today()
    return r["month"] if r else f"{t.year + (t.month == 12)}-{t.month % 12 + 1:02d}"


def options(con, ref):
    if ref == "lines":
        return [(r["id"], f'{r["brand"]} · {r["process"]}') for r in con.execute(
            "SELECT l.id, b.name brand, p.name process FROM lines l JOIN brands b ON b.id=l.brand_id "
            "JOIN processes p ON p.id=l.process_id ORDER BY 2, 3")]
    return [(r["id"], r["name"]) for r in con.execute(f"SELECT id, name FROM {ref} ORDER BY name")]


def impact(con, month, before):
    m = Month(con, month)
    after = m.blocking()
    if after == before:
        return f"Validated: {after} blocking issue{'s' * (after != 1)} in {m.title}, unchanged."
    return f"Validated: blocking issues in {m.title} went from {before} to {after}."


@app.get("/")
def home(con=Depends(db)):
    return back("/roster", month=latest(con))


@app.get("/roster", response_class=HTMLResponse)
def roster(request: Request, month: str = "", view: str = "company", id: int = 0, day: str = "", msg: str = "", con=Depends(db)):
    m = Month(con, month or latest(con))
    issues = m.issues() if m.exists else None
    choices = {"pod": options(con, "pods"), "brand": options(con, "brands"), "process": options(con, "processes"),
               "employee": [(e, x["name"]) for e, x in sorted(m.emps.items(), key=lambda x: x[1]["name"])]}
    if view in choices and not id and choices[view]:
        id = choices[view][0][0]
    lines = {"brand": [l for l, x in m.lines.items() if x["brand_id"] == id],
             "process": [l for l, x in m.lines.items() if x["process_id"] == id],
             "employee": sorted(m.of[id], key=list(m.lines).index),
             "pod": [l for l in m.lines if any(m.emps[e]["pod_id"] == id for e in m.staff[l])]}.get(view, list(m.lines))
    emps = {"pod": [e for e in m.emps if m.emps[e]["pod_id"] == id], "employee": [id] if id in m.emps else []}.get(
        view, list(dict.fromkeys(e for l in lines for e in m.staff[l])) if view in ("brand", "process") else list(m.emps))
    if issues and view != "company":
        keep = set(lines)
        issues = {"gaps": [g for g in issues["gaps"] if g["line"] in keep],
                  **{k: [x for x in issues[k] if x["emp"] in emps] for k in ("spread", "conflicts", "rules", "prefs")}}
    tally = Counter(m.st.get((id, d)) for d in m.days) if view == "employee" and id in m.emps else None
    pods = {r["id"]: dict(r) for r in con.execute("SELECT p.*, e.name lead FROM pods p LEFT JOIN employees e ON e.id=p.lead_id")}
    pub = con.execute("SELECT published FROM rosters WHERE month=?", (m.month,)).fetchone()
    changes = con.execute("SELECT * FROM changes WHERE month=? AND at >= ? ORDER BY id DESC", (m.month, pub["published"] if pub and pub["published"] else "")).fetchall()
    return T.TemplateResponse(request, "roster.html", {
        "m": m, "issues": issues, "view": view, "id": id, "choices": choices, "lines": lines, "emps": emps, "pods": pods,
        "day": day if day in m.wd else "", "msg": msg, "published": pub["published"] if pub else None, "changes": changes,
        "blocking": m.blocking(issues) if issues else 0, "locked": len(m.locked - set(m.leave)),
        "tally": tally, "earned": m.earned(id) if tally else []})


@app.post("/roster/generate")
async def generate(request: Request, con=Depends(db)):
    f = await request.form()
    m = Month(con, f["month"])
    kept = len(m.locked - set(m.leave))
    m.generate()
    m.save(f"Re-planned, keeping {kept} manual change{'s' * (kept != 1)}" if m.exists else "Roster generated")
    n = m.blocking()
    return back("/roster", month=m.month, view=f.get("view"), id=f.get("id"),
                msg=f"Planned {m.title}. {n} blocking issue{'s' * (n != 1)} need attention." if n else f"Planned {m.title} with full coverage.")


@app.post("/roster/edit")
async def edit(request: Request, con=Depends(db)):
    f = await request.form()
    m = Month(con, f["month"])
    before, e, d, to = m.blocking(), int(f["employee_id"]), f["day"], f.get("to")
    if e not in m.emps or d not in m.wd or (to and to not in m.wd):
        raise ValueError("that employee or day is not in this month")
    name, when = m.emps[e]["name"], f"{DAYS[m.wd[d]]} {int(d[8:])}"
    if (e, d) in m.leave or (e, to) in m.leave:
        raise ValueError(f"{name} is on approved leave that day; change it under Leave and comp-offs")
    if f["action"] == "move":
        m.set(e, d, "W")
        if to:
            m.set(e, to, "O")
        text = f"{name} works {when}" + (f", weekly off moved to {DAYS[m.wd[f['to']]]} {int(f['to'][8:])}" if f.get("to") else "")
    elif f["action"] == "cover":
        line = m.lines[int(f["line_id"])]
        m.set(e, d, "W")
        m.cover[(e, d)] = line["id"]
        text = f"{name} covers {line['brand']} · {line['process']} on {when}"
    else:
        if f["status"] not in ("W", "O", "H") or (f["status"] == "H" and d not in m.hol):
            raise ValueError("that status cannot be set by hand")
        m.set(e, d, f["status"])
        text = f"{name} set to {LABEL[f['status']]} on {when}"
    m.save(text)
    return back("/roster", month=m.month, view=f.get("view"), id=f.get("id"), day=f.get("day_view"),
                msg=f"{text}. " + impact(con, m.month, before))


@app.post("/roster/publish")
async def publish(request: Request, con=Depends(db)):
    f = await request.form()
    if not con.execute("UPDATE rosters SET published=? WHERE month=?", (now(), f["month"])).rowcount:
        raise ValueError(f"{f['month']} has no roster to publish")
    con.commit()
    return back("/roster", month=f["month"], msg="Published. Later changes are listed under the roster.")


@app.get("/roster.csv")
def export(month: str, con=Depends(db)):
    m = Month(con, month)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Employee", "Pod"] + m.days)
    for e, x in m.emps.items():
        w.writerow([x["name"], x["pod"] or ""] + [m.st.get((e, d), "") for d in m.days])
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="roster-{month}.csv"'})


@app.post("/explain")
def explain(month: str = Form(), line_id: int = Form(), con=Depends(db)):
    m, line = Month(con, month), line_id
    gap = next((g for g in m.issues()["gaps"] if g["line"] == line), None)
    if not gap or not os.environ.get("NVIDIA_API_KEY"):
        return JSONResponse({"text": "Nothing to explain."})
    x, name = m.lines[line], lambda e: m.emps[e]["name"]
    facts = {
        "brand": x["brand"], "process": x["process"], "shift": f'{x["start_h"]}:00-{x["end_h"]}:00',
        "minimum_staff": dict(zip(DAYS, map(int, x["need"]))), "open_on_public_holidays": bool(x["holidays"]),
        "assigned": [name(e) + (" (backup)" if (e, line) in m.backup else "") for e in m.staff[line]],
        "short_days": [{"date": d["day"], "weekday": DAYS[m.wd[d["day"]]], "needed": d["need"], "working": d["have"],
                        "absent": [f"{name(e)}: {LABEL[s]}" for e, s in d["absent"]],
                        "can_move_weekly_off": [f"{name(e)} to {to}" if to else f"{name(e)} works the holiday" for e, to in d["moves"]],
                        "free_to_cover": [name(e) for e in d["backups"]]} for d in gap["days"][:8]],
    }
    body = {"model": os.environ.get("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct"), "temperature": 0.2, "max_tokens": 300,
            "messages": [{"role": "system", "content": "You explain one staffing gap in a monthly roster to an operations "
                          "manager. Use only the facts given; never invent people or dates. In three or four plain sentences: "
                          "what is short and on which days, why, and the most practical fix among the options listed. "
                          "If nobody can move or cover, say a backup must be assigned to this brand and process."},
                         {"role": "user", "content": json.dumps(facts)}]}
    req = urllib.request.Request("https://integrate.api.nvidia.com/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f'Bearer {os.environ["NVIDIA_API_KEY"]}', "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return JSONResponse({"text": json.load(r)["choices"][0]["message"]["content"].strip()})
    except (OSError, KeyError, ValueError):
        return JSONResponse({"text": "The explanation service did not answer. The suggestions on the card still apply."})


@app.get("/setup/{table}", response_class=HTMLResponse)
def setup(request: Request, table: str, edit: int = 0, msg: str = "", con=Depends(db)):
    if table not in TABLES:
        return back("/setup/employees")
    title, fields = TABLES[table]
    refs = {k: dict(options(con, t)) for k, _, t in fields if t in TABLES}
    order = {"leaves": "date_from DESC", "holidays": "day"}.get(table, "name" if fields[0][0] == "name" else "id")
    rows = [dict(r) for r in con.execute(f"SELECT * FROM {table} ORDER BY {order}")]
    if order == "id":
        rows.sort(key=lambda r: tuple(str(refs.get(k, {}).get(r[k], r[k])) for k, _, _ in fields[:2]))
    current = next((r for r in rows if r["id"] == edit), None)
    return T.TemplateResponse(request, "setup.html", {
        "table": table, "title": title, "fields": fields, "rows": rows, "refs": refs, "tables": TABLES,
        "options": {k: options(con, t) for k, _, t in fields if t in TABLES}, "current": current, "msg": msg, "kinds": list(KIND)})


def parse(fields, f):
    row = {}
    for k, label, t in fields:
        v = f.get(k, "").strip() if t != "week" else "".join(f.get(f"{k}{i}", "0").strip() or "0" for i in range(7))
        if t == "bool":
            row[k] = int(bool(f.get(k)))
        elif t in TABLES or t in ("int", "hour", "weekday"):
            if v == "" and t in ("weekday", "pods", "employees") and k != "employee_id":
                row[k] = None
                continue
            if not v.isdigit():
                raise ValueError(f"{label} must be chosen or a whole number.")
            row[k] = int(v)
        elif t == "week" and not (v.isdigit() and len(v) == 7):
            raise ValueError(f"{label}: give a whole number for each day.")
        elif t == "date":
            try:
                row[k] = date.fromisoformat(v).isoformat()
            except ValueError:
                raise ValueError(f"{label} must be a date.") from None
        elif not v:
            raise ValueError(f"{label} is required.")
        else:
            row[k] = v
    if row.get("date_from", "") > row.get("date_to", "~"):
        raise ValueError("The leave ends before it starts.")
    if "start_h" in row and not 0 <= row["start_h"] < row["end_h"] <= 24:
        raise ValueError("The shift must start before it ends, within 0 to 24.")
    if "offs" in row and not 0 <= row["offs"] <= 6:
        raise ValueError("Weekly offs must be between 0 and 6.")
    if row.get("pref_off") is not None and not 0 <= row["pref_off"] <= 6:
        raise ValueError("Preferred off must be a weekday.")
    return row


def planned(con):
    return {month: Month(con, month).blocking() for month in [r["month"] for r in con.execute("SELECT month FROM rosters ORDER BY month").fetchall()]}


def refresh(con, before, text):
    """Re-apply leave and holidays to every planned month and log the change where it affects that month."""
    out = []
    for month, was in before.items():
        m = Month(con, month)
        snap = dict(m.st)
        m.sync()
        now = m.blocking()
        if m.st != snap or now != was:
            m.save(text)
            out.append(f"{m.title}: blocking issues {was} to {now}")
    return "; ".join(out)


@app.post("/setup/{table}")
async def save(request: Request, table: str, con=Depends(db)):
    title, fields = TABLES[table]
    f = await request.form()
    try:
        row = parse(fields, f)
        before = planned(con)
        if f.get("id"):
            con.execute(f"UPDATE {table} SET {', '.join(f'{k}=?' for k in row)} WHERE id=?", [*row.values(), int(f["id"])])
        else:
            con.execute(f"INSERT INTO {table}({', '.join(row)}) VALUES({', '.join('?' * len(row))})", list(row.values()))
        con.commit()
    except (ValueError, *INTEGRITY) as err:
        msg = str(err) if isinstance(err, ValueError) else "That already exists." if "unique" in str(err).lower() else "That refers to something that no longer exists."
        return back(f"/setup/{table}", edit=f.get("id") or None, msg=msg)
    effect = refresh(con, before, f"{title}: {'edited' if f.get('id') else 'added'} {describe(con, table, fields, row)}")
    return back(f"/setup/{table}", msg="Saved. " + (effect or "No planned roster is affected."))


@app.post("/setup/{table}/{rid}/delete")
def delete(table: str, rid: int, con=Depends(db)):
    title, fields = TABLES[table]
    row = con.execute(f"SELECT * FROM {table} WHERE id=?", (rid,)).fetchone()
    if row:
        text, before = f"{title}: removed {describe(con, table, fields, dict(row))}", planned(con)
        con.execute(f"DELETE FROM {table} WHERE id=?", (rid,))
        con.commit()
        effect = refresh(con, before, text)
        return back(f"/setup/{table}", msg="Removed. " + (effect or "No planned roster is affected."))
    return back(f"/setup/{table}")


def describe(con, table, fields, row):
    parts = []
    for k, _, t in fields[:4]:
        v = row.get(k)
        if t in TABLES:
            v = dict(options(con, t)).get(v, "")
        if v not in (None, "") and t not in ("bool", "week", "int", "hour", "weekday"):
            parts.append(str(v))
    return " · ".join(parts)
