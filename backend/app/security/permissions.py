from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import GroupRole
from app.models import ApprovalGroupMember, ApprovalTask, GroupMember, User


DESIGN_ROLES = {GroupRole.GROUP_ADMIN, GroupRole.FLOW_DESIGNER}
EXECUTE_ROLES = {GroupRole.GROUP_ADMIN, GroupRole.FLOW_EXECUTOR, GroupRole.FLOW_DESIGNER}
VIEW_ROLES = {
    GroupRole.GROUP_ADMIN,
    GroupRole.FLOW_DESIGNER,
    GroupRole.FLOW_EXECUTOR,
    GroupRole.APPROVER,
    GroupRole.VIEWER,
}
APPROVE_ROLES = {GroupRole.GROUP_ADMIN, GroupRole.APPROVER}
ADMIN_ROLES = {GroupRole.GROUP_ADMIN}


def roles_for(db: Session, user: User, group_id: str) -> set[str]:
    if user.is_super_admin:
        return {role.value for role in GroupRole}
    return set(
        db.scalars(
            select(GroupMember.role).where(
                GroupMember.user_id == user.id,
                GroupMember.group_id == group_id,
            )
        ).all()
    )


def require_group_role(
    db: Session,
    user: User,
    group_id: str,
    allowed: set[GroupRole],
) -> None:
    if user.is_super_admin:
        return
    current = roles_for(db, user, group_id)
    if not current.intersection({role.value for role in allowed}):
        raise HTTPException(status_code=403, detail="You do not have access to this group action")


def require_view(db: Session, user: User, group_id: str) -> None:
    require_group_role(db, user, group_id, VIEW_ROLES)


def require_design(db: Session, user: User, group_id: str) -> None:
    require_group_role(db, user, group_id, DESIGN_ROLES)


def require_execute(db: Session, user: User, group_id: str) -> None:
    require_group_role(db, user, group_id, EXECUTE_ROLES)


def require_group_admin(db: Session, user: User, group_id: str) -> None:
    require_group_role(db, user, group_id, ADMIN_ROLES)


def require_approve_task(db: Session, user: User, task: ApprovalTask) -> None:
    if user.is_super_admin:
        return
    current = roles_for(db, user, task.group_id)
    if GroupRole.GROUP_ADMIN.value in current:
        return
    if GroupRole.APPROVER.value not in current:
        raise HTTPException(status_code=403, detail="You cannot approve this task")
    if task.approval_group_id is None:
        return
    member = db.scalar(
        select(ApprovalGroupMember).where(
            ApprovalGroupMember.approval_group_id == task.approval_group_id,
            ApprovalGroupMember.user_id == user.id,
        )
    )
    if member is None:
        raise HTTPException(status_code=403, detail="You are not in this approval group")
