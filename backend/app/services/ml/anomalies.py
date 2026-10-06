"""Isolation Forest anomaly detection on daily NAV behaviour.

This is a descriptive tool: it flags historical days whose behaviour was
unusual for this fund. The model is fitted on the whole selected history
(there is nothing to predict, so there is no look-ahead concern), and each
flagged day is explained with robust z-scores of its features and a
comparison with the benchmark move on the same day.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from app.core.errors import InsufficientDataError, ValidationFailedError

FEATURE_LABELS = {
    "return": "daily return",
    "return_z": "return vs. trailing 3-month volatility",
    "vol_ratio": "1-month / 6-month volatility",
    "excess_return": "return minus benchmark return",
}
MIN_OBSERVATIONS = 200


def anomaly_features(nav: pd.Series, benchmark: pd.Series | None) -> pd.DataFrame:
    r = nav.astype(float).pct_change()
    trailing_sd = r.rolling(63).std().shift(1)  # volatility known *before* the day
    frame = pd.DataFrame({
        "return": r,
        "return_z": r / trailing_sd,
        "vol_ratio": r.rolling(21).std() / r.rolling(126).std(),
    })
    if benchmark is not None:
        b = benchmark.astype(float).reindex(nav.index).ffill().pct_change()
        frame["excess_return"] = r - b
        frame["benchmark_return"] = b
    return frame.dropna()


def detect(nav: pd.Series, benchmark: pd.Series | None, *, contamination: float = 0.01) -> dict:
    if not 0.001 <= contamination <= 0.1:
        raise ValidationFailedError("Contamination must be between 0.1% and 10%.")
    frame = anomaly_features(nav, benchmark)
    if len(frame) < MIN_OBSERVATIONS:
        raise InsufficientDataError(
            f"Anomaly detection needs at least {MIN_OBSERVATIONS} usable days; {len(frame)} available.")
    columns = [c for c in ("return", "return_z", "vol_ratio", "excess_return") if c in frame]
    X = frame[columns].to_numpy()  # noqa: N806
    model = IsolationForest(n_estimators=300, contamination=contamination, random_state=42)
    labels = model.fit_predict(X)
    scores = -model.score_samples(X)  # higher = more anomalous

    median = frame[columns].median()
    mad = (frame[columns] - median).abs().median().replace(0, np.nan) * 1.4826
    robust_z = (frame[columns] - median) / mad

    anomalies = []
    for i in np.where(labels == -1)[0]:
        day = frame.index[i]
        contributions = sorted(
            ({"feature": c, "label": FEATURE_LABELS[c], "value": float(frame.iloc[i][c]),
              "robust_z": float(robust_z.iloc[i][c])} for c in columns if np.isfinite(robust_z.iloc[i][c])),
            key=lambda row: -abs(row["robust_z"]),
        )
        anomalies.append({
            "date": day.date().isoformat(),
            "nav": float(nav.loc[day]),
            "return": float(frame.iloc[i]["return"]),
            "benchmark_return": float(frame.iloc[i]["benchmark_return"]) if "benchmark_return" in frame else None,
            "score": float(scores[i]),
            "drivers": contributions[:3],
            "interpretation": _interpret(frame.iloc[i]),
        })
    anomalies.sort(key=lambda row: -row["score"])
    return {
        "method": "Isolation Forest (300 trees, random_state=42)",
        "contamination": contamination,
        "features": [{"feature": c, "label": FEATURE_LABELS[c]} for c in columns],
        "observations": int(len(frame)),
        "period": {"start": frame.index[0].date().isoformat(), "end": frame.index[-1].date().isoformat()},
        "anomalies": anomalies,
        "scores": [{"date": d.date().isoformat(), "score": round(float(s), 4)} for d, s in zip(frame.index, scores,
                                                                                           strict=True)],
        "note": "Flags are statistical outliers in this fund's own history, not errors or predictions. "
                "Verify flagged dates against news or the fund's disclosures.",
    }


def _interpret(row: pd.Series) -> str:
    ret = row["return"]
    if "benchmark_return" not in row or pd.isna(row.get("benchmark_return")):
        return f"Unusual daily move of {ret:+.2%} for this fund."
    bench = row["benchmark_return"]
    if abs(bench) >= 0.02 and np.sign(bench) == np.sign(ret) and abs(ret - bench) < abs(ret) * 0.5:
        return f"Market-wide move: fund {ret:+.2%} while the benchmark moved {bench:+.2%}."
    return f"Fund-specific move: fund {ret:+.2%} versus benchmark {bench:+.2%} on the same day."
