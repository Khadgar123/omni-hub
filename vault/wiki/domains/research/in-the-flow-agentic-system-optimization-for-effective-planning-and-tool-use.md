---
omni_type: compiled_wiki
domain: research
source_id: researchflow
source_path: obsidian-vault/analysis/ICLR_2026/In-the-Flow_Agentic_System_Optimization_for_Effective_Planning_and_Tool_Use.md
paper_link: https://openreview.net/forum?id=Mf5AleTUVK
review_state: approved
---

# In-the-Flow Agentic System Optimization for Effective Planning and Tool Use

## Source

- source_id: researchflow
- analysis_path: obsidian-vault/analysis/ICLR_2026/In-the-Flow_Agentic_System_Optimization_for_Effective_Planning_and_Tool_Use.md
- paper_link: https://openreview.net/forum?id=Mf5AleTUVK

## Compiled Synthesis

通过将多轮交互分解为共享全局成功信号的独立单步优化，配合组归一化降低方差，使智能体系统能够在仅依靠稀疏最终奖励的情况下，高效学习长程规划与工具协调策略。

## Claims

- `521d383741633b40` Flow-GRPO 将轨迹级最终奖励广播至每个推理步骤，把多轮强化学习转化为一系列可处理的单步更新，结合组归一化优势实现稳定的在线信用分配，从而在智能体流程中直接优化规划器。
- `489ee9823d076b04` 通过将多轮交互分解为共享全局成功信号的独立单步优化，配合组归一化降低方差，使智能体系统能够在仅依靠稀疏最终奖励的情况下，高效学习长程规划与工具协调策略。

## Evidence Excerpt

--- title: In-the-Flow Agentic System Optimization for Effective Planning and Tool Use type: paper paper_level: A venue: ICLR year: 2026 pdf_ref: paperPDFs/ICLR_2026/In-the-Flow_Agentic_System_Optimization_for_Effective_Planning_and_Tool_Use.pdf aliases: - AFG - FASOEPTU acceptance: accepted tags: - topic/reinforcement_learning_planning_agents - topic/reinforcement_learning_planning_agents/multi_agent openreview_forum_id: Mf5AleTUVK core_operator: 采用模块化智能体系统，并将 planner 模块置于系统循环中在线优化，通过将轨迹级稀疏奖励广播到每一轮，实现有效的多轮信用分配。 primary_logic: 将多轮强化学习问题转化为一系列单轮策略更新：在每一轮，planner 基于完整的记忆上下文接收相同的全局成功信号，利用组标准化优势稳定训练，从而从稀疏反馈中学习有效的长程策略。 claims: - Flow-GRPO 通过广播单一可验证的轨迹级奖励到每一轮，将多轮 RL 转化为单轮更新。 - 组标准化优势减少方差，增强信用分配。 - 单轮更新的等价性及单调改进保证。 - 在线 Flow-GRPO 大幅超越离线 SFT 和冻结 planner。 paradigm: 将多轮强化学习问题转化为一系列单轮策略更新：在每一轮，planner 基于完整的记忆上下文接收相同的全局成功信号，利用组标准化优势稳定训练，从而从稀疏反馈中学习有效的长程策略。 --- # In-the-Flow Agentic System Optimization for Effective Planning and Tool Use > [!tip] 核心洞察 > 将多轮强化学习问题转化为一系列单轮策略更新：在每一轮，planner 基于完整的记忆上下文接收相同的全局成功信号，利用组标准化优势稳定训练，从而从稀疏反馈中学习有效的长程策略。 | 字段 | 内容 | |------|------| | 中文题名 | 在线智能体系统优化以实现有效的规划与工具使用 | | 英文题名 | In-the-Flow Agentic System Optimization for Effective Planning and Tool Use | | 会议/期刊
