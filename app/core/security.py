"""
Security utilities — password hashing and JWT generation/verification.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# ---------------------------------------------------------------------------
# Password hashing (bcrypt)
# ---------------------------------------------------------------------------

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches the stored *hashed* password."""
    return _pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

_ALGORITHM = "HS256"
_ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Encode *data* into a signed JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.APP_SECRET_KEY, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and verify a JWT token.  Raises JWTError on failure."""
    return jwt.decode(token, settings.APP_SECRET_KEY, algorithms=[_ALGORITHM])


# ---------------------------------------------------------------------------
# FastAPI Security Scheme & Authentication Dependencies
# ---------------------------------------------------------------------------

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security_scheme = HTTPBearer(
    scheme_name="Bearer",
    bearerFormat="JWT",
    description="Enter JWT access token",
    auto_error=False,
)


async def get_current_user(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
) -> dict:
    """
    Validate the Bearer JWT token and return the decoded payload dict.
    Raises 401 Unauthorized if token is missing, invalid, or expired.
    """
    token: Optional[str] = None

    if creds and creds.credentials:
        token = creds.credentials.strip()
    else:
        # Fallback to direct Authorization header check if credentials was not parsed
        auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth_header:
            auth_header = auth_header.strip()
            if auth_header.lower().startswith("bearer "):
                token = auth_header[7:].strip()
            else:
                token = auth_header

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # In case the token itself still has a 'Bearer ' prefix (e.g. entered into Swagger UI with prefix)
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    try:
        payload = decode_access_token(token)
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

