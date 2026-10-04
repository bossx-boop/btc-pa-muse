"""批量生成 81 个 BTC 信号的 Stage1 prompt。
输出：runs/<n>/s1.json  {ts, symbol, messages:[{role,content}]}
"""
import json
import os
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent.parent  # repo 根目录
sys.path.insert(0, str(_ROOT / "pa_model_llm"))
sys.path.insert(0, str(_ROOT))  # market_data.py

import pandas as pd
from market_data import load_bars
from harness import build_kline_frame, build_s1_messages

BASE = str(_ROOT)
RUNS = os.path.join(BASE, "pa_model_llm", "runs")

# 复用连续历史（含周末）+ 最新下载
sys.path.insert(0, BASE)
# NOTE: btc_signal_backtest.load_extended 为本地回测数据加载器（不在本仓库），
#       跑历史回放时需自行提供；实盘路径（live/）不需要它。
from btc_signal_backtest import load_extended


def main():
    bars = load_extended()
    t = bars["dt"].dt.tz_convert("America/New_York")
    passes = json.load(open(os.path.join(BASE, "screen_backtest_btcusd.json")))["passes"]
    os.makedirs(RUNS, exist_ok=True)
    for n, p in enumerate(passes, 1):
        ts = pd.Timestamp(p["ts"]).tz_convert("America/New_York")
        idx = bars.index[t == ts]
        if len(idx) == 0:
            print(f"[{n}] {p['ts']} 找不到K线，跳过")
            continue
        frame = build_kline_frame(bars, int(idx[0]), "BTC-USD")
        msgs = build_s1_messages(frame)
        d = os.path.join(RUNS, f"{n:03d}")
        os.makedirs(d, exist_ok=True)
        json.dump({"n": n, "ts": p["ts"], "symbol": "BTC-USD",
                   "messages": msgs},
                  open(os.path.join(d, "s1.json"), "w"), ensure_ascii=False)
        s_tok = sum(len(m["content"]) for m in msgs) // 4
        print(f"[{n}/{len(passes)}] {p['ts']} s1 prompt ≈{s_tok} tokens", flush=True)
    print("done ->", RUNS)


if __name__ == "__main__":
    main()
