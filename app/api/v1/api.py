from fastapi import APIRouter

from app.api.v1.endpoints import auth, home, user

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(user.router)
api_router.include_router(home.router)
