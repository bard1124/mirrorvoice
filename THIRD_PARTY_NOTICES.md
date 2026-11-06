# Third-party software and models

同传助手 itself is MIT-licensed. It bundles or downloads the following components, each under
its own license.

## Bundled in the installers

| Component | License | Where |
|---|---|---|
| [BlackHole](https://github.com/ExistentialAudio/BlackHole) 2ch / 16ch installer packages (macOS) | GPL-3.0 — unmodified upstream installers; source at the link | macOS app `Contents/Resources` |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server` (Windows) | MIT | Windows install folder `llama\` |
| [uv](https://github.com/astral-sh/uv) (Windows, installs the optional GPU voice pack) | MIT / Apache-2.0 | Windows install folder |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), onnxruntime | Apache-2.0 / MIT | both |
| [MLX](https://github.com/ml-explore/mlx), [mlx-lm](https://github.com/ml-explore/mlx-lm), [mlx-audio](https://github.com/Blaizzy/mlx-audio) (macOS) | MIT | macOS |
| [Qt for Python (PySide6)](https://www.qt.io/qt-for-python) | LGPL-3.0 (dynamically linked, unmodified) | both |
| PortAudio / python-sounddevice, soundfile, numpy, pynput, num2words, platformdirs, soundcard | MIT / BSD / LGPL-3.0 (pynput) | both |

## Downloaded on first run (not redistributed by this project)

| Model | License |
|---|---|
| [SenseVoiceSmall](https://github.com/FunAudioLLM/SenseVoice) (sherpa-onnx int8 export) | FunASR model license |
| [Silero VAD](https://github.com/snakers4/silero-vad) | MIT |
| [Qwen3-4B / 8B](https://modelscope.cn/models/Qwen) (MLX / GGUF) | Apache-2.0 |
| [Qwen3-TTS 12Hz Base](https://github.com/QwenLM/Qwen3-TTS) (MLX ports by mlx-community, or original) | Apache-2.0 |
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) (sherpa-onnx export) | Apache-2.0 |

## Downloaded on demand by the user

| Component | License |
|---|---|
| [VB-CABLE](https://vb-audio.com/Cable/) (Windows virtual audio cable) | Donationware — downloaded directly from vb-audio.com by the setup wizard, not redistributed |
| PyTorch + [qwen-tts](https://pypi.org/project/qwen-tts/) (Windows GPU voice pack) | BSD-3 / Apache-2.0 |

Voice cloning: only clone your own voice, or a voice whose owner has given consent.
