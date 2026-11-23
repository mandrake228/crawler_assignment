"""Асинхронный веб-краулер: всё открытое импортируется отсюда одной строкой."""

from crawler.client import AsyncCrawler
from crawler.errors import CrawlerError, FailureKind, FetchError, InvalidArgumentError

__all__ = [
    "AsyncCrawler",
    "CrawlerError",
    "FailureKind",
    "FetchError",
    "InvalidArgumentError",
]

