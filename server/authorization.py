# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import html
import logging

from .process import stop_process

PAIR_SECONDS = 90


async def request_approval(address, origin, code):
    grouped_code = " ".join(code[i:i + 4] for i in range(0, len(code), 4))
    message = (
        f"Collegamento richiesto da {html.escape(address)}\n"
        f"Pagina: {html.escape(origin)}\n\n"
        f"Codice da inserire sul telefono: {grouped_code}\n\n"
        "Autorizza solo se hai appena premuto Connetti sul tuo telefono."
    )
    try:
        process = await asyncio.create_subprocess_exec(
            "zenity", "--question", "--title=PhoneCam", f"--text={message}",
            "--ok-label=Autorizza", "--cancel-label=Rifiuta",
            f"--timeout={PAIR_SECONDS}", stdout=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        logging.exception("Impossibile aprire la finestra di autorizzazione")
        return False
    try:
        return await process.wait() == 0
    finally:
        await stop_process(process)
