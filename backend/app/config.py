from dataclasses import dataclass
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings
from typing import List, Tuple


@dataclass(frozen=True)
class Neo4jConnectionConfig:
    name: str
    uri: str
    username: str
    password: str
    database: str = "neo4j"
    enabled: bool = True
    write_protected_identifiers: Tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        return bool(
            str(self.uri or "").strip()
            and str(self.username or "").strip()
            and str(self.password or "").strip()
        )

    @property
    def is_write_protected(self) -> bool:
        target = " ".join(
            str(value or "").strip().lower()
            for value in (self.uri, self.username, self.database)
        )
        return any(identifier and identifier in target for identifier in self.write_protected_identifiers)

    @property
    def write_protection_reason(self) -> str:
        matched = [
            identifier
            for identifier in self.write_protected_identifiers
            if identifier
            and identifier
            in " ".join(
                str(value or "").strip().lower()
                for value in (self.uri, self.username, self.database)
            )
        ]
        if not matched:
            return ""
        return f"matches protected identifier(s): {', '.join(matched)}"


class Settings(BaseSettings):
    servicenow_instance_url: str = "https://dev308390.service-now.com"
    servicenow_username: str = "admin"
    servicenow_password: str = "admin"
    servicenow_client_id: str = ""
    servicenow_client_secret: str = ""
    servicenow_trust_env: bool = False
    servicenow_verify_ssl: bool = False
    graphrag_citation_instance_url: str = ""
    graphrag_record_card_scope: str = "core_records"
    neo4j_uri: str = ""
    neo4j_username: str = ""
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    neo4j_enabled: bool = True
    neo4j_uri_a: str = ""
    neo4j_username_a: str = ""
    neo4j_password_a: str = ""
    neo4j_database_a: str = "neo4j"
    neo4j_enabled_a: bool = True
    neo4j_write_protected_identifiers: str = ""
    catalog_polling_enabled: bool = True
    catalog_poll_interval_seconds: int = 3
    catalog_poll_batch_size: int = 25
    catalog_poll_max_items: int = 250
    catalog_form_freshness_seconds: int = 0
    servicenow_webhook_secret: str = ""
    servicenow_webhook_dedupe_seconds: int = 300
    graphrag_minimal_graph_mode: bool = False
    graphrag_schema_enabled: bool = True
    graphrag_debug_log_enabled: bool = False
    graphrag_debug_log_path: str = "logs/backend/graphrag_debug.log"
    graphrag_v2_enabled: bool = True
    graphrag_v2_schema_ttl_seconds: int = 300
    graphrag_schema_strict_mode: str = "dynamic"
    graphrag_domain_hint_mode: str = "auto"
    graphrag_vector_enabled: bool = False
    graphrag_embedding_model: str = "nomic-embed-text"
    graphrag_vector_top_k: int = 12
    graphrag_embedding_batch_size: int = 24
    graphrag_embedding_backfill_enabled: bool = False
    vectorrag_enabled: bool = True
    vectorrag_solutions_path: str = "backend/data/Solutions.txt"
    vectorrag_embedding_model: str = "nomic-embed-text"
    vectorrag_top_k: int = 5
    vectorrag_similarity_threshold: float = 0.55
    vectorrag_startup_reindex: bool = False

    # --- LLM layer (OpenAI-compatible: Ollama primary, Groq fallback) ---
    llm_provider: str = "ollama"  # ollama | groq
    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "llama3.2"
    ollama_embedding_model: str = "nomic-embed-text"
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "llama-3.3-70b-versatile"
    llm_fallback_enabled: bool = True
    llm_timeout_seconds: float = 90.0
    embedding_dim: int = 768
    mcp_http_url: str = ""

    app_secret_key: str = "change-me-in-env"
    admin_username: str = "admin"
    admin_password: str = ""  # plain text from .env; hashed in memory at startup
    admin_password_hash: str = ""  # optional pre-hashed (bcrypt) alternative
    jwt_expire_minutes: int = 480
    auth_enabled: bool = True
    cors_origins: str = ""
    debug: bool = True

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug_flag(cls, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"release", "prod", "production", "false", "0", "no", "off"}:
                return False
            if normalized in {"debug", "dev", "development", "true", "1", "yes", "on"}:
                return True
        return value

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def graphrag_citation_instance_url_resolved(self) -> str:
        return (self.graphrag_citation_instance_url or self.servicenow_instance_url).strip()

    @property
    def llm_provider_normalized(self) -> str:
        return "groq" if str(self.llm_provider or "").strip().lower() == "groq" else "ollama"

    @property
    def openai_chat_model(self) -> str:
        """Chat model of the primary provider (kept for call-site compatibility)."""
        if self.llm_provider_normalized == "groq":
            return self.groq_model
        return self.ollama_chat_model

    @property
    def openai_chat_configured(self) -> bool:
        if self.llm_provider_normalized == "groq":
            return bool(self.groq_api_key.strip())
        return bool(self.ollama_base_url.strip())

    @property
    def openai_embedding_configured(self) -> bool:
        # Groq has no embeddings endpoint, so embeddings always go through Ollama.
        return bool(self.ollama_base_url.strip())

    def openai_embedding_model(self, default_model: str = "") -> str:
        return self.ollama_embedding_model

    def openai_embedding_dimension_model(self, default_model: str = "") -> str:
        return self.ollama_embedding_model

    def graphrag_neo4j_config(self) -> Neo4jConnectionConfig:
        return Neo4jConnectionConfig(
            name="GraphRAG Neo4j",
            uri=self.neo4j_uri,
            username=self.neo4j_username,
            password=self.neo4j_password,
            database=self.neo4j_database or "neo4j",
            enabled=self.neo4j_enabled,
            write_protected_identifiers=self.neo4j_write_protected_identifiers_tuple,
        )

    def platform_neo4j_config(self) -> Neo4jConnectionConfig:
        return Neo4jConnectionConfig(
            name="AI platform Neo4j",
            uri=self.neo4j_uri_a,
            username=self.neo4j_username_a,
            password=self.neo4j_password_a,
            database=self.neo4j_database_a or "neo4j",
            enabled=self.neo4j_enabled_a,
            write_protected_identifiers=self.neo4j_write_protected_identifiers_tuple,
        )

    @property
    def neo4j_write_protected_identifiers_tuple(self) -> Tuple[str, ...]:
        return tuple(
            identifier.strip().lower()
            for identifier in str(self.neo4j_write_protected_identifiers or "").split(",")
            if identifier.strip()
        )

    class Config:
        env_file = str(Path(__file__).resolve().parents[1] / ".env")
        extra = "ignore"


settings = Settings()
