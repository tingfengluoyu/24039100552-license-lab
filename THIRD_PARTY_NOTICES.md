# 第三方组件与素材说明（THIRD_PARTY_NOTICES）

本项目是课程练习项目，运行依赖通过 pip 安装，**未把第三方源代码复制进仓库**。下表记录依赖名称、来源、许可证，以及本项目需要履行的义务。

| 名称 | 作者或项目 | 来源链接 | 许可证或授权 | 在本项目中的使用方式 | 需要履行的义务 |
|---|---|---|---|---|---|
| Pillow | Python Pillow 项目（Secret Labs AB、Fredrik Lundh、Jeffrey A. Clark 及贡献者） | 许可证原文 <https://github.com/python-pillow/Pillow/blob/main/LICENSE> ；项目官网 <https://python-pillow.org> | MIT-CMU（HPND 风格宽松许可；PyPI 元数据 License-Expression 记为 `MIT-CMU`） | 读取图片、缩放与重采样、解析 EXIF 拍摄时间用于排序 | 在所有副本与支持文档中保留版权与许可声明；不得以 Secret Labs AB 或作者名义为衍生品作背书 |
| reportlab | ReportLab Inc. | PyPI <https://pypi.org/project/reportlab/> ；许可条款规范文本 <https://spdx.org/licenses/BSD-3-Clause.html> | BSD-3-Clause（ReportLab 版三条款 BSD） | 创建 PDF 页面、写入图像流、设置页面尺寸与边距 | 保留版权与许可声明；以二进制形式分发时需在随附文档中复现该声明；不得用 ReportLab 名义为衍生品背书 |

## 说明

- 安装方式：`py -3 -m pip install pillow reportlab`。本机实测版本 Pillow 12.3.0、reportlab 5.0.0（2026-10-31 核对）。
- **reportlab 的许可证全文以发行包内文件为准**：`reportlab-5.0.0.dist-info/licenses/LICENSE`（本机路径 `C:\Users\ASUS\AppData\Local\Programs\Python\Python313\Lib\site-packages\reportlab-5.0.0.dist-info\licenses\LICENSE`）。其正文为三条款 BSD，与 SPDX 的 `BSD-3-Clause` 规范文本一致：①源码再分发保留版权与免责声明；②二进制再分发在文档中复现该声明；③不得用版权人名义为衍生品背书。
- ReportLab 官网没有单独托管开源 `reportlab` 库的许可证页面：`https://www.reportlab.com/about/license/` 返回 404，`https://www.reportlab.com/about/licence-terms/` 是 **ReportLab PLUS（rlextra）商业版** 的订阅条款，与本项目使用的开源库无关，故不作为许可依据引用。
- reportlab 包内自带若干字体（Bitstream Vera、DarkGarden 等）各有独立许可，本项目不单独分发这些字体文件，生成 PDF 时只使用 PDF 基础字体。
- Pillow 的 wheel 中打包了 brotli（MIT）、freetype（FTL/GPLv2 双许可）等二进制组件，其分发合规由 Pillow 自身负责；本项目只以依赖方式调用。
- 本仓库未引入其他第三方代码、图片、字体或数据集；若后续引入，请在本表追加对应行。
