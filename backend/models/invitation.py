from pydantic import BaseModel, Field, EmailStr, ConfigDict
from typing import Optional
from datetime import datetime, timezone, timedelta
import uuid
import secrets


class InvitationCreate(BaseModel):
    email: EmailStr
    role: str = "user"


class Invitation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    organizationId: str
    email: str
    role: str = "user"
    token: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    status: str = "pending"
    invitedBy: str
    expiresAt: str = Field(default_factory=lambda: (datetime.now(timezone.utc) + timedelta(days=7)).isoformat())
    createdAt: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AcceptInvitationRequest(BaseModel):
    """Body model for POST /team/accept-invite.

    Previously the password was passed as a query parameter, which leaks it
    into access logs, browser history and proxy logs.
    """
    token: str
    password: str = Field(min_length=8)
    firstName: Optional[str] = None
    lastName: Optional[str] = None


class MemberRoleUpdate(BaseModel):
    """Body model for PATCH /team/members/{member_id}/role.

    The role previously arrived as a ``?role=`` query parameter. A query
    parameter is the wrong transport for a state change: it is validated only
    inside the handler (after the request has been routed and logged), it leaks
    into access/proxy logs and browser history, and it advertises no request
    body in OpenAPI. A body model is validated by FastAPI before the handler
    runs and keeps the payload out of the URL.
    """
    role: str


class InvitationResponse(BaseModel):
    id: str
    email: str
    role: str
    status: str
    expiresAt: str
    createdAt: str
