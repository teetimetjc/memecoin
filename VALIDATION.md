# Validation

How to tell whether a change to the predictor actually worked.

Every logged row carries a **Model Version**. Accuracy figures are never pooled
across versions — a v1 number and a v2 number describe different programs.

Run the report:

```
python predictor.py --report              # current version
python predictor.py --report --version v1 # an older one
```

## Why the version stamp exists

The v1 baseline was measured at 48.8% over 2,521 predictions (Sep 1–6 2026).
That number described a model running on six working indicators, not eight:

- `calc_stoch_rsi` returned `None` on every call. Its window was one element
  shorter than `calc_rsi` needs, so the function fell through its own guard.
  The Stoch RSI column is populated **0 times in 2,540 rows**.
- `calc_vol_spike` compared the in-progress candle against completed ones, so
  it measured how far into the current minute the script happened to run.
  Median reading 0.10; 87.4% of values below 1.0. A synthetic 2.6× volume
  spike registered as 0.074.

Those legs carried 0.20 of the 1.00 weight vector — one silent, one inverted.
Fixing them changes composite output, so v1 and v2 accuracy are not comparable
and must not be averaged together.

## The thresholds, fixed in advance

Set before any v2 data existed, so a verdict cannot be talked into existence
afterward. They live in `predictor.py` as `MIN_SAMPLE` and `BREAKEVEN_MARGIN`.

| Gate | Value | Why |
| --- | --- | --- |
| Minimum priced sample | 300 rows | At n=55 the 95% CI on a 60% reading spans 46.8%–73.2% — wide enough to contain breakeven. 300 narrows it to roughly ±5.7pp. |
| Bar to clear | entry-implied breakeven + 1.0pp | A binary at *p* cents needs *p*% accuracy to break even. The margin covers fees and slippage. |
| P&L | must be positive | Accuracy above the bar with negative P&L means the wins came at worse prices than the losses. |

**Accuracy alone cannot pass anything.** A 60% strategy paying 65¢ loses money;
a 48% strategy paying 40¢ makes it. This is why `EMA-Only Entry ¢` is logged
next to `EMA-Only Correct?` — the pair is the unit of evidence, not either
column alone.

## What a v2 run should show

Checked automatically by `--report`:

1. **Stoch RSI populated** on >90% of rows. If still 0, the fix didn't take.
2. **Vol Spike median near 1.0** (was 0.10). Outside 0.5–2.0 means it's still
   sampling a partial candle.
3. **Kalshi priced on >90% of rows.** At v1's 2.2% coverage no profitability
   question can be answered at all — this is the gate that matters most.

Then, for the composite and the EMA-only rule independently: accuracy, average
entry price, realised P&L net of Kalshi's `0.07 × C × P × (1−P)` fee, and a
PASS / FAIL / TOO EARLY verdict.

## The open question

The EMA-divergence rule (EMA BEAR → UP, EMA BULL → DOWN, no call on FLAT) scored
188/320 = 58.8%, z = +3.13 on v1 data. Two caveats keep it unvalidated:

- **In-sample.** Found by testing ~8 indicators against the same dataset, which
  inflates whichever one wins.
- **Unstable day to day**: 60.5%, 58.5%, 59.2%, 47.4%, 44.0%, 83.8%. Two of six
  days sat below breakeven and one outlier carries much of the average.

It is logged in parallel, and alerts are off, so it accrues an honest
out-of-sample record without anyone acting on it. A cautionary example of why:
on the 55 v1 rows that did carry prices, the composite showed 60% accuracy and
+$202 P&L — but those rows turned out to be 13 time slots across two evenings,
and *every* prediction in those same slots ran 61.5%. The apparent edge was a
good Tuesday and Wednesday night, not a property of the model.
