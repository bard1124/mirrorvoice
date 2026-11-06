"""Qwen3-TTS voice-clone server for the Windows GPU voice pack (torch + qwen-tts, CUDA).

Runs inside the separate gpu-voice venv. Protocol on stdin/stdout:
  -> one JSON line per request: {"text": "...", "lang": "english"}
  <- 8-byte header <uint32 sample_rate, uint32 n_samples> then n_samples float32 (little endian)
Prints "READY" once the model and the reusable voice prompt are loaded.
"""
import json
import struct
import sys

import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel


def main() -> None:
    model_dir, ref_wav, ref_txt = sys.argv[1:4]
    out = sys.stdout.buffer
    model = Qwen3TTSModel.from_pretrained(model_dir, device_map="cuda:0", dtype=torch.bfloat16)
    audio, sr = sf.read(ref_wav, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    with open(ref_txt, encoding="utf-8") as f:
        text = f.read().strip()
    prompt = model.create_voice_clone_prompt(ref_audio=(audio, sr), ref_text=text, x_vector_only_mode=False)
    model.generate_voice_clone(text="Hello.", language="English", voice_clone_prompt=prompt)  # warm-up
    out.write(b"READY\n")
    out.flush()
    for line in sys.stdin:
        req = json.loads(line)
        lang = {"auto": "Auto"}.get(req.get("lang", "auto"), req.get("lang", "auto").capitalize())
        wavs, osr = model.generate_voice_clone(text=req["text"], language=lang, voice_clone_prompt=prompt)
        wav = np.asarray(wavs[0], dtype=np.float32).reshape(-1)
        out.write(struct.pack("<II", int(osr), len(wav)))
        out.write(wav.tobytes())
        out.flush()


if __name__ == "__main__":
    main()
