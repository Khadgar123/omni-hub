---
omni_type: compiled_wiki
domain: research
source_id: researchflow
source_path: obsidian-vault/analysis/ICLR_2026/A.I.R._Enabling_Adaptive_Iterative_and_Reasoning-based_Frame_Selection_For_Video_Question_Answering.md
paper_link: https://openreview.net/forum?id=SZVpOKw0YD
review_state: approved
---

# A.I.R.: Enabling Adaptive, Iterative, and Reasoning-based Frame Selection For Video Question Answering

## Source

- source_id: researchflow
- analysis_path: obsidian-vault/analysis/ICLR_2026/A.I.R._Enabling_Adaptive_Iterative_and_Reasoning-based_Frame_Selection_For_Video_Question_Answering.md
- paper_link: https://openreview.net/forum?id=SZVpOKw0YD

## Compiled Synthesis

利用强大的VLM进行深度语义分析，但通过迭代循环仅处理少量高潜帧，并通过局部密度采样发现被轻量模型低估的关键帧，从而在计算效率上实现VLM分析的可处理性，同时保持高精度帧选择。

## Claims

- `57ff43fc5f51c905` 查询与帧之间关系的语义理解深度和计算分配策略。通过VLM只对少量高潜帧进行推理分析，并利用局部密度采样迭代扩展相关区域，在控制计算成本的同时实现准确选择。
- `11668b1bfc00ffb8` 利用强大的VLM进行深度语义分析，但通过迭代循环仅处理少量高潜帧，并通过局部密度采样发现被轻量模型低估的关键帧，从而在计算效率上实现VLM分析的可处理性，同时保持高精度帧选择。

## Evidence Excerpt

--- title: "A.I.R.: Enabling Adaptive, Iterative, and Reasoning-based Frame Selection For Video Question Answering" type: paper paper_level: A venue: ICLR year: 2026 pdf_ref: paperPDFs/ICLR_2026/A.I.R._Enabling_Adaptive_Iterative_and_Reasoning-based_Frame_Selection_For_Video_Question_Answering.pdf aliases: - IR - IREAIRBFSVQA acceptance: accepted tags: - topic/vision_multimodal_applications - topic/vision_multimodal_applications/vision_models_multimodal core_operator: 查询与帧之间关系的语义理解深度和计算分配策略。通过VLM只对少量高潜帧进行推理分析，并利用局部密度采样迭代扩展相关区域，在控制计算成本的同时实现准确选择。 primary_logic: 利用强大的VLM进行深度语义分析，但通过迭代循环仅处理少量高潜帧，并通过局部密度采样发现被轻量模型低估的关键帧，从而在计算效率上实现VLM分析的可处理性，同时保持高精度帧选择。 claims: - "A.I.R. performs frame selection in three stages: Adaptive Initial Sampling, Iterative Frame Selection, and QA Stage." - Adaptive threshold is dynamically computed per video using GMM, separating high-relevance frames from low-relevance ones. - Iterative Frame Selection progressively refines the candidate set using a four-step loop with VLM analysis on small batches. - A.I.R. + InternVL-3 achieves 62.8% on LongVideoBench (+4.5%) and 82.6% on NextQA, while analyzing far fewer frames. paradigm: 利用强大的VLM进行深度语义分析，但通过迭代循环仅处理少量高潜帧，并通过局
