from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ontap_host: str
    ontap_username: str
    ontap_password: str
    ontap_verify_ssl: bool = True
    ontap_timeout: float = 30.0
    default_svm: str | None = None
    # Optional override for local SMB domain (CIFS server NetBIOS name), e.g. CAPESVM2
    cifs_local_domain: str | None = None
    app_title: str = "FSx ONTAP Local Group Manager"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def base_url(self) -> str:
        host = self.ontap_host.rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = "https://" + host
        return host + "/api"


@lru_cache
def get_settings() -> Settings:
    return Settings()
