#!/usr/bin/env python3
"""
paper.py - 狙击模式模拟交易 (Sniper Mode Paper Trading)

实时监控 Polymarket 市场，当价格低于目标价时触发买入。
这是 Taker 逻辑：我认为某资产值 X，当市场价格低于 X 时我买入。

Usage:
    python paper.py
"""

import os
import sys
import time
import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
from dotenv import load_dotenv

# 导入核心模块
from core import (
    WalletManager,
    MarketScanner,
    MarketInfo,
    REAL_MARKET_PARAMS,
    GasStrategy,
    Platform,
    logger
)

# 导入交易执行器
from trade_executor import TradeExecutor, ExecutionMode, TxResult

# 加载环境变量
load_dotenv()


# ============================================================
# 颜色输出工具
# ============================================================
class Colors:
    """ANSI 颜色代码"""
    RESET = '\033[0m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GRAY = '\033[90m'
    BOLD = '\033[1m'


def print_green(msg: str):
    print(f"{Colors.GREEN}{msg}{Colors.RESET}")


def print_red(msg: str):
    print(f"{Colors.RED}{msg}{Colors.RESET}")


def print_yellow(msg: str):
    print(f"{Colors.YELLOW}{msg}{Colors.RESET}")


def print_gray(msg: str):
    print(f"{Colors.GRAY}{msg}{Colors.RESET}")


def print_cyan(msg: str):
    print(f"{Colors.CYAN}{msg}{Colors.RESET}")


# ============================================================
# 数据结构
# ============================================================
@dataclass
class TradeRecord:
    """交易记录"""
    timestamp: datetime
    action: str           # BUY / SELL
    price: float          # 成交价格
    target_price: float   # 目标价格
    price_gap: float      # 价差 (target - current)
    amount_usdc: float    # 交易金额
    shares_acquired: float  # 获得的份额
    gas_cost: float       # Gas 费用
    tx_hash: str          # 交易哈希


@dataclass
class SniperAccount:
    """狙击者账户"""
    initial_balance: float
    current_balance: float
    total_trades: int
    total_shares: float        # 持有的总份额
    avg_buy_price: float       # 平均买入价格
    total_spent: float         # 总支出
    total_gas_spent: float
    trade_history: List[TradeRecord]

    @property
    def unrealized_pnl(self) -> float:
        """未实现盈亏 (基于目标价格)"""
        if self.total_shares == 0:
            return 0.0
        # 假设目标价格就是我们认为的公允价值
        return 0.0  # 需要外部传入当前价格计算

    @property
    def roi(self) -> float:
        if self.initial_balance == 0:
            return 0.0
        return ((self.current_balance - self.initial_balance) / self.initial_balance) * 100


# ============================================================
# Sniper Trading Engine - 狙击交易引擎
# ============================================================
class SniperTradingEngine:
    """
    狙击模式交易引擎

    核心逻辑 (Taker 逻辑):
    - 用户设定目标价格 (target_price): 我认为这个资产值多少
    - 监控市场当前卖价 (ask_price)
    - 当 ask_price < target_price 时触发买入
    - 价差 = target_price - ask_price (正值表示有利可图)

    示例:
    - 我认为 "Trump wins" 值 $0.50
    - 当市场 Ask 价格 = $0.40 时
    - Price Gap = 0.50 - 0.40 = 0.10 (10% 利润空间)
    - 触发买入!
    """

    GAMMA_API_BASE = "https://gamma-api.polymarket.com"
    MARKETS_ENDPOINT = f"{GAMMA_API_BASE}/markets"

    # 默认参数
    DEFAULT_POSITION_SIZE = 50.0       # 默认每笔交易金额 $50
    GAS_LIMIT = 300000                 # Gas Limit
    MIN_PRICE_GAP = 0.02               # 最小价差门槛 2%

    def __init__(
        self,
        market_id: str,
        market_question: str = "Unknown Market",
        target_price: float = 0.50,     # 目标价格 (我认为它值多少)
        initial_balance: float = 10000.0,
        position_size: float = DEFAULT_POSITION_SIZE,
        min_price_gap: float = MIN_PRICE_GAP,
        execution_mode: ExecutionMode = ExecutionMode.DRY_RUN
    ):
        """
        初始化狙击引擎

        Args:
            market_id: 市场 ID
            market_question: 市场问题描述
            target_price: 目标价格 (你认为资产的公允价值)
            initial_balance: 初始虚拟资金
            position_size: 每笔交易金额
            min_price_gap: 最小触发价差
            execution_mode: 执行模式 (DRY_RUN/LIVE)
        """
        self.market_id = str(market_id)
        self.market_question = market_question
        self.target_price = target_price
        self.position_size = position_size
        self.min_price_gap = min_price_gap
        self.execution_mode = execution_mode

        # 虚拟账户
        self.account = SniperAccount(
            initial_balance=initial_balance,
            current_balance=initial_balance,
            total_trades=0,
            total_shares=0.0,
            avg_buy_price=0.0,
            total_spent=0.0,
            total_gas_spent=0.0,
            trade_history=[]
        )

        # HTTP Session - 带重试机制
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({
            'User-Agent': 'ArbitrageBot-Sniper/6.0',
            'Accept': 'application/json'
        })

        # 交易执行器
        self.executor = TradeExecutor(mode=execution_mode)

        # Web3 连接 (用于获取实时 Gas Price)
        self.wallet_manager = WalletManager()
        self._web3_connected = False

        # ============================================================
        # 风控参数 (Risk Control)
        # ============================================================
        self.max_position_usdc = 500.0      # 最大持仓限制 (硬顶)
        self.cooldown_seconds = 30          # 交易冷却时间 (秒)
        self.last_trade_time = 0            # 上次交易时间戳 (Unix timestamp)
        self.current_position_usdc = 0.0    # 当前累计持仓金额

        # 监控统计
        self.ticks = 0
        self.opportunities_found = 0
        self.start_time = None

    def connect(self) -> bool:
        """连接所有必要服务"""
        # 连接 Web3
        if self.wallet_manager.connect():
            self._web3_connected = True
            print_green("✅ Web3 connected - Real-time gas prices enabled")
        else:
            print_yellow("⚠️ Web3 connection failed - Using default gas prices")

        # 连接交易执行器
        if self.executor.connect():
            print_green("✅ Trade Executor connected")
            return True
        else:
            print_yellow("⚠️ Trade Executor connection failed - Trades will be simulated locally")
            return True  # 继续运行，只是没有执行器

    def get_current_gas_price(self) -> float:
        """获取当前 Gas Price (Gwei)"""
        if not self._web3_connected or not self.wallet_manager.w3:
            return 50.0  # 默认值

        try:
            gas_price_wei = self.wallet_manager.w3.eth.gas_price
            gas_price_gwei = gas_price_wei / 1e9
            return gas_price_gwei
        except Exception:
            return 50.0

    def calculate_gas_cost_usd(self, gas_price_gwei: float) -> float:
        """计算 Gas 费用 (USD)"""
        matic_price_usd = 0.50
        gas_cost_matic = (self.GAS_LIMIT * gas_price_gwei) / 1e9
        gas_cost_usd = gas_cost_matic * matic_price_usd
        return gas_cost_usd

    def fetch_market_data(self) -> Optional[Dict]:
        """获取市场实时数据"""
        try:
            url = f"{self.MARKETS_ENDPOINT}/{self.market_id}"
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            logger.warning(f"获取市场数据超时")
            return None
        except requests.exceptions.ConnectionError:
            logger.warning(f"网络连接错误")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"获取市场数据失败: {e}")
            return None
        except Exception as e:
            logger.error(f"未知错误: {e}")
            return None

    def parse_market_data(self, data: Dict) -> Dict:
        """解析市场数据"""
        best_bid = float(data.get('bestBid', 0) or 0)
        best_ask = float(data.get('bestAsk', 0) or 0)

        # 如果没有 bid/ask，从 outcomePrices 解析
        if best_bid == 0 and best_ask == 0:
            outcome_prices = data.get('outcomePrices', '[]')
            if isinstance(outcome_prices, str):
                try:
                    prices = json.loads(outcome_prices)
                    if prices and len(prices) >= 1:
                        mid_price = float(prices[0])
                        best_bid = mid_price * 0.98
                        best_ask = mid_price * 1.02
                except (json.JSONDecodeError, ValueError):
                    pass

        mid_price = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0

        return {
            'bid': best_bid,
            'ask': best_ask,
            'mid_price': mid_price,
            'volume': float(data.get('volume', 0) or 0)
        }

    def calculate_opportunity(
        self,
        current_ask: float,
        gas_cost_usd: float
    ) -> Dict:
        """
        计算狙击机会

        核心逻辑:
        - Price Gap = Target Price - Current Ask
        - 如果 Price Gap > min_price_gap，则有机会
        - 预期利润 = (shares * target_price) - cost - gas

        Args:
            current_ask: 当前卖价 (我们的买入价)
            gas_cost_usd: Gas 费用

        Returns:
            Dict: 机会分析结果
        """
        # 价差计算 (正值 = 有利可图)
        price_gap = self.target_price - current_ask

        # 计算如果买入能获得多少份额
        if current_ask > 0:
            shares_acquired = self.position_size / current_ask
        else:
            shares_acquired = 0

        # 预期价值 (假设最终价格达到目标价)
        expected_value = shares_acquired * self.target_price

        # 总成本
        total_cost = self.position_size + gas_cost_usd

        # 预期利润
        expected_profit = expected_value - total_cost

        # 判断是否有机会
        has_opportunity = (
            price_gap >= self.min_price_gap and
            expected_profit > 0 and
            self.account.current_balance >= self.position_size and
            current_ask > 0
        )

        return {
            'has_opportunity': has_opportunity,
            'current_ask': current_ask,
            'target_price': self.target_price,
            'price_gap': price_gap,
            'price_gap_pct': (price_gap / self.target_price * 100) if self.target_price > 0 else 0,
            'shares_acquired': shares_acquired,
            'expected_value': expected_value,
            'total_cost': total_cost,
            'gas_cost': gas_cost_usd,
            'expected_profit': expected_profit
        }

    # ============================================================
    # 风控检查 (Risk Control Checks)
    # ============================================================

    def check_risk_controls(self, trade_amount: float) -> Tuple[bool, str]:
        """
        双重风控检查

        防线 A: 最大持仓限制
        防线 B: 交易冷却时间

        Args:
            trade_amount: 本次交易金额

        Returns:
            (can_trade, reason): 是否可以交易，以及原因
        """
        current_time = time.time()

        # ===== 防线 A: 最大持仓检查 =====
        projected_position = self.current_position_usdc + trade_amount
        if projected_position > self.max_position_usdc:
            remaining = self.max_position_usdc - self.current_position_usdc
            return False, f"MAX_POSITION|${self.current_position_usdc:.0f}/${self.max_position_usdc:.0f}|剩余${remaining:.0f}"

        # ===== 防线 B: 冷却时间检查 =====
        if self.last_trade_time > 0:
            elapsed = current_time - self.last_trade_time
            if elapsed < self.cooldown_seconds:
                remaining_cooldown = int(self.cooldown_seconds - elapsed)
                return False, f"COOLDOWN|{remaining_cooldown}s|等待冷却"

        return True, "CLEAR"

    def get_risk_status(self) -> Dict:
        """
        获取当前风控状态

        Returns:
            Dict: 风控状态信息
        """
        current_time = time.time()

        # 持仓状态
        position_pct = (self.current_position_usdc / self.max_position_usdc * 100) if self.max_position_usdc > 0 else 0
        position_full = self.current_position_usdc >= self.max_position_usdc

        # 冷却状态
        if self.last_trade_time > 0:
            elapsed = current_time - self.last_trade_time
            cooldown_remaining = max(0, self.cooldown_seconds - elapsed)
            in_cooldown = cooldown_remaining > 0
        else:
            cooldown_remaining = 0
            in_cooldown = False

        return {
            'current_position': self.current_position_usdc,
            'max_position': self.max_position_usdc,
            'position_pct': position_pct,
            'position_full': position_full,
            'cooldown_remaining': int(cooldown_remaining),
            'in_cooldown': in_cooldown,
            'can_trade': not position_full and not in_cooldown
        }

    def execute_snipe(self, opportunity: Dict, market_data: Dict) -> TradeRecord:
        """
        执行狙击交易

        Args:
            opportunity: 机会计算结果
            market_data: 市场数据

        Returns:
            TradeRecord: 交易记录
        """
        # 调用交易执行器
        tx_result = self.executor.execute_buy(
            market_id=self.market_id,
            outcome_index=0,  # YES
            amount_usdc=self.position_size,
            min_shares=opportunity['shares_acquired'] * 0.95  # 5% 滑点容忍
        )

        # 创建交易记录
        record = TradeRecord(
            timestamp=datetime.now(),
            action="BUY",
            price=opportunity['current_ask'],
            target_price=self.target_price,
            price_gap=opportunity['price_gap'],
            amount_usdc=self.position_size,
            shares_acquired=opportunity['shares_acquired'],
            gas_cost=opportunity['gas_cost'],
            tx_hash=tx_result.tx_hash or "N/A"
        )

        # 更新账户
        self.account.total_trades += 1
        self.account.current_balance -= (self.position_size + opportunity['gas_cost'])
        self.account.total_spent += self.position_size
        self.account.total_gas_spent += opportunity['gas_cost']
        self.account.total_shares += opportunity['shares_acquired']

        # 更新平均买入价格
        if self.account.total_shares > 0:
            self.account.avg_buy_price = self.account.total_spent / self.account.total_shares

        self.account.trade_history.append(record)
        self.opportunities_found += 1

        return record

    def print_dashboard(self, market_data: Dict, gas_price: float, opportunity: Dict):
        """打印实时仪表盘"""
        print("\n" + "=" * 70)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        runtime = ""
        if self.start_time:
            elapsed = (datetime.now() - self.start_time).total_seconds()
            runtime = f" | Runtime: {elapsed/60:.1f} min"

        mode_str = "🔴 LIVE" if self.execution_mode == ExecutionMode.LIVE else "⏸️ DRY RUN"
        print(f"{Colors.CYAN}🎯 SNIPER MODE DASHBOARD [{mode_str}]{Colors.RESET}")
        print(f"   ⏰ {now}{runtime}")
        print("=" * 70)

        # 市场信息
        question_display = self.market_question[:50] + "..." if len(self.market_question) > 50 else self.market_question
        print(f"   📈 Market: {question_display}")
        print(f"   🆔 ID: {self.market_id[:30]}...")
        print("-" * 70)

        # 价格分析 (核心区域)
        current_ask = market_data['ask']
        target = self.target_price
        gap = opportunity['price_gap']
        gap_pct = opportunity['price_gap_pct']

        # 颜色标记价差
        if gap >= self.min_price_gap:
            gap_color = Colors.GREEN
            gap_status = "🟢 OPPORTUNITY!"
        elif gap > 0:
            gap_color = Colors.YELLOW
            gap_status = "🟡 Close..."
        else:
            gap_color = Colors.RED
            gap_status = "🔴 Too Expensive"

        print(f"   💰 Current Ask (Buy Price):  ${current_ask:.4f}")
        print(f"   🎯 Target Price (My Value):  ${target:.4f}")
        print(f"   {gap_color}📊 Price Gap (Target - Ask):  {gap:+.4f} ({gap_pct:+.1f}%) {gap_status}{Colors.RESET}")
        print(f"   ⛽ Gas Price: {gas_price:.1f} Gwei → ${opportunity['gas_cost']:.3f}")
        print("-" * 70)

        # 交易预估
        print(f"   📋 If Triggered (${self.position_size:.0f} trade):")
        print(f"      Shares Acquired:  {opportunity['shares_acquired']:.2f}")
        print(f"      Expected Value:   ${opportunity['expected_value']:.2f} (at target)")
        print(f"      Expected Profit:  ${opportunity['expected_profit']:.2f}")
        print("-" * 70)

        # 账户状态
        print(f"   📊 Account Status:")
        print(f"      Balance:       ${self.account.current_balance:,.2f}")
        print(f"      Total Shares:  {self.account.total_shares:.2f}")
        print(f"      Avg Buy Price: ${self.account.avg_buy_price:.4f}")
        print(f"      Total Spent:   ${self.account.total_spent:.2f}")
        print(f"      Gas Spent:     ${self.account.total_gas_spent:.2f}")
        print("-" * 70)

        # 风控状态
        risk_status = self.get_risk_status()
        position_bar = f"${risk_status['current_position']:.0f}/${risk_status['max_position']:.0f}"
        position_pct = risk_status['position_pct']

        if risk_status['position_full']:
            pos_color = Colors.RED
            pos_indicator = "🔴 FULL"
        elif position_pct > 70:
            pos_color = Colors.YELLOW
            pos_indicator = "🟡 HIGH"
        else:
            pos_color = Colors.GREEN
            pos_indicator = "🟢 OK"

        print(f"   🛡️ Risk Control:")
        print(f"      {pos_color}Position:    {position_bar} ({position_pct:.0f}%) {pos_indicator}{Colors.RESET}")

        if risk_status['in_cooldown']:
            print(f"      {Colors.YELLOW}Cooldown:    {risk_status['cooldown_remaining']}s remaining ⏳{Colors.RESET}")
        else:
            print(f"      {Colors.GREEN}Cooldown:    Ready ✅{Colors.RESET}")

        print(f"      Can Trade:   {'✅ YES' if risk_status['can_trade'] else '❌ NO'}")
        print("-" * 70)

        # 交易统计
        print(f"   📈 Session Stats:")
        print(f"      Ticks:     {self.ticks}")
        print(f"      Trades:    {self.account.total_trades}")
        print("=" * 70)

    def run(self, duration_minutes: int = 60, interval_seconds: int = 3):
        """
        运行狙击监控

        Args:
            duration_minutes: 运行时长 (分钟)
            interval_seconds: 检查间隔 (秒)
        """
        print("\n" + "=" * 70)
        mode_str = "🔴 LIVE MODE" if self.execution_mode == ExecutionMode.LIVE else "⏸️ DRY RUN MODE"
        print(f"🎯 SNIPER MODE - {mode_str}")
        print("=" * 70)
        print(f"   Market ID:      {self.market_id}")
        print(f"   Question:       {self.market_question[:50]}...")
        print(f"   Target Price:   ${self.target_price:.4f} (我认为它值这个价)")
        print(f"   Min Price Gap:  {self.min_price_gap*100:.1f}% (触发门槛)")
        print(f"   Position Size:  ${self.position_size}")
        print(f"   Duration:       {duration_minutes} minutes")
        print(f"   Interval:       {interval_seconds} seconds")
        print("-" * 70)
        print("   Strategy: 当 Ask Price < Target Price 时触发买入")
        print("   Press Ctrl+C to stop")
        print("=" * 70)

        # 连接服务
        print("\n📡 Connecting to services...")
        self.connect()

        self.start_time = datetime.now()
        end_time = time.time() + (duration_minutes * 60)
        consecutive_failures = 0
        MAX_CONSECUTIVE_FAILURES = 10

        try:
            while time.time() < end_time:
                loop_start = time.time()
                self.ticks += 1

                # 1. 获取市场数据
                raw_data = self.fetch_market_data()
                if not raw_data:
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        print_red(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ 连续 {consecutive_failures} 次获取数据失败")
                    else:
                        print_yellow(f"[{datetime.now().strftime('%H:%M:%S')}] ⏳ 获取数据失败，重试中... ({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})")
                    time.sleep(interval_seconds)
                    continue

                consecutive_failures = 0
                market_data = self.parse_market_data(raw_data)

                # 2. 获取 Gas Price
                gas_price = self.get_current_gas_price()
                gas_cost_usd = self.calculate_gas_cost_usd(gas_price)

                # 3. 计算狙击机会
                opportunity = self.calculate_opportunity(
                    current_ask=market_data['ask'],
                    gas_cost_usd=gas_cost_usd
                )

                # 4. 打印仪表盘
                self.print_dashboard(market_data, gas_price, opportunity)

                # 5. 决策
                if opportunity['has_opportunity']:
                    # ===== 风控检查 =====
                    can_trade, risk_reason = self.check_risk_controls(self.position_size)

                    if not can_trade:
                        # 风控阻止交易
                        print_yellow(f"\n🛡️ [RISK BLOCKED] {risk_reason}")
                    else:
                        # 触发狙击!
                        record = self.execute_snipe(opportunity, market_data)

                        # ===== 更新风控状态 =====
                        self.last_trade_time = time.time()
                        self.current_position_usdc += self.position_size

                        print_green(f"\n🎯 [SNIPE TRIGGERED!]")
                        print_green(f"   Price Gap: {opportunity['price_gap_pct']:+.1f}%")
                        print_green(f"   Bought {record.shares_acquired:.2f} shares @ ${record.price:.4f}")
                        print_green(f"   Expected Profit: ${opportunity['expected_profit']:.2f}")
                        print_green(f"   Tx Hash: {record.tx_hash}")
                        print_green(f"   Position: ${self.current_position_usdc:.0f}/${self.max_position_usdc:.0f}")
                else:
                    # 等待时机
                    reason = ""
                    if opportunity['price_gap'] < self.min_price_gap:
                        reason = f"Price Gap {opportunity['price_gap_pct']:+.1f}% < {self.min_price_gap*100:.1f}%"
                    elif opportunity['expected_profit'] <= 0:
                        reason = "Expected profit negative"
                    elif self.account.current_balance < self.position_size:
                        reason = "Insufficient balance"

                    print_gray(f"\n💤 [Waiting] {reason}")

                # 等待
                elapsed = time.time() - loop_start
                sleep_time = max(0, interval_seconds - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            print("\n\n⏹️ Sniper stopped by user")

        # 打印最终报告
        self.print_final_report()

    def print_final_report(self):
        """打印最终报告"""
        runtime = 0
        if self.start_time:
            runtime = (datetime.now() - self.start_time).total_seconds() / 60

        print("\n" + "=" * 70)
        print("📊 SNIPER SESSION - FINAL REPORT")
        print("=" * 70)
        print(f"   Runtime:           {runtime:.1f} minutes")
        print(f"   Total Ticks:       {self.ticks}")
        print(f"   Target Price:      ${self.target_price:.4f}")
        print("-" * 70)
        print(f"   Initial Balance:   ${self.account.initial_balance:,.2f}")
        print(f"   Final Balance:     ${self.account.current_balance:,.2f}")
        print(f"   Total Spent:       ${self.account.total_spent:.2f}")
        print(f"   Total Gas:         ${self.account.total_gas_spent:.2f}")
        print("-" * 70)
        print(f"   Total Trades:      {self.account.total_trades}")
        print(f"   Total Shares:      {self.account.total_shares:.2f}")
        print(f"   Avg Buy Price:     ${self.account.avg_buy_price:.4f}")

        # 计算潜在盈亏 (如果以目标价卖出)
        if self.account.total_shares > 0:
            potential_value = self.account.total_shares * self.target_price
            potential_profit = potential_value - self.account.total_spent - self.account.total_gas_spent
            print(f"\n   💰 Potential Value (at target): ${potential_value:.2f}")
            print(f"   💰 Potential Profit:            ${potential_profit:.2f}")

        print("=" * 70)

        # 最近交易
        if self.account.trade_history:
            print("\n📜 Recent Trades (Last 5):")
            print("-" * 70)
            for trade in self.account.trade_history[-5:]:
                ts = trade.timestamp.strftime("%H:%M:%S")
                print(f"   [{ts}] {trade.action} | Price: ${trade.price:.4f} | Gap: {trade.price_gap:+.4f} | Shares: {trade.shares_acquired:.2f}")
            print("-" * 70)


# ============================================================
# 市场选择
# ============================================================
def select_market() -> Optional[MarketInfo]:
    """扫描并选择市场"""
    print("\n" + "=" * 70)
    print("🔍 Market Scanner - Select a market to snipe")
    print("=" * 70)

    scanner = MarketScanner(
        max_spread=0.05,
        min_volume=10000,
        min_price=0.25,
        max_price=0.75
    )

    print("\n📡 Scanning for active markets...")
    markets = scanner.scan_top_markets(limit=15)

    if not markets:
        print("❌ No suitable markets found")
        return None

    display_markets = markets[:10]

    print("\n" + "=" * 100)
    print("📊 TOP ACTIVE MARKETS")
    print("=" * 100)
    print(f"{'#':<4} {'Question':<50} {'Price':>8} {'Volume':>12} {'Spread':>8}")
    print("-" * 100)

    for idx, m in enumerate(display_markets, 1):
        mid_price = (m.best_bid + m.best_ask) / 2
        question = m.question[:47] + "..." if len(m.question) > 50 else m.question
        print(f"{idx:<4} {question:<50} {mid_price:>7.1%} {f'${m.volume:,.0f}':>12} {m.spread:>8.4f}")

    print("-" * 100)
    print("=" * 100)

    while True:
        user_input = input("\n请选择市场序号 (或 'q' 退出): ").strip()

        if user_input.lower() in ('q', 'quit', 'exit'):
            return None

        try:
            selection = int(user_input)
            if 1 <= selection <= len(display_markets):
                return display_markets[selection - 1]
            else:
                print(f"❌ 请输入 1-{len(display_markets)} 之间的数字")
        except ValueError:
            print("❌ 无效输入")


# ============================================================
# 主程序
# ============================================================
def main():
    """主入口"""
    print("\n" + "=" * 70)
    print("🎯 Arbitrage Bot V6.0 - SNIPER MODE")
    print("=" * 70)
    print("   This mode monitors prices and triggers buys when")
    print("   the market price drops below your target price.")
    print("   (Taker Logic: Buy low, believe it's worth more)")
    print("=" * 70)

    # 1. 钱包检查
    print("\n📡 Checking Web3 connection...")
    wallet_manager = WalletManager()
    if wallet_manager.connect():
        chain_id = wallet_manager.get_chain_id()
        block = wallet_manager.get_current_block()
        print_green(f"✅ Connected to Polygon (Chain ID: {chain_id}, Block: {block:,})")
    else:
        print_yellow("⚠️ Web3 connection failed - Will use default gas prices")

    # 2. 选择市场
    market = select_market()
    if not market:
        print("\n👋 Exiting...")
        return

    print(f"\n✅ Selected: {market.question}")

    # 显示当前价格
    mid_price = (market.best_bid + market.best_ask) / 2
    print(f"   Current Price: {mid_price:.1%} (Bid: {market.best_bid:.4f}, Ask: {market.best_ask:.4f})")

    # 3. 配置参数
    print("\n" + "-" * 50)
    print("⚙️ Sniper Configuration")
    print("-" * 50)

    # 目标价格
    try:
        default_target = market.best_ask * 0.95  # 默认比当前价低 5%
        target_input = input(f"   目标价格 (我认为它值多少, 默认={default_target:.4f}): ").strip()
        target_price = float(target_input) if target_input else default_target
    except ValueError:
        target_price = default_target

    # 最小价差
    try:
        gap_input = input("   最小触发价差 % (默认=2): ").strip()
        min_gap = float(gap_input) / 100 if gap_input else 0.02
    except ValueError:
        min_gap = 0.02

    # 每笔交易金额
    try:
        size_input = input("   每笔交易金额 $ (默认=50): ").strip()
        position_size = float(size_input) if size_input else 50.0
    except ValueError:
        position_size = 50.0

    # 初始资金
    try:
        balance_input = input("   初始虚拟资金 $ (默认=10000): ").strip()
        initial_balance = float(balance_input) if balance_input else 10000.0
    except ValueError:
        initial_balance = 10000.0

    # 运行时长
    try:
        duration_input = input("   运行时长 (分钟, 默认=30): ").strip()
        duration = int(duration_input) if duration_input else 30
    except ValueError:
        duration = 30

    print("-" * 50)
    print(f"   目标价格:    ${target_price:.4f}")
    print(f"   最小价差:    {min_gap*100:.1f}%")
    print(f"   每笔金额:    ${position_size:.2f}")
    print(f"   初始资金:    ${initial_balance:,.2f}")
    print(f"   运行时长:    {duration} 分钟")
    print("-" * 50)

    # 显示策略逻辑
    trigger_price = target_price * (1 - min_gap)
    print(f"\n   📋 策略说明:")
    print(f"      当 Ask Price < ${trigger_price:.4f} 时触发买入")
    print(f"      (即价格比目标低 {min_gap*100:.1f}% 以上)")

    confirm = input("\n开始 Sniper 监控? (y/n): ").strip().lower()
    if confirm not in ('y', 'yes'):
        print("已取消")
        return

    # 4. 启动狙击引擎
    engine = SniperTradingEngine(
        market_id=market.market_id,
        market_question=market.question,
        target_price=target_price,
        initial_balance=initial_balance,
        position_size=position_size,
        min_price_gap=min_gap,
        execution_mode=ExecutionMode.DRY_RUN  # 默认 Dry Run
    )

    engine.run(duration_minutes=duration, interval_seconds=3)

    print("\n✅ Sniper session completed!")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Exiting...")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
