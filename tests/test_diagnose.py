"""诊断包: one zip to send when the app is slow or wrong — the machine, the settings, the figures of
the last meetings. What was said stays on the machine unless it is asked for."""
import json
import zipfile

import pytest

from mirrorvoice import diagnose

SAID = "这个bug提的就是他那个sourcing那边"  # must never be in a package made without the words

LOG = f"""14:02:38 ===== 开始 2026-11-17 =====
14:02:50 [me] 10s: 音频块=309 峰值=0.186 片段=0 门开=True active=True stopped=False overflows=0 last_cb=0.0s ago err=None 滤后峰值=0.108
14:02:51 [them] 10s: 音频块=311 峰值=0.000 片段=0 门开=True active=True stopped=False overflows=0 last_cb=0.0s ago err=None
14:03:00 [me] 10s: 音频块=312 峰值=5.832 片段=1 门开=True active=True stopped=False overflows=0 last_cb=0.0s ago err=None 滤后峰值=0.900
14:03:01 [them] 10s: 音频块=313 峰值=0.620 片段=2 门开=True active=True stopped=False overflows=0 last_cb=0.0s ago err=None
14:03:05 [me] segment 1.70s lang=zh text='{SAID}'
14:03:07 [me] 排队 0.1s 翻译 0.27s（先念 0.27s） 出声 0.63s 抢跑 0.14s 说完到出声 0.77s: 'This bug is about the sourcing area.'
14:03:10 [me] segment 0.94s lang=zh text='重新打开那个翻了吗？'
14:03:12 [me] 排队 2.5s 翻译 0.34s（先念 0.34s） 出声 0.45s 抢跑 -2.53s 说完到出声 3.33s: "Reopen it, didn't you?"
14:03:13 [me] 接上一小段：'没有再说了，最后一句话说的什么？'
14:03:14 [me] 原声直说：'你直接用中文说。'
14:03:20 [them] segment 9.10s lang=en text="He said, \\"it's fixed\\"."
14:03:21 [them] 排队 0.8s 翻译 0.71s: '他说：“已经修好了。”'
14:03:25 [them] segment 2.28s lang=en text='The recording is problematic.'
14:03:25 [them] 排队 0.5s 翻译 2.80s: '这段录音有问题。'
14:03:26 [them] 小段先显示，等后半句：'他说了。'
14:03:27 [them] 整段重译 2 段 1.99s: '他说录音有问题。'
14:03:28 [them] 预览译文：已说 3.2s，停顿后 0.42s 显示
14:03:30 [me] 输入流 3 s 无数据（active=False），重新打开
15:08:34 ===== 开始 2026-11-17 =====
15:08:57 [me] segment 1.58s lang=zh text='第二场。'
"""


def test_what_was_said_becomes_its_length_and_the_home_folder_a_tilde():
    from pathlib import Path

    assert diagnose.redact(f"14:03:05 [me] segment 1.70s lang=zh text='{SAID}'") == \
        f"14:03:05 [me] segment 1.70s lang=zh text=‹{len(SAID)} 字符›"
    line = diagnose.redact('14:03:20 [them] segment 9.10s lang=en text="He said, \\"it\'s fixed\\"."')
    assert "fixed" not in line and "9.10s lang=en" in line  # (a text with both kinds of quotes in it)
    assert diagnose.redact("14:03:12 [me] 排队 2.5s 翻译 0.34s: \"Reopen it, didn't you?\"").endswith("0.34s: ‹22 字符›")
    assert diagnose.redact(f'  File "{Path.home()}/x/engine.py", line 3') == "  File ‹" + str(len(str(Path.home())) + 12) + " 字符›, line 3"
    assert diagnose.redact(f"cannot open {Path.home()}/Library/x") == "cannot open ~/Library/x"
    assert diagnose.redact("14:02:50 [me] 10s: 音频块=309 峰值=0.186") == "14:02:50 [me] 10s: 音频块=309 峰值=0.186"


def test_a_meeting_in_figures():
    first, second = diagnose.sessions(LOG.splitlines())
    assert len(second) == 2 and second[0].startswith("15:08:34")
    s = diagnose.session_stats(first)
    assert s["开始"] == "14:02:38 2026-11-17" and s["结束"] == "14:03:30" and s["时长(分)"] == 0.9
    assert s["我方发声"]["说完到出声(秒)"] == {"n": 2, "中位": 3.33, "九成": 3.33, "最长": 3.33}
    assert s["我方发声"]["超过2.5秒"] == 1 and s["我方发声"]["排队中位"] == 2.5 and s["我方发声"]["翻译中位"] == 0.34
    assert s["对方字幕"]["翻译(秒)"]["最长"] == 2.8 and s["对方字幕"]["合计超过3秒"] == 1
    assert s["对方字幕"]["预览译文在停顿后(秒)"]["中位"] == 0.42
    assert s["我方片段"]["语言"] == {"zh": 2} and s["对方片段"] == {"时长(秒)": {"n": 2, "中位": 9.1, "九成": 9.1, "最长": 9.1},
                                                                   "8秒以上": 1, "语言": {"en": 2}}
    assert s["我方计数"] == {"接上一小段": 1, "原声直说": 1}
    assert s["对方计数"] == {"小段先显示": 1, "整段重译": 1}
    assert s["麦克风"] == {"10秒窗口": 2, "超过满刻度": 1, "无声": 0, "最高峰值": 5.83}
    assert s["对方线路"]["无声"] == 1
    # a fault is a fault; a sentence that has the word in it is not ("重新打开那个翻了吗？")
    assert s["异常行"] == 1 and "输入流 3 s 无数据" in s["异常示例"][0]
    assert SAID not in json.dumps(s, ensure_ascii=False)


@pytest.fixture
def data(tmp_path, monkeypatch):
    home = tmp_path / "data"
    (home / "transcripts").mkdir(parents=True)
    (home / "models" / "Qwen3-4B-MLX-4bit").mkdir(parents=True)
    (home / "models" / "Qwen3-4B-MLX-4bit" / "model.safetensors").write_bytes(b"x" * 2048)
    (home / "models" / "Qwen3-4B-MLX-4bit" / ".complete").touch()
    (home / "models" / "half").mkdir()
    (home / "debug.log").write_text(LOG, encoding="utf-8")
    (home / "glossary.csv").write_text("sourcing,sourcing\n", encoding="utf-8")
    (home / "settings.json").write_text(json.dumps(
        {"quality": "fast", "mic": "外置麦克风", "meeting_context": "和 Danesh 讨论 sourcing", "llm_base_url": "10.1.2.3:8765",
         "llm_model": "", "extra": {}}, ensure_ascii=False), encoding="utf-8")
    for i in range(5):
        (home / "transcripts" / f"2026-11-1{i}_100000.csv").write_text(f"time,speaker,original,translation\n1,我,{SAID},x\n",
                                                                       encoding="utf-8-sig")
    for name, value in (("HOME", home), ("LOG_FILE", home / "debug.log"), ("MODELS", home / "models"),
                        ("SETTINGS_FILE", home / "settings.json"), ("TRANSCRIPTS", home / "transcripts"),
                        ("GLOSSARY_FILE", home / "glossary.csv")):
        monkeypatch.setattr(diagnose, name, value)
    monkeypatch.setattr(diagnose, "machine", lambda: {"系统": "macOS 15.4", "处理器": "Apple M1 Pro", "内存": "32 GB"})
    out = tmp_path / "out"
    out.mkdir()
    return out


def test_the_package_holds_no_word_that_was_said_unless_asked(data):
    path = diagnose.build(data)
    assert path.parent == data and path.name.startswith("翻了么诊断-") and path.suffix == ".zip"
    with zipfile.ZipFile(path) as z:
        names = sorted(z.namelist())
        everything = "\n".join(z.read(n).decode("utf-8") for n in names)
        report = z.read("诊断报告.txt").decode("utf-8")
        settings = json.loads(z.read("settings.json"))
    assert names == ["debug.log", "settings.json", "诊断报告.txt"]
    for words in (SAID, "This bug is about", "Danesh", "10.1.2.3", "录音有问题"):
        assert words not in everything, words
    assert settings["meeting_context"] == "‹已省略›" and settings["llm_base_url"] == "‹已省略›" and settings["mic"] == "外置麦克风"
    assert "对话内容：不包含" in report and "处理器：Apple M1 Pro" in report
    assert "Qwen3-4B-MLX-4bit  0.00 GB" in report and "half  0.00 GB（未下完）" in report
    assert "【最近 2 场】（日志里共 2 场）" in report and report.index("15:08:34") < report.index("14:02:38")  # the latest first
    assert "超过满刻度 1" in report and "说完到出声(秒)：n 2，中位 3.33" in report


def test_with_the_words_it_adds_the_log_as_it_is_the_last_records_and_the_glossary(data):
    with zipfile.ZipFile(diagnose.build(data, with_text=True)) as z:
        names = sorted(z.namelist())
        log, report = z.read("debug.log").decode("utf-8"), z.read("诊断报告.txt").decode("utf-8")
        settings = json.loads(z.read("settings.json"))
    assert names == ["debug.log", "glossary.csv", "settings.json", "会议记录/2026-11-12_100000.csv",
                     "会议记录/2026-11-13_100000.csv", "会议记录/2026-11-14_100000.csv", "诊断报告.txt"]
    assert SAID in log and "对话内容：已包含" in report and settings["meeting_context"].startswith("和 Danesh")


def test_without_a_log_or_settings_there_is_still_a_report(data):
    for name in ("debug.log", "settings.json"):
        (diagnose.HOME / name).unlink()
    with zipfile.ZipFile(diagnose.build(data)) as z:
        report = z.read("诊断报告.txt").decode("utf-8")
    assert "【最近 0 场】" in report and "处理器：Apple M1 Pro" in report


def test_the_machine_is_named_without_failing_when_a_tool_is_missing(monkeypatch):
    monkeypatch.undo()
    info = diagnose.machine()
    assert info["处理器"] and info["CPU 线程数"].isdigit() and info["系统"]
    monkeypatch.setattr(diagnose, "_run", lambda *a, **k: "")
    assert diagnose.machine()["处理器"]  # (no sysctl / powershell: what Python knows)


def test_the_command_line_says_where_it_is(data, monkeypatch, capsys):
    import argparse

    from mirrorvoice import __main__ as main

    monkeypatch.setattr(diagnose, "default_folder", lambda: data)
    main._cmd_diagnose(argparse.Namespace(with_text=False))
    out = capsys.readouterr().out
    assert "诊断包已生成：" in out and str(data) in out and "不含对话内容" in out and len(list(data.glob("*.zip"))) == 1


def test_the_button_builds_it_and_cancel_builds_nothing(data, monkeypatch):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from mirrorvoice.config import Settings
    from mirrorvoice.ui import main_window, theme

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.apply(app)
    monkeypatch.setattr(main_window, "list_devices", lambda kind: ["BlackHole 2ch", "BlackHole 16ch", "外置耳机"])
    monkeypatch.setattr(main_window, "default_output_name", lambda: "外置耳机")
    monkeypatch.setattr(main_window, "IS_MAC", False)
    monkeypatch.setattr(main_window.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    monkeypatch.setattr(diagnose, "default_folder", lambda: data)
    opened = []
    s = Settings(auto_check_update=False, setup_done=True, call_out="BlackHole 2ch", their_source="BlackHole 16ch")
    s.save()
    w = main_window.MainWindow(s)
    try:
        w.show()
        for key in ("quiet", "studio", "chat"):  # the entry has its place in every look
            w.theme_box.setCurrentIndex(w.theme_box.findData(key))
            app.processEvents()
            assert w.nav["_diagnose"].isVisible() and w.nav["_diagnose"].text() == "诊断包", key
        monkeypatch.setattr(w, "_ask_diagnose", lambda: None)
        w.nav["_diagnose"].click()
        assert list(data.glob("*.zip")) == [] and opened == []
        monkeypatch.setattr(w, "_ask_diagnose", lambda: False)
        w.nav["_diagnose"].click()
        (made,) = data.glob("*.zip")
        from pathlib import Path

        assert [Path(x) for x in opened] == [data] and made.name in w.status.text()  # (Qt writes C:/… on Windows)
        with zipfile.ZipFile(made) as z:
            assert "对话内容：不包含" in z.read("诊断报告.txt").decode("utf-8")
    finally:
        w.close()
        theme.use(app, "quiet")
