"""День 1 на настоящих сайтах: статус каждого адреса и выигрыш от параллельности."""

import asyncio
import logging
import sys
import time

USAGE = "Запуск: .venv\\Scripts\\python live_day1.py"
INSTALL = "Установка: .venv\\Scripts\\python -m pip install -r requirements.txt"

try:
    from crawler import AsyncCrawler
except ModuleNotFoundError as error:
    sys.exit(f"Нет модуля {error.name}.\n{INSTALL}\n{USAGE}")

URLS = [
    "https://example.com",
    "https://www.python.org",
    "https://docs.aiohttp.org",
    "https://httpbin.org/delay/1",
    "https://httpbin.org/delay/1?again=1",  # хвост делает адрес отдельным
    "https://httpbin.org/delay/2",
    "https://httpbin.org/status/404",
    "https://no-such-host.invalid/",
]


async def timed(urls, max_concurrent):
    """Загрузить список новым краулером; вернуть страницы и секунды."""
    crawler = AsyncCrawler(max_concurrent=max_concurrent)
    started = time.perf_counter()
    try:
        pages = await crawler.fetch_urls(urls)
        seconds = time.perf_counter() - started
    finally:
        await crawler.close()
    return pages, seconds


async def main():
    """Загрузить адреса по одному и все сразу; напечатать статусы и время."""
    print("--- по одному: max_concurrent=1 ---")
    _, slow = await timed(URLS, max_concurrent=1)
    print(f"--- все сразу: max_concurrent={len(URLS)} ---")
    pages, fast = await timed(URLS, max_concurrent=len(URLS))
    print("--- итог ---")
    for url in URLS:
        if url in pages:
            print(f"  загружен, {len(pages[url])} симв.: {url}")
        else:
            print(f"  НЕ загружен, причина в журнале выше: {url}")
    print(f"Загружено {len(pages)} страниц из {len(URLS)}")
    print(f"По одному: {slow:.2f} с. Все сразу: {fast:.2f} с. "
          f"Быстрее в {slow / fast:.1f} раза.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(main())
