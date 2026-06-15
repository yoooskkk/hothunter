# HotHunter 测试方案

## 测试流程概览

```
Phase 1: 回测验证 ← 现在在这里
  └── 不同时间周期的历史数据回测
  └── 验证策略参数有效性
  └── 确认无未来函数

Phase 2: 模拟盘运行
  └── 24h+ 连续运行
  └── 监控信号触发和行为
  └── 验证 Telegram 通知

Phase 3: 小资金实盘（可选）
  └── 在确认 Phase 1+2 满意后进行
```

---

## Phase 1：回测验证

### 1.1 数据准备

```bash
cd /opt/hothunter
docker-compose stop

# 下载 180天 5m + 15m 数据（必须两个都下载，策略用到 15m 多时间框架）
docker-compose run --rm freqtrade download-data \
  --exchange binance \
  --pairs BTC/USDT ETH/USDT BNB/USDT SOL/USDT DOGE/USDT \
  --days 180 \
  --timeframe 5m 15m
```

### 1.2 不同周期回测

```bash
# === 1个月（近期市场表现） ===
docker-compose run --rm freqtrade backtesting \
  --config /freqtrade/user_data/configs/config.dry.json \
  --strategy HotHunterStrategy \
  --timerange 20260515-

# === 3个月（中期表现） ===
docker-compose run --rm freqtrade backtesting \
  --config /freqtrade/user_data/configs/config.dry.json \
  --strategy HotHunterStrategy \
  --timerange 20260315-

# === 6个月（长期表现） ===
docker-compose run --rm freqtrade backtesting \
  --config /freqtrade/user_data/configs/config.dry.json \
  --strategy HotHunterStrategy \
  --timerange 20251215-

# === 完整180天 ===
docker-compose run --rm freqtrade backtesting \
  --config /freqtrade/user_data/configs/config.dry.json \
  --strategy HotHunterStrategy \
  --timerange 20251201-
```

### 1.3 回测指标评估标准

| 指标 | 理想值 | 可接受值 | 需优化 | 说明 |
|------|:--:|:--:|:--:|------|
| 总胜率 | > 50% | 40-50% | < 40% | 热点追踪天然胜率偏低，靠盈亏比赚钱 |
| 平均盈亏比 | > 2.0 | 1.5-2.0 | < 1.5 | 平均盈利 / 平均亏损 |
| 最大回撤 | < 15% | 15-25% | > 25% | 超过25%触发熔断 |
| 夏普比率 | > 2.0 | 1.0-2.0 | < 1.0 | 风险调整后收益 |
| 月均收益率 | > 10% | 5-10% | < 5% | 100U起步年化目标 100-300% |
| 交易次数 | > 100 | 50-100 | < 50 | 样本量太少不可信 |

### 1.4 关键验证项

```
回测中重点观察：

[ ] 是否有连续亏损 -> StoplossGuard 是否触发
[ ] 最大回撤是否接近 25% -> MaxDrawdown 是否触发
[ ] 分批止盈是否执行（查看交易明细中是否有 partial exit）
[ ] 金字塔加仓是否触发（查看是否有 position adjustment）
[ ] 热点消退离场是否及时（检查亏损交易平均持有时间）
```

### 1.5 对比测试（参数调优）

如需调整参数，对比修改前后的结果：

```bash
# 测试不同止损值
# 修改策略中的 stoploss = -0.08 → -0.06，重新回测
docker-compose run --rm freqtrade backtesting \
  --config /freqtrade/user_data/configs/config.dry.json \
  --strategy HotHunterStrategy \
  --timerange 20260315-
```

---

## Phase 2：模拟盘运行

### 2.1 启动模拟盘

```bash
# 确保 data/ 目录已清除（旧数据库会与新 schema 冲突）
rm -rf data/*
docker-compose up -d
docker logs -f hothunter
```

### 2.2 运行验证清单

```
启动后查看日志确认：

[ ] state=RUNNING（运行中，非 STOPPED）
[ ] Protections 全部加载（StoplossGuard, MaxDrawdown, CooldownPeriod）
[ ] Pairlists 加载（VolumePairList 25 + StaticPairList 5）
[ ] Telegram 收到启动消息（如已配置）
```

### 2.3 监控指标

| 检查项 | 频率 | 方法 |
|--------|:--:|------|
| 是否有入场信号 | 每小时 | Telegram `/status` |
| 持仓盈亏 | 每日 | Telegram `/profit` |
| 账户余额 | 每日 | Telegram `/balance` |
| 日志错误 | 每日 | `docker logs hothunter --tail 50` |
| 交易对列表 | 每周 | Telegram `/whitelist` |

### 2.4 模拟盘运行时长建议

| 时长 | 目的 |
|:--:|------|
| 24h | 确认启动正常、无报错、有信号生成 |
| 7天 | 验证信号质量、入场离场逻辑 |
| 30天 | 验证风控机制、盈亏一致性 |

---

## Phase 3：实盘（可选）

### 3.1 切换到实盘配置

```bash
# 1. 停止模拟盘
docker-compose down

# 2. 备份模拟盘数据库（供参考）
cp data/hothunter.dry.sqlite data/hothunter.dry.sqlite.bak

# 3. 清除数据
rm -rf data/*

# 4. 编辑 docker-compose.yml，将 config.dry.json → config.live.json
nano docker-compose.yml
# 修改 command 中的 --config 路径

# 5. 编辑 .env 填入真实 API Key
nano .env

# 6. 启动
docker-compose up -d
```

### 3.2 实盘风控底线

```
资金管理：
  - 初始资金 100U，前 3 个月不追加
  - 每月利润超过 30% 时，提取 50% 到独立钱包
  - 单月亏损超过 15% 时，暂停并重新评估策略

熔断规则：
  - 连续 5 笔亏损 -> StoplossGuard 自动暂停
  - 总回撤 > 25% -> MaxDrawdown 自动暂停
  - 回撤 > 40% -> 紧急停止，手动介入
```

---

## 常见问题

### Q: 回测结果很好，实盘却亏损？

可能原因：
1. **MTF 未来函数**（已修复，使用 `merge_informative_pair` + `ffill=True`）
2. **过拟合**（参数针对历史数据优化过度）
3. **市场结构变化**（热点币行为与回测期不同）

建议：用不同时间周期回测对比，如果只有某一段表现好，说明过拟合。

### Q: 回测没有交易信号？

可能原因：
1. 数据未下载：确认已下载 5m + 15m 数据
2. 热身期不足：`startup_candle_count=100`，前 `100根 × 5m = 8.3h` 没有信号
3. 条件太严格：检查 `populate_entry_trend` 中的条件是否过于苛刻

### Q: 模拟盘运行但没交易？

可能原因：
1. 当前市场没有符合条件的热点
2. 检查 Telegram `/whitelist` 查看当前监控的交易对
3. `docker logs hothunter` 查看是否有信号日志

---

*版本: v1.0 | 2026-06-13*
