---
omni_type: compiled_wiki
domain: research
source_id: researchflow
source_path: obsidian-vault/analysis/ICLR_2026/Adaptive_Moments_are_Surprisingly_Effective_for_Plug-and-Play_Diffusion_Sampling.md
paper_link: https://openreview.net/forum?id=qYDObsHldZ
review_state: approved
---

# Adaptive Moments are Surprisingly Effective for Plug-and-Play Diffusion Sampling

## Source

- source_id: researchflow
- analysis_path: obsidian-vault/analysis/ICLR_2026/Adaptive_Moments_are_Surprisingly_Effective_for_Plug-and-Play_Diffusion_Sampling.md
- paper_link: https://openreview.net/forum?id=qYDObsHldZ

## Compiled Synthesis

来自随机优化（Adam）的自适应矩估计可以稳定噪声引导梯度，无需设计更复杂的似然近似方法即可显著提升样本质量。

## Claims

- `e59f6f8b4fdc51f4` 来自随机优化（Adam）的自适应矩估计可以稳定噪声引导梯度，无需设计更复杂的似然近似方法即可显著提升样本质量。

## Evidence Excerpt

--- title: "Adaptive Moments are Surprisingly Effective for Plug-and-Play Diffusion Sampling" type: paper paper_level: A venue: ICLR year: 2026 pdf_ref: paperPDFs/ICLR_2026/Adaptive_Moments_are_Surprisingly_Effective_for_Plug-and-Play_Diffusion_Sampling.pdf aliases: - AMGAA - AMASEPPDS acceptance: accepted openreview_forum_id: qYDObsHldZ tags: - topic/generative_models_diffusion - topic/generative_models_diffusion/diffusion_image_video core_operator: 在采样过程中对似然分数的梯度应用自适应矩估计（Adam风格的动量与自适应缩放），从而稳定梯度方向与尺度。 primary_logic: 将随机优化中成熟的Adam自适应矩思想注入到扩散模型的引导采样中，通过跨时间步维持梯度的一阶与二阶指数移动平均，有效抑制引导信号中的噪声，使采样轨迹更一致地朝目标条件收敛，且几乎不增加计算开销。 claims: - AdamDPS在所有重建任务（超分辨16×、高斯去模糊强度12、90%随机掩码修复）的LPIPS和FID上均超越全部对比方法。 - 在ImageNet类别条件生成中，AdamDPS获得10.49%的top-10准确率，其余方法均接近1%。 - 合成实验表明，AdamDPS对引导噪声的鲁棒性远优于DPS，KL散度随噪声幅度增长更慢。 - AdamDPS相邻步的引导梯度余弦相似度始终为正，而DPS频繁出现负相似度，证明梯度方向被稳定化。 paradigm: 将随机优化中成熟的Adam自适应矩思想注入到扩散模型的引导采样中，通过跨时间步维持梯度的一阶与二阶指数移动平均，有效抑制引导信号中的噪声，使采样轨迹更一致地朝目标条件收敛，且几乎不增加计算开销。 --- # Adaptive Moments are Surprisingly Effective for Plug-and-Play Diffusion Sampling > [!tip] 核心洞察 > 将随机优化中成熟的Adam自适应矩思想注入到扩散模型的引导采样中，通过跨时间步维持梯度的一阶与二阶指数移动平均，有效抑制引导信号中的噪声，使采样轨迹更一致地朝目标条件收敛，且几乎不增加计算开销。 | 字段 | 内容 | | ------- | -----------------
