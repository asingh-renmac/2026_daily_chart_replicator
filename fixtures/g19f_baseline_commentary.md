# G19f baseline — jobs-report commentary, 10 charts

Fixture for gate **G19f** (plan.md §15.5): the park count and park QUALITY produced by
this commentary before §15 and after it. Supplied by the operator 2026-09-04 as the
replacement baseline for the earlier 2026-09-04 commentary.

Run it through `renmac-charts-build` (or `resolve_series` directly, one slot per chart
below) and record, per slot: bound vs parked, and for each park the reason plus whether
the operator judges the park WARRANTED. §15 aims to cut unwarranted parks to zero — it
does not aim to cut warranted ones, so the count alone is not the gate.

Charts 5, 8 and 10 are the compound-transform cases that motivated §15.1: a 3-month
moving average **of the monthly change**, which the flat phrase mapper silently
collapsed to a 3mma of the level. Chart 7 carries two transforms on one ticker, which
is the legend-collision case §15.1a re-keys.

---

## Commentary

### Payrolls caught up but hours and wages made the case

The labor market is strengthening, and it is strengthening in the places that matter for
inflation. Nonfarm payrolls rose 162k in August after an upwardly revised 21k in July,
with June and July together revised 55k higher and the three-month average now 71k, up
from 38k. Private payrolls added 127k, breadth improved to a 55.6 diffusion index, and
the gain came alongside a longer workweek and firmer hourly pay. The headline is partly
catch-up from two soft months, but the underlying picture is a jobs market reviving into
an inflation rate already above target — and that combination, not the payroll number, is
what should drive the policy read.

### Private domestic demand is solid, led by the factory sector

- Goods production contributed 41k of the private gain, with construction up 22k and
  manufacturing up 16k. Factory payrolls now sit 58k above their December 2025 low on
  continued gains in machinery and fabricated metal products, and the manufacturing
  diffusion index at 61.1 from 52.1 says the improvement is broad rather than a
  one-plant story; the factory workweek lengthened to 40.5 hours with overtime steady at
  3.1.

The rebound piece is leisure and hospitality, up 62k on 59k in food services and drinking
places against a 12k twelve-month average, and local government education recovering 42k
after July's drop. The seasonal factor was also unusually mild, with unadjusted payrolls
up 154k, barely below the adjusted print.

### The workweek, hourly earnings and underemployment are the real signal

- The private workweek lengthened a tenth to 34.4 hours, average hourly earnings rose 10
  cents or 0.3% to $37.75, and production and nonsupervisory pay rose 0.3% to $32.53 —
  more people, working longer, at higher pay. Aggregate weekly hours climbed 0.3% on the
  month and aggregate weekly payrolls 0.7%.
- Part time for economic reasons employment fell 414k to 4,390k, dragging U-6 to 7.7%
  from 7.9%. Firms are lengthening hours for the people they already have as involuntary
  part-time work drains away.
- The most dovish thing to point at here is soft wages, with average hourly earnings up
  just 3.1% over the year. But wage growth follows slack rather than leading it, and the
  slack measures all moved the other way this month. At any rate the inflation data own
  the reaction function; the wage print is an input to it, not the decider.

### Strong hours and headcount leave little room for productivity

- Hours worked rose 1.7 percent annualized over the last three months. All else equal,
  the stronger growth in hours implies less growth in productivity. That keeps some
  pressure under unit labor costs in the current quarter. Given this labor data, you
  shouldn't expect inflation to cool off on its own.

### The household survey tells the same story of narrowing slack

The jobless rate held at 4.1% as employment rose 569k and the labor force 683k, with
participation up two tenths to 61.6%, still half a point below January. Job leavers
jumped 121k to 914k, or 13.1% of the unemployed — workers quitting into a firmer market
rather than being pushed out of one.

- The offsetting negative is duration, with the long-term share up to 27.0%, a reminder
  that re-entry remains slow even as slack narrows.

### The Fed's trade-off is not subtle

- Take the view held by a fair number of officials that breakeven employment growth is
  now close to zero. On that arithmetic, the unemployment rate should be falling from
  here, not holding at 4.1%. That makes the slowing in wage growth we've seen less likely
  to persist, and it puts the labor market at best neutral for inflation and at worst a
  little bad for it.
- There is not much reason to wait if the jobs market is reviving. Inflation is already
  above target and it is not obvious it cools on its own with the economy this resilient
  and productivity this thin — watching and waiting with inflation above target is the
  costlier error here. It comes down to a little hiking now or a lot more later.

---

## Charts

| # | Spec | Title |
|---|---|---|
| 1 | Line — average weekly hours, total private and manufacturing; levels, monthly, last three years | "The workweek is stretching again led by factories" |
| 2 | Line — aggregate weekly payrolls, total private, year-over-year percent change, plotted against the federal funds target rate midpoint in levels | "Labor income growth is barely outrunning the policy rate" |
| 3 | Line — aggregate weekly hours index, total private industries; 3-month percent change annualized | "Hours growth has firmed off last year's lows" |
| 4 | Line — aggregate weekly payrolls, production and nonsupervisory workers; 2-quarter annualized growth, long history | "Total labor income is growing like it's the 2010s again" |
| 5 | Line — total nonfarm and total private payroll change; **3-month moving average of the monthly change** | "Payroll momentum has increased since summer lull" |
| 6 | Line — U-3 and U-6; levels, monthly | "Underemployment keeps sliding with the jobless rate flat" |
| 7 | Line — average hourly earnings, all private employees; **year-over-year percent alongside 3-month percent annualized** | "Wage growth is cooling but the pace may not last" |
| 8 | Line — all employees, construction and all employees, manufacturing; **3-month moving average of the monthly change** | "Construction and factories are both adding jobs again" |
| 9 | Line — one-month diffusion index, total private and manufacturing; levels with a 50 reference | "Hiring breadth is the widest since late 2024" |
| 10 | Line — food services and drinking places, and local government education; **monthly change in thousands**, last two years | "August's bounce mostly reversed the summer give-back" |
