from __future__ import annotations

from collections.abc import Callable

from .governor import LibraryContextGovernor
from .models import GovernedPrompt


class GovernedTextAgent:
    """Wrap a stateless text-model callback with bounded prompt assembly and persistence.

    The supplied callback must send exactly the ``messages`` it receives. It must not
    append another transcript or continue a provider-managed conversation. Structured
    tool calls, streaming deltas, attachments, and multimodal values need a custom
    adapter rather than this text-only interface.
    """

    def __init__(
        self,
        context: LibraryContextGovernor,
        call_model: Callable[[list[dict[str, str]]], str],
        *,
        system_prompt: str = "",
    ) -> None:
        self.context = context
        self.call_model = call_model
        self.system_prompt = system_prompt
        self.last_prompt: GovernedPrompt | None = None

    def turn(
        self,
        user_message: str,
        *,
        turn_id: str | None = None,
        focus: str | None = None,
        protected: bool = False,
        strict_freshness: bool = False,
    ) -> str:
        """Run one governed text turn and durably record the returned response.

        Pass a stable, unique ``turn_id`` when the caller may retry. The governor then
        uses deterministic user and assistant event IDs for idempotent persistence.
        """

        if turn_id is not None and not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
        user_event_id = None if turn_id is None else f"{turn_id}:user"
        assistant_event_id = None if turn_id is None else f"{turn_id}:assistant"
        prompt = self.context.prepare(
            user_message,
            focus=focus,
            system_prompt=self.system_prompt,
            protected=protected,
            event_id=user_event_id,
            strict_freshness=strict_freshness,
        )
        self.last_prompt = prompt
        response = self.call_model(prompt.messages)
        if not isinstance(response, str):
            raise TypeError("call_model must return response text as str")
        if not response.strip():
            raise ValueError("call_model returned an empty response")
        self.context.commit(response, event_id=assistant_event_id)
        return response
