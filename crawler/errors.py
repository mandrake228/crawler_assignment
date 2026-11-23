"""Ошибки краулера: категории сбоя, классы ошибок и помощники их текста."""

from enum import StrEnum


class FailureKind(StrEnum):
    """Категория сбоя загрузки: что именно пошло не так."""

    HTTP = "http"  # сервер ответил кодом от 400 или ответ не разобрать как HTTP
    TIMEOUT = "timeout"  # ответ не пришёл за отведённое время
    NETWORK = "network"  # соединение не установлено или оборвано

    @property
    def label(self):
        """Название категории для сообщений и журнала."""
        labels = {
            "http": "HTTP-ошибка",
            "timeout": "таймаут",
            "network": "сетевая ошибка",
        }
        return labels[self.value]


def shown(value):
    """Показать полученное значение коротко; значение со знаком @ не повторять."""
    try:
        text = repr(value)
    except Exception:  # у объекта сломано собственное представление
        text = f"объект {type(value).__name__}"
    if "@" in text:  # в адресе перед @ может стоять пароль
        return "значение со знаком @ (скрыто)"
    return text if len(text) <= 60 else text[:60] + "…"


def is_whole(value):
    """True только для обычного целого числа; True и False им не считаются."""
    return type(value) is int


class CrawlerError(Exception):
    """Общий предок всех ошибок краулера."""


class InvalidArgumentError(CrawlerError):
    """Методу передали негодный аргумент."""


class FetchError(CrawlerError):
    """Страницу не удалось загрузить: сбой одной из трёх категорий."""

    def __init__(self, url, kind, error_name, status=None):
        """Проверить и запомнить адрес, категорию, имя исходной ошибки и код."""
        if type(url) is not str or type(error_name) is not str:
            raise InvalidArgumentError(
                f"url и error_name: нужны строки, получено {shown((url, error_name))}"
            )
        if not isinstance(kind, FailureKind):
            raise InvalidArgumentError(
                f"kind: нужна категория FailureKind, получено {shown(kind)}"
            )
        if status is not None and not is_whole(status):
            raise InvalidArgumentError(
                f"status: нужно целое число или None, получено {shown(status)}"
            )
        # аргументы целиком уходят предку: по ним ошибку копируют и пересылают
        super().__init__(url, kind, error_name, status)
        self._url = url
        self._kind = kind
        self._error_name = error_name
        self._status = status

    def __str__(self):
        """Текст ошибки: адрес, категория, код ответа и имя исходной ошибки."""
        code = "" if self._status is None else f", код {self._status}"
        return f"{self._url}: {self._kind.label}{code} ({self._error_name})"

    @property
    def url(self):
        """Адрес, который не удалось загрузить."""
        return self._url

    @property
    def kind(self):
        """Категория сбоя: FailureKind."""
        return self._kind

    @property
    def status(self):
        """Код ответа; None, если кода нет: сервер не ответил."""
        return self._status
