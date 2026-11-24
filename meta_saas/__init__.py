"""Multi-tenant Meta connection module for a Flask dashboard.

Integrate using register_meta_routes(app, current_identity), where current_identity
returns (tenant_id, user_id) from the EXISTING authenticated server session.
Never take tenant_id/user_id from caller-controlled query parameters.
"""
from .routes import register_meta_routes
