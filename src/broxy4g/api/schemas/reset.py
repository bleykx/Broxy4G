from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ResetStatus = Literal["DONE", "ERROR", "PENDING", "LOCK"]


class ResetResult(BaseModel):
    status: ResetStatus
    message: str | None = None


class ResetRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1)


class ResetBatchItem(BaseModel):
    dongle_id: UUID
    status: ResetStatus
    message: str | None = None


class ResetBatchResult(BaseModel):
    results: list[ResetBatchItem]
