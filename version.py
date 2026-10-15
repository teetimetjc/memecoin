"""One row per live session, saying which code and which settings were running.

WHY THIS EXISTS. Every finding in this project is of the form "bets in period
X behaved differently from bets in period Y", and the thing that separates X
from Y is a settings change -- a stake, a side, a guard threshold. Those
changes lived only in git commits and in whoever remembered making them. The
$10 analysis took a day largely because nothing in the data said WHEN $10
started, and stake_of() has to INFER the stake from contracts and price
because nothing recorded it.

So each session writes down what it is before it bets anything. Joining this
tab to the bets by timestamp answers "what was in force when this bet was
placed" without anyone having to remember.

IT IS A LOG, NOT A CONTROL. Nothing here changes behaviour, and a failure to
write is swallowed: a session must never fail to bet because it could not
describe itself.
"""
import os
import time

SHEET = "Versions"

HEADERS = [
    "Started UTC", "Mode", "Commit", "Run", "Stake $", "Sides",
    "Band ¢", "Slip buffer ¢", "Max limit ¢", "Max drop ¢",
    "Per window", "Quiet UTC", "Day stop", "Ladder", "Shadow", "Notes",
]


def _env(k, default=""):
    return (os.environ.get(k) or default).strip()


def describe(stake, dry):
    """The settings in force, read from the environment the bot is using.

    Read from os.environ rather than from the module constants deliberately:
    this records what the WORKFLOW set, which is the thing that changes
    between sessions and the thing a later reader will want to see.
    """
    import live
    sha = _env("GITHUB_SHA")
    run = _env("GITHUB_RUN_ID")
    repo = _env("GITHUB_REPOSITORY", "teetimetjc/memecoin")
    return [
        time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "dry" if dry else "LIVE",
        sha[:7] if sha else "local",
        f"https://github.com/{repo}/actions/runs/{run}" if run else "",
        f"{stake:.2f}",
        _env("FAVLIVE_SIDES", "YES"),
        f"{live.MIN_ENTRY:.0f}-{live.MAX_ENTRY:.0f}",
        f"{live.SLIP_BUFFER_CENTS:.0f}",
        f"{live.MAX_LIMIT_CENTS:.0f}",
        f"{live.MAX_DROP_CENTS:.0f}",
        _env("FAVLIVE_MAX_PER_WINDOW", "-"),
        _env("FAVLIVE_QUIET_UTC") or "none (24h)",
        _env("FAVLIVE_DAY_STOP", "-"),
        f"down after {_env('LADDER_DOWN_AFTER','-')} to "
        f"${_env('LADDER_REDUCED','-')}, up after {_env('LADDER_UP_AFTER','-')}",
        "on" if _env("FAVLIVE_SHADOW", "1") not in ("0", "off") else "off",
        _env("FAVLIVE_NOTE"),
    ]


def log(client, stake, dry):
    """Append this session's row. Never raises."""
    try:
        import predictor as P
        row = describe(stake, dry)
        sh = client.open_by_key(P.SPREADSHEET_ID)
        try:
            ws = sh.worksheet(SHEET)
        except Exception:
            ws = sh.add_worksheet(title=SHEET, rows=2000, cols=len(HEADERS))
            ws.update(values=[HEADERS], range_name="A1")
        ws.append_rows([row], value_input_option="USER_ENTERED",
                       table_range="A1")
        print(f"  [version] logged session: {row[1]} {row[2]} "
              f"stake ${row[4]} buffer {row[7]}c drop {row[9]}c")
    except Exception as e:
        print(f"  [version] could not log the session: {str(e)[:90]}")
