"""Print the Control tab and the account balance. Changes nothing.

WHY THIS EXISTS. On 2026-10-12 the bot placed its last bet at 12:51 UTC and
then went quiet for hours while its run sat in_progress, looking healthy from
the outside. GitHub will not serve a job's log until the job ends, and this
session runs for 5h45m, so the one place that would say why is sealed for the
length of the outage.

A halt is SILENT AND STICKY, which is the combination that makes this worth a
script. live.run_window checks the Control tab first and returns without
placing anything when it does not say RUNNING, and control.halt is reached by
two paths that are not obviously the same risk:

    A FUNDING SHORTFALL, where the window needs more than the balance.
    A FAILED BALANCE READ, because check_window returns not-ok when the
    balance is None, and the caller halts on not-ok without distinguishing
    "you cannot afford this" from "I could not tell".

The second one means a single transient API failure can stop betting until a
human types RUNNING into a cell. From outside, that is indistinguishable from
a quiet market.

This reads the switch and prints it. It does not clear a halt: resuming is a
decision about real money and it stays with the human, who can do it from a
phone by typing in the cell.

Read-only. No orders, no writes -- with ONE caveat worth stating plainly:
control._tab() CREATES the Control tab with RUNNING defaults if it is missing.
That is a write, and it is the normal path on a first run, so this script
reads the worksheet directly instead and reports a missing tab as a finding.
"""

import sys

import predictor as P
import control as C


def main():
    print("=" * 78)
    print("CONTROL TAB -- is the bot allowed to bet?")
    print("=" * 78)

    try:
        sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    except Exception as e:
        print(f"  could not open the workbook: {e}")
        return 1

    try:
        ws = sh.worksheet(C.CONTROL_SHEET)
    except Exception:
        print(f"  there is no {C.CONTROL_SHEET} tab. live.run_window fails")
        print("  closed on an unreadable tab, so this alone stops all betting.")
        return 1

    try:
        rows = ws.get_all_values()
    except Exception as e:
        print(f"  could not read the tab: {e}")
        return 1

    for r in rows:
        if not r or not r[0]:
            continue
        name = r[0]
        val = r[1] if len(r) > 1 else ""
        print(f"  {name:<16} {val!r}")

    state = ""
    reason = ""
    for r in rows:
        if r and r[0] == "State":
            state = (r[1] if len(r) > 1 else "").strip().upper()
        if r and r[0] == "Reason":
            reason = (r[1] if len(r) > 1 else "").strip()

    print()
    if state == C.RUNNING:
        print("  STATE IS RUNNING. The Control tab is not what is stopping")
        print("  betting -- look at the signal side instead.")
    else:
        print(f"  STATE IS {state!r} -- BETTING IS STOPPED.")
        if reason:
            print(f"  reason recorded: {reason}")
        print()
        print("  To resume: type RUNNING into the State cell (column B) of")
        print("  the Control tab. That is deliberately a human action.")

    print()
    try:
        bal = C.fetch_balance()
        print(f"  account balance: "
              + ("unreadable" if bal is None else f"${bal:.2f}"))
        if bal is not None:
            # The funding check needs stake x signals for the whole window.
            for n in (1, 2, 5):
                for stake in (10.0,):
                    need = n * stake
                    ok = "ok" if bal + 1e-9 >= need else "SHORT"
                    print(f"    a {n}-signal window at ${stake:.0f} needs "
                          f"${need:.0f}: {ok}")
    except Exception as e:
        print(f"  balance read raised: {str(e)[:120]}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
