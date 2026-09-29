from pydantic import BaseModel, Field, ConfigDict


class Svm(BaseModel):
    uuid: str
    name: str


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=1024)


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=1024)


class MemberAdd(BaseModel):
    name: str = Field(min_length=1, max_length=1024)


class MemberList(BaseModel):
    members: list[str]


class Group(BaseModel):
    model_config = ConfigDict(extra="allow")

    sid: str
    name: str
    description: str | None = None
    svm: Svm | None = None
    members: list[str] = Field(default_factory=list)
