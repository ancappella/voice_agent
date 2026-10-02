# Voice Agent · 实时语音对话方案

基于 [Pipecat](https://github.com/pipecat-ai/pipecat) 的低延迟语音 Agent：麦克风进、扬声器出，支持 **barge-in 打断**，语音识别与合成默认跑在本地，保护隐私。

目标体验：**停说 → 开说** 端到端延迟尽量压到 **800ms 以内**（与网络/模型体积相关，见下文实测）。

---

## 方案概览

```text
浏览器 Mic (WebRTC)
        │
        ▼
┌───────────────────┐
│  Silero VAD       │  检测开说 / 停说，触发 barge-in
└─────────┬─────────┘
          ▼
┌───────────────────┐
│  Whisper (本地)   │  STT：音频不出本机（MLX / Faster-Whisper）
└─────────┬─────────┘
          ▼
┌───────────────────┐
│  Gemini Flash     │  LLM：意图理解 + 短口语回复
└─────────┬─────────┘
          ▼
┌───────────────────┐
│  Piper (本地)     │  TTS：神经合成，低延迟首包
└─────────┬─────────┘
          ▼
浏览器 Speaker
```

| 环节 | 选型 | 作用 |
|------|------|------|
| 传输 | Small WebRTC | 浏览器 ↔ 本机管道，无需 Daily 等第三方房间 |
| 端点检测 | Silero VAD | 判用户开/停说；开说即广播打断帧 |
| 语音识别 | Whisper 本地 | 隐私优先；Apple Silicon 用 MLX `tiny` |
| 意图 / 对话 | Google Gemini | 短回复、口语化，不做长文案 |
| 语音合成 | Piper 本地 | 中文默认 `zh_CN-huayan-medium` |
| 打断 | VAD barge-in | 机器人播报中用户插话 → 立刻停 LLM/TTS/播放队列 |
| 可观测 | Latency Observer | 监控 user→bot 延迟，对照 800ms 预算告警 |

---

## 设计取舍

### 为什么 STT / TTS 本地、LLM 云端？

- **STT / TTS 本地**：原始音频与合成音色留在本机，满足隐私与内网场景；Piper / Whisper tiny 首包通常几十到几百毫秒级。
- **LLM 云端（Gemini Flash）**：意图理解与对话质量更好落地；代价是网络 RTT，往往成为 800ms 预算里的主要变量。

若 LLM 也要本地（如 Ollama），可替换 `GoogleLLMService`，但需重新压测延迟预算。

### 为什么用 Pipecat？

Pipecat 把 VAD → STT → LLM → TTS → Transport 串成帧管道，自带：

- **Interruptions（barge-in）**：用户开说时取消在途 LLM/TTS，并排空未播放音频
- **RTVI + WebRTC**：浏览器开麦、收听、展示转写的标准路径
- **Metrics / Latency Observer**：便于对照延迟预算做回归

### 延迟怎么拆？

典型热身后构成（本机实测量级，随网络波动）：

| 段 | 量级 | 说明 |
|----|------|------|
| VAD + 停说策略 | ~300–500ms | `stop_secs` + speech timeout |
| Whisper tiny (MLX) | ~200–350ms | 分段 STT，VAD 判停后再转写 |
| Gemini Flash | ~500–900ms | 走公网；关 thinking 更稳 |
| Piper TTFB | ~50–80ms | 本地合成首包 |

**本地链路够快，云端 LLM 常是超预算主因。** 优化方向：更近的 LLM、更短 `max_tokens`、流式抢首句、收紧 endpointing、或换本地小模型。

---

## 快速开始

### 环境

- Python **≥ 3.11**（推荐 3.12：`uv python install 3.12`）
- macOS Apple Silicon 推荐（MLX Whisper）
- `GOOGLE_API_KEY` 或 `GEMINI_API_KEY`

### 安装与运行

```bash
git clone https://github.com/ancappella/voice_agent.git
cd voice_agent

# 建议 Python 3.12
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env，填入 GOOGLE_API_KEY

./run.sh
# 或: python server.py
```

浏览器打开：**http://localhost:7860/**

1. 点击麦克风授权并开麦  
2. 直接说话（停顿后进入识别 → 回复 → 播报）  
3. 机器人说话时插话即可打断  

官方预置客户端仍在 `/client/`。

---

## 配置

| 变量 | 默认 | 说明 |
|------|------|------|
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | — | Gemini 密钥 |
| `WHISPER_MODEL` | `tiny` | 映射为 `mlx-community/whisper-tiny` 等 |
| `PIPER_VOICE` | `zh_CN-huayan-medium` | Piper 音色 id |
| `LATENCY_BUDGET_MS` | `800` | 监控阈值（不截断回复） |
| `GEMINI_MODEL` | `gemini-2.5-flash` | 建议 Flash，勿开 thinking |

国内访问 Gemini / HuggingFace 时可设代理，例如：

```bash
export HTTPS_PROXY=http://127.0.0.1:7897
export HTTP_PROXY=http://127.0.0.1:7897
```

---

## 仓库结构

```text
.
├── server.py           # FastAPI：收音页 + WebRTC /api/offer
├── bot.py              # Pipecat 管道（VAD/STT/LLM/TTS/打断）
├── latency.py          # 800ms 延迟观察与告警
├── static/             # 浏览器收音 UI
├── bench_latency.py    # 组件级延迟探针
├── requirements.txt
├── .env.example
└── run.sh
```

---

## 关键说明

- Whisper 在此方案中是 **分段 STT**（VAD 判停后整段转写），不是流式 interim。
- Piper 进程内推理依赖 `piper-tts`（GPL）；若要隔离许可，可改用 HTTP 版 `PiperHttpTTSService`。
- 首次运行会下载 Whisper / Piper 模型，请预留磁盘与网络时间。
- 不要提交 `.env` 与本地 `models/` 权重。

---

## License

学习 / 演示用途。第三方组件（Pipecat、Whisper、Piper、Gemini 等）遵循各自许可证。
