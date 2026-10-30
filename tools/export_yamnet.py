"""Export Google's YAMNet weights to betteraaf/models/yamnet.npz and check that the
numpy implementation (betteraaf/classify.py) matches TensorFlow.

Run by .github/workflows/model.yml (needs TensorFlow, the official YAMNet code in
./tfmodels/research/audioset/yamnet, yamnet.h5 and a test wav)."""

import json
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tfmodels", "research", "audioset", "yamnet"))
sys.path.insert(0, ROOT)

import tensorflow as tf  # noqa: E402
import params as yamnet_params  # noqa: E402
import yamnet as yamnet_model  # noqa: E402

KEEP = {"Conv2D", "DepthwiseConv2D", "BatchNormalization", "ReLU",
        "GlobalAveragePooling2D", "Dense", "Activation"}

p = yamnet_params.Params()
model = yamnet_model.yamnet_frames_model(p)
model.load_weights(os.path.join(ROOT, "yamnet.h5"))

spec, arrays = [], {}
for layer in model.layers:
    t = type(layer).__name__
    if t not in KEEP:
        print("skip", t, layer.name)
        continue
    cfg = layer.get_config()
    entry = {"type": t, "name": layer.name}
    if t in ("Conv2D", "DepthwiseConv2D"):
        entry["strides"] = list(cfg["strides"])
        entry["padding"] = cfg["padding"]
        assert cfg["padding"] == "same"
    if t == "BatchNormalization":
        entry["epsilon"] = float(cfg["epsilon"])
    if t == "Activation":
        entry["activation"] = cfg["activation"]
    i = len(spec)
    for w in layer.weights:
        role = w.name.split("/")[-1].split(":")[0]
        arrays[f"{i}:{role}"] = w.numpy().astype(np.float32)
    spec.append(entry)

print(json.dumps([s["type"] for s in spec]))
out_dir = os.path.join(ROOT, "betteraaf", "models")
os.makedirs(out_dir, exist_ok=True)
np.savez_compressed(os.path.join(out_dir, "yamnet.npz"), spec=np.array(json.dumps(spec)), **arrays)

# ---- check numpy vs TensorFlow
from betteraaf import classify  # noqa: E402

net = classify.Net(os.path.join(out_dir, "yamnet.npz"))
tests = []
wav, sr = sf.read(os.path.join(ROOT, "speech_whistling2.wav"), dtype="float32")
if wav.ndim > 1:
    wav = wav.mean(axis=1)
assert sr == 16000, sr
tests.append(("speech_whistling2", wav))
t = np.arange(int(3.3 * 16000)) / 16000
chord = sum(0.15 * np.sin(2 * np.pi * f * t) for f in (220, 277.2, 329.6, 440)) * (1 + 0.3 * np.sin(2 * np.pi * 2 * t))
tests.append(("chord", chord.astype(np.float32)))
tests.append(("short", wav[:5000]))

names = yamnet_model.class_names(os.path.join(ROOT, "tfmodels", "research", "audioset", "yamnet", "yamnet_class_map.csv"))
worst = 0.0
for label, w in tests:
    tf_scores, _emb, tf_logmel = model(w)
    tf_scores = tf_scores.numpy()
    patches = classify.log_mel_patches(w)
    np_scores = net(patches)
    assert np_scores.shape == tf_scores.shape, (np_scores.shape, tf_scores.shape)
    d = float(np.max(np.abs(np_scores - tf_scores)))
    worst = max(worst, d)
    top_tf = names[np.argmax(tf_scores.mean(0))]
    top_np = names[np.argmax(np_scores.mean(0))]
    print(f"{label}: patches={len(patches)} max|diff|={d:.5f} top TF={top_tf} top numpy={top_np}")
    assert top_tf == top_np

print("worst diff", worst)
assert worst < 0.01, "numpy implementation does not match TensorFlow"
print("EXPORT OK")
