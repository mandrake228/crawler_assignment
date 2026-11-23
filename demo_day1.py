"""Демонстрация дня 1: краулер против учебного сайта. Сама сверяет результат."""

import asyncio
import gc
import logging
import re
import sys
import time
import warnings

from checks import check, expect_error, problems, section

USAGE = "Запуск: .venv\\Scripts\\python demo_day1.py [порт учебного сайта]"
INSTALL = "Установка: .venv\\Scripts\\python -m pip install -r requirements.txt"

try:
    from crawler import AsyncCrawler, CrawlerError, FetchError, InvalidArgumentError
    from local_site import LocalSite, read_port
except ModuleNotFoundError as error:
    sys.exit(f"Нет модуля {error.name}.\n{INSTALL}\n{USAGE}")


class LogCollector(logging.Handler):
    """Складывает записи журнала краулера в список вместо печати."""

    def __init__(self):
        """Начать с пустого списка записей."""
        super().__init__()
        self._entries = []

    @property
    def entries(self):
        """Копия собранных записей: пары «уровень, текст»."""
        return list(self._entries)

    def emit(self, record):
        """Запомнить уровень и текст записи."""
        self._entries.append((record.levelname, record.getMessage()))


def collect_logs():
    """Направить журнал краулера в новый сборщик и вернуть его."""
    collector = LogCollector()
    crawler_log = logging.getLogger("crawler")
    for handler in list(crawler_log.handlers):
        crawler_log.removeHandler(handler)
    crawler_log.addHandler(collector)
    crawler_log.setLevel(logging.INFO)
    crawler_log.propagate = False  # записи не идут дальше, в терминал
    return collector


def facts(error):
    """Адрес, категория, код ошибки загрузки и имя её причины."""
    cause = type(getattr(error, "__cause__", None)).__name__
    return (getattr(error, "url", None), getattr(error, "kind", None),
            getattr(error, "status", None), cause)


def without_seconds(message):
    """Заменить время в конце записи на «#.## с»: оно своё у каждого запуска."""
    return re.sub(r"\d+\.\d\d с$", "#.## с", message)


async def passes(action):
    """Выполнить действие; True, если оно прошло без ошибки."""
    try:
        await action()
    except Exception:
        return False
    return True


async def timed(urls, **settings):
    """Загрузить список новым краулером; вернуть страницы и секунды."""
    crawler = AsyncCrawler(**settings)
    started = time.perf_counter()
    try:
        pages = await crawler.fetch_urls(urls)
        seconds = time.perf_counter() - started
    finally:
        await crawler.close()
    return pages, seconds


async def direct(urls, **settings):
    """Вызвать fetch_url для всех адресов сразу; вернуть тексты или ошибки."""
    crawler = AsyncCrawler(**settings)
    try:
        calls = (crawler.fetch_url(url) for url in urls)
        return await asyncio.gather(*calls, return_exceptions=True)
    finally:
        await crawler.close()


async def show_one_page(site):
    """Раздел 1: одна страница, её текст и счётчик сайта."""
    section("1. Одна страница")
    site.reset()
    crawler = AsyncCrawler()
    try:
        text = await crawler.fetch_url(site.base_url + "/page/1")
    finally:
        await crawler.close()
    print(f"  текст страницы: {text!r}")
    check("fetch_url вернул текст страницы", text == "Страница 1")
    check("сайт получил ровно один запрос", site.hits == {"/page/1": 1})
    check("предел конкурентности по умолчанию — 10", crawler.max_concurrent == 10)


async def show_many_pages(site):
    """Раздел 2: список страниц, порядок ключей, повторы и пустой список."""
    section("2. Список страниц")
    urls = [f"{site.base_url}/page/{number}" for number in range(1, 7)]
    texts = ["Страница 1", "Страница 2", "Страница 3",
             "Страница 4", "Страница 5", "Страница 6"]
    crawler = AsyncCrawler(max_concurrent=5)
    try:
        pages = await crawler.fetch_urls(urls)
        print(f"  загружено страниц: {len(pages)}")
        check("в словаре шесть страниц", len(pages) == 6)
        check("ключи идут в порядке списка", list(pages) == urls)
        check("у каждого адреса свой текст", list(pages.values()) == texts)

        site.reset()
        twice = await crawler.fetch_urls([urls[0], urls[1], urls[0], urls[0]])
        check("повторный адрес загружен один раз",
              site.hits == {"/page/1": 1, "/page/2": 1})
        check("в словаре два адреса в порядке первого появления",
              list(twice) == urls[:2])

        site.reset()
        check("пустой список — пустой словарь", await crawler.fetch_urls([]) == {})
        check("на пустой список сайт не получил запросов", site.hits == {})
        pair = await crawler.fetch_urls(tuple(urls[:2]))
        check("кортеж адресов принят как список",
              pair == {urls[0]: "Страница 1", urls[1]: "Страница 2"})
    finally:
        await crawler.close()

    site.reset()
    await timed(urls, max_concurrent=1)
    print(f"  шесть страниц при пределе 1: соединений {site.connections}")
    check("шесть страниц подряд прошли по одному соединению", site.connections == 1)


async def show_limit(site):
    """Раздел 3: одновременно идёт не больше max_concurrent запросов."""
    section("3. Предел конкурентности")
    # хвост ?n= делает адреса разными: одинаковые загрузились бы один раз
    urls = [f"{site.base_url}/slow/150?n={number}" for number in range(1, 9)]
    site.reset()
    pages, _ = await timed(urls, max_concurrent=3)
    print(f"  восемь страниц при пределе 3: одновременно шло {site.max_active}")
    check("все восемь страниц загружены", len(pages) == 8)
    check("одновременно шло ровно три запроса", site.max_active == 3)
    check("сайт получил восемь запросов", site.hits == {"/slow/150": 8})

    site.reset()
    texts = await direct(urls[:6], max_concurrent=2)
    check("шесть прямых вызовов fetch_url: одновременно шло два",
          site.max_active == 2)
    check("прямые вызовы вернули шесть текстов",
          texts == ["Медленная страница: 150 мс"] * 6)

    # вторая страница ждёт очереди 0.3 с — дольше таймаута соединения 0.2 с
    pair = [f"{site.base_url}/slow/300?n={number}" for number in (1, 2)]
    waited, _ = await timed(pair, max_concurrent=1, connect_timeout=0.2)
    check("очередь в fetch_urls не тратит таймаут соединения", len(waited) == 2)
    both = await direct(pair, max_concurrent=1, connect_timeout=0.2)
    check("очередь в прямых вызовах тоже не тратит таймаут",
          both == ["Медленная страница: 300 мс"] * 2)


async def show_speed(site):
    """Раздел 4: тот же список последовательно и параллельно."""
    section("4. Последовательно и параллельно")
    urls = [f"{site.base_url}/slow/300?n={number}" for number in range(1, 6)]
    one_by_one, slow = await timed(urls, max_concurrent=1)
    together, fast = await timed(urls, max_concurrent=5)
    print(f"  последовательно, max_concurrent=1: {slow:.2f} с")
    print(f"  параллельно, max_concurrent=5:     {fast:.2f} с")
    print(f"  быстрее в {slow / fast:.1f} раза")
    check("оба прогона загрузили по пять страниц",
          (len(one_by_one), len(together)) == (5, 5))
    check("последовательный прогон длился больше секунды", slow > 1.0)
    check("параллельный прогон уложился в секунду", fast < 1.0)
    check("параллельно минимум вдвое быстрее", slow >= 2 * fast)


# описание сбоя, адрес учебного сайта, ожидаемые категория, код и причина
FAILURES = (
    ("страницы нет", "/status/404", "http", 404, "ClientResponseError"),
    ("сервер сломан", "/status/500", "http", 500, "ClientResponseError"),
    ("сервер молчит", "/slow/600", "timeout", None, "SocketTimeoutError"),
    ("ответ идёт по капле", "/drip/20", "timeout", None, "TimeoutError"),
    ("соединение оборвано", "/drop", "network", None, "ServerDisconnectedError"),
)


class Buggy(AsyncCrawler):
    """Краулер с ошибкой в программе: вместо загрузки делит на ноль."""

    async def fetch_url(self, url):
        """Упасть ошибкой, которая не относится к загрузке."""
        return 1 / 0


async def show_failures(site):
    """Раздел 5: три категории сбоя и список, где часть адресов не отвечает."""
    section("5. Сбои не роняют программу")
    base = site.base_url
    # /slow/600 молчит 0.6 с, /drip/20 идёт 2 с: оба дольше своих таймаутов
    crawler = AsyncCrawler(read_timeout=0.2, total_timeout=0.7)
    try:
        for description, path, kind, status, cause in FAILURES:
            error = await expect_error(description, FetchError,
                                       lambda: crawler.fetch_url(base + path))
            check(f"{description}: {kind}, код {status}, причина {cause}",
                  facts(error) == (base + path, kind, status, cause))

        codes = (400, 401, 403, 410, 429, 502, 503, 599)
        pages = await crawler.fetch_urls([f"{base}/status/{code}" for code in codes])
        check("ни один ответ с кодом от 400 не попал в словарь", pages == {})
        check("код 399 — ещё не сбой",
              await crawler.fetch_url(base + "/status/399") == "Ответ с кодом 399")

        text = await crawler.fetch_url(base + "/charset/utf-8")
        check("нечитаемые байты заменены знаком замены, сбоя нет",
              text == "Стр ��")
        text = await crawler.fetch_url(base + "/charset/hex")
        check("кодировка не для текста в заголовке не роняет загрузку",
              text == "Стр ��")

        good, also_good = base + "/page/a", base + "/page/b"
        mixed = [good, base + "/status/404", base + "/slow/600", also_good,
                 base + "/drop"]
        pages = await crawler.fetch_urls(mixed)
        print(f"  из пяти адресов загружено: {len(pages)}")
        check("в словаре только удачные страницы",
              pages == {good: "Страница a", also_good: "Страница b"})
        await expect_error("ошибка в программе не прячется среди сбоев",
                           ZeroDivisionError, lambda: Buggy().fetch_urls([good]))
    finally:
        await crawler.close()

    # соседний порт никто не слушает; Windows сообщает об этом через две секунды
    spare = site.port + 1 if site.port < 65535 else site.port - 1
    nobody = f"http://127.0.0.1:{spare}/"
    answers = await direct([nobody])
    check("по адресу никто не отвечает: network, причина ClientConnectorError",
          facts(answers[0]) == (nobody, "network", None, "ClientConnectorError"))


async def show_logs(site):
    """Раздел 6: что краулер пишет в журнал."""
    section("6. Журнал")
    collector = collect_logs()
    good, bad = site.base_url + "/page/7", site.base_url + "/status/404"
    await timed([good, bad], max_concurrent=1)  # предел 1: порядок записей постоянный
    lines = [(level, without_seconds(text)) for level, text in collector.entries]
    for level, text in lines:
        print(f"  {level}: {text}")
    check("записи идут парами: начало и итог первой страницы, потом второй",
          [text.split(":")[0] for _, text in lines]
          == ["начало", "успех", "начало", "сбой"])
    check("начало загрузки записано для обоих адресов",
          [line for line in lines if line[1].startswith("начало: ")]
          == [("INFO", f"начало: {good}"), ("INFO", f"начало: {bad}")])
    check("успех записан с кодом ответа и размером",
          ("INFO", f"успех: {good} — код 200, 10 симв., #.## с") in lines)
    check("сбой записан с адресом и типом ошибки",
          ("WARNING", f"сбой: {bad}: HTTP-ошибка, код 404 (ClientResponseError)")
          in lines)


async def show_input(site):
    """Раздел 7: негодные аргументы отсекаются до первого запроса."""
    section("7. Негодный ввод")
    site.reset()
    collector = collect_logs()
    page, local = site.base_url + "/page/8", f"127.0.0.1:{site.port}/page/8"
    secret = "http://user:secret@" + local
    refusals = []
    for description, make in (
        ("предел 0", lambda: AsyncCrawler(0)),
        ("предел отрицательный", lambda: AsyncCrawler(-1)),
        ("предел дробный", lambda: AsyncCrawler(2.5)),
        ("предел True", lambda: AsyncCrawler(True)),
        ("предел строкой", lambda: AsyncCrawler("5")),
        ("таймаут 0", lambda: AsyncCrawler(connect_timeout=0)),
        ("таймаут отрицательный", lambda: AsyncCrawler(connect_timeout=-1)),
        ("таймаут True", lambda: AsyncCrawler(connect_timeout=True)),
        ("таймаут строкой", lambda: AsyncCrawler(read_timeout="5")),
        ("таймаут не число", lambda: AsyncCrawler(read_timeout=float("nan"))),
        ("таймаут бесконечный", lambda: AsyncCrawler(total_timeout=float("inf"))),
        ("таймаут больше часа", lambda: AsyncCrawler(total_timeout=3601)),
        ("категория ошибки строкой", lambda: FetchError(page, "http", "Error")),
        ("порт сайта 0", lambda: LocalSite(0)),
    ):
        refusals.append(await expect_error(description, InvalidArgumentError, make))
    await expect_error("порт сайта занят", CrawlerError,
                       lambda: LocalSite(site.port).start())

    crawler = AsyncCrawler()
    try:
        for description, value in (
            ("адрес None", None),
            ("адрес без схемы", local),
            ("адрес не http", "ftp://" + local),
            ("адрес без хоста", "http://"),
            ("порт адреса не число", "http://127.0.0.1:abc/page/8"),
            ("две точки подряд в имени хоста", "http://a..b/"),
            ("адрес с пробелом", page + " 8"),
            ("адрес с переводом строки", page + "\n"),
            ("адрес с паролем", secret),
            ("адрес с паролем без схемы", secret[7:]),
        ):
            refusals.append(await expect_error(
                description, InvalidArgumentError, lambda: crawler.fetch_url(value)))
        for description, values in (
            ("строка вместо списка", page),
            ("строка с паролем вместо списка", secret),
            ("множество вместо списка", {page}),
            ("словарь вместо списка", {page: 1}),
            ("негодный адрес в списке", [page, 5]),
            ("адрес с паролем в списке", [page, secret]),
        ):
            refusals.append(await expect_error(
                description, InvalidArgumentError, lambda: crawler.fetch_urls(values)))
    finally:
        await crawler.close()
    check("пароль не повторён ни в одном отказе",
          all("secret" not in str(refusal) for refusal in refusals))
    check("пароль не попал в журнал", "secret" not in str(collector.entries))
    check("при негодном вводе сайт не получил ни одного запроса", site.hits == {})


async def reopen(site):
    """Закрыть краулер до запросов, после запроса и дважды; открыть заново."""
    page, other = site.base_url + "/page/8", site.base_url + "/page/9"
    crawler = AsyncCrawler()
    try:
        check("закрытие без единого запроса проходит", await passes(crawler.close))
        text = await crawler.fetch_url(page)
        # второй цикл событий в соседнем потоке: краулер открыт не в нём
        await expect_error(
            "открытый краулер в другом asyncio.run()", CrawlerError,
            lambda: asyncio.to_thread(asyncio.run, crawler.fetch_url(page)))
        await crawler.close()
        check("повторное закрытие проходит", await passes(crawler.close))
        again = await crawler.fetch_url(other)
        check("после закрытия краулер снова загружает страницы",
              (text, again) == ("Страница 8", "Страница 9"))
    finally:
        await crawler.close()


async def show_close(site):
    """Раздел 8: закрытие краулера, в том числе не вовремя."""
    section("8. Закрытие")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        await reopen(site)
        gc.collect()  # краулер из reopen уже не нужен: забытая сессия подаст голос
    unclosed = [item for item in caught if issubclass(item.category, ResourceWarning)]
    check("после close не осталось незакрытых сессий", unclosed == [])

    hasty = AsyncCrawler(max_concurrent=1)
    urls = [f"{site.base_url}/slow/200?n={number}" for number in range(1, 4)]
    tasks = [asyncio.create_task(hasty.fetch_url(url)) for url in urls]
    await asyncio.sleep(0.1)  # первая страница загружается, две ждут очереди
    await hasty.close()
    check("close() во время загрузки дождался всех трёх страниц",
          [task.done() for task in tasks] == [True, True, True])
    texts = await asyncio.gather(*tasks, return_exceptions=True)
    check("ни одна из трёх страниц не потеряна",
          texts == ["Медленная страница: 200 мс"] * 3)


SECTIONS = (show_one_page, show_many_pages, show_limit, show_speed,
            show_failures, show_logs, show_input, show_close)


async def attempt(show, site):
    """Провести раздел; падение раздела — расхождение, а не обрыв демонстрации."""
    try:
        await show(site)
    except Exception as error:
        reason = f"{type(error).__name__}: {error}"
        check(f"раздел {show.__name__} дошёл до конца ({reason})", False)


async def run_demo(port):
    """Запустить учебный сайт и провести по нему все разделы."""
    site = LocalSite(port)
    collect_logs()  # сборщик не нужен, но журнал перестаёт печататься в терминал
    await site.start()
    try:
        for show in SECTIONS:
            await attempt(show, site)
    finally:
        await site.stop()


def main(arguments=None):
    """Провести демонстрацию; вернуть 0, если всё сошлось, и 1, если нет."""
    port = read_port(sys.argv[1:] if arguments is None else arguments)
    if port is None:
        print(USAGE)
        return 1
    problems.clear()  # расхождения прошлого запуска в этой же программе не в счёт
    try:
        asyncio.run(run_demo(port))
    except CrawlerError as error:
        print(f"Демонстрация не началась: {error}")
        print(USAGE)
        return 1
    print()
    if problems:
        print(f"ДЕМОНСТРАЦИЯ ПРОВАЛЕНА. Расхождений: {len(problems)}")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("Демонстрация дня 1 завершена успешно: расхождений нет.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
