"""Was there enough size at the price the backtest bought at?

THE QUESTION THIS SETTLES. Every P&L figure for the bounce idea reads an
ask off the Path tab and books a full position at it. That is a claim about
prices. Whether a quote you can SEE is a quote you can GET, in size, is a
separate claim, and it is the one the +88% ROI on the 10-20c band rests on
without ever having been checked.

It matters most exactly where the edge is supposed to be. A 14c contract is
cheap partly because few people are trading it, so the book behind that
quote can be a handful of contracts -- and $5 at 14c is thirty-five.

Two numbers per setup, both already logged since 19 Sep:

  ASK SIZE at entry   -- can the buy fill at all, and in full?
  BID SIZE at target  -- can the take-profit be sold into when it triggers?

The second is the one that gets forgotten. An exit that cannot be sold is
not a profit, it is a position still open, and the backtest counts it banked.

Read-only. Places nothing, writes nothing.
"""

import sys
from collections import defaultdict

import hedge

STAKE = 5.0
BANDS = ((10, 20), (20, 30), (30, 40), (40, 45))
ENTRY_S = 180.0
TAKE = {10: 3.0, 20: 2.0, 30: 2.0, 40: 2.0}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    import predictor as P
    client = P._get_client()
    sh = client.open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(hedge.SHEET).get_all_values()
    if len(rows) < 2:
        print("no Path rows")
        return 1
    hi = {h: i for i, h in enumerate(rows[0])}
    need = ["Yes Bids", "Yes Asks", "Yes Bid Sz", "Yes Ask Sz",
            "First Offset s", "Step s", "Symbol", "Timestamp"]
    missing = [n for n in need if n not in hi]
    if missing:
        print(f"Path tab is missing columns: {missing}")
        return 1

    def cell(r, k):
        i = hi.get(k, -1)
        return r[i] if 0 <= i < len(r) else ""

    # Depth logging started on 19 Sep and returned blanks for a day while the
    # parser looked for the wrong response shape, so rows without sizes are
    # skipped rather than counted as zero -- counting them as zero would
    # manufacture exactly the conclusion this is testing for.
    stats = defaultdict(lambda: {"n": 0, "ask_sz": [], "bid_sz": [],
                                 "full": 0, "none": 0, "exit_ok": 0,
                                 "exit_n": 0})
    skipped = 0
    for r in rows[1:]:
        ts = str(cell(r, "Timestamp")).replace(" UTC", "").strip()
        if ts in hedge.POISONED:
            continue
        asks = [_f(x) for x in str(cell(r, "Yes Asks")).split(",")]
        bids = [_f(x) for x in str(cell(r, "Yes Bids")).split(",")]
        asz = [_f(x) for x in str(cell(r, "Yes Ask Sz")).split(",")]
        bsz = [_f(x) for x in str(cell(r, "Yes Bid Sz")).split(",")]
        first = _f(cell(r, "First Offset s")) or 30.0
        step = _f(cell(r, "Step s")) or 30.0
        off = (ENTRY_S - first) / step
        if off < 0 or abs(off - round(off)) > 0.01:
            continue
        i = int(round(off))
        if i >= len(asks) or i >= len(bids):
            continue
        a, b = asks[i], bids[i]
        if a is None or b is None:
            continue
        if i >= len(asz) or i >= len(bsz) or asz[i] is None or bsz[i] is None:
            skipped += 1
            continue

        # The cheap side, and the size resting on the side we would BUY.
        no_ask = 100.0 - b
        cheap_yes = a <= no_ask
        entry = a if cheap_yes else no_ask
        # Buying YES lifts the yes ask; buying NO hits the yes bid, because
        # buying NO at p IS selling YES at 100-p. So the size that matters
        # flips with the side, which is easy to get backwards and would
        # flatter whichever side happens to be deeper.
        size_at_entry = asz[i] if cheap_yes else bsz[i]

        band = None
        for lo, up in BANDS:
            if lo <= entry < up:
                band = (lo, up)
                break
        if band is None:
            continue

        s = stats[band]
        s["n"] += 1
        want = STAKE / (entry / 100.0)          # contracts $5 asks for
        s["ask_sz"].append(size_at_entry)
        if size_at_entry >= want:
            s["full"] += 1
        if size_at_entry < 1:
            s["none"] += 1

        # And the EXIT: when the target first trades, is anything bid there?
        take = TAKE[band[0]]
        target = entry * take
        for k in range(i + 1, min(len(bids), len(asks), len(bsz), len(asz))):
            bb, aa = bids[k], asks[k]
            if bb is None or aa is None:
                continue
            px = bb if cheap_yes else (100.0 - aa)
            if px >= target:
                sz = bsz[k] if cheap_yes else asz[k]
                if sz is None:
                    break
                s["exit_n"] += 1
                s["bid_sz"].append(sz)
                if sz >= want:
                    s["exit_ok"] += 1
                break

    def med(a):
        if not a:
            return None
        a = sorted(a)
        return a[len(a) // 2]

    print("=" * 74)
    print("CAN THE BACKTEST'S TRADES ACTUALLY BE FILLED?")
    print(f"  $ {STAKE:.0f} a bet, entry read at +{ENTRY_S/60:.0f}min")
    print(f"  {skipped} rows skipped for having no depth logged")
    print("=" * 74)
    print(f"\n{'band':>8} {'setups':>7} {'wants':>7} {'ask size':>19} "
          f"{'fills in full':>14} {'nothing there':>14}")
    for band in BANDS:
        s = stats.get(band)
        if not s or not s["n"]:
            print(f"{str(band[0])+'-'+str(band[1])+'c':>8} {'-':>7}")
            continue
        mid = (band[0] + band[1]) / 2
        want = STAKE / (mid / 100.0)
        m = med(s["ask_sz"])
        print(f"{str(band[0])+'-'+str(band[1])+'c':>8} {s['n']:>7} "
              f"{want:>6.0f}c {('median '+format(m,'.0f')) if m is not None else '-':>19} "
              f"{s['full']/s['n']*100:>13.0f}% {s['none']/s['n']*100:>13.0f}%")

    print(f"\n{'band':>8} {'targets hit':>12} {'bid size there':>16} "
          f"{'exit fills in full':>19}")
    for band in BANDS:
        s = stats.get(band)
        if not s or not s["exit_n"]:
            print(f"{str(band[0])+'-'+str(band[1])+'c':>8} {'-':>12}")
            continue
        m = med(s["bid_sz"])
        print(f"{str(band[0])+'-'+str(band[1])+'c':>8} {s['exit_n']:>12} "
              f"{('median '+format(m,'.0f')) if m is not None else '-':>16} "
              f"{s['exit_ok']/s['exit_n']*100:>18.0f}%")

    # WHICH TARGET CAN ACTUALLY BE SOLD INTO. The exit depth above is
    # measured at one target per band; the real question is how that depth
    # changes as the target moves. A greedier exit is worth less than it
    # looks if nobody is bid there.
    print("\n" + "=" * 74)
    print("THE 10-20c BAND, EXIT DEPTH AT EACH TARGET")
    print("=" * 74)
    print(f"\n{'target':>8} {'reached':>9} {'bid size':>16} {'can sell all':>14} "
          f"{'median % of position':>21}")
    for take in (1.5, 2.0, 2.5, 3.0):
        hit = ok = 0
        sizes = []
        fracs = []
        for r in rows[1:]:
            ts2 = str(cell(r, "Timestamp")).replace(" UTC", "").strip()
            if ts2 in hedge.POISONED:
                continue
            asks = [_f(x) for x in str(cell(r, "Yes Asks")).split(",")]
            bids = [_f(x) for x in str(cell(r, "Yes Bids")).split(",")]
            asz = [_f(x) for x in str(cell(r, "Yes Ask Sz")).split(",")]
            bsz = [_f(x) for x in str(cell(r, "Yes Bid Sz")).split(",")]
            first = _f(cell(r, "First Offset s")) or 30.0
            step = _f(cell(r, "Step s")) or 30.0
            off = (ENTRY_S - first) / step
            if off < 0 or abs(off - round(off)) > 0.01:
                continue
            i = int(round(off))
            if (i >= len(asks) or i >= len(bids) or i >= len(asz)
                    or i >= len(bsz)):
                continue
            a, b = asks[i], bids[i]
            if a is None or b is None or asz[i] is None or bsz[i] is None:
                continue
            no_ask = 100.0 - b
            cheap_yes = a <= no_ask
            entry = a if cheap_yes else no_ask
            if not (10 <= entry < 20):
                continue
            want = STAKE / (entry / 100.0)
            target = entry * take
            if target >= 100:
                continue
            for k in range(i + 1, min(len(bids), len(asks), len(bsz), len(asz))):
                bb, aa = bids[k], asks[k]
                if bb is None or aa is None:
                    continue
                px = bb if cheap_yes else (100.0 - aa)
                if px >= target:
                    sz = bsz[k] if cheap_yes else asz[k]
                    if sz is None:
                        break
                    hit += 1
                    sizes.append(sz)
                    fracs.append(min(1.0, sz / want))
                    if sz >= want:
                        ok += 1
                    break
        m = med(sizes)
        mf = med(fracs)
        print(f"{str(take)+'x':>8} {hit:>9} "
              f"{('median '+format(m,'.0f')) if m is not None else '-':>16} "
              f"{(str(round(ok/hit*100)) + '%') if hit else '-':>14} "
              f"{(str(round(mf*100)) + '%') if mf is not None else '-':>21}")

    print("\n'wants' is how many contracts $5 buys at the middle of the band.")
    print("'fills in full' is the share of setups with at least that many")
    print("resting at the quoted price -- the backtest assumes 100%.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
