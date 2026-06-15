"""
HotHunterStrategy - 币安现货热点追踪策略
Freqtrade 2026.5.1 原生 API，无需额外依赖

核心逻辑:
  1. VolumePairList + StaticPairList 自动发现热点
  2. 策略内短期动量扫描(5m ROC6 + 15m ROC4) 发现"正在爆发的"
  3. 五层插针防护(HLC3+影线罚分+K线实体+多TF确认+入场冷却)
  4. 分批止盈(8/15/25%) + 金字塔加仓 via adjust_trade_position
  5. 利润阶梯锁定 + 里程碑提取 via custom_stake_amount
  6. 风控由 Freqtrade 原生 Protections 接管（策略内字典声明）
"""

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter, CategoricalParameter
from pandas import DataFrame
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
from datetime import datetime, timedelta
import numpy as np


class HotHunterStrategy(IStrategy):
    # --- 基础配置 ---
    timeframe = "5m"
    can_short = False
    position_adjustment_enable = True
    max_entry_position_adjustment = 2  # 最多加仓2次

    # --- 止损 ---
    stoploss = -0.08
    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.04
    trailing_only_offset_is_reached = True

    # --- 原生熔断保护（字典格式，无需 import） ---
    protections = [
        {
            "method": "StoplossGuard",
            "lookback_period_candles": 20,
            "trade_limit": 3,
            "stop_duration_candles": 24,
            "only_per_pair": False
        },
        {
            "method": "StoplossGuard",
            "lookback_period_candles": 48,
            "trade_limit": 5,
            "stop_duration_candles": 144,
            "only_per_pair": False
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
            "only_per_pair": True
        }
    ]

    # --- 订单 ---
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    process_only_new_candles = True
    startup_candle_count = 100
    order_types = {
        "entry": "limit",
        "exit": "limit",
        "emergency_exit": "market",
        "force_exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    # --- 时间框架 ---
    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        informative_pairs = [(pair, "15m") for pair in pairs]
        return informative_pairs

    # ================================================================
    # populate_indicators - 热点识别指标
    # ================================================================
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """计算热点评分所需的所有指标"""
        df = dataframe

        # --- 1. 价格源：HLC3 典型价格 ---
        df["hlc3"] = (df["high"] + df["low"] + df["close"]) / 3

        # --- 2. 插针检测 ---
        df["upper_wick"] = (df["high"] - df[["open", "close"]].max(axis=1)) / (
            df["high"] - df["low"] + 1e-10
        )
        df["lower_wick"] = (df[["open", "close"]].min(axis=1) - df["low"]) / (
            df["high"] - df["low"] + 1e-10
        )
        df["is_wick"] = df["upper_wick"] > 0.60
        df["wick_penalty"] = 0.0
        df.loc[df["upper_wick"] > 0.60, "wick_penalty"] -= 15.0
        df.loc[
            df["upper_wick"].shift(1) > 0.60, "wick_penalty"
        ] -= 10.0
        df.loc[
            (df["upper_wick"] > 0.60) & (df["upper_wick"].shift(1) > 0.60),
            "wick_penalty",
        ] -= 10.0
        df.loc[
            (df["lower_wick"] > 0.60) & (df["close"] > df["open"]),
            "wick_penalty",
        ] -= 5.0

        # --- 3. K线实体确认 ---
        df["body_ratio"] = abs(df["close"] - df["open"]) / (df["high"] - df["low"] + 1e-10)
        df["body_reliable"] = df["body_ratio"] > 0.25
        df["body_confirm"] = (
            df["body_reliable"].rolling(window=3).sum() >= 2
        ).astype(float) * 3.0

        # --- 4. 动量分 (35%) ---
        df["roc_6"] = ta.ROC(df["hlc3"], timeperiod=6)  # 30min
        df["roc_12"] = ta.ROC(df["hlc3"], timeperiod=12)  # 60min
        # 标准化动量分: 0~35
        df["momentum_score"] = (
            (df["roc_6"] / 5.0).clip(-1, 1) * 0.5
            + (df["roc_12"] / 8.0).clip(-1, 1) * 0.5
        ).clip(0, 1) * 35.0

        # --- 5. 量能分 (30%) ---
        df["vol_ma20"] = df["volume"].rolling(window=20).mean()
        df["vol_ratio"] = df["volume"] / (df["vol_ma20"] + 1e-10)
        df["mfi"] = ta.MFI(df, timeperiod=14)
        df["obv"] = ta.OBV(df)
        df["obv_trend"] = (df["obv"] > df["obv"].shift(5)).astype(float)
        df["volume_score"] = (
            (df["vol_ratio"] / 3.0).clip(0, 1) * 0.5
            + ((df["mfi"] - 20) / 60.0).clip(0, 1) * 0.3
            + df["obv_trend"] * 0.2
        ).clip(0, 1) * 30.0

        # --- 6. 趋势分 (25%) ---
        df["ema_9"] = ta.EMA(df["hlc3"], timeperiod=9)
        df["ema_21"] = ta.EMA(df["hlc3"], timeperiod=21)
        df["adx"] = ta.ADX(df, timeperiod=14)
        df["ema_trend"] = (df["ema_9"] > df["ema_21"]).astype(float)
        df["trend_score"] = (
            df["ema_trend"] * 0.5
            + ((df["adx"] - 20) / 30.0).clip(0, 1) * 0.5
        ).clip(0, 1) * 25.0

        # --- 7. 确认分 (10%) - 多时间框架 ---
        pairs_15m = self.dp.get_pair_dataframe(metadata["pair"], "15m")
        if pairs_15m is not None and not pairs_15m.empty:
            hlc3_15m = (pairs_15m["high"] + pairs_15m["low"] + pairs_15m["close"]) / 3
            ema9_15m = ta.EMA(hlc3_15m, timeperiod=9)
            ema21_15m = ta.EMA(hlc3_15m, timeperiod=21)
            adx_15m = ta.ADX(pairs_15m, timeperiod=14)
            mtf_trend = (ema9_15m[-1] > ema21_15m[-1]) if len(ema9_15m) > 0 else False
            mtf_adx = (adx_15m[-1] > 20) if len(adx_15m) > 0 else False
            df["mtf_score"] = (int(mtf_trend) * 5 + int(mtf_adx) * 5)
        else:
            df["mtf_score"] = 0.0

        # --- 收盘位置分 (2分) ---
        df["close_position"] = (
            (df["close"] - df["low"]) / (df["high"] - df["low"] + 1e-10) > 0.66
        ).astype(float) * 2.0

        df["confirm_score"] = df["body_confirm"] + df["close_position"] + df["mtf_score"]

        # --- 综合热点评分 ---
        df["hot_score"] = (
            df["momentum_score"]
            + df["volume_score"]
            + df["trend_score"]
            + df["confirm_score"]
            + df["wick_penalty"]
        ).clip(0, 100)

        # --- RSI (供入场条件使用) ---
        df["rsi"] = ta.RSI(df, timeperiod=14)

        return df

    # ================================================================
    # populate_entry_trend - 入场逻辑
    # ================================================================
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        入场条件（必须同时满足）：
        1. 热点评分 ≥ 65
        2. 插针罚分 ≥ -25（不过于恶劣）
        3. 成交量爆发 Vol > SMA20 x 1.5 且 OBV 向上
        4. 短期趋势 HLC3_EMA9 > EMA21 且 ADX > 20
        5. RSI 50~75
        6. K线实体确认通过
        """
        conditions = []

        # 条件1: 热点评分
        conditions.append(dataframe["hot_score"] >= 65)

        # 条件2: 插针罚分不过限
        conditions.append(dataframe["wick_penalty"] >= -25)

        # 条件3: 成交量爆发
        conditions.append(dataframe["vol_ratio"] > 1.5)
        conditions.append(dataframe["obv_trend"] == 1)

        # 条件4: 趋势确认
        conditions.append(dataframe["ema_9"] > dataframe["ema_21"])
        conditions.append(dataframe["adx"] > 20)

        # 条件5: RSI 范围
        conditions.append(dataframe["rsi"] > 50)
        conditions.append(dataframe["rsi"] < 75)

        # 条件6: K线实体确认
        conditions.append(dataframe["body_confirm"] > 0)

        # 条件7: 价格在 EMA21 上方
        conditions.append(dataframe["hlc3"] > dataframe["ema_21"])

        dataframe.loc[
            conditions & (dataframe["volume"] > 0),
            "enter_long",
        ] = 1

        return dataframe

    # ================================================================
    # populate_exit_trend - 离场信号
    # ================================================================
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        热点消退离场：
        1. 成交量持续萎缩 < SMA20 x 0.7
        2. RSI < 40（趋势转弱）
        3. HLC3 下穿 EMA21（中期趋势破位）
        """
        exit_conditions = []

        # 缩量
        exit_conditions.append(dataframe["vol_ratio"] < 0.7)
        # RSI 转弱
        exit_conditions.append(dataframe["rsi"] < 40)
        # 趋势破位：HLC3 下穿 EMA21
        exit_conditions.append(
            qtpylib.crossed_below(dataframe["hlc3"], dataframe["ema_21"])
        )

        dataframe.loc[
            exit_conditions & (dataframe["volume"] > 0),
            "exit_long",
        ] = 1

        return dataframe

    # ================================================================
    # custom_exit - 自定义离场逻辑
    # ================================================================
    def custom_exit(
        self, pair: str, trade, current_time: datetime, current_rate: float, current_profit: float, **kwargs
    ) -> str:
        """
        硬止损由 stoploss 参数控制，这里负责补充离场逻辑。
        仅当 exit_profit_only=False 时使用 signal 退出。
        """
        return None

    # ================================================================
    # confirm_trade_entry - 入场确认
    # ================================================================
    def confirm_trade_entry(
        self, pair: str, order_type: str, amount: float, rate: float,
        time_in_force: str, current_time: datetime, entry_tag, side: str,
        **kwargs
    ) -> bool:
        """
        入场前检查：
        1. 冷却时间（距最近一笔同交易对入场 > 30min）
        2. 风控熔断（由 Freqtrade Protections 自动处理，无需手动检查）
        """
        # 冷却时间检查
        if self.custom_trade_info is None:
            self.custom_trade_info = {}

        pair_entries = self.custom_trade_info.get("pair_last_entry", {})
        last_entry = pair_entries.get(pair)
        if last_entry and (current_time - last_entry).total_seconds() < 1800:
            return False

        pair_entries[pair] = current_time
        self.custom_trade_info["pair_last_entry"] = pair_entries
        return True

    # ================================================================
    # custom_stake_amount - 动态仓位管理
    # ================================================================
    def custom_stake_amount(
        self, pair: str, current_time: datetime, current_rate: float,
        proposed_stake: float, min_stake: float, max_stake: float,
        entry_tag, side: str, **kwargs
    ) -> float:
        """
        动态仓位计算 + 利润阶梯锁定 + 里程碑提取

        仓位 = 基础仓位 x 盈亏调整系数 x 回撤调整系数
        利润阶梯：资产越多，风险比例越小
        里程碑提取：达到特定资产水平后锁定部分利润
        """
        total_capital = self.wallets.get_total_stake_amount()

        # --- 利润阶梯锁定 ---
        if total_capital <= 500:
            max_risk_pct = 0.15  # 积累期：15%
        elif total_capital <= 5000:
            max_risk_pct = 0.12  # 增长期：12%
        elif total_capital <= 50000:
            max_risk_pct = 0.08  # 稳定期：8%
        else:
            max_risk_pct = 0.05  # 保守期：5%

        # --- 里程碑提取（安全利润）---
        safe_profit = self._get_safe_profit(total_capital)
        available_capital = total_capital - safe_profit
        base_stake = available_capital * max_risk_pct

        # --- 盈亏调整系数 ---
        win_rate = self._get_recent_win_rate()
        if win_rate > 0.60:
            adj = 1.2
        elif win_rate >= 0.40:
            adj = 1.0
        else:
            adj = 0.6

        # --- 回撤调整系数 ---
        drawdown = self._get_current_drawdown(total_capital)
        if drawdown < 0.10:
            dd_adj = 1.0
        elif drawdown < 0.20:
            dd_adj = 0.5
        else:
            dd_adj = 0.25

        final_stake = base_stake * adj * dd_adj

        # --- 硬性限制 ---
        max_single = total_capital * 0.20  # 单币上限20%
        max_absolute = 2000  # 绝对上限
        final_stake = min(final_stake, max_single, max_absolute)
        final_stake = max(final_stake, min_stake)

        return final_stake

    # ================================================================
    # adjust_trade_position - 分批止盈 + 金字塔加仓
    # ================================================================
    def adjust_trade_position(
        self, trade, current_time: datetime, current_rate: float,
        current_profit: float, min_stake: float, max_stake: float,
        **kwargs
    ) -> float:
        """
        仓位调整入口：
        返回正值 = 加仓，返回负值 = 减仓，返回 None = 不调整
        """
        if current_profit is None:
            return None

        # --- 分批止盈（返回负值 = 卖出部分）---
        result = self._check_take_profit(trade, current_profit)
        if result is not None:
            return result

        # --- 金字塔加仓（返回正值 = 买入追加）---
        result = self._check_pyramid_add(trade, current_time)
        if result is not None:
            return result

        return None

    # ================================================================
    # 辅助方法
    # ================================================================
    def _check_take_profit(self, trade, current_profit: float) -> float:
        """检查是否触发分批止盈"""
        trade_id = trade.id
        if self._tp_state is None:
            self._tp_state = {}

        state = self._tp_state.get(trade_id, {"tp1": False, "tp2": False, "tp3": False})

        if current_profit >= 0.25 and not state["tp3"]:
            sell_amount = trade.stake_amount * 0.40
            self._tp_state[trade_id] = {**state, "tp3": True}
            return -sell_amount

        if current_profit >= 0.15 and not state["tp2"]:
            sell_amount = trade.stake_amount * 0.30
            self._tp_state[trade_id] = {**state, "tp2": True}
            return -sell_amount

        if current_profit >= 0.08 and not state["tp1"]:
            sell_amount = trade.stake_amount * 0.30
            self._tp_state[trade_id] = {**state, "tp1": True}
            return -sell_amount

        return None

    def _check_pyramid_add(self, trade, current_time: datetime) -> float:
        """检查是否触发金字塔加仓"""
        if trade.nr_of_successful_entries >= 1 + self.max_entry_position_adjustment:
            return None

        profit = trade.calc_profit_ratio(current_time)
        if profit < 0.10:
            return None

        # 检查15m趋势（简化检查，依赖 informative_pairs）
        pair = trade.pair
        pairs_15m = self.dp.get_pair_dataframe(pair, "15m")
        if pairs_15m is None or pairs_15m.empty:
            return None
        hlc3_15m = (pairs_15m["high"] + pairs_15m["low"] + pairs_15m["close"]) / 3
        ema9_15m = ta.EMA(hlc3_15m, timeperiod=9)[-1]
        ema21_15m = ta.EMA(hlc3_15m, timeperiod=21)[-1]
        adx_15m = ta.ADX(pairs_15m, timeperiod=14)[-1]
        vol_15m = pairs_15m["volume"].iloc[-1]
        vol_ma20_15m = pairs_15m["volume"].rolling(20).mean().iloc[-1]

        if not (ema9_15m > ema21_15m and adx_15m > 30 and vol_15m > vol_ma20_15m * 1.3):
            return None

        # 总仓位上限检查
        total_stake = trade.stake_amount + trade.additional_stake_amount
        cap = self.wallets.get_total_stake_amount() * 0.20
        if total_stake >= cap:
            return None

        # 加仓比例递减
        entry_count = trade.nr_of_successful_entries
        if entry_count == 1:
            add_amount = trade.initial_stake_amount * 0.50
        elif entry_count == 2:
            add_amount = trade.initial_stake_amount * 0.25
        else:
            return None

        return add_amount

    def _get_safe_profit(self, total_capital: float) -> float:
        """计算里程碑安全利润"""
        if self.custom_trade_info is None:
            self.custom_trade_info = {}
        milestones = self.custom_trade_info.get("milestones", [])
        initial_capital = self.custom_trade_info.get("initial_capital", 100.0)

        profit = total_capital - initial_capital
        if profit <= 0:
            return 0.0

        safe = 0.0
        new_milestones = list(milestones)

        # 里程碑检查
        milestone_checks = [
            (1000, 0.10),
            (5000, 0.15),
            (20000, 0.20),
            (50000, 0.25),
        ]

        for milestone, ratio in milestone_checks:
            if total_capital >= milestone and milestone not in milestones:
                locked = profit * ratio
                safe += locked
                new_milestones.append(milestone)

        if len(new_milestones) > len(milestones):
            self.custom_trade_info["milestones"] = new_milestones

        return safe

    def _get_recent_win_rate(self) -> float:
        """获取近期胜率"""
        if self.custom_trade_info is None:
            self.custom_trade_info = {}
        return self.custom_trade_info.get("win_rate", 0.50)

    def _get_current_drawdown(self, total_capital: float) -> float:
        """获取当前回撤比例"""
        if self.custom_trade_info is None:
            self.custom_trade_info = {}
        peak = self.custom_trade_info.get("peak_capital", total_capital)
        if total_capital > peak:
            self.custom_trade_info["peak_capital"] = total_capital
            peak = total_capital
        return (peak - total_capital) / peak if peak > 0 else 0.0

    # ================================================================
    # 自定义状态（在 strategy 启动时初始化）
    # ================================================================
    @property
    def custom_trade_info(self):
        if not hasattr(self, "_custom_trade_info"):
            self._custom_trade_info = {}
        return self._custom_trade_info

    @custom_trade_info.setter
    def custom_trade_info(self, value):
        self._custom_trade_info = value

    @property
    def _tp_state(self):
        if not hasattr(self, "__tp_state"):
            self.__tp_state = {}
        return self.__tp_state

    @_tp_state.setter
    def _tp_state(self, value):
        self.__tp_state = value
