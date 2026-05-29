---
omni_type: compiled_wiki
domain: research
source_id: researchflow
source_path: obsidian-vault/analysis/ICLR_2026/A_Noise_is_Worth_Diffusion_Guidance.md
paper_link: https://openreview.net/forum?id=xEWooSOgaz
review_state: approved_after_proposal
---

# A Noise is Worth Diffusion Guidance

## Source

- source_id: researchflow
- analysis_path: obsidian-vault/analysis/ICLR_2026/A_Noise_is_Worth_Diffusion_Guidance.md
- paper_link: https://openreview.net/forum?id=xEWooSOgaz

## Compiled Synthesis

通过学习将高斯噪声映射到富含结构化低频信息的“提炼噪声”，可以无需采样引导就生成高质量图像，同时保持扩散管线的完整性和广泛兼容性。

## Claims

- `1d8329048393567d` 初始噪声的空间结构（特别是低频成分）
- `ac9332c2ee2f9e4d` 通过学习将高斯噪声映射到富含结构化低频信息的“提炼噪声”，可以无需采样引导就生成高质量图像，同时保持扩散管线的完整性和广泛兼容性。

## Evidence Excerpt

--- title: A Noise is Worth Diffusion Guidance type: paper paper_level: A venue: ICLR year: 2026 pdf_ref: paperPDFs/ICLR_2026/A_Noise_is_Worth_Diffusion_Guidance.pdf aliases: - NIWDG acceptance: accepted tags: - topic/generative_models_diffusion - topic/generative_models_diffusion/diffusion_image_video core_operator: 初始噪声的空间结构（特别是低频成分） primary_logic: 通过学习将高斯噪声映射到富含结构化低频信息的“提炼噪声”，可以无需采样引导就生成高质量图像，同时保持扩散管线的完整性和广泛兼容性。 claims: - 初始噪声与反转噪声的差异集中在低频部分，表明存在可学习的结构化映射 - 图像空间损失在所有评估指标上大幅优于噪声空间损失 - NoiseRefine 在 SiT-XL/2、SD2.1、SDXL 上均显著改善 FID 和 IS，超过无引导高斯噪声 - 用户研究中，提炼噪声无引导采样与有引导高斯噪声采样在图像质量和提示遵循度上偏好率相当 paradigm: 通过学习将高斯噪声映射到富含结构化低频信息的“提炼噪声”，可以无需采样引导就生成高质量图像，同时保持扩散管线的完整性和广泛兼容性。 --- # A Noise is Worth Diffusion Guidance > [!tip] 核心洞察 > 通过学习将高斯噪声映射到富含结构化低频信息的“提炼噪声”，可以无需采样引导就生成高质量图像，同时保持扩散管线的完整性和广泛兼容性。 | 字段 | 内容 | |------|------| | 中文题名 | 噪声也值得扩散引导 | | 英文题名 | A Noise is Worth Diffusion Guidance | | 会议/期刊 | ICLR 2026 (accepted) | | Links | [paper](https://openreview.net/forum?id=xEWooSOgaz) | | Topic | #topic/generative_models_diffusion #topic/generative_models_diffusion/diffusion_image_video | | Method | NoiseRefine | | Dataset | MS-COCO 2014 validation (30K prompts), MS-COCO 2014 validation, Image
