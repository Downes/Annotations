import json
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Any


class AnnotationCreate(BaseModel):
    target_url: str = Field(..., max_length=4096)
    target_selector: Optional[Any] = None
    body: str = Field(..., max_length=50000)
    tags: list[str] = Field(default=[], max_length=20)
    visibility: str = "public"
    motivation: str = "commenting"

    @field_validator("target_url")
    @classmethod
    def check_target_url(cls, v):
        if not v.startswith(("http://", "https://")):
            raise ValueError("target_url must be an http or https URL")
        return v

    @field_validator("tags")
    @classmethod
    def check_tags(cls, v):
        for tag in v:
            if len(tag) > 100:
                raise ValueError("each tag must be 100 characters or fewer")
        return v

    @field_validator("target_selector")
    @classmethod
    def check_target_selector(cls, v):
        if v is not None and len(json.dumps(v)) > 4096:
            raise ValueError("target_selector must serialise to 4096 bytes or fewer")
        return v

    @field_validator("visibility")
    @classmethod
    def check_visibility(cls, v):
        if v not in ("public", "private"):
            raise ValueError("visibility must be public or private")
        return v

    @field_validator("motivation")
    @classmethod
    def check_motivation(cls, v):
        allowed = {
            "bookmarking", "classifying", "commenting", "describing",
            "editing", "flowing", "highlighting", "identifying", "linking",
            "moderating", "questioning", "replying", "tagging",
        }
        if v not in allowed:
            raise ValueError(f"motivation must be one of: {', '.join(sorted(allowed))}")
        return v


class BatchCheckRequest(BaseModel):
    urls: list[str] = Field(default=[], max_length=500)


class AnnotationUpdate(BaseModel):
    target_selector: Optional[Any] = None
    body: Optional[str] = Field(None, max_length=50000)
    tags: Optional[list[str]] = Field(None, max_length=20)
    visibility: Optional[str] = None
    motivation: Optional[str] = None

    @field_validator("tags")
    @classmethod
    def check_tags(cls, v):
        if v is not None:
            for tag in v:
                if len(tag) > 100:
                    raise ValueError("each tag must be 100 characters or fewer")
        return v

    @field_validator("target_selector")
    @classmethod
    def check_target_selector(cls, v):
        if v is not None and len(json.dumps(v)) > 4096:
            raise ValueError("target_selector must serialise to 4096 bytes or fewer")
        return v

    @field_validator("visibility")
    @classmethod
    def check_visibility(cls, v):
        if v is not None and v not in ("public", "private"):
            raise ValueError("visibility must be public or private")
        return v

    @field_validator("motivation")
    @classmethod
    def check_motivation(cls, v):
        allowed = {
            "bookmarking", "classifying", "commenting", "describing",
            "editing", "flowing", "highlighting", "identifying", "linking",
            "moderating", "questioning", "replying", "tagging",
        }
        if v is not None and v not in allowed:
            raise ValueError(f"motivation must be one of: {', '.join(sorted(allowed))}")
        return v
