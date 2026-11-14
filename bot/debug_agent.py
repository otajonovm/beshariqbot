"""Debug-mode NDJSON logger (session 2f1753)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOG_PATH = Path(__file__).resolve().parent.parent / ".cursor" / "debug-2f1753.log"
_SESSION = "2f1753"


def agent_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
    *,
    run_id: str = "pre",
) -> None:
    # #region agent log
    payload = {
        "sessionId": _SESSION,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    line = json.dumps(payload, ensure_ascii=False)
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    logger.info("DBG [%s] %s | %s | %s", hypothesis_id, location, message, data or {})
    # #endregion
