"""Unified authentication & authorization for cloudcost.

Entrypoints:
- `router`                — /api/auth/* routes (login, callback, refresh, logout, me)
- `AuthMiddleware`        — parses token and attaches `request.state.principal`
- `get_current_user`      — FastAPI dependency returning the authenticated User
- `require_roles(*r)`     — role guard
- `require_module(m)`     — site-wide module switch guard
- `visible_cloud_account_ids(user)` — data-scope filter helper
"""

from app.auth.principal import Principal, AuthMethod  # noqa: F401
from app.auth.dependencies import (  # noqa: F401
    get_current_principal,
    get_current_user,
    require_roles,
    require_module,
)
from app.auth.scope import (  # noqa: F401
    visible_cloud_account_ids,
    visible_data_source_ids,
    ensure_cloud_account_visible,
)
