from fastapi import APIRouter, HTTPException, status, Depends
from datetime import datetime, timedelta, timezone
import logging

from models.user import (
    User,
    UserCreate,
    UserLogin,
    UserResponse,
    TokenResponse,
    ForgotPasswordRequest,
    ResetPasswordRequest,
)
from models.organization import Organization
from utils.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
    init_identity_store,
)
from utils.invite_tokens import generate_invite_token, hash_invite_token
from utils.rbac import (
    auth_rate_limit,
    ROLE_ADMIN,
)
from services.email_service import send_email
from utils.env import get_env

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])

db = None
_RESET_TTL = timedelta(hours=1)
_FORGOT_RESPONSE = {
    "message": "If an account exists for that email, a reset link is on its way."
}
_REGISTER_TAKEN = "Could not create an account with that email. If you already have one, sign in."


def _session_version(user_doc: dict) -> int:
    try:
        return int(user_doc.get("sessionVersion") or 0)
    except (TypeError, ValueError):
        return 0


def init_db(database):
    global db
    db = database
    # The authorisation dependency must resolve identity/role from the same
    # database, otherwise a demoted or deactivated account keeps its old
    # privileges until its token expires.
    init_identity_store(database)


@router.post(
    "/register",
    response_model=TokenResponse,
    dependencies=[Depends(auth_rate_limit("auth:register"))],
)
async def register(user_data: UserCreate):
    """Register a new user and create their organization."""
    existing_user = await db.users.find_one({"email": user_data.email})
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_REGISTER_TAKEN
        )

    org_name = user_data.organizationName or f"{user_data.email.split('@')[0]}'s Organization"
    org = Organization(name=org_name)
    org_doc = org.model_dump()
    await db.organizations.insert_one(org_doc)

    # The account that creates the organisation is its owner (admin). Every
    # other account is created through the invitation flow with an explicit,
    # validated role.
    user = User(
        email=user_data.email,
        passwordHash=hash_password(user_data.password),
        firstName=user_data.firstName,
        lastName=user_data.lastName,
        role=ROLE_ADMIN,
        organizationId=org.id
    )
    user_doc = user.model_dump()
    await db.users.insert_one(user_doc)

    access_token = create_access_token(
        data={
            "sub": user.id,
            "email": user.email,
            "role": user.role,
            "organizationId": user.organizationId,
            "sv": 0,
        }
    )

    user_response = UserResponse(
        id=user.id,
        email=user.email,
        firstName=user.firstName,
        lastName=user.lastName,
        role=user.role,
        organizationId=user.organizationId,
        isActive=user.isActive,
        lastLogin=user.lastLogin,
        createdAt=user.createdAt
    )

    return TokenResponse(access_token=access_token, user=user_response)


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(auth_rate_limit("auth:login"))],
)
async def login(credentials: UserLogin):
    """Authenticate a user and return access token."""
    user_doc = await db.users.find_one({"email": credentials.email})
    if not user_doc:
        # Identical response to a wrong password: do not leak which emails exist.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )

    if not verify_password(credentials.password, user_doc["passwordHash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )

    if not user_doc.get("isActive", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated"
        )

    await db.users.update_one(
        {"id": user_doc["id"]},
        {"$set": {"lastLogin": datetime.now(timezone.utc).isoformat()}}
    )

    access_token = create_access_token(
        data={
            "sub": user_doc["id"],
            "email": user_doc["email"],
            "role": user_doc["role"],
            "organizationId": user_doc.get("organizationId"),
            "sv": _session_version(user_doc),
        }
    )

    user_response = UserResponse(
        id=user_doc["id"],
        email=user_doc["email"],
        firstName=user_doc.get("firstName"),
        lastName=user_doc.get("lastName"),
        role=user_doc["role"],
        organizationId=user_doc.get("organizationId"),
        isActive=user_doc.get("isActive", True),
        lastLogin=datetime.now(timezone.utc).isoformat(),
        createdAt=user_doc["createdAt"]
    )

    return TokenResponse(access_token=access_token, user=user_response)


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: dict = Depends(get_current_user)):
    """Get current authenticated user's information."""
    user_doc = await db.users.find_one({"id": current_user["sub"]})
    if not user_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    return UserResponse(
        id=user_doc["id"],
        email=user_doc["email"],
        firstName=user_doc.get("firstName"),
        lastName=user_doc.get("lastName"),
        role=user_doc["role"],
        organizationId=user_doc.get("organizationId"),
        isActive=user_doc.get("isActive", True),
        lastLogin=user_doc.get("lastLogin"),
        createdAt=user_doc["createdAt"]
    )


@router.post("/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    """Revoke this access token. Other sessions stay valid until they expire."""
    jti = current_user.get("jti")
    exp = current_user.get("exp")
    if isinstance(exp, datetime):
        expires_at = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
    elif isinstance(exp, (int, float)):
        expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
    else:
        expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    if isinstance(jti, str) and jti:
        await db.revoked_tokens.update_one(
            {"jti": jti},
            {"$set": {"jti": jti, "userId": current_user.get("sub"), "exp": expires_at}},
            upsert=True,
        )
    return {"message": "Successfully logged out"}


@router.post(
    "/forgot-password",
    dependencies=[Depends(auth_rate_limit("auth:forgot-password"))],
)
async def forgot_password(body: ForgotPasswordRequest):
    """Always the same response, whether or not the email is registered."""
    user_doc = await db.users.find_one({"email": str(body.email)})
    if user_doc and user_doc.get("isActive", True):
        raw, token_hash = generate_invite_token()
        expires = (datetime.now(timezone.utc) + _RESET_TTL).isoformat()
        await db.users.update_one(
            {"id": user_doc["id"]},
            {"$set": {"passwordResetHash": token_hash, "passwordResetExpires": expires}},
        )
        app_url = (get_env("APP_URL", "") or "").rstrip("/")
        reset_url = f"{app_url}/reset-password?token={raw}" if app_url else f"/reset-password?token={raw}"
        await send_email(
            to_email=user_doc["email"],
            template_type="password_reset",
            data={"reset_url": reset_url},
        )
    return _FORGOT_RESPONSE


@router.post(
    "/reset-password",
    dependencies=[Depends(auth_rate_limit("auth:reset-password"))],
)
async def reset_password(body: ResetPasswordRequest):
    """Set a new password and sign every existing session out."""
    token_hash = hash_invite_token(body.token)
    user_doc = await db.users.find_one({"passwordResetHash": token_hash})
    if not user_doc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset link")
    expires_raw = user_doc.get("passwordResetExpires")
    try:
        expires = datetime.fromisoformat(str(expires_raw).replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        expires = datetime.now(timezone.utc) - timedelta(seconds=1)
    if datetime.now(timezone.utc) > expires:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset link")

    now = datetime.now(timezone.utc).isoformat()
    result = await db.users.update_one(
        {"id": user_doc["id"], "passwordResetHash": token_hash},
        {
            "$set": {"passwordHash": hash_password(body.password), "updatedAt": now},
            "$inc": {"sessionVersion": 1},
            "$unset": {"passwordResetHash": "", "passwordResetExpires": ""},
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset link")
    return {"message": "Password updated. Sign in with your new password."}
