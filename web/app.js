// SPDX-License-Identifier: GPL-3.0-or-later
"use strict";

const ui = Object.fromEntries([
  "connection", "settings", "address", "port", "facing", "resolution", "microphone",
  "preview", "live", "status", "connect", "pairing", "code", "confirm", "disconnect",
].map(id => [id, document.getElementById(id)]));
const utf8 = new TextEncoder();
let session = null;

function privateIP(value) {
  const parts = value.split(".");
  if (parts.length !== 4 || parts.some(part => !/^(0|[1-9]\d{0,2})$/.test(part) || Number(part) > 255)) {
    return false;
  }
  const [a, b] = parts.map(Number);
  return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
}

function show(message) {
  ui.status.textContent = message;
}

function sessionPath(current, action) {
  return `/session/${current.id}/${action}`;
}

async function post(current, path, body = {}, keepalive = false) {
  let response;
  try {
    response = await fetch(current.base + path, {
      method: "POST",
      mode: "cors",
      credentials: "omit",
      redirect: "error",
      cache: "no-store",
      targetAddressSpace: "local",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive,
      signal: keepalive ? undefined : AbortSignal.any([
        current.controller.signal, AbortSignal.timeout(20000),
      ]),
    });
  } catch (error) {
    if (current.controller.signal.aborted || keepalive) throw error;
    throw new Error("Ricevitore irraggiungibile. Verifica IP, porta, Wi-Fi e il permesso «Rete locale» in Chrome. Sul PC controlla anche l’origine configurata.");
  }
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.error || "Richiesta respinta dal ricevitore.");
    error.status = response.status;
    throw error;
  }
  return result;
}

function message(current, action, payload) {
  return utf8.encode(`${current.id}\n${action}\n${payload}`);
}

async function signed(current, action, payload = "") {
  const result = await crypto.subtle.sign("HMAC", current.key, message(current, action, payload));
  const proof = Array.from(new Uint8Array(result), byte => byte.toString(16).padStart(2, "0")).join("");
  return { proof, payload };
}

async function verify(current, action, payload, proof) {
  if (typeof proof !== "string" || !/^[0-9a-f]{64}$/.test(proof)) return false;
  const bytes = Uint8Array.from(proof.match(/../g), byte => parseInt(byte, 16));
  return crypto.subtle.verify("HMAC", current.key, bytes, message(current, action, payload));
}

function stop(current, messageText = "Disconnesso") {
  if (session !== current) return;
  session = null;
  clearTimeout(current.poll);
  clearTimeout(current.connectionTimer);
  current.controller.abort();
  current.peer?.close();
  current.stream?.getTracks().forEach(track => track.stop());
  if (current.closeBody) {
    post(current, sessionPath(current, "close"), current.closeBody, true).catch(() => {});
  }
  ui.preview.srcObject = null;
  ui.settings.disabled = false;
  ui.microphone.disabled = false;
  ui.connect.hidden = false;
  ui.disconnect.hidden = true;
  ui.pairing.hidden = true;
  ui.confirm.disabled = true;
  ui.code.value = "";
  ui.live.hidden = true;
  show(messageText);
}

async function checkApproval(current) {
  try {
    const status = await post(current, sessionPath(current, "status"));
    if (session !== current || current.confirming) return;
    if (status.approved) {
      ui.confirm.disabled = false;
      show("Autorizzato sul PC. Inserisci il codice e conferma.");
      return;
    }
    current.poll = setTimeout(() => checkApproval(current), 1500);
  } catch (error) {
    stop(current, error.message);
  }
}

ui.connection.addEventListener("submit", async event => {
  event.preventDefault();
  if (session) return;
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    show("Apri la pagina PhoneCam via HTTPS in Chrome per accedere a fotocamera e microfono.");
    return;
  }
  const address = ui.address.value.trim();
  if (!privateIP(address)) {
    show("Inserisci l’indirizzo IPv4 privato del PC, per esempio 192.168.1.20.");
    return;
  }
  const [width, height] = ui.resolution.value.split("x").map(Number);
  const current = {
    base: `http://${address}:${ui.port.valueAsNumber}`,
    controller: new AbortController(),
    width, height, microphone: ui.microphone.checked, facing: ui.facing.value,
  };
  session = current;
  ui.settings.disabled = true;
  ui.microphone.disabled = true;
  ui.connect.hidden = true;
  ui.disconnect.hidden = false;
  show("Contatto il ricevitore. Se Chrome lo chiede, consenti l’accesso alla rete locale.");
  try {
    const { id } = await post(current, "/pair");
    if (session !== current) return;
    if (typeof id !== "string" || !/^[\w-]{24}$/.test(id)) {
      throw new Error("Risposta del ricevitore non valida.");
    }
    current.id = id;
    ui.pairing.hidden = false;
    show("Leggi il codice nella finestra sul PC e premi Autorizza.");
    await checkApproval(current);
  } catch (error) {
    stop(current, error.message);
  }
});

async function waitForIce(peer, signal) {
  if (peer.iceGatheringState === "complete") return;
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => finish(new Error("Ricerca del collegamento locale scaduta.")), 8000);
    const onAbort = () => finish(signal.reason);
    const onChange = () => {
      if (peer.iceGatheringState === "complete") finish();
    };
    function finish(error) {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      peer.removeEventListener("icegatheringstatechange", onChange);
      if (error) reject(error);
      else resolve();
    }
    signal.addEventListener("abort", onAbort, { once: true });
    peer.addEventListener("icegatheringstatechange", onChange);
    if (signal.aborted) onAbort();
    else onChange();
  });
}

async function startMedia(current) {
  show("Consenti a Chrome l’uso dei dispositivi selezionati.");
  const stream = await navigator.mediaDevices.getUserMedia({
    video: {
      facingMode: { exact: current.facing },
      width: { ideal: current.width, max: current.width },
      height: { ideal: current.height, max: current.height },
      frameRate: { ideal: 30, max: 30 },
    },
    audio: current.microphone,
  });
  if (session !== current) {
    stream.getTracks().forEach(track => track.stop());
    return;
  }
  current.stream = stream;
  ui.preview.srcObject = stream;
  show("Collegamento WebRTC in corso…");

  const peer = new RTCPeerConnection({ iceServers: [], bundlePolicy: "max-bundle" });
  current.peer = peer;
  for (const track of stream.getTracks()) {
    peer.addTransceiver(track, { direction: "sendonly", streams: [stream] });
    track.addEventListener("ended", () => stop(current, "La fotocamera o il microfono si è arrestato."));
  }
  peer.addEventListener("connectionstatechange", () => {
    if (session !== current) return;
    if (peer.connectionState === "connected") {
      clearTimeout(current.connectionTimer);
      ui.live.hidden = false;
      ui.microphone.disabled = !current.microphone;
      show("Trasmissione attiva");
    } else if (["failed", "closed"].includes(peer.connectionState)) {
      stop(current, "Collegamento WebRTC terminato. Premi Connetti per riprovare.");
    } else if (peer.connectionState === "disconnected") {
      ui.live.hidden = true;
      show("Rete interrotta. Attendo il ripristino…");
      clearTimeout(current.connectionTimer);
      current.connectionTimer = setTimeout(() => stop(current, "Collegamento Wi-Fi perso."), 10000);
    }
  });
  await peer.setLocalDescription(await peer.createOffer());
  await waitForIce(peer, current.controller.signal);
  const payload = JSON.stringify({
    sdp: peer.localDescription.sdp, width: current.width, height: current.height,
    microphone: current.microphone,
  });
  const answer = await post(current, sessionPath(current, "offer"), await signed(current, "offer", payload));
  if (!await verify(current, "answer", answer.sdp, answer.proof)) {
    throw new Error("Risposta WebRTC non autenticata. Riconnetti dal telefono.");
  }
  if (session !== current) return;
  current.connectionTimer = setTimeout(
    () => stop(current, "Collegamento WebRTC scaduto. Controlla Wi-Fi e firewall sul PC."), 25000,
  );
  await peer.setRemoteDescription({ type: "answer", sdp: answer.sdp });
}

ui.pairing.addEventListener("submit", async event => {
  event.preventDefault();
  const current = session;
  if (!current || current.confirming || ui.confirm.disabled) return;
  const code = ui.code.value.replace(/[\s-]/g, "").toUpperCase();
  if (!/^[A-Z2-7]{20}$/.test(code)) {
    show("Il codice deve contenere i 20 caratteri mostrati sul PC.");
    return;
  }
  current.confirming = true;
  ui.confirm.disabled = true;
  try {
    current.key = await crypto.subtle.importKey(
      "raw", utf8.encode(code), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"],
    );
    const confirmation = await post(current, sessionPath(current, "confirm"), await signed(current, "confirm"));
    if (!await verify(current, "confirmed", "", confirmation.proof)) {
      throw new Error("Identità del ricevitore non verificata.");
    }
    current.closeBody = await signed(current, "close");
    if (session !== current) {
      post(current, sessionPath(current, "close"), current.closeBody, true).catch(() => {});
      return;
    }
    ui.code.value = "";
    ui.pairing.hidden = true;
    await startMedia(current);
  } catch (error) {
    if (session !== current) return;
    if (error.status === 403 && error.message === "Codice di associazione errato.") {
      current.confirming = false;
      ui.confirm.disabled = false;
      show(error.message);
      return;
    }
    const errors = {
      NotAllowedError: "Permesso per fotocamera o microfono negato. Controlla le impostazioni di Chrome.",
      NotFoundError: "Fotocamera o microfono selezionato assente.",
      OverconstrainedError: "Fotocamera selezionata non disponibile. Prova l’altra fotocamera.",
      NotReadableError: "Il dispositivo è occupato o inaccessibile. Chiudi le altre app che lo usano.",
    };
    stop(current, errors[error.name] || error.message);
  }
});

ui.microphone.addEventListener("change", () => {
  for (const track of session?.stream?.getAudioTracks() ?? []) track.enabled = ui.microphone.checked;
});
ui.disconnect.addEventListener("click", () => { if (session) stop(session); });
window.addEventListener("pagehide", () => { if (session) stop(session); });
