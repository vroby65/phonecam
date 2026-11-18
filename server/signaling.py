# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import base64
import hmac
import ipaddress
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field

from aiohttp import web
from aiortc import RTCBundlePolicy, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError
from aiortc.sdp import SessionDescription, candidate_from_sdp

from .audio import AudioOutput, VirtualMicrophone
from .authorization import PAIR_SECONDS, request_approval
from .video import RESOLUTIONS, VideoOutput, camera_device

LOG = logging.getLogger("phonecam")
PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(network) for network in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
))
MDNS = re.compile(r"[a-zA-Z0-9-]{1,63}\.local\Z")


def local_address(value):
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return False
    return address.is_loopback or any(address in net for net in PRIVATE_NETWORKS)


def proof(code, session_id, action, payload=""):
    message = f"{session_id}\n{action}\n{payload}".encode()
    return hmac.digest(code.encode("ascii"), message, "sha256").hex()


def filtered_sdp(sdp, address, allow_mdns=False):
    lines = []
    for line in sdp.splitlines():
        if line.startswith("a=candidate:"):
            candidate = candidate_from_sdp(line.removeprefix("a=candidate:"))
            host_ok = candidate.ip == address or (allow_mdns and MDNS.fullmatch(candidate.ip))
            if candidate.type != "host" or candidate.protocol.lower() != "udp" or not host_ok:
                continue
        lines.append(line)
    return "\r\n".join(lines) + "\r\n"


def error(status, message):
    return web.json_response({"error": message}, status=status)


@dataclass
class Session:
    address: str
    origin: str
    ident: str
    code: str
    approval: asyncio.Task
    deadline: float
    stage: str = "pairing"
    peer: RTCPeerConnection | None = None
    outputs: dict = field(default_factory=dict)
    writers: list = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closing: asyncio.Task | None = None


class Receiver:
    def __init__(self, origins, authorizer=request_approval, video_factory=None,
                 audio_factory=None, microphone=None):
        self.origins = frozenset(origins)
        self.authorizer = authorizer
        self.microphone = microphone if microphone is not None else VirtualMicrophone()
        self.video_factory = video_factory or self.video_output
        self.audio_factory = audio_factory or self.audio_output
        self.session = None
        self.stopping = False

    async def video_output(self, width, height):
        return VideoOutput(camera_device(), width, height)

    async def audio_output(self):
        await self.microphone.start()
        return AudioOutput()

    @web.middleware
    async def lan_only(self, request, handler):
        transport = request.transport
        remote = transport.get_extra_info("peername") if transport else None
        local = transport.get_extra_info("sockname") if transport else None
        if not remote or not local or not local_address(remote[0]) or not local_address(local[0]):
            return error(403, "Il ricevitore accetta solo indirizzi IPv4 locali.")
        if request.host != f"{local[0]}:{local[1]}" and local != (request.host, 80):
            return error(403, "Usa l’indirizzo IPv4 del ricevitore come destinazione.")
        origin = request.headers.get("Origin")
        if origin is None and request.method == "GET" and request.path == "/health":
            return await handler(request)
        if origin not in self.origins:
            return error(403, "Origine della pagina non autorizzata sul ricevitore.")
        if request.method == "OPTIONS" and request.match_info.http_exception is None:
            if request.headers.get("Access-Control-Request-Method") != "POST":
                return error(405, "Metodo non disponibile.")
            response = web.Response(status=204)
            response.headers.update({
                "Access-Control-Allow-Methods": "POST",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Max-Age": "300",
            })
        else:
            try:
                response = await handler(request)
            except web.HTTPException as exc:
                response = error(exc.status, "Richiesta HTTP non valida.")
        response.headers.update({
            "Access-Control-Allow-Origin": origin,
            "Vary": "Origin",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        })
        return response

    async def health(self, request):
        LOG.info("Verifica diretta del ricevitore da %s", request.remote)
        return web.Response(
            text="Ricevitore PhoneCam raggiungibile. Torna alla pagina PhoneCam per collegarti.",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    async def pair(self, request):
        if request.content_type != "application/json":
            return error(415, "Usa una richiesta JSON.")
        if await request.read() != b"{}":
            return error(400, "Richiesta di associazione non valida.")
        if self.stopping:
            return error(503, "Il ricevitore si sta arrestando.")
        current = self.session
        if current is not None:
            if current.deadline <= time.monotonic() or self.rejected(current):
                await asyncio.shield(self.schedule_close(current))
            if self.session is not None or self.stopping:
                return error(409, "Il ricevitore ha già una richiesta o una connessione attiva.")
        address, origin = request.remote, request.headers["Origin"]
        code = base64.b32encode(secrets.token_bytes(13)).decode()[:20]
        ident = secrets.token_urlsafe(18)
        approval = asyncio.create_task(self.authorizer(address, origin, code))
        self.session = Session(address, origin, ident, code, approval,
                               time.monotonic() + PAIR_SECONDS)
        LOG.info("Richiesta di autorizzazione da %s", address)
        return web.json_response({"id": ident})

    @staticmethod
    def rejected(session):
        approval = session.approval
        return (session.stage == "pairing" and approval.done()
                and (approval.cancelled() or approval.exception() is not None or not approval.result()))

    async def action(self, request):
        session = self.session
        if (session is None or session.ident != request.match_info["id"]
                or session.address != request.remote or session.origin != request.headers["Origin"]):
            return error(404, "Sessione scaduta o non disponibile.")
        if request.content_type != "application/json":
            return error(415, "Usa una richiesta JSON.")
        try:
            body = await request.json()
        except (ValueError, UnicodeError):
            return error(400, "Richiesta JSON non valida.")
        if not isinstance(body, dict):
            return error(400, "Richiesta JSON non valida.")
        action = request.match_info["action"]
        async with session.lock:
            if session.closing is not None or session.deadline <= time.monotonic():
                return error(410, "Sessione scaduta o chiusa.")
            if self.rejected(session):
                self.schedule_close(session)
                LOG.info("Collegamento rifiutato da desktop per %s", session.address)
                return error(403, "Collegamento rifiutato o finestra non disponibile sul PC.")
            if action == "status":
                return web.json_response({"approved": session.approval.done()})
            signature = body.get("proof")
            payload = body.get("payload", "")
            if (not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature)
                    or not isinstance(payload, str)):
                return error(400, "Firma o contenuto della richiesta non validi.")
            try:
                signed = proof(session.code, session.ident, action, payload)
            except UnicodeError:
                return error(400, "Contenuto della richiesta non valido.")
            if not hmac.compare_digest(signature, signed):
                return error(403, "Codice di associazione errato.")
            if action == "close":
                self.schedule_close(session)
                return web.json_response({})
            if action == "confirm" and session.stage == "pairing":
                if not session.approval.done():
                    return error(425, "Autorizza prima la richiesta sul PC.")
                session.stage = "confirmed"
                session.deadline = time.monotonic() + 60
                return web.json_response({"proof": proof(session.code, session.ident, "confirmed")})
            if action == "offer" and session.stage == "confirmed":
                local_ip = request.transport.get_extra_info("sockname")[0]
                return await self.offer(session, local_ip, payload)
            return error(409, "Operazione non disponibile in questa fase.")

    async def offer(self, session, local_ip, payload):
        try:
            data = json.loads(payload)
            width, height = data["width"], data["height"]
            mic = data["microphone"]
            sdp = filtered_sdp(data["sdp"], session.address, allow_mdns=True)
            media = SessionDescription.parse(sdp).media
            expected = ["audio", "video"] if mic else ["video"]
            if (type(width) is not int or type(height) is not int
                    or (width, height) not in RESOLUTIONS or not isinstance(mic, bool)
                    or sorted(item.kind for item in media) != expected
                    or any(item.direction != "sendonly" or not item.port for item in media)):
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError, AssertionError, IndexError):
            return error(400, "Offerta WebRTC non valida.")
        session.stage = "connecting"
        session.deadline = time.monotonic() + 30
        try:
            async with asyncio.timeout(15):
                session.outputs["video"] = await self.video_factory(width, height)
                if mic:
                    session.outputs["audio"] = await self.audio_factory()
                peer = RTCPeerConnection(RTCConfiguration(
                    iceServers=[], bundlePolicy=RTCBundlePolicy.MAX_BUNDLE,
                ))
                session.peer = peer

                @peer.on("connectionstatechange")
                def state_changed():
                    if peer.connectionState == "connected" and session.closing is None:
                        session.stage = "connected"
                        session.deadline = time.monotonic() + 8 * 60 * 60
                        LOG.info("Trasmissione attiva da %s", session.address)
                    elif peer.connectionState in {"failed", "closed", "disconnected"}:
                        self.schedule_close(session)

                @peer.on("track")
                def track_received(track):
                    session.writers.append(asyncio.create_task(
                        self.write_track(session, track, session.outputs[track.kind])
                    ))

                await peer.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
                await peer.setLocalDescription(await peer.createAnswer())
                answer = filtered_sdp(peer.localDescription.sdp, local_ip)
                if "\r\na=candidate:" not in answer:
                    raise RuntimeError("Nessun candidato ICE sull'interfaccia IPv4 locale.")
        except TimeoutError:
            self.schedule_close(session)
            LOG.warning("Negoziazione WebRTC scaduta")
            return error(504, "Tempo esaurito nella negoziazione WebRTC.")
        except (RuntimeError, OSError) as exc:
            self.schedule_close(session)
            LOG.warning("Uscita o rete non disponibile: %s", exc)
            return error(503, str(exc))
        except Exception:
            self.schedule_close(session)
            LOG.exception("Negoziazione WebRTC fallita")
            return error(400, "Negoziazione WebRTC fallita.")
        return web.json_response({
            "sdp": answer, "proof": proof(session.code, session.ident, "answer", answer),
        })

    async def write_track(self, session, track, output):
        try:
            while True:
                await output.write(await track.recv())
        except MediaStreamError:
            pass
        except Exception:
            LOG.exception("Errore nell'uscita %s", track.kind)
        self.schedule_close(session)

    def schedule_close(self, session):
        if session.closing is None:
            session.closing = asyncio.create_task(self.close(session))
        return session.closing

    async def close(self, session):
        async with session.lock:
            session.approval.cancel()
            await asyncio.gather(session.approval, return_exceptions=True)
            for writer in session.writers:
                writer.cancel()
            await asyncio.gather(*session.writers, return_exceptions=True)
            try:
                if session.peer is not None:
                    await session.peer.close()
            finally:
                results = await asyncio.gather(*(output.close() for output in session.outputs.values()),
                                               return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException):
                        LOG.error("Chiusura dell'uscita fallita: %s", result)
                if self.session is session:
                    self.session = None
                LOG.info("Sessione terminata per %s", session.address)

    async def lifetime(self, _app):
        try:
            await self.microphone.start()
        except (RuntimeError, OSError) as exc:
            LOG.warning("Audio non disponibile all'avvio: %s", exc)
        sweeper = asyncio.create_task(self.sweep())
        try:
            yield
        finally:
            self.stopping = True
            sweeper.cancel()
            await asyncio.gather(sweeper, return_exceptions=True)
            try:
                if self.session is not None:
                    await self.schedule_close(self.session)
            finally:
                await self.microphone.close()

    async def sweep(self):
        while True:
            await asyncio.sleep(1)
            session = self.session
            if session is not None and session.deadline <= time.monotonic():
                self.schedule_close(session)

    def app(self):
        app = web.Application(middlewares=[self.lan_only], client_max_size=65536)
        app.cleanup_ctx.append(self.lifetime)
        app.router.add_get("/health", self.health, allow_head=False)
        app.router.add_post("/pair", self.pair)
        app.router.add_post("/session/{id}/{action:status|confirm|offer|close}", self.action)
        app.router.add_route("OPTIONS", "/pair", self.pair)
        app.router.add_route("OPTIONS", "/session/{id}/{action:status|confirm|offer|close}", self.action)
        return app
