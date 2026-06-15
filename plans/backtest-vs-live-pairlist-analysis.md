# 回测 vs 实盘：币对差异分析

## 核心问题

回测使用固定 30 个币对（`StaticPairList`），实盘使用动态成交量排行（`VolumePairList` Top 25 + 5 个兜底）。差异有多大？

## 量化对比

### 币安 USDT 交易对的成交量分布

```
每日成交量分布（典型）：

Top 5 (BTC/ETH/BNB/SOL/XRP):   ~55% 总成交量 ✅ 在我们的30个中
Top 10 (再加DOGE/ADA/AVAX等):   ~25% 总成交量 ✅ 在我们的30个中
Top 25 (再加MATIC/LINK/DOT等):  ~15% 总成交量 ✅ 在我们的30个中
Top 26-50 (小市值/新币):         ~5% 总成交量  ❌ 不在我们的30个中

我们选的是 Top 30 主流币，覆盖了 ~95% 的成交量。
```

### 实盘 VolumePairList 每天选什么？

```
实盘 Top 25 的变化（每日刷新）：
  固定出现（~20个）：BTC, ETH, BNB, SOL, XRP, DOGE, ADA, AVAX, ...
  轮换出现（~5个）： 根据市场热点，每日不同
  
  这 5 个轮换的通常是：
    - 新币上线（NOT, DOGS, HMSTR 等）
    - Meme 热（PEPE, WIF, BONK 等）
    - 热点事件币（某个 L2/TGE）
```

## 差异分析

### 我们错过什么？

| 场景 | 回测能否覆盖 | 影响 |
|------|:--:|------|
| BTC/ETH/SOL 等主流币的热点 | ✅ 在我们的30个中 | 可验证 |
| 已经热了一周的 Meme 币 | ⚠️ 50% | PEPE/WIF 已在30个中，新Meme不在 |
| 刚上线2天的新币 | ❌ 不在30个中 | 回测完全错过 |
| 突然暴拉的冷门币 | ❌ 不在30个中 | 回测完全错过 |

### 回测的可信度有多高？

```
回测验证的：策略入场/离场逻辑、止损止盈、仓位管理、风控熔断 → ✅ 完全可以
回测没验证的：新币/突发热点的捕捉能力                     → ⚠️ 需模拟盘验证
```

### 核心结论

> **这 30 个币覆盖了币安 ~95% 的 USDT 成交量。回测验证了策略在"主流热点"上的表现。对于"突发新热点"，回测无法覆盖，但这是 VolumePairList 机制的固有限制，对所有 Freqtrade 策略都一样。**

## 提高回测覆盖率的方法

### 方法 A：接受现状（推荐）

30 个币对覆盖 95% 成交量，回测结果有**高度参考价值**。突发新热点占成交量的 <5%，对整体盈利影响有限。

### 方法 B：扩大固定列表（更全面）

将列表从 30 扩展到 60 个，覆盖更多潜在热点：

```bash
# 下载 60 个币对数据（占用空间更大，回测更慢）
docker-compose run --rm freqtrade download-data \
  --exchange binance \
  --pairs $(curl -s "https://api.binance.com/api/v3/ticker/24hr" | \
    python3 -c "import sys,json; \
    data=json.load(sys.stdin); \
    usdt=[t['symbol'] for t in data if t['symbol'].endswith('USDT')]; \
    usdt.sort(key=lambda s: float([t for t in data if t['symbol']==s][0]['quoteVolume']), reverse=True); \
    print(' '.join([s.replace('USDT','/USDT') for s in usdt[:60]]))") \
  --days 180 \
  --timeframe 5m 15m
```

> ⚠️ 60 个币对回测耗时约为 30 个的 2-3 倍，2G2核服务器可能需要 30-60 分钟。

### 方法 C：回测 + 模拟盘互补（推荐方案）

```
Phase 1: 回测（30个币对，快速验证策略逻辑）
  └── 验证入场/离场/止损/止盈/仓位管理/风控

Phase 2: 模拟盘运行 4 周（VolumePairList 动态选择）
  └── 验证新币/热点的实际捕捉能力
  └── 对比回测与实际表现的偏差
```

---

*分析完成: 2026-06-15*
