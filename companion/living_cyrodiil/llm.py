"""LLM client for OpenAI-compatible services (OpenRouter by default)."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from openai import OpenAI, APIError, APITimeoutError, AuthenticationError, BadRequestError, RateLimitError

from .settings import LLMSettings

log = logging.getLogger(__name__)

# Extra tokens allowed when a model insists on thinking first, so the
# thinking can't use up the room for the reply itself.
MINIMAL_THINKING_ROOM = 1024


class LLMError(Exception):
    """The LLM call failed. The message is safe to show the player."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    seconds: float
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    stopped: bool = False           # streaming: we stopped reading early


class LLMClient:
    def __init__(self, settings: LLMSettings):
        self.settings = settings
        self._client = OpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=settings.timeout_seconds,
            max_retries=1,
            # OpenRouter's optional app attribution header.
            default_headers={"X-Title": "Living Cyrodiil"},
        )
        # How we ask about thinking. NPC replies don't need the model to think
        # first, and thinking can use the whole token budget before any words
        # are written (lore bench, Nov 4, 2026: empty replies on Kvatch and the
        # Mythic Dawn), so it starts "off". Some models refuse that ("Reasoning
        # is mandatory": Claude Sonnet, Gemini Flash), and then we switch to
        # "minimal" with extra token room, and remember it for this model.
        # Only OpenRouter understands these switches, so they're only sent there.
        on_openrouter = "openrouter.ai" in settings.base_url
        self._reasoning = "model" if (settings.reasoning or not on_openrouter) else "off"
        self._warming = threading.Lock()

    def warm_up(self) -> None:
        """Opens a connection to the LLM service ahead of the request (step
        4b-2), so the reply request doesn't wait for the connection setup
        (about 0.1 s saved, measured Nov 7). Call it at key release: it runs
        alongside speech-to-text. The service drops idle connections within
        seconds, so warming at key press, or keeping connections open longer,
        doesn't help. Returns at once; failures are only logged."""
        if self._warming.locked():
            return

        def run() -> None:
            with self._warming:
                start = time.perf_counter()
                try:
                    self._client._client.head(self.settings.base_url.rstrip("/") + "/models", timeout=5)
                    log.debug("LLM connection warmed in %.2fs", time.perf_counter() - start)
                except Exception as e:
                    log.debug("Warming the LLM connection failed (harmless): %s", e)

        threading.Thread(target=run, name="llm-warm", daemon=True).start()

    def _request_options(self) -> tuple[dict, int]:
        if self._reasoning == "off":
            return {"reasoning": {"enabled": False}}, self.settings.max_tokens
        if self._reasoning == "minimal":
            return {"reasoning": {"effort": "minimal"}}, self.settings.max_tokens + MINIMAL_THINKING_ROOM
        return {}, self.settings.max_tokens            # leave it to the model

    def _create(self, messages: list[dict], stream: bool = False):
        """Sends the request, switching to minimal thinking once if the model
        insists on it. Errors become LLMError with a message for the player."""
        for attempt in (1, 2):
            mode = self._reasoning
            extra, max_tokens = self._request_options()
            try:
                return self._client.chat.completions.create(
                    model=self.settings.model,
                    messages=messages,
                    temperature=self.settings.temperature,
                    max_tokens=max_tokens,
                    extra_body=extra or None,
                    stream=stream,
                )
            except BadRequestError as e:
                # Judge by what THIS request sent: another request may already
                # have switched the mode while this one was in flight.
                if attempt == 1 and mode == "off" and "reasoning" in str(e).lower() and "mandatory" in str(e).lower():
                    if self._reasoning == "off":
                        log.info("%s won't answer with thinking off; using minimal thinking from now on",
                                 self.settings.model)
                        self._reasoning = "minimal"
                    continue
                raise LLMError(f"The LLM service returned an error: {e}") from e
            except AuthenticationError as e:
                raise LLMError("The API key was rejected. Check api_key in settings.local.toml.") from e
            except RateLimitError as e:
                raise LLMError("The model is busy or you've reached its rate limit (common with free models). "
                               "Wait a moment, or choose another model.") from e
            except APITimeoutError as e:
                raise LLMError(f"No reply within {self.settings.timeout_seconds:.0f} seconds.") from e
            except APIError as e:
                raise LLMError(f"The LLM service returned an error: {e}") from e
        raise LLMError("The LLM service refused the request twice.")

    def complete(self, messages: list[dict]) -> LLMResult:
        start = time.perf_counter()
        resp = self._create(messages)
        seconds = time.perf_counter() - start
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        finish = resp.choices[0].finish_reason if resp.choices else None
        usage = getattr(resp, "usage", None)
        if finish == "length":
            log.warning("LLM reply hit max_tokens (%d)%s; raise [llm] max_tokens in settings.local.toml if this repeats",
                        self.settings.max_tokens, " before writing anything" if not text.strip() else " and was cut off")
        result = LLMResult(
            text=text,
            seconds=seconds,
            model=getattr(resp, "model", self.settings.model) or self.settings.model,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )
        log.info("LLM reply in %.2fs from %s (tokens in/out: %s/%s)",
                 seconds, result.model, result.prompt_tokens, result.completion_tokens)
        return result

    def complete_streaming(self, messages: list[dict], on_text: Callable[[str], bool]) -> LLMResult:
        """Like complete(), but the reply arrives as it's written (Phase 3 step
        4b): `on_text` gets each new piece of text and returns False to stop
        reading (the rest of the reply isn't needed). Returns the text read."""
        start = time.perf_counter()
        stream = self._create(messages, stream=True)
        pieces: list[str] = []
        finish = None
        first_at = None
        stopped = False
        try:
            for event in stream:
                if not event.choices:
                    continue
                choice = event.choices[0]
                delta = getattr(choice.delta, "content", None) or ""
                if choice.finish_reason:
                    finish = choice.finish_reason
                if not delta:
                    continue
                if first_at is None:
                    first_at = time.perf_counter() - start
                pieces.append(delta)
                if not on_text(delta):
                    stopped = True
                    break
        except APITimeoutError as e:
            raise LLMError(f"No reply within {self.settings.timeout_seconds:.0f} seconds.") from e
        except APIError as e:
            raise LLMError(f"The LLM service returned an error mid-reply: {e}") from e
        finally:
            if stopped:
                try:
                    stream.close()
                except Exception:
                    pass
        seconds = time.perf_counter() - start
        text = "".join(pieces)
        if finish == "length":
            log.warning("LLM reply hit max_tokens (%d)%s; raise [llm] max_tokens in settings.local.toml if this repeats",
                        self.settings.max_tokens, " before writing anything" if not text.strip() else " and was cut off")
        log.info("LLM reply streamed in %.2fs from %s (first text after %s)%s", seconds, self.settings.model,
                 f"{first_at:.2f}s" if first_at is not None else "none",
                 ", stopped early" if stopped else "")
        return LLMResult(text=text, seconds=seconds, model=self.settings.model, finish_reason=finish,
                         stopped=stopped)
