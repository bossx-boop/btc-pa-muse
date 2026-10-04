"""pa_model_llm harness：完全按原博主 prompt 跑，仅 LLM 身份不同。

原仓库：~/workspace/pa-agent-eval/repo (commit cd0aca2)
本包复用原仓库以下模块（不重写，保证 prompt 逐字一致）：
  - pa_agent.ai.prompt_assembler.PromptAssembler  → 组装 Stage1/Stage2 消息
  - pa_agent.ai.router.route_strategy_files      → Stage2 策略文件路由
  - pa_agent.ai.json_validator.JsonValidator     → LLM 输出 JSON 校验
  - pa_agent.data.snapshot.compute_indicators    → EMA20/ATR14（含 50 根预热）
  - pa_agent.data.base.KlineFrame/KlineBar       → 数据结构

唯一替换：DeepSeekClient → MetaAIClient（子代理执行的 Muse）。
prompt 文本、路由逻辑、校验规则、指标算法全部保持原样。

用法：
  python3 make_prompts.py            # 81 信号 → runs/<n>/s1.json
  # ……子代理执行 S1，回填 runs/<n>/s1_reply.txt……
  python3 make_s2.py                 # 校验 S1 → runs/<n>/s2.json
  # ……子代理执行 S2，回填 runs/<n>/s2_reply.txt……
  python3 backtest_llm.py            # 校验 S2 → 提取决策 → 回测
"""
import os
import sys
import time
import types
from pathlib import Path

# --- PyQt6 stub（原仓库 pa_agent.util.event_bus 的 GUI import chain 需要，
#     但 prompt/路由/校验逻辑用不到 GUI；此处幂等）---
if "PyQt6" not in sys.modules:
    _pyqt6 = types.ModuleType("PyQt6")
    _qtcore = types.ModuleType("PyQt6.QtCore")

    class _QObject:
        def __init__(self, *a, **k):
            pass

    _qtcore.QObject = _QObject
    _qtcore.pyqtSignal = lambda *a, **k: None  # noqa: E731
    _pyqt6.QtCore = _qtcore
    sys.modules["PyQt6"] = _pyqt6
    sys.modules["PyQt6.QtCore"] = _qtcore

# 原 PA Agent 仓库：<repo_root>/third_party/PA_Agent，或环境变量 PA_AGENT_DIR
_HERE = Path(__file__).resolve()
REPO = Path(os.environ.get("PA_AGENT_DIR",
                           _HERE.parent.parent / "third_party" / "PA_Agent"))
sys.path.insert(0, str(REPO))

from pa_agent.data.base import KlineBar, KlineFrame, IndicatorBundle
from pa_agent.data.snapshot import compute_indicators

N_FRAME = 100
WARMUP = 50


def build_kline_frame(df, end_idx: int, symbol: str) -> KlineFrame:
    """df: load_bars() 的 DataFrame（UTC dt，已排序）。
    end_idx: K1（最新已收盘信号棒）在 df 中的位置。
    取 100 根 frame + 50 根预热，newest-first，seq=1 为 K1。"""
    start = end_idx - (N_FRAME + WARMUP) + 1
    win = df.iloc[start: end_idx + 1]
    bars_newest = []
    for j, (_, r) in enumerate(win.iloc[::-1].iterrows()):
        bars_newest.append(KlineBar(
            seq=j + 1,
            ts_open=float(r["dt"].timestamp() * 1000),
            open=float(r["open"]), high=float(r["high"]),
            low=float(r["low"]), close=float(r["close"]),
            volume=float(r.get("volume", 0.0)),
            closed=True,
        ))
    indicators = compute_indicators(bars_newest)
    frame_bars = tuple(bars_newest[:N_FRAME])
    frame_ind = IndicatorBundle(
        ema20=tuple(indicators.ema20[:N_FRAME]),
        atr14=tuple(indicators.atr14[:N_FRAME]),
    )
    return KlineFrame(
        symbol=symbol, timeframe="5m", bars=frame_bars,
        indicators=frame_ind,
        snapshot_ts_local_ms=int(time.time() * 1000),
    )


def make_assembler():
    from pa_agent.ai.prompt_assembler import PromptAssembler
    return PromptAssembler(REPO / "prompt_engineering")


def build_s1_messages(frame: KlineFrame) -> list[dict]:
    pa = make_assembler()
    return pa.build_stage1(frame, analysis_mode="original")


def build_s2_messages(frame: KlineFrame, s1_json: dict) -> tuple[list[dict], list[str]]:
    from pa_agent.ai.router import route_strategy_files
    pa = make_assembler()
    files = route_strategy_files(s1_json)
    msgs = pa.build_stage2(frame, s1_json, files, [],
                           decision_stance="conservative")
    return msgs, files


def validate(stage: str, raw_text: str, frame=None, s1_json=None):
    from pa_agent.ai.json_validator import JsonValidator, Ok
    v = JsonValidator()
    kw = {}
    if frame is not None:
        kw["kline_frame"] = frame
    if s1_json is not None:
        kw["stage1_json"] = s1_json
    res = v.validate(stage, raw_text, **kw)
    if isinstance(res, Ok):
        return True, res.obj, ""
    return False, None, f"[{res.category}] {res.message}"
