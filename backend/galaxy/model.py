"""k-nearest-neighbour classifier: Gaia DR3 photometry -> object class.

Trained on Gaia stars that SIMBAD gives a spectral or object type for, and applied to every
Gaia star without one. Features are the HR-diagram position (absolute G magnitude, BP-RP)
plus the two half-colours (BP-G, G-RP), standardised. The class probability is the share of
the k nearest labelled neighbours that vote for it. Accuracy is measured on a held-out 20 %
and written to the map's metadata so the UI can show it next to every prediction.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from .classes import CLASSES


def features(abs_g, bp_rp, bp_g, g_rp) -> np.ndarray:
    X = np.column_stack([abs_g, bp_rp, bp_g, g_rp]).astype(np.float64)
    return X


class KNNClassifier:
    def __init__(self, k: int = 15):
        self.k = k

    def fit(self, X: np.ndarray, y: np.ndarray) -> "KNNClassifier":
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-9
        self.tree = cKDTree((X - self.mu) / self.sd)
        self.y = y.astype(np.int64)
        return self

    def predict_proba(self, X: np.ndarray, batch: int = 200_000) -> np.ndarray:
        n_cls = len(CLASSES)
        out = np.zeros((len(X), n_cls), np.float32)
        for s in range(0, len(X), batch):
            _, idx = self.tree.query((X[s:s + batch] - self.mu) / self.sd, k=self.k, workers=-1)
            votes = self.y[idx]
            for c in range(n_cls):
                out[s:s + batch, c] = (votes == c).mean(1)
        return out


def train_and_evaluate(X: np.ndarray, y: np.ndarray, seed: int = 7, k: int = 15) -> tuple[KNNClassifier, dict]:
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(X))
    n_test = len(X) // 5
    te, tr = perm[:n_test], perm[n_test:]
    m = KNNClassifier(k).fit(X[tr], y[tr])
    pred = m.predict_proba(X[te]).argmax(1)
    acc = float((pred == y[te]).mean())
    per = {}
    for c in CLASSES:
        sel = y[te] == c["id"]
        if sel.sum() >= 10:
            per[c["key"]] = {"n_test": int(sel.sum()), "recall": round(float((pred[sel] == c["id"]).mean()), 3)}
    within_one = float((np.abs(pred - y[te]) <= 1)[y[te] <= 5].mean()) if (y[te] <= 5).any() else None
    report = {"method": f"k-nearest neighbours (k={k}) on standardised [M_G, BP-RP, BP-G, G-RP]",
              "labels": "SIMBAD spectral / object types of Gaia DR3 stars within the map",
              "n_train": int(len(tr)), "n_test": int(n_test), "accuracy": round(acc, 3),
              "accuracy_within_one_type_main_sequence": round(within_one, 3) if within_one is not None else None,
              "per_class": per}
    # Refit on everything for the final predictions.
    return KNNClassifier(k).fit(X, y), report
