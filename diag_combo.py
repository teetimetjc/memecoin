"""Why does combo.py claim +$31/bet when search15 says every band loses?

Both read M15H. Both price the same trade. They cannot both be right, and
the absurd one is the new one, so this pulls the top pocket apart and prints
the primitives: how many bets, at what price, won how often, and what the
per-bet payoff actually is. A 310% return per bet is not a finding, it is a
receipt for a mistake.
"""
import collections
import math
import statistics as st
import sys

import predictor as P
import combo as C


def main():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    M = C.load(sh, "M15H")
    obs = C.build(M)
    print(f"markets {len(M)}   bets {len(obs)}")

    # the pocket combo.py liked best
    sel = [o for o in obs if o["vol"] > 0 and 0.02 <= o["price"] < 0.20]
    v = sorted(o["vol"] for o in obs)
    mv = v[len(v)//2]
    oi = sorted(o["oi"] for o in obs)
    mo = oi[len(oi)//2]
    print(f"median volume {mv:.1f}   median open interest {mo:.1f}")

    pocket = [o for o in obs if o["vol"] > mv and o["oi"] <= mo
              and 0.02 <= o["price"] < 0.20]
    print(f"\nPOCKET 'volume high & oi low & price 0.02-0.20': n={len(pocket)}")
    if pocket:
        w = sum(1 for o in pocket if o["won"])
        print(f"  win rate      {100*w/len(pocket):.1f}%")
        print(f"  mean price    {100*st.mean([o['price'] for o in pocket]):.1f}c")
        print(f"  mean $/bet    {st.mean([o['v'] for o in pocket]):+.2f}")
        print(f"  sides         {collections.Counter(o['side'] for o in pocket)}")
        print(f"  contracts at mean price: {int(10.0/st.mean([o['price'] for o in pocket]))}")
        print("\n  first 6 bets, raw:")
        for o in pocket[:6]:
            print(f"    {o['ser']:<12} side={o['side']:<4} price={o['price']:.3f} "
                  f"won={o['won']!s:<5} $={o['v']:+9.2f} vol={o['vol']:.0f} oi={o['oi']:.0f}")

    # THE CROSS-CHECK: same band, no other condition, every offset --
    # this is what search15 computed and found to be -$2.21/bet.
    print("\n  cross-check, price 0.02-0.20 with NO other condition:")
    allband = [o for o in obs if 0.02 <= o["price"] < 0.20]
    w = sum(1 for o in allband if o["won"])
    print(f"    n={len(allband)}  win {100*w/len(allband):.1f}%  "
          f"$/bet {st.mean([o['v'] for o in allband]):+.2f}")

    # Is `won` consistent with price? A 10c contract should win ~10% of the
    # time. If the pocket wins far more, either it is a real edge or `won`
    # is wired to the wrong side somewhere.
    print("\n  calibration of the WHOLE bet set, as a wiring check:")
    for lo, hi in ((0.02,0.10),(0.10,0.20),(0.20,0.40),(0.40,0.60),
                   (0.60,0.80),(0.80,0.98)):
        s = [o for o in obs if lo <= o["price"] < hi]
        if len(s) < 50: continue
        print(f"    {lo:.2f}-{hi:.2f}  n={len(s):<6} paid "
              f"{100*st.mean([o['price'] for o in s]):>5.1f}%  won "
              f"{100*sum(1 for o in s if o['won'])/len(s):>5.1f}%  "
              f"$/bet {st.mean([o['v'] for o in s]):>+7.2f}")

    # open interest is recorded AFTER settlement in the backfill. If the
    # pocket is defined by a post-hoc quantity, it is not a rule anyone
    # could have followed.
    print("\n  is 'oi low' knowable at entry? OpenInt in M15H is read at")
    print("  backfill time, i.e. AFTER settlement. Distribution by outcome:")
    for lab, s in (("won", [o for o in obs if o["won"]]),
                   ("lost", [o for o in obs if not o["won"]])):
        print(f"    {lab:<5} median oi {st.median([o['oi'] for o in s]):>10.1f}"
              f"   median vol {st.median([o['vol'] for o in s]):>10.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
