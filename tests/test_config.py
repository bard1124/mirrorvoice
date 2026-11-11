import json

from mirrorvoice import config


def test_old_default_pause_moves_to_the_new_one(home):
    config.SETTINGS_FILE.write_text(json.dumps({"pause": 0.5, "my_lang": "zh"}), encoding="utf-8")
    assert config.Settings.load().pause == 0.35
    config.SETTINGS_FILE.write_text(json.dumps({"pause": 0.6}), encoding="utf-8")
    assert config.Settings.load().pause == 0.6  # a value someone chose stays
