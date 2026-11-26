"""Демонстрация дня 2: парсер против учебных страниц. Сама сверяет результат."""

import asyncio
import json
import logging
import subprocess
import sys
import threading
import time
import warnings
from pathlib import Path

from checks import check, expect_error, problems, section

USAGE = "Запуск: .venv\\Scripts\\python demo_day2.py [порт учебного сайта]"
INSTALL = "Установка: .venv\\Scripts\\python -m pip install -r requirements.txt"

try:
    from crawler import (
        AsyncCrawler, CrawlerError, FetchError, HTMLParser, InvalidArgumentError,
        page_summary,
    )
    from demo_day1 import attempt, collect_logs, facts
    from local_site import LocalSite, read_port
    from sample_pages import BASED, BIG_ROWS, BROKEN, CATALOG, STUB
except ModuleNotFoundError as error:
    sys.exit(f"Нет модуля {error.name}.\n{INSTALL}\n{USAGE}")

SHOP = "http://shop.example/books/list.html"  # адрес страницы для проверок без сайта
ROOT_HANDLERS = list(logging.getLogger().handlers)  # до первого разбора страницы
KEYS = ["url", "title", "text", "links", "metadata", "images", "headings", "tables",
        "lists", "failed"]
CATALOG_TEXT = (
    "Каталог", "В лавке три полки и страница о нас.", "Полки", "Асинхронность",
    "asyncio", "aiohttp", "Сети", "Архив", "Цены", "Книга", "Цена",
    "Asyncio в примерах", "900", "Сети для всех", "750", "Как найти",
    "Открыть карту", "Прийти",
    # девять ссылок последнего абзаца стоят в исходнике на разных строках
    "Документация Партнёр Написать Меню Наверх Ещё раз о нас Полка с пробелом "
    "Негодный порт С паролем",
)


def empty_page(url, failed=()):
    """Чего ждать, когда извлекать нечего: все разделы пусты."""
    return {
        "url": url,
        "title": "",
        "text": "",
        "links": [],
        "metadata": {"title": "", "description": "", "keywords": []},
        "images": [],
        "headings": [],
        "tables": [],
        "lists": [],
        "failed": list(failed),
    }


def internal_links(base):
    """Пять ссылок «каталога» на хост самой страницы."""
    return [
        f"{base}/html/about.html",
        f"{base}/html/shelf-1",
        f"{base}/html/shelf-2?page=1",
        f"{base}/archive/",
        f"{base}/html/shelf%203",
    ]


def expected_catalog(base):
    """Чего ждать от разбора «каталога», если он пришёл с адреса base/html/catalog."""
    return {
        "url": f"{base}/html/catalog",
        "title": "Каталог книжной лавки",
        "text": "\n".join(CATALOG_TEXT),
        "links": [
            f"{base}/html/about.html",
            f"{base}/html/shelf-1",
            f"{base}/html/shelf-2?page=1",
            f"{base}/archive/",
            "https://docs.python.org/3/",
            "http://example.com/partner",
            f"{base}/html/shelf%203",
        ],
        "metadata": {
            "title": "Каталог книжной лавки",
            "description": "Книги по Python и асинхронности.",
            "keywords": ["python", "asyncio", "книги"],
        },
        "images": [
            {"src": f"{base}/html/img/shop.png", "alt": "Вход в лавку"},
            {"src": f"{base}/static/logo.png", "alt": ""},
        ],
        "headings": [
            {"level": 1, "text": "Каталог"},
            {"level": 2, "text": "Полки"},
            {"level": 2, "text": "Цены"},
            {"level": 3, "text": "Как найти"},
        ],
        "tables": [{
            "headers": ["Книга", "Цена"],
            "rows": [["Asyncio в примерах", "900"], ["Сети для всех", "750"]],
        }],
        "lists": [
            {"ordered": False, "items": ["Асинхронность", "Сети", "Архив"]},
            {"ordered": False, "items": ["asyncio", "aiohttp"]},
            {"ordered": True, "items": ["Открыть карту", "Прийти"]},
        ],
        "failed": [],
    }


def summary_line(url, links, images, length):
    """Запись журнала с итогом разбора страницы."""
    return ("INFO", f"разбор: {url} — ссылок {links}, картинок {images}, "
                    f"текст {length} симв.")


async def parse_logged(html, url=SHOP, parser=None):
    """Разобрать текст с новым сборщиком журнала; вернуть результат и записи."""
    collector = collect_logs()
    page = await (parser or HTMLParser()).parse_html(html, url)
    return page, collector.entries


def links_logged(html, base_url=SHOP):
    """Извлечь ссылки из текста с новым сборщиком; вернуть ссылки и записи."""
    collector = collect_logs()
    parser = HTMLParser()
    links = parser.extract_links(parser.make_soup(html), base_url)
    return links, collector.entries


def levels(entries):
    """Только уровни записей журнала, по порядку."""
    return [level for level, _ in entries]


async def show_catalog(site):
    """Раздел 1: исправная страница — результат разбора целиком."""
    section("1. Исправная страница")
    url = site.base_url + "/html/catalog"
    parser = HTMLParser()
    page = await parser.parse_html(CATALOG, url)
    print(f"  заголовок: {page['title']!r}")
    lines = page["text"].splitlines()
    print(f"  текст: строк {len(lines)}, знаков {len(page['text'])}")
    print(f"  ссылок {len(page['links'])}, картинок {len(page['images'])}, "
          f"заголовков {len(page['headings'])}, таблиц {len(page['tables'])}, "
          f"списков {len(page['lists'])}")
    check("результат разбора «каталога» совпал целиком",
          page == expected_catalog(site.base_url))
    check("у результата десять ключей в постоянном порядке", list(page) == KEYS)
    check("title повторяет заголовок из metadata",
          page["title"] == page["metadata"]["title"] == "Каталог книжной лавки")
    again = await parser.parse_html(CATALOG, url)
    check("повторный разбор даёт равный, но новый словарь",
          again == page and again is not page and again["links"] is not page["links"])
    check("результат сохраняется в JSON и читается обратно без потерь",
          json.loads(json.dumps(page)) == page)
    check("настройка internal_only по умолчанию выключена",
          parser.internal_only is False)
    check("сайт не получил ни одного запроса: парсеру сеть не нужна", site.hits == {})


# ссылка в странице и её полный адрес от базы BASE
BASE = SHOP + "?page=2#frag"
CONVERSIONS = (
    ("item.html", "http://shop.example/books/item.html"),
    ("./item.html", "http://shop.example/books/item.html"),
    ("../up.html", "http://shop.example/up.html"),
    ("../../../over", "http://shop.example/over"),
    ("/root", "http://shop.example/root"),
    ("?page=3", "http://shop.example/books/list.html?page=3"),
    ("//cdn.example/x.js", "http://cdn.example/x.js"),
    ("https://other.example/a#b", "https://other.example/a"),
    ("/a/./b/../c", "http://shop.example/a/c"),
    (" /space at ends ", "http://shop.example/space%20at%20ends"),
    ("/пу\nть", "http://shop.example/путь"),
    ("\\\\cdn.example\\x.js", "http://cdn.example/x.js"),
    ("/q?path=c:\\temp", "http://shop.example/q?path=c:\\temp"),
)
SKIPPED = ("#anchor", "", "   ", "\x1f#top", "mailto:a@b.example", "tel:+123",
           "javascript:void(0)", "data:text/plain,x", "ftp://files.example/f")
REFUSAL = "url: нужен адрес вида http://хост или https://хост, получено"
# негодная ссылка и причина, с которой она отброшена
DROPPED = (
    ("http://shop.example:99999/", f"{REFUSAL} 'http://shop.example:99999/'"),
    ("http://a..b/", f"{REFUSAL} 'http://a..b/'"),
    ("http://[::1", "url: адрес не разобрать, получено 'http://[::1'"),
    ("http://user:secret@shop.example/",
     "url: адрес с логином или паролем не принимается"),
    ("http:\\\\user:secret@shop.example\\x",
     "url: адрес с логином или паролем не принимается"),
    ("/" + "a" * 2000, "url: адрес длиннее 2000 знаков не принимается"),
)


def anchor(reference):
    """Тег ссылки с таким значением href."""
    return f'<a href="{reference}">x</a>'


async def show_links(site):
    """Раздел 2: ссылки — полные адреса, пропуски, отказы, база, свой хост."""
    section("2. Ссылки")
    print(f"  база: {BASE}")
    results = []
    for reference, _ in CONVERSIONS:
        links, _ = links_logged(anchor(reference), BASE)
        results.append(links)
        print(f"  {reference!r:28} -> {' '.join(links)}")
    check("тринадцать ссылок превращены в полные адреса",
          results == [[address] for _, address in CONVERSIONS])

    outcomes = [links_logged(anchor(reference), BASE) for reference in SKIPPED]
    check("якорь, пустая ссылка и ссылки не на веб-страницы пропущены молча",
          outcomes == [([], [])] * 9)
    outcomes = [links_logged(anchor(reference), BASE) for reference, _ in DROPPED]
    check("шесть негодных ссылок отброшены, о каждой предупреждение с причиной",
          outcomes == [([], [("WARNING", f"адрес отброшен на {BASE!r}: {reason}")])
                       for _, reason in DROPPED])
    check("пароль из ссылки в журнал не попал", "secret" not in str(outcomes))

    twice = anchor("/a") + anchor("/b") + anchor("/a#x") + anchor("/a")
    links, _ = links_logged(twice)
    check("повтор и та же страница с якорем попадают в список один раз",
          links == ["http://shop.example/a", "http://shop.example/b"])

    parser = HTMLParser()
    soup = parser.make_soup(BASED)
    based, _ = await parse_logged(BASED)
    print(f"  страница с тегом base: {based['links']}")
    check("с тегом base ссылки и картинки считаются от его адреса",
          (based["links"], based["images"]) == (
              ["http://shop.example/deep/a/b/item", "http://shop.example/deep/a/up"],
              [{"src": "http://shop.example/deep/a/b/pic.png", "alt": ""}]))
    check("extract_base_url находит адрес из тега base",
          parser.extract_base_url(soup, SHOP) == "http://shop.example/deep/a/b/")
    check("extract_links считает строго от переданной базы",
          parser.extract_links(soup, SHOP)
          == ["http://shop.example/books/item", "http://shop.example/up"])
    odd, entries = await parse_logged('<base href="http://[::1"><a href="x">x</a>')
    check("негодный тег base: ссылки посчитаны от адреса страницы",
          odd["links"] == ["http://shop.example/books/x"])
    check("о негодном теге base в журнале предупреждение", entries == [
        ("WARNING", f"тег base не годится на {SHOP}: 'http://[::1'"),
        ("WARNING", f"нет заголовка title: {SHOP}"),
        summary_line(SHOP, 1, 0, 1)])

    url = site.base_url + "/html/catalog"
    inner = HTMLParser(internal_only=True)
    page = await inner.parse_html(CATALOG, url)
    print(f"  только внутренние ссылки: {len(page['links'])} из 7")
    check("internal_only оставил пять ссылок на хост страницы",
          page["links"] == internal_links(site.base_url))
    check("extract_links и при internal_only отдаёт все семь ссылок",
          inner.extract_links(inner.make_soup(CATALOG), url)
          == expected_catalog(site.base_url)["links"])
    own = ["http://shop.example:8080/port", "https://shop.example/tls"]
    alien = ["http://www.shop.example/sub", "http://other.example/"]
    hosts = "".join(anchor(address) for address in own + alien)
    page = await inner.parse_html("<title>Хосты</title>" + hosts, SHOP)
    check("свой хост — то же имя: порт и схема не важны, поддомен — чужой",
          page["links"] == own)
    far_base = '<title>База</title><base href="http://cdn.example/">'
    page = await inner.parse_html(far_base + anchor("x") + anchor(SHOP), SHOP)
    check("при теге base на чужой хост своим остаётся хост страницы",
          page["links"] == [SHOP])
    cyrillic = "<title>Имя</title>" + anchor("http://пример.рф/о-нас")
    page = await inner.parse_html(cyrillic, "http://xn--e1afmkfd.xn--p1ai/")
    check("имя хоста русскими буквами и его запись для сети — один хост",
          page["links"] == ["http://пример.рф/о-нас"])


TEXT_SAMPLE = """<html><head><title>Не текст</title>
<style>p { margin: 0 }</style></head>
<body><p>Первый
   <b>блок</b></p><div>Вто<i>рой</i> блок<br>с переносом</div>
<script>hidden()</script><template><p>шаблон</p></template><!-- комментарий -->
<noscript><p>без скриптов</p></noscript>
<span>хвост</span></body></html>"""


async def show_text(site):
    """Раздел 3: текст — блоки, пробелы, невидимое, селектор."""
    section("3. Текст")
    parser = HTMLParser()
    text = parser.extract_text(parser.make_soup(TEXT_SAMPLE))
    print(f"  {text!r}")
    check("блоки идут с новой строки, слово в строчном теге не разорвано",
          text.splitlines()[1:] == ["Второй блок", "с переносом", "хвост"])
    check("перевод строки внутри абзаца — пробел, а не новая строка; пробелы сжаты",
          text.splitlines()[:1] == ["Первый блок"])
    check("заголовка, стилей, скрипта, шаблона, noscript и комментария в тексте нет",
          not any(word in text for word in ("Не текст", "margin", "hidden", "шаблон",
                                            "без скриптов", "комментарий")))
    sample = parser.make_soup(TEXT_SAMPLE)
    check("селектор html берёт весь документ, но содержимое head в текст не входит",
          parser.extract_text(sample, "html") == text)
    check("селектор не достаёт текст из скрытого тега: абзац в noscript пропущен",
          parser.extract_text(sample, "p") == "Первый блок")
    late = parser.make_soup("<body><p>в теле</p></body></html><p>после конца</p>")
    check("текст после закрытых body и html не потерян",
          parser.extract_text(late) == "в теле\nпосле конца")

    soup = parser.make_soup(CATALOG)
    before = str(soup)
    check("селектор h2 выбрал два заголовка",
          parser.extract_text(soup, "h2") == "Полки\nЦены")
    check("селектор table td выбрал четыре ячейки",
          parser.extract_text(soup, "table td")
          == "Asyncio в примерах\n900\nСети для всех\n750")
    check("пункт внутри уже найденного пункта не повторён",
          parser.extract_text(soup, "li")
          == "Асинхронность\nasyncio\naiohttp\nСети\nАрхив\nОткрыть карту\nПрийти")
    check("селектор, который ничего не нашёл, даёт пустую строку",
          parser.extract_text(soup, "article") == "")
    check("вместо документа принят один тег: текст взят изнутри него",
          parser.extract_text(soup.find("ol")) == "Открыть карту\nПрийти")
    for extract in (parser.extract_metadata, parser.extract_headings,
                    parser.extract_tables, parser.extract_lists):
        extract(soup)
    parser.extract_links(soup, SHOP)
    parser.extract_images(soup, SHOP)
    check("методы извлечения не изменили дерево тегов", str(soup) == before)


async def show_metadata(site):
    """Раздел 4: метаданные — заголовок, описание, ключевые слова."""
    section("4. Метаданные")
    parser = HTMLParser()
    data = parser.extract_metadata(parser.make_soup(CATALOG))
    print(f"  {data}")
    check("заголовок, описание и ключевые слова «каталога»",
          data == expected_catalog(site.base_url)["metadata"])
    shouting = ('<meta name="description" content="  ">'
                '<meta NAME=" DESCRIPTION " content=" Два   слова ">'
                '<meta name="description" content="третье описание">')
    check("имя мета-тега найдено в любом регистре, взято первое непустое описание",
          parser.extract_metadata(parser.make_soup(shouting))["description"]
          == "Два слова")

    no_title = [("WARNING", f"нет заголовка title: {SHOP}"),
                summary_line(SHOP, 0, 0, 0)]
    stub, entries = await parse_logged(STUB)
    check("страница без заголовка: пустые значения того же типа",
          (stub["title"], stub["metadata"]) == ("", {
              "title": "", "description": "Страница оборвана до заголовка",
              "keywords": []}))
    check("о странице без заголовка в журнале предупреждение", entries == no_title)

    icon = "<svg><title>Подсказка к иконке</title></svg>"
    hint, entries = await parse_logged(f"<html><body>{icon}</body></html>")
    check("title внутри svg — не заголовок страницы",
          hint["title"] == "" and entries == no_title)
    late, _ = await parse_logged(f"<body>{icon}<title>Настоящий</title></body>")
    check("заголовок страницы найден и после title внутри svg",
          late["title"] == "Настоящий")


DATA_SAMPLE = """<h1>Первый <h2>Вложенный</h2></h1><h2> </h2><h4>Четвёртый</h4>
<h3>Третий <i>уровень</i></h3>
<img src="a.png" alt="  Два   слова "><img src="a.png"><img alt="без адреса">
<table><thead><tr><th>Имя</th><th>Вложенная</th></tr></thead>
<tr><td>Строка</td><td>до<table><tr><td>внутри</td></tr></table>после</td></tr>
<tfoot><tr><td>Итог</td><td>1</td></tr></tfoot></table>
<table><tr><td>без</td><td>заголовков</td></tr></table><table></table>
<ol><li>раз<ul><li>глубже</li></ul></li><li> </li><li>два</li></ol><ul></ul>"""


async def show_data(site):
    """Раздел 5: картинки, заголовки h1–h3, таблицы и списки."""
    section("5. Картинки, заголовки, таблицы, списки")
    parser = HTMLParser()
    soup = parser.make_soup(DATA_SAMPLE)
    images = parser.extract_images(soup, SHOP)
    headings = parser.extract_headings(soup)
    tables = parser.extract_tables(soup)
    lists = parser.extract_lists(soup)
    for name, value in (("картинки", images), ("заголовки", headings),
                        ("таблицы", tables), ("списки", lists)):
        print(f"  {name}: {value}")
    check("картинки: полный адрес, подпись со сжатыми пробелами, повтор сохранён",
          images == [{"src": "http://shop.example/books/a.png", "alt": "Два слова"},
                     {"src": "http://shop.example/books/a.png", "alt": ""}])
    check("встроенная картинка data: в список не попала",
          parser.extract_images(parser.make_soup(CATALOG), SHOP)
          == [{"src": "http://shop.example/books/img/shop.png",
               "alt": "Вход в лавку"},
              {"src": "http://shop.example/static/logo.png", "alt": ""}])
    check("заголовки: только h1–h3, пустой пропущен, вложенный идёт своей записью",
          headings == [{"level": 1, "text": "Первый"},
                       {"level": 2, "text": "Вложенный"},
                       {"level": 3, "text": "Третий уровень"}])
    check("таблица: строки по порядку — из секций и прямо из таблицы",
          tables[:1] == [{"headers": ["Имя", "Вложенная"],
                          "rows": [["Строка", "до после"], ["Итог", "1"]]}])
    check("вложенная таблица идёт отдельно, пустая пропущена",
          tables[1:] == [{"headers": [], "rows": [["внутри"]]},
                         {"headers": [], "rows": [["без", "заголовков"]]}])
    check("списки: вложенный идёт отдельно, пустые пункт и список пропущены",
          lists == [{"ordered": True, "items": ["раз", "два"]},
                    {"ordered": False, "items": ["глубже"]}])


BROKEN_RESULT = {
    "url": SHOP,
    "title": "Битая страница",
    "text": "Заголовок\nВторой\nАбзац ссылка\nНовый абзац\n1\n2\n3\nраз\nдва\n"
            "после картинки",
    "links": ["http://shop.example/a", "http://shop.example/b"],
    "metadata": {"title": "Битая страница", "description": "", "keywords": []},
    "images": [{"src": "http://shop.example/books/x.png", "alt": "икс"}],
    "headings": [{"level": 1, "text": "Заголовок"}, {"level": 2, "text": "Второй"}],
    "tables": [{"headers": [], "rows": [["1", "2"], ["3"]]}],
    "lists": [{"ordered": False, "items": ["раз", "два"]}],
    "failed": [],
}
RSS = '<?xml version="1.0"?><rss><channel><title>Лента</title></channel></rss>'
DEPTH = 100_000  # вложенных тегов div: проверка, что разбору глубина безразлична


class Faulty(HTMLParser):
    """Парсер с ошибками в двух методах извлечения."""

    def extract_metadata(self, soup):
        """Упасть ошибкой, которая не относится к странице."""
        return 1 / 0

    def extract_tables(self, soup):
        """Вернуть не список, а None."""
        return None


class Careless(HTMLParser):
    """Парсер, у которого вместо ссылки и адреса картинки получается не адрес."""

    def extract_links(self, soup, base_url):
        """Вернуть относительный путь, который краулер загрузить не сможет."""
        return ["/relative/path"]

    def extract_images(self, soup, base_url):
        """Вернуть картинку с адресом файла на диске."""
        return [{"src": "file:///c:/secret.png", "alt": ""}]


async def show_broken(site):
    """Раздел 6: битый HTML, странные документы и частичный результат."""
    section("6. Битый HTML и частичные результаты")
    page, entries = await parse_logged(BROKEN)
    print(f"  битая страница: заголовок {page['title']!r}, ссылок "
          f"{len(page['links'])}, не извлечено разделов: {len(page['failed'])}")
    check("битая страница разобрана целиком, результат совпал",
          page == BROKEN_RESULT)
    check("починенная разметка — не потеря: в журнале только итог разбора",
          entries == [summary_line(SHOP, 2, 1, 70)])

    stub, _ = await parse_logged(STUB)
    described = {"title": "", "description": "Страница оборвана до заголовка",
                 "keywords": []}
    check("обрубок: в результате только описание, остальное пусто",
          stub == dict(empty_page(SHOP), metadata=described))
    no_tags = ("WARNING", f"в документе нет ни одного тега: {SHOP}")
    check("пустая строка: все разделы пусты, одно предупреждение",
          await parse_logged("") == (empty_page(SHOP),
                                     [no_tags, summary_line(SHOP, 0, 0, 0)]))
    check("текст без тегов: он и есть текст страницы",
          await parse_logged("цена < 900") == (
              dict(empty_page(SHOP), text="цена < 900"),
              [no_tags, summary_line(SHOP, 0, 0, 10)]))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        feed, entries = await parse_logged(RSS)
        address, _ = await parse_logged("http://shop.example/page.html")
    check("лента RSS: разбор без ошибки и предупреждение «похож на XML»",
          feed["failed"] == []
          and ("WARNING", f"документ похож на XML, а не на HTML: {SHOP}") in entries)
    check("текст, похожий на адрес, — обычный текст страницы",
          address["text"] == "http://shop.example/page.html")
    check("библиотека разбора не выдала собственных предупреждений",
          [item for item in caught if "bs4" in item.category.__module__] == [])

    half, entries = await parse_logged("<p>половинка символа: \ud800</p>")
    print(f"  документ, который разборщик отверг: failed = {half['failed']}")
    check("отвергнутый документ: пустой результат с отметкой document",
          half == empty_page(SHOP, failed=["document"]))
    check("в журнале одно предупреждение с именем ошибки разборщика",
          entries == [
              ("WARNING", f"документ не разобран: {SHOP} — UnicodeEncodeError")])

    started = time.perf_counter()
    deep, _ = await parse_logged("<div>" * DEPTH + "дно" + "</div>" * DEPTH)
    seconds = time.perf_counter() - started
    print(f"  сто тысяч вложенных тегов: {seconds:.2f} с")
    check("сто тысяч вложенных тегов разобраны без переполнения стека",
          (deep["text"], deep["failed"]) == ("дно", []))
    check("глубокий документ разобран быстрее чем за десять секунд", seconds < 10)

    url = site.base_url + "/html/catalog"
    partial, entries = await parse_logged(CATALOG, url, parser=Faulty())
    print(f"  парсер с двумя сломанными методами: failed = {partial['failed']}")
    check("частичный результат: два раздела пусты и названы в failed, "
          "остальные на месте",
          partial == dict(expected_catalog(site.base_url), title="", tables=[],
                          metadata={"title": "", "description": "", "keywords": []},
                          failed=["metadata", "tables"]))
    check("о каждом сбое раздела в журнале предупреждение с причиной",
          [text for _, text in entries if text.startswith("раздел")] == [
              f"раздел metadata не извлечён: {url} — ZeroDivisionError",
              f"раздел tables не извлечён: {url} — значение не той формы"])
    careless, _ = await parse_logged(CATALOG, parser=Careless())
    check("негодные адреса от наследника парсера в результат не попали",
          (careless["links"], careless["images"], careless["failed"])
          == ([], [], ["links", "images"]))


MANY = 1000  # негодных ссылок на одной странице


async def show_logs(site):
    """Раздел 7: что парсер пишет в журнал."""
    section("7. Журнал")
    url = site.base_url + "/html/catalog"
    _, entries = await parse_logged(CATALOG, url)
    for level, text in entries:
        print(f"  {level}: {text}")
    dropped = f"адрес отброшен на {url!r}"
    check("о «каталоге» три записи: два предупреждения и итог разбора", entries == [
        ("WARNING", f"{dropped}: {REFUSAL} 'http://127.0.0.1:99999/'"),
        ("WARNING", f"{dropped}: url: адрес с логином или паролем не принимается"),
        summary_line(url, 7, 2, 275)])
    check("пароль из ссылки в журнал не попал", "secret" not in str(entries))

    flood = "".join(anchor(f"http://shop.example:99999/{n}") for n in range(MANY))
    page, entries = await parse_logged("<title>Много ссылок</title>" + flood)
    print(f"  страница с тысячей негодных ссылок: записей в журнале {len(entries)}")
    check("тысяча негодных ссылок: двадцать предупреждений, счёт остальных и итог",
          levels(entries) == ["WARNING"] * 21 + ["INFO"] and page["links"] == []
          and entries[20] == ("WARNING", f"на {SHOP!r} отброшено ещё адресов: 980"))

    forged = anchor("http://shop.example:99999/\x0bINFO: подделка " + "я" * 500)
    long_base = '<base href="/' + "b" * 500 + '/">'
    _, entries = await parse_logged("<title>Подделка</title>" + long_base + forged)
    check("значение со страницы в журнале обрезано и без управляющих знаков",
          levels(entries) == ["WARNING", "INFO"]
          and all(len(text) < 250 and text.isprintable() for _, text in entries))
    own = logging.getLogger("crawler.parser")
    check("парсер не настраивает журнал: ни обработчиков, ни уровня",
          (own.handlers, own.level, logging.getLogger().handlers)
          == ([], logging.NOTSET, ROOT_HANDLERS))


PROGRAM_TIMEOUT = 30  # секунд на отдельную программу: зависшая будет остановлена


async def run_program(code):
    """Запустить текст отдельной программой Python; вернуть её вывод."""
    command = [sys.executable, "-X", "utf8", "-c", code]
    try:
        done = await asyncio.to_thread(
            subprocess.run, command, capture_output=True, encoding="utf-8",
            cwd=Path(__file__).parent, timeout=PROGRAM_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "программа не завершилась"
    return done.stdout.strip() or done.stderr.strip()[-200:]


# программа, где парсер из метода извлечения снова вызывает parse_html
NESTED = """import asyncio
from crawler import HTMLParser

class Nested(HTMLParser):
    def extract_text(self, soup, selector=None):
        inner = self.parse_html("<p>x</p>", "http://shop.example/")
        return asyncio.run(inner)["text"]

page = asyncio.run(Nested().parse_html("<title>t</title>", "http://shop.example/"))
print(page["title"], page["failed"])
"""


async def count_ticks(stop):
    """Считать шаги цикла событий по 10 мс, пока не попросят остановиться."""
    ticks = 0
    while not stop.is_set():
        await asyncio.sleep(0.01)
        ticks += 1
    return ticks


class Spy(HTMLParser):
    """Парсер, который запоминает, в каких потоках шло извлечение текста."""

    def __init__(self):
        """Начать с пустого набора имён потоков."""
        super().__init__()
        self._threads = set()

    @property
    def threads(self):
        """Имена потоков, в которых шло извлечение: новый список по алфавиту."""
        return sorted(self._threads)

    def extract_text(self, soup, selector=None):
        """Запомнить имя текущего потока и извлечь текст как обычно."""
        self._threads.add(threading.current_thread().name)
        return super().extract_text(soup, selector)


async def show_crawler(site):
    """Раздел 8: fetch_and_parse — загрузка и разбор одним вызовом."""
    section("8. Краулер: загрузить и разобрать")
    base = site.base_url
    site.reset()
    collector = collect_logs()
    crawler = AsyncCrawler(max_concurrent=3, read_timeout=0.5)
    try:
        page = await crawler.fetch_and_parse(base + "/html/catalog")
        print(f"  {page['url']}: {page['title']!r}, ссылок {len(page['links'])}")
        check("fetch_and_parse вернул тот же результат, что разбор текста",
              page == expected_catalog(base))
        check("сайт получил один запрос", site.hits == {"/html/catalog": 1})
        check("в журнале по порядку: начало, успех, два предупреждения, итог разбора",
              [text.split(":")[0] for _, text in collector.entries]
              == ["начало", "успех", "адрес отброшен на 'http",
                  "адрес отброшен на 'http", "разбор"])

        moved = await crawler.fetch_and_parse(base + "/moved/catalog")
        print(f"  после перенаправления: url {moved['url']}")
        print(f"  первая ссылка: {moved['links'][:1]}")
        check("после перенаправления url в результате — адрес, который просили",
              moved["url"] == base + "/moved/catalog")
        check("а ссылки посчитаны от адреса, с которого страница пришла",
              moved["links"][:4] == [
                  base + "/deep/a/b/about.html", base + "/html/shelf-1",
                  base + "/deep/a/b/shelf-2?page=1", base + "/deep/a/archive/"])

        collector = collect_logs()
        pad = "a" * (2000 - len(base + "/moved/catalog?pad="))
        long_url = f"{base}/moved/catalog?pad={pad}"  # адрес ответа выйдет длиннее
        far = await crawler.fetch_and_parse(long_url)
        noted = f"адрес ответа не годится: ссылки считаются от {long_url}"
        check("адрес ответа длиннее предела: ссылки посчитаны от запрошенного адреса",
              far["links"][:1] == [base + "/moved/about.html"]
              and ("WARNING", noted) in collector.entries)

        collector = collect_logs()
        plain = await crawler.fetch_and_parse(base + "/page/1")
        check("ответ не в формате HTML не разобран: пустой результат с отметкой",
              plain == empty_page(base + "/page/1", failed=["document"]))
        check("о таком ответе в журнале предупреждение с типом содержимого",
              ("WARNING", f"не HTML: {base}/page/1 — тип 'text/plain', разбора нет")
              in collector.entries)
        check("кодировка для имён хостов в заголовке: страница прочитана как UTF-8",
              await crawler.fetch_url(base + "/charset/punycode") == "Стр ��")

        for description, path, kind, status, cause in (
            ("страницы нет", "/status/404", "http", 404, "ClientResponseError"),
            ("учебной страницы нет", "/html/nowhere", "http", 404,
             "ClientResponseError"),
            ("сервер молчит", "/slow/900", "timeout", None, "SocketTimeoutError"),
            ("соединение оборвано", "/drop", "network", None,
             "ServerDisconnectedError"),
        ):
            error = await expect_error(description, FetchError,
                                       lambda: crawler.fetch_and_parse(base + path))
            check(f"{description}: та же ошибка загрузки, что у fetch_url",
                  facts(error) == (base + path, kind, status, cause))

        site.reset()
        slow = [f"{base}/slow/150?n={number}" for number in range(1, 9)]
        pages = await asyncio.gather(*(crawler.fetch_and_parse(url) for url in slow))
        print(f"  восемь страниц при пределе 3: одновременно шло {site.max_active}")
        check("предел конкурентности действует и на fetch_and_parse",
              (len(pages), site.max_active) == (8, 3))

        three = [f"{base}/html/based?n={number}" for number in range(1, 4)]
        tasks = [asyncio.create_task(crawler.fetch_and_parse(url)) for url in three]
        await asyncio.sleep(0)  # дать задачам начаться
        await crawler.close()
        pages = await asyncio.gather(*tasks, return_exceptions=True)
        titles = [page["title"] if type(page) is dict else page for page in pages]
        check("close() во время fetch_and_parse не потерял ни одной страницы",
              titles == ["С базой"] * 3)
    finally:
        await crawler.close()

    check("по умолчанию у краулера обычный парсер",
          type(crawler.parser) is HTMLParser and crawler.parser.internal_only is False)
    custom = HTMLParser(internal_only=True)
    inner = AsyncCrawler(parser=custom)
    try:
        page = await inner.fetch_and_parse(base + "/html/catalog")
    finally:
        await inner.close()
    check("краулер разбирает тем парсером, который ему передали",
          inner.parser is custom and page["links"] == internal_links(base))


async def show_thread(site):
    """Раздел 9: разбор идёт в своём потоке и не останавливает цикл событий."""
    section("9. Поток разбора")
    spy = Spy()
    crawler = AsyncCrawler(parser=spy)
    stop = asyncio.Event()
    counter = asyncio.create_task(count_ticks(stop))
    started = time.perf_counter()
    try:
        page = await crawler.fetch_and_parse(site.base_url + "/html/big")
    finally:
        await crawler.close()
    seconds = time.perf_counter() - started
    stop.set()
    ticks = await counter
    print(f"  большая страница, {BIG_ROWS} строк таблицы: {seconds:.2f} с")
    print(f"  цикл событий за это время сделал шагов: {ticks}")
    check("большая страница разобрана: ссылка из каждой строки таблицы",
          (len(page["links"]), len(page["tables"]), page["failed"])
          == (BIG_ROWS, 1, []))
    check("пока шёл разбор, цикл событий сделал не меньше десяти шагов", ticks >= 10)
    check("краулер разбирал страницу своим парсером, в потоке разбора",
          spy.threads == ["crawler-parse_0"])

    url = site.base_url + "/html/catalog"
    pages = await asyncio.gather(*(spy.parse_html(CATALOG, url) for _ in range(50)))
    check("пятьдесят одновременных разборов дали пятьдесят верных результатов",
          pages == [expected_catalog(site.base_url)] * 50)
    print(f"  потоки, в которых шёл разбор: {spy.threads}")
    check("все разборы шли в одном потоке, и это не поток цикла событий",
          spy.threads == ["crawler-parse_0"]
          and threading.current_thread().name == "MainThread")
    answer = await run_program(NESTED)
    print(f"  программа с разбором внутри разбора: {answer}")
    check("разбор, вызванный из метода извлечения, не повис: это сбой раздела",
          answer == "t ['text']")


async def show_summary(site):
    """Раздел 10: сводка страницы и статистика по нескольким страницам."""
    section("10. Сводка и статистика")
    base = site.base_url
    crawler = AsyncCrawler()
    try:
        names = ("catalog", "broken", "based", "stub")
        pages = await asyncio.gather(
            *(crawler.fetch_and_parse(f"{base}/html/{name}") for name in names))
    finally:
        await crawler.close()
    summary = page_summary(pages[0])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    wanted = expected_catalog(base)
    check("сводка «каталога»: счётчики вместо длинных полей", summary == {
        "url": wanted["url"], "title": "Каталог книжной лавки", "text_length": 275,
        "links_count": 7, "links": wanted["links"], "images_count": 2,
        "headings_count": 4, "tables_count": 1, "lists_count": 3, "failed": []})
    summary["links"].clear()
    check("сводка — новый словарь: её правка не меняет результат разбора",
          pages[0]["links"] == wanted["links"])
    rows = [page_summary(page) for page in pages]
    for row in rows:
        print(f"  {row['url']}: ссылок {row['links_count']}, "
              f"текст {row['text_length']} симв., картинок {row['images_count']}")
    check("статистика четырёх страниц: ссылки, длина текста, картинки",
          [(row["links_count"], row["text_length"], row["images_count"])
           for row in rows] == [(7, 275, 2), (2, 70, 1), (2, 10, 1), (0, 0, 0)])
    print(f"  всего: ссылок {sum(row['links_count'] for row in rows)}, "
          f"текст {sum(row['text_length'] for row in rows)} симв.")


# программа без пакета bs4: None в sys.modules значит «такого модуля нет»
NO_PACKAGE = """import asyncio, sys
sys.modules["bs4"] = None
from crawler import AsyncCrawler, CrawlerError, HTMLParser
crawler = AsyncCrawler()
try:
    asyncio.run(HTMLParser().parse_html("<p>x</p>", "http://shop.example/"))
except CrawlerError as error:
    print(f"{type(crawler).__name__} создан; {error}")
"""


async def show_input(site):
    """Раздел 11: негодные аргументы отсекаются до разбора и до запроса."""
    section("11. Негодный ввод")
    site.reset()
    collector = collect_logs()
    parser = HTMLParser()
    soup = parser.make_soup("<p>текст</p>")
    secret = "http://user:secret@shop.example/"
    summary = page_summary(await parser.parse_html("<title>т</title>", SHOP))
    refusals = []
    for description, action in (
        ("html байтами", lambda: parser.parse_html(b"<p>", SHOP)),
        ("html None", lambda: parser.parse_html(None, SHOP)),
        ("html числом в make_soup", lambda: parser.make_soup(5)),
        ("текст, который разборщик не принял", lambda: parser.make_soup("\ud800")),
        ("адрес страницы без схемы", lambda: parser.parse_html("<p>", "shop.example")),
        ("адрес страницы — относительный путь",
         lambda: parser.parse_html("<p>", "/books/")),
        ("адрес страницы с паролем", lambda: parser.parse_html("<p>", secret)),
        ("адрес страницы None", lambda: parser.parse_html("<p>", None)),
        ("база ссылок без схемы", lambda: parser.extract_links(soup, "shop.example")),
        ("база ссылок с паролем", lambda: parser.extract_links(soup, secret)),
        ("база картинок числом", lambda: parser.extract_images(soup, 5)),
        ("адрес для extract_base_url без хоста",
         lambda: parser.extract_base_url(soup, "http://")),
        ("строка вместо дерева тегов", lambda: parser.extract_links("<p>", SHOP)),
        ("None вместо дерева тегов", lambda: parser.extract_text(None)),
        ("строка вместо дерева: метаданные", lambda: parser.extract_metadata("<p>")),
        ("строка вместо дерева: картинки", lambda: parser.extract_images("<p>", SHOP)),
        ("строка вместо дерева: заголовки", lambda: parser.extract_headings("<p>")),
        ("строка вместо дерева: таблицы", lambda: parser.extract_tables("<p>")),
        ("строка вместо дерева: списки", lambda: parser.extract_lists("<p>")),
        ("строка вместо дерева: база", lambda: parser.extract_base_url("<p>", SHOP)),
        ("селектор числом", lambda: parser.extract_text(soup, 5)),
        ("селектор пустой", lambda: parser.extract_text(soup, "")),
        ("селектор с ошибкой", lambda: parser.extract_text(soup, "p >")),
        ("селектор с псевдоэлементом", lambda: parser.extract_text(soup, "p::before")),
        ("internal_only числом", lambda: HTMLParser(internal_only=1)),
        ("internal_only строкой", lambda: HTMLParser(internal_only="да")),
        ("internal_only None", lambda: HTMLParser(internal_only=None)),
        ("парсер краулера — строка", lambda: AsyncCrawler(parser="lxml")),
        ("парсер краулера — класс, а не объект",
         lambda: AsyncCrawler(parser=HTMLParser)),
        ("сводка не из словаря", lambda: page_summary([SHOP])),
        ("сводка из словаря без нужных ключей", lambda: page_summary({"url": SHOP})),
        ("сводка от сводки", lambda: page_summary(summary)),
    ):
        refusals.append(await expect_error(description, InvalidArgumentError, action))

    crawler = AsyncCrawler()
    try:
        for description, value in (
            ("fetch_and_parse: адрес None", None),
            ("fetch_and_parse: адрес без схемы", f"127.0.0.1:{site.port}/html/catalog"),
            ("fetch_and_parse: адрес с паролем",
             f"http://user:secret@127.0.0.1:{site.port}/html/catalog"),
        ):
            refusals.append(await expect_error(
                description, InvalidArgumentError,
                lambda: crawler.fetch_and_parse(value)))
    finally:
        await crawler.close()
    check("пароль не повторён ни в одном отказе",
          all("secret" not in str(refusal) for refusal in refusals))
    check("пароль не попал в журнал", "secret" not in str(collector.entries))
    check("при негодном вводе сайт не получил ни одного запроса", site.hits == {})

    answer = await run_program(NO_PACKAGE)
    print(f"  программа без пакета bs4: {answer}")
    check("без пакета разбора краулер создаётся, а разбор отвечает подсказкой",
          answer == "AsyncCrawler создан; нет модуля bs4: установите пакеты: "
                    ".venv\\Scripts\\python -m pip install -r requirements.txt")


SECTIONS = (show_catalog, show_links, show_text, show_metadata, show_data, show_broken,
            show_logs, show_crawler, show_thread, show_summary, show_input)


async def run_demo(port):
    """Запустить учебный сайт и провести по нему все разделы."""
    site = LocalSite(port)
    collect_logs()  # журнал перестаёт печататься в терминал
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
        HTMLParser().make_soup("")  # нет пакетов разбора — отказ сразу, до разделов
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
    print("Демонстрация дня 2 завершена успешно: расхождений нет.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
