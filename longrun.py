"""Long-dated markets: does buying mid-price and selling a multiple work?

THE QUESTION. A fifteen-minute market gives a price nine minutes to move. A
market that lives for days swings far more -- opens at 40c, drifts to 70c,
falls to 20c. The fade test died because the cheap side was 25-30%
overpriced at entry and nine minutes could not outrun that. More room and
more time is the one version of the idea that has not been tested.

THE LESSON FROM TODAY IS BUILT IN RATHER THAN BOLTED ON. The category survey
found 9-10c spreads in FX dailies and the live books turned out to be
completely EMPTY -- volume None, open interest None, nothing on either side.
A wide spread with nobody in it is not an opportunity, and a backtest that
fills orders there is describing trades that could not be placed. So this
requires REAL VOLUME on every market it counts, and reports how many were
thrown out for failing that.

WHAT IT MEASURES. For markets living longer than MIN_LIFE_H with real
volume: buy the first hour a tradeable quote appears inside the entry band,
then walk FORWARD hour by hour and sell at the first touch of each target.
Anything that never reaches target is held to settlement.

THE ENTRY BAND IS MID-PRICED ON PURPOSE. 20-60c is where there is room to
move in both directions. The cheap tail has already been measured twice and
is overpriced both times.

WHAT WOULD MAKE THIS WORK, stated so the result can be read against it: the
price would have to be WRONG at entry, not merely volatile. A 40c contract
that swings to 70c and back to 0 pays nothing unless you can tell in advance
which ones peak. Calibration -- entry price against settle rate -- is
therefore printed beside the P&L, because it is the thing that decides
whether any exit rule can help.

Read-only.
"""
import collections, statistics as st, sys, time
import backfill as B

LO, HI = 0.20, 0.60
TARGETS = (1.5, 2.0)
STAKE = 10.0
MIN_LIFE_H = 24
MIN_VOLUME = 100
MAX_SPREAD = 0.06
PER_SERIES = 12
MIN_SETTLED = 25
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 2300
SKIP = ("15m", "KXBTC", "KXETH", "KXSOL", "KXXRP", "KXDOGE")


def fee(c, p):
    return 0.07 * c * p * (1 - p)


def main():
    print("=" * 98)
    print("LONG-DATED MARKETS: buy mid-price, sell at a multiple")
    print(f"  entry band {100*LO:.0f}-{100*HI:.0f}c, life > {MIN_LIFE_H}h, "
          f"volume >= {MIN_VOLUME}, ${STAKE:.0f} a bet")
    print("=" * 98)
    d, _e = B.get("/trade-api/v2/series")
    ser = [s for s in ((d or {}).get("series") or [])
           if not any(k.lower() in str(s.get("ticker") or "").lower()
                      for k in SKIP)]
    print(f"  {len(ser):,} candidate series\n")

    started = time.time()
    rows = []
    seen = no_vol = short = 0
    for s in ser:
        if time.time() - started > BUDGET:
            break
        tk = str(s.get("ticker") or "")
        if not tk:
            continue
        try:
            mk = B.settled_markets(tk, 40)
        except Exception:
            continue
        if len(mk) < MIN_SETTLED:
            continue
        for m in mk[:PER_SERIES]:
            if time.time() - started > BUDGET:
                break
            res = str(m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue
            o, c = B.ts(m.get("open_time")), B.ts(m.get("close_time"))
            if o is None or c is None:
                continue
            life = (c - o) / 3600.0
            seen += 1
            if life < MIN_LIFE_H:
                short += 1
                continue
            vol = B._f(m.get("volume")) or 0
            if vol < MIN_VOLUME:
                no_vol += 1
                continue
            hrs = min(int(life) + 1, 240)
            dd, _x = B.get(
                f"/trade-api/v2/series/{tk}/markets/{m.get('ticker')}/candlesticks",
                start_ts=int(c) - hrs * 3600, end_ts=int(c), period_interval=60)
            path = []
            for cd in (dd or {}).get("candlesticks") or []:
                t = B._f(cd.get("end_period_ts"))

                def g(k):
                    v = cd.get(k)
                    return (B._f(v.get("close_dollars"))
                            if isinstance(v, dict) else None)

                a, b = g("yes_ask"), g("yes_bid")
                if t is None or a is None or b is None:
                    continue
                if not (0 < b <= a < 1) or (a - b) > MAX_SPREAD:
                    continue
                path.append((t, a, b))
            path.sort()
            entry = None
            for i, (t, a, b) in enumerate(path):
                if LO <= a < HI:
                    entry = (i, a)
                    break
            if entry is None:
                continue
            i0, px = entry
            n = int(STAKE / px)
            if n < 1:
                continue
            cost = n * px + fee(n, px)
            won = (res == "yes")
            out = {}
            for tgt in TARGETS:
                want = px * tgt
                sold = None
                for (_t, _a, b) in path[i0 + 1:]:
                    if b >= want:
                        sold = b
                        break
                out[tgt] = ((n * sold - fee(n, sold)) - cost if sold is not None
                            else (n * 1.0 if won else 0.0) - cost)
            rows.append(dict(px=px, n=n, cost=cost, won=won, out=out, vol=vol,
                             life=life, series=tk,
                             hold=(n * 1.0 if won else 0.0) - cost))
            time.sleep(B.PAUSE)

    print(f"  {seen:,} settled markets examined")
    print(f"  {short:,} dropped for living under {MIN_LIFE_H}h")
    print(f"  {no_vol:,} dropped for volume under {MIN_VOLUME}  "
          "<- the empty-book filter")
    print(f"  {len(rows)} usable entries\n")
    if len(rows) < 40:
        print("  TOO FEW TO READ. If most of the drops are the volume filter,")
        print("  that is itself the finding: these markets are not traded.")
        return 0

    risked = sum(r["cost"] for r in rows)
    px = 100 * st.mean([r["px"] for r in rows])
    w = 100 * sum(1 for r in rows if r["won"]) / len(rows)
    print(f"  {len(rows)} bets, ${risked:,.2f} staked, mean entry {px:.1f}c, "
          f"settled YES {w:.1f}%")
    print(f"  CALIBRATION GAP {w-px:+.1f} points   "
          "<- near zero means no exit rule can help")
    print(f"  median life {st.median([r['life'] for r in rows]):.0f}h, "
          f"median volume {st.median([r['vol'] for r in rows]):,.0f}\n")

    def rep(lab, vals, hits=None):
        tot = sum(vals)
        extra = (f"   hit {hits}/{len(rows)} ({100*hits/len(rows):.0f}%)"
                 if hits is not None else "")
        print(f"  {lab:<24} net ${tot:>+9.2f}  ${tot/len(vals):>+7.3f}/bet  "
              f"ROI {100*tot/risked:>+7.2f}%{extra}")

    rep("HOLD to settlement", [r["hold"] for r in rows])
    for tgt in TARGETS:
        hits = sum(1 for r in rows if r["out"][tgt] != r["hold"])
        rep(f"SELL at {tgt:g}x", [r["out"][tgt] for r in rows], hits)

    print("\n" + "=" * 98)
    print("  READ THE CALIBRATION GAP FIRST. If entry price matches the settle")
    print("  rate, the market was right when you bought and no exit rule")
    print("  rescues that -- selling early just changes which losses you take.")
    print("  Volatility is not edge; a price has to be WRONG, not merely busy.")
    print("=" * 98)
    return 0


if __name__ == "__main__":
    sys.exit(main())
