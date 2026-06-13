# API Key 安全方案 + 回测兼容性分析

## 1. 安全问题：配置文件中的敏感数据

### 1.1 现状问题

```json
// config.live.json 当前写法（不安全）
"exchange": {
    "key": "YOUR_API_KEY",
    "secret": "YOUR_API_SECRET"
},
"telegram": {
    "token": "YOUR_TELEGRAM_BOT_TOKEN",
    "chat_id": "YOUR_CHAT_ID"
}
```

> 明文写入配置文件 → git 提交泄露 → 镜像构建泄露 → 服务器被入侵后泄露

### 1.2 解决方案：Freqtrade 原生环境变量

Freqtrade 原生支持 `${ENV_VAR}` 语法，在配置文件中引用环境变量：

```json
// config.live.json（安全）
"exchange": {
    "key": "${HOTHUNTER_EXCHANGE_KEY}",
    "secret": "${HOTHUNTER_EXCHANGE_SECRET}"
},
"telegram": {
    "token": "${HOTHUNTER_TELEGRAM_TOKEN}",
    "chat_id": "${HOTHUNTER_TELEGRAM_CHAT_ID}"
}
```

### 1.3 部署方式

```bash
# 方式A: 系统环境变量
export HOTHUNTER_EXCHANGE_KEY="your_api_key"
export HOTHUNTER_EXCHANGE_SECRET="your_api_secret"
export HOTHUNTER_TELEGRAM_TOKEN="your_bot_token"
export HOTHUNTER_TELEGRAM_CHAT_ID="your_chat_id"
freqtrade trade --config configs/config.live.json

# 方式B: Docker Compose（推荐）
# 通过 docker-compose.yml 的 environment 或 env_file 传入
# .env 文件不提交到 git
```

---

## 2. 回测兼容性分析

### 2.1 逐项验证

| 指标 | 计算方式 | 所需数据 | 回测支持 |
|------|----------|----------|:--:|
| HLC3 | `(high+low+close)/3` | OHLCV | ✅ |
| 上/下影线比例 | `(H-max(O,C))/(H-L)` | OHLCV | ✅ |
| 影线罚分 | 自定义逻辑 | 已有列 | ✅ |
| K线实体比例 | `abs(C-O)/(H-L)` | OHLCV | ✅ |
| ROC(6), ROC(12) | `ta.ROC(hlc3, 6)` | HLC3 列 | ✅ |
| Vol_MA20 | `rolling(20).mean()` | volume | ✅ |
| MFI(14) | `ta.MFI()` | OHLCV | ✅ |
| OBV | `ta.OBV()` | OHLCV | ✅ |
| EMA(9), EMA(21) | `ta.EMA(hlc3, N)` | HLC3 列 | ✅ |
| ADX(14) | `ta.ADX()` | OHLCV | ✅ |
| RSI(14) | `ta.RSI()` | OHLCV | ✅ |
| **15m 多时间框架** | `dp.get_pair_dataframe(pair, '15m')` | 15m OHLCV | ⚠️ **需额外下载** |

### 2.2 关键发现：15m 多时间框架

`informative_pairs()` 声明的 15m 数据在回测中**可以使用**，但必须满足两个条件：

**条件1：下载 15m 历史数据**
```bash
freqtrade download-data \
  --exchange binance \
  --timeframe 5m 15m \    # ← 必须同时指定两个时间框架
  --days 180 \
  --pairs BTC/USDT ETH/USDT BNB/USDT SOL/USDT DOGE/USDT
```

**条件2：回测数据时间范围足够**
- `startup_candle_count: 60`（5m candles = 5小时）
- 15m 数据回溯 ~20 candles = 5小时
- 总最小数据窗口: 180天完全够用 ✅

### 2.3 VolumePairList 回测支持

Freqtrade 回测引擎原生支持 VolumePairList：
- 回测时使用**历史成交量数据**模拟交易对轮换
- `refresh_period=86400`（24h）在回测中正常工作
- **不需要**在 `pair_whitelist` 中预先列出所有币种

但 `min_value=100000` 这个过滤条件在回测中也有效。

### 2.4 性能优化建议

当前 `populate_indicators` 中存在一个**潜在的性能问题**：

```python
# 当前代码：每个5m K线都调用一次 dp.get_pair_dataframe
pairs_15m = self.dp.get_pair_dataframe(metadata["pair"], "15m")
```

这在**实盘中**没有问题（每5min调用一次），但在**回测中**会为每根K线都调用。Freqtrade 内部有缓存机制，所以不会太慢，但如果我们想更优雅地处理，可以使用 `merge_informative_pair` 方式。

不过当前实现已经可以工作，只是不够优雅。考虑到"不过度设计"的原则，可以选择：
- **保持现状**（推荐）：Freqtrade 有内部缓存，回测中不影响结果
- 优化方案：在 `populate_indicators` 开头合并15m数据（对性能提升不大）

### 2.5 回测配置建议

```json
// 回测专用配置片段
{
    "timeframe": "5m",
    "startup_candle_count": 100,  // 增加到100确保15m指标充分热身
    // ... 其他配置不变
}
```

`startup_candle_count` 从 60 增加到 100 更安全：
- 60 candles × 5m = 5h → 15m = 20 candles
- 100 candles × 5m = 8.3h → 15m = 33 candles
- ADX(14) 需要 14+14=28 candles 热身 → 33 > 28 ✅

---

## 3. 需要修改的文件

### 3.1 config.live.json
- `exchange.key` → `"${HOTHUNTER_EXCHANGE_KEY}"`
- `exchange.secret` → `"${HOTHUNTER_EXCHANGE_SECRET}"`
- `telegram.token` → `"${HOTHUNTER_TELEGRAM_TOKEN}"`
- `telegram.chat_id` → `"${HOTHUNTER_TELEGRAM_CHAT_ID}"`

### 3.2 docker-compose.yml
- 添加 `env_file: .env` 或 `environment:` 块

### 3.3 config.dry.json
- `startup_candle_count` 从 60 → 100

### 3.4 新增 .env.example
- 提供环境变量模板（不包含真实值）

### 3.5 新增 .gitignore
- `.env` 文件不提交

### 3.6 README.md
- 更新安全配置说明
- 增加回测数据下载步骤
