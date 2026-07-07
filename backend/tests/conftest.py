import os

from cryptography.fernet import Fernet

# Must be set BEFORE any app import (settings is read at import time).
os.environ["MAJRA_ENVIRONMENT"] = "test"
os.environ["MAJRA_DATABASE_URL"] = (
    "postgresql+psycopg://majra:majra@localhost:5432/majra_test"
)
os.environ["MAJRA_SECRET_KEY"] = "test-secret"
os.environ["MAJRA_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["MAJRA_META_APP_ID"] = "123456"
os.environ["MAJRA_META_APP_SECRET"] = "shh"
os.environ["MAJRA_PUBLIC_BASE_URL"] = "http://testserver"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import Base, SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    with engine.begin() as conn:
        for schema in ("app", "raw", "norm"):
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    init_db(engine)
    yield


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    tables = ", ".join(
        f'"{t.schema}"."{t.name}"' for t in reversed(Base.metadata.sorted_tables)
    )
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_client(client):
    r = client.post(
        "/auth/register", json={"email": "owner@majra.dev", "password": "s3cret-pass"}
    )
    assert r.status_code == 201, r.text
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return client
