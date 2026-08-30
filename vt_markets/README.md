# VT Markets MT5 Trading System

A complete, independent XAU/USD trading implementation using **VT Markets** via **MetaTrader 5**.

> ⚠️ This folder is **completely independent** of the `xau_algo/` Twelve Data implementation.
> No Twelve Data API keys are used here. No xau_algo modules are imported here.

---

## 1. How to Install MetaTrader 5

1. Download the VT Markets MT5 terminal from [vtmarkets.com](https://www.vtmarkets.com)
2. Install the terminal on Windows (MT5 Python API requires Windows)
3. Log in to your VT Markets account in the terminal

---

## 2. How to Install Python Dependencies

```bash
pip install MetaTrader5 python-dotenv pytz
```

The `MetaTrader5` package communicates with the locally installed MT5 terminal.

---

## 3. How to Configure Your VT Markets Demo Account

1. Open a demo account at vtmarkets.com
2. Note your:
   - Account number (login)
   - Password
   - Server name (visible in MT5 terminal)

---

## 4. How to Configure `MT5_LOGIN`

Set your account number in `vt_markets/.env`:

```env
MT5_LOGIN=123456789
```

---

## 5. How to Configure `MT5_PASSWORD`

```env
MT5_PASSWORD=YourPasswordHere
```

The password is **never logged** to console or file.

---

## 6. How to Configure `MT5_SERVER`

Find the server name in MT5 under **File → Login to Trade Account → Server**.

```env
MT5_SERVER=VTMarkets-Demo
```

---

## 7. How to Find the Correct XAU/USD Symbol

VT Markets may use a non-standard symbol name. The system will:

1. Check the `MT5_SYMBOL` you configured.
2. If not found, search the broker's symbol list for gold-related symbols.
3. Report all matches — you choose which to use.

Common broker variants:
```
XAUUSD
XAUUSD.a
XAUUSDm
GOLD
```

Set the correct one in `.env`:
```env
MT5_SYMBOL=XAUUSD
```

---

## 8. How to Run

```bash
cd c:\Projects\AlgoTrading
python -m vt_markets.main
```

The system will:
1. Connect to MT5
2. Validate the XAU/USD symbol
3. Load 200 historical 1-min candles for EMA warmup
4. Start live tick polling every 500ms
5. Run both EMA20 and EMA50 strategies independently

Press `Ctrl+C` to stop gracefully.

---

## 9. How Demo Mode Works

By default:
```env
TRADING_MODE=DEMO
ALLOW_LIVE_TRADING=false
```

- Orders go to the broker's demo server (no real money)
- The system logs and validates everything exactly as in live mode
- All strategy logic runs identically

---

## 10. How Live Mode Is Protected

Real-money trading requires **BOTH** flags explicitly set:
```env
TRADING_MODE=LIVE
ALLOW_LIVE_TRADING=true
```

If the MT5 account is detected as LIVE but `ALLOW_LIVE_TRADING=false`, the system **refuses to start** with a clear error message.

There is no accidental live trading.

---

## 11. How EMA20 Works

```
1-minute candle closes
         ↓
Calculate EMA(close, 20)
         ↓
BUY setup conditions (BOTH required):
    close > EMA20       ← strict inequality
    low <= EMA20 <= high    ← EMA touches candle (body or wick)
         ↓
Wait ONLY for the immediately next candle
         ↓
Live price > setup_high → BUY (3 positions at ASK)
         ↓
Next candle closes without breakout → SETUP EXPIRED
```

SELL is the mirror:
```
close < EMA20 AND low <= EMA20 <= high
         ↓
Wait for next candle
         ↓
Live price < setup_low → SELL (3 positions at BID)
```

**EMA50 is never used in EMA20 logic.**

---

## 12. How EMA50 Works

Exactly identical to EMA20, but using `EMA(close, 50)`.
**EMA20 is never used in EMA50 logic.**
The two strategies operate with completely independent state machines.

---

## 13. How the Three Orders Work

Every BUY or SELL breakout creates exactly **3 simultaneous positions**:

| # | Lot  | Risk:Reward | Take Profit         |
|---|------|-------------|---------------------|
| 1 | 0.06 | 1:1         | entry ± risk × 1.0  |
| 2 | 0.04 | 1:2         | entry ± risk × 2.0  |
| 3 | 0.02 | 1:2.5       | entry ± risk × 2.5  |

Multiple signals in the same direction are allowed simultaneously.
Opposite-direction positions are allowed simultaneously.

---

## 14. How SL/TP Works

**Stop Loss:**
- BUY: `SL = setup_candle.low`
- SELL: `SL = setup_candle.high`

**Risk:**
- BUY: `risk = entry_price − stop_loss`
- SELL: `risk = stop_loss − entry_price`

**Entry prices:**
- BUY: ASK price at moment of breakout
- SELL: BID price at moment of breakout

Positions remain open until MT5 hits their SL or TP. No time-based or opposite-signal exits.

---

## 15. How the Daily 1% Target Works

```
Daily target = current_balance × 0.01
```

For a $10,000 account: target = **$100/day**.

- Once realized P&L reaches the target → new entries are **blocked**
- Existing open positions **continue** to their SL/TP unaffected
- At **03:45 IST** each Mon–Fri → daily P&L resets, target recalculates based on current balance

---

## Project Structure

```
vt_markets/
├── config.py           ← All configurable parameters
├── main.py             ← Entry point (python -m vt_markets.main)
│
├── mt5/
│   ├── connection.py   ← MT5 connect/login/verify
│   ├── symbols.py      ← XAU/USD symbol discovery
│   ├── market_data.py  ← Historical + live tick data from MT5
│   └── execution.py    ← Order placement abstraction
│
├── strategies/
│   ├── base_strategy.py  ← State machine (shared by EMA20+EMA50)
│   ├── ema20_strategy.py ← EMA20 only
│   └── ema50_strategy.py ← EMA50 only
│
├── trading/
│   ├── daily_target.py     ← 1% daily halt guard
│   ├── risk_manager.py     ← SL/TP tier computation
│   ├── order_manager.py    ← Signal → 3 orders
│   └── position_manager.py ← Position tracking + restart recovery
│
├── models/
│   ├── candle.py   ← Candle dataclass
│   ├── tick.py     ← Tick dataclass
│   ├── signal.py   ← Signal dataclass
│   └── position.py ← VTPosition dataclass
│
└── tests/
    ├── conftest.py          ← MT5 mock fixtures
    ├── test_mt5_connection.py
    ├── test_market_data.py
    ├── test_ema20.py
    ├── test_ema50.py
    └── test_execution.py
```

---

## Running Tests

```bash
cd c:\Projects\AlgoTrading
pytest vt_markets/tests/ -v
```

All tests use mocked MT5 — no real terminal or account required.

## Running Existing Tests (must remain passing)

```bash
pytest xau_algo/tests/ -v
```
