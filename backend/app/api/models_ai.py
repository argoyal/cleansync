"""Settings → Models (AI rules spec §4): connections and which model each task uses.
Keys and header values go in, never come back out."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.auth.deps import AuthContext, require_user
from app.models.enums import AiTaskName
from app.schemas.ai import (
    ConnectionIn,
    ConnectionOut,
    ConnectionPatch,
    TaskIn,
    TaskOut,
    TestIn,
    TestOut,
)
from app.services.ai.client import AiError
from app.services.ai.connections import KEEP, ConnectionError_, ConnectionNotFound
from app.services.rules.chooser import AiChooser
from app.services.rules.mailview import MailForModel
from app.services.rules.match import Conditions, RuleView

router = APIRouter(prefix="/api/ai")

SAMPLE_MAIL = MailForModel(
    message_id=0,
    sender="digest@news.example.com",
    sender_name="Example News",
    to=["you@example.com"],
    cc=[],
    owner_in="to",
    subject="Your weekly digest: 5 stories you missed",
    date=datetime(2026, 10, 1, tzinfo=UTC),
    attachments=[],
    list_unsubscribe=True,
    mailing_list=True,
    calendar_invite=False,
    text="Here are this week's top stories from Example News. [link: news.example.com] "
    "You are receiving this because you subscribed. Unsubscribe any time.",
    labels=["INBOX"],
)
SAMPLE_RULES = [
    RuleView(
        1, 1, "Receipts", "Receipts, invoices and order confirmations.", Conditions(), "ai_only"
    ),
    RuleView(
        2, 1, "Newsletters", "Newsletters and digests I signed up for.", Conditions(), "ai_only"
    ),
]


def _ai(request: Request):
    return request.app.state.ai


def _out(c) -> ConnectionOut:
    return ConnectionOut(
        id=c.id,
        name=c.name,
        base_url=c.base_url,
        has_key=c.has_key,
        header_names=list(c.header_names or []),
    )


def _task_name(task: str) -> AiTaskName:
    try:
        return AiTaskName(task)
    except ValueError:
        raise HTTPException(404, "no such task") from None


@router.get("/connections", response_model=list[ConnectionOut])
def connections(request: Request, ctx: AuthContext = Depends(require_user)):
    return [_out(c) for c in _ai(request).connections()]


@router.post("/connections", response_model=ConnectionOut, status_code=201)
def create(body: ConnectionIn, request: Request, ctx: AuthContext = Depends(require_user)):
    try:
        return _out(_ai(request).create(body.name, body.base_url, body.api_key, body.headers))
    except ConnectionError_ as e:
        raise HTTPException(400, str(e)) from e


@router.patch("/connections/{conn_id}", response_model=ConnectionOut)
def update(
    conn_id: int, body: ConnectionPatch, request: Request, ctx: AuthContext = Depends(require_user)
):
    given = body.model_fields_set
    try:
        return _out(
            _ai(request).update(
                conn_id,
                name=body.name,
                base_url=body.base_url,
                api_key=body.api_key if "api_key" in given else KEEP,
                headers=(body.headers or {}) if "headers" in given else KEEP,
            )
        )
    except ConnectionNotFound as e:
        raise HTTPException(404, str(e)) from e
    except ConnectionError_ as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/connections/{conn_id}", status_code=204)
def delete(conn_id: int, request: Request, ctx: AuthContext = Depends(require_user)):
    try:
        _ai(request).delete(conn_id)
    except ConnectionNotFound as e:
        raise HTTPException(404, str(e)) from e
    return Response(status_code=204)


@router.get("/connections/{conn_id}/models")
async def models(conn_id: int, request: Request, ctx: AuthContext = Depends(require_user)):
    try:
        target = _ai(request).target_for(conn_id, "", timeout_s=15)
    except ConnectionNotFound as e:
        raise HTTPException(404, str(e)) from e
    try:
        return {"models": await request.app.state.ai_client.list_models(target)}
    except AiError as e:
        raise HTTPException(502, str(e)) from e


@router.get("/tasks", response_model=dict[str, TaskOut])
def tasks(request: Request, ctx: AuthContext = Depends(require_user)):
    saved = _ai(request).tasks()
    out = {}
    for name in AiTaskName:
        t = saved.get(str(name))
        out[str(name)] = TaskOut(
            task=str(name),
            connection_id=t.connection_id if t else None,
            model=t.model if t else "",
            temperature=t.temperature if t else 0.0,
            timeout_s=t.timeout_s if t else 60,
        )
    return out


@router.put("/tasks/{task}", response_model=TaskOut)
def set_task(task: str, body: TaskIn, request: Request, ctx: AuthContext = Depends(require_user)):
    name = _task_name(task)
    try:
        t = _ai(request).set_task(
            name, body.connection_id, body.model, body.temperature, body.timeout_s
        )
    except ConnectionNotFound as e:
        raise HTTPException(404, str(e)) from e
    except ConnectionError_ as e:
        raise HTTPException(400, str(e)) from e
    return TaskOut(
        task=t.task,
        connection_id=t.connection_id,
        model=t.model,
        temperature=t.temperature,
        timeout_s=t.timeout_s,
    )


@router.post("/test", response_model=TestOut)
async def test(body: TestIn, request: Request, ctx: AuthContext = Depends(require_user)):
    try:
        target = _ai(request).target_for(
            body.connection_id, body.model, body.temperature, body.timeout_s
        )
    except ConnectionNotFound as e:
        raise HTTPException(404, str(e)) from e
    chooser = AiChooser(request.app.state.ai_client, lambda: target)
    try:
        choice = await chooser.choose(SAMPLE_RULES, SAMPLE_MAIL, [])
    except AiError as e:
        return TestOut(ok=False, error=str(e))
    chose = next((r.name for r in SAMPLE_RULES if r.id == choice.rule_id), None)
    mode = _ai(request).remembered_mode(body.connection_id, body.model)
    return TestOut(
        ok=choice.raw is None,
        took_ms=choice.took_ms,
        mode=mode,
        chose=chose,
        why=choice.why,
        error=None if choice.raw is None else "The answer couldn't be read.",
    )
