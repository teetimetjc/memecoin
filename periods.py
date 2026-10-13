"""When did YES work, and when did NO? The same question Polymarket answered.

THE PROMPT. On Polymarket the identical rule LOST money 2026-04-01..07-18 and
MADE money 07-19..10-07, and Kalshi's history sits entirely inside that second
stretch. That is the single most uncomfortable fact in this project: the edge
may be a period rather than a rule. This asks what the Kalshi window itself
looks like broken into stretches, for each side separately.

WHAT IT CANNOT DO. Kalshi's M15 history starts 2026-08-04, so it covers about
ten weeks. It cannot see a months-long regime turn, because it does not
contain one. It can only say whether the ten weeks it has are uniform or
lumpy -- and lumpy over ten weeks is itself worth knowing, because a rule
whose profit arrives in two good weeks out of ten is not the steady 95% the
headline win rate suggests.

THE CELLS ARE SMALL AND THAT IS THE POINT, NOT A FLAW TO HIDE. The 90-96c
band is roughly one market in twenty, so the whole history holds a few
hundred bets a side and a week holds a few dozen. No weekly t-stat here means
anything on its own. What is readable is the SHAPE: whether the good stretches
cluster, and whether the two sides are good and bad at the same times or at
opposite times. The latter would be the first real evidence for favouring one
side by regime.

Read-only.
"""
import collections, statistics as st, sys, time
import predictor as P
import score as S

BAND=(0.90,0.96); STAKE=S.STAKE; ENTRY=S.ENTRY; MAJORS=set(S.MAJORS)

def load():
    sh=P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows=sh.worksheet(S.TAB).get_all_values()
    h={k:i for i,k in enumerate(rows[0])}
    def c(r,k):
        i=h.get(k); return r[i] if i is not None and i<len(r) else ""
    out=[]
    for r in rows[1:]:
        if not r or not r[0]: continue
        if str(c(r,"Series")) not in MAJORS: continue
        res=str(c(r,"Result")).lower().strip()
        if res not in ("yes","no"): continue
        t=S.ts(c(r,"Close Time"))
        if t is None: continue
        b,a=S._f(c(r,f"bid{ENTRY}")),S._f(c(r,f"ask{ENTRY}"))
        if b is None or a is None or not (0<b<=a<1): continue
        out.append(dict(t=t,ct=str(c(r,"Close Time")),bid=b,ask=a,yes=(res=="yes")))
    out.sort(key=lambda m:m["t"])
    print(f"  {len(out):,} graded majors, "
          f"{time.strftime('%Y-%m-%d',time.gmtime(out[0]['t']))} to "
          f"{time.strftime('%Y-%m-%d',time.gmtime(out[-1]['t']))}")
    return out

def bets(M,side):
    lo,hi=BAND; out=[]
    for m in M:
        px=m["ask"] if side=="YES" else (1.0-m["bid"])
        if not (lo<=px<hi): continue
        n=int(STAKE/px)
        if n<1: continue
        won=m["yes"] if side=="YES" else (not m["yes"])
        out.append(dict(t=m["t"],ct=m["ct"],px=px,won=won,
                        v=(n if won else 0)-(n*px+S.fee(n,px))))
    return out

def wk(t): return time.strftime("%Y wk%V (from %m-%d)",time.gmtime(t-86400*time.gmtime(t).tm_wday))

def main():
    M=load()
    Y,N=bets(M,"YES"),bets(M,"NO")
    print(f"  in-band: YES {len(Y)}  NO {len(N)}\n")
    weeks=sorted({wk(b['t']) for b in Y+N})
    print("="*104)
    print("  BY WEEK -- cells are tiny, read the SHAPE not any single row")
    print("="*104)
    print(f"  {'week':<26} {'YES n':>6} {'won':>7} {'net':>9} {'cum':>9}   "
          f"{'NO n':>6} {'won':>7} {'net':>9} {'cum':>9}")
    cy=cn=0.0
    for w in weeks:
        y=[b for b in Y if wk(b['t'])==w]; n=[b for b in N if wk(b['t'])==w]
        sy=sum(b['v'] for b in y); sn=sum(b['v'] for b in n)
        cy+=sy; cn+=sn
        wy=f"{100*sum(1 for b in y if b['won'])/len(y):.0f}%" if y else "-"
        wn=f"{100*sum(1 for b in n if b['won'])/len(n):.0f}%" if n else "-"
        print(f"  {w:<26} {len(y):>6} {wy:>7} {sy:>+9.2f} {cy:>+9.2f}   "
              f"{len(n):>6} {wn:>7} {sn:>+9.2f} {cn:>+9.2f}")
    print("\n"+"="*104)
    # how concentrated is the profit?
    for lab,rows in (("YES",Y),("NO",N)):
        if not rows: continue
        byw=collections.defaultdict(float)
        for b in rows: byw[wk(b['t'])]+=b['v']
        vals=sorted(byw.values(),reverse=True)
        tot=sum(vals); pos=sum(v for v in vals if v>0)
        print(f"  {lab}: {len(byw)} weeks, net ${tot:+.2f}. "
              f"Best week ${vals[0]:+.2f}, worst ${vals[-1]:+.2f}.")
        if tot>0:
            print(f"       the single best week is {100*vals[0]/tot:.0f}% of all "
                  f"the profit; the winning weeks total ${pos:+.2f}")
        print(f"       weeks positive: {sum(1 for v in vals if v>0)} of {len(vals)}")
    print("="*104)
    print("  Kalshi's history starts 2026-08-04 and so cannot contain a")
    print("  months-long regime turn. Profit arriving in a couple of weeks out")
    print("  of ten is not the same thing as a steady 95% edge, and the two")
    print("  sides being good at OPPOSITE times would be the first real reason")
    print("  to favour one by regime. Check that before anything else.")
    print("="*104)
    return 0

if __name__=="__main__": sys.exit(main())
