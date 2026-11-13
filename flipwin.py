"""What do the winners have in common -- tested on data not used to find it.

THE QUESTION: of the graduated tokens, do the profitable ones share anything
knowable BEFORE buying?

WHY THIS FILE IS BUILT THE WAY IT IS. "Look at the winners and find what
they have in common" always succeeds. With four hundred tokens and a dozen
candidate features there is guaranteed to be some bucket that looks
excellent, and reporting it would be reporting the shape of this particular
fortnight. This project has already killed two strategies that looked good
exactly that way -- the 19c longshot and market making -- and both died on
the same test applied here.

SO THE DATA IS SPLIT IN TIME, not at random:

    DISCOVERY   the earlier half. Every feature is bucketed here and the
                best bucket is chosen here. Nothing from this half is
                reported as a result.
    HOLDOUT     the later half. The bucket chosen on discovery is scored
                here, once. THIS IS THE ONLY NUMBER THAT MEANS ANYTHING.

A chronological split rather than a random one, because a random split
would let the same hour's launches land on both sides and leak.

THE COUNT OF FEATURES TRIED IS PRINTED, because the best of twelve tests is
not the same as one test. If a holdout result is not far better than the
others, it is the best of twelve and nothing more.

FEATURES ARE ENTRY-TIME ONLY. A feature measured after the moment of
purchase cannot inform the purchase, however well it predicts. So liquidity,
size, social presence, time to graduate, the creator's own buy and the flow
at entry are all allowed; anything read later is not.

Read-only. No wallet, no order, no row written.
"""
import collections
import json
import os
import statistics as st

MIN_N = int(os.environ.get("FLIPWIN_MIN_N") or 25)
HORIZON = int(os.environ.get("FLIPWIN_HORIZON") or 300)
# Suspect bounds, the same ones every other reader here uses, so no figure
# rests on a different set of rows.
LO, HI = -0.995, 99.0


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
        raise SystemExit(f"  tab '{title}': no data rows")
    return rows[1:], {k: i for i, k in enumerate(rows[0])}


def main():
    print("=" * 100)
    print("WHAT DO THE WINNERS SHARE -- chosen on one half, scored on the "
          "other")
    print("=" * 100)
    sh = client()
    trows, th = grab(sh, "Pump Flip Tokens")
    prows, ph = grab(sh, "Pump Flips")

    def g(r, h, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    # ------------------------------------------------- per-token attributes
    tok = {}
    for r in trows:
        m = g(r, th, "Mint")
        if not m or m in tok:
            continue
        liq = f(g(r, th, "Entry Liquidity USD"))
        # The liquidity requirement is what separates post-fix rows from the
        # bonding-curve entries; see flipcut for why a date cannot do it.
        if liq is None or liq <= 0:
            continue
        tok[m] = dict(
            when=str(g(r, th, "Graduated UTC")).strip(),
            entry=f(g(r, th, "Entry Price SOL")),
            late=f(g(r, th, "Entry lateness s")),
            liq=liq,
            fdv=f(g(r, th, "Entry FDV USD")),
            mcap=f(g(r, th, "Entry Market Cap USD")),
            socials=f(g(r, th, "Socials")),
            mins=f(g(r, th, "Mins to graduate")),
            buy_sol=f(g(r, th, "Creator Buy SOL")),
            pair_age=f(g(r, th, "Pair age s")))

    # ------------------------------- entry-time flow, and the exit price
    first, exit_px = {}, {}
    for r in prows:
        m = g(r, ph, "Mint")
        t = f(g(r, ph, "Target s"))
        px = f(g(r, ph, "Price SOL"))
        if not m or t is None or not px or m not in tok:
            continue
        t = int(t)
        # The earliest sample is the entry-time observation; its flow
        # columns are knowable at purchase.
        if m not in first or t < first[m]["t"]:
            first[m] = dict(
                t=t,
                vol5=f(g(r, ph, "Volume 5m")),
                chg5=f(g(r, ph, "Change 5m %")),
                buys=f(g(r, ph, "Buys 5m")),
                sells=f(g(r, ph, "Sells 5m")))
        if t == HORIZON:
            exit_px[m] = px

    rows = []
    for m, v in tok.items():
        e, p = v["entry"], exit_px.get(m)
        if not e or not p:
            continue
        r = p / e - 1.0
        if not (LO <= r <= HI):
            continue
        fl = first.get(m) or {}
        bs = None
        if fl.get("buys") is not None and fl.get("sells"):
            bs = fl["buys"] / max(1.0, fl["sells"])
        rows.append(dict(when=v["when"], ret=r, liq=v["liq"], fdv=v["fdv"],
                         mcap=v["mcap"], socials=v["socials"],
                         mins=v["mins"], late=v["late"],
                         buy_sol=v["buy_sol"], pair_age=v["pair_age"],
                         vol5=fl.get("vol5"), chg5=fl.get("chg5"),
                         buysell=bs))

    rows.sort(key=lambda x: x["when"])
    print(f"\n  {len(rows)} token(s) with a clean entry and a "
          f"{HORIZON}s exit")
    if len(rows) < MIN_N * 2:
        print(f"\n  FEWER THAN {MIN_N * 2}, so there is not enough to split")
        print("  into a discovery half and a holdout half. Nothing is")
        print("  reported: a pattern found on all of it and tested on none")
        print("  of it is not a finding.")
        print("=" * 100)
        return 0

    cut = len(rows) // 2
    disc, hold = rows[:cut], rows[cut:]
    print(f"  DISCOVERY {len(disc)} token(s)  {disc[0]['when']} -> "
          f"{disc[-1]['when']}")
    print(f"  HOLDOUT   {len(hold)} token(s)  {hold[0]['when']} -> "
          f"{hold[-1]['when']}")
    base_d = 100.0 * st.median([x["ret"] for x in disc])
    base_h = 100.0 * st.median([x["ret"] for x in hold])
    print(f"\n  baseline median return: discovery {base_d:+.2f}%, "
          f"holdout {base_h:+.2f}%")
    print("  A rule has to beat the HOLDOUT baseline to be worth anything.")

    FEATURES = [
        ("entry liquidity USD", "liq"),
        ("entry FDV USD", "fdv"),
        ("entry market cap USD", "mcap"),
        ("socials + websites", "socials"),
        ("minutes to graduate", "mins"),
        ("entry lateness s", "late"),
        ("creator's own buy SOL", "buy_sol"),
        ("pair age at entry s", "pair_age"),
        ("volume 5m at entry", "vol5"),
        ("price change 5m at entry", "chg5"),
        ("buys/sells at entry", "buysell"),
    ]

    print(f"\n{'-' * 100}")
    print("EACH FEATURE: best half chosen on DISCOVERY, then scored on "
          "HOLDOUT")
    print("-" * 100)
    print(f"  {'feature':<26}{'rule':>18}{'disc n':>8}{'disc med':>10}"
          f"{'hold n':>8}{'hold med':>10}{'hold up%':>10}")
    results = []
    tried = 0
    for label, key in FEATURES:
        dvals = [x[key] for x in disc if x[key] is not None]
        if len(dvals) < MIN_N:
            print(f"  {label:<26}{'-- too few on discovery':>18}")
            continue
        med = st.median(dvals)
        # A MEDIAN SPLIT, NOT QUARTILES. Two buckets of two hundred beats
        # four of fifty, and every extra bucket is another chance to find
        # noise. The direction is still chosen on discovery only.
        above = [x["ret"] for x in disc
                 if x[key] is not None and x[key] >= med]
        below = [x["ret"] for x in disc
                 if x[key] is not None and x[key] < med]
        if len(above) < MIN_N or len(below) < MIN_N:
            print(f"  {label:<26}{'-- lopsided split':>18}")
            continue
        tried += 1
        hi_med, lo_med = st.median(above), st.median(below)
        if hi_med >= lo_med:
            rule, dsel = f">= {med:,.4g}", above
            hsel = [x["ret"] for x in hold
                    if x[key] is not None and x[key] >= med]
        else:
            rule, dsel = f"< {med:,.4g}", below
            hsel = [x["ret"] for x in hold
                    if x[key] is not None and x[key] < med]
        if len(hsel) < MIN_N:
            print(f"  {label:<26}{rule:>18}{len(dsel):>8}"
                  f"{100*st.median(dsel):>+10.2f}"
                  f"{len(hsel):>8}{'-- too few':>10}")
            continue
        hm = 100.0 * st.median(hsel)
        hu = 100.0 * sum(1 for x in hsel if x > 0) / len(hsel)
        print(f"  {label:<26}{rule:>18}{len(dsel):>8}"
              f"{100*st.median(dsel):>+10.2f}{len(hsel):>8}{hm:>+10.2f}"
              f"{hu:>10.1f}")
        results.append((hm - base_h, label, rule, len(hsel), hm, hu))

    print(f"\n{'=' * 100}")
    print("VERDICT")
    print("=" * 100)
    if not results:
        print("\n  no feature produced a holdout bucket big enough to score.")
        print("=" * 100)
        return 0
    results.sort(reverse=True)
    edge, label, rule, n, hm, hu = results[0]
    print(f"  best on the holdout: {label} {rule}")
    print(f"    {n} token(s), median {hm:+.2f}%, up {hu:.1f}%")
    print(f"    versus a holdout baseline of {base_h:+.2f}%, so "
          f"{edge:+.2f} points")
    beat = sum(1 for r in results if r[0] > 0)
    print(f"  {beat} of {len(results)} features beat the baseline on the "
          f"holdout.")

    # ---------------------------------------------------------------
    # THE HOLDOUT ALONE IS NOT ENOUGH, and a simulation proved it before
    # this was written. Running this exact procedure -- median split,
    # direction chosen on discovery, best of eleven features -- against
    # PURE NOISE at this sample size produced a median holdout edge of
    # +16 points and a 90th percentile of +30. Memecoin returns are skewed
    # enough that the median of a hundred-token bucket wanders that far on
    # its own.
    #
    # So the real edge is compared against a null built from THIS data:
    # the returns are shuffled against the features, destroying any genuine
    # relationship while keeping the exact return distribution, and the
    # whole best-of-N search is run again. The share of shuffles that beat
    # the real result is a p-value -- the probability of seeing an edge
    # this large from a dataset with no signal in it at all.
    import random
    random.seed(0)
    trials = int(os.environ.get("FLIPWIN_PERMS") or 300)
    keys = [k for _, k in FEATURES]
    pool = [x["ret"] for x in rows]
    null = []
    for _ in range(trials):
        shuf = pool[:]
        random.shuffle(shuf)
        sr = [dict(x, ret=shuf[i]) for i, x in enumerate(rows)]
        sd, shd = sr[:cut], sr[cut:]
        sbase = st.median([x["ret"] for x in shd])
        best = None
        for k in keys:
            dv = [x[k] for x in sd if x[k] is not None]
            if len(dv) < MIN_N:
                continue
            md = st.median(dv)
            ab = [x["ret"] for x in sd if x[k] is not None and x[k] >= md]
            be = [x["ret"] for x in sd if x[k] is not None and x[k] < md]
            if len(ab) < MIN_N or len(be) < MIN_N:
                continue
            up = st.median(ab) >= st.median(be)
            hs = [x["ret"] for x in shd
                  if x[k] is not None and ((x[k] >= md) == up)]
            if len(hs) < MIN_N:
                continue
            e = st.median(hs) - sbase
            if best is None or e > best:
                best = e
        if best is not None:
            null.append(100.0 * best)
    if null:
        null.sort()
        worse = sum(1 for e in null if e >= edge)
        p = worse / len(null)
        print(f"\n  PERMUTATION TEST, {len(null)} shuffles of this data:")
        print(f"    a no-signal dataset produces a best-of-{tried} edge of")
        print(f"    {null[len(null)//2]:+.2f} points at the median, "
              f"{null[9*len(null)//10]:+.2f} at the 90th,")
        print(f"    and {null[-1]:+.2f} at the most extreme.")
        print(f"\n    the real edge is {edge:+.2f} points, beaten by "
              f"{worse} of {len(null)} shuffles")
        print(f"    p = {p:.3f}")
        if p > 0.10:
            print("\n  SO THIS IS NOTHING. An edge this size arises from")
            print("  data with no signal in it more often than one time in")
            print("  ten, and the honest reading is that none of these")
            print("  features separates the winners.")
        elif p > 0.05:
            print("\n  WEAK AT BEST. Worth watching as the sample grows,")
            print("  not worth trading.")
        else:
            print("\n  THIS SURVIVES THE NULL, which no pump.fun feature has")
            print("  yet. It still has to clear execution cost, and it")
            print("  should be re-tested on the next independent stretch")
            print("  before any money follows it.")
    print(f"\n  {tried} feature(s) were tried, so the headline is the best")
    print(f"  of {tried} -- which is exactly what the permutation test")
    print("  above accounts for.")
    print("\n  AND NONE OF THIS IS A PROFIT. A round-trip swap costs a pool")
    print("  fee, a priority fee and slippage each way, so a rule needs to")
    print("  clear roughly 1-2% before it is worth doing, on top of beating")
    print("  the baseline.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
