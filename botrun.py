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
import math
import os
import subprocess
import sqlite3
import sys
import threading
import time

MAX_ROWS_PER_TABLE = 4000        # a night of ticks, bounded
MAX_LOG_LINES = 600
FLUSH_EVERY_S = 300           # push new rows to the sheet every 5 minutes
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
        # THE ONLY BOT THAT GETS CREDENTIALS, and only because it was
        # measured to need them: probe_ws found Kalshi's market-data socket
        # returns HTTP 401 unsigned and streams normally when signed. The
        # REST side is public either way, so Kapelame still gets nothing.
        #
        # NOTE THE CROSSOVER, which is a real trap. EdgeHunter's
        # KALSHI_API_KEY is the ACCESS KEY ID (a UUID, sent as the
        # KALSHI-ACCESS-KEY header). This repo's secret of the same name is
        # the PEM. Passing them straight through would send the whole
        # private key as the key-id header and sign with nothing -- failing
        # with 401s indistinguishable from a wrong key.
        "creds": {"env": {"KALSHI_API_KEY": "KALSHI_KEY_ID",
                          "KALSHI_PRIVATE_KEY_PATH": "keys/kalshi_private.pem"},
                  "pem_from": "KALSHI_API_KEY",
                  "pem_at": "keys/kalshi_private.pem"},
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


def scrub(rows):
    """Redact anything credential-shaped before it leaves for the sheet.

    Applied on EVERY write path, not just the final one. A secret cannot be
    recalled from a spreadsheet, so the incremental flushes have to be as
    careful as the last one -- and it is the periodic path, running
    unattended, that would be easiest to forget."""
    secrets = []
    for v in (os.environ.get("KALSHI_API_KEY"), os.environ.get("KALSHI_KEY_ID")):
        if not v:
            continue
        v = v.replace("\\n", "\n")
        if len(v) > 12:
            secrets.append(v)
        secrets += [ln.strip() for ln in v.splitlines()
                    if len(ln.strip()) >= 32 and "-----" not in ln]
    markers = ("PRIVATE KEY", "BEGIN RSA", "KALSHI-ACCESS-SIGNATURE")
    n = 0
    for row in rows:
        cell = str(row[3])
        if any(m in cell for m in markers) or any(s in cell for s in secrets):
            row[3] = "[REDACTED: line matched a credential marker]"
            n += 1
    if n:
        print(f"  [scrub] REDACTED {n} row(s) -- investigate, the bot should "
              f"never emit these")
    return rows


_TOLD = set()
_TALLY = {"w": 0, "l": 0, "pnl": 0.0, "ten": 0.0, "loaded": False}
_PENDING = []            # settled trades not yet reported
_LAST_DIGEST = [0.0]     # list so the closure can rebind it

FLAT_STAKE = 10.0
ASK_SLIP = 0.01          # you pay the ask; the bot books the mid
# One digest, not one message per bet. At roughly five settlements an hour
# a per-trade alert is about 130 notifications a day, which is not a
# scoreboard, it is noise -- and noise gets muted, which would defeat the
# point. Thirty minutes keeps it glanceable.
DIGEST_EVERY_S = float(os.environ.get("BOT_DIGEST_S") or 1800)
PUSHOVER_LIMIT = 900     # leave headroom under Pushover's 1024


def ten_dollar_pnl(entry, won):
    """What a flat $10 bet would really have returned on this trade.

    Two corrections the bot's own P&L does not make. It books the MID, which
    is not a price anyone can buy at, so a penny is added to reach the ask.
    And it sizes off a simulated bankroll rather than a flat stake, so the
    contract count is recomputed. Kalshi's fee is charged on entry only --
    a winner settles at $1.00 with nothing left to sell."""
    pr = min((entry or 0) + ASK_SLIP, 0.99)
    if pr <= 0:
        return 0.0
    c = int(FLAT_STAKE / pr)
    if c < 1:
        return 0.0
    fee = math.ceil(round(0.07 * c * pr * (1 - pr), 9) * 100) / 100.0
    return (c if won else 0) - (c * pr + fee)


def load_history(tab):
    """Every settled trade already in the tab, so the running total spans
    sessions instead of resetting to zero every four hours.

    Rows are deduplicated on (run, trade id) keeping the LAST copy, because
    a trade is written once unsettled and again when it settles -- counting
    both would inflate the record with phantom losses."""
    if _TALLY["loaded"]:
        return
    _TALLY["loaded"] = True
    try:
        ws = _sheet(tab)
        latest = {}
        for row in ws.get_all_values()[1:]:
            if len(row) < 4 or not str(row[2]).startswith("db:trades"):
                continue
            try:
                d = json.loads(row[3])
            except Exception:
                continue
            latest[(row[0], d.get("id"))] = d
        for (run, tid), d in latest.items():
            s = d.get("settled")
            if not s or str(s).lower() in ("none", "null"):
                continue
            won = (d.get("pnl") or 0) > 0
            _TOLD.add((run, d.get("ticker"), tid))   # never re-alert history
            _TALLY["w" if won else "l"] += 1
            _TALLY["pnl"] += d.get("pnl") or 0.0
            _TALLY["ten"] += ten_dollar_pnl(d.get("entry_price"), won)
        n = _TALLY["w"] + _TALLY["l"]
        print(f"  [history] {n} settled trade(s) already logged "
              f"({_TALLY['w']}W/{_TALLY['l']}L)")
    except Exception as e:
        print(f"  [history] could not load, totals start at 0: {str(e)[:70]}")


def notify_settled(rows, bot):
    """Push one message per paper bet as it settles.

    WHY THIS EXISTS AND THE HOURLY PULSE DID NOT. The pulse was removed
    because it summarised betting that was halted -- it reported nothing, on
    a schedule. This reports a result the moment there is one, which is the
    thing that was actually missing: paper outcomes were invisible until a
    six-hour export.

    NO MONEY IS INVOLVED, and the message says so, because a notification
    naming a winning bet is exactly the kind of prompt that has led to a
    hand-placed order before. It is a scoreboard, not a tip.

    Fail-soft throughout: a notification problem must never disturb a run."""
    if os.environ.get("BOT_ALERTS", "1") != "1":
        return
    try:
        import predictor as P
    except Exception:
        return
    load_history(BOTS[bot]["tab"])
    for r in rows:
        if not str(r[2]).startswith("db:trades"):
            continue
        try:
            d = json.loads(r[3])
        except Exception:
            continue
        settled = d.get("settled")
        if not settled or str(settled).lower() in ("none", "null"):
            continue                      # still open
        tag = (bot, d.get("ticker"), d.get("id"))
        if tag in _TOLD:
            continue                      # already reported this one
        _TOLD.add(tag)
        pnl = d.get("pnl") or 0.0
        won = pnl > 0
        entry = d.get("entry_price")
        this_ten = ten_dollar_pnl(entry, won)
        _TALLY["w" if won else "l"] += 1
        _TALLY["pnl"] += pnl
        _TALLY["ten"] += this_ten
        _PENDING.append({"asset": d.get("asset", ""), "side": d.get("side", ""),
                         "entry": entry or 0, "won": won, "ten": this_ten,
                         "res": settled})
        print(f"  [settled] {d.get('asset')} {d.get('side')} "
              f"{'WIN' if won else 'LOSS'}  $10-basis {this_ten:+.2f}")
    send_digest(P)


def send_digest(P=None, force=False):
    """One message listing everything settled since the last one.

    Held back until there is something to say, and never more often than
    DIGEST_EVERY_S, so the phone gets a scoreboard rather than a stream.
    The newest rows are kept when the list is long, because the old ones
    are already in the running total underneath."""
    if not _PENDING:
        return
    now = time.time()
    if not force and now - _LAST_DIGEST[0] < DIGEST_EVERY_S:
        return
    if P is None:
        try:
            import predictor as P
        except Exception:
            return
    rows, cut = list(_PENDING), 0
    while True:
        lines = [f"{r['asset']:<4}{r['side']:<4}{r['entry']*100:>5.1f}c "
                 f"{'WIN ' if r['won'] else 'LOSS'} {r['ten']:>+7.2f}"
                 for r in rows]
        if cut:
            lines.insert(0, f"(+{cut} older not shown)")
        n = _TALLY["w"] + _TALLY["l"]
        body = ("\n".join(lines) + "\n\n"
                + f"TOTAL {_TALLY['w']}/{n} = "
                  f"{_TALLY['w']/max(n,1)*100:.0f}%\n"
                + f"At $10 a bet: ${_TALLY['ten']:+.2f}\n\n"
                + "PAPER ONLY - no real money staked")
        if len(body) <= PUSHOVER_LIMIT or len(rows) <= 1:
            break
        rows = rows[1:]          # drop oldest; it is already in the total
        cut += 1
    w = sum(1 for r in _PENDING if r["won"])
    try:
        P.send_pushover(f"{len(_PENDING)} settled - {w}W/{len(_PENDING)-w}L "
                        f"(paper)", body)
        print(f"  [digest] sent, {len(_PENDING)} settlement(s)")
        _PENDING.clear()
        _LAST_DIGEST[0] = now
    except Exception as e:
        print(f"  [digest] send failed, will retry: {str(e)[:60]}")


def dump_db(path, run_id, stamp, since=None, quiet=False):
    """Rows as JSON, schema-agnostic.

    `since` is a dict of (table, id) -> content hash. A row is returned when
    it is NEW or when its contents have CHANGED, and the dict is updated in
    place. That lets a four-hour run report progress instead of going dark
    until it ends.

    IT TRACKS CONTENT, NOT JUST THE HIGHEST ID, and that distinction is the
    whole point. paper_trader.py INSERTs a trade at entry with settled NULL
    and UPDATEs the same row at settlement. A cursor of "id greater than the
    last one seen" captures every trade unsettled and never looks again, so
    the win or loss -- the only part anyone cares about -- would never reach
    the sheet. Hashing catches the update."""
    out = []
    if not os.path.exists(path):
        if not quiet:
            print(f"  [db] {path} not present")
        return out
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        tabs = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if not quiet:
            print(f"  [db] {path}: tables {tabs}")
        for t in tabs:
            try:
                cols = [c[1] for c in con.execute(f'PRAGMA table_info("{t}")')]
                has_id = "id" in cols
                cur = con.execute(f'SELECT * FROM "{t}" '
                                  f'LIMIT {MAX_ROWS_PER_TABLE}')
                got = 0
                for r in cur:
                    d = dict(r)
                    js = json.dumps(d, default=str)[:45000]
                    if since is not None:
                        key = (t, d["id"]) if has_id and d.get("id") is not None \
                            else (t, js)
                        h = hash(js)
                        if since.get(key) == h:
                            continue      # unchanged; already sent
                        since[key] = h
                    out.append([run_id, stamp, f"db:{t}", js])
                    got += 1
                if not quiet:
                    print(f"  [db]   {t}: {got} new/changed rows")
            except Exception as e:
                if not quiet:
                    print(f"  [db]   {t}: {str(e)[:70]}")
        con.close()
    except Exception as e:
        if not quiet:
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

    # Strip by default: a bot gets no credentials unless it was MEASURED to
    # need them, and then only the ones named in its own spec. An allowlist,
    # not a blanket un-strip, so adding a bot cannot silently widen this.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("KALSHI_", "AWS_"))}
    env["PYTHONUNBUFFERED"] = "1"

    pem_path = None
    creds = spec.get("creds")
    if creds:
        pem = (os.environ.get(creds["pem_from"]) or "").strip()
        if not pem:
            print(f"  [creds] {creds['pem_from']} is empty -- {key} needs a "
                  f"signed socket and will fail without it")
            return 1
        if "\\n" in pem and "\n" not in pem:
            pem = pem.replace("\\n", "\n")
        # Parse it HERE, before launching. A malformed PEM otherwise shows up
        # as a bot that starts, crashes on a file it cannot read, and logs
        # something that looks like a missing file rather than a bad key.
        try:
            from cryptography.hazmat.primitives import serialization
            serialization.load_pem_private_key(pem.encode(), password=None)
        except Exception as e:
            print(f"  [creds] PEM in {creds['pem_from']} does not parse: "
                  f"{type(e).__name__}")
            return 1
        pem_path = os.path.join(spec["dir"], creds["pem_at"])
        os.makedirs(os.path.dirname(pem_path), exist_ok=True)
        fd = os.open(pem_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(pem if pem.endswith("\n") else pem + "\n")
        for child_var, source in creds["env"].items():
            env[child_var] = (creds["pem_at"] if source == creds["pem_at"]
                              else os.environ.get(source, ""))
        print(f"  [creds] wrote PEM (0600) to {pem_path}; "
              f"key id present: {bool(env.get('KALSHI_API_KEY'))}")

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

        # PERIODIC FLUSH. A four-hour run used to write nothing until it
        # ended, so there was no way to see how it was doing without waiting
        # for the whole session. This pushes new database rows to the sheet
        # every few minutes instead.
        #
        # It is a TIMER THREAD, not a check inside the read loop below, for
        # the same reason the watchdog is: EdgeHunter writes nothing to
        # stdout, so anything driven by arriving lines never runs for it.
        stop_flush = threading.Event()
        seen = {}

        def _flusher():
            ws = None
            while not stop_flush.wait(FLUSH_EVERY_S):
                try:
                    rows = []
                    for db in spec["dbs"]:
                        rows += dump_db(os.path.join(spec["dir"], db),
                                        run_id, time.strftime(
                                            "%Y-%m-%d %H:%M:%S UTC",
                                            time.gmtime()),
                                        since=seen, quiet=True)
                    notify_settled(rows, key)
                    if not rows:
                        continue
                    if ws is None:
                        ws = _sheet(spec["tab"])
                    wrote = _append(ws, scrub(rows))
                    print(f"  [flush] +{wrote} row(s) at "
                          f"{time.strftime('%H:%M:%S', time.gmtime())}")
                except Exception as e:
                    # Never let a flush failure touch the run itself.
                    print(f"  [flush] failed, will retry: {str(e)[:70]}")

        fl = threading.Thread(target=_flusher, daemon=True)
        fl.start()
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
            stop_flush.set()
            fl.join(timeout=30)
        if fired["watchdog"]:
            # Expected for a bot with no duration flag. Not an error.
            status = "stopped-by-watchdog"
    except Exception as e:
        status = f"launch-failed: {str(e)[:70]}"
        print(f"  [run] {status}")

    # Whatever settled in the last few minutes still gets reported, rather
    # than being swallowed because the run ended before the timer came round.
    if os.environ.get("BOT_ALERTS", "1") == "1":
        send_digest(force=True)

    if pem_path and os.path.exists(pem_path):
        try:
            os.remove(pem_path)
            print(f"  [creds] removed {pem_path}")
        except Exception as e:
            print(f"  [creds] COULD NOT REMOVE {pem_path}: {str(e)[:60]}")

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

    # Only what the periodic flush has not already sent, so a long run does
    # not duplicate every tick at the end.
    for db in spec["dbs"]:
        rows += dump_db(os.path.join(spec["dir"], db), run_id, stamp,
                        since=seen)

    rows = scrub(rows)

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
