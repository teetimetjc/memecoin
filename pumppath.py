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

# "Curve" IS APPENDED LAST, ON PURPOSE. The audit found 488 reserve
# readings above anything a bonding curve can hold, with the SAME value
# repeating across different mints -- 33,219.1 SOL on several at once. Two
# tokens cannot hold identical reserves, so one account was being read for
# many mints. Which account could not be determined from the data, because
# the address this price was read FROM was never written down: the row kept
# the mint and the numbers and threw away the only field that identifies
# their source. So it is recorded now, at the END of each header so every
# existing column keeps its position and nothing already on the tab changes
# meaning.
PRICE_HEADERS = [
    "Seen UTC", "Mint", "Target s", "Actual age s", "Price SOL",
    "vSol", "vTokens", "Real SOL", "Real Tokens", "Complete",
    "Launch Price SOL", "X vs Launch", "Session", "Curve",
]
COHORT_HEADERS = [
    "Launched UTC", "Mint", "Symbol", "Name", "Creator",
    "Creator Buy SOL", "Launch Price SOL", "Session", "Curve",
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
    # A COLUMN APPENDED AT THE END can be adopted safely, and refusing to
    # would mean a new tab or a lost diagnostic. Only an append is allowed:
    # the existing names must still match position for position, so nothing
    # already written changes meaning. Anything else is the error above.
    if len(have) < len(headers) and have == headers[:len(have)]:
        if ws.col_count < len(headers):
            ws.add_cols(len(headers) - ws.col_count)
        ws.update(values=[headers], range_name="A1")
        print(f"  tab '{title}': adopted {len(headers) - len(have)} new "
              f"column(s) at the end; existing columns unchanged")
    return ws


# EVERY HONEST PUMP.FUN CURVE HOLDS THE SAME CONSTANT PRODUCT. Each one is
# initialised at 30 virtual SOL against 1.073bn virtual tokens, and a
# constant-product AMM preserves that product as people trade, so
# vSol * vTokens is ~3.219e10 for any curve in a real trading state.
CURVE_K = 30.0 * 1.073e9
# How far from it a reading may sit and still be believed. Measured, not
# guessed: across 14 fresh launches every genuine curve read 3.219e+10 to
# four figures, while every template read 9.823e+11 -- a factor of 30 apart,
# so this band separates them with room to spare.
K_BAND = float(os.environ.get("PUMPPATH_K_BAND") or 0.25)

# THE PRODUCT ALONE IS NOT ENOUGH, and shipping it alone let 323 impossible
# readings through in the first five hours after the "fix".
#
# vSol * vTokens = k is a HYPERBOLA. It constrains how the two reserves
# relate, not where on the curve they sit, so a reading of 200 SOL against
# 161M tokens lies exactly on it and passes. It caught the template class
# -- vSol 925 against an untouched 1,073M tokens gives k 30x too large, and
# that class went from 182 bad rows to 0 -- and missed everything that is
# merely too far along.
#
# WHERE THE CURVE ENDS, arithmetically rather than by assumption: 793.1M of
# the 1,073M virtual tokens are sellable, so at exhaustion vTokens is
# 279.9M and vSol is k/279.9M = 115.01, which is the 30 it starts with plus
# the 85 raised at graduation. A reading outside those bounds describes a
# curve that sold more than it has.
VT_MIN = 279.0e6            # exhaustion, with a little margin
VT_MAX = 1073.5e6           # the initial reserve, with a little margin
VS_MIN = 29.9               # the starting virtual reserve
VS_MAX = 115.5              # 30 + the 85 raised by graduation


def decode_curve(blob):
    """Reserves from the curve account, or None if this is not a real one.

    THE ACCOUNT HAS TWO STATES AND THE OLD DECODER COULD NOT TELL THEM
    APART. That is the whole cause of the 812 impossible readings, and it
    took four wrong theories to find, so the evidence is written down here.

    Before a token's FIRST TRADE the account holds a template: virtual
    tokens at exactly the initial 1,073,000,000, real SOL at zero, and the
    virtual-SOL slot holding a value that is IDENTICAL across every such
    account -- 915.4951 on 9 of 14 fresh launches sampled together, 925.7,
    950.7 and 929.2 in the recorded data, so the template's own value
    drifts over time. After the first trade the same bytes hold real
    reserves.

    The offsets were never wrong. pricesrc verified them correctly, and
    repointing them would have corrupted the good readings while leaving
    the bad ones just as wrong.

    The two states are separated by the one property a template cannot
    fake: an honest curve's vSol * vTokens is the initialisation constant.
    A template's is thirty times larger. So the product is checked, and a
    reading that fails is refused rather than written -- the next poll will
    try again, by which time the token may have traded. A MISSING SAMPLE IS
    HONEST; A TEMPLATE READING RECORDED AS A PRICE IS NOT, and 812 of them
    made every multiple in the tab meaningless.
    """
    if len(blob) < 49:
        return None
    vt, vs, rt, rs, _sup = struct.unpack_from("<QQQQQ", blob, 8)
    vts, vss = vt / TOKEN_DEC, vs / LAMPORTS
    if not vts or not vss:
        return None
    k = vss * vts
    if not (1.0 - K_BAND) <= (k / CURVE_K) <= (1.0 + K_BAND):
        # Not a trading curve: the template, or a state this does not know.
        return None
    # AND IT MUST SIT WITHIN THE CURVE, not merely on it. Checked second
    # because the product test cannot do it: see the VS_MAX comment above.
    if not (VS_MIN <= vss <= VS_MAX) or not (VT_MIN <= vts <= VT_MAX):
        return None
    return dict(vt=vts, vs=vss,
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
    out, foreign, rejected = {}, 0, 0
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
        if dec is None:
            # decode_curve refuses anything whose constant product is not
            # the initialisation constant, which is how a pre-trade
            # template is told from a real curve. Counted separately from
            # foreign-owner accounts because the two mean different things:
            # a template will become readable once the token trades, a
            # foreign account never will.
            rejected += 1
            continue
        # A curve's virtual SOL reserve starts at 30 and only rises. Below
        # that is arithmetically impossible, so it is refused here rather
        # than written and caught by an audit later.
        if dec["vs"] >= 29.9 and dec["vt"] > 0:
            out[k] = dec
        else:
            foreign += 1
    notes = []
    if foreign:
        notes.append(f"{foreign} non-curve account(s)")
    if rejected:
        notes.append(f"{rejected} not yet trading (template state)")
    return out, ("skipped " + ", ".join(notes)) if notes and not out else None


async def recruit(cohort, seconds):
    """Fill the cohort from the free websocket subscription.

    ONE FAILED HANDSHAKE USED TO KILL THE WHOLE SESSION. The 20:04 run
    died 21 seconds in with "timed out during opening handshake" and the
    job exited FAILURE having recorded nothing -- the same defect already
    fixed in pumpscan, left in place here because recruit had no retry at
    all. A 5h45m collection window was lost to one transient connect, and
    nothing restarted it until the next hourly trigger.

    So the connection is retried with backoff inside the recruiting
    window, and the cohort is passed in and accumulated across attempts
    rather than restarted, so a drop halfway through keeps what it had.
    """
    import websockets
    end = time.time() + seconds
    tries = 0
    while time.time() < end and len(cohort) < COHORT:
        tries += 1
        if tries > 1:
            wait = min(20.0, 2.0 * (2 ** min(tries - 2, 3)))
            wait = min(wait, max(0.0, end - time.time()))
            if wait <= 0:
                break
            print(f"  recruit attempt {tries} in {wait:.0f}s "
                  f"({len(cohort)} so far)")
            await asyncio.sleep(wait)
            if time.time() >= end:
                break
        try:
            await _recruit_once(cohort, end)
        except Exception as e:
            # A connect or read failure is a retry, never the end of the
            # session. Anything already recruited is kept.
            print(f"  recruit feed problem: {type(e).__name__}: "
                  f"{str(e)[:110]}")
            continue
        # _recruit_once only returns when the window closed or the cohort
        # filled, so there is nothing left to retry.
        break
    if tries > 1:
        print(f"  recruiting took {tries} attempt(s)")
    return cohort


async def _recruit_once(cohort, end):
    """One websocket session's worth of recruiting. Raises on any failure."""
    import websockets
    async with websockets.connect(WS, open_timeout=20) as s:
        await s.send(json.dumps({"method": "subscribeNewToken"}))
        while time.time() < end and len(cohort) < COHORT:
            try:
                raw = await asyncio.wait_for(
                    s.recv(), timeout=max(1, end - time.time()))
            except asyncio.TimeoutError:
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
            # TWO MINTS MUST NOT SHARE A CURVE ACCOUNT. Each bonding curve
            # belongs to one token, so a repeated bondingCurveKey means
            # either the feed is wrong or this is not the per-token account
            # it is assumed to be -- and following both would attribute one
            # account's reserves to two tokens, which is exactly the
            # identical-value symptom the audit found. The second mint is
            # refused and the collision is reported, rather than silently
            # overwriting the first in the curve->mint map.
            if any(v["curve"] == curve for v in cohort.values()):
                clash = next(m for m, v in cohort.items()
                             if v["curve"] == curve)
                print(f"  CURVE COLLISION: {mint[:16]}... reports the same "
                      f"bonding curve as {clash[:16]}...")
                print(f"    curve {curve}")
                print("    refusing the second mint; one account cannot be "
                      "two tokens' price.")
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
          v["launch_px"] if v["launch_px"] else "", session_id, v["curve"]]
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
                (px / lp) if (px and lp) else "", session_id, curve])

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
