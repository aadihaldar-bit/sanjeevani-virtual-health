"""An existing (pre-organization) SQLite database must upgrade in place, keep its rows and survive a second run."""
import sqlalchemy as sa

from app.database import _run_startup_migrations


def old_database(path):
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE workflows (id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL, description TEXT, is_active BOOLEAN)")
        conn.exec_driver_sql("CREATE TABLE calls (id INTEGER PRIMARY KEY, call_sid VARCHAR(255), caller_number VARCHAR(50))")
        conn.exec_driver_sql("CREATE TABLE api_clients (id INTEGER PRIMARY KEY, client_name VARCHAR(255) NOT NULL, api_key_hash VARCHAR(128), key_prefix VARCHAR(20))")
        conn.exec_driver_sql("INSERT INTO workflows (id, name, is_active) VALUES (1, 'Existing desk', 1)")
        conn.exec_driver_sql("INSERT INTO calls (id, call_sid, caller_number) VALUES (7, 'CA1', '+919000000000')")
        conn.exec_driver_sql("INSERT INTO api_clients (id, client_name, api_key_hash, key_prefix) VALUES (3, 'Monitor', 'h', 'stl')")
    return engine


def columns(engine, table):
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


def test_old_tables_gain_tenant_id_and_keep_their_rows(tmp_path):
    engine = old_database(tmp_path / "old.db")
    with engine.begin() as conn:
        _run_startup_migrations(conn)
    for table in ("workflows", "calls", "api_clients"):
        assert "tenant_id" in columns(engine, table), table
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT name, tenant_id FROM workflows").one() == ("Existing desk", None)   # shared by everyone
        assert conn.exec_driver_sql("SELECT call_sid, tenant_id FROM calls").one() == ("CA1", None)
        assert conn.exec_driver_sql("SELECT client_name, tenant_id FROM api_clients").one() == ("Monitor", None)


def test_running_the_migration_twice_changes_nothing(tmp_path):
    engine = old_database(tmp_path / "old.db")
    for _ in range(2):
        with engine.begin() as conn:
            _run_startup_migrations(conn)
    assert list(columns(engine, "workflows")).count("tenant_id") == 1


def test_new_tables_are_created_by_the_normal_startup(tmp_path):
    from app.database import Base
    from app import models  # noqa: F401
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    Base.metadata.create_all(engine)
    names = set(sa.inspect(engine).get_table_names())
    assert {"tenants", "tenant_memberships", "phone_numbers", "number_allocation_events", "provider_credentials",
            "model_endpoints", "vector_stores", "admin_audit"} <= names
