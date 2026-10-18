from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from datetime import datetime, timezone
import logging

from models.user import User, UserResponse
from models.invitation import (
    AcceptInvitationRequest,
    InvitationPreviewRequest,
    Invitation,
    InvitationCreate,
    InvitationResponse,
    MemberRoleUpdate,
)
from utils.auth import get_current_user, hash_password, require_role, verify_password
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
from utils.invite_tokens import find_invitation_by_token, generate_invite_token

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

    # Only the hash is persisted; the raw token goes out in the email link.
    raw_token, token_hash = generate_invite_token()
    invitation = Invitation(
        organizationId=current_user["organizationId"],
        email=invitation_data.email,
        role=invitation_data.role,
        invitedBy=current_user["sub"],
        tokenHash=token_hash,
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
        token=raw_token
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
        {"_id": 0, "token": 0, "tokenHash": 0}
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


def _invitation_is_expired(invitation: dict) -> bool:
    expires_at = datetime.fromisoformat(invitation["expiresAt"].replace("Z", "+00:00"))
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < datetime.now(timezone.utc)


def _invite_role(invitation: dict) -> str:
    # The invitation role was validated when it was issued; normalise anyway so
    # a legacy record with an unknown role cannot grant elevated access.
    return invitation["role"] if is_valid_role(invitation.get("role")) else DEFAULT_MEMBER_ROLE


async def _claim_invitation(invitation: dict) -> None:
    """Atomically move ``pending -> accepted`` (single-use guarantee)."""
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


async def _release_invitation(invitation: dict) -> None:
    await db.invitations.update_one(
        {"id": invitation["id"], "status": "accepted"},
        {"$set": {"status": "pending"}, "$unset": {"acceptedAt": ""}},
    )


async def _join_existing_account(invitation: dict, user: dict, password: str) -> dict:
    """Accept an invite for an address that already has a LexiSense account.

    * Already in the inviting team: idempotent. The invite is consumed and
      nothing about the account changes (no role change, no password change).
    * In another team: the caller must prove they own the account with its
      current password (the endpoint is unauthenticated and the token alone
      must not be enough to move someone's account). The account then moves to
      the inviting team with the invite's role. Accounts belong to exactly one
      organization (``users.organizationId``), so this leaves the old team.
      Refused when it would leave the old team with members but no admin.
    """
    org_id = invitation["organizationId"]

    if user.get("organizationId") == org_id:
        await _claim_invitation(invitation)
        return {
            "message": "You're already a member of this team",
            "email": user["email"],
            "existingAccount": True,
            "alreadyMember": True,
        }

    if not user.get("isActive", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is switched off. Ask your LexiSense admin to turn it back on.",
        )
    # 403, not 401: the frontend treats every 401 as an expired session.
    if not verify_password(password, user.get("passwordHash", "")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="That password doesn't match your LexiSense account. Use the password you sign in with.",
        )

    old_org_id = user.get("organizationId")
    if old_org_id and user.get("role") == "admin":
        other_members = await db.users.count_documents(
            {"organizationId": old_org_id, "id": {"$ne": user["id"]}, "isActive": {"$ne": False}}
        )
        other_admins = await db.users.count_documents(
            {"organizationId": old_org_id, "id": {"$ne": user["id"]}, "role": "admin", "isActive": {"$ne": False}}
        )
        if other_members and not other_admins:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You're the only admin of your current team. Make someone else an admin there, then accept this invite.",
            )

    role = _invite_role(invitation)
    await _claim_invitation(invitation)
    try:
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {
                "organizationId": org_id,
                "role": role,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }},
        )
    except Exception:
        await _release_invitation(invitation)
        raise

    await log_action(
        organization_id=org_id,
        user_id=user["id"],
        user_email=user["email"],
        action="team_invite_accepted",
        resource_type="team",
        resource_id=invitation["id"],
        resource_title=user["email"],
        details={"role": role, "existingAccount": True, "previousOrganizationId": old_org_id},
    )
    return {
        "message": "Joined team",
        "email": user["email"],
        "existingAccount": True,
        "alreadyMember": False,
    }


@router.post(
    "/accept-invite/preview",
    dependencies=[Depends(rate_limit(INVITE_RATE_LIMITER, "team:accept-invite-preview"))],
)
async def preview_invitation(payload: InvitationPreviewRequest):
    """Look up an invitation by token so the accept page can show who invited
    whom, and to which team, before the invitee picks a password.

    Read-only: nothing is claimed or written. The token arrives in a JSON body
    (not the query string) so it stays out of API access logs. Status codes
    mirror ``POST /team/accept-invite``:

    * ``404`` unknown or cancelled token,
    * ``400`` expired invitation,
    * ``409`` invitation already accepted.
    """
    invitation = await find_invitation_by_token(db.invitations, payload.token)
    if not invitation or invitation.get("status") not in ("pending", "accepted", "expired"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired invitation",
        )
    if invitation["status"] == "accepted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invitation has already been accepted",
        )
    if invitation["status"] == "expired" or _invitation_is_expired(invitation):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitation has expired",
        )

    org = await db.organizations.find_one(
        {"id": invitation["organizationId"]}, {"_id": 0, "name": 1}
    )
    inviter = await db.users.find_one(
        {"id": invitation.get("invitedBy")},
        {"_id": 0, "firstName": 1, "lastName": 1},
    ) or {}
    inviter_name = f"{inviter.get('firstName') or ''} {inviter.get('lastName') or ''}".strip() or None
    role = _invite_role(invitation)
    # Lets the page ask for the existing password instead of a new one. The
    # token holder already knows the address, so this reveals nothing new.
    existing = await db.users.find_one(
        {"email": invitation["email"]}, {"_id": 0, "id": 1, "organizationId": 1}
    )
    account_exists = existing is not None
    # True when accepting would move the account out of another team (the
    # page warns that they'll lose access to that team's contracts).
    in_other_team = bool(existing) and existing.get("organizationId") != invitation["organizationId"]

    return {
        "email": invitation["email"],
        "accountExists": account_exists,
        "accountInOtherTeam": in_other_team,
        "organizationName": (org or {}).get("name"),
        "inviterName": inviter_name,
        "role": role,
        "expiresAt": invitation["expiresAt"],
    }


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
    invitation = await find_invitation_by_token(db.invitations, payload.token, {"status": "pending"})

    if not invitation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired invitation"
        )

    if _invitation_is_expired(invitation):
        await db.invitations.update_one(
            {"id": invitation["id"]},
            {"$set": {"status": "expired"}}
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitation has expired"
        )

    # The address may already have an account (signed up on their own after
    # the invite went out, or joined via another invite). Join that account
    # to the team instead of silently consuming the invite.
    existing_user = await db.users.find_one({"email": invitation["email"]})
    if existing_user:
        return await _join_existing_account(invitation, existing_user, payload.password)

    # Atomically claim the invitation (single-use guarantee).
    await _claim_invitation(invitation)
    role = _invite_role(invitation)

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
    payload: MemberRoleUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update a team member's role.

    The new role is taken from a validated JSON body (``MemberRoleUpdate``)
    instead of a ``?role=`` query parameter, so it is schema-checked before the
    handler runs and never appears in the request URL / access logs.
    """
    if member_id == current_user["sub"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role"
        )

    if not is_valid_role(payload.role):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of: {', '.join(ROLES)}"
        )

    result = await db.users.update_one(
        {"id": member_id, "organizationId": current_user["organizationId"]},
        {"$set": {"role": payload.role, "updatedAt": datetime.now(timezone.utc).isoformat()}}
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
        details={"role": payload.role},
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

    # Reassign the removed member's version-history authorship to the acting
    # admin. ``contract_versions.changedBy`` stores a user id, and the version
    # listing resolves it against ``users`` -- a hard delete would leave those
    # rows pointing at an account that no longer exists, so every historical
    # version the member authored would render with a missing author forever
    # (an orphaned row). Re-parenting the rows keeps the audit trail readable
    # and leaves the versions themselves intact.
    #
    # Scope: only versions whose contract belongs to the acting admin's
    # organisation are touched, so an id collision across tenants cannot leak
    # into another organisation's history.
    contract_ids = [
        c["id"]
        for c in await db.contracts.find(
            {"organizationId": current_user["organizationId"]},
            {"_id": 0, "id": 1},
        ).to_list(10_000)
    ]
    reassigned = await db.contract_versions.update_many(
        {"changedBy": member_id, "contractId": {"$in": contract_ids}},
        {"$set": {"changedBy": current_user["sub"]}},
    )

    await log_action(
        organization_id=current_user["organizationId"],
        user_id=current_user["sub"],
        user_email=current_user.get("email"),
        action="team_member_removed",
        resource_type="team",
        resource_id=member_id,
        details={"reassignedContractVersions": reassigned.modified_count},
    )

    return {"message": "Member removed successfully"}
