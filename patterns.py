"""Three untested patterns, measured off one load of the live record.

WHY ALL THREE IN ONE FILE. Each needs the same three things: the filled
bets, the real fill costs, and the settlements. Both of the quiet failures
this project has shipped lived in exactly that plumbing -- one page of fills
read as a whole history, one page of 200 settlements read as "all time" --
and the fix was to page them. Writing that paging out three times is how a
fourth copy gets it wrong. It is written once here and shared.

WHAT IS BEING ASKED, and what would count as an answer.

  1. COIN. Never split before. DOGE and XRP travel much further per minute
     than BTC, and a 94c quote does not necessarily know that. If the price
     is calibrated per coin this shows nothing; if one coin is systematically
     mispriced at the same quote, that is the first real finding in weeks.

  2. LEGS. 77 of 80 multi-bet windows settled every leg the same way. That
     is not a signal, it is a SIZING fact: two legs in one window is one bet
     at double stake wearing a disguise. So the question is not "do multi-leg
     windows lose" -- per bet they do not -- but "does taking only the first
     leg keep the same edge at lower variance". That is answered with the
     spread of per-window outcomes, not with a win rate.

  3. FILL SPEED. An order that filled NOTHING was a favourite strengthening:
     that cohort settled YES 98.4%. The mirror has never been looked at. An
     order that fills instantly and completely, at the quote, may be one
     somebody was pleased to sell. The instrumentation columns -- Presubmit c,
     Quote age s, Ack ms -- exist to answer this and have only been
     collecting since 20 Oct, so this section is expected to be thin and
     SAYS SO rather than reporting a number it cannot support.

EVERY CALIBRATION CLAIM IS SPLIT CHRONOLOGICALLY. A gap that flips sign
between the older and newer half is noise, and at this sample size most
things are. Sections 1 and 3 are therefore reported in both halves, with the
rule stated at the bottom. Section 2 is not a calibration question so it is
not split that way; it is reported whole, with the per-window spread.

BREAK-EVEN IS THE GROUP'S OWN MEAN COST PER CONTRACT. A winning contract
pays exactly $1, so a group that paid 93.4c a contract has to win 93.4% to
stand still. Never a round number, never assumed, always computed from what
was actually paid -- fee included, because the fee is part of the cost.

Read-only. Places nothing, cancels nothing, writes nothing.
"""
import collections
import math
import statistics as st

import tally as T


# ---------------------------------------------------------------- shared load

def load():
    """(bets, settled) with real costs and per-ticker payout shares.

    PAGED, both of them. See the module docstring: this is the plumbing that
    has silently lied twice.
    """
    bets = T.rows_since(T.START)
    if not bets:
        return [], []

    setts = {}
    cur, pages = None, 0
    while pages < 60:
        q = dict(limit=200)
        if cur:
            q["cursor"] = cur
        d = T.get("/portfolio/settlements", **q)
        rows = (d or {}).get("settlements") or []
        T._ingest_settlements(rows, setts)
        cur = (d or {}).get("cursor")
        pages += 1
        if not cur or not rows:
            break

    # ONE settlement is shared across every bet on that ticker, split by
    # contract count. Claiming the whole payout on each of two bets in the
    # same market is what once showed a profit that did not exist -- and it
    # would bite section 2 hardest, since multi-leg windows are its subject.
    by_ticker = {}
    for b in bets:
        by_ticker.setdefault(b["ticker"], []).append(b)
    for _tk, g in by_ticker.items():
        tot = sum(int(x["contracts"] or 0) for x in g) or 1
        for x in g:
            x["_share"] = (int(x["contracts"] or 0) / tot) if len(g) > 1 else 1.0

    fills = T.actual_costs()
    for b in bets:
        f = fills.get(b["oid"])
        b["cost"] = (f["cost"] if (f and f["cost"]) else (b["limit_cost"] or 0.0))
        s = setts.get(b["ticker"])
        b["rev"] = (s["rev"] * b.get("_share", 1.0)) if s else None
        b["coin"] = coin_of(b["ticker"])
        b["won"] = (b["rev"] or 0.0) > 0 if b["rev"] is not None else None

    settled = [b for b in bets if b["rev"] is not None]
    return bets, settled


def coin_of(ticker):
    """BTC from KXBTC15M-26OCT21... . Unknown stays unknown, never guessed."""
    t = str(ticker or "").upper()
    for c in ("BTC", "ETH", "SOL", "XRP", "DOGE"):
        if "KX" + c in t:
            return c
    return "?"


def be_line(group):
    """The win rate this group needs: its own mean cost per contract, cents."""
    c = sum(b["cost"] or 0.0 for b in group)
    n = sum(int(b["contracts"] or 0) for b in group)
    return (100.0 * c / n) if n else float("nan")


def line(label, group, minimum=12):
    """One row: n, win rate, the break-even it owes, the gap, net, interval."""
    n = len(group)
    if n < minimum:
        print(f"    {label:<16} {n:>4}      (too few to read)")
        return
    wins = sum(1 for b in group if b["won"])
    staked = sum(b["cost"] or 0.0 for b in group)
    net = sum(b["rev"] or 0.0 for b in group) - staked
    wr, be = 100.0 * wins / n, be_line(group)
    lo, hi = T.wilson(wins, n)
    print(f"    {label:<16} {n:>4}   {wr:>6.2f}   {be:>6.2f}   {wr-be:>+6.2f}   "
          f"${net:>+7.2f}   {100*lo:>5.1f}-{100*hi:<5.1f} "
          f"{'contains' if 100*lo <= be <= 100*hi else 'EXCLUDES'} BE")


def header():
    print(f"    {'group':<16} {'n':>4}   {'won %':>6}   {'need':>6}   "
          f"{'gap':>6}   {'net':>8}   95% interval")


def halves(settled):
    """(older, newer) by timestamp. The only honest way to read a mined gap."""
    s = sorted(settled, key=lambda b: b["ts"])
    m = len(s) // 2
    return s[:m], s[m:]


# ------------------------------------------------------------- 1. by the coin

def by_coin(settled):
    print("\n" + "=" * 100)
    print("1. BY COIN -- does a 94c quote mean the same thing on DOGE as on BTC?")
    print("=" * 100)
    print("  DOGE and XRP travel further per minute than BTC. If the quote is")
    print("  calibrated per coin, every gap here sits near zero.\n")
    for name, half in (("OLDER half", halves(settled)[0]),
                       ("NEWER half", halves(settled)[1])):
        print(f"  {name}")
        header()
        g = collections.defaultdict(list)
        for b in half:
            g[b["coin"]].append(b)
        for k in sorted(g):
            line(k, g[k])
        print()
    print("  WHOLE RUN, for the dollar view only -- do not read a gap off this,")
    print("  it is the same data as above with the split removed.")
    header()
    g = collections.defaultdict(list)
    for b in settled:
        g[b["coin"]].append(b)
    for k in sorted(g, key=lambda x: -len(g[x])):
        line(k, g[k])


# ------------------------------------------------- 2. legs in the same window

def by_legs(settled):
    print("\n" + "=" * 100)
    print("2. LEGS PER WINDOW -- is first-leg-only the same edge at less risk?")
    print("=" * 100)
    print("  77 of 80 multi-bet windows settled every leg identically, so a")
    print("  two-leg window is one bet at double stake. This is a variance")
    print("  question, not a win-rate one, so read the SPREAD, not the gap.\n")

    win = collections.defaultdict(list)
    for b in settled:
        win[b["ts"]].append(b)

    one = [b for ts, g in win.items() if len(g) == 1 for b in g]
    many = [b for ts, g in win.items() if len(g) > 1 for b in g]
    print("  PER BET, one-leg windows against multi-leg windows:")
    header()
    line("1 leg", one)
    line("2+ legs", many)

    agree = same = 0
    for _ts, g in win.items():
        if len(g) < 2:
            continue
        same += 1
        if len({bool(b["won"]) for b in g}) == 1:
            agree += 1
    if same:
        print(f"\n  {agree} of {same} multi-leg windows resolved every leg the "
              f"same way ({100.0*agree/same:.0f}%).")

    # THE ACTUAL COMPARISON. Per WINDOW, because that is the unit of risk:
    # what the bot staked in one correlated event, and what came back.
    full, first = [], []
    for _ts, g in sorted(win.items()):
        gg = sorted(g, key=lambda b: str(b["ticker"]))
        full.append(sum(b["rev"] or 0.0 for b in gg)
                    - sum(b["cost"] or 0.0 for b in gg))
        f = gg[0]
        first.append((f["rev"] or 0.0) - (f["cost"] or 0.0))

    def show(lab, v):
        if len(v) < 2:
            print(f"  {lab:<22} (too few windows)")
            return
        sd = st.pstdev(v)
        tot = sum(v)
        worst = min(v)
        print(f"  {lab:<22} net ${tot:>+8.2f}   per window ${tot/len(v):>+6.3f}"
              f"   sd ${sd:>5.2f}   worst ${worst:>+7.2f}"
              f"   net/sd {tot/sd if sd else float('nan'):>+6.2f}")

    print(f"\n  ACROSS {len(full)} WINDOWS:")
    show("as actually bet", full)
    show("first leg only", first)
    print("\n  READ net/sd, NOT net. Taking one leg stakes less, so a smaller")
    print("  net is expected and is not the point. The question is whether the")
    print("  return per unit of risk improves, and whether the worst window")
    print("  gets less bad -- all three of the catastrophic windows were")
    print("  multi-leg, which is the whole reason this is being asked.")


# ------------------------------------------------------------ 3. fill speed

def by_fill(settled):
    print("\n" + "=" * 100)
    print("3. FILL SPEED -- is an order that fills instantly one somebody was")
    print("   pleased to sell?")
    print("=" * 100)
    print("  An order that filled NOTHING was a favourite strengthening: that")
    print("  cohort settled YES 98.4%. This is the mirror. The columns it needs")
    print("  only started recording on 20 Oct, so a thin answer here is the")
    print("  honest one and is reported as thin rather than dressed up.\n")

    import live
    import predictor as P
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(live.LIVE_SHEET).get_all_values()
    if len(rows) < 2:
        print("  no live rows")
        return
    h = {k: i for i, k in enumerate(rows[0])}
    for col in ("Ack ms", "Quote age s", "Presubmit ¢"):
        if col not in h:
            print(f"  the '{col}' column does not exist in the sheet yet -- "
                  "nothing to measure, and no number will be invented.")
            return

    meta = {}
    for r in rows[1:]:
        oid = str(r[h["Order ID"]]).strip() if h.get("Order ID") is not None \
            and h["Order ID"] < len(r) else ""
        if not oid:
            continue

        def g(k):
            i = h[k]
            return T._f(r[i]) if i < len(r) else None
        meta[oid] = dict(ack=g("Ack ms"), age=g("Quote age s"),
                         pre=g("Presubmit ¢"))

    have = [b for b in settled if meta.get(b["oid"], {}).get("ack") is not None]
    print(f"  {len(have)} of {len(settled)} settled bets carry an Ack ms "
          "reading.\n")
    if len(have) < 25:
        print("  TOO FEW. The instrumentation needs more weeks; it is "
              "collecting on every live session.")
        return

    for b in have:
        m = meta[b["oid"]]
        b["_ack"], b["_age"] = m["ack"], m["age"]
        # Did the ask move between the scan and the submit? A quote that
        # IMPROVED while we waited is the strengthening favourite again.
        b["_drift"] = ((b["entry"] - m["pre"])
                       if (b["entry"] is not None and m["pre"] is not None)
                       else None)

    def bucket(key, edges, label):
        print(f"  by {label}")
        for nm, half in (("OLDER", halves(have)[0]), ("NEWER", halves(have)[1])):
            print(f"   {nm}")
            header()
            for lo, hi in zip(edges, edges[1:]):
                g = [b for b in half
                     if b.get(key) is not None and lo <= b[key] < hi]
                line(f"{lo:g}-{hi:g}", g)
            print()

    bucket("_ack", [0, 200, 400, 800, 10 ** 9], "acknowledgement time (ms)")
    bucket("_age", [0, 1, 2, 4, 10 ** 6], "quote age at submit (s)")


def main():
    bets, settled = load()
    print("=" * 100)
    print("THREE UNTESTED PATTERNS -- coin, correlated legs, fill speed")
    print("=" * 100)
    if not settled:
        print("  nothing settled yet")
        return 0
    print(f"  {len(bets)} filled bets since {T.START}, {len(settled)} settled")
    print("  'need' is each group's own mean cost per contract, because a "
          "winning contract pays exactly $1.")

    by_coin(settled)
    by_legs(settled)
    by_fill(settled)

    print("\n" + "=" * 100)
    print("  THE RULE. A bucket counts only if its gap is negative in BOTH")
    print("  halves and its interval excludes break-even. At this sample size")
    print("  almost nothing will clear that, and the honest reading of a sign")
    print("  that flips between halves is noise -- not a weaker version of a")
    print("  real effect. Section 2 is the exception: it is a variance")
    print("  question, so net/sd and the worst window are what decide it.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
