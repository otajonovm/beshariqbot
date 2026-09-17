from bot.handlers.admin import router as admin_router
from bot.handlers.driver import router as driver_router
from bot.handlers.group import router as group_router
from bot.handlers.passenger import router as passenger_router

__all__ = [
    "admin_router",
    "driver_router",
    "group_router",
    "passenger_router",
]
