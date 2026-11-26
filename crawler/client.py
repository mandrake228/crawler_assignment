"""Асинхронный HTTP-клиент: класс AsyncCrawler загружает страницы параллельно."""

import asyncio
import codecs
import logging
import time

import aiohttp

from crawler.errors import (
    CrawlerError, FailureKind, FetchError, InvalidArgumentError, is_whole, shown,
)
from crawler.parser import HTMLParser, unparsed_page
from crawler.urls import require_url, require_urls

logger = logging.getLogger(__name__)

MAX_TIMEOUT = 3600  # секунд: таймаут длиннее часа — уже не таймаут
HTML_TYPES = ("text/html", "application/xhtml+xml")  # ответ такого типа разбирается
SLOW_CODEC = "punycode"  # кодировка имён хостов: страницу читает минутами


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


def to_text(body, encoding):
    """Превратить байты страницы в текст; нечитаемые байты заменить знаком �."""
    try:
        if codecs.lookup(encoding).name != SLOW_CODEC:
            return body.decode(encoding, errors="replace")
    except (LookupError, ValueError):  # сервер назвал кодировку не для текста
        pass
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
        parser: HTMLParser | None = None,
    ) -> None:
        """Запомнить настройки; сеть не трогается до первого запроса."""
        self._max_concurrent = require_count("max_concurrent", max_concurrent)
        if parser is None:
            parser = HTMLParser()
        elif not isinstance(parser, HTMLParser):  # наследник парсера годится
            raise InvalidArgumentError(
                f"parser: нужен HTMLParser или None, получено {shown(parser)}"
            )
        self._parser = parser
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

    @property
    def parser(self):
        """Парсер, которым краулер разбирает загруженные страницы."""
        return self._parser

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
        text, _, _ = await self._load(url)
        return text

    async def _load(self, url):
        """Проверить адрес, дождаться очереди, загрузить: текст, адрес, тип."""
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
        """Выполнить запрос: текст, адрес ответа и тип содержимого."""
        logger.info("начало: %s", url)
        started = time.perf_counter()
        try:
            async with session.get(url) as response:
                response.raise_for_status()  # код от 400 — исключение
                status = response.status
                address = str(response.url)  # адрес после перенаправлений
                content_type = response.content_type  # без charset и регистра
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
        return text, address, content_type

    @staticmethod
    def _failure(url, kind, error, status=None):
        """Записать сбой в журнал и вернуть ошибку загрузки."""
        failure = FetchError(url, kind, type(error).__name__, status)
        logger.warning("сбой: %s", failure)
        return failure

    async def fetch_and_parse(self, url: str) -> dict:
        """Загрузить страницу и разобрать её; сбой загрузки — FetchError."""
        text, address, content_type = await self._load(url)
        if content_type not in HTML_TYPES:
            shown_type = shown(content_type)
            logger.warning("не HTML: %s — тип %s, разбора нет", url, shown_type)
            return unparsed_page(url)
        try:
            require_url(address)
        except InvalidArgumentError:
            logger.warning("адрес ответа не годится: ссылки считаются от %s", url)
            address = url
        # семафор уже свободен: разбор не занимает место в очереди загрузок
        page = await self._parser.parse_html(text, address)
        page["url"] = url  # в результате — адрес, который просили
        return page

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
