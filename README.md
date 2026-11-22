# Smart Roster

A monthly roster planner for a support team that works across many D2C brands. It plans weekly offs around each brand's coverage rules, leave, comp-offs and public holidays, then lists every gap or rule it could not satisfy, with the fixes it can see.

Live demo: https://smart-roster-nu.vercel.app. It opens on December 2026, already planned from the sample data.

## Run it

```bash
pip install -r requirements.txt
uvicorn app:app --reload        # http://127.0.0.1:8000
python -m unittest test_planner # planner tests
```

The first start creates `roster.db` with the sample data and plans December 2026. Delete the file to start again.

Environment variables, all optional, listed in `.env.example`. The app does not read `.env` itself, so export them in your shell or set them in Vercel:

- `DATABASE_URL` points the app at Postgres, for example a Supabase project (use the transaction pooler URL, port 6543). Without it the app uses a local SQLite file. On first connect it creates the tables and loads the sample data.
- `NVIDIA_API_KEY` turns on the **Explain** button on coverage gaps (a free key from build.nvidia.com). `NVIDIA_MODEL` picks the model.
- `ROSTER_DB` sets where the SQLite file lives.

On Vercel, `vercel.json` only names the framework; `vercel deploy` does the rest. Set `DATABASE_URL` there so every server instance shares one database; without it each instance keeps its own temporary copy.

## Approach

**Stack.** FastAPI, Jinja pages, and Postgres (Supabase) on the hosted app or SQLite locally, in about 1,150 lines including the sample data and templates. The planner is plain Python with no solver library, so every decision can be explained and a month plans in under a second.

**Data model.** Employees belong to pods, and each pod has a lead. A *coverage rule* is one brand plus one process. It holds the minimum staff for each weekday (0 means closed), whether it runs on public holidays, and its shift hours. *Assignments* link employees to coverage rules, optionally as backup. Leave, comp-offs and unavailability are date ranges. The roster itself is one cell per employee per day.

**Planning.**

1. Approved leave and comp-offs go in first. Anyone whose processes are all closed on a public holiday gets the holiday off.
2. Weekly offs are placed one week at a time. The most constrained person picks first, meaning the one with the fewest days off that leave no gap. Each person takes the cheapest day. A day that leaves a process short costs most, then every day beyond six in a row, then using up a process's last spare person. A preferred day, or a day when all their processes are closed, costs less.
3. On holidays that a process works, people who aren't needed get the holiday off. The rest earn a comp-off.
4. Repair passes then run. Any weekly off that causes a gap is moved to another day in the same week if that day is free. Weekly offs are also shifted within their week to break long runs.
5. Cells the manager changed by hand are locked and survive a re-plan.

**Validation.** Validation recomputes from the current data on every page load, so it can never be stale. It reports:

- **Blocking:** every brand process below its minimum on a day, with who is missing and why. Also any assignment whose shifts don't fit in the employee's day.
- **Across brands:** one person's absence leaving two or more brands short on the same day.
- **Rules:** a full week without a weekly off, or more than six days in a row.
- **Preferences:** preferred days off that were not given. With one off a week and at most six days in a row, a person whose off falls on Wednesday can't move to Sunday the next week. So when two people sharing a process both prefer Sunday, the one who gets it keeps it for the month.

Every gap lists the fixes available: move a weekly off within the week, have someone work the holiday, or a ranked list of people who are working that day and whose shift fits. One click applies a fix, and the page shows the issue count before and after.

**Changes.** Adding or removing leave, holidays, rules or assignments updates every planned month at once. The page shows the effect, for example "blocking issues 12 to 15", and logs it. After publishing, the roster page lists every change made since.

## Sample data and assumptions

- **Team:** 32 employees in 5 pods, 26 brands and 4 processes.
- **Brands:** 22 brands (85%) have one dedicated person; four of those also have a pod floater as backup, who counts toward coverage. Four brands have two or three people, either on separate processes or sharing one, like Fitkart's Customer Support needing 2 a day.
- **Calendars:** six-day processes, daily processes with holidays off, and 365-day processes, plus per-day minimums such as Brewly needing 2 on weekdays and 1 at weekends.
- **Planted conflicts:** a leave, a comp-off and a 365-day brand with only one person (Moonlit Teas) create conflicts on purpose.
- **Weeks:** weeks run Monday to Sunday and everyone gets one weekly off a week by default. A week split across two months owes its off once. If the neighbouring month isn't planned yet, the off is placed only where it opens no gap, unless skipping it would mean more than six days in a row. Rest wins then, and the gap is flagged.
- **Coverage:** a person at work covers every process they are assigned that day. That is how one person runs a whole brand.
- **Shift hours:** each process has shift hours, and an employee's assignments must fit inside their maximum day (9 hours by default).
- **Leave:** leave is entered as already approved, and it overrides the roster.
- **Comp-offs:** earned comp-offs are shown per employee, and granting one is a manager action.
- **Holidays:** the public holiday list is a sample and can be edited.

## How I thought about it

**1. How did you model employees, brands, processes, pods and their relationships?**
The unit of coverage is a brand plus a process, so a coverage rule is a first-class row: it holds the weekday minimums, the holiday rule and the shift. Employees attach to coverage rules through assignments, which can be many-to-many, so one person can cover several brands and several people can share one process. Pods group employees under a lead. Keeping the brand's calendar on the rule and the person's schedule in the roster cells is what separates "when the brand needs cover" from "when this person works".

**2. How does the system generate a roster and check it satisfies the constraints?**
It runs the greedy weekly-off placement and repair passes described above. Validation is a separate function that ignores how the roster was made. It checks every coverage rule on every day against the people present, then every employee's weeks and runs, so manual edits are checked exactly like generated ones.

**3. Single-resource brands versus brands with several people?**
The same rule handles both: a day off is cheap only if the processes stay at their minimum without that person. A one-person brand with Sundays closed gets that person's off on Sunday at no cost. A one-person brand that runs seven days can't be covered on the off day unless someone else is assigned. The planner still gives the rest day and flags the gap, suggesting a backup; Kora Skin and Nimbus Home share a floater for exactly this. Brands with several people spread their offs so the minimum holds.

**4. What happens when the person running a whole brand is unavailable on a required day?**
Their leave locks the cell, and every process of that brand shows as short that day in one card per process. The card names the missing person and gives ranked cover options: people doing the same process at another brand first, then the same pod, then lighter workloads, and only people whose shift fits. One click assigns the cover for that day and the gap closes.

**5. Conflicting requirements or not enough staff?**
Rest and approved leave are treated as hard. Coverage minimums are the next priority, and preferences come last. When coverage can't be met, the roster is still produced, but the gap is listed as blocking and Publish asks for confirmation. The roster is never quietly made invalid: Moonlit Teas, a 365-day brand with one person, shows a gap on each of his weekly offs. The only cover offered is Kabir, the one person whose shift fits, and where nobody fits the card says to assign a backup.

**6. What is automated and what is left to people?**
Placing weekly offs, holiday cover, repairs, validation and the impact of each change are automated. Granting comp-offs, borrowing someone from another brand, publishing, and overriding any cell stay with the manager. Those decisions involve people and clients, and the tool can only suggest them. Manual changes are kept on re-plan, so the manager's decisions stick.

**7. If the team and brand count doubled?**
The greedy planner would still be fast. With hundreds of people I would move to a constraint solver (CP-SAT) with the same hard and soft rules, so offs could be traded between people globally rather than one person at a time. I would also move to Postgres with logins for pod leads, and add approval by pod.

**8. With one more week?**
First, a solver-backed "find the best roster" option to compare with the greedy plan, since today a few long runs can only be fixed by moving two people's offs together. Then approvals by pod lead, and a notification to affected employees when a published roster changes.
