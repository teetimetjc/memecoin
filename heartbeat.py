"""A pulse each live session writes, so a death leaves a trace. Never raises.

WHY THIS EXISTS. On 2026-10-22 the oil session went live at 15:52, died at
16:46 with its LIVE step still marked in progress, and stayed down for two
hours and forty-five minutes. Nothing noticed. GitHub returns 404 for that
job's log, so the post-mortem is gone too -- when a runner is killed rather
than exiting, the buffered log dies with it.

That is the second time this week a bot's state was only discovered because
somebody asked. latecollect reported success for two days while collecting
nothing; this reported nothing at all while being dead. Both share one
property: THE ONLY RECORD LIVED INSIDE THE THING THAT FAILED.

So the pulse goes somewhere the runner cannot take with it. One row per
strategy in a sheet tab, overwritten in place, carrying the last time that
session was demonstrably alive and what it was doing. A session that dies
stops updating its row, and the row stays behind saying when it stopped.

ONE ROW PER STRATEGY, NOT AN APPEND LOG. A heartbeat every few minutes
across three sessions would add thousands of rows a day to a spreadsheet
that is already the system of record for real money. The question being
answered is "is it alive now", which needs one row, not a history.

IT IS RATE LIMITED AND IT IS WRAPPED. Writing costs an API call, so it
writes at most once every BEAT_SECONDS. Every failure is swallowed: a
monitoring feature that can halt the bot it monitors is worse than no
monitoring at all.
"""
import os
import time

SHEET = "Health"
HEADERS = [
    "Strategy", "Last beat UTC", "Mode", "Stake", "Series", "Band",
    "Windows seen", "Bets placed", "Run URL",
]

BEAT_SECONDS = float(os.environ.get("HEARTBEAT_SECONDS") or 180)

# Last write time and the counters, process-local. A restarted session
# starts its counters again, which is correct: they describe this run.
_STATE = dict(last=0.0, windows=0, bets=0)


def count_window(n_bets=0):
    """Called once per scanned window by the live loop. Cheap, no IO."""
    _STATE["windows"] += 1
    _STATE["bets"] += int(n_bets or 0)


def beat(client, mode, stake, series, band, force=False):
    """Write this session's pulse, at most once every BEAT_SECONDS."""
    try:
        now = time.time()
        if not force and (now - _STATE["last"]) < BEAT_SECONDS:
            return
        _STATE["last"] = now
        import predictor as P

        tag = (os.environ.get("ORDER_PREFIX") or "slice").strip()
        run = ""
        repo = os.environ.get("GITHUB_REPOSITORY")
        rid = os.environ.get("GITHUB_RUN_ID")
        if repo and rid:
            run = f"https://github.com/{repo}/actions/runs/{rid}"

        row = [
            tag,
            time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
            mode,
            f"${stake:.2f}" if isinstance(stake, (int, float)) else str(stake),
            ",".join(series) if series else "",
            band,
            _STATE["windows"],
            _STATE["bets"],
            run,
        ]

        sh = client.open_by_key(P.SPREADSHEET_ID)
        try:
            ws = sh.worksheet(SHEET)
        except Exception:
            ws = sh.add_worksheet(title=SHEET, rows=50, cols=len(HEADERS))
            ws.update(values=[HEADERS], range_name="A1")

        # Find this strategy's row and overwrite it; append only if new.
        # Keyed on the prefix, which is what distinguishes the sessions.
        col = ws.col_values(1)
        at = None
        for i, v in enumerate(col[1:], start=2):
            if str(v).strip() == tag:
                at = i
                break
        if at is None:
            ws.append_rows([row], value_input_option="USER_ENTERED",
                           table_range="A1")
        else:
            ws.update(values=[row], range_name=f"A{at}")
    except Exception as e:
        # A monitoring feature may never be the thing that stops the bot.
        print(f"  [heartbeat] skipped: {str(e)[:100]}")
