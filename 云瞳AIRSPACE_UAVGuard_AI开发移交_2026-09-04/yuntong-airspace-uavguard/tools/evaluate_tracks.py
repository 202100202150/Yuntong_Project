#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate confirmed tracks against RTK truth")
    parser.add_argument("--truth", type=Path, required=True, help="CSV: timestamp_s,x,y,z,class")
    parser.add_argument("--predictions", type=Path, required=True, help="JSONL TrackEvent objects")
    parser.add_argument("--time-tolerance-ms", type=float, default=100.0)
    parser.add_argument("--distance-gate-m", type=float, default=30.0)
    args = parser.parse_args()
    truth = []
    with args.truth.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            truth.append(
                (
                    float(row["timestamp_s"]),
                    np.array([float(row["x"]), float(row["y"]), float(row["z"])]),
                    row["class"],
                )
            )
    predictions = []
    with args.predictions.open("r", encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            if item.get("state") != "confirmed":
                continue
            timestamp = datetime.fromisoformat(
                item["timestampUtc"].replace("Z", "+00:00")
            ).timestamp()
            predictions.append(
                (timestamp, np.asarray(item["positionEnuM"], dtype=float), item["class"])
            )
    used_predictions: set[int] = set()
    errors, pairs = [], []
    tolerance_s = args.time_tolerance_ms / 1000.0
    for timestamp, position, object_class in truth:
        options = [
            (abs(prediction[0] - timestamp), index, prediction)
            for index, prediction in enumerate(predictions)
            if index not in used_predictions and abs(prediction[0] - timestamp) <= tolerance_s
        ]
        if not options:
            continue
        _, index, prediction = min(options)
        distance = float(np.linalg.norm(prediction[1] - position))
        if distance > args.distance_gate_m:
            continue
        used_predictions.add(index)
        errors.append(distance)
        pairs.append((object_class, prediction[2]))
    recall = len(errors) / max(len(truth), 1)
    rmse = float(np.sqrt(np.mean(np.square(errors)))) if errors else float("nan")
    classes = sorted({item for pair in pairs for item in pair})
    f1_values = []
    for object_class in classes:
        tp = sum(a == object_class and b == object_class for a, b in pairs)
        fp = sum(a != object_class and b == object_class for a, b in pairs)
        fn = sum(a == object_class and b != object_class for a, b in pairs)
        precision = tp / max(tp + fp, 1)
        class_recall = tp / max(tp + fn, 1)
        f1_values.append(2 * precision * class_recall / max(precision + class_recall, 1e-9))
    print(json.dumps({
        "truthSamples": len(truth),
        "matchedSamples": len(errors),
        "recall": recall,
        "positionRmseM": rmse,
        "macroF1": float(np.mean(f1_values)) if f1_values else None,
        "unmatchedPredictions": len(predictions) - len(used_predictions),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
