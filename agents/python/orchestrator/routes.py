"""Orchestrator API routes — auth, chat, conversations, marketplace, admin."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from shared.context import current_user_email, current_user_role, current_session_id, current_conversation_history
from shared.db import get_pool
from shared.jwt_utils import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from shared.usage_db import UsageTimer, log_agent_usage

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Request / Response Models ──────────────────────────────────


class SignupRequest(BaseModel):
    email: str
    password: str
    name: str


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: dict[str, Any]


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    agents_involved: list[str]


class AccessRequestBody(BaseModel):
    agent_name: str
    role_requested: str
    use_case: str


class AdminActionBody(BaseModel):
    admin_notes: str = ""


class AddToCartRequest(BaseModel):
    product_id: str
    quantity: int = 1


class UpdateCartItemRequest(BaseModel):
    quantity: int


class CartAddressRequest(BaseModel):
    shipping_address: dict | None = None
    billing_address: dict | None = None
    billing_same_as_shipping: bool = True


class ApplyCouponRequest(BaseModel):
    code: str


class CheckoutRequest(BaseModel):
    shipping_address: dict
    billing_address: dict | None = None
    billing_same_as_shipping: bool = True
    payment_method: str = "demo"


class CancelOrderRequest(BaseModel):
    reason: str


class ReturnOrderRequest(BaseModel):
    reason: str
    refund_method: str = "original_payment"


# ── Auth Dependency ────────────────────────────────────────────


async def require_auth(request: Request) -> dict[str, Any]:
    """Extract and validate JWT from Authorization header. Sets ContextVars."""
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header.removeprefix("Bearer ")
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")

    current_user_email.set(payload.get("sub", ""))
    current_user_role.set(payload.get("role", "customer"))
    current_session_id.set(request.headers.get("x-session-id", ""))

    return payload


async def require_admin(user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    """Require the authenticated user to have admin role."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_seller(user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    """Require the authenticated user to have seller or admin role."""
    if user.get("role") not in ("seller", "admin"):
        raise HTTPException(status_code=403, detail="Seller access required")
    return user


# ── Auth Routes (PUBLIC) ──────────────────────────────────────


@router.post("/api/auth/signup", response_model=AuthResponse)
async def signup(body: SignupRequest) -> AuthResponse:
    """Create a new user account and return tokens."""
    pool = get_pool()

    # Check if user already exists
    existing = await pool.fetchrow("SELECT id FROM users WHERE email = $1", body.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    password_hash = hash_password(body.password)

    row = await pool.fetchrow(
        """INSERT INTO users (email, password_hash, name, role, loyalty_tier)
           VALUES ($1, $2, $3, 'customer', 'bronze')
           RETURNING id, email, name, role, loyalty_tier, total_spend, created_at""",
        body.email,
        password_hash,
        body.name,
    )

    user_id = str(row["id"])
    access_token = create_access_token(row["email"], row["role"], user_id)
    refresh_token = create_refresh_token(row["email"])

    logger.info("auth.signup email=%s user_id=%s", body.email, user_id)

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user={
            "id": user_id,
            "email": row["email"],
            "name": row["name"],
            "role": row["role"],
            "loyalty_tier": row["loyalty_tier"],
            "total_spend": float(row["total_spend"]),
        },
    )


@router.post("/api/auth/login", response_model=AuthResponse)
async def login(body: LoginRequest) -> AuthResponse:
    """Authenticate user and return tokens."""
    pool = get_pool()

    row = await pool.fetchrow(
        """SELECT id, email, password_hash, name, role, loyalty_tier, total_spend, is_active
           FROM users WHERE email = $1""",
        body.email,
    )

    if not row:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    if not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user_id = str(row["id"])
    access_token = create_access_token(row["email"], row["role"], user_id)
    refresh_token = create_refresh_token(row["email"])

    logger.info("auth.login email=%s user_id=%s", body.email, user_id)

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user={
            "id": user_id,
            "email": row["email"],
            "name": row["name"],
            "role": row["role"],
            "loyalty_tier": row["loyalty_tier"],
            "total_spend": float(row["total_spend"]),
        },
    )


@router.post("/api/auth/refresh")
async def refresh_token(body: RefreshRequest) -> dict[str, str]:
    """Exchange a refresh token for a new access token."""
    try:
        payload = decode_token(body.refresh_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")

    email = payload.get("sub", "")

    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, role, is_active FROM users WHERE email = $1",
        email,
    )

    if not row:
        raise HTTPException(status_code=401, detail="User not found")

    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    access_token = create_access_token(email, row["role"], str(row["id"]))

    return {"access_token": access_token}


# ── Chat Routes ───────────────────────────────────────────────


@router.post("/api/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, user: dict[str, Any] = Depends(require_auth)) -> ChatResponse:
    """Main chat endpoint — sends message to the orchestrator agent."""
    from orchestrator.agent import create_orchestrator_agent

    pool = get_pool()
    user_email = user.get("sub", "")
    user_id = user.get("user_id", "")

    # Resolve or create conversation
    conversation_id = body.conversation_id
    if conversation_id:
        # Verify conversation belongs to this user
        conv = await pool.fetchrow(
            """SELECT id FROM conversations
               WHERE id = $1 AND user_id = $2 AND is_active = TRUE""",
            conversation_id,
            user_id,
        )
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        # Create new conversation with first message as title
        title = body.message[:100] if len(body.message) > 100 else body.message
        row = await pool.fetchrow(
            """INSERT INTO conversations (user_id, title)
               VALUES ($1, $2)
               RETURNING id""",
            user_id,
            title,
        )
        conversation_id = str(row["id"])

    # Save user message
    await pool.execute(
        """INSERT INTO messages (conversation_id, role, content, agent_name)
           VALUES ($1, 'user', $2, NULL)""",
        conversation_id,
        body.message,
    )

    # Load conversation history for context
    history_rows = await pool.fetch(
        """SELECT role, content FROM messages
           WHERE conversation_id = $1
           ORDER BY created_at ASC
           LIMIT 50""",
        conversation_id,
    )
    history = [{"role": r["role"], "content": r["content"]} for r in history_rows]

    # Fetch user context (profile + recent orders) for the agent
    user_row = await pool.fetchrow(
        "SELECT name, role, loyalty_tier, total_spend FROM users WHERE email = $1", user_email,
    )
    user_context_lines = []
    if user_row:
        user_context_lines.append(f"Logged-in user: {user_row['name']} ({user_email})")
        user_context_lines.append(f"Role: {user_row['role']}, Loyalty: {user_row['loyalty_tier']}, Total spend: ${user_row['total_spend']:.2f}")
    recent_orders = await pool.fetch(
        """SELECT o.id, o.status, o.total, o.created_at
           FROM orders o JOIN users u ON o.user_id = u.id
           WHERE u.email = $1 ORDER BY o.created_at DESC LIMIT 5""",
        user_email,
    )
    if recent_orders:
        user_context_lines.append(f"Recent orders ({len(recent_orders)}):")
        for o in recent_orders:
            user_context_lines.append(f"  - Order {o['id']} | {o['status']} | ${o['total']:.2f} | {o['created_at'].strftime('%Y-%m-%d')}")
    user_context = "\n".join(user_context_lines) if user_context_lines else None

    # Call the orchestrator agent via MAF-native execution. The Agent
    # already owns its tools + instructions + context providers, and the
    # ECommerceContextProvider chain injects user_context (profile +
    # recent orders) into state before each run — see
    # shared/context_providers.py. _user_context is retained for telemetry
    # only.
    _ = user_context  # context is injected via ContextProvider, kept for symmetry
    from orchestrator.agent import create_orchestrator_agent
    from shared.agent_host import _run_agent_native
    from shared.telemetry import agent_run_span

    agent = create_orchestrator_agent()
    agents_involved: list[str] = ["orchestrator"]

    # Set conversation history ContextVar so call_specialist_agent can forward it
    current_conversation_history.set(history)

    with UsageTimer() as timer:
        with agent_run_span("orchestrator"):
            try:
                response_text = await _run_agent_native(agent, body.message, history=history)
            except Exception:
                logger.exception("chat.agent_error user=%s conversation=%s", user_email, conversation_id)
                response_text = "I apologize, but I encountered an issue processing your request. Please try again."

    # Save assistant message
    await pool.execute(
        """INSERT INTO messages (conversation_id, role, content, agent_name, agents_involved)
           VALUES ($1, 'assistant', $2, 'orchestrator', $3)""",
        conversation_id,
        response_text,
        agents_involved,
    )

    # Update conversation timestamp
    await pool.execute(
        "UPDATE conversations SET last_message_at = NOW() WHERE id = $1",
        conversation_id,
    )

    # Log usage (fire-and-forget, errors are swallowed)
    await log_agent_usage(
        user_id=user_id,
        agent_name="orchestrator",
        input_summary=body.message,
        duration_ms=timer.duration_ms,
        tool_calls_count=len(agents_involved) - 1,
    )

    return ChatResponse(
        response=response_text,
        conversation_id=conversation_id,
        agents_involved=agents_involved,
    )


@router.post("/api/chat/stream")
async def chat_stream(body: ChatRequest, request: Request, user: dict[str, Any] = Depends(require_auth)):
    """Streaming chat endpoint — sends SSE events as the agent generates tokens."""
    from orchestrator.agent import create_orchestrator_agent
    from shared.agent_host import _run_agent_native_stream

    pool = get_pool()
    user_email = user.get("sub", "")
    user_id = user.get("user_id", "")

    # Resolve or create conversation
    conversation_id = body.conversation_id
    if conversation_id:
        conv = await pool.fetchrow(
            """SELECT id FROM conversations
               WHERE id = $1 AND user_id = $2 AND is_active = TRUE""",
            conversation_id,
            user_id,
        )
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        title = body.message[:100] if len(body.message) > 100 else body.message
        row = await pool.fetchrow(
            """INSERT INTO conversations (user_id, title)
               VALUES ($1, $2)
               RETURNING id""",
            user_id,
            title,
        )
        conversation_id = str(row["id"])

    # Save user message
    await pool.execute(
        """INSERT INTO messages (conversation_id, role, content, agent_name)
           VALUES ($1, 'user', $2, NULL)""",
        conversation_id,
        body.message,
    )

    # Load conversation history
    history_rows = await pool.fetch(
        """SELECT role, content FROM messages
           WHERE conversation_id = $1
           ORDER BY created_at ASC
           LIMIT 50""",
        conversation_id,
    )
    history = [{"role": r["role"], "content": r["content"]} for r in history_rows]

    # Fetch user context
    user_row = await pool.fetchrow(
        "SELECT name, role, loyalty_tier, total_spend FROM users WHERE email = $1", user_email,
    )
    user_context_lines: list[str] = []
    if user_row:
        user_context_lines.append(f"Logged-in user: {user_row['name']} ({user_email})")
        user_context_lines.append(f"Role: {user_row['role']}, Loyalty: {user_row['loyalty_tier']}, Total spend: ${user_row['total_spend']:.2f}")
    recent_orders = await pool.fetch(
        """SELECT o.id, o.status, o.total, o.created_at
           FROM orders o JOIN users u ON o.user_id = u.id
           WHERE u.email = $1 ORDER BY o.created_at DESC LIMIT 5""",
        user_email,
    )
    if recent_orders:
        user_context_lines.append(f"Recent orders ({len(recent_orders)}):")
        for o in recent_orders:
            user_context_lines.append(f"  - Order {o['id']} | {o['status']} | ${o['total']:.2f} | {o['created_at'].strftime('%Y-%m-%d')}")
    user_context = "\n".join(user_context_lines) if user_context_lines else None

    # See chat() — same reasoning: Agent owns tools / prompt / providers.
    _ = user_context  # injected via ContextProvider, unused here
    agent = create_orchestrator_agent()
    agents_involved: list[str] = ["orchestrator"]
    current_conversation_history.set(history)

    async def event_generator() -> AsyncGenerator[str, None]:
        """Yield SSE-formatted events from the streaming agent response.

        Audit fix #9: every chunk is gated on three checks before it
        ships to the wire — wall-clock budget, accumulator-byte ceiling,
        and client-disconnect probe. None of them existed before, so a
        slow client or a runaway model could pin a Starlette worker
        and grow the in-memory transcript without bound.
        """
        from shared.telemetry import agent_run_span
        from shared.config import settings

        full_response: list[str] = []
        full_bytes = 0
        truncated = False
        start_time = time.monotonic()
        deadline = start_time + float(settings.MAF_STREAM_TIMEOUT_SECONDS)
        max_bytes = int(settings.MAF_STREAM_MAX_BYTES)

        with agent_run_span("orchestrator"):
            try:
                async for chunk in _run_agent_native_stream(
                    agent,
                    body.message,
                    history=history,
                ):
                    if await request.is_disconnected():
                        logger.info(
                            "chat_stream.client_disconnected conversation=%s elapsed_ms=%d",
                            conversation_id,
                            int((time.monotonic() - start_time) * 1000),
                        )
                        break
                    if time.monotonic() > deadline:
                        logger.warning(
                            "chat_stream.timeout conversation=%s budget_s=%s",
                            conversation_id,
                            settings.MAF_STREAM_TIMEOUT_SECONDS,
                        )
                        timeout_msg = (
                            " [stream timed out — the agent took too long; please retry]"
                        )
                        full_response.append(timeout_msg)
                        yield f"data: {timeout_msg}\n\n"
                        break
                    if not truncated:
                        chunk_bytes = len(chunk.encode("utf-8"))
                        if full_bytes + chunk_bytes > max_bytes:
                            truncated = True
                            marker = " [response truncated at limit]"
                            full_response.append(marker)
                            yield f"data: {marker}\n\n"
                            continue
                        full_bytes += chunk_bytes
                        full_response.append(chunk)
                        yield f"data: {chunk}\n\n"

            except Exception:
                logger.exception("chat_stream.agent_error user=%s conversation=%s", user_email, conversation_id)
                error_msg = "I apologize, but I encountered an issue processing your request. Please try again."
                full_response.append(error_msg)
                yield f"data: {error_msg}\n\n"

        # Send metadata and termination event
        response_text = "".join(full_response)
        yield f"event: metadata\ndata: {json.dumps({"conversation_id": conversation_id, "agents_involved": agents_involved})}\n\n"
        yield "data: [DONE]\n\n"

        # Persist assistant message and update conversation (fire-and-forget)
        try:
            await pool.execute(
                """INSERT INTO messages (conversation_id, role, content, agent_name, agents_involved)
                   VALUES ($1, 'assistant', $2, 'orchestrator', $3)""",
                conversation_id,
                response_text,
                agents_involved,
            )
            await pool.execute(
                "UPDATE conversations SET last_message_at = NOW() WHERE id = $1",
                conversation_id,
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)
            await log_agent_usage(
                user_id=user_id,
                agent_name="orchestrator",
                input_summary=body.message,
                duration_ms=duration_ms,
                tool_calls_count=len(agents_involved) - 1,
            )
        except Exception:
            logger.exception("chat_stream.persist_error conversation=%s", conversation_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Conversation Routes ───────────────────────────────────────


@router.get("/api/conversations")
async def list_conversations(user: dict[str, Any] = Depends(require_auth)) -> list[dict[str, Any]]:
    """List the authenticated user's conversations."""
    pool = get_pool()
    user_id = user.get("user_id", "")

    rows = await pool.fetch(
        """SELECT c.id, c.title, c.created_at, c.last_message_at,
                  (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) as message_count
           FROM conversations c
           WHERE c.user_id = $1 AND c.is_active = TRUE
           ORDER BY c.last_message_at DESC
           LIMIT 50""",
        user_id,
    )

    return [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "message_count": r["message_count"],
            "created_at": r["created_at"].isoformat(),
            "last_message_at": r["last_message_at"].isoformat(),
        }
        for r in rows
    ]


@router.get("/api/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Get a conversation with its messages."""
    pool = get_pool()
    user_id = user.get("user_id", "")

    conv = await pool.fetchrow(
        """SELECT id, title, created_at, last_message_at
           FROM conversations
           WHERE id = $1 AND user_id = $2 AND is_active = TRUE""",
        conversation_id,
        user_id,
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = await pool.fetch(
        """SELECT id, role, content, agent_name, agents_involved, metadata,
                  tokens_in, tokens_out, created_at
           FROM messages
           WHERE conversation_id = $1
           ORDER BY created_at ASC""",
        conversation_id,
    )

    return {
        "id": str(conv["id"]),
        "title": conv["title"],
        "created_at": conv["created_at"].isoformat(),
        "last_message_at": conv["last_message_at"].isoformat(),
        "messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "content": m["content"],
                "agent_name": m["agent_name"],
                "agents_involved": m["agents_involved"] or [],
                "metadata": m["metadata"] or {},
                "tokens_in": m["tokens_in"],
                "tokens_out": m["tokens_out"],
                "created_at": m["created_at"].isoformat(),
            }
            for m in messages
        ],
    }


@router.delete("/api/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, str]:
    """Soft-delete a conversation (set is_active=false)."""
    pool = get_pool()
    user_id = user.get("user_id", "")

    result = await pool.execute(
        """UPDATE conversations SET is_active = FALSE
           WHERE id = $1 AND user_id = $2 AND is_active = TRUE""",
        conversation_id,
        user_id,
    )

    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"status": "deleted"}


# ── Marketplace Routes ────────────────────────────────────────


@router.get("/api/marketplace/agents")
async def list_agents(user: dict[str, Any] = Depends(require_auth)) -> list[dict[str, Any]]:
    """List available agents in the marketplace catalog."""
    pool = get_pool()

    rows = await pool.fetch(
        """SELECT id, name, display_name, description, category, icon, status,
                  version, capabilities, requires_approval, allowed_roles
           FROM agent_catalog
           WHERE status = 'active'
           ORDER BY display_name""",
    )

    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "display_name": r["display_name"],
            "description": r["description"],
            "category": r["category"],
            "icon": r["icon"],
            "status": r["status"],
            "version": r["version"],
            "capabilities": r["capabilities"] or [],
            "requires_approval": r["requires_approval"],
            "allowed_roles": r["allowed_roles"] or [],
        }
        for r in rows
    ]


@router.post("/api/marketplace/request")
async def submit_access_request(
    body: AccessRequestBody,
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Submit a request to access a specific agent."""
    pool = get_pool()
    user_id = user.get("user_id", "")

    # Verify agent exists
    agent = await pool.fetchrow(
        "SELECT name, requires_approval FROM agent_catalog WHERE name = $1 AND status = 'active'",
        body.agent_name,
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Check for existing pending request
    existing = await pool.fetchrow(
        """SELECT id FROM access_requests
           WHERE user_id = $1 AND agent_name = $2 AND status = 'pending'""",
        user_id,
        body.agent_name,
    )
    if existing:
        raise HTTPException(status_code=409, detail="You already have a pending request for this agent")

    # Check if already has permission
    perm = await pool.fetchrow(
        "SELECT id FROM agent_permissions WHERE user_id = $1 AND agent_name = $2",
        user_id,
        body.agent_name,
    )
    if perm:
        raise HTTPException(status_code=409, detail="You already have access to this agent")

    # If no approval required, auto-approve
    if not agent["requires_approval"]:
        await pool.execute(
            """INSERT INTO agent_permissions (user_id, agent_name, role)
               VALUES ($1, $2, $3)""",
            user_id,
            body.agent_name,
            body.role_requested,
        )

        row = await pool.fetchrow(
            """INSERT INTO access_requests (user_id, agent_name, role_requested, use_case, status, resolved_at)
               VALUES ($1, $2, $3, $4, 'approved', NOW())
               RETURNING id, status, created_at""",
            user_id,
            body.agent_name,
            body.role_requested,
            body.use_case,
        )

        return {
            "id": str(row["id"]),
            "agent_name": body.agent_name,
            "status": "approved",
            "message": "Access granted automatically — no approval required.",
        }

    # Create pending request
    row = await pool.fetchrow(
        """INSERT INTO access_requests (user_id, agent_name, role_requested, use_case)
           VALUES ($1, $2, $3, $4)
           RETURNING id, status, created_at""",
        user_id,
        body.agent_name,
        body.role_requested,
        body.use_case,
    )

    logger.info("marketplace.request user=%s agent=%s", user.get("sub"), body.agent_name)

    return {
        "id": str(row["id"]),
        "agent_name": body.agent_name,
        "status": "pending",
        "message": "Your request has been submitted and is pending admin approval.",
    }


@router.get("/api/marketplace/my-agents")
async def list_my_agents(user: dict[str, Any] = Depends(require_auth)) -> list[dict[str, Any]]:
    """List agents the authenticated user has been granted access to."""
    pool = get_pool()
    user_id = user.get("user_id", "")

    rows = await pool.fetch(
        """SELECT ap.agent_name, ap.role, ap.granted_at,
                  ac.display_name, ac.description, ac.category, ac.icon
           FROM agent_permissions ap
           JOIN agent_catalog ac ON ap.agent_name = ac.name
           WHERE ap.user_id = $1
           ORDER BY ap.granted_at DESC""",
        user_id,
    )

    return [
        {
            "agent_name": r["agent_name"],
            "display_name": r["display_name"],
            "description": r["description"],
            "category": r["category"],
            "icon": r["icon"],
            "role": r["role"],
            "granted_at": r["granted_at"].isoformat(),
        }
        for r in rows
    ]


# ── Admin Routes ──────────────────────────────────────────────


@router.get("/api/admin/requests")
async def list_pending_requests(admin: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
    """List all pending access requests (admin only)."""
    pool = get_pool()

    rows = await pool.fetch(
        """SELECT ar.id, ar.agent_name, ar.role_requested, ar.use_case,
                  ar.status, ar.created_at,
                  u.email, u.name as user_name, u.role as user_role
           FROM access_requests ar
           JOIN users u ON ar.user_id = u.id
           WHERE ar.status = 'pending'
           ORDER BY ar.created_at ASC""",
    )

    return [
        {
            "id": str(r["id"]),
            "agent_name": r["agent_name"],
            "role_requested": r["role_requested"],
            "use_case": r["use_case"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat(),
            "user_email": r["email"],
            "user_name": r["user_name"],
            "user_role": r["user_role"],
        }
        for r in rows
    ]


@router.post("/api/admin/requests/{request_id}/approve")
async def approve_request(
    request_id: str,
    body: AdminActionBody,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, str]:
    """Approve an access request (admin only)."""
    pool = get_pool()
    admin_id = admin.get("user_id", "")

    # Fetch the request
    req = await pool.fetchrow(
        """SELECT id, user_id, agent_name, role_requested, status
           FROM access_requests WHERE id = $1""",
        request_id,
    )
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    if req["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {req['status']}")

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Update request status
            await conn.execute(
                """UPDATE access_requests
                   SET status = 'approved', admin_notes = $1, reviewed_by = $2, resolved_at = NOW()
                   WHERE id = $3""",
                body.admin_notes,
                admin_id,
                request_id,
            )

            # Grant permission
            await conn.execute(
                """INSERT INTO agent_permissions (user_id, agent_name, role, granted_by)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (user_id, agent_name) DO UPDATE SET role = $3, granted_by = $4""",
                str(req["user_id"]),
                req["agent_name"],
                req["role_requested"],
                admin_id,
            )

    logger.info(
        "admin.approve request=%s agent=%s admin=%s",
        request_id, req["agent_name"], admin.get("sub"),
    )

    return {"status": "approved", "request_id": request_id}


@router.post("/api/admin/requests/{request_id}/deny")
async def deny_request(
    request_id: str,
    body: AdminActionBody,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, str]:
    """Deny an access request (admin only)."""
    pool = get_pool()
    admin_id = admin.get("user_id", "")

    req = await pool.fetchrow(
        "SELECT id, status, agent_name FROM access_requests WHERE id = $1",
        request_id,
    )
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    if req["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {req['status']}")

    await pool.execute(
        """UPDATE access_requests
           SET status = 'denied', admin_notes = $1, reviewed_by = $2, resolved_at = NOW()
           WHERE id = $3""",
        body.admin_notes,
        admin_id,
        request_id,
    )

    logger.info(
        "admin.deny request=%s agent=%s admin=%s",
        request_id, req["agent_name"], admin.get("sub"),
    )

    return {"status": "denied", "request_id": request_id}


@router.get("/api/admin/usage")
async def get_usage_stats(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Get aggregate usage statistics (admin only)."""
    pool = get_pool()

    # Overall stats
    overall = await pool.fetchrow(
        """SELECT
               COUNT(*) as total_requests,
               COUNT(DISTINCT user_id) as unique_users,
               SUM(tokens_in) as total_tokens_in,
               SUM(tokens_out) as total_tokens_out,
               AVG(duration_ms)::integer as avg_duration_ms,
               SUM(tool_calls_count) as total_tool_calls
           FROM usage_logs
           WHERE created_at >= NOW() - INTERVAL '30 days'""",
    )

    # Per-agent breakdown
    agent_rows = await pool.fetch(
        """SELECT
               agent_name,
               COUNT(*) as request_count,
               COUNT(DISTINCT user_id) as unique_users,
               SUM(tokens_in) as tokens_in,
               SUM(tokens_out) as tokens_out,
               AVG(duration_ms)::integer as avg_duration_ms,
               COUNT(*) FILTER (WHERE status = 'error') as error_count
           FROM usage_logs
           WHERE created_at >= NOW() - INTERVAL '30 days'
           GROUP BY agent_name
           ORDER BY request_count DESC""",
    )

    # Daily trend (last 7 days)
    daily_rows = await pool.fetch(
        """SELECT
               DATE(created_at) as day,
               COUNT(*) as request_count,
               COUNT(DISTINCT user_id) as unique_users
           FROM usage_logs
           WHERE created_at >= NOW() - INTERVAL '7 days'
           GROUP BY DATE(created_at)
           ORDER BY day DESC""",
    )

    return {
        "period": "last_30_days",
        "overall": {
            "total_requests": overall["total_requests"],
            "unique_users": overall["unique_users"],
            "total_tokens_in": overall["total_tokens_in"] or 0,
            "total_tokens_out": overall["total_tokens_out"] or 0,
            "avg_duration_ms": overall["avg_duration_ms"] or 0,
            "total_tool_calls": overall["total_tool_calls"] or 0,
        },
        "by_agent": [
            {
                "agent_name": r["agent_name"],
                "request_count": r["request_count"],
                "unique_users": r["unique_users"],
                "tokens_in": r["tokens_in"] or 0,
                "tokens_out": r["tokens_out"] or 0,
                "avg_duration_ms": r["avg_duration_ms"] or 0,
                "error_count": r["error_count"],
            }
            for r in agent_rows
        ],
        "daily_trend": [
            {
                "day": r["day"].isoformat(),
                "request_count": r["request_count"],
                "unique_users": r["unique_users"],
            }
            for r in daily_rows
        ],
    }


@router.get("/api/admin/audit")
async def get_audit_log(
    admin: dict[str, Any] = Depends(require_admin),
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get recent audit log from usage_logs and execution steps (admin only)."""
    pool = get_pool()

    # Clamp limit
    limit = min(limit, 200)

    rows = await pool.fetch(
        """SELECT
               ul.id, ul.agent_name, ul.input_summary, ul.tokens_in, ul.tokens_out,
               ul.tool_calls_count, ul.duration_ms, ul.status, ul.error_message,
               ul.trace_id, ul.created_at,
               u.email as user_email, u.name as user_name
           FROM usage_logs ul
           LEFT JOIN users u ON ul.user_id::uuid = u.id
           ORDER BY ul.created_at DESC
           LIMIT $1 OFFSET $2""",
        limit,
        offset,
    )

    # For each log entry, fetch execution steps
    entries = []
    for r in rows:
        steps = await pool.fetch(
            """SELECT step_index, tool_name, tool_input, tool_output, status, duration_ms
               FROM agent_execution_steps
               WHERE usage_log_id = $1
               ORDER BY step_index""",
            r["id"],
        )

        entries.append({
            "id": str(r["id"]),
            "agent_name": r["agent_name"],
            "user_email": r["user_email"],
            "user_name": r["user_name"],
            "input_summary": r["input_summary"],
            "tokens_in": r["tokens_in"],
            "tokens_out": r["tokens_out"],
            "tool_calls_count": r["tool_calls_count"],
            "duration_ms": r["duration_ms"],
            "status": r["status"],
            "error_message": r["error_message"],
            "trace_id": r["trace_id"],
            "created_at": r["created_at"].isoformat(),
            "steps": [
                {
                    "step_index": s["step_index"],
                    "tool_name": s["tool_name"],
                    "tool_input": s["tool_input"],
                    "tool_output": s["tool_output"],
                    "status": s["status"],
                    "duration_ms": s["duration_ms"],
                }
                for s in steps
            ],
        })

    total = await pool.fetchval("SELECT COUNT(*) FROM usage_logs")

    return {
        "entries": entries,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ── Product Routes ────────────────────────────────────────────


@router.get("/api/products")
async def list_products(
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    search: str | None = None,
    sort: str = "rating",
    limit: int = 50,
    offset: int = 0,
    _user: dict = Depends(require_auth),
):
    pool = get_pool()
    conditions = ["p.is_active = TRUE"]
    args: list = []
    idx = 1

    if category:
        conditions.append(f"p.category = ${idx}")
        args.append(category)
        idx += 1
    if min_price is not None:
        conditions.append(f"p.price >= ${idx}")
        args.append(min_price)
        idx += 1
    if max_price is not None:
        conditions.append(f"p.price <= ${idx}")
        args.append(max_price)
        idx += 1
    if search:
        conditions.append(f"(p.name ILIKE ${idx} OR p.description ILIKE ${idx})")
        args.append(f"%{search}%")
        idx += 1

    order = {
        "price_asc": "p.price ASC",
        "price_desc": "p.price DESC",
        "rating": "p.rating DESC",
        "newest": "p.created_at DESC",
        "name": "p.name ASC",
    }.get(sort, "p.rating DESC")

    where = " AND ".join(conditions)
    rows = await pool.fetch(
        f"""SELECT p.id, p.name, p.description, p.category, p.brand, p.price,
                   p.original_price, p.image_url, p.rating, p.review_count
            FROM products p WHERE {where}
            ORDER BY {order}
            LIMIT {limit} OFFSET {offset}""",
        *args,
    )
    total = await pool.fetchval(f"SELECT COUNT(*) FROM products p WHERE {where}", *args)
    categories = await pool.fetch("SELECT DISTINCT category FROM products WHERE is_active = TRUE ORDER BY category")

    return {
        "products": [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "description": r["description"][:200],
                "category": r["category"],
                "brand": r["brand"],
                "price": float(r["price"]),
                "original_price": float(r["original_price"]) if r["original_price"] else None,
                "image_url": r["image_url"],
                "rating": float(r["rating"]),
                "review_count": r["review_count"],
            }
            for r in rows
        ],
        "total": total,
        "categories": [r["category"] for r in categories],
    }


@router.get("/api/products/{product_id}")
async def get_product(product_id: str, _user: dict = Depends(require_auth)):
    pool = get_pool()
    row = await pool.fetchrow(
        """SELECT p.id, p.name, p.description, p.category, p.brand, p.price,
                  p.original_price, p.image_url, p.rating, p.review_count, p.specs
           FROM products p WHERE p.id = $1""",
        product_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")

    # Get stock status
    stock = await pool.fetch(
        """SELECT w.name, w.region, wi.quantity
           FROM warehouse_inventory wi
           JOIN warehouses w ON wi.warehouse_id = w.id
           WHERE wi.product_id = $1""",
        product_id,
    )
    total_stock = sum(r["quantity"] for r in stock)

    # Get recent reviews
    reviews = await pool.fetch(
        """SELECT r.id, r.rating, r.title, r.body, r.verified_purchase, r.created_at,
                  u.name as reviewer_name
           FROM reviews r
           JOIN users u ON r.user_id = u.id
           WHERE r.product_id = $1
           ORDER BY r.created_at DESC LIMIT 10""",
        product_id,
    )

    # Rating distribution
    dist = await pool.fetch(
        "SELECT rating, COUNT(*) as count FROM reviews WHERE product_id = $1 GROUP BY rating ORDER BY rating",
        product_id,
    )

    return {
        "id": str(row["id"]),
        "name": row["name"],
        "description": row["description"],
        "category": row["category"],
        "brand": row["brand"],
        "price": float(row["price"]),
        "original_price": float(row["original_price"]) if row["original_price"] else None,
        "image_url": row["image_url"],
        "rating": float(row["rating"]),
        "review_count": row["review_count"],
        "specs": json.loads(row["specs"]) if isinstance(row["specs"], str) else dict(row["specs"]) if row["specs"] else {},
        "in_stock": total_stock > 0,
        "total_stock": total_stock,
        "warehouses": [{"name": r["name"], "region": r["region"], "quantity": r["quantity"]} for r in stock],
        "reviews": [
            {
                "id": str(r["id"]),
                "rating": r["rating"],
                "title": r["title"],
                "body": r["body"],
                "verified": r["verified_purchase"],
                "reviewer": r["reviewer_name"],
                "date": r["created_at"].isoformat(),
            }
            for r in reviews
        ],
        "rating_distribution": {str(r["rating"]): r["count"] for r in dist},
    }


# ── Order Routes ──────────────────────────────────────────────


@router.get("/api/orders")
async def list_orders(
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
    user: dict = Depends(require_auth),
):
    pool = get_pool()
    email = current_user_email.get()
    conditions = ["u.email = $1"]
    args: list = [email]
    idx = 2

    if status:
        conditions.append(f"o.status = ${idx}")
        args.append(status)
        idx += 1

    where = " AND ".join(conditions)
    rows = await pool.fetch(
        f"""SELECT o.id, o.status, o.total, o.shipping_carrier, o.tracking_number,
                   o.created_at, COUNT(oi.id) as item_count
            FROM orders o
            JOIN users u ON o.user_id = u.id
            LEFT JOIN order_items oi ON oi.order_id = o.id
            WHERE {where}
            GROUP BY o.id
            ORDER BY o.created_at DESC
            LIMIT {limit} OFFSET {offset}""",
        *args,
    )
    total = await pool.fetchval(
        f"SELECT COUNT(*) FROM orders o JOIN users u ON o.user_id = u.id WHERE {where}", *args,
    )

    return {
        "orders": [
            {
                "id": str(r["id"]),
                "status": r["status"],
                "total": float(r["total"]),
                "carrier": r["shipping_carrier"],
                "tracking": r["tracking_number"],
                "item_count": r["item_count"],
                "date": r["created_at"].isoformat(),
            }
            for r in rows
        ],
        "total": total,
    }


@router.get("/api/orders/{order_id}")
async def get_order(order_id: str, user: dict = Depends(require_auth)):
    pool = get_pool()
    email = current_user_email.get()

    order = await pool.fetchrow(
        """SELECT o.id, o.status, o.total, o.shipping_address, o.billing_address,
                  o.shipping_carrier, o.tracking_number, o.coupon_code,
                  o.discount_amount, o.created_at
           FROM orders o
           JOIN users u ON o.user_id = u.id
           WHERE o.id = $1 AND u.email = $2""",
        order_id, email,
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    items = await pool.fetch(
        """SELECT oi.quantity, oi.unit_price, oi.subtotal,
                  p.id as product_id, p.name, p.category, p.image_url
           FROM order_items oi
           JOIN products p ON oi.product_id = p.id
           WHERE oi.order_id = $1""",
        order_id,
    )

    history = await pool.fetch(
        "SELECT status, notes, location, timestamp FROM order_status_history WHERE order_id = $1 ORDER BY timestamp",
        order_id,
    )

    ret = await pool.fetchrow(
        "SELECT id, reason, status, refund_method, refund_amount, return_label_url, created_at, resolved_at FROM returns WHERE order_id = $1",
        order_id,
    )

    return {
        "id": str(order["id"]),
        "status": order["status"],
        "total": float(order["total"]),
        "shipping_address": json.loads(order["shipping_address"]) if isinstance(order["shipping_address"], str) else dict(order["shipping_address"]) if order["shipping_address"] else {},
        "billing_address": json.loads(order["billing_address"]) if isinstance(order["billing_address"], str) else dict(order["billing_address"]) if order["billing_address"] else {},
        "carrier": order["shipping_carrier"],
        "tracking": order["tracking_number"],
        "coupon": order["coupon_code"],
        "discount": float(order["discount_amount"]) if order["discount_amount"] else 0,
        "date": order["created_at"].isoformat(),
        "items": [
            {
                "product_id": str(i["product_id"]),
                "name": i["name"],
                "category": i["category"],
                "image_url": i["image_url"],
                "quantity": i["quantity"],
                "unit_price": float(i["unit_price"]),
                "subtotal": float(i["subtotal"]),
            }
            for i in items
        ],
        "status_history": [
            {
                "status": h["status"],
                "notes": h["notes"],
                "location": h["location"],
                "timestamp": h["timestamp"].isoformat(),
            }
            for h in history
        ],
        "return": {
            "id": str(ret["id"]),
            "reason": ret["reason"],
            "status": ret["status"],
            "refund_method": ret["refund_method"],
            "refund_amount": float(ret["refund_amount"]) if ret["refund_amount"] else None,
            "return_label_url": ret["return_label_url"],
            "created_at": ret["created_at"].isoformat(),
            "resolved_at": ret["resolved_at"].isoformat() if ret["resolved_at"] else None,
        } if ret else None,
    }


@router.post("/api/orders/{order_id}/cancel")
async def cancel_order(order_id: str, body: CancelOrderRequest, user: dict = Depends(require_auth)):
    """Cancel a placed or confirmed order."""
    pool = get_pool()
    email = current_user_email.get()

    order = await pool.fetchrow(
        """SELECT o.id, o.status, o.total
           FROM orders o
           JOIN users u ON o.user_id = u.id
           WHERE o.id = $1 AND u.email = $2""",
        order_id, email,
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order["status"] not in ("placed", "confirmed"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel order with status '{order['status']}'. Only placed or confirmed orders can be cancelled.",
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE orders SET status = 'cancelled' WHERE id = $1",
                order_id,
            )
            await conn.execute(
                """INSERT INTO order_status_history (order_id, status, notes)
                   VALUES ($1, 'cancelled', $2)""",
                order_id,
                body.reason,
            )

    return {
        "order_id": str(order["id"]),
        "status": "cancelled",
        "refund_amount": float(order["total"]),
    }


@router.post("/api/orders/{order_id}/return")
async def return_order(order_id: str, body: ReturnOrderRequest, user: dict = Depends(require_auth)):
    """Request a return for a delivered order."""
    pool = get_pool()
    email = current_user_email.get()

    order = await pool.fetchrow(
        """SELECT o.id, o.status, o.total, o.user_id
           FROM orders o
           JOIN users u ON o.user_id = u.id
           WHERE o.id = $1 AND u.email = $2""",
        order_id, email,
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order["status"] != "delivered":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot return order with status '{order['status']}'. Only delivered orders can be returned.",
        )

    existing_return = await pool.fetchrow(
        "SELECT id FROM returns WHERE order_id = $1", order_id,
    )
    if existing_return:
        raise HTTPException(status_code=409, detail="A return has already been requested for this order")

    label_token = uuid.uuid4().hex[:12]
    return_label_url = f"/api/returns/{label_token}/label"

    async with pool.acquire() as conn:
        async with conn.transaction():
            ret = await conn.fetchrow(
                """INSERT INTO returns (order_id, user_id, reason, status, return_label_url, refund_method, refund_amount)
                   VALUES ($1, $2, $3, 'requested', $4, $5, $6)
                   RETURNING id""",
                order_id,
                str(order["user_id"]),
                body.reason,
                return_label_url,
                body.refund_method,
                float(order["total"]),
            )
            await conn.execute(
                "UPDATE orders SET status = 'returned' WHERE id = $1",
                order_id,
            )
            await conn.execute(
                """INSERT INTO order_status_history (order_id, status, notes)
                   VALUES ($1, 'returned', $2)""",
                order_id,
                body.reason,
            )

    return {
        "return_id": str(ret["id"]),
        "order_id": str(order["id"]),
        "status": "requested",
        "return_label_url": return_label_url,
        "refund_amount": float(order["total"]),
        "refund_method": body.refund_method,
    }


# ── Return Label PDF ─────────────────────────────────────────


@router.get("/api/returns/{label_token}/label")
async def get_return_label(label_token: str):
    """Generate a return shipping label PDF for the given token."""
    pool = get_pool()

    # Find the return by label URL pattern
    ret = await pool.fetchrow(
        """SELECT r.id, r.order_id, r.reason, r.status, r.return_label_url,
                  r.refund_method, r.refund_amount, r.created_at,
                  o.shipping_address, o.shipping_carrier,
                  u.name as user_name, u.email as user_email
           FROM returns r
           JOIN orders o ON r.order_id = o.id
           JOIN users u ON r.user_id = u.id
           WHERE r.return_label_url LIKE $1""",
        f"%{label_token}%",
    )
    if not ret:
        raise HTTPException(status_code=404, detail="Return label not found")

    address = ret["shipping_address"]
    if isinstance(address, str):
        address = json.loads(address)

    # Build a simple PDF using raw PDF syntax (no dependencies needed)
    user_name = ret["user_name"] or "Customer"
    user_email = ret["user_email"] or ""
    order_id = str(ret["order_id"])[:8]
    return_id = str(ret["id"])[:8]
    carrier = ret["shipping_carrier"] or "Standard Shipping"
    reason = ret["reason"] or "Return"
    created = ret["created_at"].strftime("%Y-%m-%d") if ret["created_at"] else ""
    addr_street = address.get("street", "") if address else ""
    addr_city = address.get("city", "") if address else ""
    addr_state = address.get("state", "") if address else ""
    addr_zip = address.get("zip", "") if address else ""

    barcode_text = f"RTN-{label_token.upper()}"

    pdf_content = _build_return_label_pdf(
        barcode=barcode_text,
        user_name=user_name,
        user_email=user_email,
        order_id=order_id,
        return_id=return_id,
        carrier=carrier,
        reason=reason,
        created=created,
        addr_street=addr_street,
        addr_city=addr_city,
        addr_state=addr_state,
        addr_zip=addr_zip,
    )

    from starlette.responses import Response
    return Response(
        content=pdf_content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="return-label-{label_token}.pdf"',
        },
    )


def _build_return_label_pdf(
    barcode: str,
    user_name: str,
    user_email: str,
    order_id: str,
    return_id: str,
    carrier: str,
    reason: str,
    created: str,
    addr_street: str,
    addr_city: str,
    addr_state: str,
    addr_zip: str,
) -> bytes:
    """Generate a minimal return shipping label as raw PDF (no external libs)."""
    # Page dimensions: Letter size (612 x 792 points)
    # This creates a clean, professional-looking return label

    def pdf_str(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    from_addr = f"{pdf_str(user_name)}\\n{pdf_str(addr_street)}\\n{pdf_str(addr_city)}, {pdf_str(addr_state)} {pdf_str(addr_zip)}"
    to_addr = "E-Commerce Agents Returns Center\\n1200 Returns Blvd, Suite 400\\nMemphis, TN 38118"

    # Build PDF objects
    objects = []

    # Object 1: Catalog
    objects.append("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj")

    # Object 2: Pages
    objects.append("2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj")

    # Object 3: Page
    objects.append(
        "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Contents 4 0 R /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>\nendobj"
    )

    # Object 5: Helvetica font
    objects.append("5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj")

    # Object 6: Helvetica-Bold font
    objects.append("6 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj")

    # Object 4: Page content stream
    stream_lines = [
        # --- Header bar ---
        "0.05 0.58 0.55 rg",  # Teal fill
        "0 742 612 50 re f",
        "1 1 1 rg",  # White text
        "BT /F2 20 Tf 30 760 Td (RETURN SHIPPING LABEL) Tj ET",

        # --- Barcode area ---
        "0.95 0.95 0.95 rg",
        "30 680 552 50 re f",
        "0 0 0 rg",
        f"BT /F2 16 Tf 180 700 Td ({pdf_str(barcode)}) Tj ET",
        f"BT /F1 9 Tf 30 685 Td (Scan or enter this code at drop-off) Tj ET",

        # --- Carrier ---
        "0 0 0 rg",
        f"BT /F2 12 Tf 30 655 Td (Carrier: {pdf_str(carrier)}) Tj ET",
        f"BT /F1 10 Tf 400 655 Td (Date: {pdf_str(created)}) Tj ET",

        # --- Divider ---
        "0.8 0.8 0.8 RG", "0.5 w", "30 640 m 582 640 l S",

        # --- FROM Section ---
        "0 0 0 rg",
        "BT /F2 11 Tf 30 620 Td (FROM:) Tj ET",
        f"BT /F1 11 Tf 30 605 Td ({pdf_str(user_name)}) Tj ET",
        f"BT /F1 10 Tf 30 591 Td ({pdf_str(addr_street)}) Tj ET",
        f"BT /F1 10 Tf 30 577 Td ({pdf_str(addr_city)}, {pdf_str(addr_state)} {pdf_str(addr_zip)}) Tj ET",
        f"BT /F1 9 Tf 30 562 Td ({pdf_str(user_email)}) Tj ET",

        # --- TO Section ---
        "BT /F2 11 Tf 320 620 Td (TO:) Tj ET",
        "BT /F1 11 Tf 320 605 Td (E-Commerce Agents Returns Center) Tj ET",
        "BT /F1 10 Tf 320 591 Td (1200 Returns Blvd, Suite 400) Tj ET",
        "BT /F1 10 Tf 320 577 Td (Memphis, TN 38118) Tj ET",

        # --- Divider ---
        "30 545 m 582 545 l S",

        # --- Return Details Box ---
        "0.97 0.97 0.97 rg",
        "30 460 552 75 re f",
        "0 0 0 rg",
        f"BT /F2 10 Tf 40 520 Td (Order ID:) Tj ET",
        f"BT /F1 10 Tf 140 520 Td (#{pdf_str(order_id)}...) Tj ET",
        f"BT /F2 10 Tf 40 504 Td (Return ID:) Tj ET",
        f"BT /F1 10 Tf 140 504 Td (#{pdf_str(return_id)}...) Tj ET",
        f"BT /F2 10 Tf 40 488 Td (Reason:) Tj ET",
        f"BT /F1 10 Tf 140 488 Td ({pdf_str(reason[:60])}) Tj ET",
        f"BT /F2 10 Tf 40 472 Td (Status:) Tj ET",
        f"BT /F1 10 Tf 140 472 Td (Return Requested) Tj ET",

        # --- Instructions Box ---
        "0.05 0.58 0.55 rg",  # Teal
        "30 380 552 60 re f",
        "1 1 1 rg",
        "BT /F2 12 Tf 40 420 Td (INSTRUCTIONS) Tj ET",
        "BT /F1 10 Tf 40 404 Td (1. Print this label and cut along the border.) Tj ET",
        "BT /F1 10 Tf 40 390 Td (2. Pack all items securely in the original packaging.) Tj ET",

        # Continue instructions below the box
        "0 0 0 rg",
        "BT /F1 10 Tf 40 360 Td (3. Attach this label to the outside of the package.) Tj ET",
        "BT /F1 10 Tf 40 346 Td (4. Drop off at any carrier location or schedule a pickup.) Tj ET",
        "BT /F1 10 Tf 40 332 Td (5. Your refund will be processed after we receive and inspect the items.) Tj ET",

        # --- Footer ---
        "0.6 0.6 0.6 rg",
        "BT /F1 8 Tf 30 50 Td (Generated by E-Commerce Agents | This label is valid for 30 days from the return request date.) Tj ET",
        f"BT /F1 8 Tf 30 38 Td (Label ID: {pdf_str(barcode)} | For support, contact support@ecommerce-agents.com) Tj ET",
    ]

    stream = "\n".join(stream_lines)
    stream_bytes = stream.encode("latin-1")

    objects.insert(3, f"4 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n{stream}\nendstream\nendobj")

    # Build the PDF file
    pdf_lines = ["%PDF-1.4"]
    offsets = []

    for obj in objects:
        offsets.append(len("\n".join(pdf_lines).encode("latin-1")) + 1)
        pdf_lines.append(obj)

    xref_offset = len("\n".join(pdf_lines).encode("latin-1")) + 1
    pdf_lines.append("xref")
    pdf_lines.append(f"0 {len(objects) + 1}")
    pdf_lines.append("0000000000 65535 f ")
    for off in offsets:
        pdf_lines.append(f"{off:010d} 00000 n ")

    pdf_lines.append("trailer")
    pdf_lines.append(f"<< /Size {len(objects) + 1} /Root 1 0 R >>")
    pdf_lines.append("startxref")
    pdf_lines.append(str(xref_offset))
    pdf_lines.append("%%EOF")

    return "\n".join(pdf_lines).encode("latin-1")


# ── Cart Routes ──────────────────────────────────────────────


@router.get("/api/cart")
async def get_cart(user: dict = Depends(require_auth)):
    """Get the current user's shopping cart with items."""
    pool = get_pool()
    user_id = user.get("user_id", "")

    # Lazy-create cart for user
    cart = await pool.fetchrow(
        """SELECT id, coupon_code, discount_amount, shipping_address, billing_address,
                  billing_same_as_shipping
           FROM carts WHERE user_id = $1""",
        user_id,
    )
    if not cart:
        cart = await pool.fetchrow(
            """INSERT INTO carts (user_id) VALUES ($1)
               RETURNING id, coupon_code, discount_amount, shipping_address, billing_address,
                         billing_same_as_shipping""",
            user_id,
        )

    cart_id = str(cart["id"])

    # Fetch items with product details and stock
    items = await pool.fetch(
        """SELECT ci.id, ci.product_id, ci.quantity,
                  p.name, p.brand, p.category, p.price, p.original_price, p.image_url,
                  COALESCE((SELECT SUM(wi.quantity) FROM warehouse_inventory wi WHERE wi.product_id = ci.product_id), 0) as available_qty
           FROM cart_items ci
           JOIN products p ON ci.product_id = p.id
           WHERE ci.cart_id = $1
           ORDER BY ci.added_at""",
        cart_id,
    )

    subtotal = sum(float(i["price"]) * i["quantity"] for i in items)
    discount_amount = float(cart["discount_amount"]) if cart["discount_amount"] else 0
    total = max(subtotal - discount_amount, 0)

    return {
        "id": cart_id,
        "items": [
            {
                "id": str(i["id"]),
                "product_id": str(i["product_id"]),
                "name": i["name"],
                "brand": i["brand"],
                "category": i["category"],
                "price": float(i["price"]),
                "original_price": float(i["original_price"]) if i["original_price"] else None,
                "quantity": i["quantity"],
                "subtotal": round(float(i["price"]) * i["quantity"], 2),
                "image_url": i["image_url"],
                "in_stock": i["available_qty"] > 0,
                "available_qty": i["available_qty"],
            }
            for i in items
        ],
        "item_count": sum(i["quantity"] for i in items),
        "subtotal": round(subtotal, 2),
        "discount_amount": discount_amount,
        "coupon_code": cart["coupon_code"],
        "total": round(total, 2),
        "shipping_address": json.loads(cart["shipping_address"]) if isinstance(cart["shipping_address"], str) else dict(cart["shipping_address"]) if cart["shipping_address"] else None,
        "billing_address": json.loads(cart["billing_address"]) if isinstance(cart["billing_address"], str) else dict(cart["billing_address"]) if cart["billing_address"] else None,
        "billing_same_as_shipping": cart["billing_same_as_shipping"] if cart["billing_same_as_shipping"] is not None else True,
    }


@router.post("/api/cart/items")
async def add_cart_item(body: AddToCartRequest, user: dict = Depends(require_auth)):
    """Add an item to the cart or increase its quantity."""
    pool = get_pool()
    user_id = user.get("user_id", "")

    # Validate product exists and is active
    product = await pool.fetchrow(
        "SELECT id, is_active FROM products WHERE id = $1", body.product_id,
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    if not product["is_active"]:
        raise HTTPException(status_code=400, detail="Product is no longer available")

    # Get or create cart
    cart = await pool.fetchrow("SELECT id FROM carts WHERE user_id = $1", user_id)
    if not cart:
        cart = await pool.fetchrow(
            "INSERT INTO carts (user_id) VALUES ($1) RETURNING id", user_id,
        )
    cart_id = str(cart["id"])

    # Upsert item
    await pool.execute(
        """INSERT INTO cart_items (cart_id, product_id, quantity)
           VALUES ($1, $2, $3)
           ON CONFLICT (cart_id, product_id)
           DO UPDATE SET quantity = cart_items.quantity + EXCLUDED.quantity""",
        cart_id,
        body.product_id,
        body.quantity,
    )

    # Touch cart updated_at
    await pool.execute("UPDATE carts SET updated_at = NOW() WHERE id = $1", cart_id)

    return {"status": "added", "product_id": body.product_id, "quantity": body.quantity}


@router.put("/api/cart/items/{item_id}")
async def update_cart_item(item_id: str, body: UpdateCartItemRequest, user: dict = Depends(require_auth)):
    """Update the quantity of a cart item."""
    pool = get_pool()
    user_id = user.get("user_id", "")

    # Verify item belongs to user's cart
    item = await pool.fetchrow(
        """SELECT ci.id, ci.cart_id
           FROM cart_items ci
           JOIN carts c ON ci.cart_id = c.id
           WHERE ci.id = $1 AND c.user_id = $2""",
        item_id, user_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Cart item not found")

    if body.quantity <= 0:
        await pool.execute("DELETE FROM cart_items WHERE id = $1", item_id)
    else:
        await pool.execute(
            "UPDATE cart_items SET quantity = $1 WHERE id = $2",
            body.quantity, item_id,
        )

    await pool.execute("UPDATE carts SET updated_at = NOW() WHERE id = $1", str(item["cart_id"]))

    return {"status": "updated"}


@router.delete("/api/cart/items/{item_id}")
async def remove_cart_item(item_id: str, user: dict = Depends(require_auth)):
    """Remove an item from the cart."""
    pool = get_pool()
    user_id = user.get("user_id", "")

    item = await pool.fetchrow(
        """SELECT ci.id, ci.cart_id
           FROM cart_items ci
           JOIN carts c ON ci.cart_id = c.id
           WHERE ci.id = $1 AND c.user_id = $2""",
        item_id, user_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Cart item not found")

    await pool.execute("DELETE FROM cart_items WHERE id = $1", item_id)
    await pool.execute("UPDATE carts SET updated_at = NOW() WHERE id = $1", str(item["cart_id"]))

    return {"status": "removed"}


@router.post("/api/cart/coupon")
async def apply_coupon(body: ApplyCouponRequest, user: dict = Depends(require_auth)):
    """Validate and apply a coupon code to the cart."""
    pool = get_pool()
    user_id = user.get("user_id", "")
    email = current_user_email.get()

    # Get user's cart
    cart = await pool.fetchrow("SELECT id FROM carts WHERE user_id = $1", user_id)
    if not cart:
        raise HTTPException(status_code=400, detail="Cart not found")
    cart_id = str(cart["id"])

    # Calculate current cart subtotal
    items = await pool.fetch(
        """SELECT ci.quantity, p.price
           FROM cart_items ci
           JOIN products p ON ci.product_id = p.id
           WHERE ci.cart_id = $1""",
        cart_id,
    )
    if not items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    subtotal = sum(float(i["price"]) * i["quantity"] for i in items)

    # Validate coupon
    coupon = await pool.fetchrow(
        """SELECT id, code, description, discount_type, discount_value,
                  min_spend, max_discount, usage_limit, times_used,
                  valid_from, valid_until, user_specific_email, is_active
           FROM coupons WHERE code = $1""",
        body.code.upper(),
    )
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")
    if not coupon["is_active"]:
        raise HTTPException(status_code=400, detail="Coupon is no longer active")
    if coupon["valid_until"] and coupon["valid_until"].timestamp() < time.time():
        raise HTTPException(status_code=400, detail="Coupon has expired")
    if coupon["usage_limit"] and coupon["times_used"] >= coupon["usage_limit"]:
        raise HTTPException(status_code=400, detail="Coupon usage limit reached")
    if coupon["min_spend"] and subtotal < float(coupon["min_spend"]):
        raise HTTPException(
            status_code=400,
            detail=f"Minimum spend of ${float(coupon['min_spend']):.2f} required",
        )
    if coupon["user_specific_email"] and coupon["user_specific_email"] != email:
        raise HTTPException(status_code=400, detail="This coupon is not valid for your account")

    # Calculate discount
    if coupon["discount_type"] == "percentage":
        discount = subtotal * float(coupon["discount_value"]) / 100
        if coupon["max_discount"]:
            discount = min(discount, float(coupon["max_discount"]))
    else:
        discount = float(coupon["discount_value"])

    discount = round(min(discount, subtotal), 2)

    # Apply to cart
    await pool.execute(
        "UPDATE carts SET coupon_code = $1, discount_amount = $2, updated_at = NOW() WHERE id = $3",
        coupon["code"], discount, cart_id,
    )

    return {
        "status": "applied",
        "code": coupon["code"],
        "discount_amount": discount,
        "description": coupon["description"],
    }


@router.delete("/api/cart/coupon")
async def remove_coupon(user: dict = Depends(require_auth)):
    """Remove the applied coupon from the cart."""
    pool = get_pool()
    user_id = user.get("user_id", "")

    await pool.execute(
        "UPDATE carts SET coupon_code = NULL, discount_amount = 0, updated_at = NOW() WHERE user_id = $1",
        user_id,
    )

    return {"status": "removed"}


@router.put("/api/cart/address")
async def update_cart_address(body: CartAddressRequest, user: dict = Depends(require_auth)):
    """Update shipping and/or billing address on the cart."""
    pool = get_pool()
    user_id = user.get("user_id", "")

    # Get or create cart
    cart = await pool.fetchrow("SELECT id FROM carts WHERE user_id = $1", user_id)
    if not cart:
        cart = await pool.fetchrow(
            "INSERT INTO carts (user_id) VALUES ($1) RETURNING id", user_id,
        )
    cart_id = str(cart["id"])

    shipping = json.dumps(body.shipping_address) if body.shipping_address else None
    billing = json.dumps(body.billing_address) if body.billing_address else None

    if body.billing_same_as_shipping and body.shipping_address:
        billing = shipping

    await pool.execute(
        """UPDATE carts
           SET shipping_address = COALESCE($1::jsonb, shipping_address),
               billing_address = COALESCE($2::jsonb, billing_address),
               billing_same_as_shipping = $3,
               updated_at = NOW()
           WHERE id = $4""",
        shipping, billing, body.billing_same_as_shipping, cart_id,
    )

    return {"status": "updated"}


# ── Checkout Route ───────────────────────────────────────────


@router.post("/api/checkout")
async def checkout(body: CheckoutRequest, user: dict = Depends(require_auth)):
    """Process checkout: validate cart, create order, decrement inventory, clear cart."""
    pool = get_pool()
    user_id = user.get("user_id", "")

    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Fetch cart
            cart = await conn.fetchrow(
                "SELECT id, coupon_code, discount_amount FROM carts WHERE user_id = $1",
                user_id,
            )
            if not cart:
                raise HTTPException(status_code=400, detail="No cart found")
            cart_id = str(cart["id"])

            # 2. Fetch cart items with product info
            items = await conn.fetch(
                """SELECT ci.id, ci.product_id, ci.quantity,
                          p.name, p.price, p.is_active
                   FROM cart_items ci
                   JOIN products p ON ci.product_id = p.id
                   WHERE ci.cart_id = $1""",
                cart_id,
            )
            if not items:
                raise HTTPException(status_code=400, detail="Cart is empty")

            # 3. Validate all products are active
            inactive = [i["name"] for i in items if not i["is_active"]]
            if inactive:
                raise HTTPException(
                    status_code=400,
                    detail=f"The following products are no longer available: {', '.join(inactive)}",
                )

            # 4. Check stock for each item
            for item in items:
                stock = await conn.fetchval(
                    "SELECT COALESCE(SUM(quantity), 0) FROM warehouse_inventory WHERE product_id = $1",
                    str(item["product_id"]),
                )
                if stock < item["quantity"]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Insufficient stock for '{item['name']}'. Available: {stock}, requested: {item['quantity']}",
                    )

            # 5. Calculate subtotal
            subtotal = sum(float(i["price"]) * i["quantity"] for i in items)

            # 6. Handle coupon discount
            coupon_discount = 0.0
            coupon_code = cart["coupon_code"]
            if coupon_code:
                coupon = await conn.fetchrow(
                    """SELECT discount_type, discount_value, max_discount, is_active,
                              valid_until, usage_limit, times_used, min_spend
                       FROM coupons WHERE code = $1""",
                    coupon_code,
                )
                if coupon and coupon["is_active"]:
                    valid = True
                    if coupon["valid_until"] and coupon["valid_until"].timestamp() < time.time():
                        valid = False
                    if coupon["usage_limit"] and coupon["times_used"] >= coupon["usage_limit"]:
                        valid = False
                    if coupon["min_spend"] and subtotal < float(coupon["min_spend"]):
                        valid = False

                    if valid:
                        if coupon["discount_type"] == "percentage":
                            coupon_discount = subtotal * float(coupon["discount_value"]) / 100
                            if coupon["max_discount"]:
                                coupon_discount = min(coupon_discount, float(coupon["max_discount"]))
                        else:
                            coupon_discount = float(coupon["discount_value"])

                        # Increment usage
                        await conn.execute(
                            "UPDATE coupons SET times_used = times_used + 1 WHERE code = $1",
                            coupon_code,
                        )
                    else:
                        coupon_code = None
                else:
                    coupon_code = None

            # 7. Handle loyalty discount
            loyalty_discount = 0.0
            loyalty_row = await conn.fetchrow(
                """SELECT lt.discount_pct
                   FROM users u
                   JOIN loyalty_tiers lt ON lt.name = u.loyalty_tier
                   WHERE u.id = $1""",
                user_id,
            )
            if loyalty_row and loyalty_row["discount_pct"]:
                loyalty_discount = subtotal * float(loyalty_row["discount_pct"]) / 100

            # 8. Final total
            total = max(subtotal - coupon_discount - loyalty_discount, 0)
            total = round(total, 2)

            # 9. Resolve billing address
            shipping_address = json.dumps(body.shipping_address)
            if body.billing_same_as_shipping or not body.billing_address:
                billing_address = shipping_address
            else:
                billing_address = json.dumps(body.billing_address)

            # 10. Pick a carrier
            carrier = await conn.fetchrow(
                "SELECT id, name FROM carriers ORDER BY base_rate LIMIT 1",
            )
            carrier_name = carrier["name"] if carrier else "Standard Shipping"

            # 11. Generate tracking number
            tracking = f"TRK-{uuid.uuid4().hex[:12].upper()}"

            # 12. Insert order
            total_discount = round(coupon_discount + loyalty_discount, 2)
            order = await conn.fetchrow(
                """INSERT INTO orders (user_id, status, total, shipping_address, billing_address,
                                       shipping_carrier, tracking_number, coupon_code, discount_amount)
                   VALUES ($1, 'placed', $2, $3::jsonb, $4::jsonb, $5, $6, $7, $8)
                   RETURNING id""",
                user_id, total, shipping_address, billing_address,
                carrier_name, tracking, coupon_code, total_discount,
            )
            order_id = str(order["id"])

            # 13. Insert order items
            for item in items:
                item_subtotal = round(float(item["price"]) * item["quantity"], 2)
                await conn.execute(
                    """INSERT INTO order_items (order_id, product_id, quantity, unit_price, subtotal)
                       VALUES ($1, $2, $3, $4, $5)""",
                    order_id, str(item["product_id"]), item["quantity"],
                    float(item["price"]), item_subtotal,
                )

            # 14. Insert order status history
            await conn.execute(
                """INSERT INTO order_status_history (order_id, status, notes)
                   VALUES ($1, 'placed', 'Order placed via checkout')""",
                order_id,
            )

            # 15. Decrement warehouse inventory
            for item in items:
                remaining = item["quantity"]
                warehouses = await conn.fetch(
                    """SELECT warehouse_id, product_id, quantity FROM warehouse_inventory
                       WHERE product_id = $1 AND quantity > 0
                       ORDER BY quantity DESC""",
                    str(item["product_id"]),
                )
                for wh in warehouses:
                    if remaining <= 0:
                        break
                    deduct = min(remaining, wh["quantity"])
                    await conn.execute(
                        "UPDATE warehouse_inventory SET quantity = quantity - $1 WHERE warehouse_id = $2 AND product_id = $3",
                        deduct, wh["warehouse_id"], wh["product_id"],
                    )
                    remaining -= deduct

            # 16. Update user total_spend
            await conn.execute(
                "UPDATE users SET total_spend = total_spend + $1 WHERE id = $2",
                total, user_id,
            )

            # 17. Clear cart
            await conn.execute("DELETE FROM cart_items WHERE cart_id = $1", cart_id)
            await conn.execute(
                """UPDATE carts
                   SET coupon_code = NULL, discount_amount = 0, notes = NULL, updated_at = NOW()
                   WHERE id = $1""",
                cart_id,
            )

    return {
        "order_id": order_id,
        "total": total,
        "item_count": len(items),
        "status": "placed",
        "tracking_number": tracking,
        "carrier": carrier_name,
    }


# ── Profile Routes ────────────────────────────────────────────


@router.get("/api/profile")
async def get_profile(user: dict = Depends(require_auth)):
    pool = get_pool()
    email = current_user_email.get()
    row = await pool.fetchrow(
        """SELECT u.id, u.email, u.name, u.role, u.loyalty_tier, u.total_spend, u.created_at,
                  lt.discount_pct, lt.free_shipping_threshold, lt.priority_support
           FROM users u
           LEFT JOIN loyalty_tiers lt ON lt.name = u.loyalty_tier
           WHERE u.email = $1""",
        email,
    )
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    order_count = await pool.fetchval(
        "SELECT COUNT(*) FROM orders o JOIN users u ON o.user_id = u.id WHERE u.email = $1",
        email,
    )
    review_count = await pool.fetchval(
        "SELECT COUNT(*) FROM reviews r JOIN users u ON r.user_id = u.id WHERE u.email = $1",
        email,
    )

    return {
        "id": str(row["id"]),
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "loyalty_tier": row["loyalty_tier"],
        "total_spend": float(row["total_spend"]),
        "member_since": row["created_at"].isoformat(),
        "order_count": order_count,
        "review_count": review_count,
        "tier_benefits": {
            "discount_pct": float(row["discount_pct"]) if row["discount_pct"] else 0,
            "free_shipping_threshold": float(row["free_shipping_threshold"]) if row["free_shipping_threshold"] else None,
            "priority_support": row["priority_support"] if row["priority_support"] is not None else False,
        },
    }


# ── Seller Routes ────────────────────────────────────────────


@router.get("/api/seller/products")
async def list_seller_products(
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: dict[str, Any] = Depends(require_seller),
) -> dict[str, Any]:
    """List products owned by the authenticated seller."""
    pool = get_pool()
    user_id = user.get("user_id", "")

    conditions = ["p.seller_id = $1"]
    args: list = [user_id]
    idx = 2

    if category:
        conditions.append(f"p.category = ${idx}")
        args.append(category)
        idx += 1

    where = " AND ".join(conditions)
    rows = await pool.fetch(
        f"""SELECT p.id, p.name, p.description, p.category, p.brand, p.price,
                   p.original_price, p.image_url, p.rating, p.review_count, p.is_active
            FROM products p WHERE {where}
            ORDER BY p.created_at DESC
            LIMIT {limit} OFFSET {offset}""",
        *args,
    )
    total = await pool.fetchval(f"SELECT COUNT(*) FROM products p WHERE {where}", *args)

    return {
        "products": [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "description": r["description"][:200],
                "category": r["category"],
                "brand": r["brand"],
                "price": float(r["price"]),
                "original_price": float(r["original_price"]) if r["original_price"] else None,
                "image_url": r["image_url"],
                "rating": float(r["rating"]),
                "review_count": r["review_count"],
                "is_active": r["is_active"],
            }
            for r in rows
        ],
        "total": total,
    }


@router.get("/api/seller/orders")
async def list_seller_orders(
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
    user: dict[str, Any] = Depends(require_seller),
) -> dict[str, Any]:
    """List orders containing the authenticated seller's products."""
    pool = get_pool()
    user_id = user.get("user_id", "")

    conditions = ["p.seller_id = $1"]
    args: list = [user_id]
    idx = 2

    if status:
        conditions.append(f"o.status = ${idx}")
        args.append(status)
        idx += 1

    where = " AND ".join(conditions)
    rows = await pool.fetch(
        f"""SELECT DISTINCT o.id, o.status, o.total, o.created_at,
                   buyer.name as buyer_name, buyer.email as buyer_email,
                   COUNT(oi.id) as item_count
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.id
            JOIN products p ON oi.product_id = p.id
            JOIN users buyer ON o.user_id = buyer.id
            WHERE {where}
            GROUP BY o.id, o.status, o.total, o.created_at, buyer.name, buyer.email
            ORDER BY o.created_at DESC
            LIMIT {limit} OFFSET {offset}""",
        *args,
    )
    total = await pool.fetchval(
        f"""SELECT COUNT(DISTINCT o.id)
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.id
            JOIN products p ON oi.product_id = p.id
            WHERE {where}""",
        *args,
    )

    return {
        "orders": [
            {
                "id": str(r["id"]),
                "status": r["status"],
                "total": float(r["total"]),
                "date": r["created_at"].isoformat(),
                "buyer_name": r["buyer_name"],
                "buyer_email": r["buyer_email"],
                "item_count": r["item_count"],
            }
            for r in rows
        ],
        "total": total,
    }


@router.get("/api/seller/stats")
async def get_seller_stats(user: dict[str, Any] = Depends(require_seller)) -> dict[str, Any]:
    """Get aggregate sales statistics for the authenticated seller."""
    pool = get_pool()
    user_id = user.get("user_id", "")

    product_count = await pool.fetchval(
        "SELECT COUNT(*) FROM products WHERE seller_id = $1", user_id,
    )
    total_revenue = await pool.fetchval(
        """SELECT COALESCE(SUM(oi.subtotal), 0)
           FROM order_items oi
           JOIN products p ON oi.product_id = p.id
           WHERE p.seller_id = $1""",
        user_id,
    )
    order_count = await pool.fetchval(
        """SELECT COUNT(DISTINCT o.id)
           FROM orders o
           JOIN order_items oi ON oi.order_id = o.id
           JOIN products p ON oi.product_id = p.id
           WHERE p.seller_id = $1""",
        user_id,
    )
    avg_rating = await pool.fetchval(
        "SELECT COALESCE(AVG(rating), 0) FROM products WHERE seller_id = $1",
        user_id,
    )

    return {
        "product_count": product_count,
        "total_revenue": float(total_revenue),
        "order_count": order_count,
        "avg_rating": round(float(avg_rating), 2),
    }
