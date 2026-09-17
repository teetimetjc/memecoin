"""The brakes. Built before the engine, on purpose.

A Control tab in the same workbook holds one word -- RUNNING or HALTED -- that
decides whether the bot is allowed to place bets. Nothing here places an order;
this is the thing that will be allowed to STOP one.

Why a sheet cell and not a file or an env var:
  - Each GitHub Actions run starts from nothing, so the state has to outlive
    the process. A variable in memory cannot.
  - You can clear a halt from your phone by typing in a cell. No commit, no
    redeploy, no waiting on me.
  - The reason and timestamp sit next to it, so a halt explains itself weeks
    later instead of being a mystery.

The rule it enforces, once betting exists: check the balance against what the
WHOLE window needs before placing anything. If it does not cover it, place
nothing, notify, and halt. Partial fills are refused deliberately -- funding
three of four signals silently drops whichever coin the loop reaches last,
which biases the record toward the top of the list and cannot be
reconstructed afterwards. Stopping clean is the honest failure.

Collection is never affected. A halt stops betting only; signals keep logging
and the dashboard keeps updating, so a halt costs money-making, not data.
"""

import math
import sys
from datetime import datetime, timezone

import requests

CONTROL_SHEET = "Control"

RUNNING = "RUNNING"
HALTED = "HALTED"

# A1 layout. Kept tiny and human-editable -- this tab is a switch, not a log.
LAYOUT = [
    ["Setting", "Value", "Notes"],
    ["State", RUNNING,
     "RUNNING or HALTED. Type RUNNING here to resume betting after a halt."],
    ["Reason", "",
     "Why the last halt happened. Cleared when you resume."],
    ["Changed", "",
     "When the state last changed (UTC)."],
    ["Stake $", "10",
     "Dollars per bet, once live betting is enabled."],
    ["Last balance $", "",
     "Account balance as of the last check."],
]

_ROW = {name: i + 1 for i, (name, *_ ) in enumerate(LAYOUT)}   # 1-based rows


def _tab(client):
    """The Control tab, created with defaults if this is the first run."""
    import predictor as P
    sh = client.open_by_key(P.SPREADSHEET_ID)
    try:
        return sh.worksheet(CONTROL_SHEET)
    except Exception:
        ws = sh.add_worksheet(title=CONTROL_SHEET, rows=len(LAYOUT) + 2, cols=3)
        ws.update("A1", LAYOUT)
        print(f"  {CONTROL_SHEET} tab created -- state {RUNNING}.")
        return ws


def get_state(client):
    """(state, reason). Returns HALTED if the tab is unreadable.

    Fails CLOSED. If we cannot confirm we are allowed to bet, we are not
    allowed to bet -- an unreachable sheet must never read as permission.
    """
    try:
        ws = _tab(client)
        state = (ws.acell(f"B{_ROW['State']}").value or "").strip().upper()
        reason = (ws.acell(f"B{_ROW['Reason']}").value or "").strip()
    except Exception as e:
        return HALTED, f"could not read the Control tab: {e}"
    if state not in (RUNNING, HALTED):
        return HALTED, f"Control tab says {state!r}, which is not {RUNNING} or {HALTED}"
    return state, reason


def set_state(client, state, reason=""):
    """Write the state back, with a reason and a timestamp."""
    ws = _tab(client)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ws.update(f"B{_ROW['State']}", [[state]])
    ws.update(f"B{_ROW['Reason']}", [[reason]])
    ws.update(f"B{_ROW['Changed']}", [[now]])
    return state


def halt(client, reason, notify=True):
    """Stop betting, say why, and tell the human. Safe to call twice."""
    import predictor as P
    set_state(client, HALTED, reason)
    print(f"  HALTED: {reason}")
    if notify:
        try:
            P.send_pushover("Betting halted", f"{reason}\n\nBetting is stopped "
                            f"until you set the Control tab back to {RUNNING}.")
        except Exception as e:
            print(f"  (could not send notification: {e})")
    return HALTED


def resume(client):
    """Clear a halt. Intended for a human, exposed for the self-test."""
    return set_state(client, RUNNING, "")


def get_stake(client, default=10.0):
    try:
        v = _tab(client).acell(f"B{_ROW['Stake $']}").value
        return float(str(v).replace("$", "").strip()) or default
    except Exception:
        return default


def fetch_balance():
    """Account balance in dollars, or None. Read-only: a signed GET."""
    import predictor as P
    hdrs = P._kalshi_headers("GET", "/trade-api/v2/portfolio/balance")
    if not hdrs:
        return None
    try:
        r = requests.get(f"{P.KALSHI_BASE}/portfolio/balance",
                         headers=hdrs, timeout=10)
        if not r.ok:
            print(f"  [balance] HTTP {r.status_code} {r.text[:80]}")
            return None
        cents = r.json().get("balance")
        return cents / 100.0 if isinstance(cents, (int, float)) else None
    except Exception as e:
        print(f"  [balance] {e}")
        return None


def record_balance(client, bal):
    try:
        _tab(client).update(f"B{_ROW['Last balance $']}",
                            [[f"{bal:.2f}" if bal is not None else ""]])
    except Exception:
        pass


def check_window(client, n_signals, stake=None, balance=None):
    """Can this whole window be funded? Returns (ok, needed, balance, why).

    Does NOT halt on its own -- the caller decides, so this stays usable as an
    observation in dry-run mode where halting would be pointless noise.
    """
    stake = stake if stake is not None else get_stake(client)
    needed = round(n_signals * stake, 2)
    bal = balance if balance is not None else fetch_balance()
    if bal is None:
        return False, needed, None, "could not read the account balance"
    if bal + 1e-9 < needed:
        return (False, needed, bal,
                f"{n_signals} signal(s) need ${needed:.2f} but the balance is "
                f"${bal:.2f}")
    return True, needed, bal, ""


# --- self-test -----------------------------------------------------------
# Exercises the whole mechanism against the real sheet WITHOUT any betting
# code existing yet, so the brakes are proven before the engine is built.

def selftest():
    import predictor as P
    client = P._get_client()
    print("=" * 62)
    print("CONTROL SELF-TEST -- no orders, no money, betting code not built")
    print("=" * 62)

    state, reason = get_state(client)
    print(f"  starting state          : {state} {('(' + reason + ')') if reason else ''}")

    bal = fetch_balance()
    print(f"  balance                 : {'unreadable' if bal is None else f'${bal:.2f}'}")
    record_balance(client, bal)

    stake = get_stake(client)
    print(f"  stake from the tab      : ${stake:.2f}")
    for n in (1, 3, 5):
        ok, needed, b, why = check_window(client, n, stake=stake, balance=bal)
        verdict = "fundable" if ok else f"NOT fundable -- {why}"
        print(f"  {n} signal(s) (${needed:6.2f}) : {verdict}")

    print("-" * 62)
    print("  exercising the halt path (this sends a real notification)")
    halt(client, "Control self-test -- not a real halt.")
    s2, r2 = get_state(client)
    print(f"  state after halt        : {s2} ({r2})")
    if s2 != HALTED:
        print("  FAILED: halt did not stick.")
        return 1

    resume(client)
    s3, _ = get_state(client)
    print(f"  state after resume      : {s3}")
    if s3 != RUNNING:
        print("  FAILED: resume did not stick.")
        return 1

    print("=" * 62)
    print("RESULT: the halt switch works, and it fails closed if the tab "
          "cannot be read.")
    return 0


def set_stake(dollars):
    """Write the per-bet stake. The tab stays the single source of truth --
    this just saves typing it by hand, and prints what it wrote."""
    import predictor as P
    client = P._get_client()
    ws = _tab(client)
    ws.update(f"B{_ROW['Stake $']}", [[f"{float(dollars):.2f}"]])
    print(f"  stake set to ${float(dollars):.2f} per bet")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--stake":
        sys.exit(set_stake(sys.argv[2]))
    sys.exit(selftest())
