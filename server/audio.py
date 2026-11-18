# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio

from av import AudioResampler

from .process import MediaPipe, stop_process

SINK = "phonecam_sink"
SOURCE = "phonecam_microphone"
RATE = 48000

LOOPBACK_COMMAND = (
    "pw-loopback", "--name=phonecam", "--channels=1", "-m", "[ MONO ]",
    "--capture-props={ media.class=Audio/Sink node.name=phonecam_sink "
    'node.description="PhoneCam Audio Input" node.virtual=true priority.session=0 }',
    "--playback-props={ media.class=Audio/Source node.name=phonecam_microphone "
    'node.description="PhoneCam Microphone" node.virtual=true priority.session=0 }',
)


async def pulse_nodes(kind):
    process = await asyncio.create_subprocess_exec(
        "pactl", "list", "short", kind,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 2)
        if process.returncode:
            return set()
        return {fields[1].decode() for line in output.splitlines()
                if len(fields := line.split()) >= 2}
    finally:
        await stop_process(process)


class VirtualMicrophone:
    def __init__(self):
        self.process = None

    async def start(self):
        if self.process is not None and self.process.returncode is None:
            return
        if SINK in await pulse_nodes("sinks") or SOURCE in await pulse_nodes("sources"):
            raise RuntimeError("Esiste già un dispositivo audio PhoneCam.")
        self.process = await asyncio.create_subprocess_exec(
            *LOOPBACK_COMMAND, stdout=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(4):
                while self.process.returncode is None:
                    if SINK in await pulse_nodes("sinks") and SOURCE in await pulse_nodes("sources"):
                        return
                    await asyncio.sleep(0.1)
        except TimeoutError:
            pass
        except BaseException:
            await self.close()
            raise
        await self.close()
        raise RuntimeError("PhoneCam Microphone non disponibile. Controlla PipeWire, WirePlumber e pipewire-pulse.")

    async def close(self):
        if self.process is not None:
            await stop_process(self.process)
            self.process = None


class AudioOutput:
    def __init__(self):
        self.resampler = AudioResampler(format="s16", layout="mono", rate=RATE)
        self.pipe = MediaPipe([
            "pacat", "--playback", "--raw", f"--device={SINK}",
            f"--rate={RATE}", "--channels=1", "--format=s16le", "--latency-msec=45",
            "--client-name=PhoneCam", "--stream-name=PhoneCam",
            "--property=node.dont-reconnect=true",
        ])

    async def write(self, frame):
        for converted in self.resampler.resample(frame):
            await self.pipe.write(bytes(converted.planes[0])[:converted.samples * 2])

    async def close(self):
        await self.pipe.close()
