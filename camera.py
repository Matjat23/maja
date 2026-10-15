"""Maja's eyes: the webcam, with a visible on/off control.

The camera is only on while Matty has turned it on with the Camera button, and the window shows a live
preview the whole time. A photo is taken only when Matty sends a message while the
camera is on. Photos are kept in memory just long enough to ask the vision model, and
are never saved to disk or sent anywhere. Needs opencv-python (requirements.txt).
"""

import base64
import os
import sys

try:
    import cv2
    CAMERA_AVAILABLE = True
except ImportError:
    CAMERA_AVAILABLE = False

# The vision model looks at photos of at most this size (smaller is faster).
PHOTO_MAX_SIDE = 768


class CameraError(Exception):
    """A friendly, user-facing explanation of why the camera didn't work."""


def resize(frame, max_side):
    height, width = frame.shape[:2]
    scale = max_side / max(height, width)
    if scale >= 1:
        return frame
    return cv2.resize(frame, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)


def preview_png(frame, width=260):
    """The frame as base64 PNG, small enough for the window preview."""
    height = int(frame.shape[0] * width / frame.shape[1])
    small = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    return base64.b64encode(cv2.imencode(".png", small)[1].tobytes()).decode()


def photo_for_model(frame):
    """The frame as base64 JPEG, the format the vision model receives."""
    ok, jpeg = cv2.imencode(".jpg", resize(frame, PHOTO_MAX_SIDE), [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(jpeg.tobytes()).decode()


class Camera:
    def __init__(self, index=None):
        self.index = int(index if index is not None else os.getenv("MAJA_CAMERA", "0"))
        self._capture = None

    @property
    def is_on(self):
        return self._capture is not None

    def turn_on(self):
        # DirectShow opens webcams much faster than the default on Windows.
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        capture = cv2.VideoCapture(self.index, backend)
        if not capture.isOpened() or not capture.read()[0]:
            capture.release()
            raise CameraError(
                "I can't use the camera. Check that no other app is using it, and that "
                "Windows Settings → Privacy & security → Camera allows desktop apps."
            )
        self._capture = capture

    def frame(self):
        """The current picture, or None if the camera is off or didn't deliver one."""
        if self._capture is None:
            return None
        ok, frame = self._capture.read()
        return frame if ok else None

    def turn_off(self):
        if self._capture is not None:
            self._capture.release()
            self._capture = None
