---
omni_type: compiled_wiki
domain: research
source_id: researchflow
source_path: obsidian-vault/analysis/ICLR_2026/AC-Sampler_Accelerate_and_Correct_Diffusion_Sampling_with_Metropolis-Hastings_Algorithm.md
paper_link: https://openreview.net/forum?id=kWl13kRJTQ
review_state: approved_after_proposal
---

# AC-Sampler: Accelerate and Correct Diffusion Sampling with Metropolis-Hastings Algorithm

## Source

- source_id: researchflow
- analysis_path: obsidian-vault/analysis/ICLR_2026/AC-Sampler_Accelerate_and_Correct_Diffusion_Sampling_with_Metropolis-Hastings_Algorithm.md
- paper_link: https://openreview.net/forum?id=kWl13kRJTQ

## Compiled Synthesis

通过定理4.1将密度比分解为可计算项，并训练时间依赖的判别器估计似然比，使得在任意时间步均可计算MH接受概率，从而将加速与误差校正统一在一个无需微调扩散模型的框架中。

## Claims

- `d765ad2ef9dbc626` 不从纯噪声开始逐步去噪，而是在中间时间步直接构建MALA马尔可夫链，利用Metropolis-Hastings校正使样本逼近该时间步的真实边缘分布，从而跳过大量去噪步骤。
- `41f50dbdb9dee9d2` 通过定理4.1将密度比分解为可计算项，并训练时间依赖的判别器估计似然比，使得在任意时间步均可计算MH接受概率，从而将加速与误差校正统一在一个无需微调扩散模型的框架中。

## Evidence Excerpt

--- title: "AC-Sampler: Accelerate and Correct Diffusion Sampling with Metropolis-Hastings Algorithm" type: paper paper_level: A venue: ICLR year: 2026 pdf_ref: paperPDFs/ICLR_2026/AC-Sampler_Accelerate_and_Correct_Diffusion_Sampling_with_Metropolis-Hastings_Algorithm.pdf aliases: - AC-Sampler acceptance: accepted tags: - topic/generative_models_diffusion - topic/generative_models_diffusion/diffusion_image_video core_operator: 不从纯噪声开始逐步去噪，而是在中间时间步直接构建MALA马尔可夫链，利用Metropolis-Hastings校正使样本逼近该时间步的真实边缘分布，从而跳过大量去噪步骤。 primary_logic: 通过定理4.1将密度比分解为可计算项，并训练时间依赖的判别器估计似然比，使得在任意时间步均可计算MH接受概率，从而将加速与误差校正统一在一个无需微调扩散模型的框架中。 claims: - 在CIFAR‑10无条件生成任务上，AC‑Sampler仅用15.8 NFE就实现FID 2.38，而基础采样器在17 NFE下FID为3.23。 - 在CelebA‑HQ 256×256上，AC‑Sampler以98.3 NFE取得FID 6.6，远低于基线。 - 定理4.3证明，使用最优判别器时，AC‑Sampler生成的分布与真实分布的KL散度不大于原始模型分布的KL散度。 - AC‑Sampler可以与现有加速和校正方法（如DPM‑v3、DG）结合，进一步改善FID和NFE。 paradigm: 通过定理4.1将密度比分解为可计算项，并训练时间依赖的判别器估计似然比，使得在任意时间步均可计算MH接受概率，从而将加速与误差校正统一在一个无需微调扩散模型的框架中。 --- # AC-Sampler: Accelerate and Correct Diffusion Sampling with Metropolis-Hastings Algorithm > [!tip] 核心洞察 > 通过定理4.1将密度比分解为可计算项，并训练时间依赖的判别器估计似然比，使得在任意时间步均可计算MH接受概率，从而将加速与误差校正统一在一个无需微调扩散模型的框架中。 | 字段 | 内容 | |------|------| | 中文题名 | AC-
