# images_to_pdf_v2 — 图片批量合成 PDF（自动排序版）

`images_to_pdf_v2.py` 是 `images_to_pdf.py` 的重写版，原脚本保持原样未动，方便对照。

核心变化：**不再需要把图片重命名成 1.jpg、2.jpg**。默认按「时间」排序，
截图直接丢进文件夹就能用；旧的数字命名模式仍保留为 `--sort number`。

依赖不变：`python -m pip install pillow reportlab`

---

## 1. 常用命令

最省事的方式：把截图文件夹**拖到 `合成PDF.bat` 上**松手；或者双击它，按提示粘贴文件夹路径。

```powershell
python images_to_pdf_v2.py                          # 当前文件夹，自动排序
python images_to_pdf_v2.py "D:\截图" -n 复习资料     # 指定文件夹与输出名
python images_to_pdf_v2.py "D:\截图" --print-order   # 只看排序结果和每页用的时间戳
python images_to_pdf_v2.py "D:\截图" --dry-run       # 只预览，不生成 PDF
python images_to_pdf_v2.py "D:\截图" --sort number   # 旧行为（数字文件名）
python images_to_pdf_v2.py "D:\截图" -r --min-side 400 --dedup
python images_to_pdf_v2.py "D:\截图" --page fit --margin 0
python images_to_pdf_v2.py --ask                     # 在终端里问你要哪个文件夹
```

这些命令**在哪个目录下敲都可以**，不必先 `cd` 到脚本文件夹：把 `python images_to_pdf_v2.py`
换成脚本的完整路径（或用 `C:\Users\ASUS\Desktop\图片整合脚本\合成PDF.bat`）就行。
`合成PDF.bat` 自己就是这么做的——它用 `%~dp0` 定位同目录的脚本，所以从任何地方调用都成立。
输出默认落在 `<图片文件夹>/output/pdf/`，与原脚本一致。

## 2. 排序策略 `--sort`

| 取值 | 含义 |
| --- | --- |
| `auto`（默认） | 自动挑最可信的键：有 EXIF 拍摄时间就用它；否则比较文件创建时间与修改时间，并识别「批量复制」特征 |
| `exif` | 图片自带的 EXIF `DateTimeOriginal` / `DateTimeDigitized` / `DateTime` |
| `created` | 文件创建时间（Windows `st_ctime`、macOS `st_birthtime`） |
| `modified` | 文件修改时间（复制、解压、网盘同步后仍然保留，最稳） |
| `name` | 文件名自然序（`shot2` 排在 `shot10` 前），递归时先按子目录分组 |
| `number` | 纯数字文件名排序，等价于旧脚本 |

任何时间排序出现并列时，都会退化为文件名自然序，结果永远确定。
`--print-order` 会逐页打印用的时间戳，并统计并列数量。

### auto 在做什么

1. 90% 以上的图片带 EXIF 拍摄时间 → 用 `exif`；
2. 否则看创建时间：如果**大部分文件的创建时间挤在几秒内、而修改时间是散开的**，
   这就是「刚被批量复制过」的特征（复制会刷新创建时间、保留修改时间），
   此时用 `modified`；
3. 两者都正常时用 `created`；创建时间拿不到（Linux 等）用 `modified`；
4. 所有时间都完全相同（同一秒内生成）时，明确提示并退回文件名顺序。

判断依据会打印出来，例如
`auto chose 'modified': creation times cluster inside a few seconds ...`，
不会悄悄替你决定。

## 3. 其他新增能力

**选择范围**：`-r/--recursive` 递归子目录（遇到指回自身或上层的 junction 会按真实路径剪掉，
不会把同一张图排两遍）、`--min-side PX` 丢掉缩略图/小图标、`--dedup` 跳过内容完全相同的重复图。
扩展名不在支持列表、但内容确实是图片的文件（例如被手改过后缀的截图）会列进 `Skipped` 并给出原因，
不再静默消失。
`--strict` 的含义是「只要有一张图没能进 PDF 就报错退出」：读不了、不是图片、超过 Pillow 像素上限都算；
有意过滤掉的（`--min-side`、`--dedup`、`--only-numbered`）不算。

**页面排版**：`--page a4|letter|fit`（`fit` = 每页尺寸跟随图片比例）、
`--margin MM`、`--max-dpi`（默认 200，超过则降采样并打印一行提示，`0` 关闭）、
`--encode keep|jpeg|png`、`--quality`、`--grayscale`。

**体积控制**：`--encode keep`（默认）的实际含义，取决于文件是不是 JPEG——
reportlab 只对 JPEG 做原字节透传：

- JPEG：原始字节原样塞进 PDF（真·零损失、体积最小）；
- PNG/BMP/TIFF 等：必须先解码再重新压缩，仍然**无损**，但比原文件大得多
  （几 KB 的纯色 PNG 截图，嵌进 PDF 后可能变成几 MB）；
- 屏幕截图想要小体积，用 `--encode jpeg --quality 90`，体积常常只有 keep 的几分之一，
  代价是文字边缘略软。

`--encode jpeg` 会先把重编码结果写到系统临时目录里的 `.jpg`，跑完自动清理（包括失败退出时）：
只有拿到一个 `.jpg` **路径**，reportlab 才会原样透传 JPEG 流；给它一个内存里的缓冲区，
它会解码成 RGB 再压一遍——又慢又占内存。实测 10 张 3000×2000 的 PNG，
峰值内存从 535 MB 降到 120 MB，而嵌进 PDF 的 JPEG 载荷逐字节不变。

**透明度**：带 alpha 的 PNG（RGBA、调色板透明）会先合成到**白底**再嵌入。
reportlab 会把 alpha 通道直接丢掉，不处理的话，透明区域会露出图片内部原本存着的颜色
（很多编辑器存的是黑色），看起来就是莫名其妙的黑块。

**PDF 内部编码**：`--pdf-ascii85 off|on`（默认 `off`）。PDF 允许二进制流，但 reportlab
默认把每个流用 ASCII85 包一层（体积 +25%）。默认关掉后整册体积约减 19%，现代阅读器都认；
万一遇到很老的软件打不开，加 `--pdf-ascii85 on` 回到 reportlab 原样。

**DPI**：`--page fit` 用**整批统一**的 `--dpi`（默认 150）换算页尺寸，图片自带的 DPI
元数据（PNG pHYs / JFIF density）不参与计算——一册里页尺寸保持一致更好预期。
如果你的图自带 300 dpi 的物理尺寸，显式加 `--dpi 300` 就行。

**人工干预（可选）**：`--save-order order.txt` 导出当次页序，
`--order-file order.txt` 按文件里的顺序重排；文件里没覆盖到的图片会追加在末尾。
想微调顺序时「先生成 → 改 order.txt → 再生成」即可。

## 4. 自检

```powershell
python selftest_v2.py            # 60 项断言，覆盖各排序模式、排版、选择过滤与写入安全
python selftest_v2.py --keep     # 保留 .selftest 样例目录，便于人工查看
```

覆盖：auto 选 `created` / 自动识破批量复制改选 `modified` / EXIF 优先 /
自然序 / 数字序 / 去重 / 最小尺寸 / 坏图跳过 / A4 与 fit 页尺寸 / `--max-dpi` 降体积 /
透明 PNG 压白底 / 写入被拒（磁盘满、目标被占用）时旧 PDF 不被破坏 /
order 文件读写 / `--ask` 的路径解码（UTF-8、GBK、带 BOM）/ 参数校验。

## 5. 这一轮审计修掉的问题

整合成 PDF 的那半段后来单独查了一遍（含逐字节比对嵌进 PDF 的像素），修掉这些：

1. **原子写入**：先写同目录的 `<名字>.pdf.part`，全部成功后才替换正式文件。
   以前直接写最终路径，中途出错（磁盘满、PDF 被阅读器占用、坏图中止）就可能留下半截 PDF
   或把上一版好文件毁掉；现在失败路径一律删除临时文件、保留旧文件，并明确告知。
2. **参数校验**：`--quality/--dpi/--max-dpi/--margin/--min-side` 越界、`--margin` 大到页面没空间、
   `-n` 为空或带路径分隔符、`-o` 指向已存在的文件——全部在动手之前报错退出。
3. **`--page fit` 与 `--max-dpi` 的页尺寸**：页尺寸改由**原图像素**决定，降采样只降低嵌入分辨率，
   不再把物理页悄悄改小（以前 `fit` + `max-dpi` 会让页面按降采样后的尺寸缩水）。
4. **坏图不再毁掉整册**：单张图嵌入时捕获所有异常（以前只捕 3 类），跳过并记账，最后统一提示；
   `--strict` 仍然立刻中止。
5. **透明 PNG 压白底**（见 §3）。
6. **`--pdf-ascii85 off` 默认**，整册体积约减 19%。
7. **极小页面不再溢出**：1 px 图配超大 `--dpi` 生成的亚 1 pt 页面，绘制会被收缩到页面之内；
   页尺寸打印保留一位小数。
8. 权限提示、`--ask` 路径解码、纯 ASCII 启动器等一串问题（见 §6）。
9. **静默丢图**：扩展名不支持但内容确实是图片的文件以前会无声无息地消失，现在列进 `Skipped`；
   `--strict` 以前只管「扫描之后」的失败，现在扫描阶段就没能用的图也会中止；`-r` 以前会跟着
   junction 钻进自身，把同一张图排两遍（自指 junction 曾产出 43 页），现在按真实路径去重。
10. **`--encode jpeg` 的内存**：改用临时 `.jpg` 中转后 reportlab 走 DCT 原字节透传，
    峰值内存约降到 1/4.5，输出逐字节可复现（见 §3）。

自检脚本里对应的断言就在 §4；`--pdf-ascii85 on` 能逐字节复现改动前的输出，
所以“只改了壳子没改像素”这件事是可验证的。

## 6. 拖拽启动器、随便哪个目录、以及权限（实测结论）

### `合成PDF.bat` 能做什么

| 用法 | 效果 |
| --- | --- |
| 把图片文件夹拖到它上面 | 直接合成（文件夹里有空格也没关系） |
| 双击它、不拖东西 | 在终端里问你要哪个文件夹（等价于 `--ask`，直接回车 = 脚本所在目录） |
| `合成PDF.bat "D:\截图" --sort name -n 复习资料` | 后面可以继续跟脚本自己的参数，原样转发 |
| 右键 → 以管理员身份运行 | 需要写受保护目录时才用，见下 |

启动器只负责“找 Python、转发参数”，真正的逻辑都在 `images_to_pdf_v2.py` 里，所以参数与脚本完全一致。

**为什么它是纯英文 ASCII？** 批处理文件里的非 ASCII 字节会让 `cmd.exe` 算错下一行的偏移量，
实测出现过整行被吞掉（`'出位置' is not recognized as an internal or external command`）。
所以 `.bat` 里只放 ASCII，所有中文提示都放在 Python 和本文档里——Python 走 Windows 控制台的
Unicode 接口，任何代码页都安全。文件名 `合成PDF.bat` 本身是 Unicode，这没问题：文件名是数据，
不参与批处理解析。

### 要不要管理员权限

脚本本身**不需要**管理员。它只做两件事：读图片、写 PDF。真正决定成败的是
「你对所碰的文件夹有没有写权限」，而这取决于那个文件夹的 ACL，与是不是管理员无关。

本机实测（未提权、普通用户 `ASUS`）：

| 位置 | 结果 | 依据 |
| --- | --- | --- |
| `D:\` 根目录 | 能读，**不能新建/修改** | 只有 `BUILTIN\Administrators` 有 `(OI)(CI)(F)`，`Everyone` 只有 `(OI)(CI)(RX)` |
| `D:\AI` 这类普通目录 | 同上（继承自 `D:\`） | 只继承到管理员那条 ACE |
| `D:\bilibili`、`D:\7-Zip` | **能写** | 另外显式给了 `NT AUTHORITY\Authenticated Users:(M)` |
| `C:\Users\ASUS\Desktop` 等个人目录 | 能写 | 本人 `(OI)(CI)(F)` |

所以“D 盘都要管理员”只对**一部分**目录成立：D 盘根目录及其未额外授权的子目录确实如此，
但有些安装目录反而给了普通用户修改权。同一个盘里逐目录不同，这就是 Windows ACL 的真实样子
（`icacls "D:\"` 和 `icacls "D:\bilibili"` 的输出就不一样）。

自己验证某个文件夹能不能写：

```powershell
$d = "D:\你的文件夹"
New-Item -ItemType File -Path "$d\.write-test" -Force | Out-Null
Remove-Item "$d\.write-test"
```

写不进去会直接报 Access denied。

### 三个解决办法（按推荐顺序）

1. **输出到你有权写的地方**（最省事）：
   ```powershell
   python images_to_pdf_v2.py "D:\截图" -o "$env:USERPROFILE\Desktop\pdf"
   ```
2. **一次性给自己那个文件夹的写权限**（需要管理员 PowerShell，只影响这一个文件夹）：
   ```powershell
   icacls "D:\你的文件夹" /grant *S-1-5-32-545:(OI)(CI)M /T
   ```
   `*S-1-5-32-545` 就是 `BUILTIN\Users`，用 SID 写可以避开中英文组名差异；`/T` 递归子目录。
3. **以管理员身份运行**：右键 `合成PDF.bat` → 以管理员身份运行。
   副作用：生成的文件属主会变成 `Administrators`，以后用普通身份删/改它可能又要提权，
   所以能用 1、2 就别用 3。

写不进去时脚本不再甩 traceback，而是打印一段提示，直接列出上面三个办法。

## 7. 后续可扩展方向（按性价比排序）

1. **多图合一页**：`--per-page N` 或 2-up 拼版，适合把长截图切块后拼回整页。
2. **分组出多个 PDF**：`--split-by day|folder|index`，按天/按子目录各出一个 PDF
   或插入分隔页。
3. **切分长截图**：自动按空白行切分超长截图并铺满多页，比缩小到看不清更实用。
4. **图片处理**：自动裁掉边缘黑边/白边、去水印区、`--rotate {auto,90,180,270}`、
   对比度增强（`--autocontrast`）。
5. **OCR 辅助排序/命名**：用 OCR 提取标题或页码做排序键，或把识别文本写成 PDF 书签，
   这是「图源不限于截图」时最有用的一步。
6. **书签与目录**：`--toc` 按子目录/日期生成 PDF 大纲，几百页资料才好跳转。
7. **其他输入源**：直接吃 PDF 页（pdf2image）、剪贴板图片、网页长截图目录。
8. **重复检测升级**：从 SHA-256 精确去重升级为感知哈希（pHash），能识别二次压缩过的同一页。
9. **GUI / 拖拽**：PySide6 或 tkinter 套一层，文件夹拖进去点一下就好；
   排序结果可拖拽微调后导出 order 文件。
10. **配置化**：`.pdfsort.toml` 记住常用参数（页面、dpi、输出名）。
11. **按图片自带 DPI 排版**：`--fit-dpi auto`，让 `--page fit` 用每张图的 pHYs/JFIF 密度
    决定物理页尺寸（现在是整批统一 `--dpi`，见 §3）。
