# 商品期货市场结构看板

境内商品期货的日频市场结构观察页面。单文件静态 HTML，无后端、无外部 CSS/JS 依赖。

线上地址：https://xinyuzhao526-arch.github.io/Futures-Dashboard/

## 自动更新机制

页面本身是静态文件，由仓库内的 GitHub Actions 每个交易日自动重新生成：

```
.github/workflows/daily.yml
  cron: 40 9 * * 1-5   # UTC，等于北京时间 周一至周五 17:40
       ↓
  scripts/refresh.py   取行情与席位 → data/*.csv
       ↓
  scripts/compute.py   计算趋势/拥挤/资金/共振 → dist/dashboard.json
       ↓
  scripts/build.py     渲染 → dist/index.html
       ↓
  git commit && push   → GitHub Pages 自动重新发布
```

工作流带三道护栏，任一条不满足即中止、不发布半成品：

1. 快照品种数少于 70 视为数据源异常；
2. 核心品种（CU/RB/M/I/AU/TA/LC）缺失即中止；
3. 数据无变化时不提交，避免无意义的发布。

也可以在 Actions 页面手动触发（Workflow dispatch）。

## 数据来源

| 内容 | 来源 | 说明 |
|---|---|---|
| 行情（全品种，含历史） | 新浪财经 `InnerFuturesNewService.getDailyKLine` | 主力连续合约，`s` 字段为结算价 |
| 上期所/能源中心席位 | www.shfe.com.cn 每日会员成交持仓排名 | 前 20 名多空合计 |
| 郑商所席位 | www.czce.com.cn 每日持仓排名表 | 按品种披露 |
| 广期所席位 | www.gfex.com.cn 会员持仓排名 | 按品种披露 |

大商所官网对脚本与无头浏览器均返回 HTTP 412（瑞数动态防护），其行情改用新浪同源数据，
结算价已与交易所披露值逐品种核对一致（误差 0.000%）；**大商所席位排名暂无法自动获取**。

## 口径

- 趋势、拥挤度与资金均以**结算价**计算，不用收盘价；
- 每个品种取其**持仓量最大的合约**为代表合约（与历史序列口径一致）；
- 持仓量按**单边**计；
- 资金流 = 量效应 + 价效应；`F = (OI_t − OI_{t−1})·S_t·M + OI_{t−1}·(S_t − S_{t−1})·M`；
- 换月跳空按单日 |对数收益| > 11% 识别并剥离；
- 结算价缺失（盘中未结算、停牌）的交易日不落库，不以盘中价充抵。

指标公式：

- 趋势 `T = 100·tanh(0.8·(0.5·z20 + 0.3·z60 + 0.2·z120))`，`z_k = k日对数收益 ÷ (60日日波动率 × √k)`
- 拥挤度 `C = (持仓量250日分位 + 成交持仓比分位 + 20日波动率分位) / 3`，有效观测 ≥60，读数应视为下限
- 席位净多比 `N = (L20 − S20) / (L20 + S20)`
- 三维共振：趋势、资金、席位各计 −1/0/+1，阈值 20 / 0.3% / 3%

合约乘数取自 `data/multipliers.csv`，由各交易所成交额 ÷（成交量 × 结算价）反推校核，
全部落在整数且与交易所合约规格一致。

## 已知限制

- **大商所席位缺失**：约 23 个品种无席位数据，三维共振中席位维度对这些品种弃权，
  故共振计数会系统性低于含席位的口径。
- 拥挤度仅含三项分位，缺少席位历史分位与基差分位，读数偏低。
- 换月靠日收益阈值识别，极端行情下可能误判，小幅换月可能漏判。
- 微型品种（原木、纤维板、胶合板、线材等，沉淀资金不足 3 亿）在明细表中保留，但不参与结论。
- 本页为市场结构观察工具，不构成投资建议。

## 本地运行

```bash
pip install requests pandas numpy
export FD_BASE=$(pwd)/
python scripts/refresh.py     # 取数，约 1-3 分钟
python scripts/compute.py     # 计算
python scripts/build.py       # 生成 dist/index.html
```

`scripts/sources.py` 提供四家交易所的原始接口适配；`scripts/deploy_push.py` 用于通过
Contents API 推送文件（适用于 git 协议不可用的环境）。
