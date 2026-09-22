"""Send one practice digest, through the real notification path.

WHY NOT JUST CALL send_pushover WITH "hello". That would prove Pushover
works and nothing else. The parts that can actually be wrong are the ones
in between: whether the digest assembles, whether it stays under the
character limit, whether the totals read correctly, whether the phone
renders a monospace-ish table legibly. So this builds fake settlements and
hands them to botrun.send_digest -- the same function the live run calls.

THE NUMBERS ARE FAKE AND THE MESSAGE SAYS SO, twice. A notification showing
wins is exactly the sort of thing that has prompted a hand-placed bet in
this project before, and a practice message that could be mistaken for a
real result would be worse than no practice message at all.
"""

import sys

import botrun


def main():
    # Shaped like real rows: a cheap winner, a mid-price loser, a normal
    # win -- so the practice message shows the real spread of outcomes
    # rather than a flattering row of green.
    botrun._PENDING.extend([
        {"asset": "BTC", "side": "YES", "entry": 0.565, "won": True,
         "ten": 6.93, "res": "YES"},
        {"asset": "ETH", "side": "NO", "entry": 0.505, "won": False,
         "ten": -10.12, "res": "YES"},
        {"asset": "SOL", "side": "NO", "entry": 0.405, "won": True,
         "ten": 13.63, "res": "NO"},
    ])
    # The running total is left at zero rather than borrowing the real
    # record, so nothing in this message can be mistaken for a result.
    botrun._TALLY.update({"w": 0, "l": 0, "pnl": 0.0, "ten": 0.0,
                          "loaded": True})

    import predictor as P
    real_send = P.send_pushover

    def tagged(title, message, **kw):
        return real_send(
            "TEST - " + title,
            "*** PRACTICE MESSAGE - THESE NUMBERS ARE MADE UP ***\n\n"
            + message
            + "\n\n*** NOTHING ABOVE HAPPENED. Real digests will not "
              "carry this banner. ***", **kw)

    P.send_pushover = tagged
    botrun.send_digest(P, force=True)
    print("practice digest sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
