# FIDELITY_LLM：本版与原版的差异清单

## 唯一实质差异：LLM 身份

- 原版：DeepSeek（博主配置的模型，多为 deepseek-chat / deepseek-reasoner）。
- 本版：Meta AI（Muse 系列子代理）。
- prompt 文本、schema、路由、校验、指标**全部逐字/逐逻辑复用原仓库**，
  因此同一 prompt 下输出差异只来自模型本身的判断差异。

## 已知的非 prompt 差异

1. **KlineFrame 构造**：等价重写（非直接调用 `build_analysis_frame`，
   因其依赖数据源 plumbing）。语义一致：100 根最新已收盘 K 线，
   newest-first，seq=1 为 K1（信号棒）；指标用原 `compute_indicators`
   在 150 根（100+50 预热）上计算。
2. **编排**：原 `two_stage.py`（GUI/流式/重试/账本）→ 极简批处理。
   不影响 prompt 内容与单次调用语义。
3. **experience_entries**：传空列表（与无经验记录时的原版一致）。
4. **decision_stance**：固定 "conservative"（原版默认）。
5. **analysis_mode**：固定 "original"（原版默认）。
6. **S2 订单提取**（backtest_llm.py 的 extract_order）：回测用的字段映射，
   不属于模型本身；模型输出的原始 JSON 原样保存在 s2_validated.json。

## 验证

- S1/S2 prompt 与原 PromptAssembler 输出逐字节一致（直接调用，无转写）。
- 冻结前已验证：81 个 S1 prompt 全部成功组装（约 2.9 万 tokens/个）。
