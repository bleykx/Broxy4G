from fastapi import APIRouter, Depends

from broxy4g.api.deps import require_auth

router = APIRouter(dependencies=[Depends(require_auth)], tags=["session"])


@router.get("/session")
async def session():
    """Session check: reaching this handler means Basic Auth succeeded."""
    return {"authenticated": True}
