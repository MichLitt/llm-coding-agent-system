# config_v2_schema.py — new typed config schema; do NOT modify this file

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DatabaseConfig:
    host: str = "localhost"
    port: int = 5432
    name: str = "myapp"
    pool_size: int = 5


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "text"


@dataclass
class CacheConfig:
    enabled: bool = True
    ttl: int = 300


@dataclass
class ConfigV2:
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)

    @classmethod
    def from_dict(cls, data: dict) -> "ConfigV2":
        return cls(
            db=DatabaseConfig(**data.get("db", {})),
            server=ServerConfig(**data.get("server", {})),
            logging=LoggingConfig(**data.get("logging", {})),
            cache=CacheConfig(**data.get("cache", {})),
        )
