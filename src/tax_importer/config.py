from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    symmetry_base_url: str
    symmetry_api_key: str
    workday_tenant: str
    workday_username: str
    workday_password: str
    workday_hcm_url: str
    log_level: str
    mappings_path: Path


def load_settings(env_path: str | None = None) -> Settings:
    if env_path:
        load_dotenv(env_path)
    else:
        # Load automatically from project root if .env exists
        root_env = Path(__file__).resolve().parents[2] / ".env"
        if root_env.exists():
            load_dotenv(str(root_env))

    base_dir = Path(__file__).resolve().parents[2]
    mappings_path = Path(os.getenv("LOCAL_TAX_MAPPINGS", base_dir / "mappings" / "local_tax_mappings.json"))

    return Settings(
        symmetry_base_url=os.getenv("SYMMETRY_BASE_URL", "https://api.symmetry.com"),
        symmetry_api_key=os.getenv("SYMMETRY_API_KEY", ""),
        workday_tenant=os.getenv("WORKDAY_TENANT", ""),
        workday_username=os.getenv("WORKDAY_USERNAME", ""),
        workday_password=os.getenv("WORKDAY_PASSWORD", ""),
        workday_hcm_url=os.getenv("WORKDAY_HCM_URL", ""),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        mappings_path=mappings_path,
    )
