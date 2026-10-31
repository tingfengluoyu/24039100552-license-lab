# 图片整合为 PDF（课程练习版）
> 简介：帮助使用者把一堆图片按顺序合并成一个 PDF（在此仓库中只提供此版本作为演示使用）

## 项目状态
仅作为课程练习项目，功能已可用但范围很小：只支持图片 → PDF。

## 安装或获取
环境：Windows + Python 3.x（需要 Pillow / img2pdf，见 requirements.txt）
暂不提供安装方案

## 使用说明（最小操作）
1. 把图片放进 images/ 目录（按文件名排序）
2. 双击 合成PDF.bat（或 python images_to_pdf_v2.py --input images --output out.pdf）
3. 打开 out.pdf 检查页数与顺序

## 项目结构与依赖
| 文件 | 作用 |
| images_to_pdf_v2.py | 主程序：排序、合成、输出 |
| selftest_v2.py / regression_matrix.py | 自测与回归脚本 |
| 合成PDF.bat | 双击入口 |
依赖：Pillow（图像读取）、img2pdf（PDF 生成）→ 详见 THIRD_PARTY_NOTICES.md

## 如何贡献
提 Issue 描述问题（附报错和复现步骤）；或 fork 后提 Pull Request。

## 许可证
MIT。理由：课程练习项目，希望代码能被自由复用；不要求衍生作品继续开源。
（可选：一句话说明素材版权——本仓库不包含任何第三方受版权保护的资料）
