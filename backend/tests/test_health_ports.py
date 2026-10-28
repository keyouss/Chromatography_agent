from sqlalchemy.engine import Engine

from chrom_platform.infrastructure.health import DependencyStatus, database_health, redis_health


class _Connection:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.queries: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def exec_driver_sql(self, query: str):
        self.queries.append(query)
        if self.should_fail:
            raise ConnectionError("database offline")


class _Engine:
    def __init__(self, should_fail: bool = False) -> None:
        self.connection = _Connection(should_fail)

    def connect(self):
        return self.connection


class _Redis:
    def __init__(self, value=True, error: Exception | None = None) -> None:
        self.value = value
        self.error = error
        self.calls = 0

    def ping(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.value


def test_database_health_requires_a_real_select() -> None:
    missing = database_health(None)
    assert missing.status is DependencyStatus.UNAVAILABLE
    assert missing.reason == "NOT_CONFIGURED"

    healthy_engine = _Engine()
    healthy = database_health(healthy_engine)  # type: ignore[arg-type]
    assert healthy.status is DependencyStatus.READY
    assert healthy_engine.connection.queries == ["SELECT 1"]

    failed = database_health(_Engine(True))  # type: ignore[arg-type]
    assert failed.status is DependencyStatus.UNAVAILABLE


def test_redis_health_calls_ping_and_never_treats_url_as_ready() -> None:
    assert redis_health(None).status is DependencyStatus.UNAVAILABLE
    client = _Redis()
    assert redis_health(client).status is DependencyStatus.READY
    assert client.calls == 1
    assert redis_health(_Redis(False)).status is DependencyStatus.UNAVAILABLE
    assert redis_health(_Redis(error=TimeoutError())).status is DependencyStatus.UNAVAILABLE

