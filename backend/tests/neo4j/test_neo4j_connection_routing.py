import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import Neo4jConnectionConfig, Settings  # noqa: E402
from app.services import neo4j_catalog_graph as catalog_module  # noqa: E402
from app.services import neo4j_query_runtime as runtime_module  # noqa: E402


class _FakeResult:
    def consume(self):
        return None

    def __iter__(self):
        return iter([])


class _FakeSession:
    def __init__(self, driver, database):
        self._driver = driver
        self.database = database

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, query, parameters=None, **kwargs):
        self._driver.runs.append(
            {
                "database": self.database,
                "query": str(query),
                "parameters": parameters or kwargs,
            }
        )
        return _FakeResult()


class _FakeDriver:
    def __init__(self, uri, auth):
        self.uri = uri
        self.auth = auth
        self.sessions = []
        self.runs = []
        self.closed = False

    def verify_connectivity(self):
        return None

    def session(self, database=None):
        self.sessions.append(database)
        return _FakeSession(self, database)

    def close(self):
        self.closed = True


def _driver_factory(created):
    def fake_driver(uri, auth, **kwargs):
        driver = _FakeDriver(uri, auth)
        created.append(driver)
        return driver

    return fake_driver


def test_settings_build_distinct_graphrag_and_platform_neo4j_configs():
    configured = Settings(
        _env_file=None,
        neo4j_uri="bolt://graphrag",
        neo4j_username="graph-user",
        neo4j_password="graph-pass",
        neo4j_database="graph-db",
        neo4j_uri_a="bolt://platform",
        neo4j_username_a="platform-user",
        neo4j_password_a="platform-pass",
        neo4j_database_a="platform-db",
    )

    graph_config = configured.graphrag_neo4j_config()
    platform_config = configured.platform_neo4j_config()

    assert graph_config.uri == "bolt://graphrag"
    assert graph_config.username == "graph-user"
    assert graph_config.database == "graph-db"
    assert platform_config.uri == "bolt://platform"
    assert platform_config.username == "platform-user"
    assert platform_config.database == "platform-db"
    assert graph_config != platform_config


def test_exported_catalog_singletons_have_separate_connection_roles():
    assert catalog_module.neo4j_catalog_graph_service._config.name == "GraphRAG Neo4j"
    assert catalog_module.platform_neo4j_catalog_graph_service._config.name == "AI platform Neo4j"


def test_platform_config_does_not_fallback_to_graphrag_credentials():
    configured = Settings(
        _env_file=None,
        neo4j_uri="bolt://graphrag",
        neo4j_username="graph-user",
        neo4j_password="graph-pass",
        neo4j_database="graph-db",
        neo4j_uri_a="",
        neo4j_username_a="",
        neo4j_password_a="",
    )

    platform_config = configured.platform_neo4j_config()

    assert platform_config.uri == ""
    assert platform_config.username == ""
    assert platform_config.password == ""
    assert platform_config.is_complete is False


def test_catalog_graph_services_route_to_their_configured_databases(monkeypatch):
    created = []
    monkeypatch.setattr(catalog_module.GraphDatabase, "driver", staticmethod(_driver_factory(created)))
    platform_config = Neo4jConnectionConfig(
        name="AI platform Neo4j",
        uri="bolt://platform",
        username="platform-user",
        password="platform-pass",
        database="platform-db",
    )
    graph_config = Neo4jConnectionConfig(
        name="GraphRAG Neo4j",
        uri="bolt://graphrag",
        username="graph-user",
        password="graph-pass",
        database="graph-db",
    )

    platform_service = catalog_module.Neo4jCatalogGraphService(platform_config)
    graph_service = catalog_module.Neo4jCatalogGraphService(graph_config)
    platform_service.startup()
    graph_service.startup()

    assert [(driver.uri, driver.auth) for driver in created] == [
        ("bolt://platform", ("platform-user", "platform-pass")),
        ("bolt://graphrag", ("graph-user", "graph-pass")),
    ]
    assert created[0].sessions == ["platform-db"]
    assert created[1].sessions == ["graph-db"]


def test_query_runtime_uses_its_own_session_database(monkeypatch):
    created = []
    monkeypatch.setattr(runtime_module.GraphDatabase, "driver", staticmethod(_driver_factory(created)))
    runtime = runtime_module.Neo4jQueryRuntimeService(
        Neo4jConnectionConfig(
            name="GraphRAG Neo4j",
            uri="bolt://graphrag",
            username="graph-user",
            password="graph-pass",
            database="graph-db",
        )
    )

    runtime.startup()
    runtime.validate_readonly_query = lambda cypher_query: None
    result = runtime.execute_readonly_query(
        "MATCH (n) RETURN n LIMIT $limit",
        {"limit": 1},
        limit_cap=1,
    )

    assert result == {"ok": True, "rows": [], "error": None}
    assert created[0].uri == "bolt://graphrag"
    assert created[0].auth == ("graph-user", "graph-pass")
    assert created[0].sessions == ["graph-db"]


def test_write_protected_catalog_startup_skips_constraints(monkeypatch):
    created = []
    monkeypatch.setattr(catalog_module.GraphDatabase, "driver", staticmethod(_driver_factory(created)))
    service = catalog_module.Neo4jCatalogGraphService(
        Neo4jConnectionConfig(
            name="GraphRAG Neo4j",
            uri="neo4j+s://protected-id.databases.neo4j.io",
            username="protected-id",
            password="secret",
            database="protected-id",
            write_protected_identifiers=("protected-id",),
        )
    )

    service.startup()

    assert service.is_write_protected() is True
    assert created[0].sessions == []
    assert service.sync_catalog_form(object()) is False


def test_write_protected_query_runtime_refuses_write_methods(monkeypatch):
    created = []
    monkeypatch.setattr(runtime_module.GraphDatabase, "driver", staticmethod(_driver_factory(created)))
    runtime = runtime_module.Neo4jQueryRuntimeService(
        Neo4jConnectionConfig(
            name="GraphRAG Neo4j",
            uri="neo4j+s://protected-id.databases.neo4j.io",
            username="protected-id",
            password="secret",
            database="protected-id",
            write_protected_identifiers=("protected-id",),
        )
    )
    runtime.startup()

    assert runtime.is_write_protected() is True
    assert runtime.ensure_graphrag_vector_index(
        index_name="service_request_embedding_idx",
        label="ServiceRequest",
        dimensions=1536,
    ) is False
    assert runtime.set_graphrag_node_embedding(
        label="ServiceRequest",
        sys_id="sr-1",
        embedding_vector=[0.1],
        embedding_text_hash="hash",
        embedding_model="model",
    ) is False
    assert created[0].sessions == []
