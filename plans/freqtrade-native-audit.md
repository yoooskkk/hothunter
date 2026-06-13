# Freqtrade 2026.5.1 原生能力审计 — 排除非原生依赖

## 1. 审计范围

逐项检查当前设计中的每个功能，确认 Freqtrade 2026.5.1 是否原生支持，如有原生替代则优先采用。

---

## 2. 逐项审计

### 2.1 交易对发现

| 功能 | 设计方式 | 原生支持？ | 结论 |
|------|----------|:--:|------|
| VolumePairList | Freqtrade pairlist 插件 | ✅ 原生 | 无需变更 |
| StaticPairList | Freqtrade pairlist 插件 | ✅ 原生 | 无需变更 |
| 策略内短期动量扫描 | `populate_indicators` 中计算 ROC | ✅ 原生回调 | 无需变更 |

### 2.2 指标与入场

| 功能 | 设计方式 | 原生支持？ | 结论 |
|------|----------|:--:|------|
| HLC3 典型价格 | `populate_indicators` 中计算 | ✅ 原生回调 | 无需变更 |
| 插针检测 | `populate_indicators` 中计算影线比例 | ✅ 原生回调 | 无需变更 |
| 多时间框架(15m) | `informative_pairs()` + `dp.get_pair_dataframe()` | ✅ 原生 API | 无需变更 |
| 入场信号 | `populate_entry_trend()` | ✅ 原生回调 | 无需变更 |
| 入场确认 | `confirm_trade_entry()` | ✅ 原生回调 | 无需变更 |
| 入场冷却 30min | `confirm_trade_entry()` 中检查时间 | ✅ 原生回调 | 无需变更 |

### 2.3 离场与仓位调整

| 功能 | 设计方式 | 原生支持？ | 结论 |
|------|----------|:--:|------|
| 硬止损 -8% | `stoploss = -0.08` | ✅ 原生参数 | 无需变更 |
| 移动止损 | `trailing_stop` 配置 | ✅ 原生参数 | 无需变更 |
| 离场信号 | `populate_exit_trend()` | ✅ 原生回调 | 无需变更 |
| 自定义离场 | `custom_exit()` | ✅ 原生回调 | 无需变更 |
| 分批止盈 | `adjust_trade_position()` 返回负值 | ✅ 2026.5.1 原生 API | 无需变更 |
| 金字塔加仓 | `adjust_trade_position()` 返回正值 | ✅ 2026.5.1 原生 API | 无需变更 |
| 分批止盈状态追踪 | 自定义字典 `self._exit_state` | ⚠️ 需自定义 | **必须保留**，记录已触发批次 |

### 2.4 仓位管理

| 功能 | 设计方式 | 原生支持？ | 结论 |
|------|----------|:--:|------|
| 动态仓位 | `custom_stake_amount()` | ✅ 原生回调 | 无需变更 |
| 利润阶梯锁定 | `custom_stake_amount()` 中实现 | ✅ 原生回调 | 无需变更 |
| 里程碑提取 | `custom_stake_amount()` 中排除安全利润 | ✅ 原生回调 | 无需变更 |
| 盈亏调整系数 | `custom_stake_amount()` 中计算 | ✅ 原生回调 | 无需变更 |

### 2.5 风控熔断 ⚠️ 关键审计

| 我们的设计 | Freqtrade 原生 Protections | 能否替代？ |
|-----------|---------------------------|:--:|
| 连续3笔亏损→停2h | `StoplossGuard` (lookback=3, stop_duration=120) | ✅ **可替代** |
| 连续5笔亏损→停12h | `StoplossGuard` (lookback=5, stop_duration=720) | ✅ **可替代** |
| 单日亏损>5%→停当天 | `MaxDrawdown` (max_allowed=0.05) — 但不是日维度 | ⚠️ **部分** |
| 单周亏损>12%→停本周 | `MaxDrawdown` — 但不是周维度 | ⚠️ **部分** |
| 总回撤>25%→暂停 | `MaxDrawdown` (max_allowed=0.25, stop_duration=无限) | ✅ **可替代** |
| 总回撤>40%→紧急停止 | `MaxDrawdown` (max_allowed=0.40, stop_duration=无限) | ✅ **可替代** |
| 交易对冷却 | `CooldownPeriod` (stop_duration=30) | ✅ **可替代** |
| Telegram 通知 | Freqtrade 原生 Telegram 集成 | ✅ **原生** |

### 2.6 最终结论：风控熔断

```
Freqtrade 原生 Protections 可替代约 70% 的自定义风控逻辑

原生可覆盖:
  ✅ StoplossGuard → 连续亏损熔断
  ✅ MaxDrawdown → 总回撤熔断
  ✅ CooldownPeriod → 交易对冷却
  ✅ Telegram → 通知

仍需自定义 (risk_manager.py 保留但大幅简化):
  ⚠️ 日/周维度的盈亏统计（Freqtrade原生无此维度）
  ⚠️ 利润锁定里程碑追踪
  ⚠️ 安全利润余额管理
  ⚠️ 盈亏调整系数的胜率计算
```

---

## 3. 修正后的风险控制架构

### 3.1 分层风控

```
┌─────────────────────────────────────────────────────┐
│              风控体系（修正后）                        │
│                                                      │
│  第1层: Freqtrade 原生 Protections（配置文件）        │
│  ├── StoplossGuard: 连续止损熔断                     │
│  ├── MaxDrawdown: 总回撤熔断                         │
│  ├── CooldownPeriod: 交易对冷却                      │
│  └── 无需额外代码，配置即用                           │
│                                                      │
│  第2层: 策略内置风控（策略代码内）                     │
│  ├── stoploss: 硬止损 -8%                            │
│  ├── trailing_stop: 移动止损                         │
│  └── 无需额外代码，参数即用                           │
│                                                      │
│  第3层: 轻量 risk_manager.py（仅保留原生不支持的）     │
│  ├── 日/周盈亏追踪（可选）                            │
│  ├── 利润锁定里程碑                                  │
│  ├── 安全利润余额                                    │
│  └── ~100行，极简                                    │
└─────────────────────────────────────────────────────┘
```

### 3.2 原生 Protections 配置示例

```json
{
  "protections": [
    {
      "method": "StoplossGuard",
      "lookback_period_candles": 20,
      "trade_limit": 3,
      "stop_duration_candles": 24,
      "only_per_pair": false
    },
    {
      "method": "StoplossGuard",
      "lookback_period_candles": 48,
      "trade_limit": 5,
      "stop_duration_candles": 144,
      "only_per_pair": false
    },
    {
      "method": "MaxDrawdown",
      "lookback_period_candles": 99999,
      "max_allowed_drawdown": 0.25,
      "stop_duration_candles": 99999
    },
    {
      "method": "MaxDrawdown",
      "lookback_period_candles": 99999,
      "max_allowed_drawdown": 0.40,
      "stop_duration_candles": 99999
    },
    {
      "method": "CooldownPeriod",
      "stop_duration_candles": 6,
      "only_per_pair": true
    }
  ]
}
```

> 注：日/周维度的亏损监控 Freqtrade 原生不支持，但需求优先级较低，可先行省略。如需实现，保留在 risk_manager.py 中（~30行）。

---

## 4. 最终审计结论

### 全部使用 Freqtrade 原生 API 的功能（无需额外方案）

| 类别 | 功能 | 原生API |
|------|------|---------|
| 交易对 | 发现 | `VolumePairList` + `StaticPairList` |
| 交易对 | 冷却 | `CooldownPeriod` |
| 指标 | HLC3/插针/短期动量/多TF | `populate_indicators` + `informative_pairs` |
| 入场 | 信号+确认+冷却 | `populate_entry_trend` + `confirm_trade_entry` |
| 离场 | 信号+自定义 | `populate_exit_trend` + `custom_exit` |
| 止损 | 硬止损+移动止损 | `stoploss` + `trailing_stop` |
| 仓位 | 动态仓位 | `custom_stake_amount` |
| 调仓 | 分批止盈+加仓 | `adjust_trade_position` |
| 风控 | 连续止损熔断 | `StoplossGuard` |
| 风控 | 总回撤熔断 | `MaxDrawdown` |
| 通知 | Telegram | 原生集成 |

### 唯一需要自定义的部分

| 功能 | 理由 | 复杂度 |
|------|------|:--:|
| `self._exit_state` 字典 | 追踪分批止盈已触发批次 | ~20行 |
| 利润锁定/里程碑/安全利润 | 策略专属逻辑 | ~60行（在 `custom_stake_amount` 内） |
| 盈亏调整系数（胜率统计） | 策略专属逻辑 | ~30行（在 `custom_stake_amount` 内） |
| 日/周盈亏追踪（可选） | Freqtrade无此维度 | ~30行（需时可加） |

### risk_manager.py 的角色变化

```
修正前: risk_manager.py (~150行) 
         ├── 熔断管理
         ├── 状态持久化
         ├── 利润锁定
         └── 盈亏统计

修正后: risk_manager.py (~60行，或直接合并到策略内)
         ├── 利润里程碑追踪（30行）
         ├── 安全利润管理（20行）
         └── 日/周盈亏（可选，30行）
         
         熔断 → 全由 Freqtrade Protections 接管 ✅
```

---

## 5. 结论

> **除分批止盈状态追踪（`self._exit_state` 字典）和利润锁定逻辑外，所有功能均有 Freqtrade 2026.5.1 原生 API 支持。无需任何 workaround 或 hack。**

`risk_manager.py` 可以从 ~150行 缩减到 ~60行，甚至可以直接合并到策略文件内部，进一步减少模块分散度。

---

*审计完成时间: 2026-06-13*
