"""CG1 -- calibration of cheap contracts, against REAL settlement.

Pre-registered before looking: buckets are 1c wide from 1c to 10c, the
entry is the first sample where the cheap side is in band with at least
eight minutes left, and the outcome comes from the Settled tab -- Kalshi's
own `result`, joined by ticker.

THE PREDICTION THIS IS TESTING. The earlier version of this study used a
flag we derived ourselves and reported an average price of 8.3c against a
19.4% win rate, a 2.4x mispricing. That flag proved wrong about 19% of the
time, and label noise of that size accounts for the whole gap:

    measured = true(1-e) + (1-true)e = 0.08 + 0.84e,  e=0.136 -> 19.4%

So the expectation recorded IN ADVANCE is that the corrected win rate lands
near 8-9% and the anomaly disappears. Writing that down first is the point;
a study that can accommodate either result explains nothing.

Reports N, average executable price, implied probability, actual win rate,
the gap, and EV per bet before and after Kalshi's fee -- fee charged on
entry only, since a winner settles at $1.00 with nothing to sell.
"""

import math
import sys

import hedge

STAKE = 5.0
MINLEFT = 480.0                  # eight minutes to settlement
WIN = 900.0
BUCKETS = [(i, i + 1) for i in range(1, 10)]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


fee = lambda n, p: math.ceil(0.07 * n * p * (1 - p) * 100) / 100


def collect(path_rows, settled):
    """One observation per market-window: the first cheap moment, and how
    that market actually settled."""
    hi = {h: i for i, h in enumerate(path_rows[0])}

    def cell(r, k):
        i = hi.get(k, -1)
        return r[i] if 0 <= i < len(r) else ""

    obs = []
    for r in path_rows[1:]:
        ts = str(cell(r, "Timestamp")).replace(" UTC", "").strip()
        if ts in hedge.POISONED:
            continue
        ticker = str(cell(r, "Ticker")).strip()
        res = settled.get(ticker)
        if res not in ("yes", "no"):
            continue
        asks = [_f(x) for x in str(cell(r, "Yes Asks")).split(",")]
        bids = [_f(x) for x in str(cell(r, "Yes Bids")).split(",")]
        asz = [_f(x) for x in str(cell(r, "Yes Ask Sz")).split(",")]
        bsz = [_f(x) for x in str(cell(r, "Yes Bid Sz")).split(",")]
        first = _f(cell(r, "First Offset s")) or 30.0
        step = _f(cell(r, "Step s")) or 30.0
        n = min(len(asks), len(bids))
        for k in range(n):
            secs = first + k * step
            if WIN - secs < MINLEFT:
                break
            a, b = asks[k], bids[k]
            if a is None or b is None:
                continue
            no_ask = 100.0 - b
            cheap_yes = a <= no_ask
            px = a if cheap_yes else no_ask
            if not (1.0 <= px < 10.0):
                continue
            # Which side we hold decides which settlement wins, and getting
            # this backwards is the single mistake that has cost this
            # project the most: a NO holder wins when the market resolves
            # "no".
            won = (res == "yes") if cheap_yes else (res == "no")
            sz = (asz[k] if k < len(asz) else None) if cheap_yes \
                else (bsz[k] if k < len(bsz) else None)
            obs.append({"px": px, "won": won, "yes": cheap_yes,
                        "sz": sz, "secs": secs, "ts": ts,
                        "sym": str(cell(r, "Symbol"))})
            break
    return obs


def report(obs, label, cap_depth=False):
    print(f"\n{label}")
    print(f"{'bucket':>8} {'N':>5} {'avg px':>7} {'implied':>8} "
          f"{'actual':>8} {'gap':>7} {'EV/bet':>9}")
    print("-" * 60)
    tot_ev = tot_n = tot_w = 0
    tot_px = 0.0
    for lo, hi_ in BUCKETS:
        sub = [o for o in obs if lo <= o["px"] < hi_]
        if not sub:
            continue
        n = len(sub)
        w = sum(1 for o in sub if o["won"])
        avg = sum(o["px"] for o in sub) / n
        ev = 0.0
        priced = 0
        for o in sub:
            p = o["px"] / 100.0
            c = int(STAKE / p)
            if cap_depth:
                if o["sz"] is None:
                    continue
                c = min(c, int(o["sz"]))
            if c < 1:
                continue
            cost = c * p + fee(c, p)
            ev += (c if o["won"] else 0) - cost
            priced += 1
        tot_ev += ev
        tot_n += n
        tot_w += w
        tot_px += avg * n
        print(f"{str(lo)+'-'+str(hi_)+'c':>8} {n:>5} {avg:>6.1f}c "
              f"{avg:>7.1f}% {w/n*100:>7.1f}% {w/n*100-avg:>+6.1f} "
              + (f"{ev/priced:>+8.2f}" if priced else f"{'-':>8}"))
    if not tot_n:
        print("  no observations")
        return
    print("-" * 60)
    apx = tot_px / tot_n
    print(f"{'ALL':>8} {tot_n:>5} {apx:>6.1f}c {apx:>7.1f}% "
          f"{tot_w/tot_n*100:>7.1f}% {tot_w/tot_n*100-apx:>+6.1f} "
          f"{tot_ev/tot_n:>+8.2f}")
    # Clustered by window: coins in one window share a market move, so
    # treating them as independent would shrink the interval by about the
    # square root of five and invent significance.
    byw = {}
    for o in obs:
        p = o["px"] / 100.0
        c = int(STAKE / p)
        if cap_depth:
            if o["sz"] is None:
                continue
            c = min(c, int(o["sz"]))
        if c < 1:
            continue
        cost = c * p + fee(c, p)
        byw.setdefault(o["ts"], []).append((c if o["won"] else 0) - cost)
    cl = [sum(v) / len(v) for v in byw.values()]
    if len(cl) > 2:
        m = sum(sum(v) for v in byw.values()) / sum(len(v) for v in byw.values())
        var = sum(len(v) * ((sum(v)/len(v)) - m) ** 2
                  for v in byw.values()) / (len(cl) - 1)
        half = 1.96 * (var / len(cl)) ** 0.5
        print(f"  95% CI clustered by window: {m-half:+.2f} to {m+half:+.2f} "
              f"a bet, over {len(cl)} windows")


def main():
    import predictor as P
    client = P._get_client()
    sh = client.open_by_key(P.SPREADSHEET_ID)

    srows = sh.worksheet("Settled").get_all_values()
    if len(srows) < 2:
        print("Settled tab is empty -- run settle_fetch.py first")
        return 1
    si = {h: i for i, h in enumerate(srows[0])}
    settled = {}
    for r in srows[1:]:
        t = (r[si.get("Ticker", 0)] or "").strip()
        v = (r[si.get("Result", 1)] or "").strip().lower()
        if t and v:
            settled[t] = v

    path_rows = sh.worksheet("Path").get_all_values()
    obs = collect(path_rows, settled)

    print("=" * 60)
    print("CG1 -- SUB-10c CALIBRATION, AGAINST KALSHI'S OWN RESULT")
    print("=" * 60)
    print(f"{len(settled)} markets with a real settlement result")
    print(f"{len(obs)} cheap observations, 8+ min to settlement")
    print(f"\nPREDICTED IN ADVANCE: corrected win rate near 8-9%, "
          f"anomaly gone.")

    report(obs, "ASSUMING A FULL $5 FILL")
    report(obs, "CAPPED AT THE DEPTH ACTUALLY RESTING", cap_depth=True)

    # And the comparison that matters: what the old derived flag claimed.
    print("\nFOR COMPARISON, the discredited flag reported:")
    print("   8.4c average price, 19.7% win rate, +11.3pp gap")
    return 0


if __name__ == "__main__":
    sys.exit(main())
