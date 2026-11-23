"""Асинхронный HTTP-клиент: класс AsyncCrawler загружает страницы параллельно."""

import asyncio
import logging
import time
from urllib.parse import urlsplit

import aiohttp

from crawler.errors import (
    CrawlerError, FailureKind, FetchError, InvalidArgumentError, is_whole, shown,
)

logger = logging.getLogger(__name__)

MAX_TIMEOUT = 3600  # секунд: таймаут длиннее часа — уже не таймаут


def require_count(name, value):
    """Вернуть value, если это целое число от 1; иначе отказать."""
    if not is_whole(value) or value < 1:
        raise InvalidArgumentError(
            f"{name}: нужно целое число от 1, получено {shown(value)}"
        )
    return value


def require_seconds(name, value):
    """Вернуть value, если это число секунд больше 0 и до часа; иначе отказать."""
    is_number = is_whole(value) or type(value) is float
    if not is_number or not 0 < value <= MAX_TIMEOUT:
        raise InvalidArgumentError(
            f"{name}: нужно число секунд больше 0 и не больше {MAX_TIMEOUT}, "
            f"получено {shown(value)}"
        )
    return value


def require_url(value):
    """Вернуть value, если это адрес http(s) с хостом и без пароля; иначе отказать."""
    if type(value) is not str:
        raise InvalidArgumentError(f"url: нужна строка, получено {shown(value)}")
    try:
        parts = urlsplit(value)
        # чтение порта заодно проверяет его: не число или больше 65535 — ValueError
        scheme, host, _ = parts.scheme, parts.hostname, parts.port
        secret = parts.username is not None or parts.password is not None
        (host or "").encode("idna")  # имя хоста с пустой частью, как в a..b
    except ValueError:  # негодный порт, скобка адреса IPv6, имя хоста
        scheme, host, secret = "", None, False
    if secret:
        raise InvalidArgumentError("url: адрес с логином или паролем не принимается")
    clean = value.isprintable() and " " not in value  # без пробелов и переводов строки
    if scheme not in ("http", "https") or not host or not clean:
        raise InvalidArgumentError(
            "url: нужен адрес вида http://хост или https://хост, "
            f"получено {shown(value)}"
        )
    return value


def require_urls(values):
    """Вернуть список адресов, если это список или кортеж годных адресов."""
    if type(values) not in (list, tuple):
        raise InvalidArgumentError(
            f"urls: нужен список или кортеж адресов, получено {shown(values)}"
        )
    return [require_url(value) for value in values]


def to_text(body, encoding):
    """Превратить байты страницы в текст; нечитаемые байты заменить знаком �."""
    try:
        return body.decode(encoding, errors="replace")
    except (LookupError, ValueError):  # сервер назвал кодировку не для текста
        return body.decode("utf-8", errors="replace")


class AsyncCrawler:
    """Загружает страницы через одну сессию и держит предел конкурентности."""

    MAX_CONCURRENT = 10  # запросов одновременно, если предел не задан
    CONNECT_TIMEOUT = 5.0  # секунд на установку соединения
    READ_TIMEOUT = 15.0  # секунд тишины, пока читается ответ
    TOTAL_TIMEOUT = 60.0  # секунд на весь запрос от начала до конца

    def __init__(
        self,
        max_concurrent: int = MAX_CONCURRENT,
        *,
        connect_timeout: float = CONNECT_TIMEOUT,
        read_timeout: float = READ_TIMEOUT,
        total_timeout: float = TOTAL_TIMEOUT,
    ) -> None:
        """Запомнить настройки; сеть не трогается до первого запроса."""
        self._max_concurrent = require_count("max_concurrent", max_concurrent)
        self._timeout = aiohttp.ClientTimeout(
            total=require_seconds("total_timeout", total_timeout),
            connect=require_seconds("connect_timeout", connect_timeout),
            sock_read=require_seconds("read_timeout", read_timeout),
        )
        self._session = None
        self._semaphore = None
        self._loop = None
        self._idle = None  # событие «ни одна страница сейчас не в работе»
        self._pending = 0  # сколько страниц загружается или ждёт очереди

    @property
    def max_concurrent(self):
        """Предел конкурентности: сколько запросов идёт одновременно."""
        return self._max_concurrent

    def _open(self):
        """Вернуть сессию и семафор; при первом запросе создать их."""
        loop = asyncio.get_running_loop()
        if self._session is None:
            # пул того же размера, что и предел: под семафором в нём никто не ждёт
            connector = aiohttp.TCPConnector(limit=self._max_concurrent)
            self._session = aiohttp.ClientSession(
                timeout=self._timeout, connector=connector
            )
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
            self._idle = asyncio.Event()
            self._loop = loop
        elif self._loop is not loop:
            raise CrawlerError(
                "краулер открыт в другом asyncio.run(): "
                "вызывайте close() там же, где шла загрузка"
            )
        return self._session, self._semaphore

    async def close(self) -> None:
        """Дождаться идущих загрузок и закрыть сессию; повторный вызов безвреден."""
        while self._pending:  # закрыть сессию под страницей — потерять её
            await self._idle.wait()
        session = self._session
        # сначала забыть сессию, потом закрыть: повторный вызов её уже не увидит
        self._session = self._semaphore = self._idle = self._loop = None
        if session is not None:
            await session.close()

    async def fetch_url(self, url: str) -> str:
        """Загрузить одну страницу и вернуть её текст; сбой — FetchError."""
        require_url(url)
        session, semaphore = self._open()
        self._pending += 1
        self._idle.clear()
        try:
            async with semaphore:  # очередь здесь: таймауты запроса ещё не идут
                return await self._fetch(session, url)
        finally:
            self._pending -= 1
            if not self._pending:
                self._idle.set()  # страниц в работе нет: close() может закрывать

    async def _fetch(self, session, url):
        """Выполнить запрос; сбой библиотеки превратить в FetchError."""
        logger.info("начало: %s", url)
        started = time.perf_counter()
        try:
            async with session.get(url) as response:
                response.raise_for_status()  # код от 400 — исключение
                status = response.status
                body = await response.read()
                encoding = response.get_encoding()
        # порядок веток важен: первые две ошибки — тоже потомки ClientError
        except aiohttp.ClientResponseError as error:
            code = error.status or None  # 0 значит «кода нет»: петля перенаправлений
            raise self._failure(url, FailureKind.HTTP, error, code) from error
        except asyncio.TimeoutError as error:  # с Python 3.11 это TimeoutError
            raise self._failure(url, FailureKind.TIMEOUT, error) from error
        except (aiohttp.ClientError, UnicodeError) as error:
            # UnicodeError: перенаправление на адрес с негодным именем хоста
            raise self._failure(url, FailureKind.NETWORK, error) from error
        text = to_text(body, encoding)
        seconds = time.perf_counter() - started
        logger.info(
            "успех: %s — код %s, %s симв., %.2f с", url, status, len(text), seconds
        )
        return text

    @staticmethod
    def _failure(url, kind, error, status=None):
        """Записать сбой в журнал и вернуть ошибку загрузки."""
        failure = FetchError(url, kind, type(error).__name__, status)
        logger.warning("сбой: %s", failure)
        return failure

    async def fetch_urls(self, urls: list[str]) -> dict[str, str]:
        """Загрузить страницы параллельно; вернуть «URL → текст» для удачных."""
        unique = list(dict.fromkeys(require_urls(urls)))  # повтор — один раз
        results = await asyncio.gather(
            *(self.fetch_url(url) for url in unique), return_exceptions=True
        )
        pages = {}
        for url, result in zip(unique, results):
            if isinstance(result, FetchError):
                continue  # сбой уже записан в журнал внутри fetch_url
            if isinstance(result, BaseException):
                raise result  # не сбой загрузки, а ошибка в программе: не прячем
            pages[url] = result
        return pages
