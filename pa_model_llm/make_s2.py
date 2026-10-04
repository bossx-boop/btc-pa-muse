"""S1 回填 → 校验 → 路由 → 生成 S2 prompt。
输入：runs/<n>/s1_reply.txt（子代理原文）
输出：runs/<n>/s1_validated.json，runs/<n>/s2.json
"""
import json
import os
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent.parent  # repo 根目录
sys.path.insert(0, str(_ROOT / "pa_model_llm"))
sys.path.insert(0, str(_ROOT))  # market_data.py

import pandas as pd
from harness import build_kline_frame, build_s2_messages, validate

BASE = str(_ROOT)
RUNS = os.path.join(BASE, "pa_model_llm", "runs")
sys.path.insert(0, BASE)
# NOTE: btc_signal_backtest.load_extended 为本地回测数据加载器（不在本仓库），
#       跑历史回放时需自行提供；实盘路径（live/）不需要它。
from btc_signal_backtest import load_extended


def main():
    bars = load_extended()
    t = bars["dt"].dt.tz_convert("America/New_York")
    ok, fail = 0, []
    for d in sorted(os.listdir(RUNS)):
        rp = os.path.join(RUNS, d, "s1_reply.txt")
        if not os.path.exists(rp):
            continue
        meta = json.load(open(os.path.join(RUNS, d, "s1.json")))
        ts = pd.Timestamp(meta["ts"]).tz_convert("America/New_York")
        frame = build_kline_frame(bars, int(bars.index[t == ts][0]), "BTC-USD")
        raw = open(rp).read()
        good, obj, err = validate("stage1", raw, frame=frame)
        if not good:
            fail.append((d, err[:100]))
            continue
        json.dump(obj, open(os.path.join(RUNS, d, "s1_validated.json"), "w"),
                  ensure_ascii=False, indent=1)
        msgs, files = build_s2_messages(frame, obj)
        json.dump({"n": meta["n"], "ts": meta["ts"], "strategy_files": files,
                   "messages": msgs},
                  open(os.path.join(RUNS, d, "s2.json"), "w"), ensure_ascii=False)
        s_tok = sum(len(m["content"]) for m in msgs) // 4
        cp = obj.get("cycle_position", "?")
        dr = obj.get("direction", "?")
        print(f"[{d}] S1✓ {dr}/{cp} files={len(files)} s2 ≈{s_tok} tok", flush=True)
        ok += 1
    print(f"S1 valid: {ok}, failed: {len(fail)}")
    for d, e in fail:
        print("  FAIL", d, e)


if __name__ == "__main__":
    main()
