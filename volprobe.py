"""Is `volume` real on settled long-dated markets, or is the field missing?

WHY THIS EXISTS. longrun.py dropped 14,487 of 14,487 surviving markets for
"volume under 100" and left zero usable entries. Two very different things
produce that number: these markets genuinely are not traded, or the settled
markets endpoint does not return a volume field and `None or 0` silently
scores every one of them as zero. That is exactly the bug class that has
bitten this project ~40 times -- a number treated as a measurement without
checking it is one -- so it gets checked before the finding is believed.

It prints, for long-lived settled markets: how many carry no volume key at
all, how many carry None, and the distribution of the ones that are numbers.
Read-only, small budget.
"""
import statistics as st, sys, time
import backfill as B

MIN_LIFE_H = 24
PER_SERIES = 12
MIN_SETTLED = 25
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 420
SKIP = ("15m", "KXBTC", "KXETH", "KXSOL", "KXXRP", "KXDOGE")


def main():
    print("=" * 86)
    print("VOLUME FIELD PROBE on settled markets living > %dh" % MIN_LIFE_H)
    print("=" * 86)
    d, _e = B.get("/trade-api/v2/series")
    ser = [s for s in ((d or {}).get("series") or [])
           if not any(k.lower() in str(s.get("ticker") or "").lower()
                      for k in SKIP)]
    print(f"  {len(ser):,} candidate series\n")

    started = time.time()
    missing = none_val = 0
    vols = []
    oi_missing = 0
    ois = []
    examples = []
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
            o, c = B.ts(m.get("open_time")), B.ts(m.get("close_time"))
            if o is None or c is None:
                continue
            if (c - o) / 3600.0 < MIN_LIFE_H:
                continue
            if "volume" not in m:
                missing += 1
            elif m.get("volume") is None:
                none_val += 1
            else:
                v = B._f(m.get("volume"))
                vols.append(0.0 if v is None else v)
                if len(examples) < 8:
                    examples.append((m.get("ticker"), v,
                                     m.get("open_interest")))
            if "open_interest" not in m or m.get("open_interest") is None:
                oi_missing += 1
            else:
                oi = B._f(m.get("open_interest"))
                ois.append(0.0 if oi is None else oi)

    tot = missing + none_val + len(vols)
    print(f"  {tot:,} long-lived settled markets inspected")
    print(f"  {missing:,} had NO volume key at all")
    print(f"  {none_val:,} had volume = None")
    print(f"  {len(vols):,} had a numeric volume\n")
    if vols:
        vols.sort()

        def q(f):
            return vols[min(len(vols) - 1, int(f * len(vols)))]
        print(f"  numeric volume: median {st.median(vols):,.0f}  "
              f"p25 {q(.25):,.0f}  p75 {q(.75):,.0f}  p90 {q(.90):,.0f}  "
              f"max {vols[-1]:,.0f}")
        for thr in (1, 10, 100, 1000):
            n = sum(1 for v in vols if v >= thr)
            print(f"    >= {thr:>5}: {n:,} ({100.0*n/len(vols):.1f}%)")
    print()
    if ois:
        print(f"  open_interest: {len(ois):,} numeric, "
              f"median {st.median(ois):,.0f}, {oi_missing:,} missing/None")
    print()
    for t, v, oi in examples:
        print(f"    {t}  volume={v}  open_interest={oi}")
    print()
    print("  READ IT THIS WAY. Mostly missing/None -> the longrun result was a")
    print("  measurement artefact and the test has to be rerun on a field that")
    print("  exists. Mostly numeric and small -> these markets really are dead")
    print("  and no exit rule can be traded in them.")


if __name__ == "__main__":
    main()
