"""Follow a cohort of fresh launches trade by trade. Read-only. No wallet.

WHAT THIS ADDS THAT pumpscan CANNOT. pumpscan records that a token was
launched and, later, that one graduated. It records no prices, so it cannot
answer the question that decides whether any of this is tradeable:

    HOW LONG SHOULD A POSITION BE HELD?

That is an exit-horizon question and it needs a price PATH, not two
endpoints. subscribeTokenTrade on the same websocket streams every trade
for named mints, which is a complete path rather than a sampled one.

THE COHORT DESIGN, and why it is a cohort rather than a watchlist. The
session takes the first COHORT_SIZE launches it sees and follows those, and
only those, for the rest of the run. Every member therefore has a known age
at every observation and all of them are observed over the same elapsed
window. A rolling watchlist would mix tokens of different ages and make
"return after twenty minutes" mean different things in different rows,
which is the sort of quiet confound this project keeps finding after the
fact rather than before.

THE SAMPLE IS THE FIRST N, NOT A RANDOM N, and that is a real limitation
stated here rather than buried: launches are not uniform through the hour,
so a cohort drawn at the top of a session is not a random draw from the
day. It is unbiased with respect to the thing being measured -- nothing
about a token's later price influences whether it was first -- but the
cohorts should be compared across sessions before any of it is pooled.

WHY A RAW COLUMN. Three bugs in three days came from writing a reader
against field names that seemed obvious and were wrong. The documented
shape of a trade event is not something this file has seen, so every known
field is extracted defensively AND the whole message is stored as JSON. If
a guess here is wrong, the data is still on disk and the analysis can be
fixed without re-collecting a week.

PRICE IS DERIVED, NOT QUOTED. vSolInBondingCurve / vTokensInBondingCurve is
the bonding curve's implied price after the trade -- arithmetic on two
recorded fields. Left blank when either is missing, never defaulted to
zero, because a zero would be averaged later as if it were a price.

No wallet, no private key, no order, and no possibility of one.
"""
import asyncio
import json
import os
import time
from collections import Counter, defaultdict

URL = os.environ.get("PUMPWS_URL") or "wss://pumpportal.fun/api/data"
SECONDS = int(os.environ.get("PUMPPATH_SECONDS") or 1800)
COHORT = int(os.environ.get("PUMPPATH_COHORT") or 40)
FLUSH_EVERY = int(os.environ.get("PUMPPATH_FLUSH") or 45)
MAX_ROWS = int(os.environ.get("PUMPPATH_MAX_ROWS") or 8000)
SHOW_RAW = int(os.environ.get("PUMPPATH_SHOW_RAW") or 3)
TRADE_TAB = os.environ.get("PUMPPATH_TAB") or "Pump Trades"
COHORT_TAB = os.environ.get("PUMPPATH_COHORT_TAB") or "Pump Cohort"

TRADE_HEADERS = [
    "Seen UTC", "Elapsed s", "Mint", "Side", "Trader",
    "SOL Amount", "Token Amount", "Market Cap SOL",
    "vSol", "vTokens", "Price SOL", "Signature", "Raw",
]
COHORT_HEADERS = [
    "Launched UTC", "Mint", "Symbol", "Name", "Creator",
    "Creator Buy SOL", "Launch Price SOL", "Session",
]


def f(v, d=None):
    try:
        x = float(v)
        return x if x == x else d
    except (TypeError, ValueError):
        return d


def sheet():
    import gspread
    from google.oauth2.service_account import Credentials
    raw = os.environ.get("GOOGLE_CREDENTIALS")
    if not raw:
        raise SystemExit("  no GOOGLE_CREDENTIALS")
    creds = Credentials.from_service_account_info(
        json.loads(raw),
        scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(creds)
    sid = os.environ.get("MEME_SHEET_ID")
    if not sid:
        import predictor as P
        sid = P.SPREADSHEET_ID
    return gc.open_by_key(sid)


def tab(sh, title, headers):
    try:
        ws = sh.worksheet(title)
    except Exception:
        ws = sh.add_worksheet(title=title, rows=20000, cols=len(headers))
        ws.update(values=[headers], range_name="A1")
        print(f"  created tab '{title}'")
        return ws
    have = ws.row_values(1)
    if have and have[:3] != headers[:3]:
        raise SystemExit(f"  tab '{title}' header differs: {have[:5]}")
    if not have:
        ws.update(values=[headers], range_name="A1")
    return ws


def price_of(d):
    vs, vt = f(d.get("vSolInBondingCurve")), f(d.get("vTokensInBondingCurve"))
    return (vs / vt) if (vs and vt) else ""


def trade_row(d, now, elapsed):
    vs, vt = f(d.get("vSolInBondingCurve")), f(d.get("vTokensInBondingCurve"))
    raw = json.dumps(d, default=str)
    return [
        now, round(elapsed, 1), d.get("mint", ""),
        d.get("txType", ""), d.get("traderPublicKey", ""),
        f(d.get("solAmount"), ""), f(d.get("tokenAmount"), ""),
        f(d.get("marketCapSol"), ""),
        vs if vs is not None else "", vt if vt is not None else "",
        price_of(d), d.get("signature", ""),
        raw[:4000],          # the whole message, so a wrong guess above
                             # costs an analysis fix and not a re-collection
    ]


async def run():
    import websockets
    sh = sheet()
    ws_tr = tab(sh, TRADE_TAB, TRADE_HEADERS)
    ws_co = tab(sh, COHORT_TAB, COHORT_HEADERS)

    session = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    born = {}                      # mint -> unix time first seen
    cohort_rows, trade_rows = [], []
    kinds = Counter()
    per_mint = Counter()
    shown_raw = 0
    written = 0
    t_end = time.time() + SECONDS

    def flush():
        nonlocal cohort_rows, trade_rows, written
        if cohort_rows:
            ws_co.append_rows(cohort_rows, value_input_option="USER_ENTERED",
                              table_range="A1")
            cohort_rows = []
        if trade_rows:
            ws_tr.append_rows(trade_rows, value_input_option="USER_ENTERED",
                              table_range="A1")
            written += len(trade_rows)
            trade_rows = []

    print(f"  connecting to {URL}")
    async with websockets.connect(URL, open_timeout=20,
                                  max_size=8_000_000) as sock:
        await sock.send(json.dumps({"method": "subscribeNewToken"}))
        print(f"  collecting a cohort of {COHORT} launch(es), then following "
              f"their trades for the rest of {SECONDS}s")
        last_flush = time.time()

        while time.time() < t_end:
            try:
                raw = await asyncio.wait_for(
                    sock.recv(), timeout=max(1.0, t_end - time.time()))
            except asyncio.TimeoutError:
                break
            except Exception as e:
                print(f"  recv ended: {str(e)[:120]}")
                break
            try:
                d = json.loads(raw)
            except Exception:
                continue
            if not isinstance(d, dict):
                continue

            tx = str(d.get("txType") or "")
            kinds[tx or "other"] += 1
            now_s = time.time()
            now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now_s))

            if tx == "create":
                if len(born) >= COHORT or d.get("mint") in born:
                    continue
                mint = d.get("mint")
                if not mint:
                    continue
                born[mint] = now_s
                cohort_rows.append([
                    now, mint, d.get("symbol", ""), d.get("name", ""),
                    d.get("traderPublicKey", ""), f(d.get("solAmount"), ""),
                    price_of(d), session])
                # Subscribe as soon as it is in the cohort, so the path
                # starts at the launch rather than at the end of recruiting.
                await sock.send(json.dumps(
                    {"method": "subscribeTokenTrade", "keys": [mint]}))
                if len(born) == COHORT:
                    print(f"  cohort full at {len(born)} mint(s); "
                          f"following trades only from here")
                continue

            mint = d.get("mint")
            if mint not in born:
                continue
            if written + len(trade_rows) >= MAX_ROWS:
                continue

            # THE SCHEMA OF A TRADE EVENT HAS NOT BEEN SEEN BY THIS FILE.
            # Print the first few verbatim so the next version is written
            # against what arrives rather than what was assumed.
            if shown_raw < SHOW_RAW:
                shown_raw += 1
                print(f"\n  --- trade event sample #{shown_raw} "
                      f"({len(d)} keys)")
                for k in sorted(d):
                    print(f"      {k:<26} "
                          f"{json.dumps(d[k], default=str)[:76]}")
                print()

            per_mint[mint] += 1
            trade_rows.append(trade_row(d, now, now_s - born[mint]))

            if time.time() - last_flush >= FLUSH_EVERY:
                flush()
                last_flush = time.time()
                print(f"  ... {written} trade(s) across "
                      f"{len(per_mint)} of {len(born)} cohort mint(s)")

    flush()
    print(f"\n  DONE. cohort {len(born)}, {written} trade row(s) recorded.")
    print(f"  message kinds: {dict(kinds)}")
    traded = len(per_mint)
    if born:
        print(f"\n  {traded} of {len(born)} cohort token(s) traded at all "
              f"({100.0*traded/len(born):.0f}%)")
        silent = len(born) - traded
        if silent:
            print(f"  {silent} never traded after launch in this window --")
            print(f"  which is itself a result, and the one a price-only")
            print(f"  feed would never show.")
    if per_mint:
        print("\n  busiest cohort members:")
        for m, c in per_mint.most_common(5):
            print(f"    {c:>5} trade(s)  {m}")
    print("\n  NO VERDICT IS COMPUTED HERE. Exit horizons get swept later,")
    print("  on pooled cohorts, each judged against its own cost -- the same")
    print("  bar the Kalshi guards were held to.")
    return 0


def main():
    print("=" * 96)
    print("PUMP.FUN TRADE PATH RECORDER -- one cohort, every trade")
    print("  No wallet, no private key, no order. Records only.")
    print("=" * 96)
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
