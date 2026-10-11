import base64
import hmac
import os

from fastapi import HTTPException, Request

API_USER_ENV = "BROXY4G_API_USER"
API_PASS_ENV = "BROXY4G_API_PASSWORD"

async def verify_basic_auth(request: Request) -> None:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        raise_unauthorized()

    try:
        encoded_credentials = auth_header.removeprefix("Basic ")
        decoded = base64.b64decode(encoded_credentials).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        raise_unauthorized()

    expected_user = os.environ.get(API_USER_ENV, "admin")
    expected_pass = os.environ.get(API_PASS_ENV)

    if not expected_pass:
        # Password is required for security
        raise HTTPException(status_code=500, detail="API password not configured")

    if not (hmac.compare_digest(username, expected_user) and 
            hmac.compare_digest(password, expected_pass)):
        raise_unauthorized()

def raise_unauthorized():
    # We use a custom exception or just HTTPException with headers
    # FastAPI's HTTPException doesn't easily allow adding headers to the response 
    # unless we catch it or use a custom handler. 
    # However, for Basic Auth, the browser needs the WWW-Authenticate header.
    raise HTTPException(
        status_code=401, 
        detail="Unauthorized", 
        headers={"WWW-Authenticate": "Basic realm=\"broxy4g\""}
    )
