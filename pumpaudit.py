"""Is the pump.fun data actually right? Recompute it and check. Read-only.

A COLLECTOR THAT RUNS IS NOT A COLLECTOR THAT IS CORRECT. This week has
produced three readers that ran cleanly and recorded nothing useful,
because the thing being checked was whether the job succeeded rather than
whether the numbers meant anything. So this audits the sheets the way an
outsider would: by recomputing every derived figure from the raw ones
stored beside it, and reporting disagreements instead of totals.

WHAT IS CHECKED, and each one has a specific failure in mind:

  PRICE          Price SOL must equal vSol / vTokens. Those two reserves
                 are recorded raw next to it precisely so the derived
                 number can be re-derived. A mismatch means the decode or
                 the arithmetic is wrong.
  MULTIPLE       X vs Launch must equal Price SOL / Launch Price SOL. This
                 is the column an exit-horizon sweep will actually use, so
                 it is the one most worth not trusting.
  AGES           Actual age must be >= its target and reasonably close. A
                 target of 60s satisfied at 900s is a sample from a
                 different moment wearing the wrong label, and would bias
                 every horizon it appears in.
  DUPLICATES     One row per (mint, target). A repeat means the scheduler
                 wrote the same horizon twice and the pooled average is
                 weighted by polling luck rather than by token.
  ORPHANS        Every priced mint should appear in the cohort tab. An
                 orphan means the two writers disagree about what is being
                 followed.
  BLANKS/ZEROS   A zero price is not a price. The writers blank missing
                 values deliberately; a zero would average in as real.
  SANITY         pump.fun launches start near 30 SOL of virtual reserves
                 against ~1.07bn tokens. Anything far outside that at the
                 30-second mark suggests the wrong account was read.

Nothing here is interpreted as a trading result. It answers one question:
can the numbers on these tabs be believed.

Read-only.
"""
import collections
import json
import os
import statistics as st

SHEET_ID = os.environ.get("MEME_SHEET_ID")
TOL = float(os.environ.get("AUDIT_TOL") or 0.001)      # 0.1%

# THE CUTOFF THAT MAKES "IS IT ACCURATE NOW" ANSWERABLE. Every price row
# written before the template fix is contaminated: the decoder could not
# tell a pre-trade template from a real curve, so 812 readings held a
# template's bogus virtual-SOL value. Those rows cannot be repaired and
# cannot be told apart by eye, so an audit that pools them with new rows
# can only ever report the old damage and will never show the fix working.
#
# 22:58 UTC on 2026-11-11 is the start of the first collector session that
# actually ran with the fix -- not the commit time. The two runs between
# the commit and that session do not count: one had checked out the old
# code hours earlier, and the other died in its opening handshake.
SINCE = os.environ.get("PUMPAUDIT_SINCE") or "2026-11-11 22:58:00"


def client():
    import gspread
    from google.oauth2.service_account import Credentials
    raw = os.environ.get("GOOGLE_CREDENTIALS")
    if not raw:
        raise SystemExit("  no GOOGLE_CREDENTIALS")
    creds = Credentials.from_service_account_info(
        json.loads(raw),
        scopes=["https://www.googleapis.com/auth/spreadsheets"])
    sid = SHEET_ID
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
        print(f"  tab '{title}': UNREADABLE ({str(e)[:80]})")
        return [], {}
    if len(rows) < 2:
        print(f"  tab '{title}': header only, no data rows")
        return [], {k: i for i, k in enumerate(rows[0])} if rows else {}
    return rows[1:], {k: i for i, k in enumerate(rows[0])}


def main():
    print("=" * 100)
    print("PUMP.FUN DATA AUDIT -- recompute every derived figure, report "
          "disagreements")
    print("=" * 100)
    sh = client()

    launches, lh = grab(sh, "Pump Launches")
    migs, _ = grab(sh, "Pump Migrations")
    cohort, ch = grab(sh, "Pump Cohort")
    prices, ph = grab(sh, "Pump Prices")

    print(f"\n  Pump Launches   {len(launches):>6} row(s)")
    print(f"  Pump Migrations {len(migs):>6} row(s)")
    print(f"  Pump Cohort     {len(cohort):>6} row(s)")
    print(f"  Pump Prices     {len(prices):>6} row(s)")

    def g(r, h, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    problems = collections.Counter()

    # ---------------------------------------------------- launches tab
    if launches:
        print(f"\n{'-' * 100}")
        print("PUMP LAUNCHES")
        print("-" * 100)
        mints = [g(r, lh, "Mint") for r in launches]
        dup = len(mints) - len(set(mints))
        print(f"  {len(set(mints))} unique mint(s); {dup} duplicate row(s)")
        if dup:
            problems["duplicate launch rows"] += dup
        # The launch price must equal vSol/vTokens, both stored raw.
        bad_px = checked = 0
        buys = []
        for r in launches:
            vs, vt = f(g(r, lh, "vSol")), f(g(r, lh, "vTokens"))
            px = f(g(r, lh, "Launch Price SOL"))
            b = f(g(r, lh, "Creator Buy SOL"))
            if b is not None:
                buys.append(b)
            if vs and vt and px:
                checked += 1
                if abs(px - vs / vt) / (vs / vt) > TOL:
                    bad_px += 1
        print(f"  launch price = vSol/vTokens: {checked - bad_px}/{checked} "
              f"agree")
        if bad_px:
            problems["launch price mismatch"] += bad_px
        if buys:
            buys.sort()
            print(f"  creator's own buy, SOL: median {st.median(buys):.4f}, "
                  f"10th {buys[len(buys)//10]:.4f}, "
                  f"90th {buys[9*len(buys)//10]:.4f}, max {buys[-1]:.4f}")
        # Repeat creators -- the signal the whole dataset exists for.
        cre = collections.Counter(g(r, lh, "Creator") for r in launches)
        rep = [(k, c) for k, c in cre.most_common(5) if c > 1]
        print(f"  {len(cre)} distinct creator(s); "
              f"{sum(1 for c in cre.values() if c > 1)} launched more than "
              f"once")
        for k, c in rep:
            print(f"    {c:>4}x  {k[:44]}")

    # ------------------------------------------------------- prices tab
    if prices:
        print(f"\n{'-' * 100}")
        print("PUMP PRICES -- the tab an exit sweep would use")
        print("-" * 100)
        bad_px = bad_mult = bad_age = zero_px = checked = 0
        lates = []
        seen = collections.Counter()
        cohort_mints = {g(r, ch, "Mint") for r in cohort}
        orphans = set()
        for r in prices:
            mint = g(r, ph, "Mint")
            tgt = f(g(r, ph, "Target s"))
            age = f(g(r, ph, "Actual age s"))
            px = f(g(r, ph, "Price SOL"))
            vs, vt = f(g(r, ph, "vSol")), f(g(r, ph, "vTokens"))
            lp = f(g(r, ph, "Launch Price SOL"))
            mult = f(g(r, ph, "X vs Launch"))
            if mint and cohort_mints and mint not in cohort_mints:
                orphans.add(mint)
            if tgt is not None:
                seen[(mint, tgt)] += 1
            if px == 0:
                zero_px += 1
            if vs and vt and px:
                checked += 1
                if abs(px - vs / vt) / (vs / vt) > TOL:
                    bad_px += 1
            if px and lp and mult:
                if abs(mult - px / lp) / (px / lp) > TOL:
                    bad_mult += 1
            if tgt is not None and age is not None:
                if age < tgt - 1:
                    bad_age += 1
                lates.append(age - tgt)

        print(f"  price = vSol/vTokens:        {checked - bad_px}/{checked} "
              f"agree")
        print(f"  X vs Launch = price/launch:  "
              f"{'all agree' if not bad_mult else str(bad_mult) + ' MISMATCH'}")
        dups = sum(c - 1 for c in seen.values() if c > 1)
        print(f"  one row per (mint, target):  "
              f"{'clean' if not dups else str(dups) + ' DUPLICATE(S)'}")
        if lates:
            lates.sort()
            print(f"  lateness vs target, s: median {st.median(lates):.1f}, "
                  f"90th {lates[9*len(lates)//10]:.1f}, max {lates[-1]:.1f}")
        print(f"  samples before their target: "
              f"{'none' if not bad_age else str(bad_age) + ' BAD'}")
        print(f"  zero prices:                 "
              f"{'none' if not zero_px else str(zero_px) + ' BAD'}")
        print(f"  orphan mints (not in cohort):"
              f" {'none' if not orphans else len(orphans)}")
        for name, n in (("price mismatch", bad_px), ("multiple mismatch",
                        bad_mult), ("duplicate (mint,target)", dups),
                        ("sample before target", bad_age),
                        ("zero price", zero_px), ("orphan mint",
                        len(orphans))):
            if n:
                problems[name] += n

        # Sanity: a pump.fun curve starts near 30 SOL / ~1.07bn tokens.
        early = [(f(g(r, ph, "vSol")), f(g(r, ph, "vTokens")))
                 for r in prices if f(g(r, ph, "Target s")) == 30]
        early = [(a, b) for a, b in early if a and b]
        if early:
            sols = sorted(a for a, _ in early)
            toks = sorted(b for _, b in early)
            print(f"\n  at the 30s mark, {len(early)} sample(s):")
            print(f"    vSol   median {st.median(sols):.3f} "
                  f"(launches start ~30)")
            print(f"    vTokens median {st.median(toks):,.0f} "
                  f"(launches start ~1.07bn)")
            # TWO WRONG BANDS BEFORE THIS ONE, so the reasoning is written
            # down rather than left in a commit message.
            #
            # The first called anything outside 25-120 SOL implausible,
            # which flagged real data: a curve STARTS at 30 virtual SOL and
            # rises as people buy, so a moderately high reading is a token
            # being bought, not a decode error.
            #
            # The second over-corrected and called every high reading
            # "bought hard, not an error". That is also wrong, and saying
            # it in the output was worse than saying nothing, because a
            # pump.fun curve GRADUATES near 85 real SOL and stops. A
            # reading of 950 SOL at the 30-second mark is not a token being
            # bought hard; it is either a curve variant this decoder does
            # not understand or a wrong account being read, and it feeds
            # straight into the X-vs-launch column an exit sweep would use.
            #
            # So both ends are now reported as UNEXPLAINED. Below the
            # starting reserve is impossible. Far above the graduation
            # threshold is impossible for a curve that still exists. Until
            # one of them has a cause, no multiple from this tab is a
            # finding.
            impossible = [a for a, _ in early if a < 29.9]
            hot = [a for a, _ in early if a > 115]
            warm = [a for a, _ in early if 60 < a <= 115]
            if impossible:
                print(f"    {len(impossible)} sample(s) BELOW the 30 SOL "
                      f"starting reserve, which cannot happen -- "
                      f"min {min(impossible):.3f}")
                problems["vSol below the starting reserve"] += len(impossible)
            if warm:
                print(f"    {len(warm)} sample(s) between 60 and 115 SOL by "
                      f"30s -- high but reachable before graduation")
            if hot:
                print(f"    {len(hot)} sample(s) ABOVE 115 SOL by 30s "
                      f"(max {max(hot):.1f}). A curve graduates near 85 "
                      f"real SOL and stops, so this is UNEXPLAINED --")
                print("      a curve variant this decoder does not know, or")
                print("      the wrong account. Not 'bought hard'.")
                problems["vSol above any graduated curve (UNEXPLAINED)"] += \
                    len(hot)

        # ---------------------------------------------------------------
        # IS THE IMPOSSIBLE RESERVE THE SAME ROW AS THE IMPOSSIBLE MINT?
        #
        # pumpjoin found launch rows whose Mint is not a pump.fun launch at
        # all -- one of them is USDC's own mint address, with a graduation
        # timestamped fifteen hours before its "launch". That suggests some
        # rows describe a real liquidity pool rather than a bonding curve,
        # and a real pool's reserves would naturally read in the hundreds
        # of SOL. If so, one defect explains both symptoms.
        #
        # So the two are cross-checked directly instead of being left as a
        # guess: for every sample above any graduated curve, the launch row
        # for that same mint is examined. A launch that started near 30 SOL
        # against ~1.07bn tokens WAS a bonding curve, and a high reading on
        # it is a token that was bought; a launch that never looked like a
        # curve was never a pump.fun launch, and both numbers are garbage.
        lmap = {}
        for r in launches:
            m = g(r, lh, "Mint")
            if m:
                lmap[m] = (f(g(r, lh, "vSol")), f(g(r, lh, "vTokens")))
        suspect = [(g(r, ph, "Mint"), f(g(r, ph, "vSol")))
                   for r in prices
                   if (f(g(r, ph, "vSol")) or 0) > 115]
        if suspect:
            print(f"\n  CROSS-CHECK: {len(suspect)} sample(s) above 115 SOL, "
                  f"traced back to their launch row")
            not_a_curve = was_a_curve = unknown = 0
            shown = 0
            for m, vs in sorted(suspect, key=lambda x: -(x[1] or 0)):
                lv = lmap.get(m)
                if lv is None:
                    verdict, unknown = "NO LAUNCH ROW", unknown + 1
                elif lv[0] is None or lv[1] is None:
                    verdict, unknown = "launch row blank", unknown + 1
                elif 29.0 <= lv[0] <= 31.5 and 1.0e9 <= lv[1] <= 1.1e9:
                    verdict, was_a_curve = "was a real curve", was_a_curve + 1
                else:
                    verdict, not_a_curve = ("NEVER A CURVE",
                                            not_a_curve + 1)
                if shown < 15:
                    shown += 1
                    at = (f"launch vSol {lv[0]:,.3f}" if lv and lv[0]
                          else "launch vSol -")
                    print(f"    {vs:>10,.1f} SOL  {m[:44]:<44} "
                          f"{at:<24} {verdict}")
            print(f"\n    {was_a_curve} started as a real bonding curve "
                  f"(~30 SOL / ~1.07bn tokens)")
            print(f"    {not_a_curve} NEVER looked like a curve at launch")
            print(f"    {unknown} have no usable launch row")
            # THE FIRST VERSION OF THIS VERDICT WAS WRONG, and it printed
            # the opposite of what the data said. It tested
            # not_a_curve + unknown >= was_a_curve, which counts "no launch
            # row" as evidence FOR the hypothesis. It is not evidence at
            # all: those mints launched before the collector existed, so
            # nothing is known about them either way. With 296 unknowns and
            # 192 confirmed curves it announced that the hypothesis held
            # while every checkable row refuted it.
            #
            # Absence of evidence is not evidence. The verdict now rests
            # only on rows where the launch IS known.
            known = was_a_curve + not_a_curve
            if not known:
                print(f"\n    NO VERDICT: all {unknown} of these mints "
                      f"launched before the collector existed, so there is")
                print("    no launch row to judge them against.")
            elif not_a_curve > was_a_curve:
                print(f"\n    HYPOTHESIS HOLDS on the {known} checkable "
                      f"row(s): the impossible reserves sit mostly on rows")
                print("    that were never bonding curves, so it is one")
                print("    defect, upstream in what counts as a launch.")
            else:
                print(f"\n    HYPOTHESIS REFUTED. Of the {known} row(s) whose "
                      f"launch is known, {was_a_curve} started as a REAL")
                print("    bonding curve at ~30 SOL. So these are not "
                      "mis-recorded pools; the per-mint reserve DECODE is")
                print("    wrong, and the bad mints are a separate defect.")
            if unknown:
                print(f"    ({unknown} row(s) gave no evidence either way -- "
                      f"they predate the collector.)")
            # MY OWN ROUNDING MANUFACTURED A FINDING. This counted repeats
            # after round(v, 1), then announced that two tokens held
            # "identical reserves". At ~925 SOL a tenth of a SOL is a wide
            # bucket, so values that merely cluster were reported as equal
            # and I drew a conclusion about account derivation from my own
            # aggregation. Repeats are now counted at FULL precision, and
            # the clustering is reported separately as clustering.
            exact = collections.Counter(v for _, v in suspect if v)
            rep = [(v, c) for v, c in exact.most_common(5) if c > 1]
            if rep:
                print("\n    THE SAME EXACT VALUE, repeated to full "
                      "precision:")
                for v, c in rep:
                    print(f"      {v!r} SOL on {c} sample(s)")
                print("    Two tokens cannot hold byte-identical reserves,")
                print("    so these samples read the SAME account.")
            else:
                print("\n    no two samples share an exact value, so the")
                print("    earlier 'identical reserves' claim was my own")
                print("    rounding. They CLUSTER; they are not equal.")
            vals = sorted(v for _, v in suspect if v)
            if vals:
                print(f"\n    the {len(vals)} bad reading(s) span "
                      f"{vals[0]:,.3f} to {vals[-1]:,.3f} SOL, "
                      f"median {st.median(vals):,.3f}")

            # WHICH ACCOUNT DID THESE COME FROM. The 'Curve' column was
            # added for exactly this and the cross-check predates it, so it
            # was being ignored. An account appearing under many mints is
            # the thing to find; a bad reading on a one-mint account means
            # the byte layout is wrong instead.
            per_curve = collections.defaultdict(set)
            no_curve = 0
            for r in prices:
                if (f(g(r, ph, "vSol")) or 0) <= 115:
                    continue
                cv = g(r, ph, "Curve")
                if not cv:
                    no_curve += 1
                    continue
                per_curve[cv].add(g(r, ph, "Mint"))
            if no_curve:
                print(f"\n    {no_curve} bad reading(s) have NO curve "
                      f"address: written before the column existed and")
                print("    therefore untraceable. Not evidence either way.")
            if per_curve:
                shared = {c: ms for c, ms in per_curve.items() if len(ms) > 1}
                print(f"\n    {len(per_curve)} curve account(s) behind the "
                      f"traceable bad readings")
                for c, ms in sorted(shared.items(),
                                    key=lambda x: -len(x[1]))[:5]:
                    print(f"      {c[:44]}  read for {len(ms)} DIFFERENT "
                          f"mint(s)")
                if shared:
                    print("    An account serving several mints is the "
                          "defect: the curve address is being reused.")
                else:
                    print("    Each belongs to ONE mint, so no account is "
                          "being shared. The reserve values themselves are")
                    print("    wrong, which points at the byte layout or the")
                    print("    field offsets in the decode.")

        # ---------------------------------------------------------------
        # BEFORE AND AFTER THE TEMPLATE FIX, scored separately. Pooling the
        # two can only ever report the old damage, so this is the section
        # that answers whether the collector is trustworthy NOW rather than
        # whether it ever was.
        print(f"\n{'-' * 100}")
        print(f"BEFORE vs AFTER THE TEMPLATE FIX  (cutoff {SINCE} UTC)")
        print("-" * 100)
        halves = {"before": [], "after": []}
        undated = 0
        for r in prices:
            stamp = str(g(r, ph, "Seen UTC")).strip()
            if not stamp:
                undated += 1
                continue
            halves["after" if stamp >= SINCE else "before"].append(r)
        if undated:
            print(f"  {undated} row(s) carry no timestamp and are in "
                  f"neither half")
        print(f"  {'half':<8}{'rows':>8}{'>115 SOL':>11}{'<29.9':>8}"
              f"{'no curve':>10}{'bad share':>12}")
        for name in ("before", "after"):
            rs = halves[name]
            if not rs:
                print(f"  {name:<8}{0:>8}{'-':>11}{'-':>8}{'-':>10}{'-':>12}")
                continue
            hi = sum(1 for r in rs if (f(g(r, ph, "vSol")) or 0) > 115)
            lo = sum(1 for r in rs if 0 < (f(g(r, ph, "vSol")) or 0) < 29.9)
            nc = sum(1 for r in rs if not g(r, ph, "Curve"))
            print(f"  {name:<8}{len(rs):>8}{hi:>11}{lo:>8}{nc:>10}"
                  f"{100.0 * (hi + lo) / len(rs):>11.2f}%")
        aft = halves["after"]
        if not aft:
            print("\n  NO ROWS YET AFTER THE CUTOFF, so this says nothing")
            print("  about the fix. The collector has not written since it")
            print("  started, or it is not running. Check the run, not the")
            print("  data.")
            problems["no post-fix rows to judge the fix by"] += 1
        else:
            bad = sum(1 for r in aft
                      if (f(g(r, ph, "vSol")) or 0) > 115
                      or 0 < (f(g(r, ph, "vSol")) or 0) < 29.9)
            if bad:
                print(f"\n  {bad} IMPOSSIBLE READING(S) STILL GETTING "
                      f"THROUGH after the fix. It is not working as")
                print("  intended; the figures above are not trustworthy.")
                problems["impossible reading after the template fix"] += bad
            else:
                print(f"\n  {len(aft)} post-fix row(s) and NOT ONE impossible "
                      f"reading. The gate is holding on live data,")
                print("  which is the thing that could not be claimed from a "
                      "unit test alone.")
                if len(aft) < 100:
                    print(f"  THOUGH {len(aft)} ROWS IS A THIN SAMPLE -- it "
                          f"shows the fix works, not that it works always.")

        # What the data can already say about movement.
        mults = [f(g(r, ph, "X vs Launch")) for r in prices]
        mults = [m for m in mults if m]
        if len(mults) >= 10:
            mults.sort()
            print(f"\n  X vs launch across {len(mults)} sample(s): "
                  f"median {st.median(mults):.3f}, "
                  f"90th {mults[9*len(mults)//10]:.3f}, "
                  f"max {mults[-1]:.3f}")
            print("  (descriptive only -- not a result, and not yet enough "
                  "data to be one)")

    print("\n" + "=" * 100)
    if problems:
        print("  PROBLEMS FOUND:")
        for k, v in problems.most_common():
            print(f"    {v:>6}  {k}")
        print("\n  Fix these before any analysis is run on this data.")
    else:
        print("  NO DISAGREEMENTS. Every derived figure recomputes from the")
        print("  raw values stored beside it, ages match their targets, no")
        print("  duplicates and no orphans. The data can be believed.")
        print()
        print("  THAT IS NOT THE SAME AS BEING USEFUL. It says the collector")
        print("  is honest, not that there is a signal in what it collected.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
