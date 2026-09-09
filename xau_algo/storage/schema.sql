-- xau_algo/storage/schema.sql
-- ============================
-- Run this SQL in your Supabase SQL Editor to create all required tables.
-- The application also runs this automatically via SupabaseRepository.ensure_tables()
-- on startup, so manual execution is optional.

-- -----------------------------------------------------------------
-- 1. candles: 1-minute OHLC bars
-- -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS candles (
    id        BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    symbol    TEXT        NOT NULL DEFAULT 'XAU/USD',
    open      NUMERIC(12,5),
    high      NUMERIC(12,5),
    low       NUMERIC(12,5),
    close     NUMERIC(12,5),
    UNIQUE (timestamp, symbol)
);

-- -----------------------------------------------------------------
-- 2. ticks: raw WebSocket price feed (~1 tick/sec)
-- -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticks (
    id        BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ   NOT NULL,
    symbol    TEXT          NOT NULL DEFAULT 'XAU/USD',
    price     NUMERIC(12,5) NOT NULL
);

CREATE INDEX IF NOT EXISTS ticks_timestamp_idx ON ticks (timestamp DESC);

-- Optional: partition by day for performance if tick volume is very high
-- (uncomment and adapt if needed)
-- ALTER TABLE ticks SET (
--   autovacuum_vacuum_scale_factor = 0.01,
--   autovacuum_analyze_scale_factor = 0.005
-- );

-- -----------------------------------------------------------------
-- 3. signals: EMA breakout signals (pre-position event)
-- -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    id           UUID PRIMARY KEY,
    strategy     TEXT        NOT NULL,          -- "EMA20" | "EMA50"
    direction    TEXT        NOT NULL,          -- "BUY" | "SELL"
    entry_price  NUMERIC(12,5),
    stop_loss    NUMERIC(12,5),
    setup_time   TIMESTAMPTZ,
    signal_time  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- -----------------------------------------------------------------
-- 4. trades: closed positions (mirror of trades.csv)
-- -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trades (
    id           UUID PRIMARY KEY,
    strategy     TEXT          NOT NULL,        -- "EMA20" | "EMA50"
    side         TEXT          NOT NULL,        -- "BUY" | "SELL"
    lot          NUMERIC(8,4),
    entry_price  NUMERIC(12,5),
    stop_loss    NUMERIC(12,5),
    take_profit  NUMERIC(12,5),
    risk_reward  NUMERIC(6,2),
    setup_time   TIMESTAMPTZ,
    entry_time   TIMESTAMPTZ,
    exit_time    TIMESTAMPTZ,
    exit_price   NUMERIC(12,5),
    status       TEXT,                          -- "CLOSED_TP" | "CLOSED_SL"
    pnl          NUMERIC(12,4),
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Useful view: daily P&L summary
CREATE OR REPLACE VIEW daily_pnl AS
SELECT
    DATE(exit_time AT TIME ZONE 'Asia/Kolkata') AS trade_date,
    strategy,
    COUNT(*)                                    AS trade_count,
    SUM(pnl)                                    AS total_pnl,
    SUM(CASE WHEN status = 'CLOSED_TP' THEN 1 ELSE 0 END) AS wins,
    SUM(CASE WHEN status = 'CLOSED_SL' THEN 1 ELSE 0 END) AS losses
FROM trades
WHERE exit_time IS NOT NULL
GROUP BY 1, 2
ORDER BY 1 DESC, 2;

-- -----------------------------------------------------------------
-- 5. Chart API Event Notification Triggers
-- -----------------------------------------------------------------

CREATE OR REPLACE FUNCTION notify_chart_event()
RETURNS TRIGGER AS $$
DECLARE
    event_type TEXT;
    event_ts TIMESTAMPTZ;
    payload JSON;
BEGIN
    IF TG_TABLE_NAME = 'ticks' THEN
        event_type := 'tick';
        event_ts := NEW.timestamp;
        payload := json_build_object(
            'instrument', NEW.symbol,
            'price', NEW.price
        );
    ELSIF TG_TABLE_NAME = 'candles' THEN
        event_type := 'candle_update';
        event_ts := NEW.timestamp;
        payload := json_build_object(
            'instrument', NEW.symbol,
            'open', NEW.open,
            'high', NEW.high,
            'low', NEW.low,
            'close', NEW.close
        );
    ELSIF TG_TABLE_NAME = 'signals' THEN
        event_type := 'signal';
        event_ts := COALESCE(NEW.signal_time, CURRENT_TIMESTAMP);
        payload := json_build_object(
            'id', NEW.id,
            'strategy_id', NEW.strategy,
            'type', NEW.direction,
            'price', NEW.entry_price
        );
    ELSIF TG_TABLE_NAME = 'trades' THEN
        event_type := 'trade';
        event_ts := COALESCE(NEW.entry_time, CURRENT_TIMESTAMP);
        payload := json_build_object(
            'id', NEW.id,
            'strategy_id', NEW.strategy,
            'side', NEW.side,
            'status', NEW.status,
            'profit_loss', NEW.pnl
        );
    END IF;

    -- Publish event
    PERFORM pg_notify('chart_events', json_build_object(
        'event', event_type,
        'timestamp', event_ts,
        'data', payload
    )::text);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_notify_ticks ON ticks;
CREATE TRIGGER trigger_notify_ticks
AFTER INSERT ON ticks
FOR EACH ROW EXECUTE FUNCTION notify_chart_event();

DROP TRIGGER IF EXISTS trigger_notify_candles ON candles;
CREATE TRIGGER trigger_notify_candles
AFTER INSERT OR UPDATE ON candles
FOR EACH ROW EXECUTE FUNCTION notify_chart_event();

DROP TRIGGER IF EXISTS trigger_notify_signals ON signals;
CREATE TRIGGER trigger_notify_signals
AFTER INSERT ON signals
FOR EACH ROW EXECUTE FUNCTION notify_chart_event();

DROP TRIGGER IF EXISTS trigger_notify_trades ON trades;
CREATE TRIGGER trigger_notify_trades
AFTER INSERT OR UPDATE ON trades
FOR EACH ROW EXECUTE FUNCTION notify_chart_event();
