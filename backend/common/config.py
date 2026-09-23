"""Edge runtime configuration (plan.md §4 "Config"): every host, port, secret, path and
interval comes from the environment. Each variable is documented in `infra/.env.example`.
Master data (site, machines, models, catalogue) comes from the DB and rule thresholds from
`contracts/rules.yaml` — never from here."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Empty values count as unset, so a copied .env.example with blank lines keeps defaults.
    model_config = SettingsConfigDict(env_file=None, extra="ignore", env_ignore_empty=True)

    # storage / master data
    edge_db_path: str = "data/edge.db"
    edge_site_id: str | None = None  # default: the only site in the DB
    edge_machine_ids: str = ""  # csv; empty = every ACTIVE machine of the site
    edge_seed_on_start: bool = False
    contracts_dir: Path = REPO_ROOT / "contracts"
    data_dir: Path = REPO_ROOT / "data"

    # bus
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_client_id: str = ""  # empty = edge-api-<site>-<pid>
    mqtt_reconnect_s: float = 2.0

    # auth
    edge_jwt_secret: str = ""
    jwt_ttl_s: int = 12 * 3600
    auth_disabled: bool = False

    # simulator control (contracts/sim_control.md)
    sim_mode: str = "proxy"  # proxy | replay
    sim_control_url: str = ""
    sim_scenarios_dir: Path = REPO_ROOT / "backend" / "tests" / "fixtures" / "scenarios"
    sim_replay_hold: bool = True  # after a scenario, keep sending its last raw frame at 1 Hz

    # runtime cadence
    engine_tick_s: float = 1.0
    ws_ping_s: float = 10.0
    ws_telemetry_min_interval_s: float = 1.0
    ws_queue_max: int = 256
    sync_status_interval_s: float = 10.0

    # http
    cors_origins: str = ""  # csv of allowed browser origins (P3 dev server / PWA)
    log_level: str = "INFO"

    @field_validator("sim_mode")
    @classmethod
    def _sim_mode(cls, v: str) -> str:
        if v not in ("proxy", "replay"):
            raise ValueError("SIM_MODE must be 'proxy' or 'replay'")
        return v

    @staticmethod
    def csv(value: str) -> list[str]:
        return [x.strip() for x in value.split(",") if x.strip()]

    @property
    def machine_ids(self) -> list[str]:
        return self.csv(self.edge_machine_ids)

    @property
    def cors_origin_list(self) -> list[str]:
        return self.csv(self.cors_origins)

    def check(self) -> None:
        """Fail fast on a config that must never start (called by the app lifespan)."""
        if not self.auth_disabled and not self.edge_jwt_secret:
            raise RuntimeError("EDGE_JWT_SECRET is required unless AUTH_DISABLED=true")
