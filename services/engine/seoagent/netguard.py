"""SSRF guard — moved to `shared/netguard.py`.

Three services now make requests to URLs a user typed: the crawler, the SERP
service and notifications (a webhook target is the most obviously dangerous of
the three). The guard belongs where all of them can reach it rather than
inside the engine.

This module stays as the engine's door to it so `from . import netguard` keeps
working, including in the standalone install that runs out of services/engine.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.netguard import (  # noqa: E402
    ALLOWED_SCHEMES,  # noqa: F401
    METADATA_HOSTS,  # noqa: F401
    TargetNotAllowed,  # noqa: F401
    allow_private,  # noqa: F401
    check_url,  # noqa: F401
    is_allowed,  # noqa: F401
    reset_cache,  # noqa: F401
)
