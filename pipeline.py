from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from time import monotonic, sleep

import cv2
import mss
import numpy as np
import onnxruntime as ort


PORT = 18761
STREAM_URL = f"http://127.0.0.1:{PORT}/"
TARGET_FPS = 30
FRAME_LIFETIME = 0.30
ARM_TIMEOUT = 1.0
CONTENT_THRESHOLD = 0.55
NUDITY_THRESHOLD = 0.30
NUDITY_CLASSES = (2, 3, 4, 6, 14)


@dataclass(frozen=True)
class Frame:
    sequence: int
    captured_at: float
    jpeg: bytes


class Gate:
    def __init__(self):
        self.lock = Lock()
        self.generation = 0
        self.sequence = 0
        self.mode = "Blackout"
        self.reason = "Choose a display and arm when ready."
        self.monitor = None
        self.frame = None
        self.armed_at = 0.0
        self.clean_count = 0
        self.processing_ms = 0.0
        self.fps = 0.0

    def arm(self, monitor):
        with self.lock:
            self.generation += 1
            self.monitor = dict(monitor)
            self.frame = None
            self.armed_at = monotonic()
            self.clean_count = 0
            self.processing_ms = 0.0
            self.fps = 0.0
            self.mode = "Checking"
            self.reason = "Waiting for screened frames."

    def blackout(self, reason, generation=None):
        with self.lock:
            if generation is None or generation == self.generation:
                self._blackout(reason)

    def _blackout(self, reason):
        self.generation += 1
        self.monitor = None
        self.frame = None
        self.mode = "Blackout"
        self.reason = reason

    def capture_ticket(self):
        with self.lock:
            return self.generation, self.monitor

    def approve(self, generation, captured_at, jpeg):
        with self.lock:
            if generation != self.generation or self.monitor is None:
                return
            duration = monotonic() - captured_at
            if duration >= FRAME_LIFETIME:
                self._blackout("Screening timed out. Re-arm to try again.")
                return
            self.processing_ms = duration * 1000
            self.clean_count += 1
            if self.clean_count < 2:
                return
            if self.frame is not None:
                rate = 1 / max(0.001, captured_at - self.frame.captured_at)
                self.fps = 0.8 * self.fps + 0.2 * rate if self.fps else rate
            self.sequence += 1
            self.frame = Frame(self.sequence, captured_at, jpeg)
            self.mode = "Armed"
            self.reason = "Screened video is available to OBS."

    def snapshot(self):
        with self.lock:
            now = monotonic()
            if self.frame is not None and now - self.frame.captured_at >= FRAME_LIFETIME:
                self._blackout("Capture stalled. Re-arm to try again.")
            if self.mode == "Checking" and now - self.armed_at >= ARM_TIMEOUT:
                self._blackout("Capture did not start. Re-arm to try again.")
            return self.mode, self.reason, self.frame, self.processing_ms, self.fps


class Detector:
    def __init__(self, directory: Path):
        cv2.setNumThreads(1)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 3
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self.content = self._open(directory / "image-safety.onnx", options)
        self.nudity = self._open(directory / "nudity.onnx", options)
        self.content_input = self.content.get_inputs()[0].name
        self.nudity_input = self.nudity.get_inputs()[0].name
        self.classify(np.zeros((360, 640, 3), dtype=np.uint8))

    @staticmethod
    def _open(path, options):
        return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])

    def classify(self, bgr):
        pixels = cv2.dnn.blobFromImage(bgr, 1.0, (224, 224), swapRB=True)
        probabilities = self.content.run(None, {self.content_input: pixels})[0]
        if probabilities.shape != (1, 3) or not np.isfinite(probabilities).all():
            raise RuntimeError("Unexpected image safety model output.")
        gore, sexual = (float(score) for score in probabilities[0, :2])
        if gore + sexual >= CONTENT_THRESHOLD:
            return "Graphic content detected." if gore > sexual else "Sexual content detected."

        height, width = bgr.shape[:2]
        scale = 320 / max(height, width)
        resized = cv2.resize(bgr, (max(1, round(width * scale)), max(1, round(height * scale))))
        padded = np.zeros((320, 320, 3), dtype=np.uint8)
        padded[: resized.shape[0], : resized.shape[1]] = resized
        pixels = cv2.dnn.blobFromImage(padded, 1 / 255, (320, 320), swapRB=True)
        detections = self.nudity.run(None, {self.nudity_input: pixels})[0]
        if detections.shape != (1, 22, 2100) or not np.isfinite(detections).all():
            raise RuntimeError("Unexpected nudity model output.")
        scores = detections[0, 4:, :]
        strongest = scores.max(axis=0)
        classes = scores.argmax(axis=0)
        if np.any((strongest >= NUDITY_THRESHOLD) & np.isin(classes, NUDITY_CLASSES)):
            return "Nudity detected."
        return None


def screen_frame(gate, detector, generation, captured_at, bgr):
    reason = detector.classify(bgr)
    if reason:
        gate.blackout(f"{reason} Clear the display, then re-arm.", generation)
        return

    height, width = bgr.shape[:2]
    scale = min(1, 1920 / width, 1080 / height)
    if scale < 1:
        bgr = cv2.resize(bgr, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    success, encoded = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 86])
    if not success:
        raise RuntimeError("Frame encoding failed.")
    gate.approve(generation, captured_at, encoded.tobytes())


class CaptureWorker:
    def __init__(self, gate):
        self.gate = gate
        self.ready = Event()
        self.error = None

    def run(self):
        try:
            detector = Detector(Path(__file__).resolve().parent)
            capture = mss.mss()
        except Exception as exc:
            self.error = str(exc)
            return

        self.ready.set()
        with capture:
            while True:
                generation, monitor = self.gate.capture_ticket()
                if monitor is None:
                    sleep(0.012)
                    continue

                captured_at = monotonic()
                try:
                    screenshot = capture.grab(monitor)
                    bgr = cv2.cvtColor(np.asarray(screenshot), cv2.COLOR_BGRA2BGR)
                    screen_frame(self.gate, detector, generation, captured_at, bgr)
                except Exception as exc:
                    self.gate.blackout(f"Capture or screening failed. {exc}", generation)
                sleep(max(0, 1 / TARGET_FPS - (monotonic() - captured_at)))
