"""Orchestrator API routes — auth, chat, conversations, marketplace, admin."""

from __future__ import annotations

import logging
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from shared.context import current_user_email, current_user_role, current_session_id
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

    # Call the orchestrator agent using direct chat completions API
    from orchestrator.agent import ORCHESTRATOR_TOOLS
    from orchestrator.prompts import SYSTEM_PROMPT
    from shared.agent_host import _run_agent_with_tools

    agents_involved: list[str] = ["orchestrator"]

    with UsageTimer() as timer:
        try:
            response_text = await _run_agent_with_tools(
                system_prompt=SYSTEM_PROMPT,
                tools=ORCHESTRATOR_TOOLS,
                user_message=body.message,
                history=history,
                user_context=user_context,
            )
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

    import json
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
        """SELECT o.id, o.status, o.total, o.shipping_address, o.shipping_carrier,
                  o.tracking_number, o.coupon_code, o.discount_amount, o.created_at
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
        "SELECT id, reason, status, refund_method, refund_amount, created_at, resolved_at FROM returns WHERE order_id = $1",
        order_id,
    )

    import json
    return {
        "id": str(order["id"]),
        "status": order["status"],
        "total": float(order["total"]),
        "shipping_address": json.loads(order["shipping_address"]) if isinstance(order["shipping_address"], str) else dict(order["shipping_address"]) if order["shipping_address"] else {},
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
            "created_at": ret["created_at"].isoformat(),
            "resolved_at": ret["resolved_at"].isoformat() if ret["resolved_at"] else None,
        } if ret else None,
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
