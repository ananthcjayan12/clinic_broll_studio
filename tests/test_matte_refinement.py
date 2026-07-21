import numpy as np

from clinic_broll.pipeline.matte import _decontaminate_edges, _refine_mask


def test_refined_mask_keeps_core_opaque_and_background_clear():
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    frame[:, :32] = (40, 160, 210)
    frame[:, 32:] = (20, 180, 20)
    raw = np.zeros((64, 64), dtype=np.float32)
    raw[:, :28] = 1.0
    raw[:, 28:36] = np.linspace(1.0, 0.0, 8, dtype=np.float32)[None, :]
    alpha = _refine_mask(raw, frame, previous_alpha=None, feather_px=3, temporal_blend=0.12)
    assert float(alpha[:, :20].min()) > 0.99
    assert float(alpha[:, 45:].max()) < 0.01
    assert np.any((alpha[:, 27:38] > 0.05) & (alpha[:, 27:38] < 0.95))


def test_temporal_blend_does_not_leave_a_large_motion_trail():
    frame = np.full((48, 48, 3), 120, dtype=np.uint8)
    previous = np.zeros((48, 48), dtype=np.float32)
    previous[:, :20] = 1.0
    current = np.zeros((48, 48), dtype=np.float32)
    current[:, 24:44] = 1.0
    alpha = _refine_mask(current, frame, previous_alpha=previous, feather_px=2, temporal_blend=0.12)
    assert float(alpha[:, :12].max()) < 0.05
    assert float(alpha[:, 30:38].min()) > 0.95


def test_edge_decontamination_reduces_background_colour_spill():
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    frame[:, :13] = (30, 40, 220)
    frame[:, 13:] = (20, 220, 20)
    alpha = np.zeros((32, 32), dtype=np.float32)
    alpha[:, :13] = 1.0
    alpha[:, 13] = 0.8
    alpha[:, 14] = 0.55
    alpha[:, 15] = 0.25
    cleaned = _decontaminate_edges(frame, alpha, strength=0.9, radius=3)
    assert int(cleaned[16, 14, 1]) < int(frame[16, 14, 1])
    assert int(cleaned[16, 14, 2]) > int(frame[16, 14, 2])
