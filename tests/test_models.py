import os
import threading

import pytest

from mirrorvoice import models


def test_every_quality_has_models():
    for q in models.QUALITIES:
        assert q in models.LLM and q in models.TTS_CLONE and q in models.QUALITY_LABEL
        assert models.llm_name(q).startswith("Qwen3-")
        assert len(models.required(q)) == 4
    assert models.llm_name("turbo") == "Qwen3-1.7B"


@pytest.mark.parametrize("total", [1, 7, 100, 1 << 20, (64 << 20) + 3])
def test_segment_ranges_cover_every_byte_once(total):
    rs = models.segment_ranges(total)
    assert rs[0][0] == 0 and rs[-1][1] == total - 1
    assert all(b[0] == a[1] + 1 for a, b in zip(rs, rs[1:]))
    assert len(rs) <= models.SEGMENTS


def _blob(n: int) -> bytes:
    return os.urandom(n)


def test_segmented_download_with_resume(http_files, tmp_path, monkeypatch):
    base, files, srv = http_files
    monkeypatch.setattr(models, "SEGMENT_MIN", 1000)
    data = _blob(10_000)
    files["/big.bin"] = data
    dest = tmp_path / "big.bin"
    # a previous run left half of segment 0 behind
    seg0 = models.segment_ranges(len(data))[0]
    (tmp_path / "big.bin.part0").write_bytes(data[: (seg0[1] + 1) // 2])
    seen = []
    models._fetch(f"{base}/big.bin", dest, seen.append, threading.Event(), size_hint=len(data))
    assert dest.read_bytes() == data
    assert sum(seen) == len(data)  # progress counts resumed bytes once
    assert not list(tmp_path.glob("big.bin.part*"))
    ranges = [r for p, r in srv.hits if p == "/big.bin"]
    assert len(ranges) == 1 + models.SEGMENTS and all(r.startswith("bytes=") for r in ranges)


def test_falls_back_to_one_connection_without_range_support(http_files, tmp_path, monkeypatch):
    base, files, srv = http_files
    monkeypatch.setattr(models, "SEGMENT_MIN", 1000)
    srv.ranges = False
    files["/plain.bin"] = data = _blob(5000)
    dest = tmp_path / "plain.bin"
    models._fetch(f"{base}/plain.bin", dest, lambda n: None, threading.Event(), size_hint=len(data))
    assert dest.read_bytes() == data


def test_small_file_single_connection_resume(http_files, tmp_path):
    base, files, srv = http_files
    files["/small.bin"] = data = _blob(3000)
    (tmp_path / "small.bin.part").write_bytes(data[:1000])
    models._fetch(f"{base}/small.bin", tmp_path / "small.bin", lambda n: None, threading.Event(), size_hint=3000)
    assert (tmp_path / "small.bin").read_bytes() == data
    assert srv.hits == [("/small.bin", "bytes=1000-")]


def test_cancel_keeps_partial_segments(http_files, tmp_path, monkeypatch):
    base, files, _ = http_files
    monkeypatch.setattr(models, "SEGMENT_MIN", 1000)
    files["/c.bin"] = _blob(8000)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(models.Cancelled):
        models._fetch(f"{base}/c.bin", tmp_path / "c.bin", lambda n: None, cancel, size_hint=8000)
    assert not (tmp_path / "c.bin").exists()


def test_pick_source_takes_the_faster_one(monkeypatch):
    models._chosen_source.clear()
    monkeypatch.delenv("MIRRORVOICE_MODEL_SOURCE", raising=False)
    speeds = {"modelscope": 1e5, "huggingface": 3e5}
    monkeypatch.setattr(models, "_probe_speed", lambda url, **k: speeds["huggingface" if "huggingface" in url else "modelscope"])
    assert models.pick_source("Qwen/X", "a.gguf") == "huggingface"
    speeds.update(huggingface=0.0, modelscope=0.0)
    models._chosen_source.clear()
    assert models.pick_source("Qwen/Y", "a.gguf") == "modelscope"  # both unreachable: old default
    monkeypatch.setenv("MIRRORVOICE_MODEL_SOURCE", "huggingface")
    assert models.pick_source("Qwen/Z", "a.gguf") == "huggingface"


def test_download_repo_from_the_picked_source(http_files, monkeypatch, home):
    base, files, _ = http_files
    files["/repo/config.json"] = b"{}"
    files["/repo/weights.bin"] = w = _blob(4000)
    monkeypatch.setattr(models, "list_files", lambda src, repo: [("config.json", 2), ("weights.bin", len(w))])
    monkeypatch.setattr(models, "file_url", lambda src, repo, path: f"{base}/repo/{path}")
    monkeypatch.setattr(models, "pick_source", lambda repo, path: "huggingface")
    m = models.Model("llm", "测试模型", "test-repo", modelscope="org/test-repo", approx_mb=1)
    titles = []
    models.download(m, lambda title, d, t: titles.append(title))
    assert m.ready() and (m.path / "weights.bin").read_bytes() == w
    assert any("来源 HuggingFace" in t for t in titles)


def test_lists_from_the_picked_source_and_falls_back_per_file(http_files, monkeypatch, home):
    base, files, _ = http_files
    files["/hf/model.bin"] = w = _blob(3000)
    files["/hf/config.json"] = b"{}"
    files["/ms/extra.txt"] = b"only on modelscope"  # e.g. a file the other hub lacks
    listings = {"modelscope": [("model.bin", 3000), ("config.json", 2), ("configuration.json", 5)],
                "huggingface": [("model.bin", 3000), ("config.json", 2), ("extra.txt", 18)]}
    monkeypatch.setattr(models, "list_files", lambda src, repo: listings[src])
    monkeypatch.setattr(models, "file_url",
                        lambda src, repo, path: f"{base}/{'hf' if src == 'huggingface' else 'ms'}/{path}")
    monkeypatch.setattr(models, "pick_source", lambda repo, path: "huggingface")
    m = models.Model("llm", "测试模型2", "test-repo-2", modelscope="org/test-repo-2", approx_mb=1)
    models.download(m, lambda *a: None)
    assert (m.path / "model.bin").read_bytes() == w
    assert not (m.path / "configuration.json").exists()          # not listed on the picked hub
    assert (m.path / "extra.txt").read_bytes() == b"only on modelscope"  # 404 there -> other hub
