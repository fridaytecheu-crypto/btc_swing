#!/bin/bash
# READ-ONLY status + forensics collection for V10 / V11-WARM / V11-LITE-WARM on the Mac (owner runs it). Reads journals, state,
# diagnostics, logs and launchd; runs only read-only CLI commands and the read-only forensic audit.
# Never stops, restarts or writes to a strategy root. Output: one text file to paste back.
set -u
cd ~/btc_swing || exit 1
OUT=/tmp/btc_status_$(date -u +%Y%m%dT%H%M%SZ).txt
exec > >(tee "$OUT") 2>&1
set -a; . ~/.config/btc_swing/forward.env; set +a
DATA="${BTC_DATA_DIR:?}"
echo "### COLLECTED $(date -u +%FT%TZ) · host $(hostname) · repo $(git rev-parse --short HEAD) ($(git log -1 --format=%cd --date=iso-strict)) · dirty: $(git status --porcelain | wc -l | tr -d ' ')"
echo "### c9f8a6d present: $(git cat-file -t c9f8a6d 2>/dev/null) · audit script: $(ls scripts/v11lw_forward_forensic_audit.py 2>&1)"
echo
echo "### LAUNCHD"
for a in com.btcswing.v10-forward com.btcswing.v11-warm-forward com.btcswing.v11-lite-warm-forward com.btcswing.v11-forward com.btcswing.v5-forward; do
  echo "-- $a"; launchctl print "gui/$(id -u)/$a" 2>&1 | grep -E "state =|pid =|last exit|runs =|path =" | sed 's/^/   /'
done
echo
echo "### ROOTS (pid, heartbeat, feed, journals, logs)"
python3 - "$DATA" <<'PY'
import json, os, sys, time, glob, re, math
from datetime import datetime, timezone
from collections import Counter
D = sys.argv[1]
now = time.time()
def iso(ms): return "n/a" if not ms else datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
def age(path):
    try: return f"{now-os.stat(path).st_mtime:.0f}s ago"
    except FileNotFoundError: return "MISSING"
def jl(path, kind=None):
    out=[]
    try:
        for l in open(path):
            l=l.strip()
            if not l: continue
            try: r=json.loads(l)
            except Exception: continue
            if kind is None or r.get("kind")==kind: out.append(r)
    except FileNotFoundError: pass
    return out
def perf(rows, pnl, rk, tk, hold_k, fee_k=None, fund_k=None, slip_k=None, label=""):
    def one(rs):
        n=len(rs)
        if not n: return {"n":0}
        net=[float(r[pnl]) for r in rs]; R=[float(r[rk]) for r in rs if r.get(rk) is not None]
        w=[x for x in net if x>0]; l=[x for x in net if x<=0]
        cum=peak=dd=0.0
        for x in net:
            cum+=x; peak=max(peak,cum); dd=max(dd,peak-cum)
        return {"n":n,"wins":len(w),"losses":len(l),"win_rate":round(len(w)/n,3),"net_usd":round(sum(net),2),
                "net_r_sum":round(sum(R),3) if R else None,"avg_r":round(sum(R)/len(R),3) if R else None,
                "pf":(round(sum(w)/-sum(l),2) if l and sum(l)<0 else ("inf" if w else None)),"max_dd_usd":round(dd,2),
                "avg_hold_min":round(sum(float(r.get(hold_k) or 0) for r in rs)/n,1),
                "fees_usd":round(sum(float(r.get(fee_k) or 0) for r in rs),2) if fee_k else "n/a",
                "funding_usd":round(sum(float(r.get(fund_k) or 0) for r in rs),2) if fund_k else "n/a",
                "slip_usd":round(sum(float(r.get(slip_k) or 0) for r in rs),2) if slip_k else "n/a"}
    out={}
    for name,h in (("24h",24),("72h",72),("since_launch",None)):
        rs=[r for r in rows if h is None or float(r[tk])>=(now-h*3600)*1000]
        out[name]=one(rs)
    print(f"   {label}: {json.dumps(out)}")
roots={"V10 (forward_v10)":"forward_v10","V11-WARM (forward_v11_warm)":"forward_v11_warm","V11-LITE-WARM (forward_v11_lite_warm)":"forward_v11_lite_warm"}
for label,r in roots.items():
    root=f"{D}/btc/{r}"
    print(f"\n== {label}  root={root}  exists={os.path.isdir(root)}")
    if not os.path.isdir(root): continue
    pidf=f"{root}/forward_run.pid"
    pid=open(pidf).read().strip() if os.path.exists(pidf) else None
    alive=False
    if pid:
        try: os.kill(int(pid),0); alive=True
        except Exception: alive=False
    print(f"   pid file: {pid} alive={alive}  lock: {age(root+'/forward_run.lock')}")
    if alive:
        os.system(f"ps -o pid,etime,lstart,rss,command -p {pid} | sed 's/^/   /'")
    diag=f"{root}/derived/diagnostic.json"
    try:
        d=json.load(open(diag)); print(f"   diagnostic: written {d.get('written_at')} ({age(diag)}) mode={d.get('mode')} bars={d.get('bars')} last_bar={d.get('last_bar')} in_window={d.get('in_window')} queue={d.get('queue')} errors={d.get('errors')} position={'yes' if d.get('position') or d.get('open_position') else 'no'}")
        if d.get("scores"): print(f"   scores: long={d['scores'].get('long')} short={d['scores'].get('short')}")
        if d.get("warm"): print(f"   warm: {d['warm']}")
    except Exception as e: print(f"   diagnostic: {e}")
    raw=sorted(glob.glob(f"{root}/raw/BTCUSDT/*/*.jsonl*"), key=os.path.getmtime)
    print(f"   raw feed: newest {raw[-1] if raw else 'NONE'} {age(raw[-1]) if raw else ''}  files={len(raw)}")
    for j in ("episodes","signals","shadow_trades","candidates","nearmiss","outcomes","control_twin"):
        p=f"{root}/journals/{j}.jsonl"
        if os.path.exists(p):
            rows=jl(p); print(f"   journal {j}: {len(rows)} recs, {age(p)}, kinds={dict(Counter(x.get('kind') for x in rows))}")
    try:
        st=json.load(open(f"{root}/state.json"))
        print(f"   state ({age(root+'/state.json')}): obs_start={iso(st.get('observation_start_ms'))} last_event={iso(st.get('last_event_ms'))} last_bar={iso(st.get('last_bar_ms'))} last_tick={iso(st.get('last_tick_ms'))}")
        print(f"   counters: {st.get('counters')}")
        print(f"   last_signal: {st.get('last_signal')}")
        print(f"   open shadow position: {st.get('position') or st.get('shadow_position')}")
        print(f"   totals: trades={st.get('trades')} wins={st.get('wins') or st.get('shadow_wins')} losses={st.get('losses') or st.get('shadow_losses')} cum_net_usd={st.get('cum_net_usd') or st.get('cum_shadow_net_usd')}")
        if 'twin' in st: print(f"   control twin: trades={st['twin'].get('trades')} cum_net_usd={st['twin'].get('cum_net_usd')} wins={st['twin'].get('wins')} losses={st['twin'].get('losses')} position={st['twin'].get('position') is not None}")
        if 'blocked_until_ms' in st: print(f"   V10 blocked_until={iso(st.get('blocked_until_ms'))} reason={st.get('blocked_reason')} consecutive_full_losses={st.get('consecutive_full_losses')} day_realised={st.get('day_realised_usd')}")
    except Exception as e: print(f"   state: {e}")
    sh=[x["data"] for x in jl(f"{root}/journals/shadow_trades.jsonl","SHADOW_CLOSED")]
    if sh:
        last=sh[-1]; print(f"   last SHADOW_CLOSED: {iso(last.get('exit_ms'))} side={last.get('side')} fam/tmpl={last.get('family') or last.get('selected_template')} reason={last.get('exit_reason')} net_usd={round(float(last.get('net_usd',0)),2)} net_r={round(float(last.get('net_r',0)),3)}")
        perf(sh,"net_usd","net_r","exit_ms","hold_min","fee_usd","funding_usd","slip_usd",label="SHADOW performance")
    else: print("   no closed shadow trades")
    tw=[x["data"] for x in jl(f"{root}/journals/control_twin.jsonl","TWIN_CLOSED")]
    if tw: perf(tw,"net_usd","net_r","exit_ms","hold_min","fee_usd","funding_usd","slip_usd",label="CONTROL TWIN (strict V11) performance")
    dt=f"{root}/demo/demo_trades.jsonl"
    if os.path.exists(dt):
        rows=[]
        for l in open(dt):
            try: rows.append(json.loads(l))
            except Exception: pass
        print(f"   DEMO trades file: {len(rows)} closed ({age(dt)})")
        if rows:
            last=rows[-1]; print(f"   last DEMO closed: {last.get('closed_ts')} side={last.get('side')} fam={last.get('family') or last.get('selected_template')} reason={last.get('exit_reason')} net={round(float(last.get('net_pnl',0)),2)} R={last.get('R_net_vs_planned')}")
            perf(rows,"net_pnl","R_net_vs_planned","closed_ms","hold_min","fees","funding",label="DEMO (Bybit demo, real executions on paper money) performance")
    dj=f"{root}/demo/strategy_journal.jsonl"
    if os.path.exists(dj):
        rows=jl(dj); k=Counter(x.get("kind") for x in rows)
        print(f"   DEMO journal: {len(rows)} recs ({age(dj)}) kinds={dict(k)}")
        bad=[x for x in rows if str(x.get('kind','')).startswith('ALERT_') or x.get('kind') in ('api_error','ENTRY_REJECTED','MANAGE_ERROR','DEMO_ENTRY_BLOCKED','ENTRY_NOT_FILLED')]
        for x in bad[-12:]: print(f"      {x.get('ts')} {x.get('kind')} {json.dumps(x.get('data'))[:220]}")
        ds=f"{root}/demo/strategy_state.json"
        if os.path.exists(ds):
            s=json.load(open(ds)); print(f"   DEMO state: position={'yes' if s.get('position') else 'no'} reconcile_required={s.get('reconcile_required')} ({s.get('reconcile_reason')}) halted={s.get('halted')} executed_24h={len([t for t in s.get('executed_ms',[]) if now*1000-t<86400000])} closed={s.get('closed_trades')} cum_net={s.get('cum_net_pnl')} day={s.get('day')} day_realised={s.get('day_realised_usd')} consec_full_losses={s.get('consecutive_full_losses')}")
    log=f"{root}/logs/forward_run.log"
    if os.path.exists(log):
        lines=open(log,errors="replace").read().splitlines()
        err=[l for l in lines if re.search(r"ERROR|Traceback|Exception|CRITICAL|reconnect|disconnect|gap|hole|stale",l,re.I)]
        print(f"   log: {len(lines)} lines ({age(log)}); error/reconnect-like lines total={len(err)}; last 10:")
        for l in err[-10:]: print("      "+l[:200])
        print("   log tail:"); [print("      "+l[:200]) for l in lines[-3:]]
PY
echo
echo "### CPU / THREADS of the runner processes (read-only sampling; 3 x 5 s)"
for root in forward_v10 forward_v11_warm forward_v11_lite_warm; do
  f="$DATA/btc/$root/forward_run.pid"; [ -f "$f" ] || continue; pid=$(cat "$f")
  echo "-- $root pid $pid"; ps -o pid,%cpu,%mem,etime,command -p "$pid" 2>/dev/null | sed 's/^/   /'
  echo "   threads: $(ps -M -p "$pid" 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')  open files: $(lsof -p "$pid" 2>/dev/null | wc -l | tr -d ' ')"
  top -l 3 -s 5 -pid "$pid" -stats pid,cpu,th,mem,command 2>/dev/null | grep -E "^[0-9]" | sed 's/^/   top: /'
done
echo
echo "### READ-ONLY CLI STATUS"
for c in "v10 forward status --live" "v10 demo status --live" "v11w forward status" "v11w forward verify-warm" "v11lw forward status" "v11lw forward verify-warm" "v11lw demo status" "v10 forward trades" "v11w forward trades" "v11lw forward trades"; do
  echo; echo "-- btc-swing $c"; set -a; . ~/.config/btc_swing/demo.env 2>/dev/null; set +a; uv run btc-swing $c 2>&1 | head -120
done
echo
echo "### FORENSIC AUDIT (read-only; writes only reports/forward_v11_lite_warm/forensic_*.{json,md})"
uv run python scripts/v11lw_forward_forensic_audit.py 2>&1
echo
echo "### CROSS-STRATEGY FORENSICS (read-only; writes only reports/strategy_forensics/<run>/)"
uv run python scripts/strategy_forensics.py 2>&1 | tail -60
echo
echo "### LITE compare report (read-only)"
uv run btc-swing v11lw report compare 2>&1 | tail -5
echo
echo "### DONE -> $OUT"
