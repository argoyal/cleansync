"""Mail jobs (spec §4): at most one at a time per account, run in the order asked. Like the
sync queue, it never leaves an account busy, whatever ends a job (spec §4.5)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)
MailHandler = Callable[[int, str, dict, str], Awaitable[object]]


class MailQueue:
    """Must be called from the event loop thread."""

    def __init__(
        self,
        handler: MailHandler,
        *,
        max_parallel: int = 2,
        is_stopped: Callable[[], Awaitable[bool]],
        settle: Callable[[int], None] | None = None,
    ) -> None:
        self._handler = handler
        self._sem = asyncio.Semaphore(max_parallel)
        self._is_stopped = is_stopped  # the missing-disk stop, shared with Drive
        self._settle = settle
        self._waiting: dict[int, list[list]] = {}  # account -> [[action, args, trigger], …]
        self._running: set[int] = set()
        self._workers: dict[int, asyncio.Task] = {}
        self._finish_listeners: list[Callable[[int], None]] = []
        # Set first thing in stop(): nothing new starts, and a job cut off by the stop is left
        # RUNNING for recover_mail_jobs to close at the next start (it is not a failure).
        self._stopping = False

    def request(
        self, account_id: int, action: str, args: dict | None = None, trigger: str = "SCHEDULE"
    ) -> str:
        if self._stopping:
            return "disabled"
        args = dict(args or {})
        waiting = self._waiting.setdefault(account_id, [])
        for item in waiting:
            if item[0] == action and item[1] == args:
                item[2] = trigger  # the newest reason wins; it is only a label
                return "already_queued"
        waiting.append([action, args, trigger])
        if account_id not in self._workers:
            task = asyncio.get_running_loop().create_task(self._work(account_id))
            self._workers[account_id] = task
            task.add_done_callback(lambda t: self._worker_done(t, account_id))
        return "queued"

    def _worker_done(self, task: asyncio.Task, account_id: int) -> None:
        # A worker cancelled before its first step never runs its own tidy-up: drop its
        # place and what waited behind it, unless a newer worker has taken that place since.
        if self._workers.get(account_id) is task:
            del self._workers[account_id]
            self._waiting.pop(account_id, None)

    def is_running(self, account_id: int) -> bool:
        return account_id in self._running

    def is_busy(self, account_id: int) -> bool:
        return account_id in self._running or bool(self._waiting.get(account_id))

    def waiting(self, account_id: int) -> list[tuple[str, dict]]:
        return [(a, dict(b)) for a, b, _ in self._waiting.get(account_id, [])]

    def add_finish_listener(self, listener: Callable[[int], None]) -> None:
        self._finish_listeners.append(listener)

    async def _work(self, account_id: int) -> None:
        try:
            while self._waiting.get(account_id):
                action, args, trigger = self._waiting[account_id].pop(0)
                async with self._sem:
                    self._running.add(account_id)
                    ran = False
                    try:
                        if await self._is_stopped():
                            # The disk is missing: run nothing, keep nothing waiting.
                            self._waiting.pop(account_id, None)
                            return
                        ran = True
                        try:
                            await self._handler(account_id, action, args, trigger)
                        except Exception:
                            log.exception("mail job %s for account %s failed", action, account_id)
                    finally:
                        if ran and self._stopping:
                            # The app is shutting down: leave the job row as it is.
                            self._running.discard(account_id)
                        elif ran:
                            if self._settle is not None:
                                try:
                                    self._settle(account_id)
                                except Exception:
                                    log.exception(
                                        "could not close the mail job of account %s", account_id
                                    )
                            self._running.discard(account_id)
                            for listener in self._finish_listeners:
                                try:
                                    listener(account_id)
                                except Exception:
                                    log.exception("mail finish listener failed")
        except BaseException:
            self._waiting.pop(account_id, None)  # cancelled: what waited is dropped, not stuck
            raise
        finally:
            self._running.discard(account_id)
            if self._workers.get(account_id) is asyncio.current_task():
                del self._workers[account_id]
            if not self._waiting.get(account_id):
                self._waiting.pop(account_id, None)

    async def wait_idle(self) -> None:
        while self._workers:
            await asyncio.gather(*list(self._workers.values()), return_exceptions=True)

    async def stop(self) -> None:
        self._stopping = True  # first: nothing below may start new work
        for task in list(self._workers.values()):
            task.cancel()
        await asyncio.gather(*list(self._workers.values()), return_exceptions=True)
        self._waiting.clear()
