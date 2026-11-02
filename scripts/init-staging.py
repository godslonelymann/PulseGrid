"""Create local native staging settings while preserving existing secrets."""

import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values, set_key

path = Path(".env.staging")
values = dict(dotenv_values(path)) if path.exists() else {}
os.umask(0o077)
path.touch(mode=0o600, exist_ok=True)
for key, value in {
    "JWT_SECRET": secrets.token_urlsafe(48),
    "ENCRYPTION_KEY": Fernet.generate_key().decode(),
    "STAGING_TEST_PASSWORD": secrets.token_urlsafe(32),
}.items():
    if key not in values:
        set_key(path, key, value, quote_mode="never")
native_settings = dict(
    DATABASE_URL="postgresql+psycopg://pulsegrid:local-staging-only@localhost:55435/pulsegrid",
    REDIS_URL="redis://localhost:56381/0",
    ENVIRONMENT="production",
    PUBLIC_ORIGIN="https://localhost:8443",
    SECURE_COOKIES="true",
)
for key, value in native_settings.items():
    set_key(path, key, value, quote_mode="never")
path.chmod(0o600)
print("Native staging environment ready; signing/encryption keys preserved")
