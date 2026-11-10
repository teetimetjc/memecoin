"""Price paths for a cohort of launches, read off the chain. No wallet.

WHY THIS WAS REWRITTEN. The first version subscribed to pumpportal's
per-token trade stream and recorded nothing, three runs running, because
that method is paywalled -- the refusal arrived as a plain-English
acknowledgement and two successive diagnostics of mine discarded it before
printing. The fix is not a different subscription; it is a different
source.

pricesrc verified the alternative end to end: the pump.fun bonding curve's
own account, read from a public Solana RPC with no key at all, decodes to
reserves that match the websocket's figures to nine significant figures on
a live token. Price is therefore computable from the chain for any token,
from launch onward -- including the first minutes, which DexScreener cannot
see because no pool exists yet.

THE SHAPE OF THE RUN:

  1. take the first COHORT launches of the session from the FREE websocket
     subscription, recording each one's bondingCurveKey;
  2. poll every cohort member's curve account together, in ONE
     getMultipleAccounts call, every POLL_EVERY seconds;
  3. write a row only when a token crosses one of the AGE TARGETS below.

STEP 3 IS WHAT KEEPS THIS SANE. Polling every fifteen seconds for six
hours would be ~1,400 samples a token and tens of thousands of rows a day
into a sheet that already carries the Kalshi collector. The exit-horizon
question does not need that: it needs the price at a known set of ages.
So the poll is frequent -- so a target is hit accurately -- and the WRITE
is sparse. Roughly fifteen rows per token, and the actual age at which each
sample landed is recorded beside the target, because a target of 60s hit at
73s is a different observation and pretending otherwise would quietly bias
every horizon.

ONE RPC CALL PER POLL REGARDLESS OF COHORT SIZE. getMultipleAccounts takes
up to 100 keys, so a cohort of forty costs the same as a cohort of one. A
public RPC is rate limited and this is what makes the run survivable; 429s
are backed off rather than hammered.

COMPLETION IS RECORDED. When the curve's `complete` flag flips the token
has graduated, which is the natural end of a bonding-curve price path and
an exit the analysis will want to know about.

No API key, no wallet, no private key, no order, and no possibility of one.
"""
import asyncio
import base64
import json
import os
import struct
import time
from collections import Counter

WS = os.environ.get("PUMPWS_URL") or "wss://pumpportal.fun/api/data"
RPCS = [u.strip() for u in (os.environ.get("SOLANA_RPCS") or
        "https://api.mainnet-beta.solana.com").split(",") if u.strip()]
SECONDS = int(os.environ.get("PUMPPATH_SECONDS") or 20700)
COHORT = int(os.environ.get("PUMPPATH_COHORT") or 40)
POLL_EVERY = int(os.environ.get("PUMPPATH_POLL") or 15)
RECRUIT_S = int(os.environ.get("PUMPPATH_RECRUIT") or 600)
FLUSH_EVERY = int(os.environ.get("PUMPPATH_FLUSH") or 120)
PRICE_TAB = os.environ.get("PUMPPATH_TAB") or "Pump Prices"
COHORT_TAB = os.environ.get("PUMPPATH_COHORT_TAB") or "Pump Cohort"

# The ages, in seconds, at which a price is recorded. Dense early because
# that is where a memecoin's life happens, sparse later because nothing is
# learned by sampling a dead token every minute for four hours.
TARGETS = [int(x) for x in (os.environ.get("PUMPPATH_TARGETS") or
           "30,60,120,180,300,600,900,1800,2700,3600,5400,7200,10800,14400,"
           "18000").split(",") if x.strip()]

LAMPORTS = 1_000_000_000
TOKEN_DEC = 10 ** 6
CURVE_OWNER = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

PRICE_HEADERS = [
    "Seen UTC", "Mint", "Target s", "Actual age s", "Price SOL",
    "vSol", "vTokens", "Real SOL", "Real Tokens", "Complete",
    "Launch Price SOL", "X vs Launch", "Session",
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
    sid = os.environ.get("MEME_SHEET_ID")
    if not sid:
        import predictor as P
        sid = P.SPREADSHEET_ID
    return gspread.authorize(creds).open_by_key(sid)


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


def decode_curve(blob):
    """Reserves from the curve account. Layout verified by pricesrc against
    the websocket's own figures on a live token -- 0.000% apart."""
    if len(blob) < 49:
        return None
    vt, vs, rt, rs, _sup = struct.unpack_from("<QQQQQ", blob, 8)
    return dict(vt=vt / TOKEN_DEC, vs=vs / LAMPORTS,
                rt=rt / TOKEN_DEC, rs=rs / LAMPORTS,
                complete=bool(blob[48]))


def rpc_accounts(url, keys, session):
    """getMultipleAccounts -> {key: decoded}. One call for the whole cohort."""
    try:
        r = session.post(url, timeout=25,
                         headers={"content-type": "application/json"},
                         json={"jsonrpc": "2.0", "id": 1,
                               "method": "getMultipleAccounts",
                               "params": [keys, {"encoding": "base64",
                                                 "commitment": "processed"}]})
    except Exception as e:
        return None, str(e)[:120]
    if r.status_code == 429:
        return None, "429"
    if not r.ok:
        return None, f"HTTP {r.status_code}"
    try:
        j = r.json()
    except Exception as e:
        return None, f"bad json {str(e)[:80]}"
    if "error" in j:
        return None, f"rpc {json.dumps(j['error'])[:120]}"
    vals = ((j.get("result") or {}).get("value")) or []
    # LENGTH MUST MATCH OR THE ZIP MISALIGNS EVERY KEY AFTER THE GAP.
    # getMultipleAccounts returns one slot per key, nulls included; a short
    # array would silently attribute one token's reserves to another.
    if len(vals) != len(keys):
        return None, f"length {len(vals)} != {len(keys)}"
    out, foreign = {}, 0
    for k, v in zip(keys, vals):
        if not v:
            continue
        # THE OWNER CHECK, WHICH I DEFINED AND THEN FAILED TO USE. The
        # audit found 30-second samples holding 1.853 SOL and 929 SOL --
        # both impossible for a curve that starts at 30 and graduates near
        # 85 -- because any account at all was being decoded as if it were
        # a bonding curve. An account not owned by the pump.fun program is
        # not a curve, and its bytes are not reserves.
        if v.get("owner") != CURVE_OWNER:
            foreign += 1
            continue
        data = v.get("data")
        if not isinstance(data, list) or not data:
            continue
        try:
            dec = decode_curve(base64.b64decode(data[0]))
        except Exception:
            dec = None
        # A curve's virtual SOL reserve starts at 30 and only rises. Below
        # that is arithmetically impossible, so it is refused here rather
        # than written and caught by an audit later.
        if dec and dec["vs"] >= 29.9 and dec["vt"] > 0:
            out[k] = dec
        elif dec:
            foreign += 1
    return out, (f"skipped {foreign} non-curve account(s)" if foreign
                 and not out else None)


async def recruit(cohort, seconds):
    """Fill the cohort from the free websocket subscription."""
    import websockets
    async with websockets.connect(WS, open_timeout=20) as s:
        await s.send(json.dumps({"method": "subscribeNewToken"}))
        end = time.time() + seconds
        while time.time() < end and len(cohort) < COHORT:
            try:
                raw = await asyncio.wait_for(
                    s.recv(), timeout=max(1, end - time.time()))
            except asyncio.TimeoutError:
                break
            except Exception:
                break
            try:
                d = json.loads(raw)
            except Exception:
                continue
            if not isinstance(d, dict) or d.get("txType") != "create":
                continue
            mint, curve = d.get("mint"), d.get("bondingCurveKey")
            if not mint or not curve or mint in cohort:
                continue
            vs, vt = f(d.get("vSolInBondingCurve")), f(d.get("vTokensInBondingCurve"))
            cohort[mint] = dict(
                curve=curve, born=time.time(),
                symbol=d.get("symbol", ""), name=d.get("name", ""),
                creator=d.get("traderPublicKey", ""),
                buy_sol=f(d.get("solAmount"), ""),
                launch_px=(vs / vt) if (vs and vt) else None,
                done=set())
    return cohort


def main():
    import requests
    print("=" * 100)
    print("PUMP.FUN PRICE PATHS -- read off the chain, no API key")
    print(f"  cohort {COHORT}, poll every {POLL_EVERY}s, "
          f"write at ages {TARGETS[:6]}... ({len(TARGETS)} targets)")
    print("  No wallet, no private key, no order.")
    print("=" * 100)

    sh = sheet()
    ws_px = tab(sh, PRICE_TAB, PRICE_HEADERS)
    ws_co = tab(sh, COHORT_TAB, COHORT_HEADERS)
    session_id = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    print(f"\n  recruiting up to {COHORT} launch(es) "
          f"(max {RECRUIT_S}s) ...")
    cohort = asyncio.run(recruit({}, RECRUIT_S))
    if not cohort:
        print("  no launches captured; nothing to follow.")
        return 1
    print(f"  cohort of {len(cohort)}")

    ws_co.append_rows(
        [[time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(v["born"])),
          m, v["symbol"], v["name"], v["creator"], v["buy_sol"],
          v["launch_px"] if v["launch_px"] else "", session_id]
         for m, v in cohort.items()],
        value_input_option="USER_ENTERED", table_range="A1")

    keys = [v["curve"] for v in cohort.values()]
    by_curve = {v["curve"]: m for m, v in cohort.items()}
    rows, written, errs = [], 0, Counter()
    rpc_i, backoff = 0, 0.0
    t_end = time.time() + SECONDS
    last_flush = time.time()
    sess = requests.Session()

    def flush():
        nonlocal rows, written
        if rows:
            ws_px.append_rows(rows, value_input_option="USER_ENTERED",
                              table_range="A1")
            written += len(rows)
            rows = []

    print(f"  polling for {SECONDS}s ...")
    while time.time() < t_end:
        url = RPCS[rpc_i % len(RPCS)]
        got, err = rpc_accounts(url, keys, sess)
        if err:
            errs[err[:24]] += 1
            rpc_i += 1
            backoff = min(30.0, (backoff or POLL_EVERY) * 1.7)
            time.sleep(backoff)
            continue
        backoff = 0.0
        now = time.time()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))

        for curve, dec in got.items():
            mint = by_curve.get(curve)
            if not mint:
                continue
            v = cohort[mint]
            age = now - v["born"]
            due = [t for t in TARGETS if t <= age and t not in v["done"]]
            if not due:
                continue
            # Only the newest target is written; if the loop fell behind,
            # older targets are marked done rather than filled with a price
            # from the wrong moment.
            tgt = max(due)
            for t in due:
                v["done"].add(t)
            px = (dec["vs"] / dec["vt"]) if dec["vt"] else ""
            lp = v["launch_px"]
            rows.append([
                stamp, mint, tgt, round(age, 1), px,
                dec["vs"], dec["vt"], dec["rs"], dec["rt"],
                dec["complete"], lp if lp else "",
                (px / lp) if (px and lp) else "", session_id])

        if time.time() - last_flush >= FLUSH_EVERY:
            flush()
            last_flush = time.time()
            done_all = sum(1 for v in cohort.values()
                           if len(v["done"]) >= len(TARGETS))
            print(f"  ... {written} price row(s); {done_all}/{len(cohort)} "
                  f"token(s) fully sampled")
            if done_all == len(cohort):
                print("  every token has reached its last target; stopping.")
                break
        time.sleep(POLL_EVERY)

    flush()
    print(f"\n  DONE. {written} price row(s) for {len(cohort)} token(s).")
    if errs:
        print(f"  rpc problems: {dict(errs)}")
    grad = sum(1 for v in cohort.values() if v.get("graduated"))
    samples = Counter(len(v["done"]) for v in cohort.values())
    print(f"  targets reached per token: {dict(sorted(samples.items()))}")
    print("\n  NO VERDICT HERE. Exit horizons get swept later, each against")
    print("  its own cost, on pooled cohorts -- the same bar the Kalshi")
    print("  guards were held to.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
