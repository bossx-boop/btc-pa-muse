#!/usr/bin/env python3
"""PA_Agent 确定性预筛 (screen): 纯数学、零 token。

LT 时段门 → L0 数据门 (§1.1) → L1 混乱门 (§1.3) → L2 方向门 (§2.3/§2.4，只记录不拦)
→ L3 尖峰门 (新鲜尖峰追单禁令 / 尖峰回撤放行) → L4 信号门 (§9.0)
→ L5 计划单门 (§9.0P) → L6 硬禁令+止损门 (§14/§10.1/§10.2)

原则：拿不准的一律放行（保召回）；拦下来的都是大模型也做不了单的情形。
复用仓库原函数：judge_*（decision_nodes）、几何特征（kline_features）、
市场特征（market_features）。不调用任何大模型。
"""
import sys
import types
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

# --- PyQt6 stub (repo GUI import chain)，pa_run.py 已做，此处幂等 ---
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

BULL_TYPES = ("trend_bull", "outside_bull")
BEAR_TYPES = ("trend_bear", "outside_bear")

# L4/L6 共用：信号棒过大阈值（对齐 §10.2 止损>2倍ATR 即过大）
_MAX_SIG_RANGE_ATR = 2.0
# L5：§9.0P 边界 proximity（对齐提示块 proximity≈0.35×ATR，此处略宽松取 0.8）
_P90P_PROX_ATR = 0.5       # 计划单：价格距边界/支撑阻力的最大距离（ATR 倍数）
_P90P_MIN_RANGE_ATR = 2.0  # 计划单：区间宽度下限（ATR 倍数），太窄的区间全是 chop
_MIN_SIG_RANGE_ATR = 0.5   # 信号棒：最小实体（ATR 倍数），太小的棒子不构成可交易信号
_MIN_SIG_CLOSE_POS = 0.70  # 信号棒：收盘位置下限
# L6：§10.2 区间态止损过滤器（忠实原规则：止损>区间宽度10% 视为过大）
_RANGE_STOP_PCT = 0.10

# LT 时段门（Jeff 2026-10-03）：分标的禁入时段，落在其中直接 BLOCK。
# TSLA：禁开盘前 30 分钟 [9:30,10:00)（隔夜跳空后 ATR/EMA 未消化）；
#       禁收盘前最后 3 根 bar [15:45,16:00)（没时间给交易发展）。
# BTC-USD：只禁收盘前最后 2 根 bar [15:50,16:00)；开盘不禁
#       （BTC 24h 连续，frame 用连续历史，无跳空要消化）。
_SESSION_SKIP = {
    "TSLA": [(dtime(9, 30), dtime(10, 0)), (dtime(15, 45), dtime(16, 0))],
    "BTCUSD": [(dtime(15, 50), dtime(16, 0))],
}


def _session_skip_hit(symbol, k1_time):
    key = (symbol or "").upper().replace("-", "").replace("/", "")
    for start, end in _SESSION_SKIP.get(key, []):
        if start <= k1_time < end:
            return start, end
    return None


def _layer(layers, lid, name, passed, detail):
    layers.append({"id": lid, "name": name,
                   "result": "pass" if passed else "block", "detail": detail})
    return passed


def _facts(frame, feats, f, direction, f23, f24):
    b1 = frame.bars[0]
    g1 = feats[0]
    atr = frame.indicators.atr14[0]
    ema = frame.indicators.ema20[0]
    return {
        "symbol": frame.symbol,
        "k1_close": round(b1.close, 4),
        "atr14": round(atr, 4),
        "ema20": round(ema, 4),
        "direction": direction,
        "direction_reason": (f23.reason or "")[:200],
        "always_in_answer": f24.answer,
        "always_in_branch": f24.branch or "-",
        "k1_bar_type": g1.bar_type,
        "k1_close_position": g1.close_position,
        "k1_range_atr": round(g1.range_atr_ratio, 3) if g1.range_atr_ratio else None,
        "k1_ema_gap_count": g1.ema_gap_count,
        "price_position": round(f.price_position, 3) if f.price_position is not None else None,
        "zone": f.zone,
        "supports": [round(x, 2) for x in (f.supports or [])[:3]],
        "resistances": [round(x, 2) for x in (f.resistances or [])[:3]],
        "invalidation_long": f.invalidation_long,
        "invalidation_short": f.invalidation_short,
        "breakout_quality": f.breakout_quality,
        "barbwire_score": round(f.barbwire_score, 3),
        "barbwire_candidate": bool(f.barbwire_candidate),
        "hl_bull_count": f.hl_count.bull_count,
        "hl_bear_count": f.hl_count.bear_count,
        "spike_aftermath_hint": f.spike_aftermath_hint,
        "range_high": f.range_high,
        "range_low": f.range_low,
    }


def _fresh_spike(bars, feats):
    """新鲜尖峰（K1/K2 连续同向强趋势棒，实体重叠<0.30，创新极值）。

    对齐 brief 里的「最低可路由尖峰」标准。这种情形追单被硬禁令禁止，
    且尚无回撤，跑完整模型也只会 wait。
    """
    g1, g2 = feats[0], feats[1]
    b1, b2 = bars[0], bars[1]
    for bull, types in ((True, BULL_TYPES), (False, BEAR_TYPES)):
        if g1.bar_type in types and g2.bar_type in types:
            ov = g1.overlap_prev_ratio
            r1 = g1.range_atr_ratio or 0
            newx = (b1.close > b2.close) if bull else (b1.close < b2.close)
            if ov is not None and ov < 0.30 and newx and r1 >= 1.0:
                return True, ("bull" if bull else "bear")
    return False, None


def _spike_run_direction(bars, atr):
    """复刻 repo _spike_aftermath_hint 的同向强趋势 run 扫描，仅取方向。

    阈值与 repo 一致：实体 ≥ 0.5×ATR、同向连续 ≥ 3 根（_SPIKE_MIN_RUN），
    取最近一个 run。返回 'bull' / 'bear' / None。
    只做多模式下，bear 尖峰的回撤只会形成做空侧 SPS，做多无订单可能 → BLOCK。
    """
    if not atr or atr <= 0:
        return None
    chrono = list(reversed(bars[:16]))  # _SPIKE_LOOKBACK
    best_d = None
    i = 0
    while i < len(chrono):
        o, c = float(chrono[i].open), float(chrono[i].close)
        d = 1 if c > o else (-1 if c < o else 0)
        if d == 0 or abs(c - o) < 0.5 * atr:
            i += 1
            continue
        run = [i]
        j = i + 1
        while j < len(chrono):
            oj, cj = float(chrono[j].open), float(chrono[j].close)
            dj = 1 if cj > oj else (-1 if cj < oj else 0)
            if dj != d or abs(cj - oj) < 0.35 * atr:
                break
            run.append(j)
            j += 1
        if len(run) >= 3:
            best_d = d
        i = j if j > i + 1 else i + 1
    if best_d is None:
        return None
    return "bull" if best_d > 0 else "bear"


def _signal_candidates(g1, g2, direction, ail, ais, long_only=False):
    """§9.0 信号路径候选。返回 [(note, side)]。只做多模式下只看多头信号。

    收紧（2026-10-03，Jeff：放行太多、token 不够，不用太宽）：
      - 收盘位置 0.65 → 0.70
      - 信号棒不能太小：range ≥ 0.5×ATR（太小的棒子完整模型也不会下单）
      - 不连续注册：上一根已是同等强度看涨信号时跳过（一波只放行一次，追单嫌疑）
    """
    out = []
    r = g1.range_atr_ratio or 0
    if r > _MAX_SIG_RANGE_ATR:
        return out  # 信号棒过长，§10.2 必毙，不注册
    if r < _MIN_SIG_RANGE_ATR:
        return out  # 棒子太小，不构成可交易信号
    cp = g1.close_position
    if g1.bar_type in BULL_TYPES and cp is not None and cp >= _MIN_SIG_CLOSE_POS:
        if g2 is not None and g2.bar_type in BULL_TYPES \
                and (g2.close_position or 0) >= _MIN_SIG_CLOSE_POS:
            pass  # 上一根已是同等看涨信号，不连续注册
        elif direction != "bearish" and not ais:
            out.append(("K1 看涨信号棒候选", "long"))
    if not long_only and g1.bar_type in BEAR_TYPES and cp is not None and cp <= 0.35:
        if direction != "bullish" and not ail:
            out.append(("K1 看跌信号棒候选", "short"))
    return out


def _p90p_candidates(f, direction, ail, ais, close, atr, long_only=False):
    """§9.0P 计划单路径候选。返回 ([(note, side)], trace_detail)。

    只做多模式下只评估下沿/支撑做多一侧。

    收紧（2026-10-03，Jeff：放行太多、token 不够，不用太宽）：
      - proximity 0.8 → 0.5 ATR（挂单离边界太远不是真计划单）
      - 去掉 zone 下沿/上沿的 bypass：必须真实贴着结构支撑（sup_d≤0.5）
        或区间包络下沿（dist_to_low_atr≤0.5），不能只凭"处在下三分之一"
      - 要求区间宽度 ≥ 2 ATR（太窄的区间全是 chop，模型不会下单）
      - 方向门以 prompt §9.0P 为准（stage1_prompt_1002.md L974-982）：
        做多侧只允许顺 direction（bullish）或顺 AIL；
        neutral 且 §2.4 非 AIL → §9.0P 默认 wait，不注册
        （bull 趋势里的顺势回撤位本身就是合法 §9.0P 场景，不砍）
    """
    out = []
    zone = f.zone or ""
    sup = (f.supports or [None])[0]
    res = (f.resistances or [None])[0]
    sup_d = (close - sup) / atr if sup is not None and atr else None
    res_d = (res - close) / atr if res is not None and atr else None
    env_low_d = f.dist_to_low_atr
    env_high_d = f.dist_to_high_atr
    near_low = (sup_d is not None and 0 <= sup_d <= _P90P_PROX_ATR) or \
               (env_low_d is not None and 0 <= env_low_d <= _P90P_PROX_ATR)
    near_high = (res_d is not None and 0 <= res_d <= _P90P_PROX_ATR) or \
                (env_high_d is not None and 0 <= env_high_d <= _P90P_PROX_ATR)
    width_atr = f.range_width_atr
    range_ok = width_atr is not None and width_atr >= _P90P_MIN_RANGE_ATR
    detail = (f"zone={zone} 近下沿={near_low} 近上沿={near_high} "
              f"direction={direction} AIL={ail} AIS={ais} "
              f"区间宽={width_atr}ATR{'✓' if range_ok else '✗'}")
    if bool(f.barbwire_candidate) and zone == "middle_third":
        return out, detail + "；铁丝网中部，无计划单路径"
    if near_low and range_ok and (direction == "bullish" or ail) \
            and f.invalidation_long is not None:
        out.append(("下沿/支撑做多计划单", "long"))
    if not long_only and near_high and range_ok and (direction == "bearish" or ais) \
            and f.invalidation_short is not None:
        out.append(("上沿/阻力做空计划单", "short"))
    if not out:
        detail += "；无符合方向/边界/止损锚点的计划单路径"
    return out, detail


def _vet_paths(paths, f, g1, bars, direction, ail, ais, close, atr):
    """L6：硬禁令 + §10.1/§10.2 止损 sanity。返回 (kept, killed_reasons)。"""
    kept, killed = [], []
    b1 = bars[0]
    width = None
    if f.range_high is not None and f.range_low is not None:
        width = f.range_high - f.range_low

    # 铁丝网中部：一切路径毙掉
    if bool(f.barbwire_candidate) and (f.zone or "") == "middle_third":
        return [], ["铁丝网中部：禁止一切路径"]

    up = ail or direction == "bullish"
    dn = ais or direction == "bearish"
    ema_gap = g1.ema_gap_count or 0

    for kind, side, note in paths:
        # 20GB 趋势中逆势
        if ema_gap >= 20:
            if (side == "long" and dn and not up) or (side == "short" and up and not dn):
                killed.append(f"{note}：20GB 趋势中逆势，毙")
                continue
        # 止损距离 proxy（§10.1 须有锚点；§10.2 不过大）
        if kind == "signal":
            d = (close - b1.low) if side == "long" else (b1.high - close)
        else:
            inv = f.invalidation_long if side == "long" else f.invalidation_short
            if inv is None:
                killed.append(f"{note}：无结构止损锚点，毙")
                continue
            d = abs(close - inv)
        if d / atr > 2.0:
            killed.append(f"{note}：止损距离 {d / atr:.2f}×ATR > 2，§10.2 必毙")
            continue
        if width and width > 0 and d > _RANGE_STOP_PCT * width:
            killed.append(f"{note}：止损 {d:.2f} 超区间宽度 {_RANGE_STOP_PCT:.0%}"
                          f"（{width:.2f}），§10.2 必毙")
            continue
        kept.append((kind, side, note))
    return kept, killed


def _done(frame, verdict, layers, paths, feats, f, dj):
    direction, f23, f24 = dj if dj else ("?", None, None)
    facts = _facts(frame, feats, f, direction, f23, f24) if feats else {}
    return {
        "symbol": frame.symbol,
        "verdict": verdict,
        "layers": layers,
        "paths": [{"kind": k, "side": s, "note": n} for k, s, n in paths],
        "facts": facts,
    }


def run_screen(frame, *, long_only=True):
    """对 frame 跑 7 层确定性预筛。返回 verdict 字典（可 JSON 序列化）。

    long_only=True（默认；Jeff 只做多头）：剪掉全部空头分支，并在 L2 加两道提前门：
      - §2.4 branch=AIS → BLOCK（做多为逆 Always In，原规则§14 禁止）
      - direction=bearish 且非 AIL → BLOCK（做多为逆势，无做多订单可能）
    这两类在通用模式下也会因无多头路径而被毙，只做多模式下提前到 L2 拦，
    减少误放行（= 减少完整模型次数）。long_only=False 时恢复通用双向版。
    """
    from pa_agent.ai.decision_nodes import (
        judge_data_sufficiency, judge_market_chaos, judge_direction, judge_always_in,
    )
    from pa_agent.ai.kline_features import compute_kline_geometry_features
    from pa_agent.ai.market_features import compute_simple_market_features

    bars = frame.bars
    atr = frame.indicators.atr14[0]
    close = bars[0].close
    layers, paths = [], []

    # LT 时段门（最先检查）：落在标的禁入时段直接 BLOCK，不跑完整模型
    k1_et = datetime.fromtimestamp(bars[0].ts_open / 1000,
                                   tz=ZoneInfo("America/New_York")).time()
    hit = _session_skip_hit(frame.symbol, k1_et)
    if hit:
        _layer(layers, "LT", "时段门", False,
               f"{frame.symbol} K1 {k1_et.strftime('%H:%M')} 落在禁入时段 "
               f"[{hit[0].strftime('%H:%M')},{hit[1].strftime('%H:%M')})，不跑完整模型")
        return _done(frame, "BLOCK", layers, paths, None, None, None)
    _layer(layers, "LT", "时段门", True,
           f"{frame.symbol} K1 {k1_et.strftime('%H:%M')} 不在禁入时段")

    # L0 数据门
    f11 = judge_data_sufficiency(frame)
    if not _layer(layers, "L0", "数据门§1.1", f11.answer == "是", f11.reason):
        return _done(frame, "BLOCK", layers, paths, None, None, None)

    # L1 混乱门（仓库当前实现恒为否，保留）
    f13 = judge_market_chaos(frame)
    if not _layer(layers, "L1", "混乱门§1.3", f13.answer != "是", f13.reason):
        return _done(frame, "BLOCK", layers, paths, None, None, None)

    # L2 方向门（通用模式：只记录不拦；只做多模式：加两道提前门）
    direction, f23 = judge_direction(frame)
    f24 = judge_always_in(frame)
    ail = (f24.branch == "AIL")
    ais = (f24.branch == "AIS")

    feats = compute_kline_geometry_features(frame)
    f = compute_simple_market_features(frame)
    g1 = feats[0]
    dj = (direction, f23, f24)
    l2_name = "方向门§2.3/§2.4" + ("[只做多]" if long_only else "")
    if long_only:
        if ais:
            _layer(layers, "L2", l2_name, False,
                   f"direction={direction}；§2.4 branch=AIS：做多为逆 Always In，"
                   f"原规则§14 禁止一切逆势 → 只做多模式下无订单可能")
            return _done(frame, "BLOCK", layers, paths, feats, f, dj)
        if direction == "bearish" and not ail:
            _layer(layers, "L2", l2_name, False,
                   f"direction=bearish 且非 AIL：§9.0 信号方向不一致、§9.0P "
                   f"做多侧不满足、反转仅诊断 → 只做多模式下无订单可能")
            return _done(frame, "BLOCK", layers, paths, feats, f, dj)
    _layer(layers, "L2", l2_name, True,
           f"direction={direction}；§2.4 {f24.answer} branch={f24.branch or '-'}"
           f"（{(f24.reason or '')[:90]}）［本层只记录不拦］")

    # L3 尖峰门
    fresh, fdir = _fresh_spike(bars, feats)
    hint = f.spike_aftermath_hint
    if fresh:
        _layer(layers, "L3", "尖峰门", False,
               f"新鲜{fdir}尖峰延续中（K1/K2 强趋势棒重叠<0.30）：追单禁令，无回撤")
        return _done(frame, "BLOCK", layers, paths, feats, f, dj)
    if hint == "pullback":
        if long_only:
            sdir = _spike_run_direction(bars, atr)
            if sdir == "bear":
                _layer(layers, "L3", "尖峰门[只做多]", False,
                       "尖峰回撤中，但尖峰方向为 bear：SPS 只会形成做空侧，"
                       "只做多无订单可能")
                return _done(frame, "BLOCK", layers, paths, feats, f, dj)
            # 收紧（2026-10-03）：SPS 做多是顺势交易，要求 bullish/AIL 环境；
            # 且 K1 不能已经是 bear 棒（回撤转反转，模型不会买）
            if not (direction == "bullish" or ail):
                _layer(layers, "L3", "尖峰门[只做多]", False,
                       f"bull 尖峰回撤但 direction={direction} 非 bullish/AIL："
                       f"顺势上下文不足，完整模型极少下单")
                return _done(frame, "BLOCK", layers, paths, feats, f, dj)
            if g1.bar_type in BEAR_TYPES:
                _layer(layers, "L3", "尖峰门[只做多]", False,
                       "bull 尖峰回撤但 K1 已是 bear 棒：回撤可能转反转")
                return _done(frame, "BLOCK", layers, paths, feats, f, dj)
            _layer(layers, "L3", "尖峰门[只做多]", True,
                   f"bull 尖峰回撤中（run 方向={sdir}，direction={direction}）："
                   f"可能形成 SPS 做多，值得跑完整模型")
            return _done(frame, "PASS", layers,
                          [("spike_pullback", "long", "尖峰回撤 SPS 做多候选")],
                          feats, f, dj)
        _layer(layers, "L3", "尖峰门", True, "尖峰回撤中：可能形成 SPS，值得跑完整模型")
        return _done(frame, "PASS", layers,
                      [("spike_pullback", "both", "尖峰回撤 SPS 候选")], feats, f, dj)
    if hint == "sticky_reversal_risk":
        _layer(layers, "L3", "尖峰门", False, "粘性反转风险：反转仅诊断，不产出订单")
        return _done(frame, "BLOCK", layers, paths, feats, f, dj)
    _layer(layers, "L3", "尖峰门", True, f"无新鲜尖峰（hint={hint}）")

    # L4 信号门（§9.0）：只注册候选，不直接判
    g2 = feats[1] if len(feats) > 1 else None
    for note, side in _signal_candidates(g1, g2, direction, ail, ais,
                                         long_only=long_only):
        paths.append(("signal", side, note))
    sig_desc = "、".join(f"{n}({s})" for _, s, n in paths if _ == "signal") or "无"
    _layer(layers, "L4", "信号门§9.0", True, f"信号路径候选：{sig_desc}")

    # L5 计划单门（§9.0P）：只注册候选，不直接判
    p90p, p90p_detail = _p90p_candidates(f, direction, ail, ais, close, atr,
                                         long_only=long_only)
    for note, side in p90p:
        paths.append(("p90p", side, note))
    _layer(layers, "L5", "计划单门§9.0P", True, p90p_detail)

    # L6 硬禁令 + §10.1/§10.2
    kept, killed = _vet_paths(paths, f, g1, bars, direction, ail, ais, close, atr)
    if kept:
        _layer(layers, "L6", "硬禁令+止损门§14/§10", True,
               "存活路径：" + "、".join(f"{n}({s})" for _, s, n in kept)
               + ("；毙掉：" + "、".join(killed) if killed else ""))
        return _done(frame, "PASS", layers, kept, feats, f, dj)
    _layer(layers, "L6", "硬禁令+止损门§14/§10", False,
           "全部路径被毙：" + "、".join(killed) if killed else "无候选路径")
    return _done(frame, "BLOCK", layers, [], feats, f, dj)
