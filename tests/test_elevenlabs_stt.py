from clinic_broll.providers.elevenlabs_stt import _normalise_response


def test_normalises_words_and_builds_short_editorial_phrases():
    payload = {
        "language_code": "mal",
        "language_probability": 0.98,
        "text": "നിങ്ങൾ ഭക്ഷണം കഴിച്ചോ? പല്ല് slowly damage ആകും.",
        "words": [
            {"text": "നിങ്ങൾ", "start": 0.1, "end": 0.5, "type": "word"},
            {"text": " ", "start": 0.5, "end": 0.51, "type": "spacing"},
            {"text": "ഭക്ഷണം", "start": 0.51, "end": 1.0, "type": "word"},
            {"text": "കഴിച്ചോ?", "start": 1.01, "end": 1.5, "type": "word"},
            {"text": "പല്ല്", "start": 2.3, "end": 2.7, "type": "word"},
            {"text": "slowly", "start": 2.71, "end": 3.1, "type": "word"},
            {"text": "damage", "start": 3.11, "end": 3.6, "type": "word"},
            {"text": "ആകും.", "start": 3.61, "end": 4.0, "type": "word"},
        ],
    }

    result = _normalise_response(payload, duration=4.5)

    assert result["provider"] == "elevenlabs"
    assert result["duration_seconds"] == 4.5
    assert len(result["words"]) == 7
    assert [phrase["text"] for phrase in result["phrases"]] == [
        "നിങ്ങൾ ഭക്ഷണം കഴിച്ചോ?",
        "പല്ല് slowly damage ആകും.",
    ]
    assert result["phrases"][1]["start"] == 2.3
    assert result["captions"][0]["start"] == 0.1
    assert result["captions"][0]["end"] == 1.5


def test_long_speech_is_split_for_broll_even_without_punctuation():
    words = [
        {"text": f"w{index}", "start": index * 0.7, "end": index * 0.7 + 0.5, "type": "word"}
        for index in range(12)
    ]

    result = _normalise_response({"text": "", "words": words})

    assert len(result["phrases"]) == 2
    assert all(phrase["end"] - phrase["start"] <= 6.0 for phrase in result["phrases"])
