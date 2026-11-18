# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
from contextlib import suppress


async def stop_process(process):
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.terminate()
    try:
        await asyncio.wait_for(process.wait(), 2)
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()


class MediaPipe:
    def __init__(self, command):
        self.command = command
        self.process = None

    async def write(self, data):
        if self.process is None:
            self.process = await asyncio.create_subprocess_exec(
                *self.command, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
            )
        self.process.stdin.write(data)
        await asyncio.wait_for(self.process.stdin.drain(), 2)

    async def close(self, final_frame=None):
        if self.process is None:
            return
        process = self.process
        try:
            if final_frame is not None and not process.stdin.is_closing():
                process.stdin.write(final_frame)
                await asyncio.wait_for(process.stdin.drain(), 0.5)
            process.stdin.close()
            await asyncio.wait_for(process.wait(), 1)
        except (OSError, TimeoutError):
            pass
        finally:
            process.stdin.close()
            await stop_process(process)
