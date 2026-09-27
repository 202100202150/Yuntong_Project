from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .config import ClassifierConfig
from .models import Detection2D, ObjectClass


class RoiClassifier:
    """ONNX candidate classifier. Missing weights deliberately produce UNKNOWN."""

    CLASSES = (
        ObjectClass.MULTIROTOR,
        ObjectClass.FIXED_WING_UAV,
        ObjectClass.BIRD,
        ObjectClass.UNKNOWN,
    )

    def __init__(self, config: ClassifierConfig) -> None:
        self.config = config
        self.session = None
        self.input_name: str | None = None
        model_path = Path(config.model_path) if config.model_path else None
        if model_path and model_path.exists():
            try:
                import onnxruntime as ort

                available = set(ort.get_available_providers())
                providers = [p for p in config.providers if p in available]
                self.session = ort.InferenceSession(
                    str(model_path), providers=providers or None
                )
                self.input_name = self.session.get_inputs()[0].name
            except ImportError as exc:
                raise RuntimeError(
                    "An ONNX model was configured but onnxruntime is not installed"
                ) from exc

    @property
    def ready(self) -> bool:
        return self.session is not None

    def classify(self, frame: np.ndarray, detection: Detection2D) -> Detection2D:
        if self.session is None or self.input_name is None:
            detection.object_class = ObjectClass.UNKNOWN
            detection.class_confidence = 0.0
            return detection
        x1, y1, x2, y2 = [int(round(v)) for v in detection.bbox_xyxy]
        width, height = x2 - x1, y2 - y1
        padding = max(width, height)
        x1 = max(0, x1 - padding)
        y1 = max(0, y1 - padding)
        x2 = min(frame.shape[1], x2 + padding)
        y2 = min(frame.shape[0], y2 + padding)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return detection
        image = cv2.resize(
            crop,
            (self.config.input_size, self.config.input_size),
            interpolation=cv2.INTER_CUBIC,
        )
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        tensor = np.transpose(image, (2, 0, 1))[None]
        output = np.asarray(self.session.run(None, {self.input_name: tensor})[0])
        if output.shape[-1] == len(self.CLASSES):
            logits = output.reshape(-1)
            logits -= np.max(logits)
            probabilities = np.exp(logits) / np.sum(np.exp(logits))
            index = int(np.argmax(probabilities))
            confidence = float(probabilities[index])
        elif output.ndim >= 2 and output.shape[-1] >= 5 + len(self.CLASSES):
            # YOLOX export with decoded boxes: [cx,cy,w,h,obj,class...].
            predictions = output.reshape(-1, output.shape[-1])
            scores = predictions[:, 4:5] * predictions[:, 5 : 5 + len(self.CLASSES)]
            flat_index = int(np.argmax(scores))
            _, index = np.unravel_index(flat_index, scores.shape)
            confidence = float(scores.reshape(-1)[flat_index])
        else:
            raise RuntimeError(
                f"Unsupported ONNX output shape {output.shape}; expected 4 logits or decoded YOLOX output"
            )
        if index >= len(self.CLASSES) or confidence < self.config.minimum_confidence:
            detection.object_class = ObjectClass.UNKNOWN
            detection.class_confidence = confidence
        else:
            detection.object_class = self.CLASSES[index]
            detection.class_confidence = confidence
        return detection
