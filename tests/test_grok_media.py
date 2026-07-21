from clinic_broll.providers.grok_media import _extract_candidates


def test_extracts_local_media_paths_from_stream_json():
    output = '{"type":"tool_result","content":{"path":"/tmp/output/image.png"}}\n'
    assert "/tmp/output/image.png" in _extract_candidates(output, {".png"})


def test_extracts_url_media_paths():
    output = 'finished https://example.test/render.mp4\n'
    assert any(item.startswith("https://") for item in _extract_candidates(output, {".mp4"}))
