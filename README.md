# Paper to Anything

基于 `paper2anything` 工作流改造的 Windows 桌面原型，不依赖 Codex/Claude，也不使用 MinerU。

当前已实装五条流水线：

- **论文转网页**：生成 `index.html` 和配套 `images/`。
- **论文转公众号**：生成 Markdown、JSON、样式化 HTML 和公众号封面。
- **论文转海报**：生成横向会议海报 `poster.html` 与 `poster.png`。
- **论文转小红书**：生成文案、JSON、3:4 封面和可直接发布的论文配图。
- **论文转幻灯片**：生成 16:9 `slides.pptx`、逐页演讲备注、内容 QA 和可选 AI 视觉 QA。

## 环境

```powershell
cd Paper_to_anything
pip install -r requirements.txt
```

依赖 Python 3.10+。GUI 使用 PyQt5，`pip install -r requirements.txt` 会一并安装。
界面字体统一使用项目内 `assets/HarmonyOS_Sans_SC_*.ttf` 的 Regular、Medium、Bold 三个字重，启动时自动注册，不依赖系统安装。

## GUI

```powershell
python GUI.py
```

使用步骤：

1. 点击右上角“API 设置”，输入硅基流动 API Key，或从文本文件读取。
2. 填写可用的文本模型名称并保存。
3. 在左侧选择输出任务，添加一个或多个 PDF/TXT/MD/HTML 文件。
4. 调整生成参数和输出目录，点击右侧开始按钮。

API Key 会通过 Windows DPAPI 加密后保存到 `%APPDATA%\PaperToAnything\settings.json`，下次启动自动载入；不会写入程序源码或输出目录。

## CLI

列出账号可用模型：

```powershell
python GUI.py --cli --list-models --api-key-file .\sil-key.txt --model deepseek-ai/DeepSeek-V4.1-Flash
```

生成网页：

```powershell
python GUI.py --cli --task html `
  --input "D:\path\paper.pdf" `
  --api-key-file .\sil-key.txt `
  --model deepseek-ai/DeepSeek-V4.1-Flash `
  --output-dir .\outputs
```

生成公众号文章：

```powershell
python GUI.py --cli --task wechat `
  --input "D:\path\paper.pdf" `
  --api-key-file .\sil-key.txt `
  --model deepseek-ai/DeepSeek-V4.1-Flash `
  --output-dir .\outputs
```

生成海报、小红书图文和幻灯片：

```powershell
python GUI.py --cli --task poster --input "D:\path\paper.pdf" --api-key-file .\sil-key.txt --output-language 中文
python GUI.py --cli --task xhs --input "D:\path\paper.pdf" --api-key-file .\sil-key.txt --output-language 中文
python GUI.py --cli --task slides --input "D:\path\paper.pdf" --api-key-file .\sil-key.txt --slides-length 10
```

关闭幻灯片 AI 视觉审查：

```powershell
python GUI.py --cli --task slides --input "D:\path\paper.pdf" --api-key-file .\sil-key.txt --no-visual-qa
```

启用免费模型生成公众号封面：

```powershell
python GUI.py --cli --task wechat `
  --input "D:\path\paper.pdf" `
  --api-key-file .\sil-key.txt `
  --model deepseek-ai/DeepSeek-V4.1-Flash `
  --ai-cover `
  --image-model "Kwai-Kolors/Kolors"
```

多个文件启用 AI 封面时，程序会强制让相邻两次生图请求间隔至少 20 秒。

## 开发流程

1. `app/sources.py` 读取 PDF/TXT/MD/HTML，统一成正文、标题和图片列表。
2. PDF 使用 PyMuPDF 做本地文字和图片提取；HTML 会自动下载 `<figure>` 中的远程论文图片并保存为本地 PNG，忽略页面图标和徽标。
3. 若超过 80% 的页面有效文字少于 20 字符，判定为扫描版/图片型 PDF，导入阶段直接报错。
4. `app/siliconflow.py` 封装硅基流动文本模型和生图接口。
5. `app/pipelines.py` 把五条提示词流水线与输出文件生成串起来。
6. `app/render.py` 负责公众号样式、海报 PNG、小红书封面和 python-pptx 幻灯片渲染。
7. `app/render.py` 同时负责 Markdown 转 HTML 与本地封面合成。
8. `GUI.py` 同时提供 PyQt5 GUI 和 CLI。

## 输出结构

```text
outputs/
├── html/
│   └── <文件名>/
│       ├── index.html
│       ├── images/
│       ├── source.txt
│       └── job.json
├── wechat/
    └── <文件名>/
        ├── wechat_article.md
        ├── wechat_article.json
        ├── wechat_article.html
        ├── cover.jpg
        ├── images/
        ├── source.txt
        └── job.json
├── poster/
│   └── <文件名>/
│       ├── poster.html
│       ├── poster.png
│       ├── poster_spec.json
│       └── images/
├── xhs/
│   └── <文件名>/
│       ├── xhs_post.md
│       ├── xhs_post.json
│       ├── cover.png
│       └── post_images/
└── slides/
    └── <文件名>/
        ├── slides.pptx
        ├── slide_outline.json
        ├── slide_spec.json
        ├── qa_log.json
        └── images/
```

## 当前限制

- 不支持扫描版/纯图片 PDF，也不做 OCR。
- 复杂公式、表格和插图裁切质量低于 MinerU。
- HTML 只自动收集 `<figure>` 内的远程图片；正文之外或非标准图注结构的图片可能被忽略。
- 小红书发布与公众号自动上传尚未接入，当前只生成发布所需文件。
- 海报固定为 2400×1600 横向版式；幻灯片固定为 16:9。
- 幻灯片内容 QA 和视觉 QA 各执行一轮；视觉问题会应用一次布局修正，但不做第二轮复查。
- 视觉 QA 需要本机 PowerPoint 或 LibreOffice 将 PPTX 转成预览图。
- 输出超过约 50000 字符的原文会被截断，以免超过文本模型上下文。
- 程序直接连接 `https://api.siliconflow.cn/v1`；如所在网络必须经过代理，需要后续增加代理设置。

Apache-2.0 原项目来源：`paper2anything`。
---