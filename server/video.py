# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shutil
import stat
from pathlib import Path

from .process import MediaPipe

CAMERA_NAME = "PhoneCam Camera"
RESOLUTIONS = {(640, 480), (1280, 720)}


def camera_device(sysfs=Path("/sys/devices/virtual/video4linux"), dev=Path("/dev")):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg mancante. Esegui install.sh sul PC.")
    for entry in sorted(sysfs.glob("video*")):
        try:
            name = (entry / "name").read_text().strip()
        except FileNotFoundError:
            continue
        if name == CAMERA_NAME:
            device = dev / entry.name
            if not device.exists() or not stat.S_ISCHR(device.stat().st_mode):
                continue
            if not os.access(device, os.W_OK):
                raise RuntimeError(f"Permesso di scrittura mancante su {device}.")
            return str(device)
    raise RuntimeError("PhoneCam Camera assente. Controlla v4l2loopback sul PC.")


class VideoOutput:
    def __init__(self, device, width, height):
        self.width = width
        self.height = height
        self.pipe = MediaPipe([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "rawvideo", "-pixel_format", "yuyv422",
            "-video_size", f"{width}x{height}", "-framerate", "30", "-i", "pipe:0",
            "-an", "-c:v", "rawvideo", "-pix_fmt", "yuyv422", "-f", "v4l2", device,
        ])

    async def write(self, frame):
        converted = frame.reformat(width=self.width, height=self.height, format="yuyv422")
        plane = converted.planes[0]
        data = bytes(plane)
        row_bytes = self.width * 2
        if plane.line_size != row_bytes:
            data = b"".join(data[start:start + row_bytes]
                            for start in range(0, plane.line_size * self.height, plane.line_size))
        await self.pipe.write(data)

    async def close(self):
        # YUYV, nero in range video. Evita di lasciare l'ultimo fotogramma in coda.
        black = bytes((16, 128, 16, 128)) * (self.width * self.height // 2)
        await self.pipe.close(black)
