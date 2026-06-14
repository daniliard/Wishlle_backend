from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.router import router as auth_router
from app.core.config import settings
from app.core.directus import close_directus
from app.friends.router import router as friends_router
from app.notifier.scheduler import scheduler, start_scheduler
from app.parser.router import router as parser_router
from app.profile.router import router as profile_router
from app.reservations.router import router as reservations_router
from app.wishlists.router import router as wishlists_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
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


@app.get("/health")
async def health():
    return {"status": "ok"}
