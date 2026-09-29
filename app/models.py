from pydantic import BaseModel, Field, ConfigDict


class Svm(BaseModel):
    uuid: str
    name: str


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=1024)
    privileges: list[str] = Field(default_factory=list)
    # Optional: attach NTFS permission to a volume junction path on create
    volume_uuid: str | None = None
    permission: str | None = Field(
        default=None,
        description="NTFS preset: modify | read_and_execute | full_control",
    )


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=1024)
    privileges: list[str] | None = None


class PrivilegesUpdate(BaseModel):
    privileges: list[str] = Field(default_factory=list)


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
    privileges: list[str] = Field(default_factory=list)


class VolumeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=203)
    size: str = Field(min_length=1, description="Size such as 10GB or bytes")
    aggregate: str = Field(min_length=1)
    comment: str | None = Field(default=None, max_length=1024)
    junction_path: str | None = Field(default=None, max_length=1024)
    security_style: str | None = Field(default=None)


class VolumeUpdate(BaseModel):
    size: str | None = None
    comment: str | None = Field(default=None, max_length=1024)
    state: str | None = None
    junction_path: str | None = None
    security_style: str | None = None


class VolumePermissionAttach(BaseModel):
    volume_uuid: str | None = None
    group_name: str | None = Field(default=None, max_length=256)
    permission: str = Field(min_length=1, description="modify | read_and_execute | full_control")


class VolumePermissionDetach(BaseModel):
    user_or_group: str = Field(min_length=1, max_length=1024)
