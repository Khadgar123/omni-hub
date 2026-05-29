---
omni_type: compiled_wiki
domain: research
source_id: researchflow
source_path: obsidian-vault/analysis/ICLR_2026/A2TG_Adaptive_Anisotropic_Textured_Gaussians_for_Efficient_3D_Scene_Representation.md
paper_link: https://openreview.net/forum?id=EPN5MU4liR
review_state: approved
---

# A^2TG: Adaptive Anisotropic Textured Gaussians for Efficient 3D Scene Representation

## Source

- source_id: researchflow
- analysis_path: obsidian-vault/analysis/ICLR_2026/A2TG_Adaptive_Anisotropic_Textured_Gaussians_for_Efficient_3D_Scene_Representation.md
- paper_link: https://openreview.net/forum?id=EPN5MU4liR

## Compiled Synthesis

通过梯度驱动的高斯筛选与各向异性上采样，纹理分辨率集中在场景高频、高可见区域，大量背景或低细节高斯保留1×1极小纹理，在保持渲染质量的同时显著降低内存开销，并避免均匀正方形纹理的冗余。

## Claims

- `70a17c39460dc435` 基于梯度引导的自适应纹理控制策略：利用位置梯度筛选需要高频细节的高斯，再根据高斯两个轴的比例各向异性地上采样纹理分辨率与宽高比，从而实现按需分配纹理参数。
- `a30366eb620e30cb` 通过梯度驱动的高斯筛选与各向异性上采样，纹理分辨率集中在场景高频、高可见区域，大量背景或低细节高斯保留1×1极小纹理，在保持渲染质量的同时显著降低内存开销，并避免均匀正方形纹理的冗余。

## Evidence Excerpt

--- title: "A^2TG: Adaptive Anisotropic Textured Gaussians for Efficient 3D Scene Representation" type: paper paper_level: A venue: ICLR year: 2026 pdf_ref: paperPDFs/ICLR_2026/A2TG_Adaptive_Anisotropic_Textured_Gaussians_for_Efficient_3D_Scene_Representation.pdf aliases: - 2AATG - 2AATGE3SR acceptance: accepted tags: - topic/vision_multimodal_applications - topic/vision_multimodal_applications/3d_rendering_reconstruction core_operator: 基于梯度引导的自适应纹理控制策略：利用位置梯度筛选需要高频细节的高斯，再根据高斯两个轴的比例各向异性地上采样纹理分辨率与宽高比，从而实现按需分配纹理参数。 primary_logic: 通过梯度驱动的高斯筛选与各向异性上采样，纹理分辨率集中在场景高频、高可见区域，大量背景或低细节高斯保留1×1极小纹理，在保持渲染质量的同时显著降低内存开销，并避免均匀正方形纹理的冗余。 claims: - 在固定200MB内存预算下，A2TG在DeepBlending上以189.42MB（最低内存）取得PSNR 29.86，优于Textured Gaussians*（29.51 PSNR，200MB） - 固定高斯数100万时，A2TG在DeepBlending达到PSNR 29.82，与Textured Gaussians*相当（29.80），但内存开销仅为19%（277MB） vs 1764%（724MB），节省85%以上纹理内存 - 场景中62.4%的高斯保留1×1纹理（未上采样），证明自适应分配有效避免了大量低需求区域的纹理浪费 - 消融实验去除纹理上采样（w/o Upscaling）后内存最小但质量最差，去除各向异性（w/o Anisotropy）后质量接近但内存明显增加，表明上采样与各向异性共同支撑高效的质量-内存权衡 paradigm: 通过梯度驱动的高斯筛选与各向异性上采样，纹理分辨率集中在场景高频、高可见区域，大量背景或低细节高斯保留1×1极小纹理，在保持渲染质量的同时显著降低内存开销，并避免均匀正方形纹理的冗余。 --- # A^2TG: Adaptive Anisotropic Textured Gaussians for Efficient 3D Scene Representation
