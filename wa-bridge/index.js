/**
 * WhatsApp Web Bridge – connects your personal WhatsApp to the orchestrator.
 *
 * No Meta Business API needed. Uses Baileys (WhatsApp Web multi-device protocol).
 *
 * Flow:
 *   1. Scan QR code once → session persists across restarts
 *   2. User sends WhatsApp message → bridge forwards to orchestrator HTTP API
 *   3. Orchestrator processes → sends response back via bridge HTTP endpoint
 *   4. Bridge sends WhatsApp reply to user
 *
 * Environment:
 *   ORCHESTRATOR_URL  - FastAPI server URL (default: http://localhost:8000)
 *   WA_BRIDGE_PORT    - HTTP port for this bridge (default: 3001)
 *   WA_ALLOWED_NUMBERS - Comma-separated allowed numbers (e.g. "34692842705")
 *   WA_BOT_NAME       - Bot display name in logs (default: "OrchestratorBot")
 */

import {
  default as makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
} from "@whiskeysockets/baileys";
import express from "express";
import pino from "pino";
import qrcode from "qrcode-terminal";
import { writeFileSync, existsSync, mkdirSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

// ── Config ──────────────────────────────────────────────────────────────────

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL || "http://localhost:8000";
const BRIDGE_PORT = parseInt(process.env.WA_BRIDGE_PORT || "3001");
const ALLOWED_NUMBERS = (process.env.WA_ALLOWED_NUMBERS || "")
  .split(",")
  .map((n) => n.trim())
  .filter(Boolean);
const BOT_NAME = process.env.WA_BOT_NAME || "OrchestratorBot";
const AUTH_DIR = join(__dirname, "auth_state");

// ── Logger ──────────────────────────────────────────────────────────────────

const logger = pino({ level: "info" });

// ── State ───────────────────────────────────────────────────────────────────

let sock = null;
let isConnected = false;
let qrGenerated = false;

// ── Helpers ─────────────────────────────────────────────────────────────────

function jidToNumber(jid) {
  // "34692842705@s.whatsapp.net" → "34692842705"
  return (jid || "").split("@")[0];
}

function numberToJid(number) {
  // "34692842705" → "34692842705@s.whatsapp.net"
  const clean = number.replace(/[^0-9]/g, "");
  return `${clean}@s.whatsapp.net`;
}

function isAllowed(jid) {
  if (ALLOWED_NUMBERS.length === 0) return true; // No whitelist = allow all
  const number = jidToNumber(jid);
  return ALLOWED_NUMBERS.includes(number);
}

// ── Forward message to orchestrator ─────────────────────────────────────────

async function forwardToOrchestrator(sender, text, mediaType, mediaData) {
  const url = `${ORCHESTRATOR_URL}/wa-bridge/incoming`;
  const body = {
    sender: jidToNumber(sender),
    text: text || "",
    media_type: mediaType || null,
    media_base64: mediaData || null,
    timestamp: Date.now(),
  };

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      logger.error(`Orchestrator returned ${res.status}: ${await res.text()}`);
    }
  } catch (err) {
    logger.error(`Failed to forward to orchestrator: ${err.message}`);
  }
}

// ── Connect to WhatsApp Web ─────────────────────────────────────────────────

async function connectWhatsApp() {
  if (!existsSync(AUTH_DIR)) mkdirSync(AUTH_DIR, { recursive: true });

  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger),
    },
    printQRInTerminal: false, // We handle QR ourselves
    logger: pino({ level: "silent" }), // Quiet baileys logs
    browser: [BOT_NAME, "Chrome", "1.0.0"],
    generateHighQualityLinkPreview: false,
    syncFullHistory: false,
  });

  // ── QR code ─────────────────────────────────────────────────────────────

  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      qrGenerated = true;
      console.log("\n╔══════════════════════════════════════════════╗");
      console.log("║  Escanea este QR con WhatsApp en tu movil:  ║");
      console.log("║  WhatsApp → Dispositivos vinculados → +     ║");
      console.log("╚══════════════════════════════════════════════╝\n");
      qrcode.generate(qr, { small: true });
      console.log("\nEsperando escaneo...\n");
    }

    if (connection === "open") {
      isConnected = true;
      qrGenerated = false;
      const me = sock.user;
      logger.info(
        `WhatsApp conectado: ${me?.name || "?"} (${jidToNumber(me?.id || "")})`
      );
      console.log(`\n✅ WhatsApp conectado como: ${me?.name || "?"}`);
      console.log(`   Numero: +${jidToNumber(me?.id || "")}`);
      if (ALLOWED_NUMBERS.length > 0) {
        console.log(
          `   Numeros permitidos: ${ALLOWED_NUMBERS.map((n) => "+" + n).join(", ")}`
        );
      } else {
        console.log(`   Todos los numeros permitidos (sin whitelist)`);
      }
      console.log(`   Bridge HTTP: http://localhost:${BRIDGE_PORT}\n`);
    }

    if (connection === "close") {
      isConnected = false;
      const code = lastDisconnect?.error?.output?.statusCode;
      const reason = DisconnectReason;

      if (code === reason.loggedOut) {
        logger.warn("WhatsApp session logged out. Delete auth_state and restart.");
        console.log("\n❌ Sesion cerrada. Borra wa-bridge/auth_state/ y reinicia.\n");
      } else {
        logger.info(`Disconnected (code ${code}), reconnecting in 5s...`);
        setTimeout(connectWhatsApp, 5000);
      }
    }
  });

  // ── Save credentials on update ──────────────────────────────────────────

  sock.ev.on("creds.update", saveCreds);

  // ── Incoming messages ───────────────────────────────────────────────────

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;

    for (const msg of messages) {
      // Skip own messages and status broadcasts
      if (msg.key.fromMe) continue;
      if (msg.key.remoteJid === "status@broadcast") continue;

      const sender = msg.key.remoteJid;
      if (!sender || sender.includes("@g.us")) continue; // Skip group messages

      // Check whitelist
      if (!isAllowed(sender)) {
        logger.warn(`Blocked message from unauthorized: ${jidToNumber(sender)}`);
        continue;
      }

      const content = msg.message;
      if (!content) continue;

      // ── Text message ──────────────────────────────────────────────────
      const text =
        content.conversation ||
        content.extendedTextMessage?.text ||
        "";

      // ── Voice / audio ─────────────────────────────────────────────────
      const audioMsg = content.audioMessage;
      if (audioMsg) {
        try {
          const stream = await sock.downloadMediaMessage(msg);
          const chunks = [];
          for await (const chunk of stream) chunks.push(chunk);
          const buffer = Buffer.concat(chunks);
          const base64 = buffer.toString("base64");
          const mime = audioMsg.mimetype || "audio/ogg";

          logger.info(
            `Audio from ${jidToNumber(sender)}: ${buffer.length} bytes (${mime})`
          );
          await forwardToOrchestrator(sender, "", "audio", JSON.stringify({ data: base64, mime }));
        } catch (err) {
          logger.error(`Failed to download audio: ${err.message}`);
          await sendText(sender, "No pude procesar el audio. Intenta de nuevo.");
        }
        continue;
      }

      // ── Image ─────────────────────────────────────────────────────────
      const imageMsg = content.imageMessage;
      if (imageMsg) {
        try {
          const stream = await sock.downloadMediaMessage(msg);
          const chunks = [];
          for await (const chunk of stream) chunks.push(chunk);
          const buffer = Buffer.concat(chunks);
          const base64 = buffer.toString("base64");
          const mime = imageMsg.mimetype || "image/jpeg";
          const caption = imageMsg.caption || "";

          logger.info(
            `Image from ${jidToNumber(sender)}: ${buffer.length} bytes`
          );
          await forwardToOrchestrator(
            sender,
            caption,
            "image",
            JSON.stringify({ data: base64, mime })
          );
        } catch (err) {
          logger.error(`Failed to download image: ${err.message}`);
        }
        continue;
      }

      // ── Video ─────────────────────────────────────────────────────────
      const videoMsg = content.videoMessage;
      if (videoMsg) {
        const size = videoMsg.fileLength || 0;
        if (size > 15 * 1024 * 1024) {
          await sendText(sender, "Video demasiado grande (max ~15MB).");
          continue;
        }
        try {
          const stream = await sock.downloadMediaMessage(msg);
          const chunks = [];
          for await (const chunk of stream) chunks.push(chunk);
          const buffer = Buffer.concat(chunks);
          const base64 = buffer.toString("base64");
          const mime = videoMsg.mimetype || "video/mp4";
          const caption = videoMsg.caption || "";

          logger.info(
            `Video from ${jidToNumber(sender)}: ${buffer.length} bytes`
          );
          await forwardToOrchestrator(
            sender,
            caption,
            "video",
            JSON.stringify({ data: base64, mime })
          );
        } catch (err) {
          logger.error(`Failed to download video: ${err.message}`);
        }
        continue;
      }

      // ── Document ──────────────────────────────────────────────────────
      const docMsg = content.documentMessage;
      if (docMsg) {
        const mime = docMsg.mimetype || "";
        if (
          mime.startsWith("audio/") ||
          mime.startsWith("image/") ||
          mime.startsWith("video/")
        ) {
          try {
            const stream = await sock.downloadMediaMessage(msg);
            const chunks = [];
            for await (const chunk of stream) chunks.push(chunk);
            const buffer = Buffer.concat(chunks);
            const base64 = buffer.toString("base64");
            const mediaType = mime.split("/")[0]; // "audio", "image", "video"

            await forwardToOrchestrator(
              sender,
              docMsg.caption || docMsg.fileName || "",
              mediaType,
              JSON.stringify({ data: base64, mime })
            );
          } catch (err) {
            logger.error(`Failed to download document: ${err.message}`);
          }
        } else {
          await sendText(sender, `Archivo recibido (${mime}). Solo proceso audio, imagenes y videos.`);
        }
        continue;
      }

      // ── Forward text ──────────────────────────────────────────────────
      if (text) {
        logger.info(`Message from ${jidToNumber(sender)}: ${text.substring(0, 50)}...`);
        await forwardToOrchestrator(sender, text, null, null);
      }
    }
  });
}

// ── Send text message ───────────────────────────────────────────────────────

async function sendText(jid, text) {
  if (!sock || !isConnected) {
    logger.error("Cannot send: not connected");
    return false;
  }
  try {
    await sock.sendMessage(jid, { text });
    return true;
  } catch (err) {
    logger.error(`Failed to send to ${jidToNumber(jid)}: ${err.message}`);
    return false;
  }
}

// ── HTTP API (for orchestrator to send responses back) ──────────────────────

const app = express();
app.use(express.json({ limit: "50mb" }));

// Health check
app.get("/health", (req, res) => {
  res.json({
    status: isConnected ? "connected" : "disconnected",
    qr_pending: qrGenerated,
    bridge_port: BRIDGE_PORT,
  });
});

// Send text message endpoint (called by orchestrator)
app.post("/send", async (req, res) => {
  const { to, text } = req.body;
  if (!to || !text) {
    return res.status(400).json({ error: "Missing 'to' and/or 'text'" });
  }

  const jid = numberToJid(to);
  const ok = await sendText(jid, text);
  res.json({ success: ok });
});

// Send media message (image, audio, video)
app.post("/send-media", async (req, res) => {
  const { to, media_type, data_base64, mime, caption } = req.body;
  if (!to || !data_base64) {
    return res.status(400).json({ error: "Missing fields" });
  }

  const jid = numberToJid(to);
  if (!sock || !isConnected) {
    return res.json({ success: false, error: "Not connected" });
  }

  try {
    const buffer = Buffer.from(data_base64, "base64");
    let msgContent = {};

    if (media_type === "image") {
      msgContent = { image: buffer, caption: caption || "", mimetype: mime || "image/jpeg" };
    } else if (media_type === "video") {
      msgContent = { video: buffer, caption: caption || "", mimetype: mime || "video/mp4" };
    } else if (media_type === "audio") {
      msgContent = { audio: buffer, mimetype: mime || "audio/ogg; codecs=opus", ptt: true };
    } else if (media_type === "document") {
      msgContent = { document: buffer, mimetype: mime, fileName: caption || "file" };
    }

    await sock.sendMessage(jid, msgContent);
    res.json({ success: true });
  } catch (err) {
    logger.error(`Send media failed: ${err.message}`);
    res.json({ success: false, error: err.message });
  }
});

app.listen(BRIDGE_PORT, () => {
  logger.info(`WA Bridge HTTP API on port ${BRIDGE_PORT}`);
  console.log(`\n🌉 WA Bridge HTTP API: http://localhost:${BRIDGE_PORT}`);
});

// ── Start ───────────────────────────────────────────────────────────────────

console.log(`
╔══════════════════════════════════════════════╗
║     WhatsApp Web Bridge – Orchestrator       ║
╚══════════════════════════════════════════════╝
`);

connectWhatsApp().catch((err) => {
  logger.error(`Fatal: ${err.message}`);
  process.exit(1);
});
