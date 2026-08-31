#!/usr/bin/env python3
import csv, io, json, math, os, re, sys
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

BASE = '/home/user/workspace/cron_tracking/ecb64bd3'
STATE_PATH = os.path.join(BASE, 'global_liquidity_tracker_state.json')
FRED_GROUPS = {
    'liquidity_m2': 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=WALCL,WRESBAL,WTREGEN,RRPONTSYD,WM2NS',
    'spreads_conditions': 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLC0A0CM,BAMLH0A0HYM2,NFCI,ANFCI',
    'rates_funding': 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2,DGS10,T10Y2Y,SOFR,EFFR,IORB',
}
FRED_URL = '; '.join(FRED_GROUPS.values())
STABLE_URL = 'https://stablecoins.llama.fi/stablecoins?includePrices=true'
ECB_URL = 'https://www.ecb.europa.eu/press/pr/wfs/html/index.en.html'

CORE = ['WALCL','WRESBAL','WTREGEN','RRPONTSYD','WM2NS','BAMLC0A0CM','BAMLH0A0HYM2','NFCI','ANFCI','DGS2','DGS10','T10Y2Y','SOFR','EFFR','IORB']
BALANCE_MILLIONS = {'WALCL','WRESBAL','WTREGEN'}

def now_utc(): return datetime.now(timezone.utc)
def parse_date(s): return datetime.strptime(s, '%Y-%m-%d').date()
def finite(x): return x is not None and math.isfinite(x)
def load_state():
    try:
        with open(STATE_PATH) as f: return json.load(f)
    except Exception: return {'failures': {}, 'sources': {}}
def save_json(path, obj):
    with open(path, 'w') as f: json.dump(obj, f, indent=2, allow_nan=False)

def fetch(url, outpath, timeout=30):
    try:
        req = Request(url, headers={'User-Agent':'GlobalLiquidityTracker/1.0'})
        with urlopen(req, timeout=timeout) as r:
            data = r.read()
        with open(outpath, 'wb') as f: f.write(data)
        return data, None
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'

def parse_fred(data):
    text = data.decode('utf-8-sig', errors='replace')
    rows = list(csv.DictReader(io.StringIO(text)))
    series = {k: [] for k in CORE}
    for row in rows:
        ds = row.get('observation_date') or row.get('DATE')
        if not ds: continue
        try: d = parse_date(ds)
        except Exception: continue
        for k in CORE:
            raw = row.get(k)
            if raw is None or raw in ('', '.', 'NaN'): continue
            try:
                v = float(raw)
                if finite(v): series[k].append((d, v))
            except Exception: pass
    for k in series: series[k].sort()
    return series

def obs_with_week_prior(items):
    if not items: return (None, None)
    latest = items[-1]
    target = latest[0] - timedelta(days=7)
    eligible = [x for x in items[:-1] if x[0] <= target]
    prior = eligible[-1] if eligible else (items[-2] if len(items) >= 2 else None)
    return latest, prior

def stable_total(payload):
    # DefiLlama's endpoint normally exposes peggedAssets with circulating or mcap values.
    assets = payload.get('peggedAssets') if isinstance(payload, dict) else None
    if not isinstance(assets, list): return None, None
    total = 0.0; found = 0
    for a in assets:
        if not isinstance(a, dict): continue
        val = None
        for key in ('circulating','mcap','marketCap','market_cap'):
            x = a.get(key)
            if isinstance(x, (int,float)): val = float(x); break
            if isinstance(x, dict):
                for sub in ('peggedUSD','usd','value'):
                    if isinstance(x.get(sub), (int,float)): val = float(x[sub]); break
                if val is not None: break
        if finite(val): total += val; found += 1
    return (total if found else None), found

def ecb_headline(html):
    if not html: return None
    txt = re.sub(r'<script[\s\S]*?</script>|<style[\s\S]*?</style>', ' ', html, flags=re.I)
    txt = re.sub(r'<[^>]+>', ' ', txt)
    txt = re.sub(r'\s+', ' ', txt).strip()
    dates = re.findall(r'\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+20\d{2}\b', txt)
    dated = []
    for d in dates:
        try:
            dt = datetime.strptime(d, '%d %B %Y').date()
            if dt <= now_utc().date():
                dated.append((dt, d))
        except Exception:
            pass
    latest_date = max(dated)[1] if dated else None
    if latest_date:
        pos = txt.rfind(latest_date)
        return {'date_text': latest_date, 'headline': txt[max(0,pos-120):pos+250]}
    return {'date_text': None, 'headline': txt[:350]}

def main():
    # Guard the programmatic trigger: Friday at 07:00 in America/Chicago.
    tz_check = os.popen("TZ=America/Chicago date '+%u %H'").read().strip().split()
    if tz_check != ['5','07']:
        print(json.dumps({'ran': False, 'reason': 'outside_trigger_window'})); return 0
    state = load_state()
    failures = dict(state.get('failures') or {})
    sources = dict(state.get('sources') or {})
    sources.update({'fred':FRED_URL, 'fred_liquidity_m2': FRED_GROUPS['liquidity_m2'],
                    'fred_spreads_conditions': FRED_GROUPS['spreads_conditions'],
                    'fred_rates_funding': FRED_GROUPS['rates_funding'],
                    'stablecoins':STABLE_URL, 'ecb_wfs_index':ECB_URL})
    run = {'run_utc': now_utc().replace(microsecond=0).isoformat().replace('+00:00','Z'), 'sources': sources}

    # FRED is intentionally split into three narrow direct pulls. Retry only
    # the group(s) that fail on the first attempt, once each.
    fred_series = {k: [] for k in CORE}
    fred_group_errors = {}
    fred_payloads = {}
    for group, url in FRED_GROUPS.items():
        data, err = fetch(url, os.path.join(BASE, f'fred_{group}.csv'))
        if err:
            data, err2 = fetch(url, os.path.join(BASE, f'fred_{group}_retry.csv'))
            if err2:
                fred_group_errors[group] = {'first': err, 'retry': err2}
                continue
        if data:
            fred_payloads[group] = data
            parsed = parse_fred(data)
            for k, values in parsed.items():
                fred_series[k] = values
    fred_available = any(fred_series.values())
    if fred_group_errors:
        failures['fred'] = failures.get('fred',0)+1
        run['fred_errors'] = fred_group_errors
    else:
        failures['fred'] = 0
    fred_raw = b''.join(fred_payloads.values()) if fred_payloads else None
    fred_err = '; '.join(f'{g}: {v["retry"]}' for g, v in fred_group_errors.items()) or None
    if fred_available:
        save_json(os.path.join(BASE,'fred_last_available.json'), {k: ({'date':x[-1][0].isoformat(),'value':x[-1][1]} if x else None) for k,x in fred_series.items()})
        with open(os.path.join(BASE,'fred_panel.csv'),'w',newline='') as f:
            w=csv.writer(f); w.writerow(['series','latest_date','latest_value','prior_week_date','prior_week_value'])
            for k,x in fred_series.items():
                la,pr=obs_with_week_prior(x); w.writerow([k, la[0].isoformat() if la else '', la[1] if la else '', pr[0].isoformat() if pr else '', pr[1] if pr else ''])

    stable_raw, stable_err = fetch(STABLE_URL, os.path.join(BASE,'stablecoins_raw.json'))
    stable_total = None; stable_count = None
    if stable_err:
        failures['stablecoins'] = failures.get('stablecoins',0)+1
        run['stablecoins_error'] = stable_err
    else:
        failures['stablecoins'] = 0
        try:
            stable_payload = json.loads(stable_raw.decode('utf-8'))
            stable_total, stable_count = stable_total_fn = stable_total_from_payload(stable_payload)
            run['stablecoins_assets_count'] = stable_count
        except Exception as e:
            failures['stablecoins'] = failures.get('stablecoins',0)+1
            run['stablecoins_error'] = f'parse error: {type(e).__name__}: {e}'

    ecb_raw, ecb_err = fetch(ECB_URL, os.path.join(BASE,'ecb_wfs_index.html'))
    if ecb_err:
        failures['ecb'] = failures.get('ecb',0)+1
        run['ecb_error'] = ecb_err
        ecb_info = None
    else:
        failures['ecb'] = 0
        ecb_info = ecb_headline(ecb_raw.decode('utf-8','replace'))
        save_json(os.path.join(BASE,'ecb_latest.json'), ecb_info or {})

    # Build current observation snapshot. Preserve the last complete snapshot
    # when a direct source times out, rather than replacing it with nulls.
    old_latest = dict(state.get('latest') or {})
    # A prior run may have been interrupted after replacing latest with a
    # partial snapshot. Recover the last complete weekly observation.
    if not finite(old_latest.get('fed_net_liquidity_bil')):
        recovered = state.get('prior') or {}
        if finite(recovered.get('fed_net_liquidity_bil')):
            old_latest = dict(recovered)
    # Start from the last complete snapshot so a failed group does not erase
    # otherwise valid observations from prior runs.
    latest = dict(old_latest)
    dates = []
    prior_dates = []
    if fred_available:
        for k in CORE:
            la, pr = obs_with_week_prior(fred_series.get(k, []))
            if not la: continue
            dates.append(la[0])
            raw_latest, raw_prior = la[1], pr[1] if pr else None
            if pr: prior_dates.append(pr[0])
            raw_diff = raw_latest - raw_prior if finite(raw_prior) else None
            if k in BALANCE_MILLIONS:
                latest[k+'_bil'] = raw_latest/1000.0
                latest[k+'_wow_bil'] = raw_diff/1000.0 if finite(raw_diff) else None
            elif k in {'RRPONTSYD','WM2NS'}:
                latest[('M2' if k=='WM2NS' else k)+'_bil'] = raw_latest
                latest[('M2' if k=='WM2NS' else k)+'_wow_bil'] = raw_diff if finite(raw_diff) else None
                if k=='WM2NS': latest['M2_wow_pct'] = raw_diff/raw_prior if finite(raw_diff) and finite(raw_prior) and raw_prior else None
            elif k in {'BAMLC0A0CM','BAMLH0A0HYM2'}:
                nm = 'BAMLC0A0CM' if k=='BAMLC0A0CM' else 'BAMLH0A0HYM2'
                latest[nm+'_pct'] = raw_latest
                latest[nm+'_wow_bps'] = raw_diff*100 if finite(raw_diff) else None
            elif k in {'DGS2','DGS10','T10Y2Y','SOFR','EFFR','IORB'}:
                latest[k+'_pct'] = raw_latest
                latest[k+'_wow_bps'] = raw_diff*100 if finite(raw_diff) else None
            else:
                latest[k] = raw_latest
                latest[k+'_wow'] = raw_diff if finite(raw_diff) else None
            latest[k+'_date'] = la[0].isoformat()
        if all(finite(latest.get(x)) for x in ['WALCL_bil','WTREGEN_bil','RRPONTSYD_bil']):
            latest['fed_net_liquidity_bil'] = latest['WALCL_bil']-latest['WTREGEN_bil']-latest['RRPONTSYD_bil']
            if all(finite(latest.get(x)) for x in ['WALCL_wow_bil','WTREGEN_wow_bil','RRPONTSYD_wow_bil']):
                latest['fed_net_liquidity_wow_bil'] = latest['WALCL_wow_bil']-latest['WTREGEN_wow_bil']-latest['RRPONTSYD_wow_bil']
        for pair in [('SOFR_pct','IORB_pct','SOFR_minus_IORB_bps'),('SOFR_pct','EFFR_pct','SOFR_minus_EFFR_bps')]:
            a,b,n=pair
            if finite(latest.get(a)) and finite(latest.get(b)): latest[n]=(latest[a]-latest[b])*100
        for pair in [('SOFR_minus_IORB_bps','SOFR_minus_IORB_wow_bps'),('SOFR_minus_EFFR_bps','SOFR_minus_EFFR_wow_bps')]:
            n,wn=pair
            # Compare current spread with prior-week values where both inputs are available.
            if n in latest and finite(latest.get('SOFR_wow_bps')) and finite(latest.get('IORB_wow_bps')) and n=='SOFR_minus_IORB_bps': latest[wn]=latest['SOFR_wow_bps']-latest['IORB_wow_bps']
            if n in latest and finite(latest.get('SOFR_wow_bps')) and finite(latest.get('EFFR_wow_bps')) and n=='SOFR_minus_EFFR_bps': latest[wn]=latest['SOFR_wow_bps']-latest['EFFR_wow_bps']
    latest['asof_date'] = max(dates).isoformat() if dates else old_latest.get('asof_date')
    latest['asof_prior_date'] = max(prior_dates).isoformat() if prior_dates else old_latest.get('asof_prior_date')
    if stable_total is not None:
        latest['stablecoins_total_usd'] = stable_total
        latest['stablecoins_assets_count'] = stable_count
        old = (state.get('latest') or {}).get('stablecoins_total_usd')
        if finite(old):
            latest['stablecoins_wow_usd'] = stable_total-old
            latest['stablecoins_wow_pct'] = (stable_total-old)/old if old else None
        else:
            latest['stablecoins_wow_usd'] = None; latest['stablecoins_wow_pct'] = None
    else:
        latest['stablecoins_total_usd'] = old_latest.get('stablecoins_total_usd')
        latest['stablecoins_wow_usd'] = None; latest['stablecoins_wow_pct'] = None
    if ecb_info:
        latest['ecb_wfs_date'] = ecb_info.get('date_text')
        latest['ecb_headline'] = ecb_info.get('headline')
    elif not old_latest: latest['ecb_wfs_date'] = None

    # Threshold alerts.
    alerts=[]
    def absge(key, threshold, label):
        v=latest.get(key)
        if finite(v) and abs(v)>=threshold: alerts.append(label)
    # Stablecoins are an independent direct source, so retain their alert
    # checks even when all FRED groups are unavailable after their retries.
    if finite(latest.get('stablecoins_wow_pct')) and abs(latest['stablecoins_wow_pct']) >= .01: alerts.append('Stablecoin supply WoW >= 1%')
    if finite(latest.get('stablecoins_wow_usd')) and abs(latest['stablecoins_wow_usd']) >= 2e9: alerts.append('Stablecoin supply WoW >= $2B')
    if fred_available:
        absge('fed_net_liquidity_wow_bil',100,'Fed net liquidity WoW >= $100B')
        for k in ('WALCL','WRESBAL','WTREGEN','RRPONTSYD'):
            absge(k+'_wow_bil',100,f'{k} component WoW >= $100B')
        if finite(latest.get('WRESBAL_wow_bil')) and latest['WRESBAL_wow_bil'] <= -75: alerts.append('WRESBAL down >= $75B')
        if finite(latest.get('WRESBAL_bil')) and latest['WRESBAL_bil'] < 3000: alerts.append('WRESBAL below $3.0T')
        if finite(latest.get('M2_wow_pct')) and abs(latest['M2_wow_pct']) >= 0.005: alerts.append('M2 WoW pct move >= 0.5%')
        absge('BAMLC0A0CM_wow_bps',10,'IG spread move >= 10 bps'); absge('BAMLH0A0HYM2_wow_bps',25,'HY spread move >= 25 bps')
        absge('NFCI_wow',.10,'NFCI move >= 0.10 points'); absge('ANFCI_wow',.10,'ANFCI move >= 0.10 points')
        absge('DGS2_wow_bps',20,'2Y yield move >= 20 bps'); absge('DGS10_wow_bps',20,'10Y yield move >= 20 bps'); absge('T10Y2Y_wow_bps',15,'10Y-2Y move >= 15 bps')
        for k in ('SOFR','EFFR','IORB'): absge(k+'_wow_bps',10,f'{k} move >= 10 bps')
        for k in ('SOFR_minus_IORB','SOFR_minus_EFFR'):
            v=latest.get(k+'_wow_bps')
            if finite(v) and v >= 5: alerts.append(f'{k.replace("_","-")} spread widens >= 5 bps')

    # Cheap signal score, one point per available signal, clamped.
    score=0; signal_notes=[]
    def signal(condition, pos, neg, name):
        nonlocal score
        if condition is True: score += 1; signal_notes.append('+'+name)
        elif condition is False: score -= 1; signal_notes.append('-'+name)
    signal(latest.get('fed_net_liquidity_wow_bil')>0 if finite(latest.get('fed_net_liquidity_wow_bil')) and latest.get('fed_net_liquidity_wow_bil') != 0 else None, True, False, 'Fed net liquidity')
    signal(latest.get('M2_wow_bil')>0 if finite(latest.get('M2_wow_bil')) and latest.get('M2_wow_bil') != 0 else None, True, False, 'M2')
    signal(latest.get('stablecoins_wow_usd')>0 if finite(latest.get('stablecoins_wow_usd')) and latest.get('stablecoins_wow_usd') != 0 else None, True, False, 'stablecoins')
    signal(latest.get('BAMLC0A0CM_wow_bps')<0 if finite(latest.get('BAMLC0A0CM_wow_bps')) else None, True, False, 'IG spreads')
    signal(latest.get('BAMLH0A0HYM2_wow_bps')<0 if finite(latest.get('BAMLH0A0HYM2_wow_bps')) else None, True, False, 'HY spreads')
    signal(latest.get('NFCI_wow')<0 if finite(latest.get('NFCI_wow')) else None, True, False, 'NFCI')
    signal(latest.get('ANFCI_wow')<0 if finite(latest.get('ANFCI_wow')) else None, True, False, 'ANFCI')
    signal(latest.get('WRESBAL_wow_bil')>=0 if finite(latest.get('WRESBAL_wow_bil')) else None, True, False, 'reserves')
    funding = None
    if finite(latest.get('SOFR_minus_IORB_wow_bps')): funding = latest['SOFR_minus_IORB_wow_bps'] <= 0
    elif finite(latest.get('SOFR_wow_bps')): funding = latest['SOFR_wow_bps'] <= 0
    signal(funding, True, False, 'funding')
    score=max(-5,min(5,score))
    label='expanding' if score>=2 else ('contracting' if score<=-2 else 'neutral')
    old_score=old_latest.get('regime_score')
    old_label=old_latest.get('regime_label')
    if fred_available:
        if finite(old_score) and abs(score-old_score)>=2: alerts.append('Regime score changed by >= 2')
        if old_label and old_label != label: alerts.append('Regime label changed')
    latest['regime_score']=score; latest['regime_label']=label; latest['alerts']=alerts
    fresh = bool(fred_available and latest.get('asof_date') and latest.get('asof_date') != old_latest.get('asof_date'))
    material = bool(alerts)
    # Keep a bounded prior/current history: latest and previous snapshot only.
    state_out = {
        'last_run_utc': run['run_utc'], 'last_notification_date': state.get('last_notification_date'),
        'sources': sources, 'latest': latest, 'prior': old_latest or state.get('prior') or {}, 'failures': failures,
        'signal_notes': signal_notes
    }
    save_json(STATE_PATH, state_out)
    save_json(os.path.join(BASE,'run_summary.json'), {'fresh':fresh,'material':material,'asof_date':latest.get('asof_date'),'asof_prior_date':latest.get('asof_prior_date'),'alerts':alerts,'regime_score':score,'regime_label':label,'signal_notes':signal_notes,'failures':failures})
    save_json(os.path.join(BASE,'last_pull_raw.json'), {'run_utc':run['run_utc'],'fred_error':fred_err,'stablecoins_error':stable_err,'ecb_error':ecb_err,'fred_rows':sum(len(v) for v in fred_series.values()) if fred_series else 0,'stablecoins_total_usd':stable_total,'stablecoins_assets_count':stable_count,'ecb':ecb_info})
    print(json.dumps({'ran':True,'fresh':fresh,'material':material,'latest':latest,'alerts':alerts,'signal_notes':signal_notes,'failures':failures,'stablecoins_error':stable_err,'ecb_error':ecb_err}, default=str))
    return 0

# Alias kept separate so the stablecoin parser is easy to patch if the upstream schema changes.
def stable_total_from_payload(payload): return stable_total(payload)

if __name__ == '__main__': sys.exit(main())
