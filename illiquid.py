"""Where are Kalshi's spreads wide enough to be worth quoting, and are those
markets priced correctly?

TWO QUESTIONS, ONE MEASUREMENT. They share a dataset because they share an
answer: both are about markets nobody is paying attention to.

  CAN THE SPREAD BE EARNED? Posting both sides earns the spread and pays the
  fee twice. The fee is now VERIFIED against five real charges from
  2026-10-12, matching 0.07*C*P*(1-P) to within a hundredth of a cent, so
  the break-even is exact rather than assumed:

      at 50c  round trip 3.50c      at 90c  round trip 1.26c
      at 70c  round trip 2.94c      at 95c  round trip 0.67c

  The 15-minute crypto markets run a median spread of 1.00c, which is under
  the fee at every price. Quoting them loses money on arithmetic alone. The
  question is whether anything on this exchange is wider.

  ARE THEY MISPRICED? The favourite trade measured zero across ~12,000 bets
  on crypto and sports, which are the most heavily traded things here.
  Thinly traded markets are where prices are worst, and they have never been
  looked at.

WHAT A USEFUL RESULT LOOKS LIKE. For quoting: median spread comfortably
above the round-trip fee, on a series with enough markets to be worth
automating. For mispricing: a calibration gap -- price versus outcome --
that is large and consistent, not one series out of forty.

THE TRAP TO AVOID, and it has already caught this survey once today. A wide
quote is not an opportunity if it is wide because nobody is there: an order
posted into an empty book never fills, so the spread is theoretical. Volume
is reported next to spread for exactly that reason, and a wide spread on a
market with no volume should be read as absence, not opportunity.

Read-only.
"""
import collections, statistics as st, sys, time
import backfill as B

PER_SERIES = 8
MIN_SETTLED = 25
HOURS_BACK = 72
PERIOD = 60                      # hourly candles; these markets live for days
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 2200
SKIP_WORDS = ("15m", "crypto", "btc", "eth", "sol", "xrp", "doge")


def fee_rt(p):
    return 2 * 0.07 * p * (1 - p)


def quotes(series, ticker, close):
    """[(ask, bid)] over the active window, wide ones included on purpose."""
    end = B.ts(close)
    if end is None:
        return []
    d, _e = B.get(f"/trade-api/v2/series/{series}/markets/{ticker}/candlesticks",
                  start_ts=int(end) - HOURS_BACK * 3600, end_ts=int(end),
                  period_interval=PERIOD)
    out = []
    for c in (d or {}).get("candlesticks") or []:
        va, vb = c.get("yes_ask"), c.get("yes_bid")
        a = B._f(va.get("close_dollars")) if isinstance(va, dict) else None
        b = B._f(vb.get("close_dollars")) if isinstance(vb, dict) else None
        if a is None or b is None or not (0 < b <= a < 1):
            continue
        out.append((a, b))
    return out


def main():
    print("=" * 104)
    print("WHERE ARE THE SPREADS WIDE, AND ARE THOSE MARKETS PRICED RIGHT?")
    print("  fee verified 2026-10-12: round trip is 3.50c at 50c, "
          "1.26c at 90c, 0.67c at 95c")
    print("=" * 104)
    d, _e = B.get("/trade-api/v2/series")
    allser = (d or {}).get("series") or []
    bycat = collections.defaultdict(list)
    for s in allser:
        bycat[str(s.get("category") or "(none)")].append(s)

    started = time.time()
    rows = []
    for cat, ser in sorted(bycat.items(), key=lambda kv: -len(kv[1])):
        if time.time() - started > BUDGET:
            break
        if cat.lower().startswith("crypto"):
            continue
        picked = 0
        spreads, prices, cal = [], [], []
        for s in ser:
            if picked >= 4 or time.time() - started > BUDGET:
                break
            tk = str(s.get("ticker") or "")
            if not tk or any(w in tk.lower() for w in SKIP_WORDS):
                continue
            try:
                mk = B.settled_markets(tk, 60)
            except Exception:
                continue
            if len(mk) < MIN_SETTLED:
                continue
            picked += 1
            for m in mk[:PER_SERIES]:
                if time.time() - started > BUDGET:
                    break
                res = str(m.get("result") or "").lower()
                if res not in ("yes", "no"):
                    continue
                q = quotes(tk, str(m.get("ticker")), m.get("close_time"))
                if not q:
                    continue
                spreads.extend(100 * (a - b) for a, b in q)
                mid = [(a + b) / 2 for a, b in q]
                prices.extend(100 * x for x in mid)
                # Calibration uses the EARLIEST quote in the window, so the
                # reading is not taken moments before resolution when the
                # answer is already obvious.
                cal.append((mid[0], res == "yes"))
                time.sleep(B.PAUSE)
        if spreads and len(cal) >= 5:
            rows.append(dict(cat=cat[:26], n=len(cal), q=len(spreads),
                             sp=st.median(spreads), px=st.median(prices),
                             cal=cal))

    print(f"\n  {'category':<28} {'mkts':>5} {'quotes':>7} {'spread':>8} "
          f"{'price':>7} {'fee r/t':>8} {'sp-fee':>8} {'calib gap':>10}")
    for r in sorted(rows, key=lambda x: -(x["sp"] - 100 * fee_rt(x["px"] / 100))):
        f = 100 * fee_rt(r["px"] / 100)
        px = 100 * sum(p for p, _ in r["cal"]) / len(r["cal"])
        w = 100 * sum(1 for _, y in r["cal"] if y) / len(r["cal"])
        print(f"  {r['cat']:<28} {r['n']:>5} {r['q']:>7} {r['sp']:>7.1f}c "
              f"{r['px']:>6.1f}c {f:>7.2f}c {r['sp']-f:>+7.1f}c "
              f"{w-px:>+9.1f}")

    print("\n" + "=" * 104)
    print("  sp-fee is the per-contract margin from quoting BOTH sides and")
    print("  getting both filled. Positive is necessary and nowhere near")
    print("  sufficient: an order in an empty book never fills, so read it")
    print("  beside the quote count, and treat a wide spread with few quotes")
    print("  as nobody being there rather than as money lying around.")
    print()
    print("  calib gap is outcome minus price at the START of the window.")
    print("  Near zero means the market is right and there is nothing to")
    print("  take. That is what crypto and sports both returned.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    sys.exit(main())
