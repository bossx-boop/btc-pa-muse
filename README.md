# BTC PA Screen + LLM Analysis（Muse 适配版）

BTC 5 分钟线的 PA（Price Action）模型实盘筛选 + 完整大模型分析，跑在 Meta AI（Muse）上。

## 原作者标注

本仓库是**适配层**，核心模型来自开源项目：

- **原项目**：PA Agent — AI K 线分析辅助工具（桌面端）
- **原作者**：PA Agent Team（社区开源项目）
- **原仓库**：https://github.com/rosemarycox5334-debug/PA_Agent.git
- **原 License**：AGPL-3.0（本仓库同样采用 AGPL-3.0，见 LICENSE）
- **复用的原项目模块**（未改写，保证 prompt 逐字一致）：
  - `pa_agent.ai.prompt_assembler.PromptAssembler` — Stage1/Stage2 prompt 组装
  - `pa_agent.ai.router.route_strategy_files` — Stage2 策略文件路由
  - `pa_agent.ai.json_validator.JsonValidator` — 模型输出 JSON 校验
  - `pa_agent.data.snapshot.compute_indicators` — EMA20/ATR14 指标
  - `screen.py` 另复用原项目的 `judge_*` 决策节点与 K 线几何/市场特征函数

## 为什么做这个适配

1. **省 token**：原模型每个信号的完整分析约 6 万 tokens（Stage1 约 2.9 万 + Stage2 约 2.8 万）。本仓库先跑**零 token 确定性预筛**（`screen.py`，7 层规则门 L0–L6），只把可能成交的信号送去跑完整模型。实测 BTC 一周 390 根 bar 只筛出 81 个 PASS，再经触发策略压缩到 15 次完整分析。
2. **换 LLM 执行**：原项目用 DeepSeek API；本仓库把 prompt 原样不动，执行换成 Meta AI（Muse 子代理）。除 LLM 身份外，prompt 文本、路由逻辑、schema、校验规则、指标算法全部保持原样。
3. **只做多**：预筛默认 `long_only=True`（原策略 §14 本来也限制只做多）。

## 文件结构

```
screen.py               零 token 确定性预筛（7 层门：LT 时段门/L0 数据门/L1 混乱门/
                        L2 方向门/L3 尖峰门/L4 信号门/L5 计划单门/L6 硬禁令+止损门）
runner_policy.py        触发策略 Option D：120 分钟冷却 + 新信号棒例外
market_data.py          K 线加载（tz-aware、复权、gap 保护）
pa_model_llm/
  harness.py            核心：调原项目 PromptAssembler/路由/校验，构建 S1/S2 prompt
  live/analyze_trigger 用的就是这组函数（build_s1_messages / build_s2_messages / validate）
  make_prompts.py / make_s2.py / backtest_llm.py
                        一次性历史回放批处理（需要本地数据，见下）
  README.md / FIDELITY_LLM.md
                        冻结版说明与保真度记录
live/
  live_btc_screen.py    实盘 screening：每 5 分钟拉 yfinance BTC-USD 5m，
                        周一至周五 09:30–16:00 ET 对新收盘 bar 跑 screen，
                        PASS 后按 Option D 写 pending trigger
  analyze_trigger.py    单次触发的编排 helper：build-s1 →（Muse 子代理跑 S1）→
                        build-s2 →（Muse 子代理跑 S2）→ decide 输出决策 JSON
```

## 依赖与安装

```bash
# 1. 克隆本仓库后，把原 PA Agent 仓库放到 third_party/PA_Agent
#    （或设环境变量 PA_AGENT_DIR 指向它）
git clone https://github.com/rosemarycox5334-debug/PA_Agent.git third_party/PA_Agent

# 2. 安装依赖
pip install -r requirements.txt
```

## 使用

```bash
# 实盘 screening（单次运行；生产环境用 cron 每 5 分钟跑）
python live/live_btc_screen.py

# 单次触发的完整分析（LLM 部分由 Muse 子代理执行，见 analyze_trigger.py --help）
python live/analyze_trigger.py build-s1 --ts "2026-10-06T10:35:00-04:00" --dir live/runs/20261006_1035
# ... 子代理执行 S1，回填 s1_reply.txt ...
python live/analyze_trigger.py build-s2 --dir live/runs/20261006_1035
# ... 子代理执行 S2，回填 s2_reply.txt ...
python live/analyze_trigger.py decide --dir live/runs/20261006_1035
```

## License

AGPL-3.0（见 LICENSE）。本仓库代码依赖 AGPL-3.0 的原项目，
按 AGPL 要求以相同许可证发布。
