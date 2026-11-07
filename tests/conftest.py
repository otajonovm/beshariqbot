"""Test muhiti: Settings yuklanishi uchun minimal env."""

from __future__ import annotations

import os

# bot.config importidan OLDIN
os.environ.setdefault("BOT_TOKEN", "0000000000:TEST-TEST-TOKEN-FOR-PYTEST-ONLY")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("DATABASE_URL", "")
