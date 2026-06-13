# HotHunter — 币安现货热点追踪策略

> **安全配置**：所有 API Key / Secret 通过环境变量注入，不写入配置文件，不提交到 git。
>
> **回测兼容性**：所有指标均可从历史 OHLCV 数据计算。需同时下载 5m + 15m 两个时间框架。

基于 Freqtrade 2026.5.1 的币安现货热点追踪策略，核心解决"100U→28万U→1万U"的巨大回撤问题。

## 核心创新

### 🛡️ 五层防线（防回撤）

| 层 | 机制 | 说明 |
|:--:|------|------|
| 1 | **硬止损 -8%** | 单笔最大亏损封顶 |
| 2 | **移动止损 5%** | 盈利≥3%后激活，从最高点回撤5%平仓 |
| 3 | **分批止盈 8/15/25%** | 逐批锁定利润（30%→30%→40%） |
| 4 | **利润阶梯锁定** | 资产越多，风险比例越小（15%→12%→8%→5%） |
| 5 | **原生熔断链** | Freqtrade StoplossGuard + MaxDrawdown + CooldownPeriod |

### 🔍 三层热点发现漏斗

```
StaticPairList(5个兜底) → VolumePairList(25个有量候选) → 策略内短期动量扫描(ROC 5m+15m)
```

不使用24h涨幅榜（追高陷阱），而是通过短期动量扫描发现"正在爆发"的币。

### 📉 防插针五层过滤

HLC3典型价格 + 影线罚分 + K线实体确认 + 多时间框架(5m+15m) + 入场冷却30min

## 安全配置（重要）

> **API Key / Secret 不写入配置文件，通过环境变量注入。**

```bash
# 1. 复制环境变量模板
cp .env.example .env

# 2. 编辑 .env 填入真实值（.env 已加入 .gitignore，不会提交）
# HOTHUNTER_EXCHANGE_KEY=your_binance_api_key
# HOTHUNTER_EXCHANGE_SECRET=your_binance_api_secret
# HOTHUNTER_TELEGRAM_TOKEN=your_bot_token
# HOTHUNTER_TELEGRAM_CHAT_ID=your_chat_id
```

配置文件（`config.live.json`）中通过 `${ENV_VAR}` 引用环境变量，Freqtrade 在运行时自动解析替换。

## 快速开始

### Docker 部署（推荐，2G2核适配）

```bash
# 1. 模拟盘运行（自动读取 .env 中的环境变量）
docker-compose up -d

# 2. 查看日志
docker logs -f hothunter

# 3. 切换实盘
#    编辑 docker-compose.yml，修改
#    --config .../config.dry.json  →  .../config.live.json
```

### 裸机部署

```bash
# 1. 安装依赖
pip install freqtrade>=2026.5.1

# 2. 加载环境变量
export $(cat .env | xargs)

# 3. 模拟盘
freqtrade trade \
  --config configs/config.dry.json \
  --strategy HotHunterStrategy

# 4. 实盘
freqtrade trade \
  --config configs/config.live.json \
  --strategy HotHunterStrategy
```

### 回测

```bash
# 1. 下载数据（建议 180天）
#    必须同时下载 5m + 15m（策略内多时间框架需要）
freqtrade download-data \
  --exchange binance \
  --pairs BTC/USDT ETH/USDT BNB/USDT SOL/USDT DOGE/USDT \
  --days 180 \
  --timeframe 5m 15m

# 2. 运行回测
freqtrade backtesting \
  --config configs/config.dry.json \
  --strategy HotHunterStrategy \
  --timerange 20260101-
```

> 回测兼容性：所有指标（HLC3、影线检测、ROC、MFI、OBV、EMA、ADX、RSI、15m多TF）均可从历史 OHLCV 数据计算，无需额外数据源。

## 项目结构

```
HotHunter/
├── strategies/
│   └── HotHunterStrategy.py      # 核心策略（~280行）
├── scripts/
│   └── risk_manager.py           # 风控状态管理（~120行）
├── configs/
│   ├── config.dry.json           # 模拟盘配置
│   └── config.live.json          # 实盘配置
├── data/
│   └── risk_state.json           # 风控状态持久化
├── docker-compose.yml            # Docker 部署
├── requirements.txt              # 依赖
└── README.md                     # 本文件
```

## 配置文件差异

| 配置项 | config.dry.json | config.live.json |
|--------|:--:|:--:|
| dry_run | true | false |
| API Key | 空 | 需填写 |
| Telegram | disabled | enabled |
| bot_name | HotHunter_Dry | HotHunter_Live |

**其他所有参数（策略、风控、Protections）完全相同**，确保模拟盘和实盘行为一致。

## 风控体系

系统不依赖自定义风控代码，而是使用 Freqtrade 原生 Protections：

| Protection | 作用 | 参数 |
|------------|------|------|
| StoplossGuard | 连续3笔止损→停2h | lookback=20, limit=3, duration=24 |
| StoplossGuard | 连续5笔止损→停12h | lookback=48, limit=5, duration=144 |
| MaxDrawdown | 总回撤>25%→暂停 | max_allowed=0.25 |
| MaxDrawdown | 总回撤>40%→紧急停止 | max_allowed=0.40 |
| CooldownPeriod | 交易对冷却30min | duration=6 candles |

## 利润锁定机制

当总资产达到里程碑时，自动锁定部分利润（标记为不可交易资金）：

| 里程碑 | 锁定比例 | 目的 |
|--------|:--:|------|
| 1000 U | 10% | 初具规模 |
| 5000 U | 15% | 小有成就 |
| 20000 U | 20% | 稳定期 |
| 50000 U | 25% | 保守期 |

> 锁定的利润在 `custom_stake_amount` 中排除，不参与后续交易冒风险。

## 演进路线

```
Phase 1 (当前): 纯现货热点追踪 → 验证策略逻辑 + 积累数据
Phase 2 (未来):  现货持续运行，提取40%历史利润尝试2x合约增强
                 ├── 合约策略复用同一套风控框架
                 └── 仅用利润参与，本金不动
```

## 性能（2G2核服务器）

- **CPU**: < 10%（只监控~30个交易对，每5min计算一次）
- **内存**: ~500MB（Freqtrade ~300MB + 策略数据 ~30MB + Python ~200MB）
- **磁盘**: 日志限制 10MB × 3轮转，SQLite 数据库 ~100MB/月
- **推荐**: 使用 Docker 部署，自动重启，资源限制

## 关键技术参数

| 参数 | 值 | 说明 |
|------|-----|------|
| time frame | 5m | 主K线周期 |
| max_open_trades | 5 | 最大同时持仓 |
| stoploss | -0.08 | 硬止损 8% |
| trailing_stop | 0.03/0.05 | 盈利3%激活，回撤5%平仓 |
| price_source | hlc3 | 典型价格(High+Low+Close)/3 |
| hot_score_threshold | 65 | 热点评分阈值 |
| pyramid_max | 2次 | 最多加仓2次 |
| max_stake_per_coin | 20% | 单币仓位上限 |
| max_stake_absolute | 2000U | 单笔绝对上限 |
