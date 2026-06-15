# 三个致命 Bug 分析 & 修复方案

## Bug 1: `_tp_state` 和 `custom_trade_info` 运行时漂移

### 问题
- `_tp_state[trade_id]` 存在内存中，机器人重启后丢失
- 重启后 Freqtrade 从数据库恢复持仓，但 `_tp_state` 为空
- 如果某笔交易已触发 TP1（盈利8%卖30%），重启后 profit 仍在8%~15%之间，会再次触发 TP1
- 导致重复卖出，仓位管理失控

### 修复
**放弃 `_tp_state` 字典，改用 `trade.stake_amount / trade.initial_stake_amount` 比值判断已触发的止盈批次**。

- 初始仓位: ratio = 1.0
- TP1 (卖30%)后: ratio ≈ 0.70
- TP2 (再卖30%)后: ratio ≈ 0.49
- TP3 (卖40%)后: 交易关闭

Freqtrade 的 `adjust_trade_position` 返回负值后，自动更新 `trade.stake_amount`。所以通过 ratio 可以反推已触发的止盈批次，无需内存状态。

## Bug 2: MTF 多时间框架 "未来函数"

### 问题
```python
pairs_15m = self.dp.get_pair_dataframe(metadata["pair"], "15m")
ema9_15m = np.asarray(ta.EMA(hlc3_15m, timeperiod=9))[-1]
```

`[-1]` 取 15m 数据框的最后一行。在 5m 回调中：

- **回测时**：15m 的 `[-1]` 包含了"未来数据"（已收盘的完整K线），回测时看到的 15m 信号在对应时间点实际上不可用
- **实盘时**：`[-1]` 是当前正在形成的 15m K线，每根 5m K线都会导致 `[-1]` 变化，信号闪烁

### 修复
使用 Freqtrade 的 `merge_informative_pair()` 合并数据，`ffill=True` 确保只用**已收盘**的 15m 数据：

```python
from freqtrade.strategy import merge_informative_pair

informative = self.dp.get_pair_dataframe(metadata["pair"], "15m")
informative["ema_9"] = ta.EMA(hlc3_15m, timeperiod=9)
informative["ema_21"] = ta.EMA(hlc3_15m, timeperiod=21)
informative["adx"] = ta.ADX(informative, timeperiod=14)

# ffill=True → 5m 数据点使用上一个已收盘的 15m 值
dataframe = merge_informative_pair(dataframe, informative, "5m", "15m", ffill=True)
```

## Bug 3: 金字塔加仓导致均价抬高与硬止损冲突

### 问题
- 入场 $1.00，加仓 $1.10 (50%)
- 均价 = ($1.00 + $1.10×0.5) / 1.5 = $1.033
- 硬止损 -8% = $0.951
- 此时价格从 $1.10 跌到 $0.98（未触及止损但已亏损）
- 加仓后的硬止损应该更紧

### 修复
添加 `custom_stoploss()` 方法，加仓后动态收紧止损：

```python
def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
    if trade.nr_of_successful_entries > 1:
        return -0.05  # 加仓后止损收紧到5%
    return -0.08  # 默认8%
```
