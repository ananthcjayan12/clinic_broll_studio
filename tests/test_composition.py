from clinic_broll.rendering.composition import _html


def test_layered_composition_has_hyperframes_contract():
    document = _html({
        "mode": "still",
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "duration": 12.0,
        "foreground_available": True,
        "phrases": [{"start": 0, "end": 2, "text": "നമസ്കാരം"}],
        "slots": [{
            "slot_id": "broll_001",
            "start": 2.0,
            "end": 6.0,
            "duration": 4.0,
            "layout_template": "bottom_board",
            "panel_region": 0.44,
            "keep_subject_foreground": True,
            "text_overlay": "",
            "media_file": "broll_001.png",
            "media_kind": "image",
        }],
    })
    assert 'data-composition-id="clinic-broll"' in document
    assert 'data-fps="30"' in document
    assert 'window.addEventListener(\'hf-seek\'' in document
    assert 'id="broll_001-foreground-01"' in document
    assert 'id="broll_001-foreground-02"' in document
    assert 'data-start="2.000000" data-duration="2.000000" data-media-start="2.000000"' in document
    assert 'data-start="4.000000" data-duration="2.000000" data-media-start="4.000000"' in document
    assert 'foreground.webm' in document
    assert 'bottom_board' in document
    assert "data-no-timeline" in document
    assert 'data-has-audio="true"' in document
    assert 'id="broll_001-media"' in document
    assert 'id="broll_001-panel"' in document
    assert "currentTime" not in document
    assert ".play(" not in document
    assert ".pause(" not in document
    assert "window.__hyperframes" not in document


def test_foreground_is_omitted_for_slots_that_do_not_request_it():
    document = _html({
        "mode": "still", "width": 1080, "height": 1920, "fps": 30, "duration": 4.0,
        "foreground_available": True, "phrases": [],
        "slots": [{
            "slot_id": "broll_001", "start": 1.0, "end": 3.0, "duration": 2.0,
            "layout_template": "full_frame", "panel_region": 1.0,
            "keep_subject_foreground": False, "text_overlay": "",
            "media_file": "broll_001.png", "media_kind": "image",
        }],
    })
    assert "foreground.webm" not in document


def test_foreground_internal_boundaries_are_frame_aligned():
    from clinic_broll.rendering.composition import _foreground_intervals

    intervals = _foreground_intervals({"start": 14.72, "duration": 2.94}, 30)
    assert intervals[0][0] == 14.72
    assert intervals[-1][1] == 17.66
    assert intervals[0][1] == 16.2
    assert intervals[1][0] == 16.2


def test_final_media_selection_prefers_approved_selection(tmp_path):
    from clinic_broll.rendering.composition import _select_media

    old = tmp_path / "old.png"
    new = tmp_path / "new.png"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    slot = {
        "selected_still": "old.png",
        "selected_motion": None,
        "versions": {"stills": [{"path": "old.png"}, {"path": "new.png"}], "motion": []},
    }
    assert _select_media(tmp_path, slot, "still") == new
    assert _select_media(tmp_path, slot, "final") == old


def test_motion_media_is_a_direct_hyperframes_clip():
    document = _html({
        "mode": "motion",
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "duration": 12.0,
        "foreground_available": False,
        "phrases": [],
        "slots": [{
            "slot_id": "broll_001",
            "start": 2.0,
            "end": 6.0,
            "duration": 4.0,
            "layout_template": "right_panel",
            "panel_region": 0.44,
            "keep_subject_foreground": True,
            "text_overlay": "",
            "media_file": "broll_001.mp4",
            "media_kind": "video",
        }],
    })
    section_end = document.index("</section>", document.index('id="broll_001"'))
    video_at = document.index('id="broll_001-media"')
    assert video_at > section_end
    assert 'class="generated generated-track clip right_panel"' in document
