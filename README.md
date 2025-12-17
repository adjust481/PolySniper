# PolySniper: Prediction Market Arbitrage Bot 🎯

A production-grade, event-driven arbitrage bot designed for **Polymarket** on the Polygon network. Built with Python and Web3.py, featuring real-time opportunity detection, atomic execution simulation, and strict risk management protocols.

## 🚀 Key Features

* **Sniper Strategy (`paper.py`)**: Real-time monitoring of "Taker" opportunities based on user-defined valuation models.
* **Execution Engine (`trade_executor.py`)**:
    * **Modular Design**: Decoupled strategy logic from on-chain interaction.
    * **Dual Modes**: Supports safe `Dry-Run` simulation and `Live` mainnet execution.
    * **Gas Optimization**: Dynamic gas estimation compliant with EIP-1559.
* **Market Radar (`local.py`)**: Tools for scanning high-liquidity markets and backtesting strategies against historical tick data.
* **Risk Control System**:
    * **Cooldown Mechanism**: Prevents API rate-limiting and "machine-gun" execution.
    * **Position Sizing**: Hard caps on total capital exposure per market.

## 🛠 Tech Stack

* **Language**: Python 3.9+
* **Blockchain**: Web3.py (Direct RPC interaction, Raw Transaction management)
* **Data Analysis**: Pandas, NumPy (OU Process Simulation)
* **Network**: Polygon Mainnet

## ⚡️ Quick Start

1.  **Clone the repository**
    ```bash
    git clone [https://github.com/YourUsername/PolySniper.git](https://github.com/YourUsername/PolySniper.git)
    cd PolySniper
    ```

2.  **Install dependencies**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure Environment**
    Create a `.env` file and add your private key:
    ```ini
    PRIVATE_KEY=0xYourPrivateKey...
    POLYGON_RPC=[https://polygon-rpc.com](https://polygon-rpc.com)
    ```

4.  **Run Sniper Mode (Dry Run)**
    ```bash
    python paper.py
    ```

## ⚠️ Disclaimer

This software is for educational purposes only. Cryptocurrency trading involves high risk. Use at your own risk.