# Design — charts from commentary, in the chat lane

**Status: proposal. Nothing here is built.** Written 2026-09-17.

## The idea, and what already exists

Paste a piece of economic commentary into Claude and get back the charts that make its
argument visible — without naming a series, a ticker, or a release.

Before designing anything it is worth being exact about what is already built, because
most of this feature exists and the remaining gap is small.

**The chart lane today is a resolve-and-draw engine and nothing more.** Six tools:
`resolve_series`, `remember_binding`, `forget_binding`, `render_chart`, `pick_series`,
`enrich_page`. There is no selection step for any input. When an operator attaches a
screenshot, the lane never sees the image — Claude reads it, infers the series and
transforms, and calls the tools. The model is the eyes and the judgement; the lane is the
hands.

**`econ_commentary` already does commentary → charts, end to end, in production for 38
releases.** `scripts/main.py` step 5c:

    specs  = plan_charts(commentary, release_def, release_date, ref_period)
    charts = render_charts(specs, rid, release_date)

and the chain inside it is: `chart_budget()` sizes the output from the prose length;
`chart_store.shortlist(release_id, commentary)` ranks the 1,022 catalogued Macrobond
charts by word overlap with the commentary, scoped to that release's folders;
`series_book.for_release()` supplies the plottable vocabulary; a **second Claude call**
on `templates/chart_plan_prompt.md` returns chart specs; `_validate()` drops unrenderable
ones; and `chart_renderer` then **shells out to this very lane** under its Python 3.10
interpreter to draw them.

So the two projects are already joined, and the thing being proposed here is not the
feature — it is an *interactive* entry point to a feature that currently only runs inside
a scheduled job.

## What is actually missing

`econ_commentary`'s planner is **release-scoped and batch**. `shortlist()` needs a
`release_id` to look up folders in `config/chart_store_map.json`, and the whole chain runs
headless after a scrape. There is no way to hand it arbitrary text.

One structural difference makes the interactive version much smaller than the batch one:
**`econ_commentary` needs a second Claude call because it is headless. In the chat lane
the model is already in the loop.** The lane therefore needs no planner, no budget
heuristic, no spec validator and no prompt template. It needs to hand Claude the relevant
prior charts and get out of the way. Everything downstream — resolution, the seeded
series book, rendering, parking — already works.

## Two constraints, both measured

**The AVD cannot see the chart store.** Measured 2026-09-17: none of
`C:\Users\asingh\new_work\mb_charts_store\US`, `C:\Users\madz\Work\asingh\mb_charts_store\US`
or the UNC path resolve on the host, and `MB_CHART_STORE` is unset at both machine and
user scope. The lane runs there, so today a store-reading tool would have nothing to read.
This is the single biggest feasibility question and it has a cheap answer: the index is
**1.6 MB of text** and the PNGs are **77 MB**. Ranking needs only the index.

**The release scope turns out not to be load-bearing.** The worry was that ranking across
all 1,022 charts instead of a release's ~150 would be too noisy to use. Measured on a
payrolls commentary: the global top 10 recovers **8 of the 10** the release-scoped
shortlist returns, and **18 of the global top 30** come from `Labor/NFP` unaided. The
commentary text carries enough signal to find its own folder. A cap is still essential —
858 of 1,022 charts had *some* token overlap — but the top of the list is right.

## What the store is, and is not

`mb_charts_store` holds **1,022 PNG screenshots plus LLM-written descriptions**. It
contains **no Haver tickers**: of 1,854 distinct `series` strings, zero contain an `@`.
That field is Macrobond legend text — `"Construction [c.o.p. 1 year]"` — and cannot be
re-fetched programmatically. `chart_store.py` states the rule plainly: *"A store entry can
suggest WHAT to plot; the Haver resolver still has to find it from a description."*

The field worth matching on is **`subject`** — the model's plain-English restatement of
each line, e.g. `["US CEO business confidence — current conditions vs 6 months ago",
"US corporate profits before tax with IVA and CCAdj"]`. `tags`, `use_when` and `title`
round out the blob that `shortlist` scores against; `summary` and `series` are
deliberately excluded.

The store is also **frozen**. Titles are editorial headlines from the day they were
written and 168 of them name a specific month. `templates/chart_plan_prompt.md` carries
carefully-worded rules against reusing them. A tool has no prompt, so those guardrails
have to travel in the tool description and the payload.

## Proposed tool surface

One tool, possibly two. Deliberately small — every tool added to this lane is one more
thing in every operator's context on every call.

### `find_prior_charts(text: str, limit: int = 12) -> list`

Returns trimmed store entries ranked against `text`, best first. Each entry carries the
same eight fields `chart_store.trim()` already produces — `ref`, `shows`,
`transformations`, `chart_type`, `frequency`, `use_when`, `sample`, `stale_title` — plus
the folder, so Claude can see the domain it landed in.

The payload must say, in the response and not only in the docs, that these are **prior
work shown for form**: their titles are stale, their data is stale, and none of them is an
obligation. The single most important line of `chart_plan_prompt.md` is that a store chart
must never be reached for because it is there.

### `show_prior_chart(ref: str) -> Image` — second stage, only if warranted

Returns the PNG for one catalogued `path` so Claude can look at it. This is the equivalent
of `econ_commentary`'s vision `inspect` pass. It requires the 77 MB of PNGs on the AVD,
which the index-only option avoids, so it should be built only if the text ranking proves
insufficient in practice. `chart_store.image_path()` already has the two guards this needs
— the ref must appear in the index, and the resolved path must stay inside the store —
because the value round-trips through a model.

Note what is **not** proposed: no `plan_charts`, no budget heuristic, no spec validator, no
chart-spec schema. In the chat lane Claude reads the candidates and calls the existing
`resolve_series` and `render_chart` itself. Adding a planner here would be a second,
drifting copy of `chart_planner.py` with no second model to justify it.

## Data flow

    operator pastes commentary into Claude
      -> Claude calls find_prior_charts(text)
           -> lane reads charts_index.json (local copy on the AVD)
           -> ranks by token overlap against title+subject+tags+use_when
           -> returns top N trimmed entries + the "form, not data" caveat
      -> Claude decides WHICH charts the argument needs, in its own words
      -> Claude calls resolve_series per line
           -> ratified store answers first (229 vetted series seeded 2026-09-17)
           -> otherwise catalogue search, and park rather than guess
      -> Claude calls render_chart
      -> PNG returned inline, as today

The only new code is the first box. Everything below it is the lane as it stands.

## Where the ranking should live

Three options, and the risk is the same one this project has already been bitten by.

1. **Reimplement `shortlist` in the lane.** Simplest, and wrong: two copies of one
   algorithm that drift. The precedent is not hypothetical — the vendored `queries.py`
   sat three months stale on the AVD and silently withheld the ranker fix from the chart
   resolver while the metadata MCP had it.
2. **Import `chart_store` from `econ_commentary`.** No duplication, but it makes the chat
   lane — which must not break — depend on a sibling repo's internals, and `chart_store`
   is not written as a public API.
3. **Extract the ranking into a shared module** both repos consume. Most work up front,
   no drift, and it forces the ranking to acquire a stable interface. This is the option
   the `queries.py` incident argues for.

## Failure modes

**The store is missing or unreadable.** Must return `[]` and say so, never raise.
`econ_commentary` already treats an unreachable store as a normal condition — *"a network
path that is down overnight must not be the reason a commentary fails to send"* — and the
same rule applies harder here, because this lane serves live operators.

**The index goes stale.** A local AVD copy is a snapshot. If the store is re-exported,
the lane silently ranks against old descriptions. Whatever ships the index must record
`generated` and surface it, so a stale copy is visible rather than merely old.

**Ranking returns plausible-but-wrong charts.** The scoring is token overlap against a
60-word stoplist. It is reasonable and it has never been measured against known-good
picks. The honest position is that this is unproven, and the tool's response should not
present ranked candidates as if they were answers.

**Claude reuses a stale title.** 168 titles name a specific month. Mitigated only by
wording in the payload; there is no mechanism to enforce it.

**Cost in context.** Twelve trimmed entries is roughly 1–2 KB. A `limit` of 80 — the
batch default — would be several thousand tokens on every call, which is why the default
here should be far smaller than `econ_commentary`'s.

## Open questions

1. **Ship the index into the repo, or sync it to the AVD?** In-repo means it versions with
   the code and reaches the host through the existing `git pull` deploy, at the cost of a
   1.6 MB file that changes wholesale on each re-export. Syncing keeps the repo clean but
   adds a step that can silently not happen.
2. **Is the vision pass worth 77 MB on the AVD?** Unknown until the text-only version is
   used. Deferring it is cheap; adding it later is also cheap.
3. **Should the tool accept an optional domain hint?** The measurement says it is not
   needed, but an operator who knows they want housing could say so and cut the noise.
4. **How is this evaluated?** The lesson of the retrieval work this month is that ranking
   claims need ground truth. A handful of real commentaries with known-good chart picks
   would settle in an afternoon whether token overlap is good enough.
5. **Does this belong in the chat lane at all, or in a third surface?** The lane is
   currently a narrow, well-understood tool set. Chart *selection* is a different kind of
   act from chart *drawing*, and it is worth asking once whether mixing them is right.

## Suggested staging

**Stage 1 — index only, text ranking, one tool.** Get `find_prior_charts` working against
a local copy of `charts_index.json`, no PNGs, small default limit. This is most of the
value and avoids the 77 MB question entirely.

**Stage 2 — measure.** Take real commentaries, have someone mark the charts that should
have been chosen, and score the ranking. Only then tune it.

**Stage 3 — vision, if stage 2 says text is not enough.** Ship the PNGs, add
`show_prior_chart`, reuse the existing path guards.
