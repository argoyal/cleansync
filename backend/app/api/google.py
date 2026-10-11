from fastapi import APIRouter, Depends, Request

from app.auth.deps import AuthContext, require_user

router = APIRouter()


@router.get("/api/google/usage")
def usage(request: Request, ctx: AuthContext = Depends(require_user)) -> dict:
    """Spec §7: units used today (UTC), the budget, and rclone's estimated Drive units."""
    budget = request.app.state.budget
    return {
        "day": budget.day(),
        "used": budget.used_today(),
        "budget": budget.budget(),
        "drive_estimate": budget.drive_estimate_today(),
        "reached": budget.reached(),
    }
