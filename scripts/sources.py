#!/usr/bin/env python3
"""四大商品交易所公开数据源适配层。

每个 fetch_* 返回品种级记录列表，字段统一为：
  code, name, exch, close, settle, vol, oi, turnover
其中 close/settle/vol/oi/turnover 取自该品种持仓量最大的合约（主力合约），
与看板原有口径一致。

口径来源：
  SHFE  期货日行情 kx{date}.dat：全合约 JSON，含结算价
  CZCE  每日行情表 FutureDataDaily.txt：全合约管道分隔文本
  GFEX  interfacesWebTiDayQuotes/loadList：全合约 JSON
  DCE   大商所官网有瑞数动态防护（脚本/无头浏览器均被拦），
        改用新浪财经主力连续合约行情，结算价口径已与交易所核对一致
"""
import csv
import json
import re
import time
import urllib.parse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

_session = requests.Session()
_session.headers.update({
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9",
})


def _get(url, **kw):
    kw.setdefault("timeout", 30)
    for attempt in range(3):
        try:
            r = _session.get(url, **kw)
            if r.status_code == 200:
                return r
            if r.status_code in (404, 403):
                return None
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


# --------------------------------------------------------------------------
# 上海期货交易所（含上海国际能源交易中心，品种以 sc/lu/nr/bc/ec 等代码出现）
# --------------------------------------------------------------------------
SHFE_MONTH = re.compile(r"^\d{4}$")


def fetch_shfe(date):
    """date: 'YYYYMMDD'"""
    url = f"https://www.shfe.com.cn/data/tradedata/future/dailydata/kx{date}.dat"
    r = _get(url)
    if r is None:
        return []
    try:
        j = r.json()
    except Exception:
        return []
    rows = j.get("o_curinstrument") or []
    buckets = {}
    for it in rows:
        code = (it.get("PRODUCTGROUPID") or "").strip().upper()
        if not code:
            continue
        # 只保留真实月份合约，剔除“小计”“合计”等汇总行
        if not SHFE_MONTH.match(str(it.get("DELIVERYMONTH") or "").strip()):
            continue
        oi = it.get("OPENINTEREST") or 0
        bucket = buckets.setdefault(code, {"name": (it.get("PRODUCTNAME") or code).strip(),
                                           "best": None})
        if bucket["best"] is None or oi > bucket["best"]["oi"]:
            bucket["best"] = {
                "oi": oi,
                "close": it.get("CLOSEPRICE"),
                "settle": it.get("SETTLEMENTPRICE"),
                "vol": it.get("VOLUME") or 0,
                "turnover": (it.get("TURNOVER") or 0) * 1e4,   # 万元 -> 元
            }
    out = []
    for code, b in buckets.items():
        x = b["best"]
        if not x or not x["oi"]:
            continue
        out.append({
            "code": code, "name": b["name"], "exch": "上海期货交易所",
            "close": x["close"], "settle": x["settle"],
            "vol": x["vol"], "oi": x["oi"], "turnover": x["turnover"],
        })
    return out


def fetch_shfe_seats(date):
    """品种级前 20 席位多空持仓合计。"""
    url = f"https://www.shfe.com.cn/data/tradedata/future/dailydata/pm{date}.dat"
    r = _get(url)
    if r is None:
        return {}
    try:
        cur = r.json().get("o_cursor") or []
    except Exception:
        return {}
    agg = {}
    for it in cur:
        inst = (it.get("INSTRUMENTID") or "").strip()
        rank = it.get("RANK")
        if not isinstance(rank, int) or rank < 1 or rank > 20:
            continue
        m = re.match(r"^([A-Za-z]+)\d{3,4}$", inst)
        if not m:
            continue
        code = m.group(1).upper()
        a = agg.setdefault(code, {"long": 0.0, "short": 0.0})
        a["long"] += it.get("CJ2") or 0
        a["short"] += it.get("CJ3") or 0
    return agg


# --------------------------------------------------------------------------
# 郑州商品交易所
# --------------------------------------------------------------------------
def _num(s):
    s = (s or "").replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_czce(date):
    """date: 'YYYYMMDD'"""
    y, md = date[:4], date[4:]
    url = (f"https://www.czce.com.cn/cn/DFSStaticFiles/Future/"
           f"{y}/{date}/FutureDataDaily.txt")
    r = _get(url)
    if r is None:
        return []
    r.encoding = "utf-8"
    rows = []
    header_seen = False
    for line in r.text.splitlines():
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        if cells[0].startswith("合约代码"):
            header_seen = True
            continue
        if not header_seen:
            continue
        m = re.match(r"^([A-Za-z]+)(\d{3,4})$", cells[0])
        if not m:
            continue
        # 合约代码|昨结算|今开盘|最高|最低|今收盘|今结算|涨跌1|涨跌2|成交量|持仓量|增减量|成交额(万元)|交割结算价
        try:
            close = _num(cells[5])
            settle = _num(cells[6])
            vol = _num(cells[9]) or 0
            oi = _num(cells[10]) or 0
            turnover = (_num(cells[12]) or 0) * 1e4
        except IndexError:
            continue
        if settle is None or not oi:
            continue
        rows.append({
            "code": m.group(1).upper(), "month": m.group(2),
            "close": close, "settle": settle, "vol": vol, "oi": oi,
            "turnover": turnover,
        })
    # 取每个品种持仓量最大的合约
    best = {}
    for x in rows:
        b = best.get(x["code"])
        if b is None or x["oi"] > b["oi"]:
            best[x["code"]] = x
    out = []
    for code, x in best.items():
        out.append({
            "code": code, "name": "", "exch": "郑州商品交易所",
            "close": x["close"], "settle": x["settle"],
            "vol": x["vol"], "oi": x["oi"], "turnover": x["turnover"],
        })
    return out


def fetch_czce_seats(date):
    """郑商所持仓排名按品种给出，直接取前 20 名多空合计。"""
    y = date[:4]
    url = (f"https://www.czce.com.cn/cn/DFSStaticFiles/Future/"
           f"{y}/{date}/FutureDataHolding.txt")
    r = _get(url)
    if r is None:
        return {}
    r.encoding = "utf-8"
    agg = {}
    cur = None
    for line in r.text.splitlines():
        m = re.search(r"品种：\S*?([A-Za-z]{1,3})\s", line)
        if m and "日期" in line:
            cur = m.group(1).upper()
            agg.setdefault(cur, {"long": 0.0, "short": 0.0})
            continue
        if cur is None:
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 10:
            continue
        try:
            rank = int(cells[0])
        except ValueError:
            continue
        if rank < 1 or rank > 20:
            continue
        # 名次|会员|交易量|增减|会员|持买仓量|增减|会员|持卖仓量|增减
        agg[cur]["long"] += _num(cells[5]) or 0
        agg[cur]["short"] += _num(cells[8]) or 0
    return agg


# --------------------------------------------------------------------------
# 广州期货交易所
# --------------------------------------------------------------------------
GFEX_CODE = {"工业硅": "SI", "碳酸锂": "LC", "多晶硅": "PS",
             "铂": "PT", "钯": "PD", "铸造铝合金": "AD"}


def fetch_gfex(date):
    url = "http://www.gfex.com.cn/u/interfacesWebTiDayQuotes/loadList"
    payload = urllib.parse.urlencode({"trade_date": date, "trade_type": "0"})
    for attempt in range(3):
        try:
            r = _session.post(url, data=payload, timeout=30, headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "http://www.gfex.com.cn/gfex/rihq/hqsj_tjsj.shtml",
            })
            if r.status_code == 200 and r.text.strip().startswith("{"):
                j = r.json()
                break
        except Exception:
            pass
        time.sleep(1.5)
    else:
        return []
    if j.get("code") not in (0, "0"):
        return []
    best = {}
    for it in j.get("data") or []:
        order = (it.get("varietyOrder") or "").strip().upper()
        name = (it.get("variety") or "").strip()
        code = GFEX_CODE.get(name) or order
        if not code:
            continue
        oi = it.get("openInterest") or 0
        settle = it.get("clearPrice")
        if oi <= 0 or settle in (None, "", 0):
            continue
        b = best.get(code)
        if b is None or oi > b["oi"]:
            best[code] = {
                "oi": oi,
                "close": it.get("close"),
                "settle": it.get("clearPrice"),
                "vol": it.get("volumn") or 0,
                "turnover": (it.get("turnover") or 0) * 1e4,
            }
    return [{"code": c, "name": "", "exch": "广州期货交易所", **v}
            for c, v in best.items()]


def fetch_gfex_seats(date):
    """广期所会员持仓排名（若接口不可用返回空）。"""
    url = "http://www.gfex.com.cn/u/interfacesWebTiMemberDealPosi/loadList"
    payload = urllib.parse.urlencode({"trade_date": date})
    try:
        r = _session.post(url, data=payload, timeout=30, headers={
            "Content-Type": "application/x-www-form-urlencoded",
        })
        if r.status_code != 200 or not r.text.strip().startswith("{"):
            return {}
        j = r.json()
    except Exception:
        return {}
    agg = {}
    for it in j.get("data") or []:
        code = (it.get("varietyOrder") or "").strip().upper()
        if not code:
            continue
        a = agg.setdefault(code, {"long": 0.0, "short": 0.0})
        a["long"] += it.get("longPosition") or 0
        a["short"] += it.get("shortPosition") or 0
    return agg


# --------------------------------------------------------------------------
# 大连商品交易所（经新浪财经主力连续合约）
# --------------------------------------------------------------------------
DCE_CODES_FALLBACK = [
    "A", "B", "C", "CS", "M", "Y", "P", "I", "J", "JM", "L", "V", "PP",
    "EG", "EB", "PG", "LH", "JD", "FB", "BB", "RR", "LG",
]


def _dce_codes():
    """从 multipliers.csv 取大商所品种清单，取不到时用内置表。"""
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "multipliers.csv")
    try:
        with open(p, encoding="utf-8-sig") as f:
            codes = [row["variety_code"].strip().upper()
                     for row in csv.DictReader(f)
                     if "大连" in (row.get("上市交易所") or "")]
        if codes:
            return codes
    except Exception:
        pass
    return DCE_CODES_FALLBACK


def fetch_dce(date=None, codes=None):
    """新浪 nf_<code>0 主力连续行情。

    字段：0 名称 1 时间 2 开 3 高 4 低 5 昨收 6 买 7 卖 8 最新
          9 结算 10 昨结算 11 买量 12 卖量 13 持仓量 14 成交量 15 交易所 16 品种名 17 日期
    """
    if codes is None:
        codes = _dce_codes()
    out = []
    for i in range(0, len(codes), 20):
        chunk = codes[i:i + 20]
        q = ",".join("nf_%s0" % c for c in chunk)
        r = None
        for _ in range(3):
            try:
                r = requests.get(
                    "https://hq.sinajs.cn/list=" + q, timeout=30,
                    headers={"User-Agent": UA,
                             "Referer": "https://finance.sina.com.cn"})
                if r.status_code == 200:
                    break
            except Exception:
                r = None
            time.sleep(1.2)
        if r is None or r.status_code != 200:
            continue
        r.encoding = "gbk"
        for line in r.text.splitlines():
            if '="' not in line:
                continue
            head = line.split("=")[0]
            m = re.search(r"nf_([A-Z]+)0$", head.replace("var hq_str_", ""))
            if not m:
                continue
            code = m.group(1)
            body = line.split('="', 1)[1].rstrip('";')
            f = body.split(",")
            if len(f) < 18:
                continue
            settle = _num(f[9]) or 0
            close = _num(f[8]) or 0
            if settle <= 0:
                settle = close      # 当日结算价未发布时退回收盘价
            oi = _num(f[13]) or 0
            vol = _num(f[14]) or 0
            if not oi or not settle:
                continue
            out.append({
                "code": code, "name": f[16].strip(), "exch": "大连商品交易所",
                "close": close, "settle": settle, "vol": vol, "oi": oi,
                "turnover": vol * settle,   # 新浪无成交额，按量价估算，仅用于反推乘数
                "settle_fallback": _num(f[9]) is None or (_num(f[9]) or 0) <= 0,
            })
    return out


def fetch_dce_seats(date=None):
    """大商所席位排名无法自动获取（官网有动态防护）。"""
    return {}


# --------------------------------------------------------------------------
def fetch_all(date):
    """返回 (quotes, seats)，均为品种级。"""
    quotes = []
    seats = {}
    quotes += fetch_shfe(date)
    seats.update(fetch_shfe_seats(date))
    quotes += fetch_czce(date)
    seats.update(fetch_czce_seats(date))
    quotes += fetch_gfex(date)
    seats.update(fetch_gfex_seats(date))
    if date is None:
        quotes += fetch_dce()
    return quotes, seats


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else None
    q, st = fetch_all(d)
    for x in q:
        print(x)
    print("seats:", {k: v for k, v in list(st.items())[:5]})
    print("n_quotes=", len(q), "n_seats=", len(st))
