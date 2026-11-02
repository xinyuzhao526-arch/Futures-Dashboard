#!/usr/bin/env python3
"""把 dist/dashboard.json 渲染进 template/dashboard.html，输出 dist/index.html。

用法:
    FD_BASE=/workspace/automations/futures-dashboard/ python3 scripts/build.py
"""
import json
import os
import re
import sys

BASE = os.environ.get("FD_BASE", "/workspace/automations/futures-dashboard/")
if not BASE.endswith("/"):
    BASE += "/"

SEC_COLOR = {
    "贵金属": "#8c6d3f", "有色": "#2f6f7a", "黑色": "#4a4a52", "能源": "#8a4b2a",
    "化工": "#3f6b4a", "农产品": "#7a6a2f", "软商品": "#7d4a63", "新能源": "#3c5a8a",
}
UP, DOWN = "#b02a1f", "#0f7048"
KEYS = ["code", "variety_name", "sector", "settle", "pct_chg", "T", "C", "flow",
        "flow_pct", "capital", "seat_net", "seat_conc", "vote", "n_act", "vlabel",
        "d_trend", "d_flow", "d_seat", "chg20", "flow5", "roll_last"]


def tone(v):
    return UP if (v is not None and v >= 0) else DOWN


def raw(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def settle_text(v):
    if v is None:
        return "—"
    if abs(v) >= 1000:
        return "{:,.0f}".format(v)
    return "{:,.2f}".format(v)


def row_html(r):
    sec = r.get("sector") or ""
    col = SEC_COLOR.get(sec, "#8a8074")
    v = r.get("vote") or 0
    nact = r.get("n_act") or 0
    label = r.get("vlabel") or "中性"
    cls = "bull" if label == "三维共振" else ("mix" if label == "三向分裂" else "none")
    ptext = "中性 —" if label == "中性" else "{} {}/{}".format(label, v, nact)

    def num(key, spec, colr=False):
        val = r.get(key)
        if val is None:
            return '<td class="r" data-sort-value="">—</td>'
        style = ' style="color:{}"'.format(tone(val)) if colr else ""
        return '<td class="r" data-sort-value="{}"{}>{}</td>'.format(raw(val), style, format(val, spec))

    cells = [
        '<th scope="row" data-sort-value="{n}">{n}</th>'.format(n=r["variety_name"]),
        '<td data-sort-value="{s}"><span class="mk" style="background:{c}"></span>{s}</td>'.format(s=sec, c=col),
        '<td class="r" data-sort-value="{r}">{t}</td>'.format(r=raw(r.get("settle")), t=settle_text(r.get("settle"))),
        num("pct_chg", "+.2f", True),
        num("T", "+.0f", True),
        num("C", ".0f"),
        num("flow", "+.1f", True),
        num("flow_pct", "+.2f"),
        num("capital", ",.1f"),
        num("seat_net", "+.3f"),
        num("chg20", "+.1f", True),
        '<td data-sort-value="{}"><span class="pill lt {}">{}</span></td>'.format(v, cls, ptext),
    ]
    return "<tr>" + "".join(cells) + "</tr>"


def main():
    dash_path = BASE + "dist/dashboard.json"
    tpl_path = BASE + "template/dashboard.html"
    out_path = BASE + "dist/index.html"
    for p in (dash_path, tpl_path):
        if not os.path.exists(p):
            sys.exit("missing: " + p)

    d = json.load(open(dash_path, encoding="utf-8"))
    tpl = open(tpl_path, encoding="utf-8").read()

    m = re.search(r'"asof"\s*:\s*"(\d{4}-\d{2}-\d{2})"', tpl)
    old_asof = m.group(1) if m else None
    asof = d["asof"]

    rows = [[r.get(k) for k in KEYS] for r in d["varieties"]]
    payload = {
        "asof": asof, "first": d["first"], "n_days": d["n_days"], "n_var": d["n_var"],
        "n_seat": d["n_seat"], "n_roll": d.get("n_roll_adj", d.get("n_roll", 0)), "market": d["market"],
        "sectors": d["sectors"], "flags": d["flags"], "keys": KEYS, "rows": rows,
    }
    js = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    pat = re.compile(r"(window\.__DASH__\s*=\s*)\{.*?\};(?=\s*</script>)", re.S)
    tpl, n1 = pat.subn(lambda mm: mm.group(1) + js + ";", tpl)
    if n1 != 1:
        sys.exit("expected exactly 1 __DASH__ block, got %d" % n1)

    body = "\n".join(row_html(r) for r in d["varieties"])
    pat2 = re.compile(r'(<tbody[^>]*id="detailbody"[^>]*>)(.*?)(</tbody>)', re.S)
    tpl, n2 = pat2.subn(lambda mm: mm.group(1) + "\n" + body + "\n" + mm.group(3), tpl)
    if n2 != 1:
        sys.exit("expected exactly 1 detailbody block, got %d" % n2)

    if old_asof and old_asof != asof:
        tpl = tpl.replace(old_asof, asof)

    open(out_path, "w", encoding="utf-8").write(tpl)
    print("built %s | asof %s | varieties %d | seat %d | roll gaps %d | %.0f KB"
          % (out_path, asof, d["n_var"], d["n_seat"],
             d.get("n_roll_adj", d.get("n_roll", 0)), len(tpl.encode()) / 1024))


if __name__ == "__main__":
    main()
