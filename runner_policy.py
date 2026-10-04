#!/usr/bin/env python3
"""实盘触发策略：screen PASS 之后，是否值得花一次完整模型。

定稿策略（Jeff 2026-10-03，选定 option D）：冷却 120 分钟 + 信号棒例外。

- screen PASS 后跑完整模型，然后进入冷却（120 分钟）。
- 冷却期内 screen 再 PASS：只有"新信号棒"能打破冷却。
  信号棒是离散、稀少的（回测一周约 8 根），且每根信号棒的 K1 都是当前 bar，
  天然互异——"第一次完整模型说不行、setup 演化后本来行"几乎必然对应一根
  新信号棒的出现，这个口子必须留。
- direction / AIL 的翻转不打破冷却：回测显示它们逐 bar 很"抖"，十几分钟
  翻一次，拿来当开关一周要跑 50+ 次。但这不等于永久漏掉——真正的 regime
  变化最多延迟到冷却超时（兜底重检）被覆盖，只是晚 ≤120 分钟看到，不是错过。
- 冷却超时后：兜底重检一次。

回测（9/28–10/2 五个交易日，9:30–16:00 ET）：BTC 14 次/周，TSLA 14 次/周，
均≤15。同一套函数给回测计数和实盘 runner 共用。
"""

from datetime import datetime


def should_trigger(state, ts, path_kinds, direction, ail, cooldown_min=120):
    """判断这次 screen PASS 是否值得跑完整模型。

    state: None 或 dict(time=datetime, path_kinds=set[str],
                         direction=str, ail=str)
    ts: 本次 PASS bar 的时间（datetime）
    path_kinds: 本次候选路径 kind 集合，如 {"p90p/long", "signal/long"}
    direction / ail: 本次 direction 与 always_in_branch（记入 state 备查，
        不直接作为打破冷却的条件）
    返回 (trigger: bool, reason: str)。调用方在 trigger=True 时更新 state。
    """
    kinds = set(path_kinds)
    if state is None:
        return True, "首次放行"
    if any(k.startswith("signal/") for k in kinds):
        # screen L4 已拦截连续信号棒，能走到这里的 signal 必是与上次不同根
        # 的新信号棒 → 打破冷却
        return True, "新信号棒打破冷却"
    if (ts - state["time"]).total_seconds() >= cooldown_min * 60:
        return True, f"冷却超时({cooldown_min}min)兜底重检"
    return False, "冷却期内且无新信号，去重"


def new_state(ts, path_kinds, direction, ail):
    return {"time": ts, "path_kinds": set(path_kinds),
            "direction": direction, "ail": ail}


def count_triggers(passes, cooldown_min=120):
    """对按时间排序的 PASS 记录计数。passes: list of dict(ts, paths, direction, ail)，
    ts 为 ISO 字符串。返回 (trigger_count, [(ts, reason)])。
    """
    state = None
    fired = []
    for p in passes:
        ts = p["ts"]
        tsd = ts if isinstance(ts, datetime) else datetime.fromisoformat(ts)
        kinds = {x.split(":")[0] for x in p["paths"]}
        ok, reason = should_trigger(state, tsd, kinds, p["direction"],
                                    p["ail"], cooldown_min)
        if ok:
            fired.append((p["ts"], reason))
            state = new_state(tsd, kinds, p["direction"], p["ail"])
    return len(fired), fired
