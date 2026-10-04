#!/usr/bin/env python3
"""BTC 实盘 screening（PA 模型，Jeff 2026-10-03 定稿）。

- 标的：BTC-USD，只做多（long_only=True）。
- 时间：周一至周五 09:30–16:00 ET（America/Toronto）对每根已收盘 5m bar 跑零 token screen。
- 指标历史：连续 24h 现货数据（含隔夜/周末），与回测口径一致。
- screen PASS 后按 Option D（120 分钟冷却 + 新信号棒例外）决定是否触发完整模型。
- 本脚本只做 screen + 触发判定，不跑 LLM。触发事件写入 state.json 的 pending，
  由 cron agent 编排完整分析（S1/S2 子代理）并在买入信号时发邮件。

输出：JSON summary 到 stdout。triggers 非空时 cron agent 需跟进。
"""
import os
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import yfinance as yf
import pandas as pd

# --- 可移植路径：repo 根目录 = 本文件上两级；原 PA Agent 仓库位于
#     <root>/third_party/PA_Agent（或用环境变量 PA_AGENT_DIR 覆盖）
REPO_ROOT = Path(__file__).resolve().parent.parent
PA_AGENT_DIR = Path(os.environ.get("PA_AGENT_DIR",
                                   REPO_ROOT / "third_party" / "PA_Agent"))

BASE = REPO_ROOT
LIVE = BASE / "live"
ARCHIVE = LIVE / "btc_5m_live.csv"
STATE = LIVE / "state.json"

sys.path.insert(0, str(BASE))          # screen.py / runner_policy.py / market_data.py
sys.path.insert(0, str(PA_AGENT_DIR))  # 原 PA Agent 仓库（pa_agent 包）
sys.path.insert(0, str(BASE / "pa_model_llm"))

from market_data import load_bars
from screen import run_screen
from runner_policy import should_trigger, new_state
from harness import build_kline_frame

SYMBOL = "BTC-USD"
TZ = ZoneInfo("America/Toronto")
SESSION_START = dtime(9, 30)
SESSION_END = dtime(16, 0)


def now_et():
    return datetime.now(TZ)


def in_session(ts):
    """ts: tz-aware datetime。周一至周五 09:30–16:00 ET（含 09:30 那根，不含 16:00）。"""
    if ts.weekday() >= 5:
        return False
    t = ts.timetz()
    return dtime(9, 30) <= t.time() < dtime(16, 0)


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"last_bar": None, "trigger": None, "pending": []}


def save_state(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str))


def fetch_and_merge():
    """拉 yfinance BTC-USD 5m（近 7 天），合并进 archive，去重。返回 DataFrame（load_bars 后）。"""
    df = yf.download(SYMBOL, period="7d", interval="5m", progress=False,
                     auto_adjust=False)
    if df is None or len(df) == 0:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    idx = df.index.tz_convert(TZ)
    # 只保留已收盘的 bar（去掉当前正在形成的 bar）
    now = now_et()
    cur_open = now.replace(second=0, microsecond=0)
    cur_open -= pd.Timedelta(minutes=cur_open.minute % 5,
                             seconds=cur_open.second,
                             microseconds=cur_open.microsecond)
    df = df[idx < cur_open]
    idx = idx[idx < cur_open]
    if len(df) == 0:
        return None
    df.insert(0, "dt", [t.isoformat() for t in idx])
    new = df.reset_index(drop=True)

    if ARCHIVE.exists():
        old = pd.read_csv(ARCHIVE)
        combined = pd.concat([old, new], ignore_index=True)
        combined = combined.drop_duplicates(subset=["dt"], keep="last")
        combined = combined.sort_values("dt").reset_index(drop=True)
    else:
        combined = new
    # 只保留近 10 天，控制文件大小（指标预热足够）
    combined["__d"] = pd.to_datetime(combined["dt"]).dt.tz_convert(TZ)
    cutoff = now_et() - pd.Timedelta(days=10)
    combined = combined[combined["__d"] >= cutoff].drop(columns=["__d"])
    combined.to_csv(ARCHIVE, index=False)
    return load_bars(str(ARCHIVE), symbol=SYMBOL, tz="America/Toronto")


def main():
    LIVE.mkdir(exist_ok=True)
    out = {"ok": True, "now": now_et().isoformat(), "new_bars": 0,
           "screened": 0, "passes": 0, "triggers": []}
    bars = fetch_and_merge()
    if bars is None or len(bars) < 160:
        out["ok"] = False
        out["error"] = "no data or insufficient history"
        print(json.dumps(out, ensure_ascii=False))
        return

    state = load_state()
    t = bars["dt"].dt.tz_convert(TZ)

    # 找出 last_bar 之后的新 bar
    if state["last_bar"]:
        last = pd.Timestamp(state["last_bar"]).tz_convert(TZ)
        new_idx = bars.index[t > last].tolist()
    else:
        # 首次运行：只记位置，不补筛历史，避免触发洪水
        new_idx = []
        state["last_bar"] = t.iloc[-1].isoformat()

    out["new_bars"] = len(new_idx)
    trig_state = None
    if state["trigger"]:
        tr = state["trigger"]
        trig_state = {"time": pd.Timestamp(tr["time"]).tz_convert(TZ),
                      "path_kinds": set(tr["path_kinds"]),
                      "direction": tr["direction"], "ail": tr["ail"]}

    for i in new_idx:
        ts = t.iloc[i]
        state["last_bar"] = ts.isoformat()
        if not in_session(ts):
            continue
        # frame 需要 150 根历史（含预热）
        if i < 150:
            continue
        frame = build_kline_frame(bars, i, SYMBOL)
        r = run_screen(frame, long_only=True)
        out["screened"] += 1
        if r["verdict"] != "PASS":
            continue
        out["passes"] += 1
        kinds = {f"{p['kind']}/{p['side']}" for p in r["paths"]}
        direction = r["facts"].get("direction", "?")
        ail = r["facts"].get("always_in_branch", "-")
        ok, reason = should_trigger(trig_state, ts, kinds, direction, ail, 120)
        if ok:
            trig_state = new_state(ts, kinds, direction, ail)
            state["trigger"] = {"time": ts.isoformat(),
                                "path_kinds": sorted(kinds),
                                "direction": direction, "ail": ail}
            trig = {"ts": ts.isoformat(), "reason": reason,
                    "paths": sorted(kinds), "direction": direction,
                    "k1_close": r["facts"].get("k1_close"),
                    "atr14": r["facts"].get("atr14")}
            state["pending"].append(trig)
            out["triggers"].append(trig)

    save_state(state)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
