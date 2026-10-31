# 工作区说明：图片整合脚本

把一批图片合成一个 PDF，主要用途是把屏幕截图整理成复习资料。

## 文件职责

- `images_to_pdf.py` — **母版，禁止修改**（用户要求保留对照，4846 B）。
- `images_to_pdf_v2.py` — 现役版本：排序自动推断（auto/exif/created/modified/name/number）、排版与编码选项、原子写入。
- `README_v2.md` — 用法、参数、已修问题清单、后续扩展方向（先读这个）。
- `selftest_v2.py` — 60 项断言；改完脚本必须跑 `python selftest_v2.py`。
- `合成PDF.bat` — 拖拽/双击启动器。**纯 ASCII、UTF-8 无 BOM、CRLF**，这三条不能破（cmd 会因多字节行错位吞行）。
- `.audit_me/NOTES.md` — PDF 整合部分的审计记录（问题编号、复现命令、回归基线）。
- `.auditA/` `.auditB/` `.auditC/` `.audit_embed/` — 审计脚本与样例，可整目录删除。

## 硬约束

- 任何改动只进 `images_to_pdf_v2.py`，不动 `images_to_pdf.py`。
- 本机 python：`C:\Users\ASUS\AppData\Local\Programs\Python\Python313\python.exe`（Pillow 12.3.0 + reportlab 5.0.0）。
- 验证方式：`python selftest_v2.py` 与 `python .audit_me\regression_matrix.py`，两者都应全绿。
- 输出 PDF 默认落在 `<图片文件夹>\output\pdf\`；写 D 盘受限目录需要管理员（用 `-o` 换目录最简单）。

## 上下文提示

历史对话已归档。需要细节时先读 `README_v2.md` 和 `.audit_me/NOTES.md`，不要凭猜测重新实现。
