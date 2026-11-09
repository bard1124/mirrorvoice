from mirrorvoice.timing import LatencyStats, breakdown


def test_breakdown_hides_small_queue():
    d = dict(latency=2.4, asr_s=0.3, queue_s=0.1, translate_s=1.6, voice_s=0.4)
    assert breakdown(d) == "识别 0.3s · 翻译 1.6s · 发声 0.4s = 2.4s"
    assert breakdown(dict(d, queue_s=1.2)) == "识别 0.3s · 排队 1.2s · 翻译 1.6s · 发声 0.4s = 2.4s"
    assert breakdown({"latency": 0.8}) == "0.8s"


def test_stats_name_the_bottleneck_and_suggest_faster_model():
    st = LatencyStats(window=3)
    for v in (1.4, 1.6, 1.8, 2.0):  # window keeps the last three
        st.add(dict(latency=v + 0.8, asr_s=0.3, translate_s=v, voice_s=0.5))
    st.add(dict(latency=0.1, passthrough=True))  # passthrough is not translation latency
    assert len(st.rows) == 3
    s = st.summary("fast")
    assert "最近 3 句平均延迟 2.6s" in s and "最慢的是翻译（1.8s）" in s and "更快的一档" in s
    assert "更快的一档" not in st.summary("turbo")
