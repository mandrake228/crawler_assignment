"""Адреса: проверка адреса и превращение ссылки со страницы в полный адрес."""

from urllib.parse import urldefrag, urljoin, urlsplit

from crawler.errors import InvalidArgumentError, shown

WEB_SCHEMES = ("http", "https")  # только такие адреса краулер умеет загружать
MAX_URL_LENGTH = 2000  # знаков: длиннее адрес не берут карты сайтов и многие серверы
EDGE_JUNK = "".join(map(chr, range(33)))  # пробел и управляющие знаки: края ссылки


def require_url(value):
    """Вернуть value, если это адрес http(s) с хостом и без пароля; иначе отказать."""
    if type(value) is not str:
        raise InvalidArgumentError(f"url: нужна строка, получено {shown(value)}")
    if len(value) > MAX_URL_LENGTH:
        raise InvalidArgumentError(
            f"url: адрес длиннее {MAX_URL_LENGTH} знаков не принимается"
        )
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
    if scheme not in WEB_SCHEMES or not host or not clean:
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


def tidy_reference(reference):
    """Привести ссылку из атрибута тега к виду адреса, как это делает браузер."""
    text = reference.strip(EDGE_JUNK)
    for mark in "\t\n\r":  # табуляцию и переводы строки браузер выбрасывает
        text = text.replace(mark, "")
    # обратную косую черту до знаков ? и # браузер читает как обычную
    cut = min((text.find(mark) for mark in "?#" if mark in text), default=len(text))
    text = text[:cut].replace("\\", "/") + text[cut:]
    return text.replace(" ", "%20")  # пробел внутри адреса браузер кодирует


def absolute_url(base_url, reference):
    """Полный адрес ссылки от проверенной базы; None — это не веб-страница."""
    if type(reference) is not str:
        raise InvalidArgumentError(f"url: нужна строка, получено {shown(reference)}")
    text = tidy_reference(reference)
    if not text or text.startswith("#"):  # пусто или якорь: та же самая страница
        return None
    try:
        joined = urldefrag(urljoin(base_url, text)).url  # якорь адреса не меняет
        scheme = urlsplit(joined).scheme
    except ValueError as error:  # скобка адреса IPv6 без пары
        raise InvalidArgumentError(
            f"url: адрес не разобрать, получено {shown(reference)}"
        ) from error
    if scheme not in WEB_SCHEMES:  # mailto:, tel:, javascript:, data: — не страницы
        return None
    return require_url(joined)


def same_host(first, second):
    """True, если у двух проверенных адресов одно и то же имя хоста."""
    # имя сравнивается в записи для сети: «пример.рф» и «xn--e1afmkfd.xn--p1ai» — одно
    hosts = [(urlsplit(url).hostname or "").encode("idna") for url in (first, second)]
    return hosts[0] == hosts[1]
