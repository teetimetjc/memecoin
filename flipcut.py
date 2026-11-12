"""Buy at graduation, sell N minutes later. What would that have returned?

THE QUESTION, in the operator's words: can you buy a coin that just
graduated and flip it for a nice profit.

WHAT THIS READS: 'Pump Flip Tokens' (one row per graduation, with the entry
price, its lateness, liquidity and the creator joined from our own launch
log) and 'Pump Flips' (a price at each age). Both come from pumpflip, which
prices tokens through an API rather than an on-chain decode.

NO FEE IS ASSUMED, AND THAT IS THE POINT. Picking a round-trip cost would
bury the one number that decides this, so instead the GROSS return is
reported beside the BREAK-EVEN COST: the round-trip execution cost at which
the strategy returns exactly zero. Then the question becomes "can a swap be
executed for less than X", which is checkable, instead of "did my assumed
fee make it work".

THREE WAYS THIS FLATTERS THE STRATEGY, each measured rather than waved at:

  ENTRY IS NOT THE GRADUATION PRICE. It is the first price the collector
  could see, a median of 13.8 seconds after the migration event. A real buy
  is later still and pays slippage into a pool seconds old.

  SURVIVORSHIP. A token whose pool dies stops being priceable, so it has no
  late horizon and silently drops out of the late columns. That biases the
  late horizons upward, and the count of tokens missing each horizon is
  printed so the size of the bias is visible rather than hidden.

  THE MEDIAN IS REPORTED BESIDE THE MEAN. Memecoin returns are violently
  skewed; a mean carried by one 40x on a token nobody could buy size in is
  not a strategy. If the mean is positive and the median is not, the honest
  reading is a lottery, not an edge.

Read-only. Places no order, writes no row, uses no wallet.
"""
import collections
import json
import os
import statistics as st

MIN_N = int(os.environ.get("FLIPCUT_MIN_N") or 20)

# TOKENS BEFORE THIS ARE POISONED, and pooling them would hide whether the
# fix worked -- the exact mistake already made twice with the curve
# collector, where an audit that mixed pre-fix and post-fix rows could only
# ever report the old damage.
#
# Until 14:12 UTC on 2026-11-12 the entry price could be taken from the
# BONDING CURVE pair rather than the real pool, because seconds after a
# migration the pool is often not indexed yet and the curve pair was
# accepted despite carrying no liquidity. Every curve is exhausted at the
# same reserves when it graduates, so that price is identical across
# tokens, and the resulting "returns" ran to +343,863%. Those 193 token
# rows cannot be repaired.
SINCE = os.environ.get("FLIPCUT_SINCE") or "2026-11-12 14:12:00"


def client():
    import gspread
    from google.oauth2.service_account import Credentials
    raw = os.environ.get("GOOGLE_CREDENTIALS")
    if not raw:
        raise SystemExit("  no GOOGLE_CREDENTIALS")
    creds = Credentials.from_service_account_info(
        json.loads(raw),
        scopes=["https://www.googleapis.com/auth/spreadsheets"])
    sid = os.environ.get("MEME_SHEET_ID")
    if not sid:
        import predictor as P
        sid = P.SPREADSHEET_ID
    return gspread.authorize(creds).open_by_key(sid)


def f(v):
    try:
        x = float(str(v).strip())
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def grab(sh, title):
    try:
        rows = sh.worksheet(title).get_all_values()
    except Exception as e:
        raise SystemExit(f"  tab '{title}': UNREADABLE ({str(e)[:90]})")
    if len(rows) < 2:
        raise SystemExit(f"  tab '{title}': no data rows yet")
    return rows[1:], {k: i for i, k in enumerate(rows[0])}


def pct(xs):
    return 100.0 * st.mean(xs), 100.0 * st.median(xs)


def main():
    print("=" * 104)
    print("FLIP AFTER GRADUATION -- gross return, and the cost that kills it")
    print("=" * 104)
    sh = client()
    trows, th = grab(sh, "Pump Flip Tokens")
    prows, ph = grab(sh, "Pump Flips")

    def g(r, h, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    # ------------------------------------------------------------- tokens
    tok = {}
    dropped_old = 0
    late_noliq = [0]
    for r in trows:
        m = g(r, th, "Mint")
        if not m or m in tok:
            continue
        # THE TIMESTAMP CUTOFF DID NOT WORK and the evidence said so: it
        # excluded 36 tokens when roughly 195 had been written before the
        # fix, so about 157 poisoned rows leaked through and 4.108e-07 was
        # still sitting in the extremes table.
        #
        # So the filter is now the DATA ITSELF rather than a date. The fix
        # requires a pair to have liquidity above zero before it will price
        # anything, so EVERY entry it accepts carries a liquidity figure and
        # every pre-fix curve entry carries none. Requiring liquidity
        # therefore selects exactly the post-fix rows, without depending on
        # my clock, my cutoff or when a particular collector session
        # happened to restart. 165 of 333 tokens had one, which matches the
        # leak precisely.
        #
        # The timestamp is still read and reported, because a disagreement
        # between the two would mean something else is wrong again.
        stamp = str(g(r, th, "Graduated UTC")).strip()
        liq = f(g(r, th, "Entry Liquidity USD"))
        if liq is None or liq <= 0:
            dropped_old += 1
            if stamp and stamp >= SINCE:
                late_noliq[0] += 1
            continue
        tok[m] = dict(
            entry=f(g(r, th, "Entry Price SOL")),
            late=f(g(r, th, "Entry lateness s")),
            liq=f(g(r, th, "Entry Liquidity USD")),
            creator_n=f(g(r, th, "Creator launch count")),
            socials=f(g(r, th, "Socials")),
            mins=f(g(r, th, "Mins to graduate")))
    print(f"\n  FILTER: an entry must carry a liquidity figure.")
    print(f"  {dropped_old} token(s) have none and are excluded -- their")
    print(f"  entry came from the no-liquidity bonding-curve pair, which is")
    print(f"  not a number any average should contain.")
    if late_noliq[0]:
        # A token that graduated AFTER the fix yet has no liquidity would
        # mean the fix is not holding. Stated loudly rather than buried.
        print(f"\n  WARNING: {late_noliq[0]} of those graduated AFTER "
              f"{SINCE},")
        print("  which should be impossible -- the fix refuses to price a")
        print("  pair without liquidity. The collector is not behaving as")
        print("  intended and the figures below are suspect.")
    else:
        print(f"\n  none of them graduated after {SINCE}, so the fix is")
        print("  holding: every entry it has accepted carries liquidity.")
    print(f"\n  {len(tok)} graduated token(s) with an entry")
    if len(tok) < MIN_N:
        print(f"\n  FEWER THAN {MIN_N} TOKENS SINCE THE FIX, so there is")
        print("  nothing here to report yet. The collector needs more time;")
        print("  this is not a result, and the pre-fix figures are not a")
        print("  substitute for one.")
        print("=" * 104)
        return 0
    lates = [v["late"] for v in tok.values() if v["late"] is not None]
    if lates:
        lates.sort()
        print(f"  entry lateness, s: median {st.median(lates):.1f}, "
              f"90th {lates[9*len(lates)//10]:.1f}, worst {lates[-1]:.1f}")
        print("  A REAL BUY IS LATER THAN THAT and pays slippage into a pool")
        print("  seconds old, so every figure below is better than reachable.")

    # ------------------------------------------------------------- prices
    # price[mint][target] = price in SOL
    price = collections.defaultdict(dict)
    for r in prows:
        m, t, px = g(r, ph, "Mint"), f(g(r, ph, "Target s")), f(g(r, ph,
                                                                 "Price SOL"))
        if not m or t is None or not px:
            continue
        price[m][int(t)] = px

    targets = sorted({t for d in price.values() for t in d})
    if not targets:
        raise SystemExit("  no priced horizons yet")

    print(f"\n{'-' * 104}")
    print("GROSS RETURN BY HOLDING TIME  (buy at entry, sell at the horizon)")
    print("-" * 104)
    print(f"  {'hold':>7}{'tokens':>8}{'missing':>9}{'mean %':>10}"
          f"{'median %':>11}{'up %':>8}{'break-even cost':>18}"
          f"{'worst %':>10}{'best %':>10}")
    for t in targets:
        rets = []
        for m, v in tok.items():
            e = v["entry"]
            p = price.get(m, {}).get(t)
            if not e or not p:
                continue
            rets.append(p / e - 1.0)
        missing = len(tok) - len(rets)
        if len(rets) < MIN_N:
            print(f"  {t:>6}s{len(rets):>8}{missing:>9}"
                  f"{'-- too few to report':>29}")
            continue
        mean, med = pct(rets)
        up = 100.0 * sum(1 for x in rets if x > 0) / len(rets)
        # BREAK-EVEN ROUND-TRIP COST: the cost at which the mean gross
        # return nets to zero. Reported instead of assuming a fee.
        be = mean
        print(f"  {t:>6}s{len(rets):>8}{missing:>9}{mean:>+10.2f}"
              f"{med:>+11.2f}{up:>8.1f}"
              f"{(f'{be:.2f}%' if be > 0 else 'none -- loses gross'):>18}"
              f"{100*min(rets):>+10.1f}{100*max(rets):>+10.1f}")

    # ---------------------------------------------------------------
    # WHO IS PRODUCING THE ABSURD RETURNS. The first run reported a mean
    # of +3,752% and a best of +343,863% -- a 3,400x inside five minutes,
    # which is a broken entry price rather than a trade. One such row
    # moves the mean from about +11% to thousands of percent, so the mean
    # and every break-even figure derived from it were meaningless.
    #
    # Printing the extremes with their actual prices is what makes the
    # cause findable instead of guessable, which is the lesson from
    # spending a day on a decode by theory.
    diag_t = 300 if 300 in targets else targets[len(targets) // 2]
    rows = []
    for m, v in tok.items():
        e, p = v["entry"], price.get(m, {}).get(diag_t)
        if e and p:
            rows.append((p / e - 1.0, m, e, p, v["liq"]))
    rows.sort(reverse=True)
    if rows:
        print(f"\n{'-' * 104}")
        print(f"THE EXTREMES AT {diag_t}s -- where an implausible return "
              f"comes from")
        print("-" * 104)
        print(f"  {'return %':>14}{'entry SOL':>16}{'exit SOL':>16}"
              f"{'liq USD':>12}  mint")
        for r, m, e, p, liq in rows[:4] + rows[-3:]:
            print(f"  {100*r:>+14.1f}{e:>16.3e}{p:>16.3e}"
                  f"{(f'{liq:,.0f}' if liq is not None else '-'):>12}  "
                  f"{m[:44]}")
        # A PRICE THAT MOVES MORE THAN A HUNDREDFOLD IN MINUTES is not a
        # market move on a token that just graduated with real liquidity.
        # Counted, named, and excluded from the figures below rather than
        # left to poison the mean.
        wild = [x for x in rows if x[0] > 99.0 or x[0] < -0.995]
        print(f"\n  {len(wild)} token(s) moved more than 100x or lost more "
              f"than 99.5% by {diag_t}s.")
        print("  Treated as suspect data, not trades, and excluded below.")

        clean = [x[0] for x in rows if -0.995 <= x[0] <= 99.0]
        if len(clean) >= MIN_N:
            mean, med = pct(clean)
            up = 100.0 * sum(1 for x in clean if x > 0) / len(clean)
            print(f"\n  EXCLUDING THEM, at {diag_t}s over {len(clean)} "
                  f"token(s):")
            print(f"    mean {mean:+.2f}%   median {med:+.2f}%   "
                  f"up {up:.1f}%")
            print(f"    break-even round-trip cost: "
                  + (f"{mean:.2f}%" if mean > 0 else "none, it loses gross"))

    # ---------------------------------------------------------------
    # DEAD POOLS AS LOSSES, which is the other half of an honest number.
    # A token with no price at a horizon has usually stopped being
    # indexed, which for a pump.fun pool means unsellable. Dropping it
    # flatters the strategy; calling it -100% is the conservative bound.
    # Both are printed, because the truth is between them and neither
    # alone is defensible.
    print(f"\n{'-' * 104}")
    print("THE SAME HORIZONS WITH DEAD POOLS COUNTED AS A TOTAL LOSS")
    print("  survivors-only flatters it; -100% for a vanished pool is the")
    print("  conservative bound. The answer is between the two columns.")
    print("-" * 104)
    print(f"  {'hold':>7}{'survivors':>11}{'median %':>11}"
          f"{'all, dead=-100%':>18}{'median %':>11}{'up %':>8}")
    for t in targets:
        if t == 0:
            continue
        surv, allv = [], []
        for m, v in tok.items():
            e = v["entry"]
            if not e:
                continue
            p = price.get(m, {}).get(t)
            if p:
                r = p / e - 1.0
                if not (-0.995 <= r <= 99.0):
                    continue      # suspect data, excluded from both
                surv.append(r)
                allv.append(r)
            else:
                allv.append(-1.0)
        if len(surv) < MIN_N:
            continue
        _, msurv = pct(surv)
        _, mall = pct(allv)
        upall = 100.0 * sum(1 for x in allv if x > 0) / len(allv)
        print(f"  {t:>6}s{len(surv):>11}{msurv:>+11.2f}"
              f"{len(allv):>18}{mall:>+11.2f}{upall:>8.1f}")

    print(f"\n  'missing' is tokens with no price at that horizon -- mostly")
    print("  pools that died. They drop out of the later columns, which")
    print("  biases those columns UPWARD by however many they are.")
    print("  'break-even cost' is the round-trip execution cost at which the")
    print("  mean return is exactly zero. A swap costs a pool fee, a")
    print("  priority fee and slippage EACH WAY, so compare it to that.")

    # --------------------------------------------------- does anything split
    best_t = None
    for t in targets:
        n = sum(1 for m, v in tok.items()
                if v["entry"] and price.get(m, {}).get(t))
        if n >= MIN_N:
            best_t = t if best_t is None else best_t
    # Use a mid horizon for the splits: early enough that few have dropped
    # out, late enough that something has happened.
    split_t = None
    for t in targets:
        if t >= 300:
            n = sum(1 for m, v in tok.items()
                    if v["entry"] and price.get(m, {}).get(t))
            if n >= MIN_N:
                split_t = t
                break
    if split_t is None:
        split_t = best_t
    if split_t is None:
        print("\n  not enough data for any split yet")
        print("=" * 104)
        return 0

    def report_split(label, buckets):
        print(f"\n{'-' * 104}")
        print(f"{label}  (selling at {split_t}s)")
        print("-" * 104)
        print(f"  {'bucket':>22}{'tokens':>8}{'mean %':>10}{'median %':>11}"
              f"{'up %':>8}{'break-even cost':>18}")
        for name, rets in buckets:
            if len(rets) < MIN_N:
                print(f"  {name:>22}{len(rets):>8}"
                      f"{'-- too few':>29}")
                continue
            mean, med = pct(rets)
            up = 100.0 * sum(1 for x in rets if x > 0) / len(rets)
            print(f"  {name:>22}{len(rets):>8}{mean:>+10.2f}{med:>+11.2f}"
                  f"{up:>8.1f}"
                  f"{(f'{mean:.2f}%' if mean > 0 else 'loses gross'):>18}")

    def rets_for(pred):
        out = []
        for m, v in tok.items():
            e, p = v["entry"], price.get(m, {}).get(split_t)
            if not e or not p or not pred(v):
                continue
            out.append(p / e - 1.0)
        return out

    # Liquidity at entry -- the most actionable filter, since it is knowable
    # before buying and decides whether size can be traded at all.
    report_split("BY LIQUIDITY AT ENTRY", [
        ("under $10k", rets_for(lambda v: v["liq"] is not None
                                and v["liq"] < 10_000)),
        ("$10k - $25k", rets_for(lambda v: v["liq"] is not None
                                 and 10_000 <= v["liq"] < 25_000)),
        ("$25k - $50k", rets_for(lambda v: v["liq"] is not None
                                 and 25_000 <= v["liq"] < 50_000)),
        ("$50k+", rets_for(lambda v: v["liq"] is not None
                           and v["liq"] >= 50_000)),
    ])

    # Creator history, the one real signal pump.fun has produced -- but it
    # is only known for tokens whose launch we also recorded, which is a
    # small share, so the bucket sizes matter more than the rates.
    known = [v for v in tok.values() if v["creator_n"] is not None]
    print(f"\n  creator history is known for {len(known)} of {len(tok)} "
          f"token(s) ({100.0*len(known)/max(1,len(tok)):.0f}%)")
    if len(known) < MIN_N:
        print("  TOO FEW TO SPLIT ON THE CREATOR. These tokens graduated")
        print("  from launches that mostly predate our launch log, so the")
        print("  10.09%-vs-0.05% creator signal CANNOT be tested against")
        print("  flip returns yet. It needs the launch recorder to have been")
        print("  running for as long as a token takes to graduate.")
    else:
        report_split("BY CREATOR LAUNCH COUNT", [
            ("first-time creator", rets_for(
                lambda v: v["creator_n"] is not None and v["creator_n"] <= 1)),
            ("2-10 launches", rets_for(
                lambda v: v["creator_n"] is not None
                and 2 <= v["creator_n"] <= 10)),
            ("11+ launches", rets_for(
                lambda v: v["creator_n"] is not None and v["creator_n"] > 10)),
        ])

    print("\n" + "=" * 104)
    print("  READ THE MEDIAN BESIDE THE MEAN. Memecoin returns are violently")
    print("  skewed; a mean carried by one spike on a token nobody could buy")
    print("  size in is a lottery ticket, not an edge. A positive mean with a")
    print("  negative median says most trades lose and a few pay for them.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
