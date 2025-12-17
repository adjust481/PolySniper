import asyncio
import logging
import os
import glob
from datetime import datetime
import pandas as pd
from dotenv import load_dotenv
from core import (
    SharedBacktestEngine,
    BacktestVisualizer,
    WalletManager,
    MarketScanner,
    DataRecorder,
    DataSource,
    logger
)

# 配置 pandas 显示选项，防止在终端里打印时换行错位
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)
pd.set_option('display.max_rows', 20)


# ============================================================
# 钱包检查
# ============================================================
def check_wallet() -> bool:
    """
    检查钱包连接状态和余额

    Returns:
        bool: 检查是否通过
    """
    print("\n" + "=" * 70)
    print("🔗 Real Wallet Check - Polygon Network")
    print("=" * 70)

    # 从环境变量获取钱包地址
    wallet_address = os.getenv("MY_WALLET_ADDRESS") or os.getenv("WALLET_ADDRESS")

    if not wallet_address:
        print("❌ Error: 钱包地址未配置")
        print("   请在 .env 文件中设置 MY_WALLET_ADDRESS")
        print("=" * 70 + "\n")
        return False

    # 初始化 WalletManager (会自动读取 POLYGON_RPC)
    wallet_manager = WalletManager()

    # 连接到网络
    print("\n📡 Connecting to Polygon Network...")
    if not wallet_manager.connect():
        print("❌ Connection Failed!")
        print("   - 检查网络连接")
        print("   - 尝试其他 RPC 节点")
        print("=" * 70 + "\n")
        return False

    print(f"✅ Connection Status: Connected")
    print(f"   RPC: {wallet_manager.rpc_url}")

    # 获取链 ID 验证
    chain_id = wallet_manager.get_chain_id()
    if chain_id == 137:
        print(f"✅ Chain ID: {chain_id} (Polygon Mainnet)")
    else:
        print(f"⚠️  Chain ID: {chain_id} (Expected: 137 for Polygon)")

    # 获取当前区块
    block_number = wallet_manager.get_current_block()
    if block_number:
        print(f"📦 Current Block: {block_number:,}")

    # 获取余额
    print(f"\n💰 Wallet: {wallet_address}")
    print("-" * 70)

    balances = wallet_manager.get_balance(wallet_address)

    matic_balance = balances["matic"]
    usdc_balance = balances["usdc"]

    print(f"   MATIC Balance: {matic_balance:.6f} MATIC")
    print(f"   USDC Balance:  {usdc_balance:.2f} USDC")

    # Gas 警告检查
    LOW_GAS_THRESHOLD = 0.1
    LOW_USDC_THRESHOLD = 10.0

    warnings = []
    if matic_balance < LOW_GAS_THRESHOLD:
        warnings.append(f"⚠️  Low Gas: MATIC={matic_balance:.6f} (建议 >= {LOW_GAS_THRESHOLD})")

    if usdc_balance < LOW_USDC_THRESHOLD:
        warnings.append(f"⚠️  Low Funds: USDC={usdc_balance:.2f} (建议 >= ${LOW_USDC_THRESHOLD})")

    if warnings:
        print("\n" + "!" * 70)
        for w in warnings:
            print(f"   {w}")
        print("!" * 70)

    print("\n" + "=" * 70)
    print("✅ Wallet check completed.")
    print("=" * 70 + "\n")

    return True


# ============================================================
# 功能 1: 扫描并录制市场
# ============================================================
def scan_and_select_market() -> str:
    """
    扫描市场并让用户选择

    Returns:
        str: 用户选择的市场 ID，如果退出返回 None
    """
    print("\n" + "=" * 70)
    print("🔍 Market Scanner - Polymarket Gamma API")
    print("=" * 70)

    # 初始化扫描器 (包含价格区间过滤)
    scanner = MarketScanner(
        max_spread=0.05,   # 最大价差 5%
        min_volume=1000,   # 最小成交量 $1000
        min_price=0.20,    # 最低价格 20% (过滤冷门市场)
        max_price=0.80     # 最高价格 80% (过滤已确定市场)
    )

    print("\n📡 正在扫描热门市场 (仅显示价格在 20%-80% 之间的活跃市场)...")

    # 扫描市场
    markets = scanner.scan_top_markets(limit=20)

    if not markets:
        print("❌ 未找到符合条件的市场")
        return None

    # 打印表格 (前10个) - 增加 Price 列
    display_markets = markets[:10]

    print("\n" + "=" * 110)
    print("📊 TOP 10 活跃市场 (价格 20%-80%，按交易量排序)")
    print("=" * 110)
    print(f"{'#':<4} {'Market ID':<20} {'Question':<35} {'Price':>8} {'Volume':>12} {'Spread':>8}")
    print("-" * 110)

    for idx, m in enumerate(display_markets, 1):
        # 计算中间价
        mid_price = (m.best_bid + m.best_ask) / 2
        # 截断长问题
        question = m.question[:32] + "..." if len(m.question) > 35 else m.question
        # 截断长 ID
        market_id_short = m.market_id[:18] + ".." if len(m.market_id) > 20 else m.market_id
        price_str = f"{mid_price:.1%}"
        volume_str = f"${m.volume:,.0f}"
        spread_str = f"{m.spread:.4f}"

        print(f"{idx:<4} {market_id_short:<20} {question:<35} {price_str:>8} {volume_str:>12} {spread_str:>8}")

    print("-" * 110)
    print(f"共找到 {len(markets)} 个符合条件的市场 (显示前10个)")
    print("=" * 110)

    # 交互选择
    print("\n📝 市场选择")
    print("-" * 40)

    while True:
        user_input = input("请输入你想监控的市场序号 (1-10) 或 'b' 返回: ").strip()

        if user_input.lower() in ('b', 'back', 'q', 'quit'):
            return None

        try:
            selection = int(user_input)
            if 1 <= selection <= len(display_markets):
                selected = display_markets[selection - 1]
                print("\n" + "=" * 70)
                print(f"✅ 已锁定市场:")
                print(f"   ID:       {selected.market_id}")
                print(f"   Question: {selected.question}")
                print(f"   Volume:   ${selected.volume:,.0f}")
                print(f"   Spread:   {selected.spread:.4f}")
                print(f"   Bid/Ask:  {selected.best_bid:.3f} / {selected.best_ask:.3f}")
                print("=" * 70)
                return selected.market_id
            else:
                print(f"❌ 请输入 1-{len(display_markets)} 之间的数字")
        except ValueError:
            print("❌ 无效输入，请输入数字或 'b' 返回")


def option_scan_and_record():
    """
    菜单选项 1: 扫描并录制市场数据
    """
    selected_market_id = scan_and_select_market()

    if not selected_market_id:
        print("\n返回主菜单...")
        return

    print(f"\n🎯 已锁定市场 ID: {selected_market_id}")

    # 询问是否开始录制
    print("\n" + "-" * 50)
    record_choice = input("是否开始录制数据? (y/n): ").strip().lower()

    if record_choice in ('y', 'yes'):
        # 询问录制时长
        try:
            duration_input = input("录制时长 (分钟, 默认=60): ").strip()
            duration = int(duration_input) if duration_input else 60
        except ValueError:
            duration = 60
            print("   无效输入，使用默认值: 60 分钟")

        # 开始录制
        recorder = DataRecorder(output_dir="data")

        try:
            csv_path = recorder.record(
                market_id=selected_market_id,
                duration_minutes=duration,
                interval_seconds=3
            )
            print(f"\n📁 数据已保存至: {csv_path}")

        except KeyboardInterrupt:
            print("\n录制已安全停止")

    else:
        print("\n📝 已跳过录制")
        print(f"   你可以稍后使用此 Market ID 进行录制:")
        print(f"   {selected_market_id}")


# ============================================================
# 功能 2: 真实数据回测
# ============================================================
def list_csv_files(data_dir: str = "data") -> list:
    """
    列出 data 目录下所有 CSV 文件

    Returns:
        list: [(filepath, filename, file_info), ...]
    """
    if not os.path.exists(data_dir):
        return []

    pattern = os.path.join(data_dir, "*.csv")
    files = glob.glob(pattern)

    # 按修改时间排序 (最新的在前)
    files.sort(key=os.path.getmtime, reverse=True)

    result = []
    for filepath in files:
        filename = os.path.basename(filepath)
        stat = os.stat(filepath)
        size_kb = stat.st_size / 1024
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")

        # 读取行数
        try:
            with open(filepath, 'r') as f:
                rows = sum(1 for _ in f) - 1  # 减去 header
        except:
            rows = 0

        result.append({
            'path': filepath,
            'name': filename,
            'size_kb': size_kb,
            'mtime': mtime,
            'rows': rows
        })

    return result


def select_csv_file() -> str:
    """
    让用户选择一个 CSV 文件

    Returns:
        str: 文件路径，如果取消返回 None
    """
    files = list_csv_files()

    if not files:
        print("\n❌ data/ 目录下没有 CSV 文件")
        print("   请先使用「扫描并录制市场」功能录制数据")
        return None

    print("\n" + "=" * 80)
    print("📂 可用的 CSV 数据文件")
    print("=" * 80)
    print(f"{'#':<4} {'文件名':<45} {'行数':>8} {'大小':>10} {'修改时间':<18}")
    print("-" * 80)

    for idx, f in enumerate(files, 1):
        print(f"{idx:<4} {f['name']:<45} {f['rows']:>8} {f['size_kb']:>8.1f}KB {f['mtime']:<18}")

    print("-" * 80)
    print(f"共找到 {len(files)} 个文件")
    print("=" * 80)

    # 用户选择
    while True:
        user_input = input("\n请选择文件序号 (或 'b' 返回): ").strip()

        if user_input.lower() in ('b', 'back', 'q'):
            return None

        try:
            selection = int(user_input)
            if 1 <= selection <= len(files):
                selected = files[selection - 1]
                print(f"\n✅ 已选择: {selected['name']}")
                return selected['path']
            else:
                print(f"❌ 请输入 1-{len(files)} 之间的数字")
        except ValueError:
            print("❌ 无效输入")


async def run_real_backtest(csv_path: str):
    """
    使用真实 CSV 数据运行回测

    Args:
        csv_path: CSV 文件路径
    """
    print("\n" + "=" * 80)
    print("🎞️ 真实数据回测 - Real Data Backtest")
    print("=" * 80)
    print(f"   数据文件: {csv_path}")

    # 读取 CSV 预览
    try:
        df_preview = pd.read_csv(csv_path)
        rows = len(df_preview)
        print(f"   数据行数: {rows}")

        if rows > 0:
            avg_bid = df_preview['best_bid'].mean()
            avg_ask = df_preview['best_ask'].mean()
            avg_spread = df_preview['spread'].mean()
            print(f"   平均 Bid:  {avg_bid:.4f}")
            print(f"   平均 Ask:  {avg_ask:.4f}")
            print(f"   平均 Spread: {avg_spread:.4f}")
    except Exception as e:
        print(f"   ⚠️ 预览失败: {e}")

    print("-" * 80)

    # 配置参数
    print("\n⚙️ 回测配置")
    try:
        profit_input = input("   最小盈利率 % (默认=0.3): ").strip()
        min_profit = float(profit_input) / 100 if profit_input else 0.003
    except ValueError:
        min_profit = 0.003

    try:
        offset_input = input("   OP价格偏移 (默认=0.02, 模拟套利空间): ").strip()
        op_offset = float(offset_input) if offset_input else 0.02
    except ValueError:
        op_offset = 0.02

    print(f"\n   最小盈利率: {min_profit*100:.2f}%")
    print(f"   OP价格偏移: {op_offset}")
    print("-" * 80)

    confirm = input("\n开始回测? (y/n): ").strip().lower()
    if confirm not in ('y', 'yes'):
        print("已取消")
        return

    # 运行回测
    print("\n🚀 正在运行回测...")

    engine = SharedBacktestEngine(
        bot_profiles=['retail', 'semi_pro', 'pro'],
        min_profit_rate=min_profit,
        data_source=DataSource.CSV,
        csv_path=csv_path,
        op_spread_offset=op_offset
    )

    results = await engine.run_backtest()

    # 打印结果
    print_backtest_report(results, engine.stats)


def print_backtest_report(results: dict, stats: dict):
    """
    打印详细的回测报告

    Args:
        results: 回测结果 {profile: (df, metrics)}
        stats: 统计数据
    """
    print("\n" + "=" * 80)
    print("📊 回测成绩单 - BACKTEST REPORT")
    print("=" * 80)

    # 总体统计
    print("\n【总体统计】")
    print("-" * 50)
    print(f"   总 Tick 数:      {stats.get('total_ticks', 0):,}")
    print(f"   无机会:          {stats.get('no_opportunity', 0):,}")
    print(f"   预检查拒绝:      {stats.get('precheck_rejected', 0):,}")
    print(f"   被抢跑:          {stats.get('frontrun', 0):,}")
    print(f"   单腿风险:        {stats.get('leg_risk', 0):,}")
    print(f"   盈利交易:        {stats.get('profitable_trades', 0):,}")

    # 各 Profile 详情
    print("\n【各配置收益详情】")
    print("=" * 80)
    print(f"{'配置':<12} {'成功率':>10} {'净收益($)':>12} {'总Gas':>10} {'总滑点':>10} {'被抢跑':>8} {'单腿风险':>8}")
    print("-" * 80)

    total_profit = 0
    for profile, (df, metrics) in results.items():
        if metrics:
            success_rate = metrics.get('成功率%', 0)
            net_profit = metrics.get('净收益', 0)
            total_gas = metrics.get('总Gas费用', 0)
            total_slip = metrics.get('总滑点成本', 0)
            frontrun = metrics.get('被抢跑次数', 0)
            leg_risk = metrics.get('单腿风险次数', 0)

            print(f"{profile.upper():<12} {success_rate:>9.1f}% {net_profit:>12.2f} {total_gas:>10.2f} {total_slip:>10.2f} {frontrun:>8} {leg_risk:>8}")
            total_profit += net_profit
        else:
            print(f"{profile.upper():<12} {'N/A':>10} {'N/A':>12} {'N/A':>10} {'N/A':>10} {'N/A':>8} {'N/A':>8}")

    print("-" * 80)
    print(f"{'合计':<12} {'':<10} {total_profit:>12.2f}")
    print("=" * 80)

    # 收益逻辑验证
    print("\n【收益逻辑验证】")
    try:
        retail_net = results.get('retail', (None, {}))[1].get('净收益', 0)
        semi_net = results.get('semi_pro', (None, {}))[1].get('净收益', 0)
        pro_net = results.get('pro', (None, {}))[1].get('净收益', 0)

        if pro_net > semi_net > retail_net:
            print("✅ 符合预期: PRO > SEMI_PRO > RETAIL (速度越快，收益越高)")
        elif pro_net >= semi_net >= retail_net:
            print("⚠️ 基本符合: PRO >= SEMI_PRO >= RETAIL")
        else:
            print(f"❌ 结果异常: PRO=${pro_net:.2f}, SEMI=${semi_net:.2f}, RETAIL=${retail_net:.2f}")
    except Exception as e:
        print(f"   验证失败: {e}")

    # 盈亏总结
    print("\n" + "=" * 80)
    if total_profit > 0:
        print(f"💰 总净收益: +${total_profit:.2f} (盈利)")
    elif total_profit < 0:
        print(f"💸 总净收益: -${abs(total_profit):.2f} (亏损)")
    else:
        print(f"⚖️ 总净收益: $0.00 (持平)")
    print("=" * 80 + "\n")


def option_run_backtest():
    """
    菜单选项 2: 运行真实数据回测
    """
    csv_path = select_csv_file()

    if not csv_path:
        print("\n返回主菜单...")
        return

    try:
        asyncio.run(run_real_backtest(csv_path))
    except KeyboardInterrupt:
        print("\n\n⏹️ 回测被用户中断")
    except Exception as e:
        print(f"\n❌ 回测失败: {e}")
        import traceback
        traceback.print_exc()


# ============================================================
# 主菜单
# ============================================================
def print_main_menu():
    """打印主菜单"""
    print("\n" + "=" * 50)
    print("🤖 Arbitrage Bot V6.0 - 主菜单")
    print("=" * 50)
    print("  1. 📡 扫描并录制市场 (Scan & Record)")
    print("  2. 🎞️ 运行真实回测 (Run Real Backtest)")
    print("  3. 🔄 模拟回测 (Synthetic Backtest)")
    print("  q. 👋 退出 (Quit)")
    print("=" * 50)


async def option_synthetic_backtest():
    """
    菜单选项 3: 运行模拟回测 (OU 过程)
    """
    print("\n" + "=" * 80)
    print("🔄 模拟回测 - Synthetic Backtest (OU Process)")
    print("=" * 80)

    # 配置
    try:
        events_input = input("   事件数量 (默认=15): ").strip()
        num_events = int(events_input) if events_input else 15
    except ValueError:
        num_events = 15

    try:
        days_input = input("   持续天数 (默认=3): ").strip()
        duration_days = int(days_input) if days_input else 3
    except ValueError:
        duration_days = 3

    print(f"\n   事件数: {num_events}, 天数: {duration_days}")

    confirm = input("\n开始回测? (y/n): ").strip().lower()
    if confirm not in ('y', 'yes'):
        print("已取消")
        return

    print("\n🚀 正在运行模拟回测...")

    engine = SharedBacktestEngine(
        bot_profiles=['retail', 'semi_pro', 'pro'],
        seed=42,
        min_profit_rate=0.003
    )

    results = await engine.run_backtest(
        num_events=num_events,
        events_per_day=5,
        duration_days=duration_days
    )

    print_backtest_report(results, engine.stats)


def main_loop():
    """
    主菜单循环
    """
    while True:
        print_main_menu()
        choice = input("请选择操作: ").strip().lower()

        if choice == '1':
            try:
                option_scan_and_record()
            except KeyboardInterrupt:
                print("\n\n⏹️ 操作被中断")

        elif choice == '2':
            try:
                option_run_backtest()
            except KeyboardInterrupt:
                print("\n\n⏹️ 操作被中断")

        elif choice == '3':
            try:
                asyncio.run(option_synthetic_backtest())
            except KeyboardInterrupt:
                print("\n\n⏹️ 操作被中断")

        elif choice in ('q', 'quit', 'exit'):
            print("\n👋 再见！")
            break

        else:
            print("\n❌ 无效选择，请输入 1, 2, 3 或 q")


# ============================================================
# 程序入口
# ============================================================
if __name__ == "__main__":
    # 1. 加载 .env 文件
    load_dotenv()

    print("\n" + "=" * 50)
    print("🤖 Arbitrage Bot V6.0")
    print("=" * 50)

    # 2. 钱包检查
    wallet_ok = check_wallet()

    if not wallet_ok:
        print("⚠️ 钱包检查失败")
        user_input = input("是否继续? (y/n): ").strip().lower()
        if user_input not in ('y', 'yes'):
            print("👋 已退出")
            exit(0)
        print("继续...\n")

    # 3. 进入主菜单循环
    try:
        main_loop()
    except KeyboardInterrupt:
        print("\n\n👋 程序已退出")
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()
