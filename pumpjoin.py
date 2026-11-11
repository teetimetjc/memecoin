"""What fraction of pump.fun launches actually graduate? Read-only.

THE ONE NUMBER EVERYTHING ELSE DEPENDS ON, and the one most often quoted
wrong. The naive version is migrations divided by launches -- here, 316 over
4,285, or 7.4% -- and that figure is wrong in three separate directions at
once. Each is corrected explicitly below rather than hand-waved.

  1. MIGRATIONS FROM TOKENS WE NEVER SAW LAUNCH. A graduation observed today
     may belong to a token created last week, before this collector existed.
     It has no launch row here, so counting it inflates the numerator
     against a denominator it was never part of. Fix: a migration only
     counts if its mint is in the launch set. The number discarded is
     reported, because it measures how much of the raw count was borrowed
     from the past.

  2. LAUNCHES TOO YOUNG TO HAVE GRADUATED. Most of the 4,285 were created in
     the last few hours. A token that will graduate tomorrow is counted
     today as one that did not, which deflates the rate without bound --
     the faster the collector runs, the worse the number looks. Fix: the
     rate is computed only over launches that have had at least W minutes to
     graduate, and reported as a curve across W rather than as one figure.

  3. THE COLLECTOR'S OWN BLIND SPOTS. The launch recorder died at 02:43 on
     2026-11-11 and did not restart until 04:37, and the job exited green.
     A graduation during a gap was never received, so every launch whose
     window overlaps a gap looks like a failure. Fix: gaps are detected from
     the launch stream itself and any launch whose observation window
     overlaps one is EXCLUDED, not counted as a non-graduation.

THE RATE IS REPORTED WITH A CONFIDENCE INTERVAL because the corrected
denominators are small. After requiring an hour of maturity and excluding
gap-affected launches, a 4,000-row tab can leave a few hundred usable
launches, and a few hundred near a low rate has an interval wide enough to
matter.

WHAT THIS DELIBERATELY DOES NOT TOUCH: the Pump Prices tab, the reserve
decode, and every multiple derived from it. Those have an unexplained
defect -- readings above any graduated curve -- and joining them in here
would launder it into a result. This file reads launch and migration events
only, which come straight off the websocket with no decoding.

AND A GRADUATION IS NOT A PROFIT. It means the curve filled and the token
moved to a real pool. Whether buying at launch and selling at graduation
makes money depends on the price paid, the price received, priority fees and
the spread, none of which is here. This answers "how often does it happen",
not "is it worth doing".

Read-only. No wallet, no order, no row written.
"""
import collections
import json
import os
from datetime import datetime, timedelta

SHEET_ID = os.environ.get("MEME_SHEET_ID")
# A gap is a stretch with no launch at all. The feed delivers ~7 a minute,
# so even a quiet minute is unusual; 5 minutes of total silence is the
# collector being down, not pump.fun going quiet.
GAP_MINUTES = float(os.environ.get("PUMPJOIN_GAP_MIN") or 5.0)
# Maturity windows, in minutes, to report the rate across.
WINDOWS = [15, 30, 60, 120, 240, 480]


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


def ts(v):
    """Parse the collector's 'YYYY-MM-DD HH:MM:SS' UTC stamp, or None.

    RETURNS None RATHER THAN A DEFAULT. A row with an unreadable timestamp
    cannot be placed in a window, and defaulting it to now or to epoch would
    put it in the wrong one silently.
    """
    s = str(v or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def wilson(k, n):
    if not n:
        return 0.0, 0.0
    z = 1.959964
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    hw = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return 100.0 * max(0.0, c - hw), 100.0 * min(1.0, c + hw)


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
    print("DO PUMP.FUN LAUNCHES GRADUATE? -- the base rate, properly joined")
    print("=" * 100)
    sh = client()
    lrows, lh = grab(sh, "Pump Launches")
    mrows, mh = grab(sh, "Pump Migrations")

    def g(r, h, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    # ------------------------------------------------- launches, deduped
    launches = {}
    bad_ts = 0
    for r in lrows:
        m = g(r, lh, "Mint")
        t = ts(g(r, lh, "Seen UTC"))
        if not m:
            continue
        if t is None:
            bad_ts += 1
            continue
        # Keep the EARLIEST sighting: a duplicated create event is the same
        # launch, and the first time it was seen is its age origin.
        if m not in launches or t < launches[m]:
            launches[m] = t

    migs = {}
    mig_bad = 0
    for r in mrows:
        m = g(r, mh, "Mint")
        t = ts(g(r, mh, "Seen UTC"))
        if not m:
            continue
        if t is None:
            mig_bad += 1
            continue
        if m not in migs or t < migs[m]:
            migs[m] = t

    print(f"\n  {len(lrows)} launch row(s) -> {len(launches)} unique mint(s)")
    print(f"  {len(mrows)} migration row(s) -> {len(migs)} unique mint(s)")
    if bad_ts or mig_bad:
        print(f"  {bad_ts} launch and {mig_bad} migration row(s) had an "
              f"unreadable timestamp and were dropped, not defaulted")

    # ------------------------------- correction 1: borrowed graduations
    matched = {m: t for m, t in migs.items() if m in launches}
    orphan = len(migs) - len(matched)
    print(f"\n{'-' * 100}")
    print("CORRECTION 1 -- graduations of tokens we never saw launch")
    print("-" * 100)
    print(f"  {len(matched)} of {len(migs)} migration(s) match a launch here")
    print(f"  {orphan} belong to tokens created before this collector "
          f"started, or during a gap")
    if migs:
        print(f"  so {100.0*orphan/len(migs):.1f}% of the raw migration count "
              f"is borrowed from the past and cannot be")
        print("  divided by our launch count. The naive ratio is wrong here")
        print("  before any maturity question is even asked.")

    # A graduation BEFORE its launch is impossible and would mean the join
    # is on the wrong key. Checked rather than assumed.
    backwards = [m for m, t in matched.items() if t < launches[m]]
    if backwards:
        print(f"\n  {len(backwards)} of {len(matched)} migration(s) "
              f"timestamped BEFORE their own launch, which is impossible:")
        for m in backwards[:10]:
            delta = (launches[m] - matched[m]).total_seconds()
            print(f"    {m}")
            print(f"      launch    {launches[m]}")
            print(f"      migration {matched[m]}   ({delta:,.0f}s earlier)")
        # WHETHER THIS IS FATAL DEPENDS ON THE SIZE, and the two cases are
        # genuinely different rather than a judgement call:
        #
        #   a systematically wrong join key would make MOST matches
        #   backwards, because nothing would line up;
        #
        #   a handful, at a lag of seconds, is the recorder stamping its own
        #   RECEIPT time rather than the chain's -- two sockets, two
        #   sessions, one-second resolution and out-of-order delivery around
        #   a reconnect will put a migration a few seconds ahead of a create
        #   it genuinely followed.
        #
        # So a large share stops the run, and a small one drops just those
        # mints and says so. The threshold is stated here rather than tuned
        # until the data passed.
        share = len(backwards) / max(1, len(matched))
        worst = max((launches[m] - matched[m]).total_seconds()
                    for m in backwards)
        if share > 0.02 or worst > 600:
            print(f"\n  that is {100*share:.1f}% of matches, worst "
                  f"{worst:,.0f}s. Too much to be receipt-time noise, so")
            print("  the join key is wrong. Stopping rather than reporting "
                  "a rate from it.")
            return 1
        print(f"\n  that is {100*share:.1f}% of matches, worst "
              f"{worst:,.0f}s -- consistent with receipt-time stamping")
        print("  around a reconnect, not a broken join. Those mints are")
        print("  DROPPED from everything below rather than counted either "
              "way.")
        for m in backwards:
            matched.pop(m, None)
            launches.pop(m, None)

    # ----------------------------------- correction 3: collector blind spots
    stamps = sorted(launches.values())
    gaps = []
    for a, b in zip(stamps, stamps[1:]):
        if (b - a).total_seconds() >= GAP_MINUTES * 60:
            gaps.append((a, b))
    last_seen = stamps[-1]
    print(f"\n{'-' * 100}")
    print("CORRECTION 3 -- when the collector was not listening")
    print("-" * 100)
    print(f"  launch stream runs {stamps[0]} -> {last_seen} UTC")
    if not gaps:
        print(f"  no silence of {GAP_MINUTES:.0f}+ minutes; coverage looks "
              f"continuous")
    for a, b in gaps:
        mins = (b - a).total_seconds() / 60.0
        print(f"  BLIND {a} -> {b}  ({mins:,.0f} min)")
    print("  A graduation during a blind stretch was never received, so any")
    print("  launch whose window overlaps one is EXCLUDED below rather than")
    print("  counted as a token that failed to graduate.")

    def blinded(start, end):
        return any(a < end and start < b for a, b in gaps)

    # ----------------------- correction 2: only launches old enough to count
    print(f"\n{'-' * 100}")
    print("THE GRADUATION RATE, by how long the token was given")
    print("-" * 100)
    print(f"  {'window':>8}{'usable':>9}{'grads':>8}{'rate':>9}"
          f"{'95% interval':>18}{'excluded: young':>17}{'blind':>8}")
    for w in WINDOWS:
        span = timedelta(minutes=w)
        usable = grads = young = blind = 0
        for m, t in launches.items():
            end = t + span
            if end > last_seen:
                # Not yet had its full window. Counting it as a failure is
                # correction 2's whole point.
                young += 1
                continue
            if blinded(t, end):
                blind += 1
                continue
            usable += 1
            mt = matched.get(m)
            if mt is not None and mt <= end:
                grads += 1
        if not usable:
            print(f"  {w:>6}m{usable:>9}{'--':>8}{'--':>9}"
                  f"{'not enough history':>18}{young:>17}{blind:>8}")
            continue
        lo, hi = wilson(grads, usable)
        print(f"  {w:>6}m{usable:>9}{grads:>8}"
              f"{100.0*grads/usable:>8.2f}%"
              f"{f'[{lo:.2f} - {hi:.2f}]':>18}{young:>17}{blind:>8}")

    print("\n  'usable' is the honest denominator: launches we saw, that had")
    print("  their full window, with no blind stretch inside it. 'young' and")
    print("  'blind' are what the naive 7.4% silently counted as failures.")

    # ------------------------------------------- how fast, for the survivors
    lags = sorted((matched[m] - launches[m]).total_seconds() / 60.0
                  for m in matched)
    if len(lags) >= 10:
        n = len(lags)
        print(f"\n{'-' * 100}")
        print(f"HOW LONG GRADUATION TOOK, for the {n} that did it")
        print("-" * 100)
        for lab, i in (("fastest", 0), ("10th pct", n // 10),
                       ("median", n // 2),
                       ("90th pct", min(n - 1, 9 * n // 10)),
                       ("slowest", n - 1)):
            print(f"  {lab:<12}{lags[i]:>9.1f} min")
        print("  This is the window an exit rule would have to live inside,")
        print("  and it is measured only on survivors -- the ones that never")
        print("  graduated have no lag and are not in this distribution.")

    # ------------------------------- the repeat-launcher question, unpriced
    cre = collections.defaultdict(list)
    for r in lrows:
        m, c = g(r, lh, "Mint"), g(r, lh, "Creator")
        if m and c:
            cre[c].append(m)
    print(f"\n{'-' * 100}")
    print("DOES A REPEAT LAUNCHER GRADUATE MORE OR LESS OFTEN?")
    print("-" * 100)
    print(f"  {'launches by creator':>22}{'creators':>10}{'launches':>10}"
          f"{'grads':>8}{'rate':>9}{'95% interval':>18}")
    buckets = [(1, 1), (2, 3), (4, 10), (11, 50), (51, 10 ** 9)]
    for lo_n, hi_n in buckets:
        who = [c for c, ms in cre.items() if lo_n <= len(set(ms)) <= hi_n]
        mints = {m for c in who for m in cre[c]}
        # Same maturity and blindness rules as above, at the 60m window, so
        # this table and that one are answering the same question.
        span = timedelta(minutes=60)
        tot = gr = 0
        for m in mints:
            t = launches.get(m)
            if t is None:
                continue
            end = t + span
            if end > last_seen or blinded(t, end):
                continue
            tot += 1
            mt = matched.get(m)
            if mt is not None and mt <= end:
                gr += 1
        if not tot:
            continue
        a, b = wilson(gr, tot)
        label = f"{lo_n}" if lo_n == hi_n else (
            f"{lo_n}+" if hi_n > 10 ** 8 else f"{lo_n}-{hi_n}")
        print(f"  {label:>22}{len(who):>10}{tot:>10}{gr:>8}"
              f"{100.0*gr/tot:>8.2f}%{f'[{a:.2f} - {b:.2f}]':>18}")
    print("\n  READ THE INTERVALS, NOT THE RATES. If they overlap, this table")
    print("  has not distinguished repeat launchers from one-offs, however")
    print("  different the point estimates look.")

    print("\n" + "=" * 100)
    print("  AND A GRADUATION IS NOT A PROFIT. It means the curve filled and")
    print("  the token moved to a real pool. Whether buying at launch and")
    print("  selling at graduation makes money depends on prices, priority")
    print("  fees and spread, none of which is in this file.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
