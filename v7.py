"""v7 -- ask the question Kalshi actually settles, and try to beat its price.

EVERY RULE BEFORE THIS ONE PREDICTED THE WRONG THING. v1-v6 all forecast
direction: will the coin be higher in fifteen minutes than it is right now?
Kalshi does not sell that. It sells "will the coin finish above STRIKE", and
the strike is set at market open, somewhere near but not at spot. Scored
correctly the whole v6 record loses (see grade_check.py), and the apparent
edge came from grading long shots as if they were coin flips.

So v7 changes the target, not the signals.

WHAT IT PREDICTS. P(coin finishes above the strike). That is the contract.

WHAT IT MUST BEAT. Not 50%. The market's own quoted probability. Kalshi's
book is well calibrated -- bucket its quotes and outcomes track them closely
-- so being "right 55% of the time" is worth nothing if the market already
said 55%. The only thing that pays is a forecast that is better than the
price, so the model is fitted as a CORRECTION to the price:

    logit(p_model) = logit(p_market) + beta . features

With that form, beta = 0 reproduces the market exactly and is the honest null.
Any profit has to come from beta being reliably non-zero out of sample. This
is also why the features include the strike distance: how far the strike sits
from spot is most of what decides the contract, and no earlier rule ever saw
it.

HOW IT IS JUDGED. Split by TIME, never at random: train on the earlier 70%,
test on the later 30%, and report only the test numbers. Random splits leak,
because five coins in one 15-minute window share a market move and would land
on both sides of the split. For the same reason every interval clusters by
window rather than by bet.

Three things have to hold before any of this is worth money:
  1. test log-loss below the market's own log-loss (a better forecast)
  2. edge against the price paid with a 95% interval clear of zero
  3. that edge surviving the fee, which is real and charged on every trade

Read-only. Places nothing.
"""

import math
import sys

import numpy as np

FEATURES = [
    # The one no previous rule could see: how far the strike is from spot,
    # scaled by price. Most of what decides the contract.
    ("strike_dist", lambda f: f["dist"]),
    # ... and its magnitude, because near and far strikes behave differently
    # in ways a signed term alone cannot express.
    ("strike_dist_abs", lambda f: abs(f["dist"])),
    ("cvd_ratio", lambda f: f["cvd"]),
    # The interaction that would matter if order flow pushes price toward or
    # away from a strike: flow only helps if it points the right way.
    ("cvd_x_dist", lambda f: f["cvd"] * f["dist"]),
    ("mkt_frac", lambda f: f["mktfrac"]),
    ("funding", lambda f: f["funding"]),
    ("rsi", lambda f: f["rsi"]),
    ("vwap_dev", lambda f: f["vwap"]),
    ("ob_imb", lambda f: f["ob"]),
    ("vol_spike", lambda f: f["volspike"]),
]


def _logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def build(rows, headers):
    """Rows -> (X, y, offset, price, window key). One row per market, not per bet.

    Note this is per MARKET, not per signal: the contract exists whether or
    not a rule fired on it, and the question "is this book beatable" is about
    the market. Restricting to rows where v6 happened to fire would inherit
    v6's selection and answer a narrower question than the one being asked.
    """
    I = {h: i for i, h in enumerate(headers) if h}

    def num(r, k):
        i = I.get(k, -1)
        if not (0 <= i < len(r)):
            return None
        try:
            return float(r[i])
        except (TypeError, ValueError):
            return None

    X, y, off, px, win, when = [], [], [], [], [], []
    for r in rows[1:]:
        pred, tgt, ev = num(r, "Price at Pred"), num(r, "K Target"), num(r, "Price at Eval")
        up = num(r, "K Up%")
        if not (pred and tgt and ev and up) or not (0 < up < 100):
            continue
        f = {
            "dist": (tgt - pred) / pred * 100.0,
            "cvd": num(r, "OF CVD Ratio") or 0.0,
            "mktfrac": num(r, "OF Mkt Frac") or 0.0,
            "funding": (num(r, "FUT Funding Rate") or 0.0) * 1000.0,
            "rsi": ((num(r, "RSI(7)") or 50.0) - 50.0) / 50.0,
            "vwap": num(r, "VWAP Dev %") or 0.0,
            "ob": num(r, "OB Imbalance") or 0.0,
            "volspike": (num(r, "Vol Spike Ratio") or 1.0) - 1.0,
        }
        X.append([fn(f) for _, fn in FEATURES])
        y.append(1.0 if ev > tgt else 0.0)
        off.append(_logit(up / 100.0))
        px.append(up / 100.0)
        ts = r[I["Timestamp"]]
        win.append(ts)
        when.append(ts)
    return (np.array(X, float), np.array(y, float), np.array(off, float),
            np.array(px, float), win, when)


def fit(X, y, off, l2=2.0, iters=40):
    """Logistic regression as a correction to the market's own log-odds.

    Fitted by IRLS (Newton), not plain gradient descent. The first version of
    this used gradient descent and quietly under-fit: on synthetic data with a
    KNOWN planted edge it recovered a coefficient of 0.03 where the truth was
    0.45, moved its probabilities about a point away from the market, and
    reported "no edge". A fitter that cannot find an edge that is definitely
    there cannot be trusted to say one is absent, so the synthetic test is
    part of the file now (see selftest()).

    Standardised inside so the ridge penalty means the same for every feature;
    coefficients come back on the standardised scale, which is the one worth
    reading. Ridge is not decoration: ten features against a few hundred
    independent windows will otherwise fit noise.
    """
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1.0
    Z = np.column_stack([np.ones(len(y)), (X - mu) / sd])
    b = np.zeros(Z.shape[1])
    pen = np.eye(Z.shape[1]) * l2
    pen[0, 0] = 0.0                       # never penalise the intercept
    for _ in range(iters):
        eta = off + Z @ b
        p = 1.0 / (1.0 + np.exp(-eta))
        W = np.clip(p * (1 - p), 1e-6, None)
        g = Z.T @ (y - p) - pen @ b
        H = Z.T @ (Z * W[:, None]) + pen
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        b += step
        if np.max(np.abs(step)) < 1e-8:
            break
    return b[0], b[1:], mu, sd


def predict(b0, b, mu, sd, X, off):
    Z = (X - mu) / sd
    return 1.0 / (1.0 + np.exp(-(off + b0 + Z @ b)))


def logloss(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def _fee(n, p):
    return math.ceil(round(0.07 * n * p * (1 - p), 9) * 100) / 100.0


def bet_pnl(price, won, stake=10.0):
    """Buy the side the model likes at the market's price, $10 flat."""
    p = min(max(price, 0.01), 0.99)
    n = stake / p
    return (n if won else 0.0) - stake - _fee(n, p)


def clustered(vals, keys):
    """Mean and 95% interval, clustering rows that share a 15-minute window."""
    g = {}
    for v, k in zip(vals, keys):
        g.setdefault(k, []).append(v)
    cl = [sum(v) / len(v) for v in g.values()]
    nb = sum(len(v) for v in g.values())
    m = sum(sum(v) for v in g.values()) / nb
    if len(cl) < 2:
        return m, 0.0, nb, len(cl)
    va = sum(len(v) * (c - m) ** 2 for v, c in zip(g.values(), cl)) / (len(cl) - 1)
    return m, 1.96 * math.sqrt(va / len(cl)), nb, len(cl)


def run(rows, headers, edge_cut=0.04):
    X, y, off, px, win, when = build(rows, headers)
    n = len(y)
    if n < 400:
        return {"error": f"only {n} usable rows"}

    # Split by TIME. Sorting by the timestamp first, because the sheet is
    # append-ordered per window but five coins share each window and must not
    # be split across the boundary.
    order = np.argsort(np.array(when, dtype=object).astype(str), kind="stable")
    X, y, off, px = X[order], y[order], off[order], px[order]
    win = [win[i] for i in order]
    cut_ts = sorted(set(win))[int(len(set(win)) * 0.7)]
    tr = np.array([w < cut_ts for w in win])
    te = ~tr
    if te.sum() < 100:
        return {"error": "holdout too small"}

    b0, b, mu, sd = fit(X[tr], y[tr], off[tr])
    p_model = predict(b0, b, mu, sd, X[te], off[te])
    p_mkt = px[te]
    yy, ww = y[te], [w for w, k in zip(win, te) if k]

    ll_model, ll_mkt = logloss(p_model, yy), logloss(p_mkt, yy)

    # Bet only where the model disagrees with the price by more than the cut.
    # A 1-point disagreement is noise and would be eaten by the fee.
    nets, edges, keys, took = [], [], [], 0
    for i in range(len(yy)):
        d = p_model[i] - p_mkt[i]
        if abs(d) < edge_cut:
            continue
        took += 1
        if d > 0:                       # model says YES is underpriced
            nets.append(bet_pnl(p_mkt[i], yy[i] == 1.0))
            edges.append((1.0 if yy[i] == 1.0 else 0.0) - p_mkt[i])
        else:
            nets.append(bet_pnl(1.0 - p_mkt[i], yy[i] == 0.0))
            edges.append((1.0 if yy[i] == 0.0 else 0.0) - (1.0 - p_mkt[i]))
        keys.append(ww[i])

    out = {
        "n": int(n), "n_train": int(tr.sum()), "n_test": int(te.sum()),
        "cut": cut_ts, "edge_cut": edge_cut,
        "ll_model": round(ll_model, 5), "ll_mkt": round(ll_mkt, 5),
        "ll_gain": round(ll_mkt - ll_model, 5),
        "coefs": {nm: round(float(v), 4) for (nm, _), v in zip(FEATURES, b)},
        "bets": took,
    }
    if took >= 20:
        em, eci, nb, nc = clustered(edges, keys)
        pm, pci, _, _ = clustered(nets, keys)
        out.update({
            "edge_pp": round(em * 100, 3), "edge_ci": [round((em - eci) * 100, 3),
                                                       round((em + eci) * 100, 3)],
            "ev_bet": round(pm, 3), "ev_ci": [round(pm - pci, 3), round(pm + pci, 3)],
            "total": round(sum(nets), 2), "windows": nc,
            "verdict": ("EDGE" if (em - eci) > 0 and (pm - pci) > 0
                        else "NOTHING" if (pm + pci) < 0 else "UNPROVEN"),
        })
    else:
        out["verdict"] = "TOO FEW BETS"
    return out


def selftest():
    """Prove the machinery can find an edge before trusting it to deny one.

    Two synthetic books, both with the strike distance priced correctly:
      - one where nothing else matters   -> must report no edge
      - one where the book is BLIND to CVD -> must recover it

    Both halves earned their place. The first fitter used gradient descent
    and recovered 0.03 where the truth was 0.45, and the first synthetic had
    the book quoting the true probability -- signal included -- so there was
    nothing left to find and "no edge" was the correct answer to a question
    nobody meant to ask. A null result is only worth reporting from a
    procedure that demonstrably finds a real effect.
    """
    rng = np.random.default_rng(7)
    H = ["Timestamp", "Symbol", "Price at Pred", "RSI(7)", "Vol Spike Ratio",
         "VWAP Dev %", "OB Imbalance", "K Target", "K Up%", "Price at Eval",
         "OF CVD Ratio", "OF Mkt Frac", "FUT Funding Rate"]

    def synth(signal, n_win=900):
        rows = [H]
        for w in range(n_win):
            ts = f"2026-01-{w//96+1:02d} {(w%96)//4:02d}:{(w%4)*15:02d}"
            for c in range(5):
                pred = 100.0
                dist = rng.normal(0, 0.10)
                tgt = pred * (1 + dist / 100)
                cvd = rng.normal(0, 1)
                lo_mkt = -dist / 0.12              # what the book knows
                p_mkt = 1 / (1 + math.exp(-lo_mkt))
                p_true = 1 / (1 + math.exp(-(lo_mkt + signal * cvd)))
                p_mkt = min(max(p_mkt + rng.normal(0, 0.01), .02), .98)
                up = 1 if rng.random() < p_true else 0
                rows.append([ts, f"C{c}", pred, 50, 1.0, 0.0, 0.0, tgt,
                             p_mkt * 100, tgt * (1.001 if up else 0.999),
                             cvd, 0.5, 0.0])
        return rows

    ok = True
    null = run(synth(0.0), H)
    if null["verdict"] == "EDGE":
        print("  FAIL: found an edge in an efficient book"); ok = False
    else:
        print(f"  pass: efficient book -> {null['verdict']} "
              f"(cvd coef {null['coefs']['cvd_ratio']:+.3f})")
    planted = run(synth(0.45), H)
    if planted["verdict"] != "EDGE" or planted["coefs"]["cvd_ratio"] < 0.3:
        print(f"  FAIL: missed a planted edge -> {planted['verdict']}, "
              f"coef {planted['coefs']['cvd_ratio']:+.3f}"); ok = False
    else:
        print(f"  pass: planted 0.45 -> recovered "
              f"{planted['coefs']['cvd_ratio']:+.3f}, verdict EDGE")
    print("  SELFTEST " + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        return selftest()
    import predictor as P
    client = P._get_client()
    sh = client.open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(P.SHEET_NAME).get_all_values()
    res = run(rows, P.ALL_HEADERS)
    print("=" * 66)
    print("v7 -- can anything beat Kalshi's own price on the STRIKE question?")
    print("=" * 66)
    if "error" in res:
        print("  " + res["error"])
        return 1
    print(f"  rows {res['n']}  train {res['n_train']}  holdout {res['n_test']}"
          f"  (split at {res['cut']})")
    print(f"\n  FORECAST QUALITY (holdout)")
    print(f"    market log-loss {res['ll_mkt']:.5f}")
    print(f"    model  log-loss {res['ll_model']:.5f}"
          f"   improvement {res['ll_gain']:+.5f}")
    print(f"\n  COEFFICIENTS (correction to the market's log-odds)")
    for k, v in sorted(res["coefs"].items(), key=lambda kv: -abs(kv[1])):
        print(f"    {k:18s} {v:+.4f}")
    print(f"\n  BETTING the disagreements over {res['edge_cut']*100:.0f} points")
    print(f"    bets taken {res['bets']}")
    if "edge_pp" in res:
        print(f"    edge  {res['edge_pp']:+.2f}pp   95% CI "
              f"{res['edge_ci'][0]:+.2f} to {res['edge_ci'][1]:+.2f}")
        print(f"    EV    ${res['ev_bet']:+.3f}/bet  95% CI "
              f"${res['ev_ci'][0]:+.3f} to ${res['ev_ci'][1]:+.3f}")
        print(f"    total ${res['total']:+.2f} over {res['windows']} windows")
    print(f"\n  VERDICT: {res['verdict']}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
