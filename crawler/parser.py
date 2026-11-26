"""Разбор HTML: класс HTMLParser достаёт из страницы текст, ссылки и данные."""

import asyncio
import functools
import logging
import re
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from crawler.errors import CrawlerError, InvalidArgumentError, shown
from crawler.urls import absolute_url, require_url, same_host

if TYPE_CHECKING:  # только для подсказок редактора: при запуске пакет не грузится
    from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

INSTALL = "установите пакеты: .venv\\Scripts\\python -m pip install -r requirements.txt"
BUILDER = "lxml"  # разборщик, которому bs4 отдаёт текст страницы
OPEN_TAG = re.compile("<[A-Za-z]")  # так начинается любой открывающий тег
CLOSERS = re.compile(r"</(?:body|html)\s*>", re.IGNORECASE)  # концы тела и документа
# теги, содержимое которых читатель страницы не видит
HIDDEN = ("head", "script", "style", "template", "noscript", "title")
# теги, которые браузер начинает с новой строки
BLOCKS = (
    "address", "article", "aside", "blockquote", "caption", "dd", "details", "div",
    "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
    "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "legend", "li", "main", "menu",
    "nav", "ol", "option", "p", "pre", "section", "summary", "table", "td", "th",
    "tr", "ul",
)
HEADINGS = ("h1", "h2", "h3")
LISTS = ("ul", "ol")
ROW_PLACES = ("thead", "tbody", "tfoot", "tr")  # где в таблице лежат её строки
DOCUMENT = "document"  # отметка в failed: документ не разобран целиком
BASE = "base"  # отметка в failed: база не найдена, ссылки считаются от адреса страницы
# образец результата разбора: ключи и типы значений; у списка показан один элемент
PAGE_SHAPE = {
    "url": "",
    "title": "",
    "text": "",
    "links": [""],
    "metadata": {"title": "", "description": "", "keywords": [""]},
    "images": [{"src": "", "alt": ""}],
    "headings": [{"level": 0, "text": ""}],
    "tables": [{"headers": [""], "rows": [[""]]}],
    "lists": [{"ordered": False, "items": [""]}],
    "failed": [""],
}
LANE = "crawler-parse"  # имя потока разбора
# один поток на весь разбор: два потока Python всё равно не считают одновременно
PARSE_LANE = ThreadPoolExecutor(max_workers=1, thread_name_prefix=LANE)


@functools.cache  # загрузка выполняется один раз; после ошибки попытка повторится
def load_bs4():
    """Загрузить пакеты разбора; если их нет — ошибка с подсказкой, как поставить."""
    try:
        import bs4
        import lxml.etree  # noqa: F401 — разборщик: без него bs4 страницу не разберёт
    except ImportError as error:
        raise CrawlerError(f"нет модуля {error.name}: {INSTALL}") from error
    return bs4


def silence(bs4):
    """Отключить два предупреждения bs4: о тех же случаях парсер пишет в журнал."""
    wanted = [
        ("ignore", None, bs4.XMLParsedAsHTMLWarning, None, 0),
        ("ignore", None, bs4.MarkupResemblesLocatorWarning, None, 0),
    ]
    # список фильтров меняется, только когда наших двух нет в его начале: каждая
    # правка заставляет Python забыть, какие предупреждения он уже показывал
    if warnings.filters[:2] != wanted:
        warnings.filterwarnings("ignore", category=bs4.MarkupResemblesLocatorWarning)
        warnings.filterwarnings("ignore", category=bs4.XMLParsedAsHTMLWarning)


def require_html(value):
    """Вернуть value, если это строка с текстом страницы; иначе отказать."""
    if type(value) is not str:
        raise InvalidArgumentError(f"html: нужна строка, получено {shown(value)}")
    return value


def require_soup(value):
    """Вернуть value, если это дерево тегов или один его тег; иначе отказать."""
    if not isinstance(value, load_bs4().Tag):
        raise InvalidArgumentError(
            f"soup: нужно дерево тегов BeautifulSoup, получено {shown(value)}"
        )
    return value


def blank(shape):
    """Пустое значение по образцу: пустая строка, пустой список, словарь пустых."""
    if type(shape) is dict:
        return {key: blank(inner) for key, inner in shape.items()}
    return type(shape)()  # str() — пустая строка, list() — пустой список


def fits(value, shape):
    """True, если значение устроено как образец: те же типы, все ключи образца."""
    if type(value) is not type(shape):
        return False
    if type(shape) is dict:  # лишние ключи не мешают: наследник вправе добавить своё
        return all(key in value and fits(value[key], shape[key]) for key in shape)
    if type(shape) is list:
        return all(fits(item, shape[0]) for item in value)
    return True


def blank_page(url):
    """Результат разбора, в котором пока ничего нет."""
    page = blank(PAGE_SHAPE)
    page["url"] = url
    return page


def unparsed_page(url):
    """Результат для документа, который не разобран: всё пусто, в failed отметка."""
    page = blank_page(url)
    page["failed"].append(DOCUMENT)
    return page


def page_summary(page: dict) -> dict:
    """Сводка страницы: счётчики вместо длинных полей."""
    if not fits(page, PAGE_SHAPE):
        raise InvalidArgumentError(
            f"page: нужен результат parse_html, получено {shown(page)}"
        )
    return {
        "url": page["url"],
        "title": page["title"],
        "text_length": len(page["text"]),
        "links_count": len(page["links"]),
        "links": list(page["links"]),
        "images_count": len(page["images"]),
        "headings_count": len(page["headings"]),
        "tables_count": len(page["tables"]),
        "lists_count": len(page["lists"]),
        "failed": list(page["failed"]),
    }


def squeezed(text):
    """Строка, в которой любые пробелы подряд сжаты до одного, а края чистые."""
    return " ".join(text.split())


def text_of(node, skip=()):
    """Текст тега: блоки — с новой строки, пробелы сжаты; теги из skip пропущены."""
    bs4 = load_bs4()
    pieces = []
    stack = [(node, iter(node.children))]  # стопка вместо рекурсии: глубина любая
    while stack:
        parent, children = stack[-1]
        child = next(children, None)
        if child is None:  # дети кончились: выходим из тега
            stack.pop()
            if parent.name in BLOCKS:
                pieces.append("\n")
        elif type(child) is bs4.NavigableString:  # текст; комментарий — другого типа
            # перевод строки в исходнике страницы — обычный пробел, а не новая строка
            pieces.append(str(child).replace("\n", " "))
        elif isinstance(child, bs4.Tag) and child.name not in HIDDEN:
            if child.name == "br" or child.name in BLOCKS:
                pieces.append("\n")
            if child.name not in skip:  # пропущенный тег оставляет только разрыв
                stack.append((child, iter(child.children)))
    lines = (squeezed(line) for line in "".join(pieces).split("\n"))
    return "\n".join(line for line in lines if line)


def line_of(node, skip=()):
    """Текст тега одной строкой."""
    return squeezed(text_of(node, skip))


def ids_inside(tags):
    """Номера объектов всего, что лежит внутри данных тегов."""
    # bs4 сравнивает теги по содержимому, поэтому запоминаются номера объектов
    inside = set()
    for tag in tags:
        if id(tag) not in inside:  # внутренности вложенного тега уже учтены
            inside.update(id(inner) for inner in tag.descendants)
    return inside


class HTMLParser:
    """Разбирает HTML страницы: текст, ссылки, метаданные, картинки, таблицы, списки."""

    MAX_DROPPED_SHOWN = 20  # столько отброшенных адресов одного извлечения в журнале

    def __init__(self, *, internal_only: bool = False) -> None:
        """Запомнить настройку: оставлять ли только ссылки на тот же хост."""
        if type(internal_only) is not bool:
            raise InvalidArgumentError(
                f"internal_only: нужно True или False, получено {shown(internal_only)}"
            )
        self._internal_only = internal_only

    @property
    def internal_only(self):
        """True, если в результате остаются только ссылки на хост самой страницы."""
        return self._internal_only

    def make_soup(self, html: str) -> "BeautifulSoup":
        """Разобрать текст HTML в дерево тегов; текст, который не разобрать, — отказ."""
        require_html(html)
        bs4 = load_bs4()
        silence(bs4)
        try:
            # текст после </body> и </html> браузер читает как продолжение страницы,
            # а lxml отбрасывает: эти закрывающие теги убираются до разбора
            return bs4.BeautifulSoup(CLOSERS.sub("", html), BUILDER)
        except (ValueError, bs4.ParserRejectedMarkup) as error:
            # так разборщик отвергает текст: например, с половинкой символа
            raise InvalidArgumentError(
                f"html: разборщик не принял текст ({type(error).__name__})"
            ) from error

    def extract_base_url(self, soup: "BeautifulSoup", url: str) -> str:
        """Адрес, от которого считаются относительные ссылки: <base href> или url."""
        require_soup(soup)
        require_url(url)
        tag = soup.find("base", href=True)
        if tag is None:
            return url
        try:
            base = absolute_url(url, tag["href"])
        except InvalidArgumentError:
            base = None
        if base is None:
            logger.warning("тег base не годится на %s: %s", url, shown(tag["href"]))
            return url
        return base

    def _addresses(self, soup, base_url, tag_name, attribute):
        """Пары «тег, полный адрес» для тегов с адресом в атрибуте."""
        where = shown(base_url)  # база могла прийти со страницы: в журнал — коротко
        found, dropped = [], 0
        for tag in soup.find_all(tag_name, attrs={attribute: True}):
            try:
                address = absolute_url(base_url, tag[attribute])
            except InvalidArgumentError as error:
                dropped += 1
                if dropped <= self.MAX_DROPPED_SHOWN:
                    logger.warning("адрес отброшен на %s: %s", where, error)
                continue
            if address is not None:
                found.append((tag, address))
        if dropped > self.MAX_DROPPED_SHOWN:
            rest = dropped - self.MAX_DROPPED_SHOWN
            logger.warning("на %s отброшено ещё адресов: %s", where, rest)
        return found

    def extract_links(self, soup: "BeautifulSoup", base_url: str) -> list[str]:
        """Полные адреса всех ссылок страницы: без повторов, в порядке появления."""
        require_soup(soup)
        require_url(base_url)
        pairs = self._addresses(soup, base_url, "a", "href")
        return list(dict.fromkeys(address for _, address in pairs))

    def extract_images(self, soup: "BeautifulSoup", base_url: str) -> list[dict]:
        """Все картинки страницы: полный адрес src и подпись alt."""
        require_soup(soup)
        require_url(base_url)
        return [
            {"src": address, "alt": squeezed(tag.get("alt") or "")}
            for tag, address in self._addresses(soup, base_url, "img", "src")
        ]

    def extract_text(self, soup: "BeautifulSoup", selector: str = None) -> str:
        """Текст страницы без скриптов и стилей; с selector — текст найденных тегов."""
        require_soup(soup)
        if selector is None:
            return text_of(soup)
        if type(selector) is not str:
            raise InvalidArgumentError(
                f"selector: нужна строка или None, получено {shown(selector)}"
            )
        try:
            pattern = soup.css.compile(selector)
        except Exception as error:
            # здесь селектор только читается, дерево не тронуто: любая ошибка — о нём
            raise InvalidArgumentError(
                f"selector: нужен селектор CSS, получено {shown(selector)}"
            ) from error
        found = soup.select(pattern)
        # тег внутри уже найденного не повторяется, тег внутри скрытого — не текст
        skip = ids_inside(found) | ids_inside(soup.find_all(HIDDEN))
        shown_tags = (tag for tag in found if tag.name not in HIDDEN)
        texts = [text_of(tag) for tag in shown_tags if id(tag) not in skip]
        return "\n".join(text for text in texts if text)

    def extract_metadata(self, soup: "BeautifulSoup") -> dict:
        """Заголовок title, описание description и ключевые слова keywords."""
        require_soup(soup)
        drawn = ids_inside(soup.find_all("svg"))  # title внутри картинки — подсказка
        titles = [tag for tag in soup.find_all("title") if id(tag) not in drawn]
        content = {"description": "", "keywords": ""}
        for tag in soup.find_all("meta"):
            name = (tag.get("name") or "").strip().lower()
            if name in content and not content[name]:  # берётся первое непустое
                content[name] = squeezed(tag.get("content") or "")
        words = [word.strip() for word in content["keywords"].split(",")]
        return {
            "title": line_of(titles[0]) if titles else "",
            "description": content["description"],
            "keywords": [word for word in words if word],
        }

    def extract_headings(self, soup: "BeautifulSoup") -> list[dict]:
        """Заголовки h1, h2 и h3 в порядке появления: уровень и текст."""
        require_soup(soup)
        headings = []
        for tag in soup.find_all(HEADINGS):
            # заголовок, попавший внутрь другого, идёт своей записью
            text = line_of(tag, skip=HEADINGS)
            if text:
                level = int(tag.name[1])  # цифра из имени тега: h2 — уровень 2
                headings.append({"level": level, "text": text})
        return headings

    def extract_tables(self, soup: "BeautifulSoup") -> list[dict]:
        """Таблицы страницы: строка заголовков и строки данных."""
        require_soup(soup)
        tables = []
        for table in soup.find_all("table"):
            headers, rows = [], []
            # строки берутся только свои и по порядку: вложенная таблица — отдельно
            for place in table.find_all(ROW_PLACES, recursive=False):
                inner = place.find_all("tr", recursive=False)
                for row in [place] if place.name == "tr" else inner:
                    cells = row.find_all(("td", "th"), recursive=False)
                    texts = [line_of(cell, skip=("table",)) for cell in cells]
                    is_header = all(cell.name == "th" for cell in cells)
                    if cells and is_header and not headers and not rows:
                        headers = texts  # первая строка из одних th — заголовки
                    elif cells:
                        rows.append(texts)
            if headers or rows:
                tables.append({"headers": headers, "rows": rows})
        return tables

    def extract_lists(self, soup: "BeautifulSoup") -> list[dict]:
        """Списки ul и ol: вид списка и тексты его пунктов."""
        require_soup(soup)
        lists = []
        for tag in soup.find_all(LISTS):
            # пункты берутся только свои: вложенный список пойдёт отдельной записью
            own = tag.find_all("li", recursive=False)
            texts = [line_of(item, skip=LISTS) for item in own]
            items = [text for text in texts if text]
            if items:
                lists.append({"ordered": tag.name == "ol", "items": items})
        return lists

    async def parse_html(self, html: str, url: str) -> dict:
        """Разобрать страницу в словарь; разбор идёт в отдельном потоке."""
        require_html(html)
        require_url(url)
        if threading.current_thread().name.startswith(LANE):
            # разбор из потока разбора встал бы в очередь за самим собой — навсегда
            raise CrawlerError("parse_html нельзя вызывать из метода извлечения")
        # разбор занимает процессор: в своём потоке он не останавливает цикл событий
        loop = asyncio.get_running_loop()
        try:
            work = loop.run_in_executor(PARSE_LANE, self._parse, html, url)
        except RuntimeError as error:  # поток разбора уже остановлен
            raise CrawlerError("разбор невозможен: программа завершается") from error
        return await work

    def _parse(self, html, url):
        """Собрать результат по разделам; сбой раздела не роняет остальные."""
        load_bs4()  # нет пакета — ошибка выходит наружу: это не сбой страницы
        try:
            soup = require_soup(self.make_soup(html))
        except Exception as error:  # документ не разобран целиком
            reason = type(error.__cause__ or error).__name__
            logger.warning("документ не разобран: %s — %s", url, reason)
            return unparsed_page(url)
        page = blank_page(url)
        start = html[:100].lstrip("\ufeff \t\r\n").lower()
        tagless = OPEN_TAG.search(html) is None
        if tagless:
            logger.warning("в документе нет ни одного тега: %s", url)
        elif start.startswith("<?xml") and "<html" not in html.lower():
            logger.warning("документ похож на XML, а не на HTML: %s", url)
        found = self._section(page, BASE, "", self.extract_base_url, soup, url)
        base = found or url  # база не найдена — ссылки считаются от адреса страницы
        for name, extract, arguments in (
            ("text", self.extract_text, (soup,)),
            ("links", self._page_links, (soup, base, url)),
            ("metadata", self.extract_metadata, (soup,)),
            ("images", self._page_images, (soup, base)),
            ("headings", self.extract_headings, (soup,)),
            ("tables", self.extract_tables, (soup,)),
            ("lists", self.extract_lists, (soup,)),
        ):
            shape = PAGE_SHAPE[name]
            page[name] = self._section(page, name, shape, extract, *arguments)
        page["title"] = page["metadata"]["title"]
        if not page["title"] and not tagless and "metadata" not in page["failed"]:
            logger.warning("нет заголовка title: %s", url)
        logger.info(
            "разбор: %s — ссылок %s, картинок %s, текст %s симв.",
            url, len(page["links"]), len(page["images"]), len(page["text"]),
        )
        return page

    @staticmethod
    def _section(page, name, shape, extract, *arguments):
        """Вызвать извлечение раздела; сбой или не та форма — пустой раздел."""
        try:
            value = extract(*arguments)
        except Exception as error:  # сбой раздела не должен ронять всю страницу
            reason = type(error).__name__
        else:
            if fits(value, shape):
                return value
            reason = "значение не той формы"
        logger.warning("раздел %s не извлечён: %s — %s", name, page["url"], reason)
        page["failed"].append(name)
        return blank(shape)

    def _page_links(self, soup, base_url, url):
        """Ссылки страницы; при internal_only — только на её собственный хост."""
        links = self.extract_links(soup, base_url)
        for link in links:  # и у наследника парсера каждая ссылка — годный адрес
            require_url(link)
        if self._internal_only:
            links = [link for link in links if same_host(link, url)]
        return links

    def _page_images(self, soup, base_url):
        """Картинки страницы; адрес каждой проверен, как адрес ссылки."""
        images = self.extract_images(soup, base_url)
        for image in images:
            require_url(image["src"])
        return images
