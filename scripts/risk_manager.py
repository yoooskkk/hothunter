"""
风险状态管理 —— 轻量级，仅管理 Freqtrade 原生 Protections 不覆盖的部分

职责:
  1. 利润里程碑追踪 (总资产达到里程碑时锁定安全利润)
  2. 安全利润余额管理 (标记不可交易资金)
  3. 近期胜率统计 (供 custom_stake_amount 调整仓位)

所有熔断逻辑 (连续亏损、最大回撤、交易对冷却) 
已由 Freqtrade 原生 Protections 接管，此处不重复。
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional


class RiskManager:
    """
    风控状态管理器
    
    用法:
        rm = RiskManager("data/risk_state.json")
        rm.update_after_trade(profit=0.15, is_win=True)
        safe_profit = rm.get_safe_profit(total_capital=1200)
    
    状态文件格式:
    {
        "milestones": [1000, 5000],      # 已达成的里程碑
        "initial_capital": 100.0,         # 初始本金
        "trades": [                        # 最近30笔交易
            {"profit": 0.15, "is_win": true, "time": "..."}
        ],
        "peak_capital": 0.0              # 历史最高资产
    }
    """

    STATE_FILE: str = ""
    _state: Dict = {}
    _max_trades_in_memory: int = 30

    def __init__(self, state_file: str = "data/risk_state.json"):
        self.STATE_FILE = state_file
        self._state = self._load()

    # ---- 公开接口 ----

    def update_after_trade(self, profit: float, is_win: bool, timestamp: str) -> None:
        """每笔交易结束后调用，更新状态"""
        trades = self._state.get("trades", [])
        trades.append({"profit": profit, "is_win": is_win, "time": timestamp})
        if len(trades) > self._max_trades_in_memory:
            trades = trades[-self._max_trades_in_memory:]
        self._state["trades"] = trades
        self._save()

    def get_win_rate(self, lookback: int = 20) -> float:
        """获取近期胜率（最近N笔）"""
        trades = self._state.get("trades", [])
        recent = trades[-lookback:] if len(trades) > lookback else trades
        if not recent:
            return 0.50
        wins = sum(1 for t in recent if t["is_win"])
        return wins / len(recent)

    def get_safe_profit(self, total_capital: float) -> float:
        """
        计算已锁定的安全利润（不可交易资金）
        
        里程碑规则:
          总资产 ≥ 1000U: 锁定超过初始资本部分的 10%
          总资产 ≥ 5000U: 锁定 15%
          总资产 ≥ 20000U: 锁定 20%
          总资产 ≥ 50000U: 锁定 25%
        """
        initial = self._state.get("initial_capital", 100.0)
        profit = total_capital - initial
        if profit <= 0:
            return 0.0

        milestones = self._state.get("milestones", [])
        safe = 0.0
        new_milestones = list(milestones)

        milestone_rules = [
            (1000, 0.10),
            (5000, 0.15),
            (20000, 0.20),
            (50000, 0.25),
        ]

        for milestone, ratio in milestone_rules:
            if total_capital >= milestone and milestone not in milestones:
                locked = profit * ratio
                safe += locked
                new_milestones.append(milestone)

        if len(new_milestones) > len(milestones):
            self._state["milestones"] = new_milestones
            self._save()

        return safe

    def update_peak(self, total_capital: float) -> float:
        """更新并返回当前回撤比例"""
        peak = self._state.get("peak_capital", total_capital)
        if total_capital > peak:
            self._state["peak_capital"] = total_capital
            peak = total_capital
            self._save()
        return (peak - total_capital) / peak if peak > 0 else 0.0

    def set_initial_capital(self, capital: float) -> None:
        """设置初始本金（仅在首次运行时）"""
        if "initial_capital" not in self._state or self._state["initial_capital"] == 0:
            self._state["initial_capital"] = capital
            self._save()

    def get_initial_capital(self) -> float:
        return self._state.get("initial_capital", 100.0)

    # ---- 内部方法 ----

    def _load(self) -> Dict:
        """加载状态文件"""
        path = Path(self.STATE_FILE)
        if path.exists() and path.stat().st_size > 0:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {"milestones": [], "initial_capital": 100.0, "trades": [], "peak_capital": 0.0}

    def _save(self) -> None:
        """保存状态文件"""
        try:
            os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
            with open(self.STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, ensure_ascii=False)
        except OSError:
            pass  # 静默失败，不影响策略运行
