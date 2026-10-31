# 审计笔记：images_to_pdf_v2.py 的 PDF 整合部分

对照母版：`images_to_pdf.py`（158 行 / 4846 B，全程未改动，只读对照）。
审计对象：`images_to_pdf_v2.py`（行号随修复变动，见下）。
审计方式：三个子代理分头查（A 格式/编码、B 页面几何、C 静默丢图与破坏性），
主代理读代码 + 自己造探针复现 + 逐字节解析 PDF 对象流验证。
所有报告与复现脚本在 `.auditA\`、`.auditB\`、`.auditC\`、`.audit_me\`。

## 已修的问题（按严重度）

### F1 破坏性写入 —— 一次失败会毁掉上一次的好 PDF（最严重）
`canvas.Canvas()` 直接打开**最终路径**，中途异常（坏图 + `--strict`、磁盘满、PDF 被占用、
Ctrl-C、进程被杀）会静默 truncate 上一次的 PDF。
修法：写同目录 `<name>.pdf.part` → 成功才 `temp_pdf.replace(output_pdf)`；
`discard_temp_pdf()` 清临时文件；`output_pdf_locked_hint()` 区分“PDF 被占用”与“目录没权限”。
实测：`regression_matrix.py --out after2.json` 里 `strict_guard` → `rc=1 untouched=True before=191321 after=191321 parts=[]`。

### F2 参数/路径校验漏洞
`-n "../evil"` 写到输出目录之外、`-o <已存在的文件>` 抛 traceback、PDF 被占用误报成权限问题。
修法：`validate_args()` 在动手前校验 `--quality 1..100`、`--dpi>=1`、`--max-dpi>=0`、
`--min-side>=0`、`--margin>=0` 且 `2*margin*MM < min(页宽,页高)`、`-n` 非空且不含路径分隔符、
不可是绝对路径；`-o` 指向已存在文件 → “Output path exists and is not a folder”。

### F3 `--page fit` 下 `--max-dpi` 会改变页面物理尺寸
1000x700 @dpi200 → 324x216pt，加 `--max-dpi 100` 变 162x108pt。
修法：fit 页面框按**原始像素**算（`entry.width / dpi * 72 + 2*margin`），`setPageSize` 移进该分支，
删掉“按降采样后尺寸覆盖页宽高”的旧逻辑。实测 `fit_dpi200_maxdpi100` → box 324x216（与 `fit_dpi200` 一致），体积 74752 < 252167。

### F4 坏图中断整轮（兜底异常类型太窄）
逐页嵌入只捕 `(UnidentifiedImageError, OSError, ValueError)`，而坏图可能抛 `zlib.error`、
`struct.error`、`SyntaxError`、reportlab 的 `TypeError`。
修法：该循环改为 `except Exception`（`--strict` 仍立即中止）。
副产物：截断 PNG 其实在**扫描阶段**就被 `getexif()` 引爆、以 `unreadable (OSError)` 跳过，
所以 `--strict` 的复现要用**截断 JPEG**（`guard_src` 里插 `00_broken_first.jpg`）。

### F5 体积：ASCII85 白加约 25%
reportlab 默认 `useA85=1`，把图片流也包一层 ASCII85。
修法：新增 `--pdf-ascii85 on|off`，默认 off。**坑**：`useA85` 不能作为关键字传给
`canvas.Canvas`（`Canvas.__init__(..., **kwds)` 会静默吞掉），必须在 `main()` 里设
`rl_config.useA85 = args.pdf_ascii85 == "on"`。
实测：a4_default 312527 → 252353 B（-19.3%）；`--pdf-ascii85 on` 精确复现 312527 B。

### F6 透明 PNG 的 alpha 被丢弃（子代理 A 发现，可见的错误像素）
reportlab 对非 JPEG 一律 `im.convert("RGB")`（`reportlab/lib/utils.py:719-725`），
透明像素会露出图片内部存着的颜色（常见是黑），PDF 里既无 `/SMask` 也无 `/Mask`。
修法：`ImageEntry.has_alpha`（`scan_image` 记录）+ `flatten_alpha()` 合成到白底，
并强制走变换路径（透传路径绕不过 reportlab 的 convert）。
实测 `.audit_me\check_alpha.py`：改动前透明像素嵌成 `(255,0,0)`（存的红），
改动后 `(255,255,255)`，不透明像素 `(0,0,255)` 保持；灰度=255；`--encode jpeg` 同样压白。

### F7 文案错误：`--encode keep` 对 PNG 并不“保留原字节”
reportlab 只对 `.jpg/.jpeg` 走 `jpg_imagedata()` 原字节透传，PNG 一律解码重压
（3998 B 的 PNG → PDF 里 603755 B 的 FlateDecode 流）。
修法：改 docstring 与 README，说清“keep 只有 JPEG 是真透传；PNG 无损但会大很多；
要小用 `--encode jpeg`”。

### F8 降采样静默进行（子代理 A 发现）
默认 `--max-dpi 200` 会把 300 dpi 照片悄悄缩小到一半，且不提示。
修法：`Prepared.resized` + `render_page(stats=...)` 统计，输出
`N image(s) were downscaled to honour --max-dpi 200 (use --max-dpi 0 ...)`。
（默认值本身没改：截图场景 200 dpi 够用，改了反而会突然变胖。）

### F9 亚 1 pt 页面溢出 MediaBox（子代理 B 发现）
1 px 图配超大 `--dpi` 在 fit 模式下算出 0.48 pt 页面，放大绘制会溢出。
修法：`page_geometry()` 的可用区下限 `max(0.1, 页面 - 2*margin)`，去掉盲目放大上限，
`draw_w/draw_h` 恒等于可用区。
实测：1x1 px @dpi150 fit → 0.48x0.48 pt 页，绘制 0.48x0.48 不再溢出；
40x30 px 在 A4 上仍被放大到 595.2756x446.4567 填满页宽。

### F10 打印精度（外观）
`Page size: 1 x 1 pt` 其实是 0.75 pt（`{:.0f}`）。
修法：改 `{:.1f}`。

## 已澄清、不是 bug
- `scan_image()` 已处理 EXIF 方向（orientation 5–8 交换宽高），`entry.width/height` 是**显示后**尺寸。
- `-n` 未加 `.pdf` 会自动补上；`--dedup` 用逐字节 SHA-256，安全。
- 内容相同的图片在 PDF 里自动复用同一个 XObject（reportlab 按像素 digest 命名 ImageReader），不双倍占体积。
- JPEG 的 CMYK/渐进/灰度、调色板+透明、1bit、I16、BMP、TIFF、动图第一帧、`.JPG` 大写、
  PNG 改名 `.jpg`：子代理 A 全部实测像素等于源图（最大通道差 0）。
- 像素上限：>178 MP 被 Pillow 安全阀拦下并跳过该张、其余照排。

## 明确不改的行为（有意的设计选择）
- 图片自带的 DPI 元数据（PNG pHYs / JFIF density）不参与 `--page fit` 的页尺寸计算，
  整批统一用 `--dpi`：一册里页尺寸一致更可预期；需要时显式 `--dpi 300`。
  想按每张图元数据排版，记在 README 的扩展方向里（`--fit-dpi auto`）。

## 回归基线（`.audit_me\regression_matrix.py`，8 张可用图）
修复后（`after4.json`，页数 / 字节数 / 首页框 / 滤镜）：
- a4_default 8 / 252351 / 595.28x841.89 / DCT,Flate
- letter_margin10 8 / 252300 / 612x792 / DCT,Flate
- fit_dpi200 8 / 252168 / 324x216 / DCT,Flate
- fit_dpi200_maxdpi100 8 / 74757 / **324x216**（F3 修复前是 162x108）
- keep_maxdpi0 8 / 252352
- encode_jpeg_gray 8 / 228593 / 只有 DCT
- encode_png_maxdpi80 8 / 870358 ← PNG 重编码最亏（F7 的由来）
- min_side_dedup 6 / 251117
- sort_name_reverse 8 / 252352
- a4_ascii85_on 8 / **312526**（= 修复前 312527，证明只改壳未改像素）
- strict_guard rc=1 untouched=True before=after=191321 parts=[]

## 复现命令
```powershell
$py = "C:\Users\ASUS\AppData\Local\Programs\Python\Python313\python.exe"
& $py selftest_v2.py                    # 60 passed, 0 failed
cd .audit_me
& $py regression_matrix.py --out after4.json
& $py check_alpha.py                    # 透明度逐像素验证
& $py peak.py "label" -- ..\..\某目录 -o out --encode jpeg --max-dpi 0   # 峰值内存采样
```

## 子代理 C（静默丢图 / 资源 / 破坏性）—— 已回收并修复
C 在 966 行版本上重跑过全部结论，四个真 bug 已修（F11–F14）：
- F1 原子写入 ✅ C 独立复现：save() 失败/旧 PDF 只读 → exit 1、旧文件字节不变、无 `.part` 残留。
- F4 坏图兜底 ✅（扫描阶段与嵌入阶段分别验证）。
- 隐藏文件（`.hidden.png`、`attrib +H`）✅ 都会入选；`--dedup` 是 SHA-256 全字节比对，400 张不同图不误删。
- 文件名/路径边缘（中文、俄文、emoji、单引号、`%`、`!`、`&`、前导 `-`、空格）、order 文件的
  CRLF/LF/BOM/缺失名/重名/空文件、`-n` 已存在名覆盖、`-o` 指向文件 → 全部符合预期（12/12 页逐一验证）。
- 两个进程同时跑同一目标：都 exit 0，最终文件完整（`%PDF-1.3` + `%%EOF`）。
- C 未测：junction 指向 symlink 的环（本机建 symlink 被拒 WinError 1314）。

### F11 扩展名不支持但内容是图片 → 静默丢弃（C 发现）
`iter_image_paths` 在扫描前就按扩展名筛掉，`collect_images` 根本没机会记录。
修复：`iter_image_paths` 现在产出所有文件（并做真实路径去重），扩展名过滤移到 `collect_images`，
用 `looks_like_image()`（magic number + `ftyp`）判断，命中就记 `unsupported file type '...' (looks like an image)`。
`.txt`/`.pdf` 等真文本文件仍完全不打扰。

### F12 `--strict` 管不到扫描阶段筛掉的图（C 发现）
0 字节 `bad.jpg` 在 `scan_image` 就被跳过，`--strict` 依旧 exit 0。修复：`collect_images` 之后
按 `is_fatal_skip()`（`INTENTIONAL_SKIP_MARKERS` = min-side / dedup / only-numbered / unsupported 之外的都算致命）
汇总，`--strict` 时 `--strict: N image(s) could not be used:` 退出。

### F13 `-r` 跟着 junction 进环 → 重复页（C 发现）
`rglob` 不管 junction；自指 junction 曾产出 43 页、`nested.png` 出现两次。修复：自写 `walk()`，
用 `os.path.realpath` 记录已访问目录，每个真实目录只进一次。

### F14 `--encode jpeg` 内存随总像素线性增长（C 发现）
C 实测 10/20/30 张 3000×2000 → 515/955/1395 MB（每张约 44 MB）。我先前「只留 buffer、丢掉 PIL 图像」
的改法**无效**（182/89/535 MB 前后一致）——大头在 reportlab 内部：给 `drawImage` 一个缓冲区，
它会解码成 RGB 再重新压。真正修复：`--encode jpeg` 先把重编码结果写到 `%TEMP%` 的 `.jpg`，
把**路径**交给 reportlab，走 `jpg_imagedata` 原字节透传。
实测 10 张 3000×2000 PNG：峰值 **535 MB → 120 MB**，墙钟 1.51 s → 0.91 s，
从 PDF 里抽出的 10 条 DCT 流 payload 与修复前**逐字节相同**（PDF 整体只差 2 字节）。
临时文件由 `Prepared.cleanup` + `images_to_pdf` 的 `finally` 删除（成功/失败路径都测过不留残留）。

### F15 并发同名临时文件（C 的并发用例顺带发现）
`.part` 名字里加上 pid（`<名字>.pdf.<pid>.part`），两个进程同时写同一目标不会互相踩。

基线复查（`after4.json`）：a4_default 252351、a4_ascii85_on 312526（对修复前 312527 仍逐字节等价）、
`fit_dpi200_maxdpi100` 首页框 324x216、`encode_jpeg_gray` 只有 DCTDecode、`encode_png_maxdpi80` 870358、
`strict_guard` rc=1 untouched=True parts=[]。页数/页面框/滤镜全部未变；逐字节差异只出现在我这次
`--encode jpeg` 改动之后（payload 已单独证明相同）。
