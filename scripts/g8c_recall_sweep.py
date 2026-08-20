"""
g8c_recall_sweep.py — the description-only recall/park breakdown (seed bypassed).

Phase 2 of the texas follow-up: run live resolution across EVERY description-only
series in the 26 samples and report, per series:
  * resolved-live / parked
  * for parks WITH keyed truth: recall miss (truth not in candidate pool) vs
    relevance miss (truth surfaced but gate-rejected)
  * reachability of a recall-miss truth within / beyond the 25-cap

Two modes (live search is the haver MCP, which only the agent can call — so the
search hits are CAPTURED into a fixture and replayed here, exactly like
g8c_texas.py):

  enumerate : print the description-only slots + write
              outputs/g8c/sweep_queries.json  (unique {query,databases,sa_status}
              the agent must run via search_series, max_results=25)
              outputs/g8c/sweep_slots.json     (slots + attached truth)
  resolve   : load outputs/g8c/sweep_hits.json (agent-captured MCP results) and
              run the resolver; classify + report.
"""
import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import resolve as R   # noqa: E402

OUT = ROOT / "outputs" / "g8c"
OUT.mkdir(parents=True, exist_ok=True)
READS = ROOT / "outputs" / "g8" / "reads.json"
CORR = ROOT / "outputs" / "g8" / "corrections.json"

# corrections.json keys → reads.json labels (truth is keyed on screenshot label)
_CORR_ALIAS = {
    "ingested:2026-06-29_a2f643425c_0.png": "ingested:2026-06-29_a2f643425c_0.png",
}


def _load_truth() -> dict:
    """{(label, idx_str) → 'code@db' or [codes]} from corrections.json."""
    raw = json.loads(CORR.read_text(encoding="utf-8"))
    truth = {}
    for label, blk in raw.items():
        if label.startswith("_"):
            continue
        for idx, sd in (blk.get("series") or {}).items():
            if "ticker" in sd:
                truth[(label, str(idx))] = sd["ticker"]
    return truth


def _desc_only_slots():
    """Yield (label, slot) for every NON-formula series (the description path)."""
    reads = json.loads(READS.read_text(encoding="utf-8"))
    for entry in reads:
        label = entry["label"]
        spec = entry["spec"]
        for i, s in enumerate(spec.get("series", []) or []):
            slot = R.slot_from_series(i, s)
            if slot.get("formula"):
                continue                       # formula path, not description-only
            yield label, slot


def enumerate_mode():
    truth = _load_truth()
    slots_out, queries, seen = [], [], set()
    n_total = n_truth = n_nativema = 0
    print("DESCRIPTION-ONLY SERIES (non-formula) across the 26 samples\n" + "=" * 70)
    for label, slot in _desc_only_slots():
        n_total += 1
        idx = str(slot["idx"])
        tkey = (label, idx)
        tval = truth.get(tkey)
        if tval:
            n_truth += 1
        nm = slot.get("native_ma_ambiguous")
        if nm:
            n_nativema += 1
        attempts = R.build_search_attempts(slot)
        rec = {"label": label, "idx": slot["idx"],
               "description": slot["description"],
               "base_descriptor": slot.get("base_descriptor"),
               "sa_hint": slot.get("sa_hint"), "freq_hint": slot.get("freq_hint"),
               "native_ma_ambiguous": nm, "truth": tval,
               "attempts": attempts}
        slots_out.append(rec)
        tflag = f"  TRUTH={tval}" if tval else ""
        nflag = "  [native-MA→clarify]" if nm else ""
        print(f"\n{label} [S{slot['idx']}]{tflag}{nflag}")
        print(f"   desc: {slot['description']!r}")
        for a in attempts:
            key = json.dumps([a["query"], a["databases"], a["sa_status"]])
            if key not in seen:
                seen.add(key)
                queries.append(a)
            print(f"     q: {a['query']!r}  db={a['databases']} sa={a['sa_status']}")
    (OUT / "sweep_slots.json").write_text(
        json.dumps(slots_out, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "sweep_queries.json").write_text(
        json.dumps(queries, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n" + "=" * 70)
    print(f"description-only series : {n_total}")
    print(f"  of which keyed-truth  : {n_truth}")
    print(f"  of which native-MA    : {n_nativema} (park for clarification, not recall)")
    print(f"unique live queries to run (25-cap): {len(queries)}")
    print(f"wrote {OUT/'sweep_queries.json'} and {OUT/'sweep_slots.json'}")


def _norm_code(c):
    return (c or "").strip().upper()


def _load_hits() -> dict:
    """Merge every agent-captured group file under outputs/g8c/sweep_hits/.
    Each file: { json([query,databases,sa_status]) : [ {code,descriptor,frequency,
    sa_status,agg_type?}, ... ] }."""
    merged = {}
    d = OUT / "sweep_hits"
    for f in sorted(d.glob("*.json")):
        merged.update(json.loads(f.read_text(encoding="utf-8")))
    return merged


def resolve_mode():
    hits = _load_hits()
    if not hits:
        print("no captured hits under outputs/g8c/sweep_hits/ — run the live searches first")
        return

    # code → metadata row (first occurrence wins), for get_meta
    code_meta = {}
    for rows in hits.values():
        for r in rows:
            k = _norm_code(r.get("code"))
            if k and k not in code_meta:
                code_meta[k] = r

    def search(query, databases=None, sa_status=None):
        return hits.get(json.dumps([query, databases, sa_status]), [])

    def get_meta(code):
        r = code_meta.get(_norm_code(code))
        if not r:
            return None
        return {"descriptor": r.get("descriptor"), "sa_status": r.get("sa_status"),
                "frequency": r.get("frequency"), "agg_type": r.get("agg_type")}

    confirm = lambda c: _norm_code(c) in code_meta     # in catalog → confirmable

    truth = _load_truth()
    rows_report = []
    for label, slot in _desc_only_slots():
        tval = truth.get((label, str(slot["idx"])))
        # raw union pool (pre-gate) across this slot's attempts
        pool = set()
        for a in R.build_search_attempts(slot):
            for h in search(a["query"], a["databases"], a["sa_status"]):
                pool.add(_norm_code(h.get("code")))
        res = R.resolve_slot(slot, confirm=confirm, get_meta=get_meta, search=search,
                             clarified={}, trusted={}, learned={})  # SEED BYPASSED
        rec = {"label": label, "idx": slot["idx"], "desc": slot["description"],
               "truth": tval, "status": res["status"],
               "needs_clarification": res.get("needs_clarification"),
               "resolved": res.get("resolved"), "relevance": res.get("relevance"),
               "exact": res.get("relevance_exact"), "via": res.get("bound_via_query"),
               "reason": res.get("reason"), "pool_n": len(pool)}
        if res["status"] == R.SLOT_RESOLVED:
            rec["outcome"] = "resolved-live"
            if tval:
                rec["correct"] = (_norm_code(res.get("resolved")) == _norm_code(tval))
        elif res.get("needs_clarification"):
            rec["outcome"] = "parked-clarify(native-MA)"
        else:
            rec["outcome"] = "parked"
            if tval:
                in_pool = _norm_code(tval) in pool
                rec["miss_type"] = "relevance-miss" if in_pool else "recall-miss"
                rec["truth_in_pool"] = in_pool
        rows_report.append(rec)

    # ── print ────────────────────────────────────────────────────────────────
    print("PER-SERIES (seed bypassed, learned bypassed, confirm=in-catalog)\n" + "=" * 78)
    cur = None
    for r in rows_report:
        if r["label"] != cur:
            cur = r["label"]
            print(f"\n{cur}")
        tag = r["outcome"]
        extra = ""
        if r["outcome"] == "resolved-live":
            ok = "" if r.get("truth") is None else ("  ✓TRUTH" if r.get("correct") else "  ✗WRONG")
            extra = f"→ {r['resolved']}  (exact={r['exact']} sim={r['relevance']} via={r['via']!r}){ok}"
        elif r["outcome"] == "parked":
            mt = f"  [{r.get('miss_type')}]" if r.get("truth") else "  [no-truth]"
            extra = f"(pool={r['pool_n']}){mt}  {r['reason']}"
        else:
            extra = f"(pool={r['pool_n']})  {r['reason']}"
        t = f" TRUTH={r['truth']}" if r.get("truth") else ""
        print(f"  S{r['idx']} {tag:26s} {extra}{t}")
        print(f"       desc: {r['desc']!r}")

    # ── aggregate ──────────────────────────────────────────────────────────────
    n = len(rows_report)
    resolved = [r for r in rows_report if r["outcome"] == "resolved-live"]
    parked = [r for r in rows_report if r["outcome"] == "parked"]
    clar = [r for r in rows_report if r["outcome"].startswith("parked-clarify")]
    truthed = [r for r in rows_report if r.get("truth")]
    correct = [r for r in truthed if r.get("correct")]
    wrong = [r for r in resolved if r.get("truth") and not r.get("correct")]
    recall_miss = [r for r in parked if r.get("miss_type") == "recall-miss"]
    relev_miss = [r for r in parked if r.get("miss_type") == "relevance-miss"]
    print("\n" + "=" * 78)
    print(f"description-only series           : {n}")
    print(f"  resolved-live                   : {len(resolved)}  "
          f"({100*len(resolved)/n:.0f}% day-one auto-resolve)")
    print(f"  parked (ambiguous/recall)       : {len(parked)}")
    print(f"  parked-clarify (native-MA)      : {len(clar)}")
    print(f"among the {len(truthed)} keyed-truth series:")
    print(f"  resolved & CORRECT              : {len(correct)}")
    print(f"  resolved & WRONG (silent mis-bind): {len(wrong)}   <-- must be 0")
    print(f"  parked — recall miss (truth not in pool) : {len(recall_miss)}")
    print(f"  parked — relevance miss (truth in pool)  : {len(relev_miss)}")
    (OUT / "sweep_report.json").write_text(
        json.dumps(rows_report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {OUT/'sweep_report.json'}")


def reach_mode():
    """For every keyed-truth series, report the truth ticker's RANK in each query
    variant at the 25-cap and the 100-cap — answering 'reachable within 25 by ANY
    variant?' vs 'buried past 25 but in 100' vs 'genuinely unreachable (needs the
    learned store + a one-time human supply)'."""
    hits25 = _load_hits()
    hits100 = json.loads((OUT / "sweep_hits_100.json").read_text(encoding="utf-8"))
    truth = _load_truth()
    print("REACHABILITY of the truth ticker per query variant (25-cap vs 100-cap)\n"
          + "=" * 78)
    for label, slot in _desc_only_slots():
        tval = truth.get((label, str(slot["idx"])))
        if not tval:
            continue
        T = _norm_code(tval)
        best25 = best100 = None
        lines = []
        for a in R.build_search_attempts(slot):
            key = json.dumps([a["query"], a["databases"], a["sa_status"]])
            r25 = [_norm_code(h["code"]) for h in hits25.get(key, [])]
            r100 = [_norm_code(h["code"]) for h in hits100.get(key, [])]
            p25 = (r25.index(T) + 1) if T in r25 else None
            p100 = (r100.index(T) + 1) if T in r100 else None
            if p25 and (best25 is None or p25 < best25):
                best25 = p25
            if p100 and (best100 is None or p100 < best100):
                best100 = p100
            lines.append(f"     q={a['query']!r:55s} sa={a['sa_status']}  "
                         f"n25={len(r25):2d} rank25={p25}  n100={len(r100):3d} rank100={p100}")
        if best25:
            verdict = f"REACHABLE in 25-cap (best rank {best25})"
        elif best100:
            verdict = f"BURIED past 25 (best rank {best100} within 100)"
        else:
            verdict = "UNREACHABLE by any variant within 100 — learned-store territory"
        print(f"\n{label} S{slot['idx']}  TRUTH={tval}  ->  {verdict}")
        print(f"     desc: {slot['description']!r}")
        for ln in lines:
            print(ln)


def store_mode():
    """Prove the equilibrium loop on the genuinely-UNREACHABLE recall miss
    (PCUSERH): parks today → human supplies once → learned store → auto-resolves
    next run via the learned fast-path, even though search still can't find it."""
    import tempfile
    hits = _load_hits()

    def search(query, databases=None, sa_status=None):
        return hits.get(json.dumps([query, databases, sa_status]), [])

    code_meta = {}
    for rows in hits.values():
        for r in rows:
            code_meta.setdefault(_norm_code(r.get("code")), r)

    def get_meta(code):
        r = code_meta.get(_norm_code(code))
        return None if not r else {"descriptor": r.get("descriptor"),
                                   "sa_status": r.get("sa_status"),
                                   "frequency": r.get("frequency"),
                                   "agg_type": r.get("agg_type")}
    confirm = lambda c: True          # production: live DLX confirms PCUSERH exists

    target = None
    for label, slot in _desc_only_slots():
        if label == "sample2_chart3.png" and slot["idx"] == 1:
            target = slot
            break
    assert target, "PCUSERH slot not found"
    truth = "PCUSERH@USECON"
    desc = target["description"]
    print(f"slot: {desc!r}\ntruth (human-supplied once): {truth}\n" + "=" * 70)

    # RUN 1 — no learned store: parks (search can't reach PCUSERH)
    r1 = R.resolve_slot(target, confirm=confirm, get_meta=get_meta, search=search,
                        clarified={}, trusted={}, learned={})
    print(f"RUN 1 (cold)   : {r1['status']:9s}  {r1['reason']}")

    # human supplies it once → write the learned store (hermetic temp dir)
    tmp = Path(tempfile.mkdtemp())
    R.save_learned(desc, truth, directory=tmp)
    learned = R.load_learned(directory=tmp)
    print(f"human supplies {truth} -> learned_descriptors.json written "
          f"({len(learned)} entry)")

    # RUN 2 — learned store present: auto-resolves via the learned fast-path
    r2 = R.resolve_slot(target, confirm=confirm, get_meta=get_meta, search=search,
                        clarified={}, trusted={}, learned=learned)
    ok = r2["status"] == R.SLOT_RESOLVED and _norm_code(r2["resolved"]) == _norm_code(truth)
    print(f"RUN 2 (learned): {r2['status']:9s}  -> {r2['resolved']}  "
          f"via={r2.get('bound_via_query')!r}  reason={r2['reason']}")
    print("\n" + ("PROOF OK — parked-then-supplied auto-resolves next run"
                  if ok else "PROOF FAILED"))


def _remeasure_search_meta():
    """Search surface + get_meta from the cap-100 filtered + unfiltered fixtures,
    sliceable to any cap. Serves both the primary filtered lookup and the resolver's
    SA-advisory retry (sa=None → unfiltered fixture)."""
    filt = json.loads((OUT / "sweep_hits_100.json").read_text(encoding="utf-8"))
    unf = json.loads((OUT / "sweep_hits_100_unfiltered.json").read_text(encoding="utf-8"))
    code_meta = {}
    for store in (filt, unf):
        for rows in store.values():
            for r in rows:
                code_meta.setdefault(_norm_code(r.get("code")), r)

    def make_search(cap):
        def search(query, databases=None, sa_status=None, max_results=None):
            rows = filt.get(json.dumps([query, databases, sa_status]))
            if rows is None:
                rows = unf.get(json.dumps([query, databases, None]), [])
            return rows[:cap]
        return search

    def get_meta(code):
        r = code_meta.get(_norm_code(code))
        return None if not r else {"descriptor": r.get("descriptor"),
                                   "sa_status": r.get("sa_status"),
                                   "frequency": r.get("frequency"),
                                   "agg_type": r.get("agg_type")}
    confirm = lambda c: _norm_code(c) in code_meta
    return make_search, get_meta, confirm


def _run_at_cap(cap, make_search, get_meta, confirm):
    """Resolve all 33 description-only slots at a given search cap; return per-slot
    {status, resolved, viable(set), exacts(set)}."""
    search = make_search(cap)
    out = {}
    for label, slot in _desc_only_slots():
        res = R.resolve_slot(slot, confirm=confirm, get_meta=get_meta, search=search,
                             clarified={}, trusted={}, learned={})
        viable = {_norm_code(v["code"]) for v in res.get("candidate_detail", [])}
        exacts = {_norm_code(v["code"]) for v in res.get("candidate_detail", []) if v["exact"]}
        out[(label, slot["idx"])] = {
            "status": res["status"], "needs_clarification": res.get("needs_clarification"),
            "resolved": _norm_code(res.get("resolved")) if res.get("resolved") else None,
            "viable": viable, "exacts": exacts, "desc": slot["description"]}
    return out


def remeasure_mode():
    make_search, get_meta, confirm = _remeasure_search_meta()
    truth = _load_truth()
    r25 = _run_at_cap(25, make_search, get_meta, confirm)
    r30 = _run_at_cap(30, make_search, get_meta, confirm)

    def split(rmap):
        res = [k for k, v in rmap.items() if v["status"] == R.SLOT_RESOLVED]
        clar = [k for k, v in rmap.items()
                if v["status"] != R.SLOT_RESOLVED and v["needs_clarification"]]
        park = [k for k, v in rmap.items()
                if v["status"] != R.SLOT_RESOLVED and not v["needs_clarification"]]
        return res, park, clar

    res25, park25, clar25 = split(r25)
    res30, park30, clar30 = split(r30)
    n = len(r30)
    print("RE-MEASURE with (i) SA-retry-unfiltered + (ii) lag-strip + (iii) cap-30 "
          "(seed+learned bypassed)\n" + "=" * 80)

    # bind flips 25 -> 30
    print("\nCHANGES vs the cap-25 / pre-fix baseline:")
    for k in r30:
        a, b = r25[k], r30[k]
        if (a["status"], a["resolved"]) != (b["status"], b["resolved"]):
            tv = truth.get((k[0], str(k[1])))
            tag = ""
            if b["status"] == R.SLOT_RESOLVED and tv:
                tag = "  ✓TRUTH" if _norm_code(b["resolved"]) == _norm_code(tv) else "  ✗WRONG"
            print(f"  {k[0]} S{k[1]}: {a['status']}({a['resolved']}) -> "
                  f"{b['status']}({b['resolved']}){tag}")
            print(f"      desc: {b['desc']!r}")

    # reject-load delta from the cap bump
    def reject_load(rmap):
        tot = 0
        for v in rmap.values():
            tot += len(v["viable"]) - (1 if v["status"] == R.SLOT_RESOLVED else 0)
        return tot
    rl25, rl30 = reject_load(r25), reject_load(r30)
    new_viable_total = new_exact_total = 0
    new_exact_detail = []
    for k in r30:
        new_v = r30[k]["viable"] - r25[k]["viable"]
        new_e = r30[k]["exacts"] - r25[k]["exacts"]
        new_viable_total += len(new_v)
        if new_e:
            new_exact_total += len(new_e)
            new_exact_detail.append((k, new_e))

    print("\nREJECT-LOAD DELTA (cap 25 -> 30):")
    print(f"  cross-check-passing candidates the gate must consider: "
          f"{rl25 + len(res25)} -> {rl30 + len(res30)} viable total")
    print(f"  net reject-load (viable NOT bound): {rl25} -> {rl30}  (delta +{rl30-rl25})")
    print(f"  NEW cross-check-passing candidates surfaced only at 30: {new_viable_total}")
    print(f"  of which NEW EXACT-token-set (gate-binding-risk) siblings: {new_exact_total}")
    for k, e in new_exact_detail:
        print(f"     {k[0]} S{k[1]}: new exact {sorted(e)}")

    # headline + truth residual at cap-30
    truthed = [k for k in r30 if truth.get((k[0], str(k[1])))]
    correct = [k for k in truthed if r30[k]["status"] == R.SLOT_RESOLVED
               and _norm_code(r30[k]["resolved"]) == _norm_code(truth[(k[0], str(k[1]))])]
    wrong = [k for k in truthed if r30[k]["status"] == R.SLOT_RESOLVED
             and _norm_code(r30[k]["resolved"]) != _norm_code(truth[(k[0], str(k[1]))])]
    park_truth = [k for k in truthed if k in park30]
    print("\n" + "=" * 80)
    print(f"DAY-ONE AUTO-RESOLVE (post-fix): cap-25 {len(res25)}/{n} "
          f"({100*len(res25)/n:.0f}%)  ->  cap-30 {len(res30)}/{n} "
          f"({100*len(res30)/n:.0f}%)   [pre-fix baseline was 16/33 = 48%]")
    print(f"  parked              : {len(park25)} -> {len(park30)}")
    print(f"  parked-clarify(nMA) : {len(clar25)} -> {len(clar30)}")
    print(f"among {len(truthed)} keyed-truth: CORRECT {len(correct)}, "
          f"WRONG {len(wrong)} (must be 0), still-parked {len(park_truth)}")
    for k in park_truth:
        print(f"  residual park: {k[0]} S{k[1]} TRUTH={truth[(k[0], str(k[1]))]}")
    print("\n(residual truth parks should be genuine-unreachables / native-MA — "
          "learned-store territory, proven to close on one supply.)")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "enumerate"
    if mode == "enumerate":
        enumerate_mode()
    elif mode == "resolve":
        resolve_mode()
    elif mode == "reach":
        reach_mode()
    elif mode == "store":
        store_mode()
    elif mode == "remeasure":
        remeasure_mode()
    elif mode == "diag":
        make_search, get_meta, confirm = _remeasure_search_meta()
        search = make_search(30)
        truth = _load_truth()
        for label, slot in _desc_only_slots():
            tv = truth.get((label, str(slot["idx"])))
            if not tv:
                continue
            res = R.resolve_slot(slot, confirm=confirm, get_meta=get_meta,
                                 search=search, clarified={}, trusted={}, learned={})
            print(f"\n{label} S{slot['idx']}  TRUTH={tv}  -> {res['status']}"
                  f" {res.get('resolved') or ''}")
            print(f"   reason: {res['reason']}")
            print(f"   attempts: " + "; ".join(
                f"{a['query']!r}(n={a['n']}{',unf' if a.get('retried_unfiltered') else ''})"
                for a in res.get("search_attempts", [])))
            for v in res.get("candidate_detail", [])[:6]:
                star = " <-TRUTH" if _norm_code(v["code"]) == _norm_code(tv) else ""
                print(f"     {v['code']:18s} exact={str(v['exact']):5s} sim={v['sim']} "
                      f"agg={v['agg']} freq={v['freq']}{star}")
    else:
        print(f"unknown mode {mode!r}")
