---
omni_type: compiled_wiki
domain: research
source_id: researchflow
source_path: obsidian-vault/analysis/ICLR_2026/Adaptive_Rollout_Allocation_for_Online_Reinforcement_Learning_with_Verifiable_Rewards.md
paper_link: https://openreview.net/forum?id=Z5sWYACAop
review_state: approved
---

# Adaptive Rollout Allocation for Online Reinforcement Learning with Verifiable Rewards

## Source

- source_id: researchflow
- analysis_path: obsidian-vault/analysis/ICLR_2026/Adaptive_Rollout_Allocation_for_Online_Reinforcement_Learning_with_Verifiable_Rewards.md
- paper_link: https://openreview.net/forum?id=Z5sWYACAop

## Compiled Synthesis

梯度方差与提示的成功概率 p 之间存在解析关系（对于 Dr. GRPO 和 RLOO 分别为 Var ∝ (n-1)/n² · p(1-p) 和 Var ∝ 1/(n-1) · p(1-p)），因此可以通过预测 p 来估计每个提示的预期梯度方差，然后求解一个凸优化问题来分配展开预算，以最小化总梯度方差。

## Claims

- `44bbff63ecf4e82e` 梯度方差与提示的成功概率 p 之间存在解析关系（对于 Dr. GRPO 和 RLOO 分别为 Var ∝ (n-1)/n² · p(1-p) 和 Var ∝ 1/(n-1) · p(1-p)），因此可以通过预测 p 来估计每个提示的预期梯度方差，然后求解一个凸优化问题来分配展开预算，以最小化总梯度方差。

## Evidence Excerpt

--- title: "Adaptive Rollout Allocation for Online Reinforcement Learning with Verifiable Rewards" type: paper paper_level: A venue: ICLR year: 2026 pdf_ref: paperPDFs/ICLR_2026/Adaptive_Rollout_Allocation_for_Online_Reinforcement_Learning_with_Verifiable_Rewards.pdf aliases: - VVIPAS - ARAORLVR acceptance: accepted openreview_forum_id: Z5sWYACAop tags: - topic/reinforcement_learning_planning_agents - topic/reinforcement_learning_planning_agents/deep_rl core_operator: 基于高斯过程预测每个提示的成功概率，动态计算和最小化梯度方差，从而自适应地分配推广次数，将计算预算集中在具有最大信息增益的提示上。 primary_logic: 通过理论分析揭示梯度方差与提示成功概率 p 的函数关系，利用高斯过程在嵌入空间中对 p 进行在线预测，并将分配问题形式化为一个凸优化问题，可在总预算约束下精确求解并取整，从而显著提升采样效率和最终模型性能。 claims: - VIP 持续超越基于均匀或启发式分配的所有基线方法，在 AIME24/25 上提升显著（例如 RLOO+VIP 的 Pass@32 从 18.29% 提高到 30.55%）。 - 在 Bamboogle 和 MuSiQue 工具增强推理任务上，Dr. GRPO+VIP 和 RLOO+VIP 均一致提高 EM、F1@5 和 Precision@5。 - 高斯过程预测器的成功概率 MAE 始终低于移动平均和岭回归基线。 - VIP 的额外计算开销极小（1.5B 模型 1.12%，7B 模型 0.83%）。 paradigm: 通过理论分析揭示梯度方差与提示成功概率 p 的函数关系，利用高斯过程在嵌入空间中对 p 进行在线预测，并将分配问题形式化为一个凸优化问题，可在总预算约束下精确求解并取整，从而显著提升采样效率和最终模型性能。 --- # Adaptive Rollout Allocation for Online Reinforcement Learning with Verifiable Rewards > [!tip] 核心洞察 > 通过理论分析揭示梯度方差与提示成功概率 p 的函数关系，利用高斯过程在嵌入空间中对 p 进行在线预测，并将分配问
