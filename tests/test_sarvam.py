from clinic_broll.providers.sarvam import _normalise_response


def test_normalises_batch_chunk_timestamps():
    payload = {
        "transcript": "പല്ല് വേദന cavity ആകാം",
        "language_code": "ml-IN",
        "timestamps": {
            "chunks": ["പല്ല് വേദന", "cavity ആകാം"],
            "start_time_seconds": [0.1, 2.4],
            "end_time_seconds": [2.2, 4.8],
        },
    }
    result = _normalise_response(payload, 5.0)
    assert result["phrases"][0]["text"] == "പല്ല് വേദന"
    assert result["phrases"][1]["start"] == 2.4
    assert result["language_code"] == "ml-IN"


def test_normalises_nested_rest_phrase_timestamps():
    payload = {
        "transcript": "hello",
        "timestamps": {
            "words": ["hello"],
            "start_time_seconds": [0.0],
            "end_time_seconds": [0.6],
            "timestamps": {
                "words": ["hello"],
                "start_time_seconds": [0.0],
                "end_time_seconds": [0.6],
            },
        },
    }
    result = _normalise_response(payload, 1.0)
    assert result["words"][0]["word"] == "hello"
    assert result["phrases"][0]["end"] == 0.6
