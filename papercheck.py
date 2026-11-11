"""Is the Kalshi paper test actually collecting, and what does it say so far?

WHY THIS EXISTS. favlive was switched to dry mode after two live losses. A
dry session places nothing, so the only record it leaves is the 'Paper Bets'
tab, and the run history was showing a long sequence of `cancelled` runs --
which is what a session being superseded looks like, but is also what a
collector that writes nothing looks like. Those two cannot be told apart
from the run list, and the in-progress run's log is not served by GitHub
until the job ends, so the tab itself has to be read.

WHAT IS CHECKED, in order of what would actually be wrong:

  ROWS          How many paper rows exist, and the first and last timestamp.
                A tab that stopped growing is the failure this file is for.
  PER DAY       Rows per UTC day, printed rather than averaged, because an
                average across an outage is a statement about the outage.
  GRADED        Each row's ticker looked up on Kalshi for its SETTLEMENT
                result, not a spot-price proxy. A row whose market has not
                settled yet is counted as pending, never as a loss.
  ROI           Paper ROI on the graded rows, with the fee the order WOULD
                have paid charged against it.

WHAT A PAPER NUMBER IS WORTH, repeated here so it is not lost: the entry
price is the QUOTE the selector saw, not a fill. Nothing was sent, so there
is no fill to read. The live record puts the decision-to-order gap at about
-0.75c to +1.61c, which was most of T-3's measured edge. So paper ROI is
systematically BETTER than trading would have been. Read it as "did the
favourite hold", never as "this is what we would have made".

AND IT IS NOT THE PRE-REGISTERED TEST. The MAX_DROP_CENTS 3 test was
pre-registered on 150 SETTLED LIVE bets at $5. Paper rows do not count
toward it. While favlive runs dry, that test is paused, not progressing.

Read-only. Places no order, writes no row, changes no setting.
"""
import collections
import json
import os
import statistics as st
import time

PAPER_TAB = os.environ.get("PAPER_TAB") or "Paper Bets"
FEE_RATE = 0.07          # Kalshi: 0.07 * n * p * (1-p), rounded up per order
GRADE_MAX = int(os.environ.get("PAPER_GRADE_MAX") or 1200)


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
        x = float(str(v).replace("$", "").replace("¢", "").strip())
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def series_of(ticker):
    """The series a Kalshi ticker belongs to, e.g. KXBTC15M.

    A 15-minute crypto ticker looks like KXBTC15M-26NOV11H04-T104999.99, so
    the series is everything before the first hyphen. Anything that does not
    look like that is returned whole rather than silently truncated.
    """
    t = (ticker or "").strip().upper()
    return t.split("-")[0] if "-" in t else (t or "?")


def wilson(won, n):
    """95% Wilson score interval on a hit rate, in percent.

    A NORMAL APPROXIMATION IS WRONG HERE. These rates sit near 90% with n
    sometimes under 20, where the symmetric interval runs past 100% and
    understates how little a single day says.
    """
    if not n:
        return 0.0, 0.0
    z = 1.959964
    p = won / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    hw = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return 100.0 * max(0.0, c - hw), 100.0 * min(1.0, c + hw)


def settlement(tickers):
    """Kalshi's own settlement result per ticker, or None if unsettled.

    THE SETTLEMENT RESULT IS THE ONLY ACCEPTABLE GRADER. Re-deriving an
    outcome from a spot price read later would regrade the market against a
    different source than the one that paid, and this project has already
    been bitten by exactly that.

    GRADED IN BATCHES, because one request per ticker is not viable. The
    first version of this file fetched /markets/{ticker} 527 times at ~0.22s
    each and was still going after a minute; it got about 250 through before
    the job ended, so the whole grading half produced nothing. GET /markets
    takes a comma-separated `tickers` list, which turns that into six
    requests. If the list parameter is not honoured -- which would show as a
    response full of markets nobody asked about -- that is detected and said
    out loud rather than quietly mis-joined.
    """
    import requests
    import predictor as P
    want = sorted(tickers)[:GRADE_MAX]
    out = {}
    CHUNK = 100
    for i in range(0, len(want), CHUNK):
        batch = want[i:i + CHUNK]
        qs = "tickers=" + ",".join(batch) + f"&limit={CHUNK}"
        # Kalshi signs the PATH ONLY, without the query string. Signing the
        # full path with the query is what produces a 401 that looks like a
        # bad key.
        h = P._kalshi_headers("GET", "/trade-api/v2/markets")
        if not h:
            print("  could not sign a markets request; grading skipped")
            return {}
        try:
            r = requests.get(P.KALSHI_BASE + "/markets?" + qs,
                             headers=h, timeout=30)
        except Exception as e:
            print(f"  batch at {i}: request failed ({str(e)[:70]})")
            continue
        if not r.ok:
            print(f"  batch at {i}: HTTP {r.status_code} {r.text[:120]}")
            continue
        ms = (r.json() or {}).get("markets") or []
        asked = set(batch)
        got = {m.get("ticker") for m in ms}
        extra = got - asked
        if extra:
            print(f"  THE tickers FILTER WAS IGNORED: {len(extra)} market(s) "
                  f"came back that were not asked for. Grading is not "
                  f"trustworthy this way; stopping.")
            return {}
        for m in ms:
            res = (m.get("result") or "").strip().lower()
            # An empty result means NOT SETTLED. Never read it as a loss.
            out[m.get("ticker")] = res or None
        print(f"  ... {len(out)}/{len(want)} looked up")
        time.sleep(0.2)
    missing = [t for t in want if t not in out]
    if missing:
        print(f"  {len(missing)} ticker(s) were not returned by Kalshi at "
              f"all; left ungraded rather than assumed")
    return out


def main():
    print("=" * 96)
    print("THE KALSHI PAPER TEST -- is it collecting, and what does it say")
    print("=" * 96)
    sh = client()
    try:
        rows = sh.worksheet(PAPER_TAB).get_all_values()
    except Exception as e:
        print(f"\n  tab '{PAPER_TAB}': UNREADABLE ({str(e)[:100]})")
        print("  If the tab does not exist, no dry session has ever written")
        print("  a paper row and the whole dry period collected nothing.")
        return 1
    if len(rows) < 2:
        print(f"\n  tab '{PAPER_TAB}' holds a header and NO DATA ROWS.")
        print("  The dry sessions are not writing. That is the bug.")
        return 1

    h = {k: i for i, k in enumerate(rows[0])}
    data = [r for r in rows[1:] if r and r[0].strip()]

    def g(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    print(f"\n  {len(data)} paper row(s)")
    ts = sorted(str(g(r, "Timestamp")) for r in data)
    print(f"  first {ts[0]}")
    print(f"  last  {ts[-1]}")

    # ------------------------------------------------------------ per day
    per = collections.Counter(t[:10] for t in ts)
    print(f"\n{'-' * 96}")
    print("ROWS PER UTC DAY -- printed, not averaged")
    print("-" * 96)
    for d in sorted(per):
        print(f"  {d}   {per[d]:>5}")
    recent = sorted(per)[-1]
    print(f"\n  the most recent day with any row is {recent}")

    # ----------------------------------------------------------- grading
    tickers = sorted({g(r, "Ticker") for r in data if g(r, "Ticker")})
    print(f"\n{'-' * 96}")
    print(f"GRADING {len(tickers)} distinct ticker(s) from Kalshi settlement")
    print("-" * 96)
    res = settlement(tickers)
    if not res:
        print("  nothing could be graded; the counts above still stand.")
        print("=" * 96)
        return 0

    settled = {k: v for k, v in res.items() if v}
    print(f"  {len(settled)} settled, {len(res) - len(settled)} still open")

    # Build one record per gradeable row, THEN aggregate. The first version
    # accumulated totals in the loop, which made every split a second pass
    # over the same parsing and invited the two passes to disagree.
    recs = []
    for r in data:
        t = g(r, "Ticker")
        outcome = settled.get(t)
        if not outcome:
            continue
        side = (g(r, "Side") or "").strip().lower()
        n = f(g(r, "Contracts")) or 0
        q = f(g(r, "Quote ¢"))
        if not n or not q:
            continue
        p = q / 100.0
        # The paper row records which side the selector wanted. A 'yes'/'up'
        # row is paid when the market settles yes; a 'no'/'down' row when it
        # settles no. Anything else is left ungraded rather than guessed.
        if side in ("yes", "up", "buy_yes"):
            winner = outcome == "yes"
        elif side in ("no", "down", "buy_no"):
            winner = outcome == "no"
        else:
            continue
        fee = 0.07 * n * p * (1.0 - p)
        # Kalshi rounds the fee UP to the next cent at ORDER level.
        fee = (int(fee * 100) + (1 if fee * 100 % 1 else 0)) / 100.0
        recs.append({
            "day": str(g(r, "Timestamp"))[:10],
            "series": series_of(t),
            "cost": n * p,
            "p": p,
            "fee": fee,
            "payout": float(n) if winner else 0.0,
            "won": 1 if winner else 0,
        })
    if not recs:
        print("\n  no row could be graded (side or price missing).")
        print("=" * 96)
        return 0

    def report(label, rs, indent="  "):
        if not rs:
            return
        staked = sum(x["cost"] for x in rs)
        fees = sum(x["fee"] for x in rs)
        payout = sum(x["payout"] for x in rs)
        won = sum(x["won"] for x in rs)
        net = payout - staked - fees
        mp = st.mean([x["p"] for x in rs])
        # BREAK-EVEN IS THE GROUP'S OWN MEAN COST, never 50%.
        be = 100.0 * (mp + 0.07 * mp * (1 - mp))
        lo, hi = wilson(won, len(rs))
        print(f"{indent}{label:<14}{len(rs):>5}"
              f"{100.0*won/len(rs):>9.2f}%"
              f"{be:>10.2f}%"
              f"{100.0*won/len(rs) - be:>+9.2f}"
              f"{net:>+11.2f}"
              f"{100.0*net/staked:>+9.2f}%"
              f"   [{lo:.1f}-{hi:.1f}]")

    head = (f"  {'group':<14}{'n':>5}{'hit':>10}{'need':>10}"
            f"{'edge pt':>9}{'net $':>11}{'ROI':>10}   [95% hit]")

    print(f"\n{'-' * 112}")
    print("PAPER RESULT ON THE SETTLED ROWS")
    print("-" * 112)
    print(head)
    report("ALL", recs)

    # --------------------------------------------------------- by series
    print(f"\n{'-' * 112}")
    print("BY SERIES -- which coin is paying for the others")
    print("-" * 112)
    print(head)
    by_s = collections.defaultdict(list)
    for x in recs:
        by_s[x["series"]].append(x)
    for k in sorted(by_s, key=lambda k: -len(by_s[k])):
        report(k, by_s[k])

    # ------------------------------------------------------------ by day
    print(f"\n{'-' * 112}")
    print("BY UTC DAY -- XRP was switched off on 2026-11-09, the drop guard")
    print("went 5c -> 3c on 2026-11-10. Both are visible as a date, not a flag.")
    print("-" * 112)
    print(head)
    by_d = collections.defaultdict(list)
    for x in recs:
        by_d[x["day"]].append(x)
    for k in sorted(by_d):
        report(k, by_d[k])

    print(f"\n  A SINGLE DAY IS 13-67 BETS. The 95% interval on the right is")
    print("  the reason no day on its own means anything: at n=20 it spans")
    print("  tens of points, so a good day and a bad day are the same day.")

    print("\n" + "=" * 112)
    print("  AND THIS IS NOT THE PRE-REGISTERED TEST. MAX_DROP_CENTS 3 was")
    print("  registered on 150 SETTLED LIVE bets at $5. Paper rows do not")
    print("  count toward it; while favlive runs dry that test is paused.")
    print("  Entry prices here are QUOTES, not fills, so this is optimistic.")
    print("  'edge pt' is hit rate MINUS the break-even that group's own mean")
    print("  cost demands. Negative means that group loses money at its own")
    print("  prices, however high the hit rate looks.")
    print("=" * 112)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
