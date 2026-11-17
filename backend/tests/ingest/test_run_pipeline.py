from typing import Self

import pytest

from app.ingest import run_pipeline


class _Result:
    def scalar_one(self) -> bool:
        return False


class _Connection:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def exec_driver_sql(self, *_: object) -> _Result:
        return _Result()


class _Engine:
    def connect(self) -> _Connection:
        return _Connection()


def test_daily_pipeline_does_not_run_when_lock_is_held(monkeypatch) -> None:
    crawled = False

    def crawl() -> None:
        nonlocal crawled
        crawled = True

    monkeypatch.setattr(
        run_pipeline, "tag_stories", lambda *_args, **_kwargs: pytest.fail("tagged")
    )

    assert run_pipeline.run_daily_pipeline(_Engine(), crawl) == 0
    assert not crawled
