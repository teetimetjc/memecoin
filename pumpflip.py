"""Buy at graduation, sell N minutes later -- record what that would do.

THE QUESTION THIS EXISTS FOR, in the operator's words: can you buy a coin
that just graduated and flip it.

WHY NOTHING ALREADY BUILT CAN ANSWER IT. pumppath follows the BONDING
CURVE, and a token leaves the curve at the moment it graduates. So every
price this project has collected stops exactly where this strategy would
start. The two are on opposite sides of the same line, and a day was spent
fixing curve decoding that is irrelevant here.

THE PRICE SOURCE IS DELIBERATELY NOT AN ON-CHAIN DECODE. A graduated token
trades in an AMM pool whose account layout this code does not know, and the
one layout it did assume produced 812 impossible readings before the cause
was found. DexScreener returns a parsed price for a token address with no
struct unpacking at all, and it is a source INDEPENDENT of this project's
own chain reads, so a later on-chain version could be checked against it
rather than trusted. Verified end to end on a live graduation:
pump-amm pair, priceNative 0.0000002691, 829 buys and 515 sells in five
minutes.

WHAT IS RECORDED, per graduated token, at a series of ages:
    price in SOL and USD, the pool's liquidity, and the five-minute buy and
    sell counts -- because a price with no liquidity behind it is not a
    price you could trade.

THE ENTRY IS NOT THE GRADUATION PRICE, and this is the single most
important caveat. The first sample is taken as soon as the migration event
arrives and the API answers, which is seconds late, and its lateness is
recorded on every row. A real buy would be later still and would pay
slippage into a pool seconds old. So the entry here is BETTER than
reachable, and every return computed from it is optimistic.

NO FEE IS CHARGED HERE EITHER. A pump-amm swap pays a pool fee, a priority
fee and spread, each way. Rows are raw; the reader charges cost, and
reports the break-even cost rather than assuming one, so the honest
question becomes "how cheap would execution have to be" instead of "did it
win".

AND SPEED IS THE LIKELIEST KILLER, said here rather than discovered after
building on it. Graduation is a public on-chain event. The bots that trade
it are co-located and paying for priority; this runner is hundreds of
milliseconds away on a public RPC. A positive result here would be
necessary but nowhere near sufficient.

Read-only. No wallet, no private key, no order, no possibility of one.
"""
import asyncio
import json
import os
import time
from collections import Counter

WS = os.environ.get("PUMPWS_URL") or "wss://pumpportal.fun/api/data"
DEX = "https://api.dexscreener.com/latest/dex/tokens/"
SECONDS = int(os.environ.get("PUMPFLIP_SECONDS") or 20700)
POLL_EVERY = int(os.environ.get("PUMPFLIP_POLL") or 20)
FLUSH_EVERY = int(os.environ.get("PUMPFLIP_FLUSH") or 120)
TAB = os.environ.get("PUMPFLIP_TAB") or "Pump Flips"
MAX_ROWS = int(os.environ.get("PUMPFLIP_MAX_ROWS") or 6000)
# ONE ADDRESS PER REQUEST, and the reason is a bug this nearly shipped with.
# The endpoint accepts comma-separated addresses, so batching looked free --
# but the RESPONSE is capped at 30 pairs TOTAL, not 30 per token. Asking for
# a graduated memecoin alongside wrapped SOL returned thirty pairs, every
# one of them SOL's, and the memecoin was simply absent. Queried alone it
# had three pairs and a price. So a batch silently drops whichever tokens
# lose the race for the pair budget, and the collector would have recorded
# nothing for them with no error anywhere.
#
# The cost is paid back by polling on DEMAND instead of continuously: a
# token is only priced when one of its horizons is actually due, so it costs
# about one request per horizon rather than one per poll cycle.
DUE_SLACK = int(os.environ.get("PUMPFLIP_DUE_SLACK") or 0)
# How long a graduated token stays worth following. An hour covers every
# horizon a flip could use; beyond that it is a hold, not a flip.
TRACK_S = int(os.environ.get("PUMPFLIP_TRACK") or 3600)

# Ages at which a price is recorded. Dense early because a flip lives or
# dies in the first minutes.
TARGETS = [int(x) for x in (os.environ.get("PUMPFLIP_TARGETS") or
           "0,30,60,120,300,600,900,1800,3600").split(",") if x.strip()]

LAUNCH_TAB = os.environ.get("PUMPSCAN_TAB") or "Pump Launches"
TOKEN_TAB = os.environ.get("PUMPFLIP_TOKEN_TAB") or "Pump Flip Tokens"

# TWO TABS, BECAUSE THE DATA HAS TWO SHAPES. A token's creator, launch time
# and socials do not change between horizons, so repeating ten such columns
# across nine price rows would write them eighty times for nothing -- and
# this spreadsheet already carries 65k Kalshi rows plus 11k launches, so
# volume is a real constraint rather than a tidiness preference.
#
# One row per graduation goes to TOKEN_TAB; one row per price sample goes to
# the price tab, joined on Mint.
TOKEN_HEADERS = [
    "Graduated UTC", "Mint", "Pool", "Launched UTC", "Mins to graduate",
    "Creator", "Creator Buy SOL", "Creator launch count",
    "Entry Price SOL", "Entry lateness s", "Entry Liquidity USD",
    "Entry FDV USD", "Entry Market Cap USD", "Socials", "Pair age s",
    "Session",
]

# PER-SAMPLE FIELDS, chosen for what could plausibly separate a winner from
# a rug rather than for what the API happened to offer:
#   liquidity        a price with no depth behind it is not one you can sell
#                    into, and depth draining is what a rug looks like
#   volume m5/h1     whether anyone is actually trading it
#   change m5/h1     momentum, the thing the original scanner alerted on
#   buys/sells       one-sided flow; heavy sells with a flat price means the
#                    pool is absorbing an exit
#   fdv/mcap         size, so a result is not an artefact of tiny tokens
HEADERS = [
    "Seen UTC", "Mint", "Pool", "Target s", "Actual age s",
    "Price SOL", "Price USD", "Liquidity USD", "Volume 5m", "Volume 1h",
    "Change 5m %", "Change 1h %", "Buys 5m", "Sells 5m",
    "Buys 1h", "Sells 1h", "FDV USD", "Market Cap USD",
    "Entry Price SOL", "X vs Entry", "Session",
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
    if not have:
        ws.update(values=[headers], range_name="A1")
        return ws
    if have == headers:
        return ws
    # A HEADER THAT DIFFERS ANYWHERE IS A REAL PROBLEM, not just one whose
    # first three names differ. This collector's schema changed between its
    # first dispatch and this one -- the earlier version wrote 14 columns
    # and this one writes 21, with different fields from position 9 on --
    # and a check that only compared the first three names would have let
    # 21-column rows be appended under the old header. The extra columns
    # would carry no name, and every reader maps by name, so volume, price
    # change and the hourly counts would simply vanish while looking fine.
    rows_used = len(ws.col_values(1))
    if rows_used <= 1:
        # Header only, nothing to misalign. Safe to restate.
        ws.resize(cols=max(ws.col_count, len(headers)))
        ws.update(values=[headers], range_name="A1")
        print(f"  tab '{title}': header rewritten ({len(have)} -> "
              f"{len(headers)} columns); the tab held no data rows")
        return ws
    if len(have) < len(headers) and have == headers[:len(have)]:
        # A pure end-append: existing names keep their positions, so
        # nothing already written changes meaning.
        ws.resize(cols=max(ws.col_count, len(headers)))
        ws.update(values=[headers], range_name="A1")
        print(f"  tab '{title}': adopted {len(headers) - len(have)} new "
              f"column(s) at the end; existing columns unchanged")
        return ws
    raise SystemExit(
        f"  tab '{title}' holds {rows_used - 1} row(s) under a DIFFERENT "
        f"header and the difference is not an end-append.\n"
        f"    on the sheet: {have}\n"
        f"    expected:     {headers}\n"
        f"  Refusing to append: the rows would be written under the wrong\n"
        f"  column names. Rename or clear that tab, then re-run.")


def launch_index(sh):
    """{mint: (launched, creator, buy_sol)} plus how often each creator has
    launched, read from this project's own launch log.

    THIS JOIN IS THE POINT OF THE WHOLE TAB. The only real signal pump.fun
    has produced here is creator history: a first-time creator graduated
    10.09% of the time against 0.05% for wallets with 51+ launches, and the
    intervals do not overlap. If that separates graduations, it may well
    separate the graduations worth BUYING from the ones that dump -- but
    only if the creator is recorded beside the price, which needs this
    lookup at the moment of graduation.

    A mint that is not in the log is left blank rather than defaulted. Most
    graduations here are of tokens launched before this collector existed,
    and writing a zero launch count for them would make unknown creators
    look like first-timers -- the exact category the signal says is good.
    """
    try:
        rows = sh.worksheet(LAUNCH_TAB).get_all_values()
    except Exception as e:
        print(f"  could not read '{LAUNCH_TAB}': {str(e)[:90]}")
        print("  graduations will carry no launch history. Not fatal, but")
        print("  the creator signal cannot be tested without it.")
        return {}, {}
    if len(rows) < 2:
        return {}, {}
    h = {k: i for i, k in enumerate(rows[0])}

    def g(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    idx, counts = {}, Counter()
    for r in rows[1:]:
        mint = g(r, "Mint")
        if not mint:
            continue
        creator = g(r, "Creator")
        counts[creator] += 1
        # Keep the EARLIEST sighting; a duplicated create event is the same
        # launch and the first time it was seen is its age origin.
        seen = g(r, "Seen UTC")
        if mint not in idx or (seen and seen < idx[mint][0]):
            idx[mint] = (seen, creator, f(g(r, "Creator Buy SOL")))
    print(f"  launch log: {len(idx)} mint(s), {len(counts)} creator(s)")
    return idx, counts


def price_one(mint, session):
    """One token's price, or None. ONE address per request -- see DUE_SLACK.

    A TOKEN CAN HAVE SEVERAL PAIRS and they do not all mean the same thing.
    The graduated token checked here had three: a pumpswap pool with
    $12,367 of liquidity, a meteora pool with $1.71, and a pumpfun entry
    with none at all, quoting 0.0000003673, 0.0000002687 and 0.0000004108
    -- a 53% spread between the extremes. Taking whichever came first would
    be taking a coin flip.

    So the pump.fun family is preferred by name, and within that the
    DEEPEST pool wins, because that is the one a trade would actually
    touch and the shallow ones quote prices nobody can fill. Which pool was
    chosen is written on the row rather than left to be guessed.
    """
    try:
        r = session.get(DEX + mint, timeout=25)
    except Exception as e:
        print(f"  price request failed: {type(e).__name__}: {str(e)[:80]}")
        return None
    if r.status_code == 429:
        print("  rate limited by the price API; backing off")
        time.sleep(5.0)
        return None
    if not r.ok:
        return None
    try:
        pairs = (r.json() or {}).get("pairs") or []
    except Exception:
        return None
    best = None
    for p in pairs:
        if ((p.get("baseToken") or {}).get("address") or "") != mint:
            # The token must be the BASE of the pair; as a quote its price
            # would be the other asset's.
            continue
        liq = f((p.get("liquidity") or {}).get("usd"), 0.0) or 0.0
        dex = str(p.get("dexId", "")).lower()
        # A PAIR WITH NO LIQUIDITY IS NOT A PRICE, and taking one produced
        # returns of +343,863%. Seconds after a migration the real pool is
        # often not indexed yet, and the only pair on offer is the
        # BONDING CURVE entry, dexId 'pumpfun', carrying no liquidity. Every
        # curve is exhausted at the same reserves when it graduates, so that
        # price is IDENTICAL across tokens -- 4.108e-07 appeared as the
        # entry for two different mints, and 2.796e-08 for two more. The
        # "return" was then a curve price against a real pool price.
        #
        # So a pair must have liquidity to be priced at all, and the real
        # AMM is preferred over the curve rather than merely scored beside
        # it. Returning None makes the caller wait and try again, which is
        # correct: a few samples lost beats a fabricated entry.
        if liq <= 0:
            continue
        curve = 1 if dex == "pumpfun" else 0
        score = (-curve, liq)
        if best is None or score > best[0]:
            best = (score, p)
    if best is None:
        return None
    p = best[1]
    tx = p.get("txns") or {}
    m5, h1 = tx.get("m5") or {}, tx.get("h1") or {}
    vol = p.get("volume") or {}
    chg = p.get("priceChange") or {}
    info = p.get("info") or {}
    socials = len(info.get("socials") or []) + len(info.get("websites") or [])
    return dict(px_sol=f(p.get("priceNative")), px_usd=f(p.get("priceUsd")),
                liq=f((p.get("liquidity") or {}).get("usd")),
                vol5=f(vol.get("m5")), vol1h=f(vol.get("h1")),
                chg5=f(chg.get("m5")), chg1h=f(chg.get("h1")),
                buys=f(m5.get("buys")), sells=f(m5.get("sells")),
                buys1h=f(h1.get("buys")), sells1h=f(h1.get("sells")),
                fdv=f(p.get("fdv")), mcap=f(p.get("marketCap")),
                socials=socials, created=f(p.get("pairCreatedAt")),
                pool=p.get("dexId", ""), pairs=len(pairs))


async def watch(found, seconds):
    """Collect migrations onto `found` for `seconds`, reconnecting on drops.

    ONE DROPPED FEED MUST NOT END THE SESSION. pumpscan lost three hours
    that way and exited green, and pumppath lost a whole window to a single
    failed handshake. Both are fixed; this is written that way from the
    start rather than after losing a night's data.
    """
    import websockets
    end = time.time() + seconds
    tries = 0
    while time.time() < end:
        tries += 1
        if tries > 1:
            wait = min(20.0, 2.0 * (2 ** min(tries - 2, 3)))
            wait = min(wait, max(0.0, end - time.time()))
            if wait <= 0:
                break
            await asyncio.sleep(wait)
            if time.time() >= end:
                break
        try:
            async with websockets.connect(WS, open_timeout=20,
                                          max_size=4_000_000) as s:
                await s.send(json.dumps({"method": "subscribeMigration"}))
                print("  subscribed to migrations")
                while time.time() < end:
                    try:
                        raw = await asyncio.wait_for(
                            s.recv(), timeout=max(1.0, end - time.time()))
                    except asyncio.TimeoutError:
                        return
                    try:
                        d = json.loads(raw)
                    except Exception:
                        continue
                    if not isinstance(d, dict):
                        continue
                    if d.get("txType") != "migrate":
                        continue
                    mint = d.get("mint")
                    if not mint or mint in found:
                        continue
                    found[mint] = dict(pool=d.get("pool", ""),
                                       at=time.time(), done=set(),
                                       entry=None, entry_late=None)
                    print(f"  GRADUATED {mint}  pool={d.get('pool','')}  "
                          f"({len(found)} so far)")
                return
        except Exception as e:
            print(f"  migration feed dropped: {type(e).__name__}: "
                  f"{str(e)[:100]}")
            continue


def main():
    import requests
    print("=" * 100)
    print("FLIP AFTER GRADUATION -- record what buying at the migration "
          "would do")
    print(f"  horizons {TARGETS} s, poll every {POLL_EVERY}s, "
          f"follow each token {TRACK_S}s")
    print("  No wallet, no private key, no order. Prices from an API, not "
          "an on-chain decode.")
    print("=" * 100)

    sh = sheet()
    ws = tab(sh, TAB, HEADERS)
    ws_tok = tab(sh, TOKEN_TAB, TOKEN_HEADERS)
    launches, creator_counts = launch_index(sh)
    session_id = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    http = requests.Session()
    http.headers.update({"Accept": "application/json"})

    found, rows, written = {}, [], 0
    tok_rows, tok_written = [], 0
    t_end = time.time() + SECONDS
    last_flush = time.time()
    stats = Counter()

    def flush():
        nonlocal rows, written, tok_rows, tok_written
        if rows:
            ws.append_rows(rows, value_input_option="USER_ENTERED",
                           table_range="A1")
            written += len(rows)
            rows = []
        if tok_rows:
            ws_tok.append_rows(tok_rows, value_input_option="USER_ENTERED",
                               table_range="A1")
            tok_written += len(tok_rows)
            tok_rows = []

    # The migration feed runs in the background for the whole session while
    # the main loop prices what it finds.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    feed = loop.create_task(watch(found, SECONDS))

    while time.time() < t_end:
        loop.run_until_complete(asyncio.sleep(POLL_EVERY))
        now = time.time()
        # ONLY TOKENS WITH A HORIZON ACTUALLY DUE. A token owes its 3600s
        # sample for a whole hour, so pricing everything that merely owes
        # something would cost one request per token per cycle -- 180 a
        # minute on a sixty-token cohort, against a limit around 300. Asking
        # only when a target has come due costs about one request per
        # horizon, nine per token for its whole life.
        live = []
        for m, v in found.items():
            if now - v["at"] > TRACK_S + 60:
                continue
            pending = [t for t in TARGETS if t not in v["done"]]
            if not pending:
                continue
            if (now - v["at"]) >= min(pending) - DUE_SLACK:
                live.append(m)
        if not live:
            continue
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))
        for mint in live:
            v = found[mint]
            d = price_one(mint, http)
            stats["price_calls"] += 1
            time.sleep(0.25)
            if not d:
                stats["no_price"] += 1
                continue
            px = d["px_sol"]
            if not px:
                # A missing or zero price is not a price. Skipped rather
                # than written as zero, which would average in as real.
                stats["zero_price"] += 1
                continue
            age = now - v["at"]
            due = [t for t in TARGETS if t <= age and t not in v["done"]]
            if not due:
                continue
            tgt = max(due)
            for t in due:
                v["done"].add(t)
            if v["entry"] is None:
                # THE ENTRY IS THE FIRST PRICE WE COULD SEE, not the
                # graduation price, and how late it was is recorded so no
                # later reader can mistake one for the other.
                v["entry"] = px
                v["entry_late"] = age
                # The token's standing attributes are written ONCE, here,
                # at the moment it has a usable entry. The launch join is
                # blank for mints launched before this collector existed --
                # left blank rather than zeroed, since a zero launch count
                # would make an unknown creator look like a first-timer,
                # which is the category the signal says is good.
                lk = launches.get(mint)
                launched, creator, buy_sol = lk if lk else ("", "", None)
                mins = ""
                if launched:
                    try:
                        t0 = time.mktime(time.strptime(
                            launched, "%Y-%m-%d %H:%M:%S"))
                        mins = round((v["at"] - (t0 - time.timezone)) / 60.0,
                                     1)
                    except Exception:
                        mins = ""
                pair_age = ""
                if d.get("created"):
                    pair_age = round(now - d["created"] / 1000.0, 1)
                tok_rows.append([
                    stamp, mint, d.get("pool") or v["pool"], launched, mins,
                    creator, buy_sol if buy_sol is not None else "",
                    creator_counts.get(creator, "") if creator else "",
                    px, round(age, 1),
                    d["liq"] if d["liq"] is not None else "",
                    d["fdv"] if d["fdv"] is not None else "",
                    d["mcap"] if d["mcap"] is not None else "",
                    d.get("socials", ""), pair_age, session_id])
            if written + len(rows) >= MAX_ROWS:
                stats["row_cap"] += 1
                continue
            rows.append([
                stamp, mint, d.get("pool") or v["pool"], tgt, round(age, 1),
                px, d["px_usd"] if d["px_usd"] else "",
                d["liq"] if d["liq"] is not None else "",
                d["vol5"] if d["vol5"] is not None else "",
                d["vol1h"] if d["vol1h"] is not None else "",
                d["chg5"] if d["chg5"] is not None else "",
                d["chg1h"] if d["chg1h"] is not None else "",
                d["buys"] if d["buys"] is not None else "",
                d["sells"] if d["sells"] is not None else "",
                d["buys1h"] if d["buys1h"] is not None else "",
                d["sells1h"] if d["sells1h"] is not None else "",
                d["fdv"] if d["fdv"] is not None else "",
                d["mcap"] if d["mcap"] is not None else "",
                v["entry"], (px / v["entry"]) if v["entry"] else "",
                session_id])
        if time.time() - last_flush >= FLUSH_EVERY:
            flush()
            last_flush = time.time()
            print(f"  ... {written} row(s) written, {len(found)} "
                  f"graduation(s) seen")

    feed.cancel()
    try:
        loop.run_until_complete(feed)
    except Exception:
        pass
    flush()
    print(f"\n  DONE. {len(found)} graduation(s) seen, {written} price "
          f"row(s) to '{TAB}', {tok_written} token row(s) to '{TOKEN_TAB}'.")
    print(f"  {dict(stats)}")
    known = sum(1 for m in found if m in launches)
    if found:
        print(f"\n  {known} of {len(found)} graduation(s) matched a launch in "
              f"our own log ({100.0*known/len(found):.0f}%).")
        print("  The rest launched before this collector existed, so their")
        print("  creator history is unknown and left blank -- not zero.")
    lates = [v["entry_late"] for v in found.values()
             if v["entry_late"] is not None]
    if lates:
        lates.sort()
        print(f"\n  ENTRY LATENESS, seconds after the migration event: "
              f"median {lates[len(lates)//2]:.1f}, "
              f"worst {lates[-1]:.1f}")
        print("  A real buy would be later than this and would pay slippage")
        print("  into a pool seconds old, so these entries are better than")
        print("  reachable and every return from them is optimistic.")
    print("\n  This file records. It computes no verdict, because a session")
    print("  that both collects and concludes tends to conclude from")
    print("  whatever it happened to collect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
