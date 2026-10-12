"""Schemas for local users and teams (all JSON-safe, no secret material)."""

from __future__ import annotations

from pydantic import BaseModel


class UserSignup(BaseModel):
    email: str
    name: str | None = None
    password: str


class UserLogin(BaseModel):
    email: str
    password: str


class UserRead(BaseModel):
    id: int
    email: str
    name: str | None = None
    disabled: bool = False
    created_at: str


class TeamCreate(BaseModel):
    name: str


class TeamMemberAdd(BaseModel):
    email: str
    role: str = "member"


class TeamMemberRead(BaseModel):
    email: str
    role: str
    created_at: str


class TeamRead(BaseModel):
    id: int
    name: str
    created_by_email: str | None = None
    created_at: str
    members: list[TeamMemberRead] = []
