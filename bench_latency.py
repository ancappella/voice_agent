"""Offline latency probe: Whisper STT + Gemini + Piper TTS (no WebRTC)."""

from __future__ import annotations

import asyncio
import os
import platform
import time
import wave
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from loguru import logger

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=True)
os.environ.setdefault("NLTK_ALLOW_PROXIED_URLOPEN", "1")

BUDGET_MS = float(os.getenv("LATENCY_BUDGET_MS", "800"))


def _api_key() -> str:
    key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("缺少 GOOGLE_API_KEY / GEMINI_API_KEY")
    return key


def _make_tone_wav(path: Path, seconds: float = 1.2, sr: int = 16000) -> Path:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    audio = (0.2 * np.sin(2 * np.pi * 220 * t) + 0.1 * np.sin(2 * np.pi * 440 * t)).astype(
        np.float32
    )
    pcm = (audio * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


async def _drain_stt(stt, pcm: bytes) -> tuple[float, str]:
    from pipecat.frames.frames import TranscriptionFrame

    t0 = time.perf_counter()
    text = ""
    async for frame in stt.run_stt(pcm):
        if isinstance(frame, TranscriptionFrame):
            text += frame.text or ""
    return (time.perf_counter() - t0) * 1000, text


async def bench_stt(audio_path: Path) -> float:
    from pipecat.transcriptions.language import Language

    use_mlx = platform.system() == "Darwin" and platform.machine() == "arm64"
    model = os.getenv("WHISPER_MODEL", "tiny")

    if use_mlx:
        from pipecat.services.whisper.stt import WhisperSTTServiceMLX

        stt = WhisperSTTServiceMLX(
            settings=WhisperSTTServiceMLX.Settings(model=model, language=Language.ZH)
        )
    else:
        from pipecat.services.whisper.stt import WhisperSTTService

        stt = WhisperSTTService(
            device="auto",
            compute_type="int8",
            settings=WhisperSTTService.Settings(model=model, language=Language.ZH),
        )

    with wave.open(str(audio_path), "rb") as w:
        pcm = w.readframes(w.getnframes())

    warm_ms, _ = await _drain_stt(stt, pcm)
    ms, text = await _drain_stt(stt, pcm)
    logger.info(f"STT warm={warm_ms:.0f}ms run={ms:.0f}ms text={text!r}")
    return ms


async def bench_llm() -> float:
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.services.google.llm import GoogleLLMService

    llm = GoogleLLMService(
        api_key=_api_key(),
        settings=GoogleLLMService.Settings(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            system_instruction="用一句话口语回复，不超过20字。",
            temperature=0.3,
            max_tokens=64,
        ),
    )
    ctx = LLMContext()
    ctx.add_message({"role": "user", "content": "明天天气怎么样？用一句话回答。"})

    t0 = time.perf_counter()
    text = await llm.run_inference(ctx)
    ms = (time.perf_counter() - t0) * 1000
    logger.info(f"LLM run_inference={ms:.0f}ms text={text!r}")
    return ms


async def bench_tts() -> float:
    from pipecat.services.piper.tts import PiperTTSService

    voice = os.getenv("PIPER_VOICE", "zh_CN-huayan-medium")
    download_dir = ROOT / "models" / "piper"
    tts = PiperTTSService(
        download_dir=download_dir,
        settings=PiperTTSService.Settings(voice=voice),
    )
    text = "你好，我是语音助手。"

    async for _ in tts.run_tts(text, context_id="warm"):
        pass

    t0 = time.perf_counter()
    first = None
    chunks = 0
    async for _ in tts.run_tts(text, context_id="bench"):
        if first is None:
            first = time.perf_counter()
        chunks += 1
    ttfb = ((first or time.perf_counter()) - t0) * 1000
    total = (time.perf_counter() - t0) * 1000
    logger.info(f"TTS TTFB={ttfb:.0f}ms total={total:.0f}ms chunks={chunks}")
    return ttfb


async def main():
    audio = _make_tone_wav(ROOT / "models" / "bench_tone.wav")
    logger.info(f"budget={BUDGET_MS:.0f}ms | probing components…")

    results: dict[str, float | None] = {}
    try:
        results["stt_ms"] = await bench_stt(audio)
    except Exception as e:
        logger.exception(f"STT failed: {e}")
        results["stt_ms"] = None

    try:
        results["llm_ms"] = await bench_llm()
    except Exception as e:
        logger.exception(f"LLM failed: {e}")
        results["llm_ms"] = None

    try:
        results["tts_ms"] = await bench_tts()
    except Exception as e:
        logger.exception(f"TTS failed: {e}")
        results["tts_ms"] = None

    parts = [v for v in results.values() if v is not None]
    pipeline = sum(parts) if parts else None
    endpointing = 250 + 250
    est = (pipeline + endpointing) if pipeline is not None else None

    print("\n=== latency probe ===")
    for k, v in results.items():
        print(f"{k}: {v:.0f}ms" if v is not None else f"{k}: FAIL")
    if est is not None:
        print(f"sum(STT+LLM+TTS): {pipeline:.0f}ms")
        print(f"+endpointing(~{endpointing}ms): ~{est:.0f}ms")
        print(f"budget {BUDGET_MS:.0f}ms → {'OK' if est <= BUDGET_MS else 'OVER'}")
    print("=====================\n")


if __name__ == "__main__":
    asyncio.run(main())
