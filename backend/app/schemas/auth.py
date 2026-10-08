from pydantic import BaseModel


class LoginIn(BaseModel):
    username: str
    password: str


class MeOut(BaseModel):
    username: str
    must_change_password: bool
    csrf_token: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str
