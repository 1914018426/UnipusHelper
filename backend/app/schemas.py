from pydantic import BaseModel, EmailStr, Field, field_serializer
from typing import Optional
from datetime import datetime, timedelta

class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: int
    email: str
    default_phone: Optional[str] = None
    created_at: Optional[datetime] = None

    @field_serializer("created_at")
    def serialize_created_at(self, value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        return (value + timedelta(hours=8)).isoformat()

    class Config:
        from_attributes = True

class TaskCreate(BaseModel):
    phone: str
    password: str

class TaskResponse(BaseModel):
    id: int
    user_id: int
    email: str
    phone: str
    status: str
    progress: float
    log: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_serializer("created_at", "updated_at")
    def serialize_dt(self, value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        return (value + timedelta(hours=8)).isoformat()

    class Config:
        from_attributes = True

class TaskStatus(BaseModel):
    id: int
    status: str
    progress: float
    log: str
