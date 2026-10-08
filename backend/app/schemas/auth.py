from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=1024)


class MeOut(BaseModel):
    username: str
    must_change_password: bool
    csrf_token: str


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)
