"""Claude wrapper: cached system prompt, adaptive thinking and a tool-use loop.

This is a trimmed version of the production client. The real bot exposes ~20 tools
(tasks, calendar, email, food and workout logs, content planning, long-term memory, web search);
a representative subset is shown here.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Awaitable, Callable

from anthropic import AsyncAnthropic


logger = logging.getLogger(__name__)

ToolExecutor = Callable[[str, dict], Awaitable[object]]

# Server-side tools run on Anthropic's side; capped so a single message can't run up the bill.
WEB_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 5, "max_content_tokens": 20000},
]

# Tool order and content must stay stable between requests, otherwise the prompt cache breaks.
TOOLS = [
    {
        "name": "add_task",
        "description": (
            "Add a task to the user's to-do list. Call it as soon as they say 'remind me', 'don't forget', "
            "'I need to buy/book/do…' or mention a to-do in passing. Don't ask, just add. One call per task. "
            "Check open tasks in context first: if the same task already exists in other words, update it instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Verb + object, keeping the details they gave: dates, brands, names."},
                "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                "area": {"type": "string", "enum": ["work", "growth", "home", "relationships", "health", "finance"]},
                "due_date": {"type": "string", "description": "ISO date, only if a deadline was named."},
            },
            "required": ["title", "priority", "area"],
        },
    },
    {
        "name": "add_calendar_event",
        "description": (
            "Create a Google Calendar event: 'put it on tomorrow at 5', 'book Friday morning for filming'. "
            "Convert relative dates to ISO 8601 with timezone yourself; default duration is 1 hour."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "start_iso": {"type": "string", "description": "e.g. '2026-04-30T17:00:00+04:00'"},
                "end_iso": {"type": "string", "description": "start + 1 hour if no duration was given"},
                "description": {"type": "string"},
                "location": {"type": "string"},
            },
            "required": ["summary", "start_iso", "end_iso"],
        },
    },
    {
        "name": "read_recent_emails",
        "description": "Read recent unread emails to triage the inbox or find a specific message.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "enum": ["personal", "work"]},
                "max_results": {"type": "integer", "default": 20},
            },
            "required": ["account"],
        },
    },
    {
        "name": "create_email_draft",
        "description": "Prepare a reply draft in the personal Gmail. Never sends: the user reviews and sends it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "reply_to_msg_id": {"type": "string", "description": "Gmail message id to reply to, keeps the thread."},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "log_food",
        "description": "Log a meal from a photo, voice note or text, with an estimate of protein and calories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "protein_g": {"type": "number"},
                "kcal": {"type": "number"},
            },
            "required": ["description", "protein_g", "kcal"],
        },
    },
    {
        "name": "remember",
        "description": (
            "Save a durable fact about the user (a preference, a decision, a person in their life) to long-term "
            "memory, so later conversations can use it. Not for one-off details."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"fact": {"type": "string"}},
            "required": ["fact"],
        },
    },
]


def build_system_prompt(persona_path: str, memory_path: str | None) -> str:
    """Persona and goals + long-term memory. Re-read on every call, so `remember` takes effect at once."""
    parts = [Path(persona_path).read_text(encoding="utf-8")]
    if memory_path and Path(memory_path).exists():
        parts.append("## What you remember about the user\n" + Path(memory_path).read_text(encoding="utf-8"))
    return "\n\n".join(parts)


class ClaudeClient:
    def __init__(self, persona_path: str = "./data/persona.md", memory_path: str | None = "./data/memory.md",
                 api_key: str | None = None, default_model: str = "claude-sonnet-5"):
        self.client = AsyncAnthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.persona_path = persona_path
        self.memory_path = memory_path
        self.default_model = default_model

    async def reply(
        self,
        user_text: str,
        history: list[dict] | None = None,
        tool_executor: ToolExecutor | None = None,
        images: list[tuple[str, str]] | None = None,
        max_tokens: int = 16000,
    ) -> str:
        """Send a message to Claude and return the final text, running any tools it calls along the way.

        images: [(base64, media_type)] attached to this message (screenshots, meal photos).
        The system prompt is cached for an hour, so a long persona costs almost nothing per message.
        """
        messages: list[dict] = list(history or [])
        if images:
            content = [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}}
                       for b64, mt in images]
            content.append({"type": "text", "text": user_text})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": user_text})

        kwargs = {
            "model": self.default_model,
            "max_tokens": max_tokens,
            "system": [{
                "type": "text",
                "text": build_system_prompt(self.persona_path, self.memory_path),
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }],
            "thinking": {"type": "adaptive"},
            "tools": TOOLS + WEB_TOOLS,
            "tool_choice": {"type": "auto"},
            "messages": messages,
        }

        paused_text: list[str] = []
        for _ in range(8):  # hard cap on tool round-trips
            response = await self.client.messages.create(**kwargs)

            if response.stop_reason == "end_turn":
                return "\n".join(paused_text + [self._extract_text(response.content)]).strip()

            if response.stop_reason == "pause_turn":
                # Server-side search hit its own iteration limit: send the turn back as-is and the server resumes.
                if text := self._extract_text(response.content):
                    paused_text.append(text)
                messages.append({"role": "assistant", "content": response.content})
                continue

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    logger.info("Tool call: %s args=%s", block.name, block.input)
                    try:
                        if tool_executor is None:
                            raise RuntimeError("tool executor not configured")
                        result, is_error = await tool_executor(block.name, block.input), False
                    except Exception as e:
                        logger.exception("Tool %s failed", block.name)
                        result, is_error = f"Tool failed: {e}", True
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        # a tool may return content blocks (text + images), e.g. carousel slides
                        "content": result if isinstance(result, list) else str(result),
                        "is_error": is_error,
                    })
                # All results go back in one message, so parallel tool calls work.
                messages.append({"role": "user", "content": tool_results})
                continue

            return self._extract_text(response.content)

        logger.warning("Tool loop hit the iteration cap")
        return self._extract_text(response.content)

    @staticmethod
    def _extract_text(content) -> str:
        # Answers with web citations arrive as several consecutive text blocks: join without separators.
        return "".join(b.text for b in content if b.type == "text").strip()
