"""A wide, disciplined sweep for anything that made money -- with the holdout built in.

The request is for something niche but not a random outlier. Those two are
separated by one thing only: a candidate found in one stretch of time has to
be scored on a DIFFERENT stretch it could not have been fitted to. So this
splits M15H by time, hunts in the early 70%, and reports every candidate's
result on the late 30% beside it. The discovery column is allowed to look
good. Only the holdout column counts.

WHY THIS IS NOT JUST ANOTHER GRID SEARCH. Four of this project's dead
strategies were found by grids like this and died later:

  A 1,188-rule grid whose best rule beat a shuffle and then reverted.
  A combo pocket at +$23/bet that passed a t-test, a both-ways day check, a
  shuffle AND a holdout -- and was LOOKAHEAD, because Volume and OpenInt in
  M15H are written after settlement. Those two fields are therefore not read
  here, at all, for any purpose.
  A Bollinger rule at 97.2% that was a one-minute clock error.
  A fallen favourite at +3.71pp on discovery, -$1.29/bet on 2,355 held-out
  bets, with the spec frozen in advance.

So the rules of this sweep are fixed before it runs:

  CELLS ARE DECLARED, NOT MINED. Series, side, entry offset, price band and
  hour of day -- quantities knowable at entry, from the quote path only.

  A CELL NEEDS 300 DISCOVERY BETS AND 150 HOLDOUT BETS. Below that it is not
  a finding, it is a sample size.

  EVERY NUMBER IS MONEY, with the verified fee 0.07*C*P*(1-P) unrounded, at
  a flat $10, paying the ask.

  ERRORS CLUSTER ON THE CLOSE TIME, because five series closing in the same
  minute share a move.

  THE WINNER MUST SURVIVE deleting the two best close-times AND the two
  worst -- the first catches a lucky streak, the second catches pennies in
  front of a steamroller.

  HOW MANY CELLS WERE SEARCHED IS PRINTED, so a single good-looking row can
  be read against the number of chances it had.

Read-only.
"""

import collections
import math
import random
import statistics as st
import sys

import predictor as P

TAB = "M15H"
OFFSETS = (14, 12, 9, 6, 3, 1)
STAKE = 10.0
SPLIT = 0.70              # first 70% of close times = discovery
MIN_DISC = 300
MIN_HOLD = 150
BANDS = ((0.02, 0.10), (0.10, 0.25), (0.25, 0.45),
         (0.45, 0.60), (0.60, 0.80), (0.80, 0.96))
HOURS = ((0, 6), (6, 12), (12, 18), (18, 24))    # CT
NPERM = 300


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def ts(s):
    import calendar
    import time
    try:
        return calendar.timegm(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def fee(c, p):
    return 0.07 * c * p * (1 - p)


def cse(groups, n):
    G = len(groups)
    if G < 2 or not n:
        return None
    m = sum(sum(v) for v in groups.values()) / n
    ss = sum((sum(v) - len(v) * m) ** 2 for v in groups.values())
    return math.sqrt((G / (G - 1.0)) * ss / (n * n))


def load():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(TAB).get_all_values()
    h = {k: i for i, k in enumerate(rows[0])}
    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue

        def c(k):
            i = h.get(k)
            return r[i] if i is not None and i < len(r) else ""

        res = str(c("Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        t = ts(c("Close Time"))
        if t is None:
            continue
        path = {}
        for o in OFFSETS:
            b, a = _f(c(f"bid{o}")), _f(c(f"ask{o}"))
            if b is not None and a is not None and 0 < b <= a < 1:
                path[o] = (b, a)
        if not path:
            continue
        # NOTE: Volume and OpenInt are deliberately NOT read. In M15H both
        # are written at backfill time, after settlement.
        out.append(dict(ser=str(c("Series")), t=t, ct=str(c("Close Time")),
                        yes=(res == "yes"), path=path,
                        hour=int(((t // 3600) - 5) % 24)))
    print(f"  {len(out)} settled markets with a usable quote path")
    return out


def bets_for(M, side, off, band):
    """Every $10 bet this (side, offset, band) would have taken."""
    lo, hi = band
    out = []
    for m in M:
        q = m["path"].get(off)
        if not q:
            continue
        b, a = q
        px = a if side == "YES" else (1.0 - b)
        if px is None or not (lo <= px < hi):
            continue
        c = int(STAKE / px)
        if c < 1:
            continue
        won = m["yes"] if side == "YES" else (not m["yes"])
        v = (c if won else 0) - (c * px + fee(c, px))
        out.append((v, m["ct"], m["ser"], m["hour"], px, won))
    return out


def score(bets):
    n = len(bets)
    if not n:
        return None
    g = collections.defaultdict(list)
    for v, ct, _, _, _, _ in bets:
        g[ct].append(v)
    m = sum(b[0] for b in bets) / n
    se = cse(g, n)
    return dict(n=n, mean=m, se=se, t=((m / se) if se else 0.0),
                win=sum(1 for b in bets if b[5]) / n,
                px=st.mean([b[4] for b in bets]), groups=g)


def robust(bets):
    """(without two best close-times, without two worst)."""
    d = collections.defaultdict(float)
    for v, ct, _, _, _, _ in bets:
        d[ct] += v
    by = sorted(d.values(), reverse=True)
    if len(by) < 5:
        return 0.0, 0.0
    return sum(by[2:]), sum(by[:-2])


def main():
    M = load()
    if not M:
        return 1
    times = sorted({m["t"] for m in M})
    cut = times[int(SPLIT * len(times))]
    disc = [m for m in M if m["t"] < cut]
    hold = [m for m in M if m["t"] >= cut]
    import time as _t
    print(f"  discovery: {len(disc)} markets up to "
          f"{_t.strftime('%Y-%m-%d', _t.gmtime(cut))}")
    print(f"  holdout:   {len(hold)} markets after that\n")

    sers = sorted({m["ser"] for m in M})
    cells = []
    for side in ("YES", "NO"):
        for off in OFFSETS:
            for band in BANDS:
                cells.append(("ALL", side, off, band, None))
                for hr in HOURS:
                    cells.append(("ALL", side, off, band, hr))
                for s in sers:
                    cells.append((s, side, off, band, None))
    print(f"searching {len(cells)} declared cells "
          f"(series x side x offset x price band x hour)\n")

    def sel(bets, ser, hr):
        out = bets
        if ser != "ALL":
            out = [b for b in out if b[2] == ser]
        if hr is not None:
            out = [b for b in out if hr[0] <= b[3] < hr[1]]
        return out

    cache_d, cache_h = {}, {}
    found = []
    for (ser, side, off, band, hr) in cells:
        k = (side, off, band)
        if k not in cache_d:
            cache_d[k] = bets_for(disc, side, off, band)
            cache_h[k] = bets_for(hold, side, off, band)
        bd = sel(cache_d[k], ser, hr)
        if len(bd) < MIN_DISC:
            continue
        sd = score(bd)
        if sd is None or sd["mean"] <= 0:
            continue
        bh = sel(cache_h[k], ser, hr)
        if len(bh) < MIN_HOLD:
            continue
        sh_ = score(bh)
        found.append((sd, sh_, bd, bh, (ser, side, off, band, hr)))

    print(f"cells positive in DISCOVERY with enough data both sides: "
          f"{len(found)}\n")
    if not found:
        print("Nothing was positive in discovery with a scoreable holdout.")
        print("That is the answer: no niche in this data made money.")
        return 0

    found.sort(key=lambda x: -x[0]["mean"])
    hdr = (f"  {'cell':<46} {'disc $/bet':>11} {'disc t':>7} "
           f"{'HOLDOUT $/bet':>14} {'hold t':>7} {'hold n':>7}")
    print(hdr)
    print("  " + "-" * 96)
    survivors = []
    for sd, sh_, bd, bh, (ser, side, off, band, hr) in found[:25]:
        lab = (f"{ser}/{side}/T-{off}/{100*band[0]:.0f}-{100*band[1]:.0f}c"
               + (f"/{hr[0]}-{hr[1]}h" if hr else ""))
        mark = ""
        if sh_["mean"] > 0 and sh_["t"] > 2.0:
            b2, w2 = robust(bh)
            if b2 > 0 and w2 > 0:
                mark = "  <== SURVIVES"
                survivors.append((sd, sh_, bd, bh,
                                  (ser, side, off, band, hr), lab))
            else:
                mark = "  (holdout positive, fails robustness)"
        elif sh_["mean"] > 0:
            mark = "  (holdout positive, t<2)"
        print(f"  {lab:<46} {sd['mean']:>+11.3f} {sd['t']:>+7.1f} "
              f"{sh_['mean']:>+14.3f} {sh_['t']:>+7.1f} {sh_['n']:>7}{mark}")

    print("\n" + "=" * 104)
    if not survivors:
        print("NOTHING SURVIVED. Cells looked positive in discovery and did")
        print("not hold up on data they were not chosen from -- which is what")
        print(f"{len(cells)} chances at a coin flip produces. No niche here")
        print("made money.")
        print("=" * 104)
        return 0

    print(f"{len(survivors)} CELL(S) SURVIVED THE HOLDOUT")
    print("=" * 104)
    for sd, sh_, bd, bh, key, lab in survivors:
        print(f"\n  {lab}")
        print(f"    discovery  n={sd['n']:<6} ${sd['mean']:+.3f}/bet  "
              f"won {100*sd['win']:.1f}% at {100*sd['px']:.1f}c  "
              f"t={sd['t']:+.1f}")
        print(f"    HOLDOUT    n={sh_['n']:<6} ${sh_['mean']:+.3f}/bet  "
              f"won {100*sh_['win']:.1f}% at {100*sh_['px']:.1f}c  "
              f"t={sh_['t']:+.1f}")
        b2, w2 = robust(bh)
        print(f"    holdout without two best close-times  ${b2:+.2f} total")
        print(f"    holdout without two worst close-times ${w2:+.2f} total")
        # A shuffle on the HOLDOUT, to see what its own windows produce.
        vals = [b[0] for b in bh]
        g = collections.defaultdict(list)
        for i, b in enumerate(bh):
            g[b[1]].append(i)
        sizes = collections.defaultdict(list)
        for ct, ix in g.items():
            sizes[len(ix)].append(ix)
        rnd = random.Random(31)
        nulls = []
        for _ in range(NPERM):
            sv = [0.0] * len(vals)
            for size, gl in sizes.items():
                order = list(range(len(gl)))
                rnd.shuffle(order)
                for a_, b_ in enumerate(order):
                    src, dst = gl[b_], gl[a_]
                    for z in range(size):
                        sv[dst[z]] = vals[src[z]]
            nulls.append(sum(sv) / len(sv))
        nulls.sort()
        beat = sum(1 for x in nulls if x >= sh_["mean"])
        print(f"    holdout shuffle: median ${st.median(nulls):+.3f}  "
              f"90th ${nulls[int(0.9*NPERM)]:+.3f}  "
              f"p={(beat+1)/(NPERM+1):.3f}")

    print("\n" + "=" * 104)
    print(f"{len(cells)} cells were searched, so roughly "
          f"{len(cells)*0.025:.0f} would clear a 2-sigma bar by luck in")
    print("discovery alone. Surviving the holdout is much harder than that,")
    print("but it is still not a forward test: the holdout sat on disk while")
    print("the cells were chosen. Anything above earns a FROZEN SPEC and")
    print("forward data, which is exactly what the fallen favourite got")
    print("before it failed at -$1.29/bet.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    sys.exit(main())
