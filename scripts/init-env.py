import secrets
from pathlib import Path

from cryptography.fernet import Fernet

path = Path(".env")
if path.exists():
    print(".env already exists; preserving keys")
else:
    path.write_text(f"JWT_SECRET={secrets.token_urlsafe(48)}\nENCRYPTION_KEY={Fernet.generate_key().decode()}\n")
    path.chmod(0o600)
    print("Created .env with local secrets")
