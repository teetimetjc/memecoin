"""Run a third-party paper bot on a runner and land its output in the sheet.

WHY THIS IS DELIBERATELY DUMB. Neither bot can be executed in the session
where this was written -- the sandbox blocks every market-data host they
need -- so this harness has never seen their real output. Anything here that
assumed a table name, a column order or a log format would be a guess, and a
guess that runs unattended overnight fails silently and wastes the night.

So it assumes almost nothing:

  THE SQLITE DUMP IS SCHEMA-AGNOSTIC. It asks the database which tables
  exist, then writes each row as JSON. Ugly to read, impossible to get
  wrong. Once a real night's output exists the columns can be normalised
  from something real instead of something imagined.

  STDOUT IS CAPTURED WHOLESALE, not filtered. A filter is a guess about
  which lines matter, and the interesting line on a night that goes wrong is
  always the one the filter dropped.

  A RUN IS LOGGED EVEN WHEN THE BOT DIES. Crash, timeout and clean exit all
  append a summary row. A missing row therefore means the harness itself
  failed, which is a different diagnosis from "the bot found nothing", and
  the two must never look alike.

PAPER ONLY. This runs `main.py` (EdgeHunter) and `paper_trader.py`
(kalshi-crypto-bot). It will not run trader.py, which is a real live trader
with an empty strategy stub, and it passes no Kalshi credentials to either
child process -- so even a code change upstream could not place an order
from here.

Usage:  python botrun.py <edgehunter|kapelame> <seconds>
"""

import json
import os
import subprocess
import sqlite3
import sys
import threading
import time

MAX_ROWS_PER_TABLE = 4000        # a night of ticks, bounded
MAX_LOG_LINES = 600
TAIL_KEEP = 400

BOTS = {
    "edgehunter": {
        "tab": "EdgeHunter Paper",
        "dir": "bots/EdgeHunter",
        # main.py takes no duration and runs until killed. The watchdog below
        # is the ONLY thing that stops it.
        "cmd": [".venv/bin/python", "main.py"],
        "dbs": ["edgehunter.db"],
        # structlog writes here, NOT to stdout. A first canary captured 0
        # stdout lines from this bot and hung, because the reader blocked on
        # a pipe that was never going to speak.
        "logs": ["logs/edgehunter.jsonl"],
    },
    "kapelame": {
        "tab": "Kapelame Paper",
        "dir": "bots/kalshi-crypto-bot",
        # --duration is parsed by hand in that file, not argparse; it still
        # works, and the watchdog below is the real backstop.
        "cmd": [".venv/bin/python", "paper_trader.py", "--duration"],
        "dbs": ["kalshi_live_paper.db"],
        "logs": ["kalshi_live_paper.log"],
    },
}

HEADERS = ["Run ID", "Logged UTC", "Source", "Payload"]


def _sheet(name):
    import predictor as P
    client = P._get_client()
    sh = client.open_by_key(P.SPREADSHEET_ID)
    try:
        ws = sh.worksheet(name)
    except Exception:
        ws = sh.add_worksheet(title=name, rows=20000, cols=len(HEADERS))
        ws.update("A1", [HEADERS])
    return ws


def _append(ws, rows):
    """Fail-soft, in chunks. A sheet hiccup must not discard a whole night."""
    n = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        for attempt in range(3):
            try:
                ws.append_rows(chunk, value_input_option="RAW",
                               table_range="A1")
                n += len(chunk)
                break
            except Exception as e:
                if attempt == 2:
                    print(f"  [sheet] gave up on chunk {i}: {str(e)[:80]}")
                else:
                    time.sleep(2 * (attempt + 1))
    return n


def dump_db(path, run_id, stamp):
    """Every row of every table, as JSON. No schema assumed."""
    out = []
    if not os.path.exists(path):
        print(f"  [db] {path} not present")
        return out
    try:
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        tabs = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        print(f"  [db] {path}: tables {tabs}")
        for t in tabs:
            try:
                cur = con.execute(f'SELECT * FROM "{t}" LIMIT {MAX_ROWS_PER_TABLE}')
                got = 0
                for r in cur:
                    out.append([run_id, stamp, f"db:{t}",
                                json.dumps(dict(r), default=str)[:45000]])
                    got += 1
                print(f"  [db]   {t}: {got} rows")
            except Exception as e:
                print(f"  [db]   {t}: {str(e)[:70]}")
        con.close()
    except Exception as e:
        print(f"  [db] {path}: {str(e)[:80]}")
    return out


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in BOTS:
        print(f"usage: python botrun.py <{'|'.join(BOTS)}> <seconds>")
        return 2
    key = sys.argv[1]
    secs = int(sys.argv[2])
    spec = BOTS[key]
    run_id = time.strftime("%Y%m%d-%H%M", time.gmtime())
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    cmd = list(spec["cmd"])
    if cmd[-1] == "--duration":
        cmd.append(str(secs))

    print(f"=== {key} :: run {run_id} :: {secs}s :: tab '{spec['tab']}' ===")
    print(f"    cmd: {' '.join(cmd)}  (cwd {spec['dir']})")

    # No Kalshi credentials reach the child. Paper mode needs none, and this
    # way an upstream change cannot quietly start signing orders.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("KALSHI_", "AWS_"))}
    env["PYTHONUNBUFFERED"] = "1"

    lines, status, rc = [], "ok", None
    t0 = time.time()
    fired = {"watchdog": False}
    try:
        p = subprocess.Popen(cmd, cwd=spec["dir"], env=env,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True,
                             bufsize=1)

        # THE WATCHDOG, and why it is a timer rather than a check inside the
        # read loop. The first version tested the clock only when a line
        # arrived on stdout. EdgeHunter logs through structlog to a FILE and
        # says almost nothing on stdout, so the reader blocked on a pipe that
        # was never going to speak, the clock was never consulted, and a
        # 15-minute canary was still running 17 minutes later. A timer runs
        # whether or not the child ever writes a byte; killing it closes the
        # pipe, which is what ends the loop below.
        def _stop():
            fired["watchdog"] = True
            for finish in (p.terminate, p.kill):
                try:
                    finish()
                    p.wait(timeout=20)
                    return
                except Exception:
                    continue

        wd = threading.Timer(secs + 60, _stop)
        wd.daemon = True
        wd.start()
        try:
            for line in p.stdout:
                line = line.rstrip()
                lines.append(line)
                if len(lines) <= 80 or len(lines) % 200 == 0:
                    print(f"    | {line[:150]}")
            rc = p.wait(timeout=120)
        except subprocess.TimeoutExpired:
            p.kill()
            status = "timeout"
        finally:
            wd.cancel()
        if fired["watchdog"]:
            # Expected for a bot with no duration flag. Not an error.
            status = "stopped-by-watchdog"
    except Exception as e:
        status = f"launch-failed: {str(e)[:70]}"
        print(f"  [run] {status}")

    elapsed = time.time() - t0
    print(f"=== finished: status={status} rc={rc} elapsed={elapsed:.0f}s "
          f"stdout_lines={len(lines)} ===")

    rows = [[run_id, stamp, "run",
             json.dumps({"bot": key, "status": status, "rc": rc,
                         "requested_s": secs, "elapsed_s": round(elapsed),
                         "stdout_lines": len(lines)})]]

    # Head and tail of stdout. The middle of a long quiet run is the least
    # informative part of it; the start says whether it booted and the end
    # says how it died.
    keep = lines[:MAX_LOG_LINES - TAIL_KEEP] + (
        [f"... {len(lines) - MAX_LOG_LINES} lines omitted ..."]
        if len(lines) > MAX_LOG_LINES else []) + lines[-TAIL_KEEP:] \
        if len(lines) > MAX_LOG_LINES else lines
    for ln in keep:
        rows.append([run_id, stamp, "stdout", ln[:45000]])

    # Log FILES, which for EdgeHunter are where the real output lives.
    for rel in spec.get("logs", []):
        path = os.path.join(spec["dir"], rel)
        if not os.path.exists(path):
            print(f"  [log] {path} not present")
            continue
        try:
            with open(path, errors="replace") as f:
                flines = [l.rstrip() for l in f]
            keep = (flines if len(flines) <= MAX_LOG_LINES
                    else flines[:MAX_LOG_LINES - TAIL_KEEP]
                    + [f"... {len(flines) - MAX_LOG_LINES} lines omitted ..."]
                    + flines[-TAIL_KEEP:])
            for ln in keep:
                rows.append([run_id, stamp, f"log:{rel}", ln[:45000]])
            print(f"  [log] {path}: {len(flines)} lines, kept {len(keep)}")
            # Echo a sample into the JOB log too. Zero signals can mean "saw
            # nothing worth trading" or "never connected to a feed", and the
            # row count cannot tell those apart -- which is the distinction
            # that decides whether a night was worth anything.
            for ln in flines[:6] + (["  ..."] + flines[-6:]
                                    if len(flines) > 12 else []):
                print(f"  [log]   {ln[:220]}")
        except Exception as e:
            print(f"  [log] {path}: {str(e)[:70]}")

    for db in spec["dbs"]:
        rows += dump_db(os.path.join(spec["dir"], db), run_id, stamp)

    try:
        ws = _sheet(spec["tab"])
        wrote = _append(ws, rows)
        print(f"=== wrote {wrote}/{len(rows)} rows to '{spec['tab']}' ===")
    except Exception as e:
        print(f"=== SHEET WRITE FAILED: {str(e)[:120]} ===")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
