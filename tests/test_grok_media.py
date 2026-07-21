from clinic_broll.providers import grok_media
from clinic_broll.providers.grok_media import _extract_candidates, _streaming_text


def test_extracts_local_media_paths_from_stream_json():
    output = '{"type":"tool_result","content":{"path":"/tmp/output/image.png"}}\n'
    assert "/tmp/output/image.png" in _extract_candidates(output, {".png"})


def test_extracts_url_media_paths():
    output = 'finished https://example.test/render.mp4\n'
    assert any(item.startswith("https://") for item in _extract_candidates(output, {".mp4"}))


def test_reassembles_streaming_text_tokens():
    output = '\n'.join([
        '{"type":"thought","data":"ZDR requires output"}',
        '{"type":"thought","data":".upload_url"}',
    ])
    assert _streaming_text(output) == "ZDR requires output.upload_url"


def test_detects_complete_zdr_s3_configuration(monkeypatch, tmp_path):
    config = tmp_path / ".grok" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        '[tools.zdr_video_output_s3]\n'
        'bucket = "bucket"\nregion = "auto"\nendpoint = "https://example.test"\n'
        '[tools.zdr_video_output_s3.read_write]\n'
        'access_key_id = "id"\nsecret_access_key = "secret"\n'
    )
    monkeypatch.setattr(grok_media.Path, "home", lambda: tmp_path)

    assert grok_media._zdr_s3_configured()
