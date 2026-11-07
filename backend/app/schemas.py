import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text80 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^1[3-9]\d{9}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ChildInput(StrictModel):
    name: Text80
    phone: Phone
    major: Text80
    class_name: Text80
    relationship: Text80


class RegisterInput(StrictModel):
    role: Literal["student", "parent"]
    name: Text80
    phone: Phone
    major: str = Field(default="", max_length=100)
    class_name: str = Field(default="", max_length=100)
    children: list[ChildInput] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def valid_role_fields(self):
        if self.role == "student" and (not self.major or not self.class_name or self.children):
            raise ValueError("学生专业和班级必填，学生不能提交孩子信息")
        if self.role == "parent" and not self.children:
            raise ValueError("请填写至少一个孩子的信息")
        return self


class LoginInput(StrictModel):
    username: Text80
    password: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=200)]


class SignupInput(RegisterInput):
    password: Annotated[str, StringConstraints(strip_whitespace=False, min_length=8, max_length=200)]

    @model_validator(mode="after")
    def validate_credentials(self):
        if not self.password.strip():
            raise ValueError("密码不能全为空白")
        return self


class QuestionInput(StrictModel):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9-]{16,64}$")
    text: str = Field(min_length=1, max_length=2000)
    business_year: int | None = Field(default=None, ge=2000, le=2100)
    admission_year: int | None = Field(default=None, ge=2000, le=2100)
    previous_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class DocumentMeta(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    publisher: str = Field(default="", max_length=200)
    version: str = Field(default="1", min_length=1, max_length=100)
    business_year: int | None = Field(default=None, ge=2000, le=2100)
    admission_year: int | None = Field(default=None, ge=2000, le=2100)
    effective_from: date | None = None
    effective_to: date | None = None
    confirmed_by: str = Field(default="", max_length=100)
    confirmed_on: date | None = None
    basis: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def dates(self):
        if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
            raise ValueError("生效日不能晚于失效日")
        return self


class FormalReply(DocumentMeta):
    content: str = Field(min_length=1, max_length=30000)

    @model_validator(mode="after")
    def formal_basis(self):
        if not self.confirmed_by or not self.confirmed_on or not self.basis:
            raise ValueError("正式答复需要确认人、确认日期和依据")
        return self


class IssueUpdate(StrictModel):
    revision: int = Field(ge=1)
    state: Literal["open", "in_progress", "awaiting_material", "awaiting_retest", "completed", "dismissed"]
    owner: str = Field(default="", max_length=100)
    note: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)


class MergeInput(StrictModel):
    source_ids: list[str] = Field(min_length=1, max_length=30)
    note: str = Field(min_length=1, max_length=2000)


class EventUpdate(StrictModel):
    state: Literal["open", "following", "closed"]
    note: str = Field(min_length=1, max_length=4000)


def scrub(text: str) -> str:
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[手机号已隐去]", text)
    return re.sub(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", "[邮箱已隐去]", text)
