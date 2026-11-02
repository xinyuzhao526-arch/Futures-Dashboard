import pandas as pd, numpy as np, json

import os as _os
BASE = _os.environ.get('FD_BASE', '/workspace/automations/futures-dashboard/')
if not BASE.endswith('/'): BASE += '/'
ROLL_TH = 0.11   # daily |log return| above this is treated as an unadjusted rollover gap

q = pd.read_csv(BASE+'data/quotes_daily.csv')
s = pd.read_csv(BASE+'data/snapshot_latest.csv')
seats = pd.read_csv(BASE+'data/seats_latest.csv')

# 补三个此前缺失的品种（郑商所 CF/PF/SF）
import os
_mq = BASE+'data/missing_quotes.csv'
if os.path.exists(_mq):
    q = pd.concat([q, pd.read_csv(_mq)], ignore_index=True)
    s = pd.concat([s, pd.read_csv(BASE+'data/missing_snapshot.csv')], ignore_index=True)
    seats = pd.concat([seats, pd.read_csv(BASE+'data/missing_seats.csv')], ignore_index=True)

q['交易日'] = pd.to_datetime(q['交易日'])
q = q.rename(columns={'收盘':'close','settle':'settle','成交量':'vol','持仓量(手)':'oi','上市交易所':'exch'})
q = q.sort_values(['variety_code','交易日'])

s = s.drop_duplicates(subset=['variety_code']).rename(
    columns={'成交量':'vol','持仓量(手)':'oi','oi_chg':'oi_chg','成交额':'turnover'})
s = s[s['vol'] > 0].copy()
s['mult'] = s['turnover'] / (s['vol'] * s['settle'])     # implied multiplier, validated vs contract specs
_mp = BASE+'data/multipliers.csv'
if os.path.exists(_mp):
    _mt = pd.read_csv(_mp)
    _mt['variety_code'] = _mt['variety_code'].astype(str).str.strip().str.upper()
    _ref = _mt.set_index('variety_code')['multiplier'].to_dict()
    s['mult'] = s['variety_code'].str.upper().map(_ref).fillna(s['mult'])
snap = s.set_index('variety_code')

names = s.set_index('variety_code')['variety_name'].to_dict()
names.update(q.dropna(subset=['variety_name']).drop_duplicates('variety_code')
             .set_index('variety_code')['variety_name'].to_dict())
s['variety_name'] = s['variety_code'].map(names)
NAME_FIX = {'PR':'瓶片','OP':'胶版印刷纸','PL':'丙烯','PM':'普麦','PF':'涤纶短纤'}
names.update(NAME_FIX)
s['variety_name'] = s['variety_code'].map(names).fillna(s['variety_name'])
seats['variety_name'] = seats['variety_code'].map(names).fillna(seats['variety_name'])

SECTOR = {
 '贵金属': ['AU','AG','PT','PD'],
 '有色':   ['CU','AL','ZN','PB','NI','SN','BC','AO','AD'],
 '黑色':   ['RB','HC','I','J','JM','SF','SM','SS','WR','ZC'],
 '能源':   ['SC','LU','FU','BU','PG','EC'],
 '化工':   ['TA','MA','EG','EB','PP','L','V','RU','NR','BR','FG','SA','UR','PX','SH','PR','BZ','SP','OP','PF'],
 '农产品': ['A','B','M','Y','P','C','CS','JD','LH','OI','RM','RS','PL','WH','RI','JR','LR','RR','PK','LG','BB','FB'],
 '软商品': ['CF','CY','SR','AP','CJ'],
 '新能源': ['SI','LC','PS'],
}
sec_of = {c:k for k,v in SECTOR.items() for c in v}

ASOF = q['交易日'].max()
roll_count = 0
rows = []
for code, g in q.groupby('variety_code'):
    g = g.sort_values('交易日').reset_index(drop=True)
    if len(g) < 130 or code not in snap.index: continue
    S = g['settle'].values.astype(float)
    OI = g['oi'].values.astype(float)
    VOL = g['vol'].values.astype(float)
    r = np.diff(np.log(S))
    roll = np.abs(r) > ROLL_TH
    roll_count += int(roll.sum())
    r_c = np.where(roll, 0.0, r)
    P = np.exp(np.concatenate([[0.0], np.cumsum(r_c)])) * S[-1]

    m = float(snap.at[code, 'mult'])
    sd = np.std(r_c[-60:], ddof=1)
    if not np.isfinite(sd) or sd <= 0: continue
    lp = np.log(P)
    sig = {k: (lp[-1]-lp[-1-k])/(sd*np.sqrt(k)) for k in (20,60,120)}
    T = 100*np.tanh(0.8*(0.5*sig[20] + 0.3*sig[60] + 0.2*sig[120]))

    ratio = np.where(OI>0, VOL/np.maximum(OI,1), np.nan)
    r20 = pd.Series(r_c).rolling(20).std().values
    def pct_last(x, w=250):
        x = pd.Series(x).iloc[-w:]; x = x[np.isfinite(x)]
        if len(x) < 60: return np.nan
        return 100.0*(x < x.iloc[-1]).mean()
    p1, p2, p3 = pct_last(OI), pct_last(ratio), pct_last(r20)
    pv = [v for v in (p1,p2,p3) if np.isfinite(v)]
    C = float(np.mean(pv)) if len(pv) >= 2 else np.nan

    settle_now = float(snap.at[code, 'settle'])
    oi_chg = float(snap.at[code, 'oi_chg'])
    pct = float(snap.at[code, 'pct_chg']) / 100.0
    prev_settle = settle_now / (1.0 + pct) if pct > -1 else settle_now
    oi_prev = float(snap.at[code, 'oi']) - oi_chg
    vol_eff = oi_chg * settle_now * m
    price_eff = oi_prev * (settle_now - prev_settle) * m
    capital = float(snap.at[code, 'oi']) * settle_now * m / 1e8

    tot = 0.0; ok = True
    for i in range(len(S)-5, len(S)):
        if i < 1: ok = False; break
        if roll[i-1]: continue
        dS = S[i]-S[i-1]; dOI = OI[i]-OI[i-1]
        if abs(dOI)/max(OI[i-1],1) > 0.25: ok = False; break
        tot += dOI*S[i]*m + OI[i-1]*dS*m

    rows.append(dict(code=code, T=T, C=C, p1=p1, p2=p2, p3=p3,
                     flow=(vol_eff+price_eff)/1e8, vol_eff=vol_eff/1e8, price_eff=price_eff/1e8,
                     capital=capital, flow5=(tot/1e8 if ok else np.nan),
                     chg20=100*(P[-1]/P[-21]-1),
                     roll_last=bool(roll[-1]) if len(roll) else False,
                     n_roll=int(roll.sum()), mult=m))
ind = pd.DataFrame(rows)

df = s[['variety_code','variety_name','settle','prev_settle','pct_chg','vol','oi','oi_chg']].rename(columns={'variety_code':'code'})
df = df.merge(ind, on='code', how='inner')
df = df.merge(seats[['variety_code','long_top20','short_top20','total_oi']].rename(columns={'variety_code':'code'}),
              on='code', how='left')
df['seat_net'] = (df['long_top20']-df['short_top20'])/(df['long_top20']+df['short_top20'])
df['seat_conc'] = (df['long_top20']+df['short_top20'])/(2*df['total_oi'])
df['sector'] = df['code'].map(sec_of)
df = df.dropna(subset=['sector','T','flow'])

def dims(r):
    t = 0 if abs(r['T']) < 20 else (1 if r['T'] > 0 else -1)
    f = 0
    if np.isfinite(r['flow']) and r['capital'] > 0 and abs(r['flow'])/r['capital'] >= 0.003:
        f = 1 if r['flow'] > 0 else -1
    sn = 0 if (not np.isfinite(r['seat_net']) or abs(r['seat_net']) < 0.03) else (1 if r['seat_net'] > 0 else -1)
    return t, f, sn

out_rows = []
for _, r in df.iterrows():
    t, f, sn = dims(r)
    act = [d for d in (t,f,sn) if d != 0]
    if not act:
        out_rows.append(dict(dir=0, vote=0, n_act=0, d_trend=t, d_flow=f, d_seat=sn, vlabel='中性'))
        continue
    tot = sum(act)
    dirn = 0 if tot == 0 else (1 if tot > 0 else -1)
    agree = sum(1 for d in act if d == dirn) if dirn != 0 else 0
    if dirn == 0: lab = '三向分裂'
    elif agree == 3: lab = '三维共振'
    elif len(act) == 2: lab = '两维同向'
    elif agree == 2: lab = '两维同向'
    else: lab = '一维主导'
    out_rows.append(dict(dir=dirn, vote=agree, n_act=len(act), d_trend=t, d_flow=f, d_seat=sn, vlabel=lab))
df = df.join(pd.DataFrame(out_rows, index=df.index))

flags = []
for _, r in df.iterrows():
    rs = []
    if abs(r['T'])>60 and np.isfinite(r['flow']) and np.sign(r['T'])!=np.sign(r['flow']): rs.append('趋势与资金反向')
    if np.isfinite(r['C']) and r['C']>=85 and r['vote']<=1: rs.append('拥挤且三维背离')
    if np.isfinite(r['C']) and r['C']>=85 and abs(r['pct_chg'])<0.5: rs.append('拥挤但价格滞涨')
    if r['roll_last']: rs.append('当日主力换月')
    if rs: flags.append(dict(code=r['code'], name=r['variety_name'], sector=r['sector'], reasons=rs,
                             T=round(r['T'],1), flow=round(r['flow'],2), C=round(r['C'],1)))

sectors = []
for name in SECTOR:
    g = df[df['sector']==name]
    if not len(g): continue
    cap = g['capital'].fillna(0); w = cap/cap.sum() if cap.sum()>0 else None
    signed = (g['dir'].fillna(0)*g['vote'].fillna(0))
    score = float((signed*w).sum()) if w is not None else np.nan
    x = dict(name=name, n=int(len(g)), long=int((g['T']>0).sum()), short=int((g['T']<0).sum()),
             flow=round(float(g['flow'].sum()),1), capital=round(float(cap.sum()),1),
             score=round(score,2),
             chg=round(float((g['pct_chg']*cap).sum()/cap.sum()),2) if cap.sum()>0 else None,
             seat=round(float(g['seat_net'].mean()),3),
             res3=int((g['vote']>=3).sum()), res1=int((g['vote']<=1).sum()))
    v, lo = x['score'], x['long']-x['short']
    if v>=1.8 and lo>0: st, tx = 'bull','多头共振'
    elif v>=0.9 and lo>0: st, tx = 'bull','偏多'
    elif v<=-1.8 and lo<0: st, tx = 'bear','空头共振'
    elif v<=-0.9 and lo<0: st, tx = 'bear','偏空'
    elif abs(lo)<=1: st, tx = 'mix','多空分化'
    elif (v>0 and lo<0) or (v<0 and lo>0): st, tx = 'mix','广度与共振背离'
    else: st, tx = 'none','待确认'
    x['st'], x['st_txt'] = st, tx
    x['signed'] = round(float(signed.mean()),2)
    sectors.append(x)

for c in ['T','C','flow','flow5','capital','seat_net','seat_conc','pct_chg','chg20','mult','vol_eff','price_eff']:
    df[c] = pd.to_numeric(df[c], errors='coerce').round(3)
df['flow_pct'] = (df['flow']/df['capital']*100).round(2)

def clean(o):
    if isinstance(o, dict): return {k:clean(v) for k,v in o.items()}
    if isinstance(o, list): return [clean(v) for v in o]
    if isinstance(o, float) and (np.isnan(o) or np.isinf(o)): return None
    if isinstance(o, np.integer): return int(o)
    if isinstance(o, np.floating): return float(o)
    if isinstance(o, np.bool_): return bool(o)
    return o

out = dict(
  asof=str(ASOF.date()), first=str(q['交易日'].min().date()), n_days=int(q['交易日'].nunique()),
  n_var=int(len(df)), n_seat=int(df['seat_net'].notna().sum()),
  n_roll_adj=int(roll_count),
  varieties=df.sort_values('capital', ascending=False).to_dict('records'),
  sectors=sectors,
  flags=sorted(flags, key=lambda f:-abs(f['T']))[:10],
  market=dict(long=int((df['T']>0).sum()), short=int((df['T']<0).sum()),
              up=int((df['pct_chg']>0).sum()), down=int((df['pct_chg']<0).sum()),
              inflow=round(float(df.loc[df['flow']>0,'flow'].sum()),1),
              outflow=round(float(df.loc[df['flow']<0,'flow'].sum()),1),
              net=round(float(df['flow'].sum()),1),
              capital=round(float(df['capital'].sum()),1),
              res3=int((df['vote']>=3).sum()), res2=int((df['vote']==2).sum()),
              res1=int((df['vote']<=1).sum()),
              seat_net=round(float(df['seat_net'].mean()),3),
              med_T=round(float(df['T'].median()),1))
)
out = clean(out)
json.dump(out, open(BASE+'dist/dashboard.json','w'), ensure_ascii=False, indent=1)

print('asof', out['asof'], '| varieties', out['n_var'], '| seat', out['n_seat'], '| roll gaps', roll_count)
print('market', out['market'])
for x in sectors: print(f"  {x['name']:4s} n={x['n']:2d} {x['long']}多{x['short']}空 资金{x['flow']:+8.1f}亿 共振{x['score']:+.2f} {x['st_txt']}")
print('flags:', [(f['name'], ','.join(f['reasons'])) for f in out['flags']])
