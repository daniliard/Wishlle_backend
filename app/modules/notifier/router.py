"""Службовий endpoint для безпечного тесту нагадувань поточного користувача."""

from fastapi import APIRouter, Depends

from app.modules.notifier.service import send_due_event_reminders
from app.modules.profile.router import current_user_id

router = APIRouter()


@router.post("/run-mine")
async def run_my_due_reminders(
    user_id: str = Depends(current_user_id),
) -> dict[str, int]:
    return await send_due_event_reminders(only_user_id=user_id)
