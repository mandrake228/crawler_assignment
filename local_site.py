"""Учебный сайт на этом компьютере: отвечает по правилу, записанному в адресе."""

import asyncio
import sys

from aiohttp import web

from crawler.errors import CrawlerError, InvalidArgumentError, is_whole, shown

HOST = "127.0.0.1"  # адрес «этот компьютер»: снаружи сайт не виден
DEFAULT_PORT = 8421  # порт по умолчанию; занят — передайте сайту другой
GARBLED = "Стр ".encode() + b"\xff\xfe"  # два последних байта — не UTF-8
DRIP_PAUSE = 0.1  # секунд между точками «капающей» страницы
USAGE = "Запуск: .venv\\Scripts\\python local_site.py [порт]"


async def page(request):
    """Обычная страница: её текст известен заранее."""
    return web.Response(text=f"Страница {request.match_info['name']}")


async def slow(request):
    """Медленная страница: отвечает через столько миллисекунд, сколько в адресе."""
    delay_ms = int(request.match_info["ms"])
    await asyncio.sleep(delay_ms / 1000)
    return web.Response(text=f"Медленная страница: {delay_ms} мс")


async def status(request):
    """Ответ с кодом из адреса: 404, 500 или любой другой от 200 до 599."""
    code = int(request.match_info["code"])
    return web.Response(status=code, text=f"Ответ с кодом {code}")


async def charset(request):
    """Страница с нечитаемыми байтами; кодировку в заголовке задаёт адрес."""
    name = request.match_info["name"]
    kind = f"text/html; charset={name}"
    return web.Response(body=GARBLED, headers={"Content-Type": kind})


async def drip(request):
    """«Капающая» страница: отдаёт по точке с паузами, точек — сколько в адресе."""
    response = web.StreamResponse()
    response.content_type = "text/plain"
    await response.prepare(request)
    try:
        for _ in range(int(request.match_info["count"])):
            await response.write(b".")
            await asyncio.sleep(DRIP_PAUSE)
    except ConnectionResetError:
        pass  # клиент ушёл посреди ответа: дописывать некому
    return response


async def drop(request):
    """Обрыв: сайт закрывает соединение, ничего не ответив."""
    request.transport.close()
    return web.Response()  # обработчик обязан вернуть ответ; клиент его не получит


ROUTES = [
    web.get("/page/{name}", page),
    web.get(r"/slow/{ms:\d{1,4}}", slow),
    web.get(r"/status/{code:[2-5]\d\d}", status),
    web.get(r"/charset/{name:[a-z0-9-]{1,20}}", charset),
    web.get(r"/drip/{count:\d{1,2}}", drip),
    web.get("/drop", drop),
]


class LocalSite:
    """Запускает учебный сайт и считает, что у него спрашивали."""

    def __init__(self, port=DEFAULT_PORT):
        """Запомнить порт; сайт начнёт отвечать после start()."""
        if not is_whole(port) or not 1 <= port <= 65535:
            raise InvalidArgumentError(
                f"port: нужно целое число от 1 до 65535, получено {shown(port)}"
            )
        self._port = port
        self._runner = None
        self._hits = {}
        self._peers = set()
        self._active = 0
        self._max_active = 0

    @property
    def port(self):
        """Порт, на котором сайт отвечает."""
        return self._port

    @property
    def base_url(self):
        """Начало адреса любой страницы сайта."""
        return f"http://{HOST}:{self._port}"

    @property
    def hits(self):
        """Сколько раз спросили каждый путь: копия счётчика."""
        return dict(self._hits)

    @property
    def connections(self):
        """Сколько разных соединений открыли клиенты."""
        return len(self._peers)

    @property
    def max_active(self):
        """Наибольшее число запросов, которые сайт обрабатывал одновременно."""
        return self._max_active

    def reset(self):
        """Обнулить счётчики перед новым измерением."""
        self._hits.clear()
        self._peers.clear()
        self._max_active = 0

    @web.middleware
    async def _count(self, request, handler):
        """Посчитать запрос, соединение и то, сколько запросов идёт одновременно."""
        self._hits[request.path] = self._hits.get(request.path, 0) + 1
        if request.transport is not None:
            # у каждого соединения свой адрес клиента: по нему они и различаются
            self._peers.add(request.transport.get_extra_info("peername"))
        self._active += 1
        self._max_active = max(self._max_active, self._active)
        try:
            return await handler(request)
        finally:
            self._active -= 1

    async def start(self):
        """Запустить сайт и вернуть его адрес; повторный вызов ничего не меняет."""
        if self._runner is None:
            app = web.Application(middlewares=[self._count])
            app.add_routes(ROUTES)
            runner = web.AppRunner(
                app,
                access_log=None,  # сайт молчит: в проверках виден журнал краулера
                handler_cancellation=True,  # клиент ушёл — обработчик отменяется
            )
            await runner.setup()
            try:
                await web.TCPSite(runner, HOST, self._port).start()
            except OSError as error:
                await runner.cleanup()
                raise CrawlerError(
                    f"порт {self._port} недоступен, скорее всего занят: "
                    "передайте сайту другой порт"
                ) from error
            self._runner = runner
        return self.base_url

    async def stop(self):
        """Остановить сайт; повторный вызов безвреден."""
        runner, self._runner = self._runner, None
        if runner is not None:
            await runner.cleanup()


async def serve(port):
    """Держать сайт запущенным, пока программу не остановят."""
    site = LocalSite(port)
    print(f"Учебный сайт работает: {await site.start()}/page/1")
    print("Остановить: Ctrl+C")
    try:
        await asyncio.Event().wait()  # событие не наступит: ждём остановки
    finally:
        await site.stop()


def read_port(arguments):
    """Порт из аргументов командной строки; None, если аргументы негодные."""
    if not arguments:
        return DEFAULT_PORT
    text = arguments[0]
    if len(arguments) > 1 or not text.isdecimal() or len(text) > 5:
        return None
    return int(text)


def main(arguments):
    """Запустить сайт как программу; необязательный аргумент — порт."""
    port = read_port(arguments)
    if port is None:
        print(USAGE)
        return 1
    try:
        asyncio.run(serve(port))
    except KeyboardInterrupt:
        print("Сайт остановлен.")
    except CrawlerError as error:
        print(f"Сайт не запустился: {error}")
        print(USAGE)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
