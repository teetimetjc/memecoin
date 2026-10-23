"""Signals that reached the order stage and were not placed. Read-only.

WHY THIS EXISTS. The heartbeat counters showed the T-3 session with 15
signal-windows and 0 bets placed over five hours. That number only
increments AFTER cycle()'s three early returns, so those fifteen windows
had a signal, survived the dedupe, and went to run_window -- and produced
no PLACED row. "Nothing was in band" had been the working explanation and
it is now contradicted by the bot's own counter.

The distinction matters because the two causes want opposite responses. A
rule that finds nothing is working as designed and the answer is patience.
A rule that finds something every window and cannot place it is broken, or
is being declined by a guard, and patience just wastes days.

WHAT IT READS. Every row the sessions wrote in the last few hours, grouped
by Status and by the Detail the bot recorded. live.py writes a row for the
outcome of each attempt, so a decline leaves its reason behind: the drop
guard refusing a collapsing ask, an order that filled nothing because the
ask rose past the limit, a funding check, a cap.

HOW THE SESSIONS ARE TOLD APART. The Order ID holds the exchange's uuid,
not the ORDER_PREFIX, so strategy is inferred from the ticker and the
stake: WTI tickers are the oil session, $3 is the T-3 test, and the five
coins at $4-6 are the frozen spec. That is an inference and the file says
so; a non-placed row often has no contracts at all, which makes the stake
unrecoverable, so such rows are grouped by ticker alone.

Read-only. Places nothing.
"""
import collections
import sys
import time

import live
import tally as T

HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0


def main():
    sh = T.P._get_client().open_by_key(T.P.SPREADSHEET_ID)
    rows = sh.worksheet(live.LIVE_SHEET).get_all_values()
    if len(rows) < 2:
        print("  no rows")
        return 0
    h = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    cutoff = time.strftime("%Y-%m-%d %H:%M",
                           time.gmtime(time.time() - HOURS * 3600))
    recent = [r for r in rows[1:]
              if r and r[0] and T.norm_ts(r[0]) >= cutoff]

    print("=" * 96)
    print(f"WHAT HAPPENED TO EVERY SIGNAL IN THE LAST {HOURS:.0f} HOURS")
    print("=" * 96)
    print(f"  {len(recent)} rows written since {cutoff} UTC\n")
    if not recent:
        print("  Nothing was written at all. That is a different fault from")
        print("  a decline: it means no signal reached the order stage, and")
        print("  the heartbeat's signal-window counter should also be zero.")
        return 0

    def series_of(r):
        return str(c(r, "Ticker")).split("-")[0]

    def strategy_of(r):
        s = series_of(r)
        if s.startswith("KXWTI"):
            return "oil"
        st = T.stake_of(dict(contracts=T._f(c(r, "Contracts")),
                             entry=T._f(c(r, "Entry ¢"))))
        if str(st) == "3":
            return "t3"
        if st is None:
            return "unknown (no contracts recorded)"
        return f"slice (${st})"

    by_status = collections.Counter()
    by_reason = collections.Counter()
    per_strat = collections.defaultdict(collections.Counter)
    for r in recent:
        stt = str(c(r, "Status")).strip().upper() or "(blank)"
        by_status[stt] += 1
        per_strat[strategy_of(r)][stt] += 1
        if stt != "PLACED":
            d = str(c(r, "Detail")).strip()
            # Trim the variable parts so like reasons group together.
            key = d[:70] if d else "(no detail recorded)"
            by_reason[key] += 1

    print("-- BY STATUS " + "-" * 82)
    for k, n in by_status.most_common():
        print(f"  {k:<28} {n:>4}")
    print()

    print("-- BY STRATEGY " + "-" * 80)
    for s in sorted(per_strat):
        bits = ", ".join(f"{k} {v}" for k, v in per_strat[s].most_common())
        print(f"  {s:<28} {bits}")
    print()

    if by_reason:
        print("-- WHY THE NON-PLACED ONES WERE NOT PLACED " + "-" * 53)
        for k, n in by_reason.most_common(15):
            print(f"  {n:>4}  {k}")
        print()

    print("  READ IT THIS WAY. A decline with a reason is the guards doing")
    print("  their job and is not a fault. A row with no detail, or no row")
    print("  at all against a non-zero signal-window count, means the signal")
    print("  path broke between the decision and the record -- which is the")
    print("  case worth chasing, because it is invisible from the outside.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
