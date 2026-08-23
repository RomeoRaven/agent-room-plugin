"""Durable mention-to-delegate delivery for the canonical Agent Room."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from collections.abc import Awaitable, Callable

try:  # package load under protoAgent
    from .store import RoomStore
except ImportError:  # host-free direct module tests
    from store import RoomStore


log = logging.getLogger("protoagent.plugins.agent_room")


class MentionWorker:
    def __init__(
        self,
        store: RoomStore,
        *,
        invoke_delegate: Callable[..., Awaitable[str]],
        resolve_mentions: Callable[..., list[dict]] | None = None,
    ):
        self.store = store
        self.invoke_delegate = invoke_delegate
        self.resolve_mentions = resolve_mentions

    def _prompt(self, work: dict) -> str:
        context = self.store.thread_context(
            room_id=work["room_id"],
            thread_id=work["source_thread_id"],
            through_sequence=work["source_sequence"],
            limit=20,
        )
        transcript = "\n".join(
            f"#{message['sequence']} {message['author_principal']}: {message['body']}" for message in context
        )
        transcript = transcript[-8000:]
        return (
            "You were explicitly mentioned in the shared Agent Room. "
            "Return only one concise human-visible room reply. "
            "This action class is room_reply: do not perform mutations or build deferred execution. "
            "If the request needs mutation or approval, state that it is blocked.\n\n"
            f"Room thread context:\n{transcript}"
        )

    async def run_once(self) -> bool:
        work = self.store.claim_mention_work()
        if work is None:
            return False

        if work["status"] == "invoking":
            try:
                reply_body = await self.invoke_delegate(
                    work["delegate_name"],
                    self._prompt(work),
                    f"{work['room_id']}:{work['source_generation']}:{work['source_thread_id']}",
                    permissions="readonly",
                )
                work = {
                    **work,
                    **self.store.save_mention_reply(work["id"], reply_body),
                }
            except Exception:
                log.warning("Room mention delegate invocation failed (mention=%s)", work["id"], exc_info=True)
                self.store.fail_mention(work["id"], "mention dispatch failed")
                return True

        try:
            mentions = (
                self.resolve_mentions(
                    room_id=work["room_id"],
                    principal=work["target_principal"],
                    body=work["reply_body"],
                    parent_mention=work,
                )
                if self.resolve_mentions
                else None
            )
            reply = self.store.post(
                room_id=work["room_id"],
                principal=work["target_principal"],
                client_message_id=f"mention-reply:{work['id']}",
                body=work["reply_body"],
                author_kind="agent",
                thread_id=work["source_thread_id"],
                reply_to_message_id=work["source_message_id"],
                mentions=mentions,
            )
            self.store.complete_mention(work["id"], reply["message"]["id"])
        except Exception:
            log.warning("Room mention reply posting failed (mention=%s)", work["id"], exc_info=True)
            self.store.fail_mention(work["id"], "mention reply posting failed")
        return True


class MentionSurface:
    def __init__(self, worker: MentionWorker, *, idle_seconds: float = 0.25):
        self.worker = worker
        self.idle_seconds = max(0.05, float(idle_seconds))
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        self.worker.store.mark_interrupted_mentions_ambiguous()
        while True:
            worked = await self.worker.run_once()
            if not worked:
                await asyncio.sleep(self.idle_seconds)

    async def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="agent-room-mention-delivery")
        return self._task

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
