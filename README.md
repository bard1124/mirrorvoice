<p align="center"><b>简体中文</b> | <a href="README.en.md">English</a></p>

<p align="center"><img src="packaging/assets/icon.png" width="120" alt="MirrorVoice"></p>

<h1 align="center">镜声 MirrorVoice</h1>

<p align="center"><a href="https://github.com/bard1124/mirrorvoice/releases/latest"><img alt="下载最新版" src="https://img.shields.io/github/v/release/bard1124/mirrorvoice?label=下载最新版"></a> <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue"> <img alt="macOS | Windows" src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey"></p>

<p align="center">
<b>本地实时同声传译：你说中文，会议对方听到用你自己声音说的英文；对方说英文，你看到中文字幕。</b><br>
全部在本机运行 · 不联网 · 不需要 API Key · macOS (Apple Silicon) / Windows
</p>

---

## 功能

| | |
|---|---|
| 🎙️ **你的声音说外语** | 录 12 秒样本，用 Qwen3-TTS 零样本克隆你的音色，把译文用"你的声音"送进 Teams / Zoom / Meet / 腾讯会议 |
| 💬 **对方说话出字幕** | 会议里对方的声音实时识别并翻译，屏幕底部悬浮字幕条（置顶、可拖动、全屏会议上方也可见） |
| ⚡ **低延迟** | 说完一句约 1.5 秒对方听到（M1 Pro 实测）；你的发言优先于字幕处理。每句显示识别 / 翻译 / 发声各花多少时间，并提示瓶颈 |
| 🔒 **默认完全本地** | 识别 SenseVoice、翻译 Qwen3、合成 Qwen3-TTS 都在本机；模型下载后可断网使用 |
| 🎧 **可选在线语音识别** | 阿里云百炼 Fun-ASR 流式识别：对方说话时字幕边说边出、整句后换成译文，长句按句号自动分段；只在有人说话时上传，失败自动改回本机识别 |
| ☁️ **可选在线翻译** | 填 API key 改用 DeepSeek、豆包（火山方舟）、通义千问、硅基流动、OpenAI、OpenRouter，或任意 OpenAI 兼容地址（含本机 Ollama / LM Studio）；不用下载翻译模型，key 存在系统钥匙串 |
| 📖 **术语表与会议背景** | 产品名、人名、专业词固定译法，双向生效；可填“识别常错成”的写法（如 万佛马 → OneForma），翻译前自动纠正；一句话会议背景帮助选对专业用词 |
| 🩺 **一键测试链路** | 不用开会，自动检测对方能否听到你的翻译、对方字幕能否出来、麦克风和监听是否正常，并给出修复建议；Teams / Zoom / 腾讯会议 / 飞书分步设置说明 |
| 🎧 **设备防呆** | 每个声音设备标注用途和推荐项，选了会回声或对方听不到的组合会提示；macOS 换耳机后一键让会议扬声器跟着换 |
| 📝 **会议记录** | 每场会议自动保存中英对照 Markdown + CSV |
| 🎛️ **按住说话 / 一键静音** | 全局快捷键：按住右 Option/Alt 才翻译，或 ⌃⌥M 静音 |
| 🔁 **原声透传** | 你直接说对方的语言时，不翻译、把你的原声送过去 |
| 🌏 **多语言** | 中文 / 英语 / 日语 / 韩语（粤语可作为输入）任意组合 |
| 🖥️ **两种界面** | 图形控制面板（默认）或命令行风格（彩色日志，输入 `m` 静音 / `u` 更新 / `q` 退出 / `ui` 切回） |
| 🪟 **悬浮窗与托盘** | 对话悬浮窗（我和对方的原文 + 译文，置顶、可拖动缩放）、对方字幕条可常驻；托盘 / 菜单栏图标和 macOS Dock 右键菜单：开始/停止、暂停/继续、退出 |
| 🧭 **首次向导** | 自动下载模型（ModelScope / HuggingFace 自动选更快的源，大文件多线程，断点续传）、安装虚拟声卡、录音色、会议软件设置 |
| 🔄 **一键更新** | 启动时检查 GitHub 新版本（离线自动跳过），一键下载、SHA256 校验、替换并重启；模型和设置全部保留。命令行模式输入 `u` |

## 安装

从 [Releases](../../releases) 下载：

- **macOS（Apple Silicon，macOS 14+）**：`MirrorVoice-x.y.z-macos-arm64.dmg`，拖进「应用程序」。未经 Apple 公证，首次请右键 →「打开」。
- **Windows 10/11 x64**：`MirrorVoice-x.y.z-windows-x64-setup.exe`。未签名，SmartScreen 提示时点「更多信息 → 仍要运行」。

首次打开会进入设置向导，模型约 4.5 GB（高质量模式约 8 GB）。

### 各平台能力

| | macOS Apple Silicon | Windows + NVIDIA RTX 30 系及以上 | Windows 其它（无 N 卡，或 RTX 20 / GTX 16） |
|---|---|---|---|
| 识别 / 字幕 | ✅ | ✅ | ✅ |
| 翻译 | MLX (Qwen3) | llama.cpp Vulkan/CUDA | llama.cpp CPU/Vulkan |
| 说出译文 | ✅ 你的克隆音色 | ✅ 你的克隆音色（向导里装 GPU 音色包，约 3 GB） | ✅ 你的克隆音色（Pocket TTS，CPU 实时，仅英文输出，需用英文朗读录样本）；或标准音色 Kokoro |
| 虚拟麦克风 | BlackHole 2ch（安装包内置） | VB-CABLE（向导里下载） | VB-CABLE |
| 对方声音来源 | BlackHole 16ch + 多输出设备（向导自动创建） | 扬声器回采（无需设置） | 扬声器回采 |

GPU 音色包需要显卡原生支持 bf16（计算能力 ≥ 8.0，即 RTX 30 系及以上）。RTX 20 / GTX 16 只能模拟 bf16，实测 RTX 2060 合成 3 秒语音要 10 秒，fp16 则会溢出，所以这类显卡自动按"无 NVIDIA"处理；可在主界面"GPU 音色"里强制开启。

### 内存需求

全部功能开启（翻译 + 克隆音色 + 对方字幕）实测约 **5 GB** 内存（M1 Pro）；Windows CPU 方案相近。**16 GB 内存的电脑可以流畅运行**（建议用“快速”模型；“高质量”约多占 3 GB）。CPU 较老时翻译会慢一些（每句约 1–3 秒）。

8 GB 内存或老电脑可以选“极速”档（Qwen3-1.7B）：翻译速度约快一倍（M1 Pro 实测每句 0.36 秒，快速档 0.78 秒），但翻译质量明显较低。

### 在线翻译（可选）

左侧“在线模型”（或首次向导里的“改用在线翻译”）选服务商、填 API key、选模型，点“测试”会翻译一句话并显示耗时。

- 选了在线翻译就不再下载本地翻译模型（省约 1–5 GB），适合内存小或没有好显卡的电脑。
- API key 存在 macOS 钥匙串 / Windows 凭据管理器，不写进设置文件；也可用环境变量 `MIRRORVOICE_<服务商>_API_KEY`（如 `MIRRORVOICE_DEEPSEEK_API_KEY`）。macOS 每次更新后第一次读取会弹一次钥匙串授权，选“始终允许”即可。
- 只发送文字：你说的话（识别后的文字）、会议背景和用到的术语。声音不上传。对方说的话默认仍用本地模型翻译，勾选“对方说的话也用在线翻译”后才发给服务商。
- 为同传调过：每句 8 秒超时、最多快速重试一次；连续失败 3 次（或 key 无效）时，如果本地模型已下载，接下来 60 秒自动改用本地模型。推理型模型（如 deepseek-reasoner）每句要先“思考”，不适合同传。

### 在线语音识别（可选）

「在线模型」→「语音识别」：选"在线流式识别"，填阿里云百炼的 API key（和通义千问翻译是同一个 key），勾选用在"我说的话"和/或"对方说的话"，点「测试连接」。

- 对方说话时，字幕条先显示正在识别的原文；用在线翻译时，长句说到一半会先出一行临时译文，整句说完换成正式译文。
- 只在本机检测到有人说话时才上传声音（按识别时长计费），安静 15 秒后关闭这次识别任务。
- 长句中间不停顿时，按识别结果里连续两次都没变的句号、问号自动分段，不再每 5 秒硬切。
- 连续失败 3 次（或 key 无效）时，本次会议改用本机识别。

Mac 上本机识别可选 **Qwen3-ASR 1.7B**（「在线模型」→「语音识别」→ 本机识别模型）：英文和中英夹杂明显更准（实测加噪音的中英夹杂句错误率 36% → 2%），需下载 2.4 GB、运行时多占约 2.4 GB 内存，每句识别多 1.5–3 秒，所以推荐“只用于对方字幕”。术语表里的词会作为识别热词。

本机识别也改进了：长句被迫提前切断时，会退回到最后一个句号或逗号处切，剩下的半句并到下一句；普通话被误识别成粤语或日语时，按你设定的语言重新识别；翻译时带上同一个人最近 3 轮的原文和译文作为上下文。

### 公司网络 / 证书报错

模型下载和检查更新先用应用内置的证书库，校验失败时会自动改用系统证书（macOS 钥匙串 / Windows 证书存储）。公司网络如果做了 HTTPS 检查，只要公司根证书已经装进系统（浏览器能正常打开 GitHub 和 ModelScope 就说明装好了），下载就能正常进行。排查时可以设置环境变量 `MIRRORVOICE_TLS=certifi|system|default`，强制只用其中一个证书库；`MIRRORVOICE_MODEL_SOURCE=modelscope|huggingface` 可以固定模型下载源。

## 会议软件设置

| | 麦克风 | 扬声器 |
|---|---|---|
| macOS | BlackHole 2ch | Teams 扬声器 (耳机+字幕) |
| Windows | CABLE Output (VB-Audio Virtual Cable) | 你的耳机 |

请使用耳机，避免翻译语音被麦克风再次收进去。会议软件的降噪建议调低或关闭。控制面板“声音设备”里的“会议软件设置”有 Teams / Zoom / 腾讯会议 / 飞书的分步说明，“测试链路”可以在开会前检查整条线路。

## 工作原理

```
你的麦克风 → Silero VAD 断句 → SenseVoice 识别 → Qwen3 翻译 → Qwen3-TTS 你的音色 → 虚拟麦克风 → 会议软件
会议扬声器 → 回采 / BlackHole 16ch → SenseVoice 识别 → Qwen3 翻译 → 悬浮字幕
```

一个模型线程独占所有 MLX/LLM/TTS 对象，你的发言（优先级 0）总是插队到字幕（优先级 1）前面。

## 从源码运行

```bash
# macOS (Apple Silicon)
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-mac.txt
.venv/bin/python -m mirrorvoice            # 图形界面
.venv/bin/python -m mirrorvoice cli        # 命令行风格
.venv/bin/python -m mirrorvoice download   # 只下载模型
```

测试：`.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest -q tests`（CI 在 macOS 和 Windows 上都会跑，失败就不发版）。Windows 真机测试清单见 [docs/windows-test-checklist.md](docs/windows-test-checklist.md)。

打包：macOS `bash packaging/mac/build_dmg.sh`；Windows 由 GitHub Actions 构建（`.github/workflows/build.yml`，推送 `v*` 标签自动发布 Release）。

数据目录：macOS `~/Library/Application Support/MirrorVoice`，Windows `%LOCALAPPDATA%\MirrorVoice`（模型、音色样本、会议记录、术语表、设置、日志）。

## 参考与致谢

功能设计参考了 [LiveTranslate](https://github.com/NBS282/LiveTranslate)（按住说话、原声透传、首次向导）、[My Translator](https://github.com/phuc-nt/my-translator)（双向翻译、术语表、会话记录）、[LiveCaptions-Translator](https://github.com/SakiRinn/LiveCaptions-Translator)（字幕样式、历史导出）。模型与组件许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 负责任使用

音色克隆只用于你自己的声音，或已获得本人同意的声音。在会议中使用前，请告知参会者你在使用实时翻译。

## License

[MIT](LICENSE)
