# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import ipaddress
import logging
import os
import shutil
from urllib.parse import urlsplit

from aiohttp import web

from .signaling import Receiver, local_address


def page_origin(value):
    try:
        url = urlsplit(value)
        hostname = url.hostname
        port = url.port
    except ValueError:
        return False
    if not hostname or url.username is not None:
        return False
    if url.scheme != "https" and not (
            url.scheme == "http" and hostname in {"localhost", "127.0.0.1"}):
        return False
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and port != {"https": 443, "http": 80}[url.scheme]:
        host += f":{port}"
    return value == f"{url.scheme}://{host}"


def main():
    parser = argparse.ArgumentParser(description="Ricevitore locale PhoneCam")
    parser.add_argument("--host", default=os.getenv("PHONECAM_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=os.getenv("PHONECAM_PORT", "8765"))
    parser.add_argument("--origin", action="append", help="Origine HTTPS esatta della pagina")
    args = parser.parse_args()
    origins = args.origin or os.getenv("PHONECAM_ORIGINS", "").split()
    if not origins or not all(page_origin(origin) for origin in origins):
        parser.error("Indica almeno un'origine esatta, per esempio https://utente.github.io")
    if args.host != "0.0.0.0" and not local_address(args.host):
        parser.error("Indica un indirizzo IPv4 privato o 0.0.0.0.")
    if not 1 <= args.port <= 65535:
        parser.error("La porta deve essere compresa tra 1 e 65535.")
    if not shutil.which("zenity"):
        parser.error("zenity mancante. Esegui install.sh.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("aioice").setLevel(logging.WARNING)
    logging.info("Origini consentite: %s", ", ".join(origins))
    web.run_app(
        Receiver(origins).app(), host=str(ipaddress.IPv4Address(args.host)), port=args.port,
        access_log=None, shutdown_timeout=5,
        print=lambda message: logging.info("%s", message.replace("\n", " ")),
    )


if __name__ == "__main__":
    main()
