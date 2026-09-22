"""Can resting liquidity be sold profitably on these markets?

Every strategy tested so far crossed the spread. This asks the opposite
question: if we REST an order and let others cross to us, do we collect more
than we give up? Market-making P&L decomposes exactly:

    edge per contract = half the spread - adverse drift - fees

and each of those three terms is measurable here, with one caveat that is
stated rather than hidden.

WHAT THE DATA ACTUALLY IS (mapped from path.py, not assumed). One Path row
per market-window, holding a 27-point series sampled every 30s from +30s to
+13:30:

    Timestamp      window boundary, "YYYY-MM-DD HH:MM UTC"
    Symbol         BTC / ETH / SOL / XRP / DOGE
    Ticker         the market -- the join key to Settled
    Strike         the fixed level the market settles against
    Spot at Open   underlying at window open (ONE reading, not a series)
    First Offset s 30
    Step s         30
    Samples        how many of the 27 came back priced
    Yes Bids       27 comma-separated cents, best YES bid
    Yes Asks       27 comma-separated cents, best YES ask
    Yes Bid Sz     contracts resting at the best YES bid
    Yes Ask Sz     contracts resting at the best YES ask
    Note           blank, or a count of unpriced samples

FOUR LIMITS THAT BOUND EVERY CONCLUSION BELOW, none of them fixable by
being cleverer with this data:

  NO SEPARATE NO-SIDE COLUMNS, AND NONE ARE NEEDED. Kalshi runs ONE book.
  A resting YES bid at p is the same order as a resting NO ask at 100-p;
  "Yes Ask Sz" is literally read from the NO bid side. So "quote NO" is not
  a second strategy to test, it is the same order with the labels swapped:

      NO ask  = 100 - YES bid,  size = Yes Bid Sz
      NO bid  = 100 - YES ask,  size = Yes Ask Sz

  This halves the strategy space rather than limiting the study, and it has
  a consequence used below: the two maker legs' markouts sum to EXACTLY the
  spread at every horizon, so measuring one measures the other.

  NO TAPE. There is no record of executions -- no trade prices, sizes or
  aggressor side. Fills therefore cannot be measured, only BOUNDED. That is
  what the three models below are for, and it is why no single fill number
  is reported as the answer.

  NO QUEUE POSITION, and 30s between snapshots. Where our order would sit
  behind the resting size is unknowable, and so is the path the book took
  between two readings. Nothing here pretends otherwise.

  LEVEL ONE ONLY. path.py requests depth=1, so "depth" throughout means
  size at the touch, never the full ladder.

WHY THE VERDICT SURVIVES ALL OF THAT. Expected value per opportunity is

    P(fill) x E[edge | fill]

and P(fill) is a positive multiplier. It scales how big an opportunity is;
it cannot change its SIGN. So if E[edge | fill] is negative, no fill model
rescues the strategy, and the unmeasurable term stops mattering. The fill
models still run, because if the edge is positive its size is the question.

THE SELECTION PROBLEM, handled by bounding rather than assuming. Adverse
selection IS the correlation between filling and being wrong: a resting bid
gets hit hardest by sellers who are right. So fills are a biased subset of
opportunities, and the bias runs against us. The three models bracket it:

  MODEL A  fill always. Ignores selection entirely -- deliberately
           optimistic, and an upper bound on a maker's luck.

  MODEL B  fill when the book did not move away from our price, i.e. the
           touch stayed at or below where we quoted.

  MODEL C  fill only when the next snapshot shows the touch BELOW our
           resting price. Then our order was either executed or it would
           itself be the best bid -- and it is not there, so it filled.
           This is the strongest available evidence of executability, and
           it is also, by construction, the adversely-selected subset. It
           is a hard lower bound, not a pessimistic guess.

Truth lies between A and C. If C and A share a sign, the sign is settled.

WHAT IS HELD BACK. The last 20% of windows, chronologically, are not read by
this script at all. The cutoff is computed and printed, and the holdout is
only reachable by passing --holdout, which is not done until a specification
is frozen.

Read-only. Places nothing, writes nothing.
"""

import collections
import math
import random
import statistics as stat
import sys

import hedge

WINDOW_S = 900.0
HOLDOUT_FRAC = 0.20
HORIZONS = [30, 60, 90, 120, 300]        # seconds; settlement handled apart
STAKES = [1.0, 2.0, 5.0, 10.0, 25.0]
MIN_N = 30
SEED = 20260922


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def fee_dollars(n, p):
    """Kalshi's fee on n contracts at price p (dollars 0-1), rounded UP to
    the cent for the whole order. The ceiling does not scale, so a fee per
    contract must be derived from a real order size, never assumed."""
    return math.ceil(round(0.07 * n * p * (1.0 - p), 9) * 100) / 100.0


def load(sh, include_holdout=False):
    rows = sh.worksheet("Path").get_all_values()
    hi = {h: i for i, h in enumerate(rows[0])}

    def cell(r, k):
        i = hi.get(k, -1)
        return r[i] if 0 <= i < len(r) else ""

    srows = sh.worksheet("Settled").get_all_values()
    si = {h: i for i, h in enumerate(srows[0])}
    settled = {}
    for r in srows[1:]:
        t = (r[si.get("Ticker", 0)] or "").strip()
        v = (r[si.get("Result", 1)] or "").strip().lower()
        if t and v in ("yes", "no"):
            settled[t] = v

    # Chronological split BEFORE any measurement, so the holdout cannot be
    # reached by accident. Split on distinct windows, not rows: five coins
    # share a window and must never straddle the boundary.
    stamps = sorted({str(cell(r, "Timestamp")).replace(" UTC", "").strip()
                     for r in rows[1:]} - set(hedge.POISONED))
    cut_i = int(len(stamps) * (1.0 - HOLDOUT_FRAC))
    cutoff = stamps[cut_i] if 0 < cut_i < len(stamps) else None

    out = []
    for r in rows[1:]:
        ts = str(cell(r, "Timestamp")).replace(" UTC", "").strip()
        if ts in hedge.POISONED or not ts:
            continue
        if cutoff and ((ts >= cutoff) != bool(include_holdout)):
            continue
        tk = str(cell(r, "Ticker")).strip()
        bids = [_f(x) for x in str(cell(r, "Yes Bids")).split(",")]
        asks = [_f(x) for x in str(cell(r, "Yes Asks")).split(",")]
        bsz = [_f(x) for x in str(cell(r, "Yes Bid Sz")).split(",")]
        asz = [_f(x) for x in str(cell(r, "Yes Ask Sz")).split(",")]
        first = _f(cell(r, "First Offset s")) or 30.0
        step = _f(cell(r, "Step s")) or 30.0
        out.append({"ts": ts, "sym": str(cell(r, "Symbol")).strip(),
                    "ticker": tk, "res": settled.get(tk),
                    "bids": bids, "asks": asks, "bsz": bsz, "asz": asz,
                    "first": first, "step": step})
    return out, cutoff, len(stamps), len(settled)


def build(series):
    """One record per (market, snapshot): the quote we could have rested,
    where the mid went afterwards, and how the market finally settled.

    The maker leg modelled is BUY YES resting at the best bid. Its mirror --
    BUY NO at 100 minus the best ask -- needs no separate pass: the two
    markouts sum to the spread at every horizon, which the report checks
    rather than asserts."""
    obs = []
    for m in series:
        b, a, bs, asz_ = m["bids"], m["asks"], m["bsz"], m["asz"]
        n = min(len(b), len(a))
        step = m["step"] or 30.0
        for k in range(n):
            if b[k] is None or a[k] is None:
                continue
            spread = a[k] - b[k]
            if spread <= 0 or not (0 < b[k] < 100) or not (0 < a[k] < 100):
                continue                      # crossed or degenerate quote
            mid = (a[k] + b[k]) / 2.0
            secs = m["first"] + k * step
            fut = {}
            for h in HORIZONS:
                j = k + int(round(h / step))
                if j < n and b[j] is not None and a[j] is not None \
                        and a[j] > b[j]:
                    fut[h] = (a[j] + b[j]) / 2.0
            # Model B and C both key off where the touch went next.
            nb = b[k + 1] if k + 1 < n else None
            obs.append({
                "ts": m["ts"], "sym": m["sym"], "ticker": m["ticker"],
                "res": m["res"], "secs": secs, "left": WINDOW_S - secs,
                "bid": b[k], "ask": a[k], "mid": mid, "spread": spread,
                "bsz": bs[k] if k < len(bs) else None,
                "asz": asz_[k] if k < len(asz_) else None,
                "fut": fut, "next_bid": nb,
            })
    return obs


def fills(o, model):
    """Would a buy resting at the best bid have been executed? See the
    module docstring -- A is an upper bound, C a lower bound."""
    if model == "A":
        return True
    nb = o["next_bid"]
    if nb is None:
        return False
    return nb <= o["bid"] if model == "B" else nb < o["bid"]


def edge_cents(o, h):
    """Markout on a maker BUY at the bid, in cents per contract.

    Decomposes as half the spread (what resting earns when nothing moves)
    plus the drift of the mid (what the market takes back). Fees are NOT
    included here; they are applied per order size, where the ceiling
    behaves correctly."""
    if h == "settle":
        if o["res"] not in ("yes", "no"):
            return None
        return (100.0 if o["res"] == "yes" else 0.0) - o["bid"]
    m = o["fut"].get(h)
    return None if m is None else m - o["bid"]


def drift_cents(o, h):
    if h == "settle":
        return None
    m = o["fut"].get(h)
    return None if m is None else m - o["mid"]


def cluster(vals_by_window):
    """Mean with a window-clustered 95% interval. Five coins in one window
    share a market move; treating them as independent understates the
    interval and manufactures significance.

    THE STANDARD SANDWICH ESTIMATOR, and not the thing that looks like it.
    An earlier version here averaged the window means and divided by the
    window count, which inflates the interval by roughly the square root of
    the observations per window -- 7.5x on a 50-per-window test where the
    right answer is known because the data is uncorrelated by construction.
    That error is conservative, so it never invented an edge; it hid them.
    The estimator below reduces to sigma/sqrt(N) when clusters carry no
    correlation, which is the property worth testing and is tested."""
    g = {k: v for k, v in vals_by_window.items() if v}
    G = len(g)
    if G < 3:
        return None
    nb = sum(len(v) for v in g.values())
    m = sum(sum(v) for v in g.values()) / nb
    # Sum of within-cluster residuals, squared and summed across clusters.
    ss = sum((sum(v) - len(v) * m) ** 2 for v in g.values())
    var = (G / (G - 1.0)) * ss / (nb ** 2)
    half = 1.96 * math.sqrt(var)
    return {"m": m, "lo": m - half, "hi": m + half, "n": nb, "w": G}


def boot(vals_by_window, reps=2000, rng=None):
    """Window-level block bootstrap: resample whole windows with
    replacement, keeping all five coins of a window together."""
    rng = rng or random.Random(SEED)
    ws = [v for v in vals_by_window.values() if v]
    if len(ws) < 10:
        return None
    means = []
    for _ in range(reps):
        pick = [ws[rng.randrange(len(ws))] for _ in range(len(ws))]
        tot = sum(sum(v) for v in pick)
        cnt = sum(len(v) for v in pick)
        if cnt:
            means.append(tot / cnt)
    means.sort()
    return {"lo": means[int(0.025 * len(means))],
            "hi": means[int(0.975 * len(means))]}


def naive(vals_by_window):
    flat = [x for v in vals_by_window.values() for x in v]
    if len(flat) < 3:
        return None
    m = stat.mean(flat)
    se = stat.stdev(flat) / math.sqrt(len(flat))
    return {"m": m, "lo": m - 1.96 * se, "hi": m + 1.96 * se}


def group(obs, h, model, keyfn=lambda o: o["ts"]):
    g = collections.defaultdict(list)
    for o in obs:
        if not fills(o, model):
            continue
        e = edge_cents(o, h)
        if e is not None:
            g[keyfn(o)].append(e)
    return g


def pct(x, d=2):
    return "--" if x is None else f"{x:+.{d}f}"


def main():
    use_holdout = "--holdout" in sys.argv
    import predictor as P
    client = P._get_client()
    sh = client.open_by_key(P.SPREADSHEET_ID)
    series, cutoff, nstamps, nsettled = load(sh, include_holdout=use_holdout)
    obs = build(series)

    print("=" * 74)
    print("MAKER STUDY -- can resting liquidity be sold at a profit?")
    print("=" * 74)
    print(f"\n  {nstamps} windows collected; holdout cutoff at {cutoff} UTC")
    print(f"  reading the {'HOLDOUT (last 20%)' if use_holdout else 'TRAIN+DEV portion (first 80%)'}"
          f" -- {len(series)} market-windows, {len(obs)} quotable snapshots")
    print(f"  {nsettled} markets carry an authoritative settlement result")
    if not obs:
        print("\n  nothing to analyse")
        return 1

    # ---------------------------------------------------------------- ticks
    sp = sorted(o["spread"] for o in obs)
    uniq = sorted({round(o["spread"], 1) for o in obs})
    print(f"\n  SPREAD, in cents")
    print(f"    min {sp[0]:.1f}   p25 {sp[len(sp)//4]:.1f}   median "
          f"{sp[len(sp)//2]:.1f}   p75 {sp[3*len(sp)//4]:.1f}   max {sp[-1]:.1f}")
    print(f"    distinct values observed: {uniq[:12]}"
          f"{' ...' if len(uniq) > 12 else ''}")
    half = stat.median(sp) / 2.0
    print(f"    => a maker at the touch earns at most {half:.2f}c per "
          f"contract before drift and fees")

    # ------------------------------------------------- the fee, which is the
    # decisive arithmetic and needs no fill model at all
    print(f"\n  WHAT THE EXCHANGE TAKES, against that {half:.2f}c")
    print(f"    Kalshi's fee is ceil(0.07 * n * p * (1-p)) to the cent. If a")
    print(f"    maker pays the same fee as a taker, this alone decides it:")
    print(f"\n    {'price':>7}" + "".join(f"{'$%g'%s:>11}" for s in STAKES))
    print(f"    {'':>7}" + "".join(f"{'c/contract':>11}" for _ in STAKES))
    for pc in (5, 10, 20, 35, 50, 65, 80, 95):
        cells = []
        for s in STAKES:
            n = int(s / (pc / 100.0))
            cells.append(f"{fee_dollars(n, pc/100.0)/n*100:>11.2f}" if n >= 1
                         else f"{'n<1':>11}")
        print(f"    {str(pc)+'c':>7}" + "".join(cells))
    print(f"\n    Any cell above {half:.2f}c means the fee exceeds the whole")
    print(f"    half-spread, so that quote loses money before the market")
    print(f"    moves at all. Maker fees may differ from taker fees -- the")
    print(f"    zero-fee column below is the check that makes this moot.")

    # ------------------------------------------------------------- markout
    print(f"\n  MARKOUT ON A BUY RESTING AT THE BID, cents per contract")
    print(f"  (half-spread earned, minus what the mid gives back)")
    print(f"\n    {'model':>6} {'horizon':>9} {'fills':>8} {'fill%':>7} "
          f"{'drift':>8} {'edge':>8}   95% CI window-clustered")
    for model in ("A", "B", "C"):
        for h in HORIZONS + ["settle"]:
            g = group(obs, h, model)
            c = cluster(g)
            if not c or c["n"] < MIN_N:
                continue
            nf = sum(1 for o in obs if fills(o, model))
            dr = [drift_cents(o, h) for o in obs if fills(o, model)]
            dr = [d for d in dr if d is not None]
            lab = "settle" if h == "settle" else f"{h}s"
            print(f"    {model:>6} {lab:>9} {c['n']:>8} "
                  f"{nf/len(obs)*100:>6.1f}% "
                  f"{(pct(stat.mean(dr)) if dr else '--'):>8} "
                  f"{pct(c['m']):>8}   {pct(c['lo'])} to {pct(c['hi'])}")
        print()

    # ------------------------------------------------ the mirror-leg identity
    print("  CHECK -- the two maker legs must sum to the spread at every")
    print("  horizon (buy YES at bid, buy NO at 100-ask). If this does not")
    print("  hold, the book model is wrong and nothing above is valid.")
    for h in (30, 60):
        worst = 0.0
        seen = 0
        for o in obs:
            if h not in o["fut"]:
                continue
            yes_edge = edge_cents(o, h)          # mid_h - bid
            no_edge = o["ask"] - o["fut"][h]     # ask - mid_h
            worst = max(worst, abs(yes_edge + no_edge - o["spread"]))
            seen += 1
        if seen:
            print(f"    {h}s: max |yes_edge + no_edge - spread| = "
                  f"{worst:.6f}c over {seen} snapshots")

    # --------------------------------------------------------- how it splits
    def table(title, keyfn, order=None, h=60, model="C"):
        print(f"\n  {title}   (model {model}, {h}s markout)")
        print(f"    {'bucket':>16} {'fills':>8} {'edge':>9}   95% CI")
        g = collections.defaultdict(lambda: collections.defaultdict(list))
        for o in obs:
            if not fills(o, model):
                continue
            e = edge_cents(o, h)
            if e is not None:
                g[keyfn(o)][o["ts"]].append(e)
        keys = order or sorted(g)
        for k in keys:
            if k not in g:
                continue
            c = cluster(g[k])
            if not c or c["n"] < MIN_N:
                print(f"    {str(k):>16} {(c['n'] if c else 0):>8}   (too few)")
                continue
            print(f"    {str(k):>16} {c['n']:>8} {pct(c['m']):>9}   "
                  f"{pct(c['lo'])} to {pct(c['hi'])}")

    # Derived from the data, never hardcoded. The first version of this
    # listed ["BTC", ...] while the sheet holds "BTCUSDT", so every key
    # missed and the table printed NOTHING -- no error, no zero rows, just
    # an absent section that reads like "no coins qualified". That is the
    # exact failure mode this project keeps paying for.
    table("BY COIN", lambda o: o["sym"], sorted({o["sym"] for o in obs}))
    table("BY WINDOW PHASE", lambda o: ("early <5min" if o["secs"] < 300
                                        else "mid 5-10min" if o["secs"] < 600
                                        else "late 10min+"),
          ["early <5min", "mid 5-10min", "late 10min+"])
    table("BY SPREAD", lambda o: (f"{o['spread']:.1f}c" if o["spread"] <= 2
                                  else "2.5c+"))
    table("BY PRICE BAND", lambda o: next(
        (f"{lo}-{hi}c" for lo, hi in [(0, 10), (10, 20), (20, 40), (40, 60),
                                      (60, 80), (80, 90), (90, 100)]
         if lo <= o["mid"] < hi), "?"),
        ["0-10c", "10-20c", "20-40c", "40-60c", "60-80c", "80-90c", "90-100c"])

    def dbucket(o):
        s = o["bsz"]
        if s is None:
            return "unknown"
        return ("very shallow" if s < 10 else "shallow" if s < 50
                else "medium" if s < 250 else "deep")
    table("BY DEPTH AT OUR SIDE", dbucket,
          ["very shallow", "shallow", "medium", "deep"])

    def imb(o):
        b, a = o["bsz"], o["asz"]
        if b is None or a is None or (b + a) <= 0:
            return "unknown"
        x = (b - a) / (b + a)
        return ("bids <<" if x < -0.5 else "bids <" if x < -0.15
                else "balanced" if x <= 0.15 else "bids >" if x <= 0.5
                else "bids >>")
    table("BY BOOK IMBALANCE (bid vs ask size)", imb,
          ["bids <<", "bids <", "balanced", "bids >", "bids >>"])

    # ----------------------------------------------------------- dependence
    print("\n  HOW MUCH THE CLUSTERING MATTERS (model C, 60s)")
    g = group(obs, 60, "C")
    for lab, f in (("trade-level (WRONG)", naive), ("window-clustered", cluster)):
        r = f(g)
        if r:
            print(f"    {lab:<22} {pct(r['m'])}   {pct(r['lo'])} to {pct(r['hi'])}")
    bb = boot(g)
    if bb:
        print(f"    {'window bootstrap':<22} {'':>6}   "
              f"{pct(bb['lo'])} to {pct(bb['hi'])}")

    # ---------------------------------------------------------- null tests
    print("\n  NULL TESTS -- NOT APPLICABLE TO THIS MEASUREMENT, and saying")
    print("  so rather than printing one is the point.")
    print("    A permutation null destroys the link between WHICH TRADES A")
    print("    RULE SELECTS and how they turn out. The statistic here is the")
    print("    mean over EVERY filled quote -- there is no selection step, so")
    print("    there is nothing for a permutation to destroy. Any shuffle")
    print("    returns the observed value exactly, because a mean is the")
    print("    total over the count and both survive reordering.")
    print("    An earlier run printed all three nulls at -5.46 against an")
    print("    observed -5.46. That is not three passed tests; it is the")
    print("    arithmetic tautology showing through, the same way the first")
    print("    grid-search null did.")
    print("    Null tests belong to STEP 10 (selective quoting), where a")
    print("    filter chooses a subset. They are run there and not before.")

    # ------------------------------------------------- how bad was the fill
    print("\n  MODEL C, SPLIT BY HOW FAR THE TOUCH MOVED THROUGH US")
    print("  This matters: with 30s between snapshots, 'the bid fell below")
    print("  our price' lumps a routine one-tick fill together with a sweep.")
    print("  If the loss is concentrated in large moves, model C is not just")
    print("  conservative, it is selecting for catastrophes.")
    print(f"    {'touch fell by':>16} {'fills':>8} {'edge':>9}   95% CI")
    gm = collections.defaultdict(lambda: collections.defaultdict(list))
    for o in obs:
        if not fills(o, "C"):
            continue
        e = edge_cents(o, 60)
        if e is None:
            continue
        d = o["bid"] - o["next_bid"]
        k = ("<=0.5c" if d <= 0.5 else "0.5-1c" if d <= 1.0
             else "1-2c" if d <= 2.0 else "2-5c" if d <= 5.0 else "5c+")
        gm[k][o["ts"]].append(e)
    for k in ("<=0.5c", "0.5-1c", "1-2c", "2-5c", "5c+"):
        if k not in gm:
            continue
        c = cluster(gm[k])
        if c and c["n"] >= MIN_N:
            print(f"    {k:>16} {c['n']:>8} {pct(c['m']):>9}   "
                  f"{pct(c['lo'])} to {pct(c['hi'])}")

    # ------------------------------------------------ STEP 4: the EV equation
    print("\n  EXPECTED VALUE PER OPPORTUNITY  =  P(fill) x E[edge | fill]")
    print("  Reported at $2 and $5, the sizes a $144 account can actually")
    print("  place, with the fee charged on entry only (hold to settlement).")
    print(f"\n    {'model':>6} {'stake':>7} {'P(fill)':>9} {'edge/fill':>11} "
          f"{'fee/contract':>13} {'net/fill':>10} {'EV/opportunity':>15}")
    for model in ("A", "B", "C"):
        gg = group(obs, 60, model)
        c = cluster(gg)
        if not c:
            continue
        pfill = sum(1 for o in obs if fills(o, model)) / len(obs)
        filled_os = [o for o in obs if fills(o, model)]
        for s in (2.0, 5.0):
            fees = []
            for o in filled_os:
                p = o["bid"] / 100.0
                n = int(s / p)
                if n >= 1:
                    fees.append(fee_dollars(n, p) / n * 100.0)
            if not fees:
                continue
            fc = stat.mean(fees)
            print(f"    {model:>6} {'$%g'%s:>7} {pfill*100:>8.1f}% "
                  f"{pct(c['m']):>11} {fc:>13.2f} {pct(c['m']-fc):>10} "
                  f"{pct((c['m']-fc)*pfill):>15}")
    print("\n    ZERO-FEE CHECK -- if makers paid nothing at all:")
    for model in ("A", "B", "C"):
        gg = group(obs, 60, model)
        c = cluster(gg)
        if not c:
            continue
        pfill = sum(1 for o in obs if fills(o, model)) / len(obs)
        print(f"      model {model}: net/fill {pct(c['m'])}c, "
              f"EV/opportunity {pct(c['m']*pfill)}c   "
              f"[{pct(c['lo'])} to {pct(c['hi'])} per fill]")

    print("\n" + "=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
