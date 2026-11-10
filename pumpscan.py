"""Record every pump.fun launch and every graduation. Read-only. No wallet.

WHAT THIS IS FOR, and why it measures a base rate before anything else.

The probe established that pumpportal's websocket delivers launch-time
events a datacenter can actually reach, with fields pump.fun's own HTTP API
would not serve: the creator's wallet, how much of their own token they
bought, and the bonding-curve reserves at creation. It also delivers a
`migrate` event when a token graduates to a real pool.

So the first question is not "which token should be bought". It is:

    OF THE TOKENS LAUNCHED, WHAT FRACTION EVER GRADUATE?

Everything else depends on that number and it is cheap to measure
correctly. Published figures exist; this project's standing rule is that a
number measured here beats a number read elsewhere, because the sampling is
ours and the definition is ours.

THE RATE IS THE DESIGN CONSTRAINT. The probe saw 7 creates a minute, which
is roughly ten thousand a day. Nothing can track all of them, so what the
scanner KEEPS is the whole product, and the filter is therefore explicit,
configurable and logged rather than buried in a conditional.

WHAT IS RECORDED, verbatim from the feed, no interpretation:

    creates   mint, symbol, name, creator wallet, the creator's own initial
              buy in both tokens and SOL, the bonding curve reserves, and
              the implied launch price (vSol/vTokens, which is arithmetic on
              two recorded fields rather than a quoted price)
    migrates  mint and destination pool

A create and a migrate are joined by mint, later, by pumpjoin. This file
only records; it computes no verdict, because a session that both collects
and concludes tends to conclude from whatever it happened to collect.

CREATOR TRACKING IS THE POINT OF KEEPING traderPublicKey. A wallet that
launches many tokens is the most checkable rug signal available before a
purchase, and it cannot be seen from DexScreener at all. Counting them
needs the full log, which is why creates are written even when nothing
about them looks interesting.

WHAT THIS CANNOT ANSWER, said here rather than discovered later: receiving
an event quickly is not the same as being able to act on it. This runner is
hundreds of milliseconds from the chain and the bots that profit from
launches are co-located and paying priority fees. A signal found here would
still have to survive that gap, and nothing in this file tests it.

NO CREDENTIALS BEYOND THE SHEET. No wallet, no private key, no order, no
possibility of one.
"""
import asyncio
import json
import os
import time
from collections import Counter

URL = os.environ.get("PUMPWS_URL") or "wss://pumpportal.fun/api/data"
SECONDS = int(os.environ.get("PUMPSCAN_SECONDS") or 1800)
FLUSH_EVERY = int(os.environ.get("PUMPSCAN_FLUSH") or 60)
SHEET_TAB = os.environ.get("PUMPSCAN_TAB") or "Pump Launches"
MIG_TAB = os.environ.get("PUMPSCAN_MIG_TAB") or "Pump Migrations"

# THE FILTER, stated rather than hidden. Empty means keep everything, which
# is the right default while the base rate is unknown: filtering first and
# measuring second would measure the filter.
MIN_SOL = float(os.environ.get("PUMPSCAN_MIN_SOL") or 0.0)
MAX_ROWS = int(os.environ.get("PUMPSCAN_MAX_ROWS") or 6000)

CREATE_HEADERS = [
    "Seen UTC", "Mint", "Symbol", "Name", "Creator",
    "Creator Buy SOL", "Creator Buy Tokens",
    "Market Cap SOL", "vSol", "vTokens", "Launch Price SOL",
    "Pool", "Mayhem", "Signature",
]
MIG_HEADERS = ["Seen UTC", "Mint", "Pool", "Signature"]


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
    sid = os.environ.get("MEME_SHEET_ID") or os.environ.get("SPREADSHEET_ID")
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
    # NEVER REWRITE AN EXISTING HEADER to a different shape -- this tab may
    # already hold rows written by an earlier version, and shifting columns
    # silently changes the meaning of what is already on disk.
    have = ws.row_values(1)
    if have and have[:3] != headers[:3]:
        raise SystemExit(f"  tab '{title}' has a different header: {have[:5]}")
    if not have:
        ws.update(values=[headers], range_name="A1")
    return ws


def create_row(d, now):
    vs, vt = f(d.get("vSolInBondingCurve")), f(d.get("vTokensInBondingCurve"))
    # Launch price is arithmetic on two recorded fields, not a quote. If
    # either is missing it is left blank rather than defaulted to zero,
    # because a zero would be averaged later as if it were a price.
    px = (vs / vt) if (vs and vt) else ""
    return [
        now, d.get("mint", ""), d.get("symbol", ""), d.get("name", ""),
        d.get("traderPublicKey", ""),
        f(d.get("solAmount"), ""), f(d.get("initialBuy"), ""),
        f(d.get("marketCapSol"), ""), vs if vs is not None else "",
        vt if vt is not None else "", px,
        d.get("pool", ""), bool(d.get("is_mayhem_mode")),
        d.get("signature", ""),
    ]


async def run():
    import websockets
    sh = sheet()
    ws_create = tab(sh, SHEET_TAB, CREATE_HEADERS)
    ws_mig = tab(sh, MIG_TAB, MIG_HEADERS)

    creates, migs = [], []
    kinds = Counter()
    creators = Counter()
    written = 0
    skipped_small = 0
    t_end = time.time() + SECONDS
    last_flush = time.time()

    def flush():
        nonlocal creates, migs, written
        if creates:
            ws_create.append_rows(creates, value_input_option="USER_ENTERED",
                                  table_range="A1")
            written += len(creates)
            creates = []
        if migs:
            ws_mig.append_rows(migs, value_input_option="USER_ENTERED",
                               table_range="A1")
            migs = []

    print(f"  connecting to {URL}")
    async with websockets.connect(URL, open_timeout=20,
                                  max_size=4_000_000) as sock:
        for m in ("subscribeNewToken", "subscribeMigration"):
            await sock.send(json.dumps({"method": m}))
        print(f"  subscribed; recording for {SECONDS}s into "
              f"'{SHEET_TAB}' and '{MIG_TAB}'")

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

            tx = d.get("txType") or ""
            kinds[tx or "other"] += 1
            now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

            if tx == "create":
                sol = f(d.get("solAmount"), 0.0) or 0.0
                if MIN_SOL and sol < MIN_SOL:
                    skipped_small += 1
                    continue
                if written + len(creates) >= MAX_ROWS:
                    continue
                creators[d.get("traderPublicKey", "")] += 1
                creates.append(create_row(d, now))
            elif tx == "migrate":
                migs.append([now, d.get("mint", ""), d.get("pool", ""),
                             d.get("signature", "")])

            if time.time() - last_flush >= FLUSH_EVERY:
                flush()
                last_flush = time.time()
                print(f"  ... {written} create(s) written, "
                      f"{kinds.get('migrate', 0)} migration(s) seen")

    flush()
    print(f"\n  DONE. {written} create(s), {kinds.get('migrate', 0)} "
          f"migration(s) recorded.")
    if skipped_small:
        print(f"  {skipped_small} create(s) skipped by "
              f"PUMPSCAN_MIN_SOL={MIN_SOL}")
    print(f"  message kinds: {dict(kinds)}")
    rep = [(k, c) for k, c in creators.most_common(8) if c > 1]
    if rep:
        print("\n  WALLETS THAT LAUNCHED MORE THAN ONCE IN THIS SESSION:")
        for k, c in rep:
            print(f"    {c:>4}x  {k}")
        print("  A repeat launcher is the most checkable pre-purchase signal")
        print("  available, and it is invisible from DexScreener.")
    print("\n  NOTE: a migration seen here need not belong to a create seen")
    print("  here -- it may be an older token graduating. The ratio of these")
    print("  two counts is NOT the graduation rate; pumpjoin computes that")
    print("  by matching mints with a proper elapsed-time window.")
    return 0


def main():
    print("=" * 96)
    print("PUMP.FUN LAUNCH RECORDER -- records only, concludes nothing")
    print("  No wallet, no private key, no order. Writes two sheet tabs.")
    print("=" * 96)
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
