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
import os
import sys
import time
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
    # APPENDED AT THE END ON PURPOSE. _ROW is positional and
    # record_balance writes to B{_ROW['Last balance $']}, so a row inserted
    # anywhere above would move the cell every other function addresses and
    # start writing the balance over something else.
    ["Skip series", "",
     "Comma-separated series to NOT bet, e.g. KXXRP15M. Blank = bet all. "
     "Read every window, so a change here takes effect in minutes."],
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


def get_skip_series(client, default=frozenset()):
    """Series not to bet, read from the Control tab EVERY window.

    WHY THIS IS NOT A CONSTANT IN live.py. It was, and on 2026-11-08 that
    cost three unwanted bets. A favlive session runs for 5h45m and
    actions/checkout happens once, at the start, so a change pushed to
    live.py is invisible to the process already running -- for up to nearly
    six hours. XRP was switched off in code at 14:04 and the session that
    had started at 12:22 went on betting it until it was cancelled by hand.

    A value on the Control tab has none of that latency: the tab is already
    read every window for the state and the stake, so a coin switched off
    here stops being bet within one fifteen-minute cycle, from a phone,
    with no deploy and no session restart.

    FAILS TOWARD THE CALLER'S DEFAULT, not toward betting everything. An
    unreadable tab must never silently re-enable a series someone turned
    off; get_state already refuses to bet at all in that case, and this
    keeps whatever the code was configured with rather than widening it.
    """
    try:
        v = _tab(client).acell(f"B{_ROW['Skip series']}").value
    except Exception as e:
        print(f"  [control] could not read Skip series ({str(e)[:60]}); "
              f"keeping the configured default {sorted(default)}")
        return frozenset(default)
    if v is None or not str(v).strip():
        return frozenset(default)
    return frozenset(s.strip().upper() for s in str(v).split(",") if s.strip())


def fetch_balance():
    """Account balance in dollars, or None. Read-only: a signed GET."""
    d = _balance_json()
    if not d:
        return None
    cents = d.get("balance")
    return cents / 100.0 if isinstance(cents, (int, float)) else None


def record_balance(client, bal):
    try:
        _tab(client).update(f"B{_ROW['Last balance $']}",
                            [[f"{bal:.2f}" if bal is not None else ""]])
    except Exception:
        pass


# A refusal that means "I could not check", as opposed to "I checked and
# there is not enough money". The two must not be treated alike: a shortfall
# needs a human and should halt, while an unreadable balance is a transient
# API condition that the next window will probably read fine. Halting on the
# second one would stop all betting on a single 429.
TRANSIENT = "TRANSIENT: "

# How hard to try before calling a balance read unreadable. Kalshi returns
# 429 on this endpoint under load -- it did so twice in a row on the
# transfer read-backs on 2026-11-07, reporting a transfer that had in fact
# succeeded as unconfirmed. One retry would have cleared both.
BAL_TRIES = int(os.environ.get("BALANCE_READ_TRIES") or 3)


def _balance_json():
    """The raw balance payload, retried on transient failures, or None.

    RETRIES ONLY WHAT IS WORTH RETRYING. A 429 or a 5xx is the server
    saying "not now"; a 401 is the key being wrong and will say the same
    thing however many times it is asked.
    """
    import predictor as P
    for attempt in range(BAL_TRIES):
        hdrs = P._kalshi_headers("GET", "/trade-api/v2/portfolio/balance")
        if not hdrs:
            return None
        try:
            r = requests.get(f"{P.KALSHI_BASE}/portfolio/balance",
                             headers=hdrs, timeout=10)
            if r.ok:
                return r.json()
            if r.status_code != 429 and r.status_code < 500:
                print(f"  [balance] HTTP {r.status_code} -- not retried")
                return None
            print(f"  [balance] HTTP {r.status_code}, attempt "
                  f"{attempt + 1}/{BAL_TRIES}")
        except Exception as e:
            print(f"  [balance] {str(e)[:80]}, attempt "
                  f"{attempt + 1}/{BAL_TRIES}")
        if attempt + 1 < BAL_TRIES:
            time.sleep(0.6 * (2 ** attempt))
    return None


def shard_balances():
    """{exchange_index: dollars}, or None if the breakdown cannot be read.

    KALSHI SHARDED THE EXCHANGE. Each shard is a separate exchange instance
    with SEPARATE COLLATERAL, and an order is authorised against the balance
    on its own shard -- the account total has nothing to do with it.

    This is not theory. On 2026-11-04 at 03:20 UTC a ~$5 XRP order was
    refused for insufficient balance while this module's own check had just
    approved the window on a $99.29 total. All $99.29 sat on shard 0; every
    crypto series trades on shard 2, which held $0.00. Both numbers were
    right and they were measuring different things.
    """
    d = _balance_json()
    if not d:
        return None
    rows = d.get("balance_breakdown")
    if not rows:
        return None
    out = {}
    for row in rows:
        try:
            out[int(row.get("exchange_index"))] = float(row.get("balance"))
        except (TypeError, ValueError):
            continue
    return out or None


_SHARD_CACHE = {}


def shard_of(ticker):
    """The exchange_index a ticker trades on, or None. Cached per process.

    Cached because a window asks about the same handful of series every
    cycle and a market's shard does not move mid-session. None is NOT
    cached: an unreadable answer should be retried, not frozen in.
    """
    if not ticker:
        return None
    key = str(ticker).split("-")[0]           # the series prefix
    if key in _SHARD_CACHE:
        return _SHARD_CACHE[key]
    import predictor as P
    hdrs = P._kalshi_headers("GET", f"/trade-api/v2/markets/{ticker}")
    if not hdrs:
        return None
    try:
        r = requests.get(f"{P.KALSHI_BASE}/markets/{ticker}",
                         headers=hdrs, timeout=10)
        if not r.ok:
            return None
        idx = ((r.json() or {}).get("market") or {}).get("exchange_index")
        idx = int(idx) if idx is not None else None
    except Exception:
        return None
    if idx is not None:
        _SHARD_CACHE[key] = idx
    return idx


def check_window(client, n_signals, stake=None, balance=None, tickers=None):
    """Can this whole window be funded? Returns (ok, needed, balance, why).

    Does NOT halt on its own -- the caller decides, so this stays usable as an
    observation in dry-run mode where halting would be pointless noise.

    WITH `tickers`, THE CHECK IS PER SHARD, which is the only check that
    means anything: signals are grouped by the shard their market trades on
    and each group is tested against that shard's own collateral. A window
    spanning two shards must be funded on both, and the first shard that
    cannot cover its share fails the whole window -- partial funding is
    refused here for the same reason it always has been, because funding
    some signals and not others silently drops whichever the loop reaches
    last and biases the record in a way nothing can reconstruct later.

    WITHOUT `tickers`, OR IF THE BREAKDOWN CANNOT BE READ, it falls back to
    the old total-balance behaviour and says so. A fallback that blocked
    would stop all betting the first time one API call failed, which is a
    worse failure than the one being fixed; a fallback that is silent is how
    this bug survived in the first place.
    """
    stake = stake if stake is not None else get_stake(client)
    needed = round(n_signals * stake, 2)
    bal = balance if balance is not None else fetch_balance()
    if bal is None:
        # TRANSIENT for the same reason as the shard read below: a balance
        # that would not load is the API being busy, not the account being
        # empty, and it should cost one window rather than halt the bot.
        return (False, needed, None,
                TRANSIENT + f"could not read the account balance after "
                f"{BAL_TRIES} attempt(s)")

    if tickers:
        shards = shard_balances()
        if shards is None:
            # THE SECOND FAIL-OPEN, AND THE ONE THAT ACTUALLY FIRED.
            #
            # This used to print a warning and fall through to the total
            # test, which then passed -- the total is always healthy, that
            # is the entire point of the shard problem. On 2026-11-08 the
            # balance read came back 429, the breakdown was None, the
            # window was approved on a $91.51 total while shard 2 held
            # $3.85, two orders went to Kalshi and both were rejected for
            # insufficient_balance. The bot then HALTED on the rejection.
            #
            # So the old fallback did not keep the bot running; it swapped
            # a quiet skip for a halt and a phone notification. Refusing
            # here is strictly better, PROVIDED the refusal is marked
            # transient so the caller skips instead of halting.
            return (False, needed, bal,
                    TRANSIENT + "the per-shard balance could not be read "
                    f"after {BAL_TRIES} attempt(s), so funding cannot be "
                    "checked against the shard Kalshi authorises on. "
                    "Skipping this window rather than sending orders that "
                    "would be rejected.")
        else:
            by_shard = {}
            unknown = 0
            for t in tickers:
                idx = shard_of(t)
                if idx is None:
                    unknown += 1
                    continue
                by_shard[idx] = by_shard.get(idx, 0) + 1
            # AN UNRESOLVED SHARD IS NOT A PASS. The first version counted
            # these and carried on, so if EVERY lookup failed -- one rate
            # limit from Kalshi does it, and 429s were coming back the night
            # this was written -- by_shard was empty, the loop below never
            # ran, and the function returned True having checked nothing.
            # That is a fail-open in the brakes, and the same shape as the
            # bug it was written to fix: a check that returns a plausible
            # pass instead of refusing. A signal whose shard cannot be read
            # cannot be funded-checked, so it is refused.
            # MARKED TRANSIENT, because it is. An unreadable shard is an
            # API hiccup, not a funding problem, and halting the whole bot
            # on one 429 was never the intent of refusing here.
            if unknown:
                return (False, needed, bal,
                        TRANSIENT + f"{unknown} of {len(tickers)} signal(s) "
                        f"have an unreadable exchange shard, so their "
                        f"funding cannot be checked. Refusing rather than "
                        f"guessing -- an unchecked approval is how this "
                        f"went wrong before.")
            if not by_shard:
                return (False, needed, bal,
                        TRANSIENT + "no signal resolved to an exchange "
                        "shard; nothing could be checked")
            for idx in sorted(by_shard):
                want = round(by_shard[idx] * stake, 2)
                have = shards.get(idx, 0.0)
                if have + 1e-9 < want:
                    return (False, needed, bal,
                            f"{by_shard[idx]} signal(s) on exchange shard "
                            f"{idx} need ${want:.2f} but that shard holds "
                            f"${have:.2f} (account total ${bal:.2f} is on "
                            f"other shards and Kalshi will not use it)")
            # The per-shard test passed. The total still has to as well:
            # both are necessary, neither is sufficient, and falling through
            # rather than returning early is what makes that true.

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


def _cli_state(word):
    """Flip the switch from a workflow, for scheduling a start or a stop."""
    import predictor as P
    client = P._get_client()
    if word == RUNNING:
        resume(client)
        print(f"  state set to {RUNNING} -- betting may resume at the next window")
    else:
        halt(client, "Halted from the control workflow.")
    st, why = get_state(client)
    print(f"  confirmed: {st}{' -- ' + why if why else ''}")
    return 0 if st == word else 1


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--stake":
        sys.exit(set_stake(sys.argv[2]))
    if len(sys.argv) > 1 and sys.argv[1] == "--recent":
        # Dump the last few Live Bets rows. Theorising about which ticker was
        # sent is pointless when the row records it exactly.
        import predictor as P
        _sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
        try:
            _rows = _sh.worksheet("Live Bets").get_all_values()
        except Exception as _e:
            print(f"  no Live Bets tab yet: {_e}")
            sys.exit(0)
        print(f"  {len(_rows)-1} row(s); last 8:")
        for _r in _rows[-8:]:
            print("   ", " | ".join(str(_x)[:46] for _x in _r))
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "--placed":
        # How many orders have ever actually been placed. Printed as a bare
        # number so a shell can compare it before and after a window and know
        # whether to stop trying. Any failure prints nothing and exits non-zero
        # -- a caller looping "until one bet lands" must stop on an unreadable
        # count, never treat it as "no bet yet" and keep betting.
        import predictor as P
        _sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
        _rows = _sh.worksheet("Live Bets").get_all_values()
        _i = _rows[0].index("Status")
        print(sum(1 for _r in _rows[1:] if len(_r) > _i and _r[_i] == "PLACED"))
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "--status":
        # Read-only. Says what the switch is without touching it, so checking
        # cannot itself change anything or fire a notification.
        import predictor as P
        _c = P._get_client()
        _st, _why = get_state(_c)
        _b = fetch_balance()
        print(f"  state   : {_st}{' -- ' + _why if _why else ''}")
        print(f"  stake   : ${get_stake(_c):.2f}")
        print(f"  balance : {'unreadable' if _b is None else f'${_b:.2f}'}")
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] in ("--run", "--halt"):
        sys.exit(_cli_state(RUNNING if sys.argv[1] == "--run" else HALTED))
    sys.exit(selftest())
