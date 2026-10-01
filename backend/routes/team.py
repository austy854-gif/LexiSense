from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from datetime import datetime, timezone
import logging

from models.user import User, UserResponse
from models.invitation import Invitation, InvitationCreate, InvitationResponse, AcceptInvitationRequest
from utils.auth import get_current_user, hash_password, require_role
from utils.rbac import (
    ADMIN_ROLES,
    DEFAULT_MEMBER_ROLE,
    INVITE_RATE_LIMITER,
    ROLES,
    is_valid_role,
    rate_limit,
)
from services.email_service import send_invitation_email
from services.audit_service import log_action

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/team", tags=["Team"])

#: Every membership-management endpoint is admin-only, enforced through the
#: shared role registry instead of an inline string comparison.
_require_admin = require_role(*ADMIN_ROLES)

db = None

def init_db(database):
    global db
    db = database


@router.get("/members", response_model=List[UserResponse])
async def list_team_members(current_user: dict = Depends(get_current_user)):
    """List all team members in the organization."""
    members = await db.users.find(
        {"organizationId": current_user["organizationId"]},
        {"_id": 0, "passwordHash": 0}
    ).to_list(1000)

    return [
        UserResponse(
            id=m["id"],
            email=m["email"],
            firstName=m.get("firstName"),
            lastName=m.get("lastName"),
            role=m["role"],
            organizationId=m.get("organizationId"),
            isActive=m.get("isActive", True),
            lastLogin=m.get("lastLogin"),
            createdAt=m["createdAt"]
        )
        for m in members
    ]


@router.post("/invite", response_model=InvitationResponse, dependencies=[Depends(_require_admin)])
async def invite_member(
    invitation_data: InvitationCreate,
    current_user: dict = Depends(get_current_user)
):
    """Invite a new member to the organization."""
    # Validate the role at the boundary. Previously any string was accepted and
    # stored on the invitation, and then copied verbatim onto the new account.
    if not is_valid_role(invitation_data.role):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of: {', '.join(ROLES)}"
        )

    existing_user = await db.users.find_one({"email": invitation_data.email})
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists"
        )

    existing_invite = await db.invitations.find_one({
        "email": invitation_data.email,
        "organizationId": current_user["organizationId"],
        "status": "pending"
    })
    if existing_invite:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitation already sent to this email"
        )

    invitation = Invitation(
        organizationId=current_user["organizationId"],
        email=invitation_data.email,
        role=invitation_data.role,
        invitedBy=current_user["sub"]
    )

    invite_doc = invitation.model_dump()
    await db.invitations.insert_one(invite_doc)

    # Get inviter details and organization name
    inviter = await db.users.find_one(
        {"id": current_user["sub"]},
        {"_id": 0, "firstName": 1, "lastName": 1, "email": 1},
    ) or {}
    org = await db.organizations.find_one(
        {"id": current_user["organizationId"]}, {"_id": 0, "name": 1}
    )

    inviter_name = f"{inviter.get('firstName', '')} {inviter.get('lastName', '')}".strip() or inviter.get("email", "Team member")
    org_name = org.get("name", "the organization") if org else "the organization"

    # Send invitation email
    email_result = await send_invitation_email(
        to_email=invitation_data.email,
        inviter_name=inviter_name,
        organization_name=org_name,
        role=invitation_data.role,
        token=invitation.token
    )

    # The invitation token is a bearer secret -- never log its value.
    logger.info(
        "Invitation created",
        extra={
            "invitation_id": invitation.id,
            "organization_id": current_user["organizationId"],
            "email_sent": bool(email_result),
        },
    )

    await log_action(
        organization_id=current_user["organizationId"],
        user_id=current_user["sub"],
        user_email=current_user.get("email"),
        action="team_invite_sent",
        resource_type="team",
        resource_id=invitation.id,
        resource_title=invitation_data.email,
        details={"role": invitation_data.role},
    )

    return InvitationResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        status=invitation.status,
        expiresAt=invitation.expiresAt,
        createdAt=invitation.createdAt
    )


@router.get("/invitations", response_model=List[InvitationResponse], dependencies=[Depends(_require_admin)])
async def list_invitations(current_user: dict = Depends(get_current_user)):
    """List all pending invitations for the organization."""
    invitations = await db.invitations.find(
        {"organizationId": current_user["organizationId"]},
        {"_id": 0}
    ).sort("createdAt", -1).to_list(1000)

    return [
        InvitationResponse(
            id=inv["id"],
            email=inv["email"],
            role=inv["role"],
            status=inv["status"],
            expiresAt=inv["expiresAt"],
            createdAt=inv["createdAt"]
        )
        for inv in invitations
    ]


@router.post(
    "/accept-invite",
    dependencies=[Depends(rate_limit(INVITE_RATE_LIMITER, "team:accept-invite"))],
)
async def accept_invitation(payload: AcceptInvitationRequest):
    """Accept an invitation and create user account.

    The invitation is *claimed atomically* before the account is written: the
    ``pending -> accepted`` transition is a single conditional update, so two
    concurrent requests with the same token cannot both create an account.
    """
    invitation = await db.invitations.find_one({"token": payload.token, "status": "pending"})

    if not invitation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired invitation"
        )

    expires_at = datetime.fromisoformat(invitation["expiresAt"].replace("Z", "+00:00"))
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        await db.invitations.update_one(
            {"id": invitation["id"]},
            {"$set": {"status": "expired"}}
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitation has expired"
        )

    # Another invite for the same address may have been accepted first.
    # Return the generic message so the endpoint cannot be used to enumerate
    # which addresses already have accounts.
    existing_user = await db.users.find_one({"email": invitation["email"]})
    if existing_user:
        await db.invitations.update_one(
            {"id": invitation["id"], "status": "pending"},
            {"$set": {"status": "accepted"}}
        )
        return {"message": "Account created successfully", "email": invitation["email"]}

    # Atomically claim the invitation (single-use guarantee).
    claim = await db.invitations.update_one(
        {"id": invitation["id"], "status": "pending"},
        {"$set": {
            "status": "accepted",
            "acceptedAt": datetime.now(timezone.utc).isoformat(),
        }},
    )
    if claim.modified_count == 0:
        # Lost the race to a concurrent acceptance of the same token.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired invitation"
        )

    # The invitation role was validated when it was issued; normalise anyway so
    # a legacy record with an unknown role cannot grant elevated access.
    role = invitation["role"] if is_valid_role(invitation.get("role")) else DEFAULT_MEMBER_ROLE

    user = User(
        email=invitation["email"],
        passwordHash=hash_password(payload.password),
        firstName=payload.firstName,
        lastName=payload.lastName,
        role=role,
        organizationId=invitation["organizationId"]
    )

    user_doc = user.model_dump()
    try:
        await db.users.insert_one(user_doc)
    except Exception:
        # Release the claim so a transient write failure does not strand the
        # invitation in a state where it can never be accepted.
        await db.invitations.update_one(
            {"id": invitation["id"]},
            {"$set": {"status": "pending"}},
        )
        raise

    await log_action(
        organization_id=invitation["organizationId"],
        user_id=user.id,
        user_email=user.email,
        action="team_invite_accepted",
        resource_type="team",
        resource_id=invitation["id"],
        resource_title=invitation["email"],
        details={"role": role},
    )

    return {"message": "Account created successfully", "email": user.email}


@router.delete("/invitations/{invitation_id}", dependencies=[Depends(_require_admin)])
async def cancel_invitation(
    invitation_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Cancel a pending invitation."""
    result = await db.invitations.delete_one({
        "id": invitation_id,
        "organizationId": current_user["organizationId"]
    })

    if result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found"
        )

    await log_action(
        organization_id=current_user["organizationId"],
        user_id=current_user["sub"],
        user_email=current_user.get("email"),
        action="team_invite_cancelled",
        resource_type="team",
        resource_id=invitation_id,
    )

    return {"message": "Invitation cancelled"}


@router.patch("/members/{member_id}/role", dependencies=[Depends(_require_admin)])
async def update_member_role(
    member_id: str,
    role: str,
    current_user: dict = Depends(get_current_user)
):
    """Update a team member's role."""
    if member_id == current_user["sub"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role"
        )

    if not is_valid_role(role):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of: {', '.join(ROLES)}"
        )

    result = await db.users.update_one(
        {"id": member_id, "organizationId": current_user["organizationId"]},
        {"$set": {"role": role, "updatedAt": datetime.now(timezone.utc).isoformat()}}
    )

    if result.matched_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found"
        )

    await log_action(
        organization_id=current_user["organizationId"],
        user_id=current_user["sub"],
        user_email=current_user.get("email"),
        action="team_role_updated",
        resource_type="team",
        resource_id=member_id,
        details={"role": role},
    )

    return {"message": "Role updated successfully"}


@router.delete("/members/{member_id}", dependencies=[Depends(_require_admin)])
async def remove_member(
    member_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Remove a team member from the organization."""
    if member_id == current_user["sub"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove yourself"
        )

    result = await db.users.delete_one({
        "id": member_id,
        "organizationId": current_user["organizationId"]
    })

    if result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found"
        )

    await log_action(
        organization_id=current_user["organizationId"],
        user_id=current_user["sub"],
        user_email=current_user.get("email"),
        action="team_member_removed",
        resource_type="team",
        resource_id=member_id,
    )

    return {"message": "Member removed successfully"}
