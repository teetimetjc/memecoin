"""Buy the cheap YES instead of the dear NO, and sell it into a spike. Worth it?

THE IDEA. When NO sits at 90-96c at T-9, YES is at 4-10c. Rather than paying
92c for a favourite that returns 8c, buy the 8c longshot and sell it if the
underlying wobbles toward the strike. Small stake, occasional multiple.

WHY THE FEE MAKES THIS HARD BEFORE ANY DATA IS LOOKED AT. Kalshi charges
0.07 * contracts * P * (1-P), which as a share of the money staked is:

    buy at 95c   0.07*0.05/0.95 =  0.37% of stake
    buy at 50c   0.07*0.50/0.50 =  7.00%
    buy at  5c   0.07*0.95/0.05 =  6.65%

So the longshot costs about eighteen times the favourite in fee drag, and an
early exit pays a second fee on the way out. The price must move a long way
to clear the round trip.

WHAT IS MEASURED, and it is deliberately the most flattering version: for
each market whose YES is 4-10c at T-9, the HIGHEST yes bid at any minute
between T-8 and T-1 -- the best exit that existed, with perfect timing nobody
has. Entry pays the ask, exit takes the bid, both legs pay the fee.

IF THE PERFECT-TIMING VERSION DOES NOT CLEAR THE FEES, the idea is dead and
no execution rule rescues it. If it does clear them, that is not a strategy
either -- it is permission to ask the real question, which is what a FIXED
exit rule earns, since nobody sells the high.

Read-only.
"""
import sys, time, statistics as st
import backfill as B
import score as S

MAJORS=list(S.MAJORS); ENTRY=S.ENTRY
LO_C, HI_C = 4.0, 10.0          # the YES price band, i.e. NO at 90-96c
STAKE=10.0
PER_SERIES=400
BUDGET=int(sys.argv[1]) if len(sys.argv)>1 else 2000

def fee(c,p): return 0.07*c*p*(1-p)

def path(series,ticker,close):
    """(offset -> (yes_ask_close, yes_bid_close, yes_bid_high)) for T-1..T-12."""
    end=B.ts(close)
    if end is None: return {}
    d,err=B.get(f"/trade-api/v2/series/{series}/markets/{ticker}/candlesticks",
                start_ts=int(end)-13*60, end_ts=int(end), period_interval=1)
    if not d: return {}
    out={}
    for c in (d or {}).get("candlesticks") or []:
        t=B._f(c.get("end_period_ts"))
        if t is None or t>=end: continue
        def g(k,f):
            v=c.get(k); return B._f(v.get(f)) if isinstance(v,dict) else None
        a=g("yes_ask","close_dollars"); b=g("yes_bid","close_dollars")
        bh=g("yes_bid","high_dollars")
        if a is None or b is None: continue
        if bh is None or bh<b: bh=b
        off=int(round((end-t)/60.0))
        if 1<=off<=12: out[off]=(a,b,bh)
    return out

def main():
    print("="*94)
    print("BUY THE CHEAP YES AND SELL THE SPIKE -- with perfect exit timing")
    print(f"  YES {LO_C:.0f}-{HI_C:.0f}c at T-{ENTRY} (i.e. NO at "
          f"{100-HI_C:.0f}-{100-LO_C:.0f}c), ${STAKE:.0f} a bet")
    print("="*94)
    mk=[]
    for s in MAJORS:
        got=B.settled_markets(s,PER_SERIES)
        mk.extend((s,m) for m in got)
    print(f"  {len(mk)} settled markets available; sampling within {BUDGET}s\n")
    started=time.time()
    rows=[]; looked=0
    for series,m in mk:
        if time.time()-started>BUDGET: break
        q=path(series,str(m.get("ticker")),m.get("close_time"))
        if ENTRY not in q: continue
        looked+=1
        ask=q[ENTRY][0]
        if not (LO_C/100.0 <= ask < HI_C/100.0): continue
        n=int(STAKE/ask)
        if n<1: continue
        cost=n*ask+fee(n,ask)
        # best exit available between T-8 and T-1
        best=0.0; bestoff=None
        for off in range(1,ENTRY):
            if off in q and q[off][2]>best:
                best=q[off][2]; bestoff=off
        res=str(m.get("result") or "").lower()
        hold=(n*1.0 if res=="yes" else 0.0)-cost
        sell=(n*best-fee(n,best))-cost if bestoff else -cost
        rows.append(dict(ask=ask,best=best,off=bestoff,n=n,cost=cost,
                         hold=hold,sell=sell,won=(res=="yes")))
        time.sleep(B.PAUSE)
    el=time.time()-started
    print(f"  {looked:,} markets with a T-{ENTRY} quote in {el:.0f}s; "
          f"{len(rows)} had YES in {LO_C:.0f}-{HI_C:.0f}c\n")
    if len(rows)<40:
        print("  too few to read -- raise the budget."); return 0
    def rep(lab,vals,risked):
        tot=sum(vals)
        print(f"  {lab:<34} net ${tot:>+9.2f}  ${tot/len(vals):>+7.3f}/bet  "
              f"ROI {100*tot/risked:>+6.2f}%")
    risked=sum(r["cost"] for r in rows)
    print(f"  {len(rows)} bets, ${risked:,.2f} staked, "
          f"mean entry {100*st.mean([r['ask'] for r in rows]):.1f}c")
    print(f"  mean BEST bid T-8..T-1: {100*st.mean([r['best'] for r in rows]):.1f}c")
    print(f"  the longshot actually won {100*sum(1 for r in rows if r['won'])/len(rows):.1f}% of the time\n")
    rep("HOLD to settlement",[r["hold"] for r in rows],risked)
    rep("SELL at the perfect moment",[r["sell"] for r in rows],risked)
    up=[r for r in rows if r["sell"]>0]
    print(f"\n  a profitable exit existed at some point in "
          f"{100*len(up)/len(rows):.1f}% of markets")
    fees=sum(fee(r["n"],r["ask"])+fee(r["n"],r["best"]) for r in rows)
    print(f"  fees on the round trip: ${fees:,.2f}, "
          f"{100*fees/risked:.2f}% of everything staked")
    print("\n"+"="*94)
    tot=sum(r["sell"] for r in rows)
    if tot<=0:
        print("  DEAD. Even selling the exact high of every market loses money")
        print("  after fees. No execution rule beats perfect timing, so there")
        print("  is nothing here to refine.")
    else:
        print("  NOT DEAD, AND NOT A STRATEGY. This is the best exit that ever")
        print("  existed, taken with hindsight nobody has. The real question is")
        print("  what a FIXED rule earns -- sell at 2x, or at a set price --")
        print("  and that will be a fraction of this.")
    print("="*94)
    return 0

if __name__=="__main__": sys.exit(main())
