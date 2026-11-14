"""The lip model (Phase 4 step 2), on made-up data."""

from __future__ import annotations

import numpy as np

from living_cyrodiil import lip, lip_model
from living_cyrodiil.lip_model import RATE, Example, LipSync


def _tone(seconds, level=0.2, freq=200.0):
    t = np.arange(int(seconds * RATE)) / RATE
    return (level * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_features_one_frame_per_lip_frame():
    f = lip_model.features(_tone(2.0), 60)
    assert f.shape == (60, lip_model.MELS) and np.isfinite(f).all()
    # loudness is evened out: a quiet copy looks the same
    q = lip_model.features(_tone(2.0, level=0.02), 60)
    assert np.allclose(f, q, atol=1e-3)


def test_blinks_every_one_to_three_seconds():
    b = lip_model.blinks(300, seed=4)          # 10 s
    starts = np.flatnonzero((b[1:] > 0.9) & (b[:-1] <= 0.9))
    assert 3 <= len(starts) <= 10
    assert b.max() == 1.0 and np.array_equal(b, lip_model.blinks(300, seed=4))


def test_loudness_stand_in_opens_with_the_sound():
    wav = np.concatenate([np.zeros(RATE, np.float32), _tone(1.0), np.zeros(RATE, np.float32)])
    s = lip_model.loudness_shapes(wav, 90)
    jaw = s[:, lip.CHANNELS["Aah"]]
    assert jaw[:25].max() < 0.05 and jaw[35:55].min() > 0.5 and jaw[70:].max() < 0.05


def test_without_a_model_weights_use_the_stand_in_and_blink():
    w = LipSync(None).weights(_tone(2.0), RATE, seed=1)
    assert w.shape == (60, lip.WEIGHTS)
    assert w[:, lip.CHANNELS["Aah"]].max() > 0.5
    assert np.array_equal(w[:, 16], w[:, 17])


def test_train_save_load_predict(tmp_path):
    # A made-up "language": a low tone means Aah, a high tone means Eee.
    rng = np.random.default_rng(0)
    examples = []
    for _ in range(40):
        parts, shapes = [], []
        for _ in range(6):
            n = int(rng.integers(8, 20))
            high = bool(rng.integers(2))
            parts.append(_tone(n / 30, freq=1500.0 if high else 200.0))
            y = np.zeros((n, 16), np.float32)
            y[:, lip.CHANNELS["Eee" if high else "Aah"]] = 1.0
            shapes.append(y)
        wav = np.concatenate(parts); y = np.concatenate(shapes)
        examples.append(Example(lip_model.features(wav, len(y)), y))
    result = lip_model.train(examples, epochs=4, crop=60, batch=16, threads=2)
    score = lip_model.evaluate(result, examples[:5])
    assert score["per_shape"]["Aah"] > 0.5 and score["per_shape"]["Eee"] > 0.5

    import torch
    path = tmp_path / "lip_model.pt"
    torch.save({"version": lip_model.VERSION, "net": result.net.state_dict(), "mean": result.mean,
                "std": result.std, "note": "test"}, path)
    lips = LipSync(None, model_file=path)
    lips.load_or_build()
    assert lips.ready and lips.status == "ready"
    w = lips.weights(_tone(1.0, freq=1500.0), RATE)
    assert w[5:25, lip.CHANNELS["Eee"]].mean() > w[5:25, lip.CHANNELS["Aah"]].mean()


def test_an_old_model_version_is_not_used(tmp_path):
    import torch
    path = tmp_path / "lip_model.pt"
    torch.save({"version": -1}, path)
    lips = LipSync(None, model_file=path)
    lips.load_or_build()                        # would build: no Data folder, so it fails softly
    assert not lips.ready and lips.status.startswith("failed")


def test_borrowed_gestures_fit_the_length_and_keep_their_shape():
    rng = np.random.default_rng(1)
    bank = [rng.random((n, 17)).astype(np.float16) for n in (40, 90, 150, 300)]
    g = lip_model.borrow_gestures(bank, 100, seed=3)
    assert g.shape == (100, 17) and g.min() >= 0 and g.max() <= 1
    # the same seed, the same gestures
    assert np.array_equal(g, lip_model.borrow_gestures(bank, 100, seed=3))


def test_gesture_bank_saves_and_loads(tmp_path):
    rng = np.random.default_rng(2)
    bank = [rng.random((n, 17)).astype(np.float16) for n in (40, 60)]
    path = tmp_path / "gestures.npz"
    np.savez_compressed(path, version=lip_model.GESTURE_VERSION, lengths=np.array([40, 60]),
                        tracks=np.concatenate(bank))
    lips = LipSync(None, model_file=tmp_path / "lip_model.pt")
    lips.load_or_build()                        # no model (no Data folder), but the gestures load
    assert len(lips._gestures) == 2 and np.array_equal(lips._gestures[1], bank[1])
    w = lips.weights(_tone(1.5), RATE, seed=5)
    assert w[:, 22:].max() > 0                  # brows and glances are in
