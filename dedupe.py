"""Tickers another strategy already holds. Read-only.

WHY THIS IS NEEDED THE MOMENT TWO STRATEGIES RUN AT ONCE.

live._client_order_id namespaces by ORDER_PREFIX on purpose, so that two
rules both wanting BTC in the window closing at 18:45 do not collide and
silently drop one of them. That is correct for independent strategies -- and
it means the exchange's own duplicate rejection, which is the only layer
that can see both orders, will NOT stop the T-3 bot from buying a market the
T-9 bot already bought six minutes earlier.

The result would be one market held twice, at two different prices, under
two order ids. Two consequences, both bad:

  EXPOSURE DOUBLES on a single contract outcome. Window legs resolve
  together 96% of the time, so this is not diversification, it is one bet
  at twice the size, in exactly the place the stake rule was written to
  prevent.

  THE SETTLEMENT SPLITS. tally shares one settlement across every bet on a
  ticker by contract count. Two strategies on one market makes each one's
  recorded P&L a fraction of the truth, and neither record then describes
  what that strategy did.

So the later strategy asks what the earlier one is already holding and
leaves those markets alone. It reads the sheet rather than the exchange
because the sheet is where strategy identity lives: the Order ID carries
the ORDER_PREFIX, so "held by someone else" is answerable without any new
column.

Read-only. Fails OPEN on purpose -- if the sheet cannot be read this returns
an empty set, which means the T-3 bot bets normally rather than halting. A
research bot that stops trading because a spreadsheet was slow is worse than
one that occasionally doubles a market, and the per-window cap and the
stake bound the damage either way. The log says which happened.
"""
import os

SHEET = "Live Bets"


def held_tickers(client, exclude_prefix=None, lookback_rows=400):
    """{ticker} currently recorded as PLACED by any OTHER strategy.

    `exclude_prefix` is this strategy's own ORDER_PREFIX; its rows are not
    counted, because a strategy is allowed to know about its own bets
    through the usual per-process guard and should not block itself.
    """
    try:
        import predictor as P
        sh = client.open_by_key(P.SPREADSHEET_ID)
        rows = sh.worksheet(SHEET).get_all_values()
        if len(rows) < 2:
            return set()
        h = {k: i for i, k in enumerate(rows[0])}
        ti, si, oi = h.get("Ticker"), h.get("Status"), h.get("Order ID")
        if ti is None or si is None:
            return set()
        out = set()
        # Only the tail matters: markets close every fifteen minutes, so a
        # ticker far enough back cannot still be open.
        for r in rows[max(1, len(rows) - lookback_rows):]:
            if len(r) <= max(ti, si):
                continue
            if str(r[si]).strip().upper() != "PLACED":
                continue
            oid = str(r[oi]).strip() if (oi is not None and oi < len(r)) else ""
            if exclude_prefix and oid.startswith(f"{exclude_prefix}-"):
                continue
            tk = str(r[ti]).strip()
            if tk:
                out.add(tk)
        return out
    except Exception as e:
        print(f"  [dedupe] could not read holdings, betting normally: "
              f"{str(e)[:90]}")
        return set()


def enabled():
    return (os.environ.get("FAVLIVE_SKIP_HELD") or "0").strip() == "1"
