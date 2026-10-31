# 图片整合为 PDF（课程练习版）

> 简介：帮助使用者把一堆图片按顺序合并成一个 PDF 文件，省掉手动一张张排序、插页的麻烦。

## 项目状态

课程练习项目，**代码已可用，但此版本仅做演示使用，不会进一步扩展**：只做「图片 → PDF」这一件事。

- 已实现：图片扫描、多种排序策略（文件名 / 创建时间 / 修改时间 / EXIF 拍摄时间 / 自然序）、页面适配、边距、灰度、重复图去重、失败保护（先写 `.part` 再原子改名）。
- 未实现：图形界面、OCR、压缩优化、多文件夹批量任务。这些不在本仓库的计划内。

## 安装或获取

### 环境要求

- Windows 10 / 11（排序会参考文件创建时间；macOS / Linux 也能运行，但 `created` 排序行为不同）
- Python 3.8 及以上，且带 pip
- 第三方库只有两个：**Pillow、reportlab**，没有其它依赖

### 获取代码

整个仓库就是完整可运行版本，两种方式任选：

- **下载**：仓库首页 → `Code` → `Download ZIP`，解压后进入 `图片整合脚本/` 目录；
- **克隆**：`git clone https://github.com/tingfengluoyu/24039100552-license-lab.git`，然后 `cd 24039100552-license-lab/图片整合脚本`。

需要的文件都在 `图片整合脚本/` 里：`images_to_pdf_v2.py`（主程序）、`合成PDF.bat`（双击启动器）、`selftest_v2.py` 与 `regression_matrix.py`（测试）。

### 安装依赖（第一步）

```bat
py -3 -m pip install pillow reportlab
```

如果机器上 `py` 不可用，把 `py -3` 换成 `python`。国内网络慢可加镜像：

```bat
py -3 -m pip install pillow reportlab -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 验证安装是否成功

```bat
py -3 -c "import PIL, reportlab; print('deps ok')"
py -3 images_to_pdf_v2.py --help
```

第一条打印 `deps ok`、第二条能列出参数说明，就说明环境就绪。

### 常见问题

- 双击 `合成PDF.bat` 提示 `no Python with Pillow and reportlab was found`：依赖没装，或装到了另一个 Python 解释器上——用同一个 `py -3` / `python` 重装。
- 写入受保护目录（例如某些 D 盘目录）失败：右键 `合成PDF.bat` →「以管理员身份运行」。
- 默认输出位置是 `<图片所在目录>/output/pdf/merged_images.pdf`，可用 `-n` 改文件名、`-o` 改输出目录。

## 使用说明（最小操作）

1. 把要合并的图片放进一个文件夹（例如 `D:\shots`），**不需要重命名**；
2. 双击 `合成PDF.bat`，把文件夹拖到窗口里（或直接把文件夹拖到 bat 图标上）；
3. 完成后打开 `D:\shots\output\pdf\merged_images.pdf`，检查页数与顺序。

命令行等价写法：

```bat
py -3 images_to_pdf_v2.py D:\shots
py -3 images_to_pdf_v2.py D:\shots -n 复习资料.pdf
py -3 images_to_pdf_v2.py D:\shots --sort modified --print-order
py -3 images_to_pdf_v2.py D:\shots -r --min-side 400 --dedup
py -3 images_to_pdf_v2.py D:\shots --dry-run
```

常用参数：

| 参数 | 作用 |
| --- | --- |
| `--sort auto\|created\|modified\|exif\|name\|number` | 排序依据，默认 `auto`（有 EXIF 用拍摄时间，否则比较创建/修改时间） |
| `-n 名字` | 输出文件名（默认 `merged_images.pdf`） |
| `-o 目录` | 输出目录（默认 `<图片目录>/output/pdf`） |
| `-r` | 包含子文件夹里的图片 |
| `--dedup` | 跳过内容完全相同的重复图片 |
| `--min-side PX` | 跳过短边小于该像素数的图 |
| `--page a4\|letter\|fit` | 页面尺寸；`fit` 表示页面跟随图片 |
| `--margin MM` / `--grayscale` | 页面边距（毫米）/ 转灰度 |
| `--print-order` / `--dry-run` | 打印最终顺序 / 只打印顺序不生成 PDF |

## 项目结构与依赖

| 路径 | 作用 |
| --- | --- |
| `图片整合脚本/images_to_pdf_v2.py` | 主程序：扫描图片、排序、生成 PDF |
| `图片整合脚本/合成PDF.bat` | 双击启动器：自动寻找带依赖的 Python，无参数时询问文件夹 |
| `图片整合脚本/selftest_v2.py` | 自测脚本 |
| `图片整合脚本/regression_matrix.py` | 回归矩阵（多排序策略组合验证） |
| `图片整合脚本/README_v2.md` | 更完整的开发版说明 |
| `图片整合脚本/NOTES.md` | 设计与踩坑记录 |
| `图片整合脚本/AGENTS.md` | 给 AI 助手的项目约定 |
| `LICENSE` / `README.md` / `compliance-analysis.md` | 许可证、本说明、许可证选择分析 |

依赖：**Pillow**（图像读取、缩放、EXIF 解析）、**reportlab**（PDF 页面与图像流生成）。两者均为第三方开源库，来源链接、许可证与需要履行的义务见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 如何贡献

- **提 Issue**：<https://github.com/tingfengluoyu/24039100552-license-lab/issues> —— 请附上：图片数量与大致尺寸、运行的具体命令、报错原文（可加 `--print-order` 贴出排序结果）。
- **提 Pull Request**：<https://github.com/tingfengluoyu/24039100552-license-lab/pulls> —— fork 后修改 `图片整合脚本/images_to_pdf_v2.py`，本地跑一遍 `py -3 selftest_v2.py` 再提交。
- 本项目是课程练习，**不以性能优化为目标**；反馈以「能复现」为先。

## 许可证

本项目采用 **MIT** 许可证，全文见 [LICENSE](LICENSE)。

- **为什么选它**：这是课程练习项目，希望代码能被自由复用——别人可以复制、修改、分发，包括闭源再发布，不要求衍生作品继续开源；MIT 的义务最少（保留版权与许可声明即可），最不容易因误操作违规。
- **覆盖范围**：仓库内的源代码与文档。仓库**不包含**任何第三方受版权保护的资料（图片、题库、教材扫描件）；用本工具处理他人素材时，素材本身的授权由使用者自行负责。
- **第三方组件**：Pillow、reportlab 的许可证与义务见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。