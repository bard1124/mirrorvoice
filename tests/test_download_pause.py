"""A model download can be paused, continued and cancelled (asked for on 2026-11-16: 高质量 chosen
in a meeting began 4 GB that nothing could stop), and 开始 asks before it begins gigabytes."""
import io
import tarfile
import threading
import time

import pytest

from mirrorvoice import models
from mirrorvoice.config import MODELS

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt  # noqa: E402

from mirrorvoice.ui.downloads import CANCELLED, PAUSED, DownloadManager  # noqa: E402


def _until(cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    app = QtWidgets.QApplication.instance()
    while not cond() and time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        time.sleep(0.005)
    return cond()


# ---------------------------------------------------------------- files

def test_what_an_unfinished_download_left_is_found_by_exact_name_and_deleted(home):
    m = models.Model("tts", "测试音色", "pause-Base", modelscope="org/pause-Base", approx_mb=1)
    other = models.Model("tts", "测试音色 8bit", "pause-Base-8bit", modelscope="org/pause-Base-8bit", approx_mb=1)
    for folder in (m.path, other.path):
        folder.mkdir(parents=True)
        (folder / "weights.bin.part").write_bytes(b"x" * 100)
    (other.path / ".complete").touch()
    one = models.Model("asr", "测试识别", "pause-one.onnx", url="http://x/pause-one.onnx", approx_mb=1)
    for name in ("pause-one.onnx.part", "pause-one.onnx.part0", "pause-one.onnx.part1", "pause-one.onnx.partial-notes"):
        (MODELS / name).write_bytes(b"x" * 10)
    assert models.partial_paths(m) == [m.path] and models.partial_bytes(m) == 100
    assert models.partial_paths(other) == [] and models.partial_bytes(other) == 0  # complete: nothing to delete
    assert sorted(p.name for p in models.partial_paths(one)) == ["pause-one.onnx.part", "pause-one.onnx.part0",
                                                                 "pause-one.onnx.part1"]
    models.discard_partial(m)
    models.discard_partial(one)
    models.discard_partial(other)
    assert not m.path.exists() and other.ready() and (other.path / "weights.bin.part").exists()
    assert [p.name for p in MODELS.glob("pause-one.onnx*")] == ["pause-one.onnx.partial-notes"]


def test_unpacking_can_be_stopped_and_is_done_again_without_downloading_again(http_files, home):
    base, files, srv = http_files
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as t:
        for name in ("pause-arch/a.bin", "pause-arch/b.bin"):
            info = tarfile.TarInfo(name)
            info.size = 5
            t.addfile(info, io.BytesIO(b"12345"))
    files["/pause-arch.tar.bz2"] = buf.getvalue()
    m = models.Model("asr", "测试压缩包", "pause-arch", url=f"{base}/pause-arch.tar.bz2", approx_mb=1)
    cancel = threading.Event()
    with pytest.raises(models.Cancelled):  # 暂停 arrives while it unpacks
        models.download(m, lambda title, d, t: "解压" in title and cancel.set(), cancel)
    assert not m.ready() and (MODELS / "pause-arch.tar.bz2").exists()
    hits = len(srv.hits)
    models.download(m, lambda title, d, t: None, threading.Event())
    assert m.ready() and (m.path / "b.bin").read_bytes() == b"12345" and len(srv.hits) == hits
    assert not (MODELS / "pause-arch.tar.bz2").exists()


# ---------------------------------------------------------------- the manager

@pytest.fixture
def dm(monkeypatch, home):
    """The app's one manager, with two models that are not here and a download that goes on until
    it is told to stop (or to finish)."""
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    a = models.Model("llm", "测试翻译", "pause-llm", modelscope="org/pause-llm", approx_mb=2048)
    b = models.Model("tts", "测试音色", "pause-tts", modelscope="org/pause-tts", approx_mb=512)
    monkeypatch.setattr(models, "required", lambda quality, s=None: [] if quality == "fast" else [a, b])
    finish, calls = threading.Event(), []

    def download(model, progress, cancel):
        model.path.mkdir(parents=True, exist_ok=True)
        (model.path / "weights.bin.part").write_bytes(b"x" * 1000)
        progress(model.title, 1000, 4000)
        calls.append(model.key)  # (the tests go on from here: the file is there)
        while not finish.is_set():
            if cancel.is_set():
                progress(model.title, 2000, 4000)  # a last word on its way out
                raise models.Cancelled()
            time.sleep(0.005)
        (model.path / "weights.bin.part").rename(model.path / "weights.bin")
        (model.path / ".complete").touch()

    monkeypatch.setattr(models, "download", download)
    m = DownloadManager.get()
    m.running, m.paused, m.error, m._thread = False, False, "", None
    m.finish, m.calls, m.models = finish, calls, (a, b)
    yield m
    finish.set()
    m._cancel.set()
    if m._thread is not None:
        m._thread.join(10)
    m.running, m.paused, m.error, m._thread = False, False, "", None
    for model in (a, b):
        models.discard_partial(model)
        if model.path.exists():
            import shutil
            shutil.rmtree(model.path)


def test_pause_stops_at_once_keeps_the_files_and_continue_goes_on(dm):
    seen, ended = [], []
    dm.overall.connect(lambda d, t, speed: seen.append(d), Qt.ConnectionType.DirectConnection)
    dm.finished.connect(ended.append, Qt.ConnectionType.DirectConnection)
    dm.start("quality")
    assert _until(lambda: dm.calls == ["llm"]) and dm.running
    worker = dm._thread
    dm.pause()
    assert (dm.running, dm.paused, dm.error) == (False, True, PAUSED) and ended == [PAUSED]  # not after the network
    worker.join(10)
    assert not worker.is_alive() and ended == [PAUSED]  # the worker that was stopped has nothing more to say
    assert all(d < 2000 for d in seen)  # … nor does the bar move after the pause
    assert dm.partial_bytes("quality") == 1000  # what was fetched is kept
    dm.start("quality")
    assert dm.running and not dm.paused and dm.error == ""
    dm.finish.set()
    assert _until(lambda: ended[-1] == "") and not dm.running and dm.missing("quality") == []


def test_continue_waits_for_the_worker_that_is_still_stopping(dm):
    dm.start("quality")
    assert _until(lambda: dm.calls == ["llm"])
    first = dm._thread
    dm.pause()
    dm.start("quality")  # at once: the first may still be writing
    assert _until(lambda: len(dm.calls) == 2)
    assert not first.is_alive()  # the second began only when the first was gone


def test_cancel_deletes_what_is_not_complete(dm):
    ended = []
    dm.finished.connect(ended.append, Qt.ConnectionType.DirectConnection)
    dm.start("quality")
    assert _until(lambda: dm.calls == ["llm"])
    dm.discard("quality")
    assert not dm.running and dm.paused and dm.error == ""
    assert _until(lambda: ended == [CANCELLED])
    assert dm.partial_bytes("quality") == 0 and not dm.models[0].path.exists()


# ---------------------------------------------------------------- the control panel

@pytest.fixture
def window(dm, monkeypatch):
    from mirrorvoice.config import Settings
    from mirrorvoice.ui import main_window, theme

    theme.apply(QtWidgets.QApplication.instance())
    for kind in ("warning", "information", "critical"):
        monkeypatch.setattr(QtWidgets.QMessageBox, kind, lambda *a, **k: None)
    monkeypatch.setattr(main_window, "list_devices", lambda kind: ["BlackHole 2ch", "BlackHole 16ch", "外置耳机"])
    monkeypatch.setattr(main_window, "default_output_name", lambda: "外置耳机")
    monkeypatch.setattr(main_window, "IS_MAC", False)
    s = Settings(auto_check_update=False, setup_done=True, call_out="BlackHole 2ch", their_source="BlackHole 16ch",
                 monitor="外置耳机", quality="quality")
    s.save()
    w = main_window.MainWindow(s)
    yield w
    w.engine.stop()
    w.close()


def test_the_banner_offers_pause_while_it_downloads_and_continue_or_cancel_after(window, dm):
    w = window
    assert not w.banner.isHidden() and "下载模型" == w.banner_btn.text() and w.banner_cancel.isHidden()
    w.banner_btn.click()
    assert _until(lambda: dm.calls == ["llm"])
    assert _until(lambda: "模型下载中" in w.banner_text.text())
    assert not w.banner_pause.isHidden() and w.banner_btn.isHidden() and w.banner_cancel.isHidden()
    w.banner_pause.click()
    assert _until(lambda: "已暂停" in w.banner_text.text())
    assert w.banner_pause.isHidden() and w.banner_btn.text() == "继续下载" and not w.banner_cancel.isHidden()
    w.banner_btn.click()
    assert dm.running and not w.banner_pause.isHidden() and w.banner_cancel.isHidden()


def test_cancel_asks_first_then_goes_back_to_the_model_that_is_here(window, dm, monkeypatch):
    w = window
    w.banner_btn.click()
    assert _until(lambda: dm.calls == ["llm"])
    w.banner_pause.click()
    asked = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or QtWidgets.QMessageBox.StandardButton.No)
    w.banner_cancel.click()
    assert len(asked) == 1 and "删除" in asked[0] and dm.partial_bytes("quality") == 1000  # "No": nothing happens
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)
    w.banner_cancel.click()
    assert _until(lambda: w.s.quality == "fast")  # the choice that needs no download
    assert dm.partial_bytes("quality") == 0 and w.quality.currentData() == "fast" and w.banner.isHidden()


def test_start_asks_before_it_downloads_gigabytes(window, dm, monkeypatch):
    w = window
    answers, asked = iter(["", "go"]), []

    def ask(missing, mb, ready):
        asked.append((len(missing), mb, ready))
        return next(answers)

    monkeypatch.setattr(w, "_ask_download", ask)
    w._toggle()
    assert asked == [(2, 2560, "fast")] and not dm.running and not w.engine.running  # "not now": nothing begins
    w._toggle()
    assert dm.running and not w.engine.running
    dm.pause()
    monkeypatch.setattr(w, "_ask_download", lambda *a: "back")
    w._toggle()
    assert w.s.quality == "fast" and not dm.running


def test_a_small_rest_is_downloaded_without_asking(window, dm, monkeypatch):
    w = window
    monkeypatch.setattr(w, "BIG_DOWNLOAD_MB", 4096)
    monkeypatch.setattr(w, "_ask_download", lambda *a: pytest.fail("asked"))
    w._toggle()
    assert dm.running


# ---------------------------------------------------------------- a passage in place of its pieces

def _them(w, row, src, tgt):
    w._on_event("them", {"src": src, "tgt": tgt, "row": row, "latency": 1.0, "asr_s": 0.1, "queue_s": 0.0,
                         "translate_s": 0.5})


def test_a_passage_takes_the_place_of_its_pieces_in_every_list(window):
    w = window
    n, n_them = w.log.count(), w.log_them.count()
    _them(w, 1, "And this bug is the user.", "而且这个bug就是用户。")
    w._on_event("me", {"src": "嗯，你继续说", "tgt": "Go on", "passthrough": False, "latency": 1.0, "asr_s": 0.1,
                       "queue_s": 0.0, "translate_s": 0.4, "voice_s": 0.5})
    _them(w, 2, "Who applied for the job.", "谁申请了这份工作。")
    _them(w, 3, "Is this a model issue.", "这是模型问题吗？")
    assert w.log.count() == n + 4 and w.log_them.count() == n_them + 3
    w._on_event("them_run", {"rows": [1, 2], "src": "And this bug is the user. Who applied for the job.",
                             "tgt": "这个 bug 出在申请这份工作的用户身上。"})
    assert w.log.count() == n + 3 and w.log_them.count() == n_them + 2
    assert "出在申请这份工作的用户身上" in w.log.item(n).text() and "整段重译" in w.log.item(n).text()
    assert "Go on" in w.log.item(n + 1).text() and "模型问题" in w.log.item(n + 2).text()  # the others stay
    assert [e[2] for e in w.convo.entries if e[0] == "them"] == ["这个 bug 出在申请这份工作的用户身上。", "这是模型问题吗？"]
    w._on_event("them_run", {"rows": [1, 2], "src": "x", "tgt": "y"})  # rows that are gone: nothing to do
    assert w.log.count() == n + 3


# ---------------------------------------------------------------- the set-up wizard

def test_the_wizard_page_can_pause_continue_and_cancel_and_does_not_start_again_by_itself(dm, monkeypatch):
    from mirrorvoice.config import Settings
    from mirrorvoice.ui import theme
    from mirrorvoice.ui.wizard import Wizard

    theme.apply(QtWidgets.QApplication.instance())
    s = Settings(auto_check_update=False, quality="quality")
    s.save()
    z = Wizard(s)
    try:
        z._go(1)  # the page starts the download by itself
        assert _until(lambda: dm.calls == ["llm"]) and not z.dl_pause.isHidden() and z.dl_btn.isHidden()
        z.dl_pause.click()
        assert _until(lambda: "已暂停" in z.dl_status.text())
        assert z.dl_pause.isHidden() and z.dl_btn.text() == "继续下载" and not z.dl_cancel.isHidden()
        z._go(-1)
        z._go(1)  # back on the page: what I paused stays paused
        assert not dm.running and len(dm.calls) == 1 and "已暂停" in z.dl_status.text()
        z.dl_btn.click()
        assert _until(lambda: len(dm.calls) == 2) and dm.running and not z.dl_pause.isHidden()
        z.dl_pause.click()
        monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)
        z.dl_cancel.click()
        assert _until(lambda: "已取消" in z.dl_status.text())
        assert dm.partial_bytes("quality") == 0 and z.dl_btn.text() == "下载" and z.dl_cancel.isHidden()
    finally:
        z.close()
