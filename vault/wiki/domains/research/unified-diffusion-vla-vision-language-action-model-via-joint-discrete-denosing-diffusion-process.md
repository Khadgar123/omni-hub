---
omni_type: compiled_wiki
domain: research
source_id: researchflow
source_path: obsidian-vault/analysis/ICLR_2026/Unified_Diffusion_VLA_Vision-Language-Action_Model_via_Joint_Discrete_Denosing_Diffusion_Process.md
paper_link: https://openreview.net/forum?id=UvQOcw2oCD
review_state: approved
---

# Unified Diffusion VLA: Vision-Language-Action Model via Joint Discrete Denosing Diffusion Process

## Source

- source_id: researchflow
- analysis_path: obsidian-vault/analysis/ICLR_2026/Unified_Diffusion_VLA_Vision-Language-Action_Model_via_Joint_Discrete_Denosing_Diffusion_Process.md
- paper_link: https://openreview.net/forum?id=UvQOcw2oCD

## Compiled Synthesis

同步联合去噪使得动作预测在持续、充分的未来视觉引导下由粗到精地演化，将抽象的动作推理转化为以视觉预测为条件的逆运动学问题，从而在统一的扩散轨迹中实现理解、生成和执行的深度协同。

## Claims

- `e2215b03ea88b92b` 联合离散去噪扩散过程（JD3P）：将未来图像和动作 tokens 在同一个离散扩散轨迹中同步去噪，每一步动作 tokens 因果地关注图像 tokens，通过迭代精炼实现从视觉观察到动作的渐进式映射。
- `c822b72498f3a3b6` 同步联合去噪使得动作预测在持续、充分的未来视觉引导下由粗到精地演化，将抽象的动作推理转化为以视觉预测为条件的逆运动学问题，从而在统一的扩散轨迹中实现理解、生成和执行的深度协同。

## Evidence Excerpt

--- title: "Unified Diffusion VLA: Vision-Language-Action Model via Joint Discrete Denosing Diffusion Process" type: paper paper_level: A venue: ICLR year: 2026 pdf_ref: paperPDFs/ICLR_2026/Unified_Diffusion_VLA_Vision-Language-Action_Model_via_Joint_Discrete_Denosing_Diffusion_Process.pdf aliases: - UVUDV - UDVVLAMJDDDP acceptance: accepted tags: - topic/vision_multimodal_applications - topic/vision_multimodal_applications/robotics core_operator: 联合离散去噪扩散过程（JD3P）：将未来图像和动作 tokens 在同一个离散扩散轨迹中同步去噪，每一步动作 tokens 因果地关注图像 tokens，通过迭代精炼实现从视觉观察到动作的渐进式映射。 primary_logic: 同步联合去噪使得动作预测在持续、充分的未来视觉引导下由粗到精地演化，将抽象的动作推理转化为以视觉预测为条件的逆运动学问题，从而在统一的扩散轨迹中实现理解、生成和执行的深度协同。 claims: - JD3P 联合解码相比自回归解码在 CALVIN 上平均长度提升 0.46（4.64 vs. 4.18），推理速度提升 4.3 倍（219.3 vs. 50.2 tokens/s）。 - 联合预测未来图像（而非无视觉生成或仅重建当前图像）使 CALVIN 平均长度从 4.21/4.39 显著提升至 4.64。 - UD-VLA 在 CALVIN (Avg. Len. 4.64)、LIBERO (Avg. 96.1%) 和 SimplerEnv (Overall 76.0%) 上均取得 SOTA，超越所有先前统一 VLA。 - Hybrid attention（块内双向、块间因果）比纯因果或纯双向注意力平均长度提升 0.32–0.60。 paradigm: 同步联合去噪使得动作预测在持续、充分的未来视觉引导下由粗到精地演化，将抽象的动作推理转化为以视觉预测为条件的逆运动学问题，从而在统一的扩散轨迹中实现理解、生成和执行的深度协同。 --- # Unified Diffusion VLA: Vision-Language-Action Model via Joint Discrete Denosing Diffusion Process > [!tip]
