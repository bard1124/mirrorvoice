from mirrorvoice import translate as t

ROWS = [("OneForma", "OneForma", "万佛马|万福马"), ("镜声", "MirrorVoice", "静声"), ("森缇菲", "Centific", "scientific")]


def test_correct_asr_me_side():
    assert t.correct_asr("我们万佛马的项目，万福马和静声", ROWS, "me") == "我们OneForma的项目，OneForma和镜声"


def test_correct_asr_them_side_whole_words_only():
    out = t.correct_asr("Welcome to Scientific, unscientifically speaking", ROWS, "them")
    assert out == "Welcome to Centific, unscientifically speaking"


def test_correct_asr_without_aliases_is_identity():
    assert t.correct_asr("hello", [("a", "b", "")], "me") == "hello"


def test_glossary_roundtrip_keeps_two_and_three_columns(home):
    t.save_glossary([("镜声", "MirrorVoice", ""), ("OneForma", "OneForma", "万佛马"), ("", "x", ""), ("y", "", "")])
    assert t.load_glossary() == [("镜声", "MirrorVoice", ""), ("OneForma", "OneForma", "万佛马")]
    raw = (home / "glossary.csv").read_text(encoding="utf-8").splitlines()
    assert raw == ["镜声,MirrorVoice", "OneForma,OneForma,万佛马"]  # old 2-column files stay readable


def test_glossary_hits_both_directions():
    assert t.glossary_hits("Centific and 镜声", ROWS) == [("镜声", "MirrorVoice"), ("Centific", "森缇菲")]


def test_system_prompt_meeting_background():
    p = t.system_prompt("zh", "en", False, "支付系统的技术评审")
    assert "支付系统的技术评审" in p and p.endswith("no quotes.")
    assert "Background" not in t.system_prompt("zh", "en", False, "  ")


def test_spoken_en_and_last_line():
    assert t.spoken_en("We grew 20% in 2024 at 3:30") == "We grew twenty percent in twenty twenty-four at three thirty"
    assert t.last_line("<think>x</think>\nfirst\n\"Final line\"") == "Final line"


class _FakeBackend:
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def chat(self, msgs):
        self.seen.append(msgs)
        return self.replies.pop(0)

    def close(self):
        pass


def _translator(replies):
    tr = t.Translator.__new__(t.Translator)
    tr.glossary, tr.meeting, tr.backend = [], "", _FakeBackend(replies)
    return tr


def test_no_think_switch_is_sent():
    tr = _translator(["Hello."])
    assert tr.translate("你好", "zh", "en") == "Hello."
    assert tr.backend.seen[0][-1]["content"].endswith(" /no_think")


def test_reasoning_is_retried_then_dropped():
    tr = _translator(['Then "项目" is "project"', "Our project."])
    assert tr.translate("我们的项目", "zh", "en") == "Our project."
    tr = _translator(['"项目" means', '"项目" still'])
    assert tr.translate("我们的项目", "zh", "en") == ""  # never voice an explanation


def test_chinese_subtitles_are_not_flagged():
    assert not t.looks_like_reasoning("我们周五发布修复。", "zh")
    assert t.looks_like_reasoning('The word "清结算" means', "en")
