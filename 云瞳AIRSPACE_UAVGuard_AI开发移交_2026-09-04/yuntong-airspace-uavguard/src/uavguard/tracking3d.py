from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from itertools import count
from threading import RLock

import numpy as np

from .config import TrackingConfig
from .models import ObjectClass, StereoMeasurement, TrackEvent, TrackState


@dataclass(slots=True)
class _Track3D:
    track_id: str
    state: np.ndarray
    covariance: np.ndarray
    last_timestamp_s: float
    state_name: TrackState = TrackState.CANDIDATE
    hit_history: deque[bool] = field(default_factory=lambda: deque(maxlen=8))
    class_votes: dict[ObjectClass, float] = field(
        default_factory=lambda: defaultdict(float)
    )
    reprojection_error_px: float = 0.0
    camera_ids: tuple[str, ...] = ()
    missed_s: float = 0.0

    @property
    def position(self) -> np.ndarray:
        return self.state[:3]

    @property
    def velocity(self) -> np.ndarray:
        return self.state[3:6]

    def classification(self) -> tuple[ObjectClass, float]:
        if not self.class_votes:
            return ObjectClass.UNKNOWN, 0.0
        object_class, score = max(self.class_votes.items(), key=lambda item: item[1])
        total = sum(self.class_votes.values())
        confidence = float(score / total) if total > 0 else 0.0
        if object_class == ObjectClass.UNKNOWN:
            confidence = 0.0
        return object_class, confidence


class Tracker3D:
    def __init__(self, config: TrackingConfig) -> None:
        self.config = config
        self._tracks: dict[str, _Track3D] = {}
        self._ids = count(1)
        self._lock = RLock()

    @staticmethod
    def _transition(dt: float) -> np.ndarray:
        transition = np.eye(9)
        for axis in range(3):
            transition[axis, axis + 3] = dt
            transition[axis, axis + 6] = 0.5 * dt * dt
            transition[axis + 3, axis + 6] = dt
        return transition

    def _predict(self, track: _Track3D, timestamp_s: float) -> None:
        dt = max(0.0, min(timestamp_s - track.last_timestamp_s, 1.0))
        transition = self._transition(dt)
        process = np.diag(
            [0.2, 0.2, 0.2, 1.0, 1.0, 1.0, 4.0, 4.0, 4.0]
        ) * max(dt, 0.04)
        track.state = transition @ track.state
        track.covariance = transition @ track.covariance @ transition.T + process
        track.last_timestamp_s = timestamp_s
        track.missed_s += dt

    def _update(self, track: _Track3D, measurement: StereoMeasurement) -> None:
        observation = np.hstack([np.eye(3), np.zeros((3, 6))])
        measurement_covariance = np.asarray(measurement.covariance_m2, dtype=np.float64)
        diagonal = np.clip(np.diag(measurement_covariance), 0.25, 400.0)
        measurement_covariance = measurement_covariance.copy()
        np.fill_diagonal(measurement_covariance, diagonal)
        innovation = measurement.position_enu_m - observation @ track.state
        innovation_covariance = (
            observation @ track.covariance @ observation.T + measurement_covariance
        )
        gain = (
            track.covariance
            @ observation.T
            @ np.linalg.pinv(innovation_covariance)
        )
        track.state += gain @ innovation
        track.covariance = (
            np.eye(9) - gain @ observation
        ) @ track.covariance
        track.missed_s = 0.0
        track.hit_history[-1] = True
        vote = max(measurement.class_confidence, 0.05)
        track.class_votes[measurement.object_class] += vote
        track.reprojection_error_px = measurement.reprojection_error_px
        track.camera_ids = measurement.camera_ids

    def _event(
        self, track: _Track3D, timestamp_s: float, just_confirmed: bool = False
    ) -> TrackEvent:
        object_class, confidence = track.classification()
        return TrackEvent(
            track_id=track.track_id,
            timestamp_s=timestamp_s,
            state=track.state_name,
            object_class=object_class,
            class_confidence=confidence,
            position_enu_m=track.position.copy(),
            velocity_enu_mps=track.velocity.copy(),
            covariance_m2=track.covariance[:3, :3].copy(),
            reprojection_error_px=track.reprojection_error_px,
            camera_ids=track.camera_ids,
            metadata={"justConfirmed": just_confirmed},
        )

    def update(
        self, measurements: list[StereoMeasurement], timestamp_s: float
    ) -> list[TrackEvent]:
        with self._lock:
            for track in self._tracks.values():
                if track.hit_history.maxlen != self.config.confirm_window:
                    track.hit_history = deque(
                        track.hit_history, maxlen=self.config.confirm_window
                    )
                track.hit_history.append(False)
                self._predict(track, timestamp_s)

            candidates: list[tuple[float, str, int]] = []
            for track_id, track in self._tracks.items():
                for index, measurement in enumerate(measurements):
                    distance = float(
                        np.linalg.norm(track.position - measurement.position_enu_m)
                    )
                    if distance <= self.config.association_distance_m:
                        candidates.append((distance, track_id, index))

            used_tracks: set[str] = set()
            used_measurements: set[int] = set()
            updated_tracks: list[_Track3D] = []
            for _, track_id, index in sorted(candidates):
                if track_id in used_tracks or index in used_measurements:
                    continue
                track = self._tracks[track_id]
                self._update(track, measurements[index])
                used_tracks.add(track_id)
                used_measurements.add(index)
                updated_tracks.append(track)

            for index, measurement in enumerate(measurements):
                if index in used_measurements:
                    continue
                track_id = f"track-{next(self._ids):06d}"
                state = np.zeros(9, dtype=np.float64)
                state[:3] = measurement.position_enu_m
                covariance = np.diag(
                    [25.0, 25.0, 25.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
                )
                position_covariance = measurement.covariance_m2.copy()
                np.fill_diagonal(
                    position_covariance,
                    np.clip(np.diag(position_covariance), 0.25, 400.0),
                )
                covariance[:3, :3] = position_covariance
                track = _Track3D(
                    track_id=track_id,
                    state=state,
                    covariance=covariance,
                    last_timestamp_s=timestamp_s,
                    hit_history=deque([True], maxlen=self.config.confirm_window),
                    reprojection_error_px=measurement.reprojection_error_px,
                    camera_ids=measurement.camera_ids,
                )
                track.class_votes[measurement.object_class] += max(
                    measurement.class_confidence, 0.05
                )
                self._tracks[track_id] = track
                updated_tracks.append(track)

            events: list[TrackEvent] = []
            for track in updated_tracks:
                previous_state = track.state_name
                if sum(track.hit_history) >= self.config.confirm_hits:
                    track.state_name = TrackState.CONFIRMED
                just_confirmed = (
                    previous_state != TrackState.CONFIRMED
                    and track.state_name == TrackState.CONFIRMED
                )
                events.append(self._event(track, timestamp_s, just_confirmed))

            expired = [
                track_id
                for track_id, track in self._tracks.items()
                if track.missed_s > self.config.max_missed_s
            ]
            for track_id in expired:
                track = self._tracks.pop(track_id)
                track.state_name = TrackState.LOST
                events.append(self._event(track, timestamp_s))
            return events

    def snapshot(self, confirmed_only: bool = False) -> list[dict]:
        with self._lock:
            tracks = list(self._tracks.values())
            if confirmed_only:
                tracks = [
                    track for track in tracks if track.state_name == TrackState.CONFIRMED
                ]
            return [self._event(track, track.last_timestamp_s).to_dict() for track in tracks]
