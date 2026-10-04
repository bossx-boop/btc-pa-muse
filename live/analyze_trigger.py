#!/usr/bin/env python3
"""单次触发的完整模型编排 helper（cron agent 调用，LLM 部分由子代理执行）。

用法：
  analyze_trigger.py build-s1 --ts <ISO> --dir <rundir>
      按 ts 在 live archive 定位 K1，生成 s1.json（完整 Stage1 prompt）。
  analyze_trigger.py build-s2 --dir <rundir>
      校验 s1_reply.txt → 生成 s2.json（完整 Stage2 prompt）。
  analyze_trigger.py decide --dir <rundir>
      校验 s2_reply.txt → 输出决策 JSON（含 order，未下单则 order=null）。
"""
import os
import sys
import json
import argparse
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

# --- 可移植路径：repo 根目录 = 本文件上两级；原 PA Agent 仓库位于
#     <root>/third_party/PA_Agent（或用环境变量 PA_AGENT_DIR 覆盖）
REPO_ROOT = Path(__file__).resolve().parent.parent
PA_AGENT_DIR = Path(os.environ.get("PA_AGENT_DIR",
                                   REPO_ROOT / "third_party" / "PA_Agent"))

BASE = REPO_ROOT
LIVE = BASE / "live"
ARCHIVE = LIVE / "btc_5m_live.csv"

sys.path.insert(0, str(BASE))          # screen.py / runner_policy.py / market_data.py
sys.path.insert(0, str(PA_AGENT_DIR))  # 原 PA Agent 仓库（pa_agent 包）
sys.path.insert(0, str(BASE / "pa_model_llm"))

from market_data import load_bars
from harness import build_kline_frame, build_s1_messages, build_s2_messages, validate
from backtest_llm import extract_order

SYMBOL = "BTC-USD"
TZ = ZoneInfo("America/Toronto")


def locate(ts_iso):
    bars = load_bars(str(ARCHIVE), symbol=SYMBOL, tz="America/Toronto")
    t = bars["dt"].dt.tz_convert(TZ)
    ts = pd.Timestamp(ts_iso).tz_convert(TZ)
    idx = bars.index[t == ts]
    if len(idx) == 0:
        # 容差：找最接近且不超过 ts 的 bar
        cand = bars.index[t <= ts]
        if len(cand) == 0:
            raise SystemExit(f"no bar at or before {ts_iso}")
        i = int(cand[-1])
    else:
        i = int(idx[0])
    return bars, i, t.iloc[i]


def cmd_build_s1(args):
    d = Path(args.dir)
    d.mkdir(parents=True, exist_ok=True)
    bars, i, ts = locate(args.ts)
    frame = build_kline_frame(bars, i, SYMBOL)
    msgs = build_s1_messages(frame)
    (d / "s1.json").write_text(json.dumps(
        {"ts": ts.isoformat(), "bar_index": i, "messages": msgs},
        ensure_ascii=False))
    (d / "meta.json").write_text(json.dumps(
        {"ts": ts.isoformat(), "k1_close": float(frame.bars[0].close)},
        ensure_ascii=False, indent=1))
    print(json.dumps({"ok": True, "ts": ts.isoformat(), "k1_close": float(frame.bars[0].close)}))


def cmd_build_s2(args):
    d = Path(args.dir)
    bars, i, ts = locate(json.loads((d / "meta.json").read_text())["ts"])
    frame = build_kline_frame(bars, i, SYMBOL)
    raw = (d / "s1_reply.txt").read_text()
    ok, obj, err = validate("stage1", raw, frame=frame)
    if not ok:
        print(json.dumps({"ok": False, "stage": "s1_validate", "error": err[:300]}))
        sys.exit(1)
    (d / "s1_validated.json").write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    msgs, files = build_s2_messages(frame, obj)
    (d / "s2.json").write_text(json.dumps(
        {"ts": ts.isoformat(), "strategy_files": files, "messages": msgs},
        ensure_ascii=False))
    print(json.dumps({"ok": True, "direction": obj.get("direction"),
                      "cycle": obj.get("cycle_position"),
                      "strategy_files": files}))


def cmd_decide(args):
    d = Path(args.dir)
    meta = json.loads((d / "meta.json").read_text())
    bars, i, ts = locate(meta["ts"])
    frame = build_kline_frame(bars, i, SYMBOL)
    s1 = json.loads((d / "s1_validated.json").read_text())
    raw = (d / "s2_reply.txt").read_text()
    ok, obj, err = validate("stage2", raw, frame=frame, s1_json=s1)
    if not ok:
        print(json.dumps({"ok": False, "stage": "s2_validate", "error": err[:300]}))
        sys.exit(1)
    (d / "s2_validated.json").write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    od = extract_order(obj)
    term = obj.get("terminal", {})
    out = {"ok": True, "ts": ts.isoformat(),
           "order_type": obj["decision"].get("order_type"),
           "terminal": f"{term.get('node_id')}/{term.get('outcome')}",
           "order": od}
    print(json.dumps(out, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("build-s1")
    a.add_argument("--ts", required=True)
    a.add_argument("--dir", required=True)
    b = sub.add_parser("build-s2")
    b.add_argument("--dir", required=True)
    c = sub.add_parser("decide")
    c.add_argument("--dir", required=True)
    args = ap.parse_args()
    {"build-s1": cmd_build_s1, "build-s2": cmd_build_s2,
     "decide": cmd_decide}[args.cmd](args)


if __name__ == "__main__":
    main()
