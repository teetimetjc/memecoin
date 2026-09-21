"""What was live, when -- the record the dashboards segment on.

WHY THIS EXISTS. Live results are only interpretable against the settings
that produced them. Six $5 trades at a flat 2.5x and six at a
price-dependent target are not twelve trades of one strategy; pooling them
answers a question nobody asked and hides the change that mattered. Without
a record, the pooling happens silently, because by then the code says only
what is running NOW and the old setting is a commit nobody reads.

So every change to what the live runs do is appended here, with the UTC
minute it took effect. Rules:

  APPEND ONLY. Editing a past entry rewrites history, which is the specific
  failure this guards against -- it is exactly how a strategy comes to look
  like it always had the settings that turned out to work.

  THE TIME IS WHEN IT WENT LIVE, not when it was decided or committed.
  Trades are matched to settings by timestamp, so a wrong minute silently
  files trades under the wrong rule.

  ONE ENTRY PER CHANGE, even a small one. A cap lifted from 2.5x to 3x is
  a different strategy for the purpose of reading a P&L curve.

`current()` answers what was live at a given moment, and the payload ships
the whole list so a page can mark the boundaries rather than drawing one
line through settings that changed underneath it.
"""

CHANGES = [
    {
        "at": "2026-09-20 22:15",
        "what": "champion, live $5 tests",
        "rules": "one side 20-35c at +2min, sell at 2x",
        "why": "first live trades -- testing whether a resting take-profit "
               "fills unattended at all, not whether the rule is profitable",
        "stake": 5.0,
    },
    {
        "at": "2026-09-21 13:00",
        "what": "combo set, six rules at once",
        "rules": "ALL 10-20c/0-20c +3min 3x; ALL 10-20c +3min 2.5x; "
                 "DOGE 15-30c +2min 3x; BTC 30-40c +3min 2.5x; "
                 "XRP 10-25c +3min 3x",
        "why": "the six combinations clearing 67% ROI in the 1,188-rule grid "
               "search, fired together; overlaps resolved by buying a setup "
               "once at the greediest target that selected it",
        "stake": 5.0,
    },
    {
        "at": "2026-09-21 13:41",
        "what": "combo set, take-profit capped at 2.5x",
        "rules": "as above, every target lowered to at most 2.5x",
        "why": "3x closes a position 15% of the time against 26% at 2.5x; "
               "bought roughly 1.7x the feedback for about $0.33 a trade of "
               "expected value while the mechanism was still being learned",
        "stake": 5.0,
    },
    {
        "at": "2026-09-21 13:56",
        "what": "price-dependent targets",
        "rules": "10-20c at +3min sell 3x; 20-40c at +3min sell 2x; "
                 "nothing under 10c or over 40c",
        "why": "a flat multiple is not one rule: 2.5x from 15c is a move to "
               "38c, from 36c it demands 90c. A live 36c trade peaked at 63c, "
               "missed 90c and settled worthless. Per-band profit says cheap "
               "entries want more greed and dearer ones less; under 10c loses "
               "at every target. Holdout +$1.24 a trade against +$1.01 flat "
               "2.5x and +$0.45 flat 3x -- and better than tuning every band "
               "separately, which managed +$1.11",
        "stake": 5.0,
    },
]


def current(ts=None):
    """What was live at a UTC 'YYYY-MM-DD HH:MM', or now. None if before all."""
    if ts is None:
        import datetime
        ts = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%d %H:%M")
    live = None
    for c in CHANGES:                    # chronological; last one at or before
        if c["at"] <= ts:
            live = c
        else:
            break
    return live


def payload():
    """The list, for a dashboard to draw boundaries with."""
    return CHANGES


def main():
    now = current()
    print(f"{len(CHANGES)} recorded changes; live now: "
          f"{now['what'] if now else 'nothing'}")
    for c in CHANGES:
        print(f"  {c['at']} UTC  ${c['stake']:.0f}  {c['what']}")
        print(f"                     {c['rules']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
