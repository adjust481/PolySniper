#!/usr/bin/env python3
"""
trade_executor.py - 交易执行器模块

负责与区块链交互，执行真实交易。
当前版本为模拟模式 (Dry Run)，打印交易结构但不发送真实交易。

Usage:
    from trade_executor import TradeExecutor
    executor = TradeExecutor()
    executor.execute_buy(market_id, outcome_index=0, amount_usdc=100)
"""

import os
import json
from datetime import datetime
from typing import Optional, Dict, Tuple
from dataclasses import dataclass
from enum import Enum

from dotenv import load_dotenv
from web3 import Web3
from core import WalletManager, logger

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


def print_cyan(msg: str):
    print(f"{Colors.CYAN}{msg}{Colors.RESET}")


# ============================================================
# 交易模式枚举
# ============================================================
class ExecutionMode(Enum):
    DRY_RUN = "dry_run"      # 模拟模式：只打印，不发送
    LIVE = "live"            # 实盘模式：发送真实交易


# ============================================================
# 交易结果数据类
# ============================================================
@dataclass
class TxResult:
    """交易执行结果"""
    success: bool
    tx_hash: Optional[str] = None
    gas_used: int = 0
    gas_price_gwei: float = 0.0
    error_message: Optional[str] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


# ============================================================
# 合约地址常量 (Polygon Mainnet)
# ============================================================
class ContractAddresses:
    """Polygon 链上相关合约地址"""
    # USDC.e (Bridged USDC from Ethereum)
    USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

    # Polymarket CTF Exchange (Conditional Token Framework)
    # 注意：这是示例地址，需要替换为实际的 Polymarket 合约
    POLYMARKET_CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

    # Polymarket Neg Risk CTF Exchange
    POLYMARKET_NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"


# ============================================================
# ERC20 最小 ABI
# ============================================================
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"}
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    }
]


# ============================================================
# CTF Exchange 最小 ABI (Polymarket Conditional Token Framework)
# ============================================================
CTF_EXCHANGE_ABI = [
    {
        "inputs": [
            {"name": "conditionId", "type": "bytes32"},
            {"name": "amount", "type": "uint256"},
            {"name": "minShares", "type": "uint256"}
        ],
        "name": "buy",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"name": "conditionId", "type": "bytes32"},
            {"name": "shares", "type": "uint256"},
            {"name": "minAmount", "type": "uint256"}
        ],
        "name": "sell",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"name": "user", "type": "address"},
            {"name": "conditionId", "type": "bytes32"}
        ],
        "name": "getBalance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]


# ============================================================
# TradeExecutor - 交易执行器
# ============================================================
class TradeExecutor:
    """
    交易执行器 - 负责与区块链交互

    功能:
    - 检查/执行 Token 授权
    - 构建并发送买入交易
    - 构建并发送卖出交易
    - 支持 Dry Run 模式 (模拟)

    当前版本: Dry Run 模式，只打印交易结构
    """

    def __init__(
        self,
        mode: ExecutionMode = ExecutionMode.DRY_RUN,
        wallet_manager: WalletManager = None
    ):
        """
        初始化 TradeExecutor

        Args:
            mode: 执行模式 (DRY_RUN 或 LIVE)
            wallet_manager: WalletManager 实例，如果不传则自动创建
        """
        self.mode = mode
        self.wallet_manager = wallet_manager or WalletManager()
        self._connected = False
        self._wallet_address: Optional[str] = None

        # 从环境变量读取钱包地址
        self._wallet_address = os.getenv("MY_WALLET_ADDRESS") or os.getenv("WALLET_ADDRESS")

        # 合约实例 (连接后初始化)
        self.usdc_contract = None
        self.ctf_contract = None

        # 交易统计
        self.tx_count = 0
        self.total_gas_spent = 0.0

    def connect(self) -> bool:
        """
        连接到区块链网络

        Returns:
            bool: 连接是否成功
        """
        if self.wallet_manager.connect():
            self._connected = True
            chain_id = self.wallet_manager.get_chain_id()
            block = self.wallet_manager.get_current_block()

            # 初始化合约实例
            w3 = self.wallet_manager.w3
            self.usdc_contract = w3.eth.contract(
                address=Web3.to_checksum_address(ContractAddresses.USDC),
                abi=ERC20_ABI
            )
            self.ctf_contract = w3.eth.contract(
                address=Web3.to_checksum_address(ContractAddresses.POLYMARKET_CTF_EXCHANGE),
                abi=CTF_EXCHANGE_ABI
            )

            print_cyan(f"🔗 [EXECUTOR] Connected to Polygon (Chain: {chain_id}, Block: {block:,})")
            print_cyan(f"   USDC Contract: {ContractAddresses.USDC[:10]}...{ContractAddresses.USDC[-6:]}")
            print_cyan(f"   CTF Exchange:  {ContractAddresses.POLYMARKET_CTF_EXCHANGE[:10]}...{ContractAddresses.POLYMARKET_CTF_EXCHANGE[-6:]}")

            if self.mode == ExecutionMode.DRY_RUN:
                print_yellow("⚠️  [EXECUTOR] Running in DRY RUN mode - No real transactions will be sent")
            else:
                print_green("🔴 [EXECUTOR] Running in LIVE mode - Real transactions enabled!")

            return True
        else:
            print_red("❌ [EXECUTOR] Failed to connect to blockchain")
            return False

    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._connected and self.wallet_manager.is_connected()

    def _get_private_key(self) -> Optional[str]:
        """
        安全获取私钥

        优先级:
        1. 环境变量 PRIVATE_KEY
        2. 环境变量 WALLET_PRIVATE_KEY

        Returns:
            str: 私钥 (带或不带 0x 前缀都可以)
            None: 如果未配置
        """
        private_key = os.getenv("PRIVATE_KEY") or os.getenv("WALLET_PRIVATE_KEY")

        if not private_key:
            print_red("❌ [EXECUTOR] Private key not found in environment variables")
            print_red("   Please set PRIVATE_KEY or WALLET_PRIVATE_KEY in .env file")
            return None

        # 确保有 0x 前缀
        if not private_key.startswith("0x"):
            private_key = "0x" + private_key

        return private_key

    def _sign_and_send_transaction(
        self,
        tx: dict,
        tx_type: str = "Transaction"
    ) -> TxResult:
        """
        签名并发送交易的通用方法

        Args:
            tx: 构建好的交易字典 (包含 from, to, gas, gasPrice, nonce, data 等)
            tx_type: 交易类型描述 (用于日志)

        Returns:
            TxResult: 交易结果
        """
        w3 = self.wallet_manager.w3
        gas_price_gwei = tx.get('gasPrice', 0) / 1e9

        # 1. 获取私钥
        private_key = self._get_private_key()
        if not private_key:
            return TxResult(
                success=False,
                error_message="Private key not configured",
                gas_price_gwei=gas_price_gwei
            )

        try:
            # 2. 签名交易
            print_yellow(f"   🔐 Signing {tx_type}...")
            signed_tx = w3.eth.account.sign_transaction(tx, private_key)

            # 3. 发送交易
            print_yellow(f"   📤 Broadcasting {tx_type} to network...")
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            tx_hash_hex = tx_hash.hex()

            print_cyan(f"   📝 Tx Hash: {tx_hash_hex}")
            print_yellow(f"   ⏳ Waiting for confirmation (timeout: 120s)...")

            # 4. 等待回执
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            # 5. 检查交易状态
            gas_used = receipt.get('gasUsed', 0)
            status = receipt.get('status', 0)

            if status == 1:
                # 交易成功
                print_green(f"   ✅ {tx_type} CONFIRMED!")
                print_green(f"   📦 Block:    {receipt.get('blockNumber', 'N/A')}")
                print_green(f"   ⛽ Gas Used: {gas_used:,}")

                self.total_gas_spent += (gas_used * gas_price_gwei / 1e9) * 0.50  # MATIC price

                return TxResult(
                    success=True,
                    tx_hash=tx_hash_hex,
                    gas_used=gas_used,
                    gas_price_gwei=gas_price_gwei
                )
            else:
                # 交易失败 (reverted)
                print_red(f"   ❌ {tx_type} REVERTED on-chain!")
                print_red(f"   📦 Block:    {receipt.get('blockNumber', 'N/A')}")
                print_red(f"   ⛽ Gas Used: {gas_used:,} (wasted)")

                return TxResult(
                    success=False,
                    tx_hash=tx_hash_hex,
                    gas_used=gas_used,
                    gas_price_gwei=gas_price_gwei,
                    error_message="Transaction reverted on-chain"
                )

        except Exception as e:
            error_msg = str(e)

            # 解析常见错误
            if "insufficient funds" in error_msg.lower():
                print_red(f"   ❌ Insufficient funds for gas!")
            elif "nonce too low" in error_msg.lower():
                print_red(f"   ❌ Nonce too low - transaction may have been replaced")
            elif "replacement transaction underpriced" in error_msg.lower():
                print_red(f"   ❌ Gas price too low to replace pending transaction")
            elif "timeout" in error_msg.lower():
                print_red(f"   ❌ Transaction confirmation timeout (120s)")
            else:
                print_red(f"   ❌ {tx_type} failed: {error_msg}")

            return TxResult(
                success=False,
                error_message=error_msg,
                gas_price_gwei=gas_price_gwei
            )

    # ============================================================
    # Token 授权相关
    # ============================================================

    def check_usdc_allowance(self) -> Tuple[float, bool]:
        """
        检查 USDC 对 CTF Exchange 的授权额度 (便捷方法)

        Returns:
            (allowance_amount, is_sufficient): 授权金额和是否充足
        """
        return self.check_allowance(
            token_address=ContractAddresses.USDC,
            spender_address=ContractAddresses.POLYMARKET_CTF_EXCHANGE
        )

    def check_allowance(
        self,
        token_address: str = None,
        spender_address: str = None
    ) -> Tuple[float, bool]:
        """
        检查 Token 授权额度

        Args:
            token_address: Token 合约地址 (默认 USDC)
            spender_address: 被授权的合约地址 (默认 CTF Exchange)

        Returns:
            (allowance_amount, is_sufficient): 授权金额和是否充足
        """
        # 使用默认值
        token_address = token_address or ContractAddresses.USDC
        spender_address = spender_address or ContractAddresses.POLYMARKET_CTF_EXCHANGE

        if not self.is_connected():
            print_red("❌ [EXECUTOR] Not connected to blockchain")
            return 0.0, False

        try:
            w3 = self.wallet_manager.w3

            # 使用已初始化的合约或创建新实例
            if token_address == ContractAddresses.USDC and self.usdc_contract:
                token_contract = self.usdc_contract
            else:
                token_contract = w3.eth.contract(
                    address=Web3.to_checksum_address(token_address),
                    abi=ERC20_ABI
                )

            # 获取 decimals
            decimals = token_contract.functions.decimals().call()

            # 获取 allowance
            owner = Web3.to_checksum_address(self._wallet_address)
            spender = Web3.to_checksum_address(spender_address)

            allowance_raw = token_contract.functions.allowance(owner, spender).call()
            allowance = allowance_raw / (10 ** decimals)

            # 判断是否充足 (大于 $1000 视为充足)
            is_sufficient = allowance > 1000

            print_cyan(f"📋 [EXECUTOR] Allowance Check:")
            print(f"   Token:     {token_address[:10]}...{token_address[-6:]}")
            print(f"   Spender:   {spender_address[:10]}...{spender_address[-6:]}")
            print(f"   Allowance: ${allowance:,.2f}")
            print(f"   Status:    {'✅ Sufficient' if is_sufficient else '⚠️ Need Approval'}")

            return allowance, is_sufficient

        except Exception as e:
            print_red(f"❌ [EXECUTOR] Allowance check failed: {e}")
            return 0.0, False

    def approve_token(
        self,
        token_address: str,
        spender_address: str,
        amount: float = None  # None = 无限授权
    ) -> TxResult:
        """
        执行 Token 授权

        Args:
            token_address: Token 合约地址
            spender_address: 被授权的合约地址
            amount: 授权金额 (None = 无限授权)

        Returns:
            TxResult: 交易结果
        """
        if not self.is_connected():
            return TxResult(success=False, error_message="Not connected")

        try:
            w3 = self.wallet_manager.w3

            # 创建合约实例
            token_contract = w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=ERC20_ABI
            )

            # 获取 decimals
            decimals = token_contract.functions.decimals().call()

            # 计算授权金额
            if amount is None:
                # 无限授权 (2^256 - 1)
                approve_amount = 2**256 - 1
                amount_display = "Unlimited"
            else:
                approve_amount = int(amount * (10 ** decimals))
                amount_display = f"${amount:,.2f}"

            # 构建交易
            owner = Web3.to_checksum_address(self._wallet_address)
            spender = Web3.to_checksum_address(spender_address)

            # 估算 Gas
            gas_price = w3.eth.gas_price
            gas_price_gwei = gas_price / 1e9

            tx = token_contract.functions.approve(spender, approve_amount).build_transaction({
                'from': owner,
                'gas': 100000,
                'gasPrice': gas_price,
                'nonce': w3.eth.get_transaction_count(owner),
            })

            print_yellow(f"\n📝 [EXECUTOR] Approve Transaction:")
            print(f"   Token:      {token_address[:10]}...{token_address[-6:]}")
            print(f"   Spender:    {spender_address[:10]}...{spender_address[-6:]}")
            print(f"   Amount:     {amount_display}")
            print(f"   Gas Price:  {gas_price_gwei:.2f} Gwei")
            print(f"   Gas Limit:  {tx['gas']:,}")
            print(f"   Nonce:      {tx['nonce']}")

            if self.mode == ExecutionMode.DRY_RUN:
                print_yellow("   ⏸️  DRY RUN - Transaction NOT sent")
                return TxResult(
                    success=True,
                    tx_hash="0x_DRY_RUN_" + datetime.now().strftime("%H%M%S"),
                    gas_price_gwei=gas_price_gwei
                )
            else:
                # LIVE 模式 - 签名并发送交易
                print_green("   🔴 LIVE MODE - Sending real approval transaction...")
                return self._sign_and_send_transaction(tx, "Approve")

        except Exception as e:
            print_red(f"❌ [EXECUTOR] Approve failed: {e}")
            return TxResult(success=False, error_message=str(e))

    # ============================================================
    # 交易执行
    # ============================================================

    def execute_buy(
        self,
        market_id: str,
        outcome_index: int,
        amount_usdc: float,
        min_shares: float = 0
    ) -> TxResult:
        """
        执行买入交易

        Args:
            market_id: 市场 ID (Condition ID)
            outcome_index: 结果索引 (0=Yes, 1=No)
            amount_usdc: 买入金额 (USDC)
            min_shares: 最小获得的份额数 (滑点保护)

        Returns:
            TxResult: 交易结果
        """
        self.tx_count += 1
        outcome_str = "YES" if outcome_index == 0 else "NO"

        print_green(f"\n🚀 [EXECUTOR] Preparing Buy Tx | Market: {market_id[:20]}... | Amount: ${amount_usdc:.2f}")

        if not self.is_connected():
            print_red("❌ [EXECUTOR] Not connected to blockchain")
            return TxResult(success=False, error_message="Not connected")

        try:
            w3 = self.wallet_manager.w3

            # 获取当前 Gas Price
            gas_price = w3.eth.gas_price
            gas_price_gwei = gas_price / 1e9

            # 构建交易参数 (模拟)
            tx_params = {
                'type': 'BUY',
                'market_id': market_id,
                'outcome': outcome_str,
                'outcome_index': outcome_index,
                'amount_usdc': amount_usdc,
                'min_shares': min_shares,
                'from': self._wallet_address,
                'to': ContractAddresses.POLYMARKET_CTF_EXCHANGE,
                'gas_limit': 300000,
                'gas_price_gwei': gas_price_gwei,
                'nonce': w3.eth.get_transaction_count(Web3.to_checksum_address(self._wallet_address)),
                'timestamp': datetime.now().isoformat()
            }

            # 估算 Gas 费用 (USD)
            # 假设 MATIC 价格 $0.50
            matic_price = 0.50
            estimated_gas_cost = (tx_params['gas_limit'] * gas_price_gwei / 1e9) * matic_price

            print_cyan(f"\n📋 [EXECUTOR] Buy Transaction Details:")
            print(f"   ┌─────────────────────────────────────────────")
            print(f"   │ Type:        {tx_params['type']}")
            print(f"   │ Market:      {market_id[:30]}...")
            print(f"   │ Outcome:     {outcome_str} (index: {outcome_index})")
            print(f"   │ Amount:      ${amount_usdc:.2f} USDC")
            print(f"   │ Min Shares:  {min_shares:.4f}")
            print(f"   ├─────────────────────────────────────────────")
            print(f"   │ From:        {self._wallet_address[:10]}...{self._wallet_address[-6:]}")
            print(f"   │ To:          {tx_params['to'][:10]}...{tx_params['to'][-6:]}")
            print(f"   │ Gas Limit:   {tx_params['gas_limit']:,}")
            print(f"   │ Gas Price:   {gas_price_gwei:.2f} Gwei")
            print(f"   │ Est. Cost:   ${estimated_gas_cost:.4f}")
            print(f"   │ Nonce:       {tx_params['nonce']}")
            print(f"   └─────────────────────────────────────────────")

            if self.mode == ExecutionMode.DRY_RUN:
                print_yellow(f"\n   ⏸️  DRY RUN MODE - Transaction NOT sent to blockchain")
                print_yellow(f"   📦 Transaction would be submitted with above parameters")

                self.total_gas_spent += estimated_gas_cost

                return TxResult(
                    success=True,
                    tx_hash=f"0xDRY_RUN_{self.tx_count:04d}_{datetime.now().strftime('%H%M%S')}",
                    gas_used=tx_params['gas_limit'],
                    gas_price_gwei=gas_price_gwei
                )
            else:
                # LIVE 模式 - 构建并发送真实交易
                print_green(f"\n   🔴 LIVE MODE - Sending real BUY transaction...")

                # 将 market_id 转换为 bytes32 conditionId
                # 注意: 这里假设 market_id 已经是有效的 hex 格式
                if market_id.startswith("0x"):
                    condition_id = bytes.fromhex(market_id[2:].zfill(64))
                else:
                    condition_id = bytes.fromhex(market_id.zfill(64))

                # USDC 有 6 位小数
                amount_raw = int(amount_usdc * 1e6)
                min_shares_raw = int(min_shares * 1e6)  # 份额也用 6 位小数

                # 构建合约调用交易
                owner = Web3.to_checksum_address(self._wallet_address)

                buy_tx = self.ctf_contract.functions.buy(
                    condition_id,
                    amount_raw,
                    min_shares_raw
                ).build_transaction({
                    'from': owner,
                    'gas': 300000,
                    'gasPrice': gas_price,
                    'nonce': w3.eth.get_transaction_count(owner),
                    'chainId': w3.eth.chain_id
                })

                return self._sign_and_send_transaction(buy_tx, "Buy")

        except Exception as e:
            print_red(f"❌ [EXECUTOR] Buy execution failed: {e}")
            return TxResult(success=False, error_message=str(e))

    def execute_sell(
        self,
        market_id: str,
        outcome_index: int,
        amount_shares: float,
        min_usdc: float = 0
    ) -> TxResult:
        """
        执行卖出交易

        Args:
            market_id: 市场 ID (Condition ID)
            outcome_index: 结果索引 (0=Yes, 1=No)
            amount_shares: 卖出份额数
            min_usdc: 最小获得的 USDC (滑点保护)

        Returns:
            TxResult: 交易结果
        """
        self.tx_count += 1
        outcome_str = "YES" if outcome_index == 0 else "NO"

        print_green(f"\n🚀 [EXECUTOR] Preparing Sell Tx | Market: {market_id[:20]}... | Shares: {amount_shares:.4f}")

        if not self.is_connected():
            print_red("❌ [EXECUTOR] Not connected to blockchain")
            return TxResult(success=False, error_message="Not connected")

        try:
            w3 = self.wallet_manager.w3

            # 获取当前 Gas Price
            gas_price = w3.eth.gas_price
            gas_price_gwei = gas_price / 1e9

            # 构建交易参数 (模拟)
            tx_params = {
                'type': 'SELL',
                'market_id': market_id,
                'outcome': outcome_str,
                'outcome_index': outcome_index,
                'amount_shares': amount_shares,
                'min_usdc': min_usdc,
                'from': self._wallet_address,
                'to': ContractAddresses.POLYMARKET_CTF_EXCHANGE,
                'gas_limit': 300000,
                'gas_price_gwei': gas_price_gwei,
                'nonce': w3.eth.get_transaction_count(Web3.to_checksum_address(self._wallet_address)),
                'timestamp': datetime.now().isoformat()
            }

            # 估算 Gas 费用 (USD)
            matic_price = 0.50
            estimated_gas_cost = (tx_params['gas_limit'] * gas_price_gwei / 1e9) * matic_price

            print_cyan(f"\n📋 [EXECUTOR] Sell Transaction Details:")
            print(f"   ┌─────────────────────────────────────────────")
            print(f"   │ Type:        {tx_params['type']}")
            print(f"   │ Market:      {market_id[:30]}...")
            print(f"   │ Outcome:     {outcome_str} (index: {outcome_index})")
            print(f"   │ Shares:      {amount_shares:.4f}")
            print(f"   │ Min USDC:    ${min_usdc:.2f}")
            print(f"   ├─────────────────────────────────────────────")
            print(f"   │ From:        {self._wallet_address[:10]}...{self._wallet_address[-6:]}")
            print(f"   │ To:          {tx_params['to'][:10]}...{tx_params['to'][-6:]}")
            print(f"   │ Gas Limit:   {tx_params['gas_limit']:,}")
            print(f"   │ Gas Price:   {gas_price_gwei:.2f} Gwei")
            print(f"   │ Est. Cost:   ${estimated_gas_cost:.4f}")
            print(f"   │ Nonce:       {tx_params['nonce']}")
            print(f"   └─────────────────────────────────────────────")

            if self.mode == ExecutionMode.DRY_RUN:
                print_yellow(f"\n   ⏸️  DRY RUN MODE - Transaction NOT sent to blockchain")

                self.total_gas_spent += estimated_gas_cost

                return TxResult(
                    success=True,
                    tx_hash=f"0xDRY_RUN_{self.tx_count:04d}_{datetime.now().strftime('%H%M%S')}",
                    gas_used=tx_params['gas_limit'],
                    gas_price_gwei=gas_price_gwei
                )
            else:
                # LIVE 模式 - 构建并发送真实交易
                print_green(f"\n   🔴 LIVE MODE - Sending real SELL transaction...")

                # 将 market_id 转换为 bytes32 conditionId
                if market_id.startswith("0x"):
                    condition_id = bytes.fromhex(market_id[2:].zfill(64))
                else:
                    condition_id = bytes.fromhex(market_id.zfill(64))

                # 份额和最小 USDC 都用 6 位小数
                shares_raw = int(amount_shares * 1e6)
                min_usdc_raw = int(min_usdc * 1e6)

                # 构建合约调用交易
                owner = Web3.to_checksum_address(self._wallet_address)

                sell_tx = self.ctf_contract.functions.sell(
                    condition_id,
                    shares_raw,
                    min_usdc_raw
                ).build_transaction({
                    'from': owner,
                    'gas': 300000,
                    'gasPrice': gas_price,
                    'nonce': w3.eth.get_transaction_count(owner),
                    'chainId': w3.eth.chain_id
                })

                return self._sign_and_send_transaction(sell_tx, "Sell")

        except Exception as e:
            print_red(f"❌ [EXECUTOR] Sell execution failed: {e}")
            return TxResult(success=False, error_message=str(e))

    # ============================================================
    # 工具方法
    # ============================================================

    def get_stats(self) -> Dict:
        """获取执行器统计"""
        return {
            'mode': self.mode.value,
            'connected': self._connected,
            'tx_count': self.tx_count,
            'total_gas_spent_usd': self.total_gas_spent
        }

    def print_status(self):
        """打印执行器状态"""
        stats = self.get_stats()

        print(f"\n{'='*50}")
        print(f"📊 Trade Executor Status")
        print(f"{'='*50}")
        print(f"   Mode:           {stats['mode'].upper()}")
        print(f"   Connected:      {'Yes' if stats['connected'] else 'No'}")
        print(f"   Transactions:   {stats['tx_count']}")
        print(f"   Gas Spent:      ${stats['total_gas_spent_usd']:.4f}")
        print(f"{'='*50}\n")


# ============================================================
# 测试入口
# ============================================================
if __name__ == "__main__":
    print("\n" + "="*60)
    print("🧪 TradeExecutor - Test Mode")
    print("="*60)

    # 创建执行器 (Dry Run 模式)
    executor = TradeExecutor(mode=ExecutionMode.DRY_RUN)

    # 连接
    if executor.connect():
        # 检查授权 (使用便捷方法)
        executor.check_usdc_allowance()

        # 模拟买入
        executor.execute_buy(
            market_id="0x1234567890abcdef1234567890abcdef12345678",
            outcome_index=0,  # YES
            amount_usdc=50.0,
            min_shares=45.0
        )

        # 模拟卖出
        executor.execute_sell(
            market_id="0x1234567890abcdef1234567890abcdef12345678",
            outcome_index=0,  # YES
            amount_shares=100.0,
            min_usdc=95.0
        )

        # 打印状态
        executor.print_status()
    else:
        print("❌ Failed to connect")
