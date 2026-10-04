# pa_model_llm：原 prompt + Meta AI 完整版

本目录是博主 PA Agent 的**完整大模型版**冻结实现。

## 与原版的关系

| 项目 | 原版（博主） | 本版 |
|---|---|---|
| Stage1/Stage2 prompt 文本 | prompt_engineering/*.txt + prompt_assembler.py 组装 | **完全相同**，直接调用原 `PromptAssembler` |
| 策略文件路由 | `route_strategy_files` | **完全相同**，直接调用 |
| JSON 校验 | `JsonValidator` + STAGE1/2_SCHEMA | **完全相同**，直接调用 |
| EMA20/ATR14 | `compute_indicators`（50 根预热） | **完全相同**，直接调用 |
| K 线 frame | `build_analysis_frame`（100 根已收盘，K1=信号棒） | 等价实现（见 harness.py） |
| LLM | DeepSeek API | **Meta AI（Muse 子代理）** ← 唯一不同 |
| GUI/编排 | two_stage.py + PyQt | 极简批处理（make_prompts/make_s2/backtest_llm） |

## 设计原则

prompt 相关的**每一行代码都不重写**，全部直接 import 原仓库模块。
本包只做三件事：造 KlineFrame、调 PromptAssembler、调 JsonValidator。
凡是原仓库能做的，一律用原仓库的。

## 运行流程

```
python3 make_prompts.py   # 81 信号 → runs/<n>/s1.json（S1 完整消息）
# 子代理（Meta AI）逐个执行 S1 prompt，结果写入 runs/<n>/s1_reply.txt
python3 make_s2.py         # 校验 S1 → 路由策略文件 → runs/<n>/s2.json
# 子代理逐个执行 S2 prompt，结果写入 runs/<n>/s2_reply.txt
python3 backtest_llm.py    # 校验 S2 → 提取订单 → 后续 K 线回测 → llm_backtest_results.json
```

## 子代理执行规范（Meta AI 代替 DeepSeek）

子代理收到的任务：
1. 读取 runs/<n>/s1.json（或 s2.json）中的 system + user 消息
2. **严格按 prompt 要求执行**，prompt 内"禁止调用任何工具"同样适用
3. 只输出裸 JSON（S1→STAGE1_SCHEMA，S2→STAGE2_SCHEMA），不写解释
4. 回复写入 runs/<n>/s1_reply.txt（或 s2_reply.txt）

校验失败时最多重试 2 次，重试时附上校验错误信息。
