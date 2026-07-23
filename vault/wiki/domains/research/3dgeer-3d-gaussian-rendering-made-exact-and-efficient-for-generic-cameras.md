---
omni_type: compiled_wiki
domain: research
source_id: researchflow
source_path: obsidian-vault/analysis/ICLR_2026/3DGEER_3D_Gaussian_Rendering_Made_Exact_and_Efficient_for_Generic_Cameras.md
paper_link: https://openreview.net/forum?id=4voMNlRWI7
review_state: approved
---

# 3DGEER: 3D Gaussian Rendering Made Exact and Efficient for Generic Cameras

## Source

- source_id: researchflow
- analysis_path: obsidian-vault/analysis/ICLR_2026/3DGEER_3D_Gaussian_Rendering_Made_Exact_and_Efficient_for_Generic_Cameras.md
- paper_link: https://openreview.net/forum?id=4voMNlRWI7

## Compiled Synthesis

通过将每个3D高斯映射到各向同性的规范坐标系，光线-高斯密度积分可简化为闭合形式的马氏距离指数函数，该形式等价于先前工作中的“最大响应”启发式，但具有严格的投影精确性；同时，在角度域（θ, φ）中定义PBF，使得关联计算与相机模型无关，且可通过二次方程解析求解。

## Claims

- `5e7a9006c0e739e3` 将光线-粒子关联从图像空间或场景级BVH提升至相机子视锥（CSF）与粒子包围视锥（PBF）之间的精确视锥级关联，并推导出闭合形式的PBF解析解，从而在保持投影精确性的同时实现高效GPU并行化。
- `d29521ab813ef4f3` 通过将每个3D高斯映射到各向同性的规范坐标系，光线-高斯密度积分可简化为闭合形式的马氏距离指数函数，该形式等价于先前工作中的“最大响应”启发式，但具有严格的投影精确性；同时，在角度域（θ, φ）中定义PBF，使得关联计算与相机模型无关，且可通过二次方程解析求解。

## Evidence Excerpt

--- title: "3DGEER: 3D Gaussian Rendering Made Exact and Efficient for Generic Cameras" type: paper paper_level: A venue: ICLR year: 2026 pdf_ref: paperPDFs/ICLR_2026/3DGEER_3D_Gaussian_Rendering_Made_Exact_and_Efficient_for_Generic_Cameras.pdf aliases: - 33GRMEEGC acceptance: accepted tags: - topic/vision_multimodal_applications - topic/vision_multimodal_applications/3d_rendering_reconstruction core_operator: 将光线-粒子关联从图像空间或场景级BVH提升至相机子视锥（CSF）与粒子包围视锥（PBF）之间的精确视锥级关联，并推导出闭合形式的PBF解析解，从而在保持投影精确性的同时实现高效GPU并行化。 primary_logic: 通过将每个3D高斯映射到各向同性的规范坐标系，光线-高斯密度积分可简化为闭合形式的马氏距离指数函数，该形式等价于先前工作中的“最大响应”启发式，但具有严格的投影精确性；同时，在角度域（θ, φ）中定义PBF，使得关联计算与相机模型无关，且可通过二次方程解析求解。 claims: - 3DGEER在ScanNet++全FoV上达到31.50 PSNR，远超FisheyeGS的27.90 PSNR和3DGUT的28.14 PSNR。 - 3DGEER在MipNeRF360上达到27.76 PSNR，超越3DGS（27.21 PSNR）和3DGUT（27.37 PSNR），同时保持327 FPS的实时帧率。 - PBF关联仅需0.63 GB显存，远低于EWA的2.2 GB和UT的1.4 GB，且每tile关联高斯数仅为475，远少于EWA的2203。 - 3DGEER在ZipNeRF跨相机泛化实验中，在Pinhole训练-Fisheye测试的最具挑战性设置下显著优于所有基线。 paradigm: 通过将每个3D高斯映射到各向同性的规范坐标系，光线-高斯密度积分可简化为闭合形式的马氏距离指数函数，该形式等价于先前工作中的“最大响应”启发式，但具有严格的投影精确性；同时，在角度域（θ, φ）中定义PBF，使得关联计算与相机模型无关，且可通过二次方程解析求解。 --- # 3DGEER: 3D Gaussian Rendering Made Exact and Efficient for Generic Cameras > [!tip]
