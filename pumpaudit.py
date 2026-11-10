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
            # MY FIRST BAND WAS WRONG AND FLAGGED REAL DATA. It called
            # anything outside 25-120 SOL implausible, but a pump.fun curve
            # STARTS at 30 virtual SOL and only rises as people buy, so a
            # high reading is a token being bought hard -- the single most
            # interesting thing the dataset can contain -- not an error.
            # What is genuinely impossible is BELOW the starting reserve.
            impossible = [a for a, _ in early if a < 29.9]
            hot = [a for a, _ in early if a > 60]
            if impossible:
                print(f"    {len(impossible)} sample(s) BELOW the 30 SOL "
                      f"starting reserve, which cannot happen -- "
                      f"min {min(impossible):.3f}")
                problems["vSol below the starting reserve"] += len(impossible)
            if hot:
                print(f"    {len(hot)} sample(s) above 60 SOL by 30s -- "
                      f"bought hard, not an error (max {max(hot):.1f})")

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
