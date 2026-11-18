# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import hashlib
import secrets
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import ClientSession, TCPConnector
from aiohttp.test_utils import TestClient, TestServer
from aiortc import (AudioStreamTrack, RTCBundlePolicy, RTCConfiguration, RTCPeerConnection,
                    RTCSessionDescription, VideoStreamTrack)

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from server.main import page_origin
from server.signaling import Receiver, filtered_sdp, local_address, proof
from server.video import camera_device

ORIGIN = "https://phonecam.example"
OTHER_ORIGIN = "https://preview.example"
HEADERS = {"Origin": ORIGIN}


class FakeMicrophone:
    started = False
    closed = False

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True


class Output:
    def __init__(self):
        self.received = asyncio.Event()
        self.closed = False

    async def write(self, _frame):
        self.received.set()

    async def close(self):
        self.closed = True


class ReceiverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.approval = asyncio.Future()
        self.code = None
        self.video = Output()
        self.audio = Output()

        async def authorizer(_address, _origin, code):
            self.code = code
            return await self.approval

        async def video_factory(_width, _height):
            return self.video

        async def audio_factory():
            return self.audio

        self.mic = FakeMicrophone()
        self.factories = (authorizer, video_factory, audio_factory)
        self.client = await self.client_for("127.0.0.1")

    async def client_for(self, address):
        self.receiver = Receiver([ORIGIN, OTHER_ORIGIN], *self.factories, self.mic)
        client = TestClient(TestServer(self.receiver.app(), host=address))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        return client

    async def begin_pair(self, client, public_key, nonce):
        commitment = hashlib.sha256(f"{public_key}\n{nonce}".encode()).hexdigest()
        response = await client.post("/pair", json={"commitment": commitment}, headers=HEADERS)
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    @staticmethod
    def public_hex(key):
        return key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
        ).hex()

    async def pair(self, client=None):
        client = client or self.client
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_key = self.public_hex(private_key)
        nonce = secrets.token_hex(16)
        started = await self.begin_pair(client, public_key, nonce)
        ident, server_key = started["id"], started["publicKey"]
        receiver_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), bytes.fromhex(server_key),
        )
        secret = private_key.exchange(ec.ECDH(), receiver_key)
        info = f"phonecam-pair-v1\n{ident}\n{public_key}\n{server_key}\n{nonce}".encode()
        self.key = HKDF(hashes.SHA256(), 32, salt=None, info=info).derive(secret)
        response = await client.post(
            f"/session/{ident}/exchange", json={"publicKey": public_key, "nonce": nonce},
            headers=HEADERS,
        )
        self.assertEqual(response.status, 200, await response.text())
        await asyncio.sleep(0)
        expected = int(proof(self.key, ident, "pairing-code")[:8], 16) % 10000
        self.assertEqual(self.code, f"{expected:04d}")
        return ident

    async def action(self, ident, action, *, client=None, payload="", key=None, origin=ORIGIN):
        client = client or self.client
        signature = proof(key or self.key, ident, action, payload)
        return await client.post(
            f"/session/{ident}/{action}", headers={"Origin": origin},
            json={"proof": signature, "payload": payload},
        )

    async def confirm(self, ident, client=None):
        self.approval.set_result(True)
        await asyncio.sleep(0)
        response = await self.action(ident, "confirm", client=client)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["proof"], proof(self.key, ident, "confirmed"))

    async def wait_closed(self):
        async with asyncio.timeout(5):
            while self.receiver.session is not None:
                await asyncio.sleep(0.02)

    async def test_start_shutdown_cancels_pending_dialog(self):
        self.assertTrue(self.mic.started)
        await self.pair()
        pending = self.receiver.session.approval
        await self.client.close()
        self.assertTrue(pending.cancelled())
        self.assertTrue(self.mic.closed)
        self.assertIsNone(self.receiver.session)

    async def test_direct_health_check_is_lan_only_and_does_not_pair(self):
        response = await self.client.get("/health")
        self.assertEqual(response.status, 200)
        self.assertIn("Ricevitore PhoneCam raggiungibile", await response.text())
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        for headers in ({"Host": "other.example"}, {"Origin": "https://other.example"}):
            self.assertEqual((await self.client.get("/health", headers=headers)).status, 403)
        self.assertEqual((await self.client.post("/pair", json={})).status, 403)
        self.assertIsNone(self.receiver.session)

    async def test_cors_rejects_unconfigured_origin_and_dns_rebinding(self):
        response = await self.client.options("/pair", headers={
            **HEADERS, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        })
        self.assertEqual(response.status, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(response.headers["Access-Control-Allow-Headers"], "Content-Type")
        response = await self.client.options("/unknown", headers={
            **HEADERS, "Access-Control-Request-Method": "POST",
        })
        self.assertEqual(response.status, 404)
        for headers in ({"Origin": "https://other.example"}, {**HEADERS, "Host": "attacker.example"}):
            result = await self.client.post("/pair", json={}, headers=headers)
            self.assertEqual(result.status, 403)
            self.assertNotIn("Access-Control-Allow-Origin", result.headers)
        self.assertIsNone(self.receiver.session)

    async def test_approval_requires_session_key_and_matching_origin(self):
        ident = await self.pair()
        self.assertEqual((await self.action(ident, "confirm")).status, 425)
        self.assertEqual((await self.client.post("/pair", json={"commitment": "a" * 64}, headers=HEADERS)).status, 409)
        self.assertEqual((await self.action(ident, "confirm", origin=OTHER_ORIGIN)).status, 404)
        self.assertEqual((await self.action(ident, "confirm", key=self.code.encode())).status, 403)
        await self.confirm(ident)
        self.assertEqual((await self.action(ident, "confirm")).status, 409)
        self.assertEqual((await self.action(ident, "offer", payload="{}")).status, 400)
        self.assertEqual((await self.action(ident, "close")).status, 200)
        await self.wait_closed()
        self.assertEqual((await self.action(ident, "confirm")).status, 404)

    async def test_pairing_binds_the_public_key_before_showing_the_code(self):
        public_key = self.public_hex(ec.generate_private_key(ec.SECP256R1()))
        other_key = self.public_hex(ec.generate_private_key(ec.SECP256R1()))
        nonce = secrets.token_hex(16)
        started = await self.begin_pair(self.client, public_key, nonce)
        path = f"/session/{started['id']}/exchange"
        self.assertIsNone(self.code)
        reply = await self.client.post(
            path, json={"publicKey": other_key, "nonce": nonce}, headers=HEADERS,
        )
        self.assertEqual(reply.status, 403)
        self.assertIsNone(self.code)
        reply = await self.client.post(
            path, json={"publicKey": public_key, "nonce": nonce}, headers=HEADERS,
        )
        self.assertEqual(reply.status, 200)
        reply = await self.client.post(
            path, json={"publicKey": public_key, "nonce": nonce}, headers=HEADERS,
        )
        self.assertEqual(reply.status, 409)

    async def test_confirmation_cannot_be_reused_from_another_address(self):
        ident = await self.pair()
        self.approval.set_result(True)
        async with ClientSession(connector=TCPConnector(local_addr=("127.0.0.2", 0))) as other:
            response = await other.post(
                self.client.make_url(f"/session/{ident}/confirm"), headers=HEADERS,
                json={"proof": proof(self.key, ident, "confirm")},
            )
            self.assertEqual(response.status, 404)
        response = await self.action(ident, "confirm")
        self.assertEqual(response.status, 200)

    async def test_refusal_can_be_reported_without_code_and_new_pair_allowed(self):
        ident = await self.pair()
        self.approval.set_result(False)
        await asyncio.sleep(0)
        status = await self.client.post(f"/session/{ident}/status", json={}, headers=HEADERS)
        self.assertEqual(status.status, 403)
        self.assertIn("rifiutato", (await status.json())["error"])
        await self.wait_closed()
        self.approval = asyncio.Future()
        self.assertNotEqual(await self.pair(), ident)

    async def test_expired_approval_releases_slot(self):
        ident = await self.pair()
        self.receiver.session.deadline = time.monotonic() - 1
        self.assertEqual((await self.action(ident, "confirm")).status, 410)
        await self.wait_closed()
        self.assertTrue(self.approval.cancelled())

    async def test_missing_camera_reports_unavailable_and_closes_session(self):
        async def missing_camera(_width, _height):
            return camera_device(Path("/path/without/a/camera"))

        self.receiver.video_factory = missing_camera
        ident = await self.pair()
        await self.confirm(ident)
        peer = self.peer()
        payload = await self.sender_offer(peer, microphone=False)
        response = await self.action(ident, "offer", payload=payload)
        self.assertEqual(response.status, 503)
        self.assertRegex((await response.json())["error"], "Camera assente|FFmpeg mancante")
        await self.wait_closed()

    async def test_missing_microphone_releases_prepared_video(self):
        async def missing_audio():
            raise RuntimeError("PhoneCam Microphone non disponibile.")

        self.receiver.audio_factory = missing_audio
        ident = await self.pair()
        await self.confirm(ident)
        payload = await self.sender_offer(self.peer(), microphone=True)
        response = await self.action(ident, "offer", payload=payload)
        self.assertEqual(response.status, 503)
        self.assertIn("Microphone", (await response.json())["error"])
        await self.wait_closed()
        self.assertTrue(self.video.closed)

    def peer(self):
        peer = RTCPeerConnection(RTCConfiguration(iceServers=[], bundlePolicy=RTCBundlePolicy.MAX_BUNDLE))
        self.addAsyncCleanup(peer.close)
        return peer

    @staticmethod
    async def sender_offer(peer, microphone):
        peer.addTransceiver(VideoStreamTrack(), direction="sendonly")
        if microphone:
            peer.addTransceiver(AudioStreamTrack(), direction="sendonly")
        await peer.setLocalDescription(await peer.createOffer())
        return json.dumps({
            "sdp": peer.localDescription.sdp, "width": 640, "height": 480,
            "microphone": microphone,
        })

    async def negotiate(self):
        # Aioice gathers candidates on network interfaces and omits loopback.
        command = await asyncio.create_subprocess_exec(
            "hostname", "-I", stdout=asyncio.subprocess.PIPE,
        )
        output, _ = await command.communicate()
        addresses = output.decode().split()
        address = next((a for a in addresses if local_address(a) and not a.startswith("127.")), None)
        if not address:
            self.skipTest("Nessuna interfaccia IPv4 privata per il test ICE")
        await self.client.close()
        client = await self.client_for(address)
        ident = await self.pair(client)
        peer = self.peer()
        await self.confirm(ident, client)
        payload = await self.sender_offer(peer, microphone=True)
        response = await self.action(ident, "offer", client=client, payload=payload)
        self.assertEqual(response.status, 200, await response.text())
        answer = await response.json()
        self.assertEqual(answer["proof"], proof(self.key, ident, "answer", answer["sdp"]))
        await peer.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type="answer"))
        async with asyncio.timeout(15):
            await asyncio.gather(self.video.received.wait(), self.audio.received.wait())
        self.assertEqual(self.receiver.session.stage, "connected")
        return client, ident, peer, self.receiver.session.peer

    async def test_webrtc_audio_video_and_authenticated_disconnect(self):
        client, ident, _peer, receiver_peer = await self.negotiate()
        self.assertEqual((await self.action(ident, "close", client=client)).status, 200)
        await self.wait_closed()
        self.assertTrue(self.video.closed and self.audio.closed)
        self.assertEqual(receiver_peer.connectionState, "closed")

    async def test_peer_disconnect_releases_resources(self):
        _client, _ident, peer, receiver_peer = await self.negotiate()
        await peer.close()
        await self.wait_closed()
        self.assertEqual(receiver_peer.connectionState, "closed")
        self.assertTrue(self.video.closed and self.audio.closed)

    async def test_server_shutdown_closes_active_media(self):
        client, _ident, _peer, receiver_peer = await self.negotiate()
        await client.close()
        self.assertTrue(self.mic.closed)
        self.assertEqual(receiver_peer.connectionState, "closed")
        self.assertTrue(self.video.closed and self.audio.closed)
        self.assertIsNone(self.receiver.session)


class ValidationTests(unittest.TestCase):
    def test_camera_lookup_reports_missing_binary_and_device(self):
        with patch("server.video.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "FFmpeg mancante"):
                camera_device()
        with tempfile.TemporaryDirectory() as dirname, patch("server.video.shutil.which", return_value="/bin/ffmpeg"):
            with self.assertRaisesRegex(RuntimeError, "Camera assente"):
                camera_device(Path(dirname))

    def test_ipv4_and_page_origins(self):
        for value in ("127.0.0.1", "10.2.3.4", "172.31.1.2", "192.168.1.2"):
            self.assertTrue(local_address(value))
        for value in ("8.8.8.8", "172.32.1.2", "100.64.0.1", "::1"):
            self.assertFalse(local_address(value))
        self.assertTrue(page_origin("https://account.github.io"))
        self.assertTrue(page_origin("http://localhost:8000"))
        for origin in ("http://account.github.io", "https://account.github.io/phonecam",
                       "https://user@account.github.io", "https://account.github.io:443"):
            self.assertFalse(page_origin(origin))

    def test_ice_advertises_only_local_udp_candidates(self):
        sdp = ("a=candidate:one 1 udp 1 192.168.1.12 1234 typ host\r\n"
               "a=candidate:two 1 udp 1 8.8.8.8 1234 typ srflx\r\n"
               "a=candidate:three 1 tcp 1 192.168.1.12 1234 typ host tcptype passive\r\n"
               "a=candidate:four 1 udp 1 test-device.local 1234 typ host\r\n")
        filtered = filtered_sdp(sdp, "192.168.1.12")
        self.assertIn("candidate:one", filtered)
        for label in ("two", "three", "four"):
            self.assertNotIn(f"candidate:{label}", filtered)
        self.assertIn("candidate:four", filtered_sdp(sdp, "192.168.1.12", allow_mdns=True))
