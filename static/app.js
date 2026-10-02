/**
 * Chapter07 voice UI — Pipecat SmallWebRTC client
 * Connects to local /api/offer (same process as bot).
 */
import { PipecatClient, RTVIEvent } from "https://esm.sh/@pipecat-ai/client-js@1.13.1";
import { SmallWebRTCTransport } from "https://esm.sh/@pipecat-ai/small-webrtc-transport@1.10.8?deps=@pipecat-ai/client-js@1.13.1";

const $ = (id) => document.getElementById(id);

const btnMic = $("btnMic");
const micLabel = $("micLabel");
const meterBar = $("meterBar");
const chat = $("chat");
const dot = $("dot");
const statusText = $("statusText");
const latencyText = $("latencyText");
const botAudio = $("botAudio");

let client = null;
let connected = false;
let connecting = false;
let userBubble = null;
let botBubble = null;
let botText = "";
let speakStartedAt = 0;
let meterTimer = null;
let analyser = null;
let meterStream = null;

function setStatus(kind, text) {
  statusText.textContent = text;
  dot.className = "dot" + (kind ? ` ${kind}` : "");
}

function appendBubble(role, text, partial = false) {
  const el = document.createElement("div");
  el.className = `bubble ${role}` + (partial ? " partial" : "");
  el.innerHTML = `<span class="who">${role === "user" ? "你" : "助手"}</span>`;
  const body = document.createElement("span");
  body.className = "body";
  body.textContent = text;
  el.appendChild(body);
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
  return el;
}

function updateBubble(el, text, partial) {
  if (!el) return;
  el.classList.toggle("partial", !!partial);
  const body = el.querySelector(".body");
  if (body) body.textContent = text;
  chat.scrollTop = chat.scrollHeight;
}

/** Merge bot text without duplicating sentences / chunks. */
function pushBotText(chunk) {
  const text = (chunk || "").trim();
  if (!text) return;

  if (!botText) {
    botText = text;
  } else if (text === botText || botText.endsWith(text)) {
    return;
  } else if (text.startsWith(botText)) {
    // cumulative snapshot from server
    botText = text;
  } else if (botText.includes(text)) {
    return;
  } else {
    botText += text;
  }

  if (!botBubble) botBubble = appendBubble("bot", botText, false);
  else updateBubble(botBubble, botText, false);
}

async function startMeter() {
  stopMeter();
  try {
    meterStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
    const ctx = new AudioContext();
    const src = ctx.createMediaStreamSource(meterStream);
    analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    src.connect(analyser);
    const data = new Uint8Array(analyser.frequencyBinCount);
    meterTimer = setInterval(() => {
      analyser.getByteFrequencyData(data);
      let sum = 0;
      for (const v of data) sum += v;
      const avg = sum / data.length / 255;
      meterBar.style.width = `${Math.min(100, Math.round(avg * 180))}%`;
    }, 50);
  } catch {
    /* mic meter is best-effort; Pipecat also asks for mic */
  }
}

function stopMeter() {
  if (meterTimer) clearInterval(meterTimer);
  meterTimer = null;
  analyser = null;
  if (meterStream) {
    meterStream.getTracks().forEach((t) => t.stop());
    meterStream = null;
  }
  meterBar.style.width = "0%";
}

function createClient() {
  const c = new PipecatClient({
    transport: new SmallWebRTCTransport({
      iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
    }),
    enableMic: true,
    enableCam: false,
    callbacks: {
      onTransportStateChanged: (state) => {
        if (state === "connecting" || state === "authenticating") {
          setStatus("warn", "连接中…");
        } else if (state === "ready" || state === "connected") {
          setStatus("on", "已连接 · 可以说话");
        } else if (state === "disconnected") {
          setStatus("", "已断开");
        }
      },
      onConnected: () => {
        connected = true;
        connecting = false;
        btnMic.classList.add("active");
        btnMic.setAttribute("aria-pressed", "true");
        micLabel.textContent = "点击关麦";
        setStatus("on", "已连接 · 可以说话");
      },
      onDisconnected: () => {
        connected = false;
        connecting = false;
        btnMic.classList.remove("active", "speaking");
        btnMic.setAttribute("aria-pressed", "false");
        micLabel.textContent = "点击开麦";
        stopMeter();
        setStatus("", "未连接");
      },
      onBotReady: () => setStatus("on", "机器人就绪"),
      onUserStartedSpeaking: () => {
        speakStartedAt = performance.now();
        btnMic.classList.add("speaking");
        userBubble = appendBubble("user", "正在听…", true);
        botBubble = null;
        botText = "";
      },
      onUserStoppedSpeaking: () => {
        btnMic.classList.remove("speaking");
        if (userBubble) {
          const body = userBubble.querySelector(".body");
          if (body && (body.textContent === "正在听…" || body.textContent === "…")) {
            updateBubble(userBubble, "识别中…", true);
            const pending = userBubble;
            setTimeout(() => {
              if (pending && pending.classList.contains("partial")) {
                const b = pending.querySelector(".body");
                if (b && (b.textContent === "识别中…" || b.textContent === "正在听…")) {
                  updateBubble(pending, "（未识别到文字）", false);
                }
              }
            }, 8000);
          }
        }
      },
      onBotStartedSpeaking: () => {
        if (speakStartedAt) {
          const ms = Math.round(performance.now() - speakStartedAt);
          latencyText.textContent =
            ms <= 800 ? `延迟 ${ms}ms ✓` : `延迟 ${ms}ms（超 800ms）`;
          speakStartedAt = 0;
        }
        btnMic.classList.add("speaking");
      },
      onBotStoppedSpeaking: () => {
        btnMic.classList.remove("speaking");
      },
      onUserTranscript: (t) => {
        const text = (t?.text || "").trim();
        if (!text) return;
        if (!userBubble) userBubble = appendBubble("user", text, !t.final);
        else updateBubble(userBubble, text, !t.final);
        if (t.final) userBubble = null;
      },
      onBotTranscript: (t) => pushBotText(t?.text),
      // BotOutput 与 BotTranscript 常会重复同一句，统一走去重合并
      onBotOutput: (data) => {
        if (data?.text) pushBotText(data.text);
      },
      onError: (err) => {
        console.error(err);
        const msg = err?.message || err?.data?.error || "出错了";
        setStatus("err", typeof msg === "string" ? msg : "出错了");
        if (userBubble?.classList.contains("partial")) {
          updateBubble(userBubble, `（识别失败）`, false);
          userBubble = null;
        }
      },
    },
  });

  c.on(RTVIEvent.TrackStarted, (track, participant) => {
    if (!participant?.local && track.kind === "audio") {
      botAudio.srcObject = new MediaStream([track]);
    }
  });

  return c;
}

async function connect() {
  if (connecting || connected) return;
  connecting = true;
  setStatus("warn", "请求麦克风…");
  micLabel.textContent = "连接中…";
  try {
    client = createClient();
    await client.initDevices();
    await startMeter();
    await client.connect({
      webrtcRequestParams: {
        endpoint: `${location.origin}/api/offer`,
      },
    });
  } catch (e) {
    console.error(e);
    connecting = false;
    stopMeter();
    setStatus("err", e?.message || "连接失败");
    micLabel.textContent = "点击重试";
    try {
      await client?.disconnect();
    } catch {
      /* ignore */
    }
    client = null;
  }
}

async function disconnect() {
  stopMeter();
  try {
    await client?.disconnect();
  } catch {
    /* ignore */
  }
  client = null;
  connected = false;
  connecting = false;
  btnMic.classList.remove("active", "speaking");
  btnMic.setAttribute("aria-pressed", "false");
  micLabel.textContent = "点击开麦";
  setStatus("", "未连接");
  latencyText.textContent = "延迟 —";
}

btnMic.addEventListener("click", () => {
  if (connected) disconnect();
  else connect();
});

setStatus("", "未连接");
