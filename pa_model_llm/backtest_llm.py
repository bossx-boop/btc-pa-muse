"""S2 回填 → 校验 → 提取决策 → 回测模拟。
输入：runs/<n>/s2_reply.txt
输出：runs/<n>/s2_validated.json，llm_backtest_results.json
"""
import json
import os
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent.parent  # repo 根目录
sys.path.insert(0, str(_ROOT / "pa_model_llm"))
sys.path.insert(0, str(_ROOT))  # market_data.py

import pandas as pd
from harness import build_kline_frame, validate

BASE = str(_ROOT)
RUNS = os.path.join(BASE, "pa_model_llm", "runs")
sys.path.insert(0, BASE)
# NOTE: btc_signal_backtest.load_extended 为本地回测数据加载器（不在本仓库），
#       跑历史回放时需自行提供；实盘路径（live/）不需要它。
from btc_signal_backtest import load_extended, simulate


class _S2:
    def __init__(self, entry, stop, tp1):
        self.entry, self.stop, self.tp1 = entry, stop, tp1


def extract_order(s2: dict):
    """从 S2 JSON 提取做多订单；无单返回 None。"""
    dec = s2.get("decision", {})
    order = dec.get("order", {}) or s2.get("order", {})
    otype = order.get("order_type", "") or dec.get("order_type", "")
    if otype in ("", "不下单", "no_order", "wait"):
        return None
    direction = order.get("direction", "") or dec.get("direction", "")
    if direction not in ("做多", "long", "多"):
        return None
    try:
        entry = float(order.get("entry_price") or dec.get("entry_price"))
        stop = float(order.get("stop_price") or dec.get("stop_price"))
        tp1 = float(order.get("tp1_price") or dec.get("tp1_price")
                    or order.get("take_profit_1") or dec.get("take_profit_1"))
    except (TypeError, ValueError):
        return None
    return {"order_type": otype, "entry": entry, "stop": stop, "tp1": tp1,
            "win_rate": s2.get("estimated_win_rate") or dec.get("estimated_win_rate"),
            "terminal": dec.get("terminal_node") or s2.get("terminal_node")}


def main():
    bars = load_extended()
    t = bars["dt"].dt.tz_convert("America/New_York")
    results = []
    for d in sorted(os.listdir(RUNS)):
        rp = os.path.join(RUNS, d, "s2_reply.txt")
        if not os.path.exists(rp):
            continue
        meta = json.load(open(os.path.join(RUNS, d, "s1.json")))
        ts = pd.Timestamp(meta["ts"]).tz_convert("America/New_York")
        k1_idx = int(bars.index[t == ts][0])
        frame = build_kline_frame(bars, k1_idx, "BTC-USD")
        s1 = json.load(open(os.path.join(RUNS, d, "s1_validated.json")))
        raw = open(rp).read()
        good, obj, err = validate("stage2", raw, frame=frame, s1_json=s1)
        rec = {"n": meta["n"], "ts": meta["ts"]}
        if not good:
            rec["s2"] = f"校验失败: {err[:120]}"
            rec["sim"] = None
            results.append(rec)
            print(f"[{d}] S2 校验失败", flush=True)
            continue
        json.dump(obj, open(os.path.join(RUNS, d, "s2_validated.json"), "w"),
                  ensure_ascii=False, indent=1)
        od = extract_order(obj)
        if od is None:
            rec["s2"] = "未下单"
            rec["sim"] = None
        else:
            rec["s2"] = (f"做多{od['order_type']} entry={od['entry']} "
                         f"stop={od['stop']} tp1={od['tp1']}")
            res, det = simulate(bars, k1_idx, _S2(od["entry"], od["stop"], od["tp1"]))
            rec["sim"] = {"result": res, **det}
        results.append(rec)
        print(f"[{d}] {rec['s2']}" +
              (f" | {rec['sim']['result']}" if rec["sim"] else ""), flush=True)
    json.dump(results, open(os.path.join(BASE, "pa_model_llm",
                                         "llm_backtest_results.json"), "w"),
              ensure_ascii=False, indent=1)
    print("done")


if __name__ == "__main__":
    main()
