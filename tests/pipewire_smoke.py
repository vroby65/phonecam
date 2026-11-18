# SPDX-License-Identifier: GPL-3.0-or-later
"""Prova facoltativa con una sessione PipeWire, usando esclusivamente un tono sintetico."""
import asyncio
import math
import struct
from fractions import Fraction

from av import AudioFrame

from server.audio import SINK, SOURCE, AudioOutput, VirtualMicrophone, pulse_nodes
from server.process import stop_process


async def main():
    microphone = VirtualMicrophone()
    audio = AudioOutput()
    recorder = None
    try:
        await microphone.start()
        recorder = await asyncio.create_subprocess_exec(
            "pacat", "--record", "--raw", f"--device={SOURCE}", "--rate=48000",
            "--channels=1", "--format=s16le", "--latency-msec=45",
            stdout=asyncio.subprocess.PIPE,
        )
        async with asyncio.timeout(8):
            samples = 960
            for step in range(50):
                frame = AudioFrame(format="s16", layout="stereo", samples=samples)
                frame.sample_rate = 48000
                frame.time_base = Fraction(1, 48000)
                frame.pts = step * samples
                sine = (int(9500 * math.sin(2 * math.pi * 1000 * i / 48000))
                        for i in range(samples))
                stereo = (struct.pack("<hh", value, value) for value in sine)
                frame.planes[0].update(b"".join(stereo))
                await audio.write(frame)
                await asyncio.sleep(0.02)
            recorded = await recorder.stdout.readexactly(48000 * 2)
        values = [sample for (sample,) in struct.iter_unpack("<h", recorded)]
        if max(map(abs, values)) < 5000:
            raise RuntimeError("Il tono sintetico non è arrivato alla sorgente.")
    finally:
        if recorder is not None:
            await stop_process(recorder)
        await audio.close()
        await microphone.close()
    if SOURCE in await pulse_nodes("sources") or SINK in await pulse_nodes("sinks"):
        raise RuntimeError("I nodi PipeWire sono rimasti dopo la chiusura.")
    print("Tono stereo convertito in mono, letto da PhoneCam Microphone e nodi rimossi.")


if __name__ == "__main__":
    asyncio.run(main())
