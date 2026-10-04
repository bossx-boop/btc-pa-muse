"""market_data.py — MANDATORY entry point for price data.

Every backtest / research script in this project MUST load bars through
``load_bars()`` and MUST take daily indicators from the helpers here.
Corporate-action (split) adjustments and lookahead rules live in this one
place so they cannot be silently forgotten by the next script.

Rules enforced:
  1. Split adjustment: OHLC before a split ex-date is divided by the split
     ratio (volume multiplied), using SPLIT_CALENDAR below.
  2. Lookahead guard: ``daily_atr()`` returns the ATR as known at the prior
     day's close (shifted by one day). A same-day ATR must never be used
     for that day's trading decisions.
  3. Fail-loud guard: after adjustment, any overnight gap larger than
     MAX_SANE_OVERNIGHT_GAP raises — that means a split is missing from
     SPLIT_CALENDAR. Add it there; do not work around it.
"""

import datetime

import numpy as np
import pandas as pd

# Split calendar: (ex-date, ratio). A 5-for-1 split has ratio 5.
# Bars with date strictly BEFORE ex-date are divided by ratio.
SPLIT_CALENDAR = {
    "TSLA": [
        (datetime.date(2020, 8, 31), 5),  # 5-for-1
        (datetime.date(2022, 8, 25), 3),  # 3-for-1
    ],
}

# Largest overnight move that is NOT a split in the 2020-2026 TSLA data is
# ~23%. Anything above 40% after adjustment means an unlisted split.
MAX_SANE_OVERNIGHT_GAP = 0.40


def split_factor(dates, symbol):
    """Cumulative pre-split divisor per bar (>= 1)."""
    dates = pd.Series(dates)
    f = np.ones(len(dates), dtype=float)
    for ex_date, ratio in sorted(SPLIT_CALENDAR.get(symbol, [])):
        f[(dates < ex_date).to_numpy()] *= ratio
    return f


def apply_splits(df, symbol):
    df = df.copy()
    f = split_factor(df["date"], symbol)
    for col in ("open", "high", "low", "close"):
        df[col] = df[col] / f
    df["volume"] = df["volume"] * f
    return df


def assert_no_unadjusted_splits(df, symbol):
    d = df.groupby("date").agg(c=("close", "last"))
    gaps = d["c"].pct_change().abs().dropna()
    bad = gaps[gaps > MAX_SANE_OVERNIGHT_GAP]
    if len(bad):
        worst = bad.nlargest(3)
        raise ValueError(
            f"[{symbol}] {len(bad)} overnight gap(s) > "
            f"{MAX_SANE_OVERNIGHT_GAP:.0%} after split adjustment — "
            f"a split is missing from SPLIT_CALENDAR. Worst:\n{worst}\n"
            f"Add the split to SPLIT_CALENDAR in market_data.py."
        )


def load_bars(csv_path, symbol="TSLA", tz="America/New_York"):
    """Load 5-min bars: tz-aware, split-adjusted, gap-guarded."""
    df = pd.read_csv(csv_path)
    df["dt"] = pd.to_datetime(df["dt"], utc=True).dt.tz_convert(tz)
    df["date"] = df["dt"].dt.date
    df = apply_splits(df, symbol)
    assert_no_unadjusted_splits(df, symbol)
    return df.reset_index(drop=True)


def daily_atr(df5, period=14):
    """Daily ATR as known at the PRIOR close (shifted 1 day — no lookahead).

    Columns: atr, atr_q25, atr_q90 (60-day rolling quantiles, also shifted).
    Rows before warmup are NaN and must be skipped by callers.
    """
    d = df5.groupby("date").agg(open=("open", "first"), high=("high", "max"),
                                low=("low", "min"), close=("close", "last"))
    prev_close = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"],
                    (d["high"] - prev_close).abs(),
                    (d["low"] - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    d["atr"] = atr.shift(1)
    d["atr_q25"] = atr.rolling(60, min_periods=60).quantile(0.25).shift(1)
    d["atr_q90"] = atr.rolling(60, min_periods=60).quantile(0.90).shift(1)
    return d
