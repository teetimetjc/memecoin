"""What else on Kalshi opens and closes fast -- and is any of it traded?

THE QUESTION. The 15-minute crypto markets are the only thing this bot runs
on. If other series turn over at a similar speed, the same rule could in
principle be pointed at them, and more windows a day is the one thing that
genuinely helps: it shortens the time to every decision this project is
waiting on.

THE FILTER THAT MATTERS IS NOT SPEED, IT IS LIQUIDITY, and that is the whole
reason this file is careful. The long-dated test already died here. It found
14,487 markets, applied an exit rule to them, and the books turned out to be
EMPTY -- volume None, open interest None, nothing on either side. A backtest
that fills orders in an empty book is describing trades that could not have
been placed. Wide spreads with nobody in them are not opportunities.

So every candidate is reported WITH its traded volume, and a series with no
volume is printed as dead rather than quietly dropped. A fast market nobody
trades is worse than useless: it is a tempting-looking number.

TWO FIELD-NAME TRAPS ARE PRE-EMPTED HERE, because this exact family of bug
has now cost this project four separate findings:

  The settled /markets response carries NO `volume` key at all -- 2,341 of
  2,341 missing -- so `m.get("volume") or 0` scores every market as zero and
  silently drops the lot.

  The candlesticks carry `volume_fp`, not `volume`. Same mistake one level
  down, which dropped all 5,906 markets on the retry.

Volume therefore comes from the candles, by the `_fp` name, and the count of
markets that returned no candles at all is printed so a zero is readable as
"no data" rather than "no trading".

LIFETIME IS close_time - open_time, bucketed. Sub-hour is what the bot's
cadence could actually use; the rest is printed for context and labelled.

Read-only.
"""
import collections
import statistics as st
import sys
import time

import backfill as B

MAX_LIFE_H = 24           # everything faster than a day
PER_SERIES = 6            # markets sampled per series, for the volume read
MIN_SETTLED = 10
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 2400
# The ones already being traded. Listed so they appear as a BASELINE rather
# than as a discovery -- a candidate is only interesting next to these.
KNOWN = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")

BUCKETS = ((0, 0.35, "under 20 min"), (0.35, 1.01, "20-60 min"),
           (1.01, 4, "1-4 h"), (4, 12, "4-12 h"), (12, 24.01, "12-24 h"))


def bucket_of(h):
    for lo, hi, name in BUCKETS:
        if lo <= h < hi:
            return name
    return None


def main():
    print("=" * 104)
    print("FAST-TURNOVER KALSHI SERIES -- what else closes quickly, and is it "
          "actually traded?")
    print("=" * 104)
    d, _e = B.get("/trade-api/v2/series")
    ser = [str(s.get("ticker") or "") for s in ((d or {}).get("series") or [])]
    ser = [s for s in ser if s]
    print(f"  {len(ser):,} series on the exchange\n")

    started = time.time()
    rows = []
    checked = skipped_few = 0
    for tk in ser:
        if time.time() - started > BUDGET:
            print(f"  [budget reached after {checked} series]\n")
            break
        try:
            mk = B.settled_markets(tk, 60)
        except Exception:
            continue
        checked += 1
        if len(mk) < MIN_SETTLED:
            skipped_few += 1
            continue

        lives = []
        for m in mk:
            o, c = B.ts(m.get("open_time")), B.ts(m.get("close_time"))
            if o is None or c is None:
                continue
            lives.append((c - o) / 3600.0)
        if not lives:
            continue
        life = st.median(lives)
        if life > MAX_LIFE_H or life <= 0:
            continue

        # Volume, from the CANDLES and by the _fp name. See the docstring.
        vols, nocand = [], 0
        for m in mk[:PER_SERIES]:
            c = B.ts(m.get("close_time"))
            if c is None:
                continue
            hrs = max(1, min(int(life) + 1, 240))
            dd, _x = B.get(
                f"/trade-api/v2/series/{tk}/markets/{m.get('ticker')}"
                "/candlesticks",
                start_ts=int(c) - hrs * 3600, end_ts=int(c),
                period_interval=60)
            cands = (dd or {}).get("candlesticks") or []
            if not cands:
                nocand += 1
                continue
            vols.append(sum((B._f(cd.get("volume_fp")) or 0.0)
                            for cd in cands))
            time.sleep(B.PAUSE)

        rows.append(dict(series=tk, life=life, n=len(mk),
                         vol=(st.median(vols) if vols else 0.0),
                         sampled=len(vols), nocand=nocand,
                         known=tk in KNOWN))

    print(f"  {checked:,} series inspected, {skipped_few:,} skipped for having "
          f"under {MIN_SETTLED} settled markets")
    print(f"  {len(rows):,} with a median life under {MAX_LIFE_H}h\n")
    if not rows:
        print("  Nothing came back. That is a result about the probe, not the")
        print("  exchange -- check the budget before concluding anything.")
        return 0

    by = collections.defaultdict(list)
    for r in rows:
        b = bucket_of(r["life"])
        if b:
            by[b].append(r)

    for _lo, _hi, name in BUCKETS:
        g = by.get(name) or []
        if not g:
            continue
        print("-" * 104)
        print(f"{name.upper()}   ({len(g)} series)")
        print("-" * 104)
        print(f"  {'series':<26} {'median life':>12} {'settled':>8} "
              f"{'median volume':>14}  note")
        for r in sorted(g, key=lambda x: -x["vol"])[:18]:
            life = r["life"]
            lab = (f"{life*60:.0f} min" if life < 1.5 else f"{life:.1f} h")
            note = "<- ALREADY TRADED" if r["known"] else ""
            if r["vol"] <= 0:
                note = ("no volume in any sampled market"
                        + (f" ({r['nocand']} returned no candles)"
                           if r["nocand"] else "")) or note
            print(f"  {r['series']:<26} {lab:>12} {r['n']:>8} "
                  f"{r['vol']:>14,.0f}  {note}")
        dead = sum(1 for r in g if r["vol"] <= 0)
        print(f"  {dead} of {len(g)} had no traded volume in any sampled "
              "market.\n")

    print("=" * 104)
    print("  HOW TO READ THIS. Compare every candidate against the KXxxx15M")
    print("  rows, which are what the bot already trades -- those are the")
    print("  known-liquid baseline. A series with a fast clock and no volume")
    print("  is not an opportunity; it is the long-dated test again, where")
    print("  14,487 markets had empty books on both sides.")
    print()
    print("  AND SPEED ALONE PROVES NOTHING. A tradeable candidate still has")
    print("  to be calibration-tested the same way the 15-minute markets")
    print("  were. This file finds places to look, not edges.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
