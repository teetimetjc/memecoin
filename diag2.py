"""Is 'high volume' a cause or a symptom, for CHEAP contracts specifically?

The first lookahead check compared volume by outcome across every bet and
found almost nothing -- 54,178 against 51,517. But most bets are not cheap,
and that average buries the question.

The hypothesis now: when a longshot COMES IN, the market trades heavily on
the way. High volume would then be a symptom of the surprise, not a
condition preceding it -- and Volume in M15H is read after settlement, so
the pocket would be selecting winners by their aftermath. Testable directly:
split volume by outcome INSIDE the cheap band.
"""
import collections
import statistics as st
import sys

import predictor as P
import combo as C


def main():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    obs = C.build(C.load(sh, "M15H"))
    v = sorted(o["vol"] for o in obs); mv = v[len(v)//2]
    oi = sorted(o["oi"] for o in obs); mo = oi[len(oi)//2]

    print("VOLUME BY OUTCOME, INSIDE EACH PRICE BAND")
    print(f"{'band':<12}{'n':>7}{'won n':>7}{'med vol WON':>14}"
          f"{'med vol LOST':>14}{'ratio':>8}{'win% hi vol':>12}{'win% lo vol':>12}")
    for lo, hi in ((0.02,0.10),(0.10,0.20),(0.20,0.40),(0.40,0.60),
                   (0.60,0.80),(0.80,0.98)):
        s = [o for o in obs if lo <= o["price"] < hi]
        w = [o for o in s if o["won"]]; l = [o for o in s if not o["won"]]
        if len(w) < 20 or len(l) < 20: continue
        mw, ml = st.median([o["vol"] for o in w]), st.median([o["vol"] for o in l])
        hiv = [o for o in s if o["vol"] > mv]
        lov = [o for o in s if o["vol"] <= mv]
        wh = 100*sum(1 for o in hiv if o["won"])/len(hiv) if hiv else float("nan")
        wl = 100*sum(1 for o in lov if o["won"])/len(lov) if lov else float("nan")
        print(f"{lo:.2f}-{hi:.2f}  {len(s):>7}{len(w):>7}{mw:>14.0f}{ml:>14.0f}"
              f"{mw/ml:>8.2f}{wh:>11.1f}%{wl:>11.1f}%")

    print("\nTHE POCKET, SPLIT THE OTHER WAY")
    pk = [o for o in obs if o["vol"] > mv and o["oi"] <= mo
          and 0.02 <= o["price"] < 0.20]
    rest = [o for o in obs if 0.02 <= o["price"] < 0.20 and o not in pk]
    for lab, s in (("pocket", pk), ("same band, not pocket", rest)):
        if not s: continue
        print(f"  {lab:<24} n={len(s):<6} paid "
              f"{100*st.mean([o['price'] for o in s]):>5.1f}%  won "
              f"{100*sum(1 for o in s if o['won'])/len(s):>5.1f}%  "
              f"med vol {st.median([o['vol'] for o in s]):>9.0f}  "
              f"med oi {st.median([o['oi'] for o in s]):>9.0f}")

    print("\nIF the ratio above is well over 1.0 in the cheap bands, 'high")
    print("volume' is the aftermath of the longshot winning, not a signal")
    print("available beforehand -- and the pocket is unfollowable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
