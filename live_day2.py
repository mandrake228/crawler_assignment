"""День 2 на настоящих сайтах: загрузить, разобрать и показать сводку страниц."""

import asyncio
import json
import logging
import sys
import time

USAGE = "Запуск: .venv\\Scripts\\python live_day2.py"
INSTALL = "Установка: .venv\\Scripts\\python -m pip install -r requirements.txt"

try:
    from crawler import AsyncCrawler, CrawlerError, FetchError, page_summary
except ModuleNotFoundError as error:
    sys.exit(f"Нет модуля {error.name}.\n{INSTALL}\n{USAGE}")

URLS = [
    "https://example.com",
    "https://www.python.org",
    "https://docs.aiohttp.org/en/stable/",
    "https://httpbin.org/html",
    "https://httpbin.org/json",  # ответ не в формате HTML: разбора не будет
    "https://httpbin.org/status/404",
    "https://no-such-host.invalid/",
]
LINKS_SHOWN = 5  # столько первых ссылок страницы печатается в сводке


async def main():
    """Загрузить и разобрать адреса параллельно; напечатать сводки и статистику."""
    crawler = AsyncCrawler(max_concurrent=len(URLS))
    started = time.perf_counter()
    try:
        calls = (crawler.fetch_and_parse(url) for url in URLS)
        results = await asyncio.gather(*calls, return_exceptions=True)
    finally:
        await crawler.close()
    seconds = time.perf_counter() - started
    print("--- сводки ---")
    summaries = []
    for url, result in zip(URLS, results):
        if isinstance(result, FetchError):
            print(f"НЕ загружен, причина в журнале выше: {url}")
            continue
        if isinstance(result, BaseException):
            raise result  # не сбой загрузки, а ошибка в программе: не прячем
        summary = page_summary(result)
        summaries.append(summary)
        # в выводе — первые ссылки; сколько их всего, говорит links_count
        short = dict(summary, links=summary["links"][:LINKS_SHOWN])
        print(json.dumps(short, ensure_ascii=False, indent=2))
    print("--- итог ---")
    parsed = [summary for summary in summaries if not summary["failed"]]
    links = sum(summary["links_count"] for summary in parsed)
    length = sum(summary["text_length"] for summary in parsed)
    images = sum(summary["images_count"] for summary in parsed)
    print(f"Загружено {len(summaries)} из {len(URLS)} адресов за {seconds:.2f} с, "
          f"разобрано без потерь: {len(parsed)}.")
    print(f"Ссылок: {links}. Картинок: {images}. Длина текста: {length} симв.")


if __name__ == "__main__":
    # заголовок чужой страницы может не уместиться в кодировку терминала
    sys.stdout.reconfigure(errors="replace")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        asyncio.run(main())
    except CrawlerError as error:  # например, не поставлены пакеты разбора
        sys.exit(f"Скрипт остановлен: {error}")
