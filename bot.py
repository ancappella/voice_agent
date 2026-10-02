"""
Chapter07 · Pipecat 实时语音管道（兼容 pipecat-ai 0.0.108）

麦克风 → Silero VAD → 本地 Whisper STT → Gemini 意图理解 → Piper TTS → 扬声器
- barge-in：用户说话时立刻打断机器人（allow_interruptions + VAD start）
- 延迟：UserBotLatencyObserver，目标 < 800ms
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.piper.tts import PiperTTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.turns.user_start import VADUserTurnStartStrategy
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from latency import create_latency_observer

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=True)

SYSTEM_INSTRUCTION = """你是低延迟语音助手，负责理解用户意图并简短口头回复。

规则：
1. 先抓住意图（问答 / 指令 / 闲聊 / 澄清），再回复。
2. 口语化，一两句话内说完；不要列表、emoji、Markdown。
3. 不确定时先确认一句，不要长篇猜测。
4. 用户打断后，只回应最新那句。
"""

# 低延迟 VAD：更快确认开/停说 → 更快触发 STT 与 barge-in
VAD = SileroVADAnalyzer(
    params=VADParams(
        confidence=0.6,
        start_secs=0.15,
        stop_secs=0.25,
        min_volume=0.5,
    )
)

transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
}


def _api_key() -> str:
    key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("请设置 GOOGLE_API_KEY 或 GEMINI_API_KEY")
    return key


def _resolve_whisper_model(name: str, *, mlx: bool) -> str:
    """Map short names (tiny/base/…) to string model ids mlx_whisper / faster-whisper accept."""
    key = (name or "tiny").strip().lower()
    if mlx:
        from pipecat.services.whisper.stt import MLXModel

        mlx_map = {
            "tiny": MLXModel.TINY,
            "medium": MLXModel.MEDIUM,
            "large": MLXModel.LARGE_V3,
            "large-v3": MLXModel.LARGE_V3,
            "turbo": MLXModel.LARGE_V3_TURBO,
            "large-v3-turbo": MLXModel.LARGE_V3_TURBO,
            "distil": MLXModel.DISTIL_LARGE_V3,
            "q4": MLXModel.LARGE_V3_TURBO_Q4,
        }
        if key in mlx_map:
            return mlx_map[key].value
        if "/" in name:
            return name
        return MLXModel.TINY.value

    from pipecat.services.whisper.stt import Model

    fw_map = {
        "tiny": Model.TINY,
        "base": Model.BASE,
        "small": Model.SMALL,
        "medium": Model.MEDIUM,
        "large": Model.LARGE,
        "large-v3": Model.LARGE,
        "turbo": Model.LARGE_V3_TURBO,
    }
    m = fw_map.get(key)
    if m is not None:
        return m.value
    return name if name else Model.TINY.value


def _build_stt():
    """本地 Whisper：Apple Silicon 用 MLX，其它平台用 Faster-Whisper。"""
    model_name = os.getenv("WHISPER_MODEL", "tiny")
    lang = Language.ZH

    use_mlx = platform.system() == "Darwin" and platform.machine() == "arm64"
    if use_mlx:
        from pipecat.services.whisper.stt import WhisperSTTServiceMLX

        model = _resolve_whisper_model(model_name, mlx=True)
        logger.info(f"STT: WhisperSTTServiceMLX model={model}")
        return WhisperSTTServiceMLX(
            settings=WhisperSTTServiceMLX.Settings(
                model=model,
                language=lang,
                no_speech_prob=0.6,
                temperature=0.0,
            ),
        )

    from pipecat.services.whisper.stt import WhisperSTTService

    model = _resolve_whisper_model(model_name, mlx=False)
    logger.info(f"STT: WhisperSTTService model={model}")
    return WhisperSTTService(
        device="auto",
        compute_type="int8",
        settings=WhisperSTTService.Settings(
            model=model,
            language=lang,
            no_speech_prob=0.4,
        ),
    )


def _build_tts() -> PiperTTSService:
    voice = os.getenv("PIPER_VOICE", "zh_CN-huayan-medium")
    download_dir = ROOT / "models" / "piper"
    download_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"TTS: Piper voice={voice}")
    return PiperTTSService(
        download_dir=download_dir,
        settings=PiperTTSService.Settings(voice=voice),
    )


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    budget_ms = float(os.getenv("LATENCY_BUDGET_MS", "800"))
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    stt = _build_stt()
    tts = _build_tts()
    llm = GoogleLLMService(
        api_key=_api_key(),
        settings=GoogleLLMService.Settings(
            model=gemini_model,
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.4,
            max_tokens=96,
        ),
    )

    # barge-in：VAD 检测到用户开说立刻 InterruptionFrame
    # SpeechTimeout 比 SmartTurn 更轻；timeout 压低以挤进 800ms 预算
    user_turn = UserTurnStrategies(
        start=[VADUserTurnStartStrategy(enable_interruptions=True)],
        stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.25)],
    )

    context = LLMContext()
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=VAD,
            user_turn_strategies=user_turn,
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_agg,
            llm,
            tts,
            transport.output(),
            assistant_agg,
        ]
    )

    latency_observer = create_latency_observer(budget_ms=budget_ms)

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        idle_timeout_secs=getattr(runner_args, "pipeline_idle_timeout_secs", 300),
        observers=[latency_observer],
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("client connected — greeting")
        context.add_message(
            {
                "role": "user",
                "content": "请用一句话自我介绍，并说明可以说「打断你」。",
            }
        )
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("client disconnected")
        await task.cancel()

    logger.info(
        f"pipeline ready | barge-in=on | latency budget={budget_ms:.0f}ms | "
        f"llm={gemini_model}"
    )

    runner = PipelineRunner(handle_sigint=getattr(runner_args, "handle_sigint", True))
    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
