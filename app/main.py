from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.router import router as auth_router
from app.core.config import settings
from app.core.directus import close_directus
from app.catalog.router import router as catalog_router
from app.notifications.router import router as notifications_router
from app.events.router import router as events_router
from app.friends.router import router as friends_router
from app.notifier.scheduler import scheduler, start_scheduler
from app.notifier.router import router as notifier_router
from app.parser.router import router as parser_router
from app.profile.router import router as profile_router
from app.reservations.router import router as reservations_router
from app.wishlists.router import router as wishlists_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)
    await close_directus()


app = FastAPI(
    title="Wishlle Backend",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(parser_router, prefix="/api", tags=["parser"])
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(profile_router, prefix="/api/profile", tags=["profile"])
app.include_router(friends_router, prefix="/api/friends", tags=["friends"])
app.include_router(wishlists_router, prefix="/api/wishlists", tags=["wishlists"])
app.include_router(reservations_router, prefix="/api/reservations", tags=["reservations"])
app.include_router(events_router, prefix="/api/events", tags=["events"])
app.include_router(catalog_router, prefix="/api/catalog", tags=["catalog"])
app.include_router(notifications_router, prefix="/api/notifications", tags=["notifications"])
app.include_router(notifier_router, prefix="/api/reminders", tags=["reminders"])


@app.get("/health")
async def health():
    return {"status": "ok"}
