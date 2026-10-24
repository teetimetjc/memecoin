"""Is every session that should be alive actually alive? Alerts if not.

THE FAILURE THIS CATCHES. The oil session died 54 minutes into a 5h45m run
and stayed down for 2h45m. Its GitHub job showed the LIVE step "in
progress" the whole time, its log 404s, and nothing anywhere said it had
stopped. The only reason it was found is that somebody asked.

The general shape, which this project has now hit twice in a week: THE ONLY
RECORD OF A JOB'S HEALTH LIVED INSIDE THE JOB. heartbeat.py moves the pulse
into the sheet; this reads it from outside and complains.

WHAT IT CHECKS. For each strategy that is supposed to be running, how long
since its last pulse. A session beats every three minutes, so anything past
STALE_MINUTES is either dead, wedged, or never started. All three deserve
the same alert, because all three mean the same thing to the account: that
strategy is not betting and nobody was told.

WHY IT ALERTS RATHER THAN JUST PRINTING. A report nobody reads is the
problem it is meant to solve. It pushes when something is stale, and stays
silent when everything is fine, so the notification means something.

IT DOES NOT RESTART ANYTHING. Each session has its own hourly schedule and
will restart itself; a watchdog that also restarts can fight that schedule
and double a session. Reporting is the job here. If a strategy is stale for
hours, that is a decision for a human, not an automatic retry loop.

Read-only against the exchange; writes nothing to the record.
"""
import calendar
import os
import sys
import time

import heartbeat as HB

# Minutes without a pulse before a session counts as stale. A session beats
# every three minutes, and hourly restarts leave a gap of a minute or two
# between sessions, so fifteen is comfortably past normal and well inside
# the 2h45m outage this exists to catch.
STALE_MINUTES = float(os.environ.get("HEALTH_STALE_MINUTES") or 15)

# The strategies expected to be running, by ORDER_PREFIX. Set
# HEALTH_EXPECT to change it without editing code -- a session that is
# deliberately stopped should be removed here, or the alert becomes noise
# and noise is how a real one gets ignored.
EXPECT = tuple(
    x.strip() for x in
    (os.environ.get("HEALTH_EXPECT") or "slice,oil,t3").split(",")
    if x.strip())

LABEL = {
    "slice": "the frozen T-9 crypto session",
    "oil": "the WTI session",
    "t3": "the T-3 test",
}


def main():
    import predictor as P
    try:
        sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
        rows = sh.worksheet(HB.SHEET).get_all_values()
    except Exception as e:
        print(f"  could not read the Health tab: {str(e)[:120]}")
        # A watchdog that cannot see is itself a failure worth shouting
        # about, because silence from here would otherwise read as health.
        try:
            P.send_pushover("Bot health UNKNOWN",
                            "The watchdog could not read the Health tab.")
        except Exception:
            pass
        return 1

    beats = {}
    for r in rows[1:] if len(rows) > 1 else []:
        if not r or not str(r[0]).strip():
            continue
        beats[str(r[0]).strip()] = r

    # ---- IS BETTING EVEN ENABLED? ---------------------------------------
    #
    # A pulse says the process is alive. It does NOT say the bot is allowed
    # to bet. control.get_state returns HALTED when the Control tab says so
    # and when the tab cannot be read, and run_window returns on either
    # before writing a row -- so a halted account looks exactly like a quiet
    # market: sessions beat, jobs stay green, the sheet stays empty.
    #
    # That is not hypothetical. On 2026-10-24 a single duplicate-order
    # rejection on one ticker halted all three strategies for about seven
    # hours. Every check this file performed came back "ok" throughout,
    # because every session really was alive. It was watching the wrong
    # thing.
    halted_reason = None
    try:
        import control
        st, why = control.get_state(P._get_client())
        if st != control.RUNNING:
            halted_reason = why or "no reason recorded"
    except Exception as e:
        halted_reason = f"could not read the Control tab: {str(e)[:80]}"

    now = time.time()
    print("=" * 88)
    print("SESSION HEALTH -- who is alive, and since when")
    print("=" * 88)
    if halted_reason:
        print(f"  *** BETTING IS HALTED: {halted_reason}")
        print("  *** Every session below may be alive and placing NOTHING.")
        print()
    stale, missing = [], []
    for tag in EXPECT:
        name = LABEL.get(tag, tag)
        r = beats.get(tag)
        if not r or len(r) < 2:
            print(f"  {tag:<7} {name:<34} NO PULSE EVER RECORDED")
            missing.append(tag)
            continue
        try:
            # calendar.timegm, NOT mktime minus time.timezone. The stamp is
            # UTC and mktime reads it as local; the usual correction with
            # time.timezone ignores DST, so on a host observing it the
            # staleness would be an hour out and the watchdog would either
            # cry wolf or miss an outage. timegm has no such failure mode.
            t = calendar.timegm(time.strptime(str(r[1]).strip(),
                                              "%Y-%m-%d %H:%M:%S"))
        except Exception:
            print(f"  {tag:<7} {name:<34} unreadable timestamp {r[1]!r}")
            missing.append(tag)
            continue
        mins = (now - t) / 60.0
        extra = ""
        if len(r) > 7:
            extra = f"  windows {r[6]}, bets {r[7]}"
        flag = "STALE" if mins > STALE_MINUTES else "ok"
        print(f"  {tag:<7} {name:<34} {flag:<5} last beat "
              f"{mins:6.1f} min ago{extra}")
        if mins > STALE_MINUTES:
            stale.append((tag, name, mins))

    print()
    if not stale and not missing and not halted_reason:
        print(f"  All {len(EXPECT)} expected session(s) are beating and "
              "betting is enabled. No alert sent.")
        return 0

    # A halt alerts even when every session is beating, because "alive" and
    # "working" came apart and this file exists to notice that.
    if halted_reason and not stale and not missing:
        print(f"  ALERTING: betting is halted -- {halted_reason}")
        try:
            P.send_pushover("Bot HALTED -- placing no bets",
                            f"All sessions are alive but the Control tab is "
                            f"not RUNNING.\n{halted_reason}")
            print("  pushover sent")
        except Exception as e:
            print(f"  pushover failed: {str(e)[:100]}")
        return 2

    lines = []
    for tag, name, mins in stale:
        lines.append(f"{tag}: no pulse for {mins:.0f} min ({name})")
    for tag in missing:
        lines.append(f"{tag}: no pulse ever recorded "
                     f"({LABEL.get(tag, tag)})")
    if halted_reason:
        lines.append(f"AND betting is halted: {halted_reason}")
    body = "\n".join(lines)
    print("  ALERTING:")
    for l in lines:
        print(f"    {l}")
    try:
        P.send_pushover(
            f"Bot down: {len(stale) + len(missing)} session(s) stale", body)
        print("\n  pushover sent")
    except Exception as e:
        print(f"\n  pushover failed: {str(e)[:100]}")
    # Exit non-zero so the run itself shows red in the Actions list -- a
    # second place the problem is visible without anyone asking.
    return 2


if __name__ == "__main__":
    sys.exit(main())
