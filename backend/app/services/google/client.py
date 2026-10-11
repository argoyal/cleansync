"""One HTTP client for every Google API call CleanSync makes itself (spec §7): paced per
mailbox, counted against the day's budget, and patient with Google's rate limits."""

import asyncio
import logging
from collections.abc import Callable

import httpx

from app.services.drive_api.tokens import request_or_none
from app.services.google.quota import ApiBudget, Pacer, cost

log = logging.getLogger(__name__)
RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
FIRST_WAIT_S = 1.0
LONGEST_WAIT_S = 64.0
RATE_TRIES = 8
RATE_MSG = "Google asked CleanSync to slow down. It goes on at the next check."


class RateLimited(Exception):
    def __init__(self) -> None:
        super().__init__(RATE_MSG)


def error_reasons(resp: httpx.Response) -> set[str]:
    """The `reason` of each error in a Google error answer (empty when there is none)."""
    try:
        errors = resp.json()["error"].get("errors") or []
    except (ValueError, KeyError, TypeError, AttributeError):
        return set()
    return {str(e.get("reason")) for e in errors if isinstance(e, dict)}


def is_rate_limited(resp: httpx.Response) -> bool:
    if resp.status_code == 429:
        return True
    return resp.status_code == 403 and bool(error_reasons(resp) & RATE_REASONS)


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("Retry-After", "")
    return float(value) if value.isdigit() else None


class GoogleClient:
    def __init__(
        self,
        *,
        budget: ApiBudget | None,
        pacer: Pacer | None,
        http_factory: Callable[[], httpx.AsyncClient] = lambda: httpx.AsyncClient(timeout=60),
        sleep=asyncio.sleep,
        rate_tries: int = RATE_TRIES,
    ) -> None:
        self.budget = budget
        self.pacer = pacer
        self.http_factory = http_factory
        self.sleep = sleep
        self.rate_tries = rate_tries

    async def request(
        self,
        key: str,
        op: str,
        method: str,
        url: str,
        *,
        enforce_budget: bool = True,
        wait_out_rate_limits: bool = True,
        http_factory: Callable[[], httpx.AsyncClient] | None = None,
        **kwargs,
    ) -> httpx.Response | None:
        """One Google call. `key` names the mailbox (the account's remote name) for the pace.

        BudgetReached is raised before anything is sent once the day's budget is used up
        (only with enforce_budget; a count-only call goes ahead even if the counting itself
        fails). `http_factory` replaces the client's own for this call (Drive's shorter timeout). A rate limit is waited out (1, 2, 4 … 64 s, or what
        Google's Retry-After says) and RateLimited raised after rate_tries answers in a row.
        A call with wait_out_rate_limits=False is neither paced nor retried: the answer is
        handed back as it is (Drive calls, which are counted but never held back).
        None when Google could not be reached at all."""
        units = cost(op)
        wait = FIRST_WAIT_S
        for attempt in range(1, self.rate_tries + 1):
            if self.budget is not None:
                if enforce_budget:
                    self.budget.spend(units)
                else:
                    try:
                        self.budget.spend(units, enforce=False)
                    except Exception:  # counting must never stop a call that is only counted
                        log.exception("could not count a Google call (%s)", op)
            if self.pacer is not None and wait_out_rate_limits:
                await self.pacer.wait(key, units)
            resp = await request_or_none(http_factory or self.http_factory, method, url, **kwargs)
            if resp is None or not wait_out_rate_limits or not is_rate_limited(resp):
                return resp
            if attempt == self.rate_tries:
                break
            pause = min(_retry_after(resp) or wait, LONGEST_WAIT_S)
            log.info("Google rate limit (%s); waiting %.0f s", op, pause)
            await self.sleep(pause)
            wait = min(wait * 2, LONGEST_WAIT_S)
        raise RateLimited()
