#!/usr/bin/env python3
"""看板数据自主更新入口。

数据来源：
  行情（全品种、含结算价与历史） 新浪财经 InnerFuturesNewService.getDailyKLine
  席位  上期所/郑商所/广期所官网当日披露（大商所有动态防护，暂不可得）

产物（供 compute.py 使用）：
  data/quotes_daily.csv     交易日, variety_code, variety_name, 上市交易所, 收盘, settle, 成交量, 持仓量(手)
  data/snapshot_latest.csv  variety_code, variety_name, settle, prev_settle, pct_chg, 成交量, 持仓量(手), oi_chg, 成交额
  data/seats_latest.csv     variety_code, variety_name, long_top20, short_top20, total_oi

用法：
  python scripts/refresh.py            # 全量刷新（拉全部历史，约 1-3 分钟）
  python scripts/refresh.py --seats-off # 只更新行情
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HIST_DAYS = 400          # quotes_daily 保留的交易日数

sys.path.insert(0, os.path.join(ROOT, "scripts"))
import sources  # noqa: E402


def load_universe():
    """品种清单：以 multipliers.csv 为准（code, name, exchange, multiplier）。"""
    path = os.path.join(DATA, "multipliers.csv")
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = (row.get("variety_code") or "").strip().upper()
            if not code:
                continue
            out.append({
                "code": code,
                "name": (row.get("variety_name") or "").strip(),
                "exch": (row.get("上市交易所") or "").strip(),
                "mult": row.get("multiplier") or "",
            })
    return out


def fetch_history(code, retries=3):
    """取单个品种主力连续合约的完整日线（含结算价）。"""
    sym = code + "0"
    url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
           f"var%20_{sym}=/InnerFuturesNewService.getDailyKLine?symbol={sym}")
    for attempt in range(retries):
        try:
            r = requests.get(url, headers={"User-Agent": UA,
                                           "Referer": "https://finance.sina.com.cn"},
                             timeout=40)
            if r.status_code == 200:
                m = re.search(r"=\((\[.*\])\);?\s*$", r.text, re.S)
                if m:
                    return code, json.loads(m.group(1))
        except Exception:
            pass
        time.sleep(1.0 + attempt)
    return code, None


def build_quotes(universe):
    codes = [u["code"] for u in universe]
    meta = {u["code"]: u for u in universe}
    hist = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fetch_history, c): c for c in codes}
        for i, fut in enumerate(as_completed(futs), 1):
            code, data = fut.result()
            if data:
                hist[code] = data
            if i % 20 == 0:
                print(f"  行情进度 {i}/{len(codes)}", flush=True)
    print(f"  取到行情品种 {len(hist)}/{len(codes)}", flush=True)

    # 交易日并集（取最近 HIST_DAYS 天）
    all_dates = sorted({row["d"] for rows in hist.values() for row in rows})
    keep = set(all_dates[-HIST_DAYS:]) if len(all_dates) > HIST_DAYS else set(all_dates)

    qrows = []
    for code, rows in hist.items():
        m = meta.get(code, {})
        for row in rows:
            if row["d"] not in keep:
                continue
            try:
                settle = float(row.get("s") or 0)
                close = float(row.get("c") or 0)
                vol = float(row.get("v") or 0)
                oi = float(row.get("p") or 0)
            except (TypeError, ValueError):
                continue
            if settle <= 0:
                settle = close
            if settle <= 0:
                continue
            qrows.append({
                "交易日": row["d"], "variety_code": code,
                "variety_name": m.get("name", ""), "上市交易所": m.get("exch", ""),
                "收盘": close, "settle": settle, "成交量": vol, "持仓量(手)": oi,
            })
    qrows.sort(key=lambda x: (x["交易日"], x["variety_code"]))
    return qrows, hist, keep


def build_snapshot(hist, universe, keep):
    """最新交易日的品种快照。"""
    meta = {u["code"]: u for u in universe}
    latest = max(keep)
    snaps = []
    for code, rows in hist.items():
        rows = [r for r in rows if r["d"] <= latest]
        if len(rows) < 2:
            continue
        cur, prev = rows[-1], rows[-2]
        try:
            settle = float(cur.get("s") or 0) or float(cur.get("c") or 0)
            prev_settle = float(prev.get("s") or 0) or float(prev.get("c") or 0)
            vol = float(cur.get("v") or 0)
            oi = float(cur.get("p") or 0)
            prev_oi = float(prev.get("p") or 0)
        except (TypeError, ValueError):
            continue
        if settle <= 0:
            continue
        m = meta.get(code, {})
        try:
            mult = float(m.get("mult") or 0)
        except ValueError:
            mult = 0.0
        pct = (settle / prev_settle - 1) * 100 if prev_settle else 0.0
        snaps.append({
            "variety_code": code,
            "variety_name": m.get("name", ""),
            "settle": settle,
            "prev_settle": prev_settle,
            "pct_chg": round(pct, 2),
            "成交量": vol,
            "持仓量(手)": oi,
            "oi_chg": oi - prev_oi,
            "成交额": vol * settle * mult if mult else 0.0,
        })
    return snaps, latest


def write_csv(path, rows, fields):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seats-off", action="store_true")
    args = ap.parse_args()

    universe = load_universe()
    print(f"品种清单 {len(universe)} 个", flush=True)

    qrows, hist, keep = build_quotes(universe)
    write_csv(os.path.join(DATA, "quotes_daily.csv"), qrows,
              ["交易日", "variety_code", "variety_name", "上市交易所",
               "收盘", "settle", "成交量", "持仓量(手)"])
    print(f"quotes_daily.csv 写入 {len(qrows)} 行", flush=True)

    snaps, latest = build_snapshot(hist, universe, keep)
    write_csv(os.path.join(DATA, "snapshot_latest.csv"), snaps,
              ["variety_code", "variety_name", "settle", "prev_settle", "pct_chg",
               "成交量", "持仓量(手)", "oi_chg", "成交额"])
    print(f"snapshot_latest.csv 写入 {len(snaps)} 行，基准日 {latest}", flush=True)

    if args.seats_off:
        return

    # 席位：三家交易所官网当日披露
    d = latest.replace("-", "")
    seats = {}
    try:
        seats.update(sources.fetch_shfe_seats(d))
    except Exception as e:
        print("  SHFE 席位失败:", e, flush=True)
    try:
        seats.update(sources.fetch_czce_seats(d))
    except Exception as e:
        print("  CZCE 席位失败:", e, flush=True)
    try:
        seats.update(sources.fetch_gfex_seats(d))
    except Exception as e:
        print("  GFEX 席位失败:", e, flush=True)

    name_of = {u["code"]: u["name"] for u in universe}
    total_oi = {}
    for s in snaps:
        total_oi[s["variety_code"]] = s["持仓量(手)"]
    srows = []
    for code, v in sorted(seats.items()):
        if code not in name_of:
            continue
        srows.append({
            "variety_code": code,
            "variety_name": name_of[code],
            "long_top20": round(v["long"], 0),
            "short_top20": round(v["short"], 0),
            "total_oi": total_oi.get(code, 0),
        })
    write_csv(os.path.join(DATA, "seats_latest.csv"), srows,
              ["variety_code", "variety_name", "long_top20", "short_top20", "total_oi"])
    print(f"seats_latest.csv 写入 {len(srows)} 行", flush=True)


if __name__ == "__main__":
    main()
