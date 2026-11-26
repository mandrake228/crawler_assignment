"""Учебные страницы: тексты HTML для учебного сайта и для проверок парсера."""

# исправная страница: все виды данных и все виды ссылок
CATALOG = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Каталог   книжной лавки</title>
  <meta name="description" content="Книги по Python и асинхронности.">
  <meta name="Keywords" content="python, asyncio, , книги">
  <style>body { color: black; }</style>
  <script>var visits = 1;</script>
</head>
<body>
  <h1>Каталог</h1>
  <p>В лавке <b>три</b> полки и <a href="about.html">страница о нас</a>.</p>
  <h2>Полки</h2>
  <ul>
    <li><a href="/html/shelf-1">Асинхронность</a>
      <ul><li>asyncio</li><li>aiohttp</li></ul>
    </li>
    <li><a href="shelf-2?page=1#top">Сети</a></li>
    <li><a href="../archive/">Архив</a></li>
  </ul>
  <h2>Цены</h2>
  <table>
    <tr><th>Книга</th><th>Цена</th></tr>
    <tr><td>Asyncio в примерах</td><td>900</td></tr>
    <tr><td>Сети для всех</td><td>750</td></tr>
  </table>
  <h3>Как найти</h3>
  <ol><li>Открыть карту</li><li>Прийти</li></ol>
  <img src="img/shop.png" alt="Вход в лавку">
  <img src="/static/logo.png">
  <img src="data:image/gif;base64,R0lGOD" alt="точка">
  <p><a href="https://docs.python.org/3/">Документация</a>
     <a href="//example.com/partner">Партнёр</a>
     <a href="mailto:shop@example.com">Написать</a>
     <a href="javascript:void(0)">Меню</a>
     <a href="#top">Наверх</a>
     <a href="about.html">Ещё раз о нас</a>
     <a href=" /html/shelf 3 ">Полка с пробелом</a>
     <a href="http://127.0.0.1:99999/">Негодный порт</a>
     <a href="http://reader:secret@127.0.0.1/">С паролем</a></p>
  <noscript>Включите JavaScript</noscript>
  <!-- служебный комментарий -->
</body>
</html>
"""

# битая страница: теги не закрыты и перепутаны
BROKEN = """<html><head><title>Битая <b>страница</title>
<body><h1>Заголовок<h2>Второй</h1>
<p>Абзац <a href="/a">ссылка <p>Новый абзац
<table><tr><td>1<td>2<tr><td>3</table>
<ul><li>раз<li>два</ul>
<img src="x.png" alt="икс">
<a href="/b">после картинки</a></div></span>
"""

# обрубок: документ оборван раньше заголовка
STUB = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="description" content="Страница оборвана до заголовка">
"""

# страница с тегом base: относительные ссылки считаются от его адреса
BASED = """<html><head><title>С базой</title><base href="/deep/a/b/"></head>
<body><a href="item">товар</a> <a href="../up">выше</a>
<img src="pic.png" alt=""></body></html>
"""

BIG_ROWS = 20000  # строк в таблице большой страницы: её разбор занимает заметное время
BIG_ROW = (
    "<tr><td>{n}</td><td>Книга {n}</td>"
    '<td><a href="/html/book-{n}">открыть</a></td></tr>\n'
)
# большая страница: её строит код, а не рука
BIG = (
    "<html><head><title>Склад</title></head><body><h1>Склад</h1>\n"
    "<table><tr><th>Номер</th><th>Книга</th><th>Ссылка</th></tr>\n"
    + "".join(BIG_ROW.format(n=number) for number in range(1, BIG_ROWS + 1))
    + "</table></body></html>\n"
)

PAGES = {
    "catalog": CATALOG,
    "broken": BROKEN,
    "stub": STUB,
    "based": BASED,
    "big": BIG,
}
