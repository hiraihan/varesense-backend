from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.auth.deps import get_current_user
from varesense.auth.models import User
from varesense.auth.schemas import LoginRequest, TokenResponse, UserOut
from varesense.auth.service import authenticate, issue_token_pair
from varesense.core.db import get_session

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)):
    user = await authenticate(session, body.email, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="bad email or password"
        )
    return TokenResponse(**issue_token_pair(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user
