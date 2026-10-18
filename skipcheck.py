"""The bets the drop guard refused: did they settle YES or NO?

The guard's case has always been the historical cohort -- fills more than 5c
below quote won 50% against a 72.6% break-even. That is an argument from a
pattern, not from the guard's own record. Every signal it has declined is
logged with a ticker, and Kalshi knows how each market settled, so the
guard can be scored directly.

HOW TO READ IT. A declined signal whose market settled NO is a loss avoided.
One that settled YES is a winner skipped. The second kind is the cost of the
guard and it is real, so both are counted.

WHAT CANNOT BE KNOWN: the price the order would have filled at, since the
ask was collapsing at the time. Valuing a skipped winner at the quote is
therefore the most GENEROUS possible reading of what was missed, and makes
the guard look as bad as it can. If it still comes out ahead on that
basis, the result is not a flattering one.

Read-only.
"""
import tally as T
import live


def result_of(ticker):
    d = T.get(f"/markets/{ticker}")
    m = (d or {}).get("market") or {}
    return str(m.get("result") or "").lower()


def main():
    sh = T.P._get_client().open_by_key(T.P.SPREADSHEET_ID)
    rows = sh.worksheet(live.LIVE_SHEET).get_all_values()
    h = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    skips = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        det = str(c(r, "Detail"))
        if str(c(r, "Status")).strip() != "SKIPPED":
            continue
        if "ask fell" in det or "bid ran up" in det:
            skips.append(r)

    print("=" * 92)
    print("SIGNALS THE DROP GUARD DECLINED")
    print("=" * 92)
    if not skips:
        print("  none on record")
        return 0
    print(f"  {len(skips)} declined\n")
    won = lost = unknown = 0
    saved = missed = 0.0
    for r in skips:
        tk = str(c(r, "Ticker"))
        entry = T._f(c(r, "Entry ¢"))
        pre = T._f(c(r, "Presubmit ¢"))
        res = result_of(tk)
        # What a $5 bet would have done, valued at the QUOTE -- generous to
        # the skipped winners, hostile to the guard.
        n = int(5.0 / ((entry or 93) / 100.0)) if entry else 0
        cost = n * (entry or 0) / 100.0
        fee = 0.07 * n * (entry or 0) / 100.0 * (1 - (entry or 0) / 100.0)
        if res == "yes":
            won += 1
            missed += n * 1.0 - cost - fee
        elif res == "no":
            lost += 1
            saved += cost + fee
        else:
            unknown += 1
        print(f"  {r[0]:<20} {tk[:26]:<26} quote {entry if entry else '-':>5} "
              f"presub {pre if pre is not None else '-':>5}  settled "
              f"{(res or 'unknown').upper():<7} "
              f"{'<- loss avoided' if res=='no' else ('<- winner skipped' if res=='yes' else '')}")
        print(f"    {str(c(r,'Detail'))[:88]}")
    print()
    print(f"  settled NO  (loss avoided) : {lost}   ~${saved:.2f} not lost")
    print(f"  settled YES (winner missed): {won}   ~${missed:.2f} not won")
    print(f"  unknown                    : {unknown}")
    print()
    print(f"  NET EFFECT OF THE GUARD, on these alone: ${saved - missed:+.2f}")
    print("  Skipped winners are valued at the quote, which is the best price")
    print("  they could possibly have filled at while the ask was falling.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
