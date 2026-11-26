"""Асинхронный веб-краулер: всё открытое импортируется отсюда одной строкой."""

from crawler.client import AsyncCrawler
from crawler.errors import CrawlerError, FailureKind, FetchError, InvalidArgumentError
from crawler.parser import HTMLParser, page_summary

__all__ = [
    "AsyncCrawler",
    "CrawlerError",
    "FailureKind",
    "FetchError",
    "HTMLParser",
    "InvalidArgumentError",
    "page_summary",
]
