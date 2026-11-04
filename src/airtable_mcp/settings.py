"""Non-credential configuration for the airtable-mcp server.

Credentials are never read from here -- they come from a per-request HTTP
header (see middleware.py). This module only holds process-level settings
that are the same for every tenant.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-level settings. Unknown environment variables are ignored
    rather than rejected, because the runtime environment injects variables
    this project does not declare."""

    model_config = SettingsConfigDict(extra="ignore")

    mcp_http_host: str = "0.0.0.0"
    mcp_http_port: int = 8080


settings = Settings()
