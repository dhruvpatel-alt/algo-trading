# XAU/USD Algo Trading System

A Python-based XAU/USD (Gold Spot) paper-trading and backtesting system built on **Twelve Data** market data with two independent EMA-touch-breakout strategies.

---

## 1. Python Version

Python **3.10+** is required (uses `X | Y` union type hints).

---

## 2. Installation

```bash
cd c:\Projects\AlgoTrading\xau_algo
pip install -r requirements.txt
```

---

## 3. `.env` Setup

The system reads your API keys from `c:\Projects\AlgoTrading\.env`. No changes needed if your keys are already there:

```env
TWELVE_DATA_API_KEY_1=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWELVE_DATA_API_KEY_2=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWELVE_DATA_API_KEY_3=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWELVE_DATA_API_KEY_4=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWELVE_DATA_API_KEY_5=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

- At least one key must be present.
- Keys are **never logged**. Only their index (e.g., `KEY_2`) appears in logs.
- All five are loaded and rotated automatically on rate-limit errors.

---

## 4. Twelve Data API Configuration

The system uses **two Twelve Data endpoints**:

| Mode | Endpoint | Purpose |
|------|----------|---------|
| REST | `GET /time_series` | Historical 1-min OHLCV (backtest) |
| WebSocket | `wss://ws.twelvedata.com/v1/quotes/price` | Live price ticks (paper trading) |

**Key rotation**: If a key returns HTTP 429 or an API-level rate-limit error, the client automatically rotates to the next key. All 5 keys can serve different reconnect attempts.

---

## 5. How to Run Backtest

```bash
cd c:\Projects\AlgoTrading\xau_algo
python main.py --mode backtest --days 7
```

- Fetches the last ~7 days of 1-minute XAU/USD candles via REST.
- Runs EMA20 and EMA50 strategies over the data.
- Prints a summary table at the end.
- Saves all closed trades to `logs/trades.csv` and `logs/trades.jsonl`.

```bash
# Backtest 30 days
python main.py --mode backtest --days 30
```

---

## 6. How to Run Paper Trading

```bash
cd c:\Projects\AlgoTrading\xau_algo
python main.py --mode paper
```

- Connects to Twelve Data WebSocket for live XAU/USD price ticks.
- Builds 1-minute candles in real-time.
- Runs both strategies independently on each completed candle.
- Fires breakout detection tick-by-tick (does NOT wait for candle close).
- Logs every event to `logs/trading.log` and stdout.
- Press **Ctrl+C** for graceful shutdown.

---

## 7. How the EMA20 Strategy Works

```
1-minute candle closes
        ↓
Calculate EMA20 (period=20)
        ↓
BUY conditions — BOTH must be true:
    close  >  EMA20          (strict)
    low   <=  EMA20  <=  high
        ↓
Store setup_high, setup_low
Watch ONLY the immediately next candle:
    If price > setup_high → BUY (3 positions)
    If candle closes without breakout → SETUP EXPIRED
```

SELL is the mirror image:
```
    close  <  EMA20  AND  low <= EMA20 <= high
        ↓
If price < setup_low on next candle → SELL
```

**EMA50 is never used in EMA20 logic.**

---

## 8. How the EMA50 Strategy Works

Exactly identical methodology, but:
- Uses `EMA(close, 50)` instead of `EMA(close, 20)`.
- Completely independent state machine.
- **EMA20 is never used in EMA50 logic.**

Both strategies run simultaneously. Their signals do not affect each other.

---

## 9. How SL/TP Works

Every breakout signal creates **exactly 3 positions**:

| Lot  | Risk:Reward | Take Profit |
|------|-------------|-------------|
| 0.06 | 1:1         | entry ± risk × 1.0 |
| 0.04 | 1:2         | entry ± risk × 2.0 |
| 0.02 | 1:2.5       | entry ± risk × 2.5 |

**Stop Loss**:
- BUY: `SL = setup_candle.low`
- SELL: `SL = setup_candle.high`

**Risk**:
- BUY: `risk = entry_price − stop_loss`
- SELL: `risk = stop_loss − entry_price`

**PnL formula** (XAU/USD spot gold, `CONTRACT_SIZE = 100`):
```
BUY  PnL = (exit − entry) × lot × 100
SELL PnL = (entry − exit) × lot × 100
```

Positions close **only** when SL or TP is hit. No time-based or opposite-signal exits.

---

## 10. How the Daily 1% Target Works

```
daily_target = current_balance × 0.01
```

For a $10,000 account: target = **$100/day**.

- Tracks **combined realized P&L** from both EMA20 + EMA50 strategies.
- Once `broker.daily_pnl >= daily_target`:
  - All **new entries are blocked**.
  - All **existing positions continue** until their own SL/TP.
- At **03:45 IST** each Mon–Fri: daily P&L resets, target recalculates based on current balance (compound growth).

---

## 11. How to Interpret Logs

Each log line follows this format:
```
YYYY-MM-DD HH:MM:SS | LEVEL    | module | message
```

Key events to watch for:

| Log Pattern | Meaning |
|-------------|---------|
| `EMA20 BUY setup detected` | Setup candle identified — waiting for next candle |
| `EMA20 BUY breakout` | Live price crossed setup high → 3 positions opened |
| `EMA20 BUY setup EXPIRED` | Next candle closed without breakout → setup dead |
| `EMA20 SELL setup detected` | SELL setup candle identified |
| `CLOSED EMA20 BUY … CLOSED_TP` | Position hit take profit |
| `CLOSED EMA20 BUY … CLOSED_SL` | Position hit stop loss |
| `Daily target reached` | No more new entries today |
| `Trading session started` | Daily reset at 03:45 IST |
| `Rotating API key: KEY_1 → KEY_2` | Rate limit hit, switched keys |

---

## 12. Important Assumptions

| # | Assumption | Configurable |
|---|-----------|--------------|
| 1 | **Entry price** = breakout level (`setup_high` for BUY, `setup_low` for SELL) | `config.ENTRY_AT_BREAKOUT_LEVEL` |
| 2 | **Same-candle SL/TP conflict** (backtest): SL is assumed to hit first | `config.ASSUME_SL_FIRST_ON_CONFLICT` |
| 3 | **Contract size**: 1 lot = 100 oz (standard spot gold) | `config.CONTRACT_SIZE` |
| 4 | **EMA warmup**: no trades until 20 (EMA20) or 50 (EMA50) candles available | Not configurable |
| 5 | `close == EMA` → **no setup** (strict inequality per spec) | Not configurable |
| 6 | **Daily reset** at 03:45 IST Mon–Fri | `config.START_TIME`, `config.TIMEZONE`, `config.TRADING_DAYS` |
| 7 | **No slippage or spread** modelled in backtest | Future enhancement |
| 8 | **OHLC-only backtest**: no tick data; candle range used for SL/TP detection | Future enhancement |

---

## Project Structure

```
xau_algo/
├── config.py              ← All configurable parameters
├── main.py                ← CLI entry point (--mode paper | backtest)
│
├── data/
│   ├── twelve_data_client.py   ← REST client with key rotation
│   ├── websocket_client.py     ← Live price WebSocket with key rotation
│   └── candle_builder.py       ← Assembles ticks → 1-min candles
│
├── indicators/
│   └── ema.py             ← Pure EMA calculation (no side effects)
│
├── strategies/
│   ├── base_strategy.py   ← State machine (shared by EMA20 + EMA50)
│   ├── ema20_strategy.py  ← EMA20 — uses ONLY EMA20
│   └── ema50_strategy.py  ← EMA50 — uses ONLY EMA50
│
├── trading/
│   ├── position.py        ← Single trade position dataclass
│   ├── paper_broker.py    ← Balance, equity, SL/TP management
│   └── daily_target.py    ← 1% daily halt guard
│
├── backtest/
│   └── engine.py          ← Historical backtest runner
│
├── storage/
│   └── trade_repository.py ← CSV + JSONL trade persistence
│
├── tests/
│   ├── test_ema20_strategy.py
│   ├── test_ema50_strategy.py
│   ├── test_paper_broker.py
│   └── test_daily_target.py
│
└── logs/                  ← Auto-created at runtime
    ├── trading.log
    ├── trades.csv
    └── trades.jsonl
```

---

## Running Tests

```bash
cd c:\Projects\AlgoTrading\xau_algo
pytest tests/ -v
```
