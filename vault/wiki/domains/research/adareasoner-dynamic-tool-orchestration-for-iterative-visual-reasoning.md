---
omni_type: compiled_wiki
domain: research
source_id: researchflow
source_path: obsidian-vault/analysis/ICLR_2026/AdaReasoner_Dynamic_Tool_Orchestration_for_Iterative_Visual_Reasoning.md
paper_link: https://openreview.net/forum?id=nUGPEmQ2ut
review_state: approved_after_proposal
---

# AdaReasoner: Dynamic Tool Orchestration for Iterative Visual Reasoning

## Source

- source_id: researchflow
- analysis_path: obsidian-vault/analysis/ICLR_2026/AdaReasoner_Dynamic_Tool_Orchestration_for_Iterative_Visual_Reasoning.md
- paper_link: https://openreview.net/forum?id=nUGPEmQ2ut

## Compiled Synthesis

通过将外部工具视为认知的主动延伸，并利用冷启动数据引导和强化学习优化，模型可以自主涌现出自适应工具行为——学会采纳有益工具、丢弃无关工具、调节使用频率——从而将性能瓶颈从模型自身规模转移到外部工具质量上。

## Claims

- `ef4568abc5603150` 通过将外部工具视为认知的主动延伸，并利用冷启动数据引导和强化学习优化，模型可以自主涌现出自适应工具行为——学会采纳有益工具、丢弃无关工具、调节使用频率——从而将性能瓶颈从模型自身规模转移到外部工具质量上。

## Evidence Excerpt

--- title: "AdaReasoner: Dynamic Tool Orchestration for Iterative Visual Reasoning" type: paper paper_level: A venue: ICLR year: 2026 pdf_ref: paperPDFs/ICLR_2026/AdaReasoner_Dynamic_Tool_Orchestration_for_Iterative_Visual_Reasoning.pdf aliases: - ADTOIVR acceptance: accepted openreview_forum_id: nUGPEmQ2ut tags: - topic/vision_multimodal_applications - topic/vision_multimodal_applications/vision_models_multimodal core_operator: 引入多轮动态工具编排机制：将工具增强推理形式化为状态-动作-观察序列决策过程，并辅以专门设计的数据管线（包含反思与工具失败案例）和适配多轮工具调用的工具GRPO强化学习算法，使模型能够自适应地选择、组合、弃用工具。 primary_logic: 通过冷启动阶段向模型植入正确的工具使用模式，再利用强化学习中的多轮奖励和自适应激励机制优化工具调用策略，模型能够自主发展出根据任务需求调整工具种类和使用频率的涌现行为，从而突破模型规模的限制，使小模型获得与大型专有模型匹敌甚至更优的性能。 claims: - AdaReasoner 为 7B 模型带来平均 +38.7% 的性能提升，在 VSP 上达到 97.6% 准确率，远超基线。 - 工具冷启动 (TC) 与工具 GRPO (TG) 的组合训练显著优于单独使用直接 SFT 或直接 GRPO，例如 7B 模型在 VSP 上提升 68.00 个百分点。 - 在冷启动数据中加入反思和回溯机制能大幅提升鲁棒性：当路径规划工具 A* 不可用时，含反思训练的模型性能为 91.36，而无反思训练仅为 67.27。 - 训练期间未见过的工具（A*）可在推理时被模型零样本采纳并正确调用（成功率 94.53%），且通过 RL 训练模型能掌握工具的应用场景，在导航任务上达到 96.33% 准确率。 paradigm: 通过冷启动阶段向模型植入正确的工具使用模式，再利用强化学习中的多轮奖励和自适应激励机制优化工具调用策略，模型能够自主发展出根据任务需求调整工具种类和使用频率的涌现行为，从而突破模型规模的限制，使小模型获得与大型专有模型匹敌甚至更优的性能。 --- # AdaReasoner: Dynamic Tool Orchestration for Iterative Visual R
