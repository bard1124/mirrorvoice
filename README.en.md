<p align="center"><a href="README.md">简体中文</a> | <b>English</b></p>

<p align="center"><img src="packaging/assets/icon.png" width="120" alt="TongChuan"></p>

<h1 align="center">TongChuan 同传助手</h1>

<p align="center"><a href="https://github.com/bard1124/tongchuan/releases/latest"><img alt="Download latest release" src="https://img.shields.io/github/v/release/bard1124/tongchuan?label=Download%20latest%20release"></a> <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue"> <img alt="macOS | Windows" src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey"></p>

<p align="center">
<b>Local real-time interpretation: you speak Chinese, the other side of the call hears English in <i>your own voice</i>; they speak English, you read Chinese subtitles.</b><br>
Runs entirely on your machine · works offline · no API keys · macOS (Apple Silicon) / Windows
</p>

---

## Features

| | |
|---|---|
| 🎙️ **Your voice, another language** | Record a 12-second sample; Qwen3-TTS zero-shot cloning speaks the translation in your voice into Teams / Zoom / Meet / Tencent Meeting |
| 💬 **Live subtitles for the other side** | Their speech is recognised and translated into an always-on-top, draggable subtitle bar (visible over full-screen meetings) |
| ⚡ **Low latency** | About 1.5 s from the end of your sentence until they hear it (measured on an M1 Pro); your speech always jumps ahead of subtitle work |
| 🔒 **Fully local** | SenseVoice ASR, Qwen3 translation and Qwen3-TTS run on-device; after the first model download no network is needed |
| 📖 **Glossary** | Fixed translations for product names, people and jargon, applied in both directions |
| 📝 **Meeting transcripts** | Every session is saved as a bilingual Markdown file + CSV |
| 🎛️ **Push-to-talk / mute** | Global hotkeys: hold Right Option/Alt to talk, or ⌃⌥M (Ctrl+Alt+M) to mute |
| 🔁 **Passthrough** | When you already speak their language, your real voice is sent untranslated |
| 🌏 **Languages** | Chinese / English / Japanese / Korean in any direction (Cantonese as input) |
| 🖥️ **Two front ends** | Graphical control panel (default) or a terminal-style view with coloured logs (type `m` mute / `q` quit / `ui` switch back) |
| 🧭 **First-run wizard** | Downloads models (ModelScope/GitHub, resumable), installs the virtual audio cable, records your voice, explains the meeting-app setup |

## Install

Download from [Releases](../../releases):

- **macOS (Apple Silicon, macOS 14+)**: `TongChuan-x.y.z-macos-arm64.dmg`, drag to Applications. The app is not notarized: the first time, right-click → Open.
- **Windows 10/11 x64**: `TongChuan-x.y.z-windows-x64-setup.exe`. Not code-signed: on the SmartScreen prompt choose "More info → Run anyway".

The setup wizard starts on first launch. Models are about 4.5 GB (about 8 GB in high-quality mode).

### Platform support

| | macOS Apple Silicon | Windows + NVIDIA GPU | Windows without NVIDIA |
|---|---|---|---|
| Recognition / subtitles | ✅ | ✅ | ✅ |
| Translation | MLX (Qwen3) | llama.cpp Vulkan/CUDA | llama.cpp CPU/Vulkan |
| Spoken translation | ✅ your cloned voice | ✅ your cloned voice (install the ~3 GB GPU voice pack from the wizard) | ✅ standard English voice (Kokoro, not your voice) |
| Virtual microphone | BlackHole 2ch (bundled) | VB-CABLE (downloaded by the wizard) | VB-CABLE |
| Other side's audio | BlackHole 16ch + Multi-Output device (created by the wizard) | speaker loopback (no setup) | speaker loopback |

## Meeting app setup

| | Microphone | Speaker |
|---|---|---|
| macOS | BlackHole 2ch | Teams 扬声器 (耳机+字幕) — the headphones + BlackHole 16ch Multi-Output device |
| Windows | CABLE Output (VB-Audio Virtual Cable) | your headphones |

Use headphones so the spoken translation is not picked up by your microphone again.

## How it works

```
your mic      → Silero VAD → SenseVoice ASR → Qwen3 translation → Qwen3-TTS (your voice) → virtual mic → meeting app
meeting audio → loopback / BlackHole 16ch → SenseVoice ASR → Qwen3 translation → subtitle overlay
```

A single model thread owns every MLX/LLM/TTS object; your speech (priority 0) always goes ahead of subtitles (priority 1).

## Run from source

```bash
# macOS (Apple Silicon)
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-mac.txt
.venv/bin/python -m tongchuan            # control panel
.venv/bin/python -m tongchuan cli        # terminal-style view
.venv/bin/python -m tongchuan download   # download models only
```

Packaging: macOS `bash packaging/mac/build_dmg.sh`; Windows is built by GitHub Actions (`.github/workflows/build.yml`; pushing a `v*` tag publishes a Release).

Data folder: macOS `~/Library/Application Support/TongChuan`, Windows `%LOCALAPPDATA%\TongChuan` (models, voice sample, transcripts, glossary, settings, logs).

## Credits

Feature ideas from [LiveTranslate](https://github.com/NBS282/LiveTranslate) (push-to-talk, passthrough, setup wizard), [My Translator](https://github.com/phuc-nt/my-translator) (two-way translation, glossary, transcripts) and [LiveCaptions-Translator](https://github.com/SakiRinn/LiveCaptions-Translator) (subtitle styling, history export). Model and component licenses: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Responsible use

Only clone your own voice, or a voice whose owner has consented. Let other participants know you are using live translation.

## License

[MIT](LICENSE)
