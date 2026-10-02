"""User→bot latency observer with an 800ms budget alarm."""

from __future__ import annotations

from loguru import logger

from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver


def create_latency_observer(budget_ms: float = 800.0) -> UserBotLatencyObserver:
    """Wrap UserBotLatencyObserver and warn when latency exceeds budget."""
    observer = UserBotLatencyObserver()
    budget_s = budget_ms / 1000.0

    @observer.event_handler("on_latency_measured")
    async def on_latency_measured(_observer, latency: float):
        ms = latency * 1000.0
        if ms <= budget_ms:
            logger.info(f"latency ok: {ms:.0f}ms (budget {budget_ms:.0f}ms)")
        else:
            logger.warning(
                f"latency OVER budget: {ms:.0f}ms > {budget_ms:.0f}ms "
                f"(+{(ms - budget_ms):.0f}ms)"
            )

    @observer.event_handler("on_latency_breakdown")
    async def on_latency_breakdown(_observer, breakdown):
        turn = getattr(breakdown, "user_turn_secs", None)
        if turn is not None:
            logger.info(f"user turn: {turn * 1000.0:.0f}ms")
        for line in breakdown.chronological_events():
            logger.info(f"  {line}")

    @observer.event_handler("on_first_bot_speech_latency")
    async def on_first_bot_speech(_observer, latency: float):
        logger.info(f"first bot speech: {latency * 1000.0:.0f}ms")

    observer.latency_budget_secs = budget_s  # type: ignore[attr-defined]
    return observer
