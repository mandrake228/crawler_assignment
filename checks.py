"""Помощники проверок: печатают [ок], [отказ] и [РАСХОЖДЕНИЕ], копят расхождения."""

import inspect

problems = []  # расхождения всех проверок одного запуска


def section(title):
    """Напечатать заголовок раздела."""
    print(f"\n=== {title} ===")


def check(description, condition):
    """Напечатать [ок] или [РАСХОЖДЕНИЕ]; расхождение запомнить в problems."""
    if condition:
        print(f"  [ок] {description}")
    else:
        print(f"  [РАСХОЖДЕНИЕ] {description}")
        problems.append(description)


async def expect_error(description, error_class, action):
    """Дождаться ошибки нужного класса, напечатать [отказ] и вернуть ошибку."""
    try:
        result = action()
        if inspect.isawaitable(result):
            await result
    except error_class as error:
        print(f"  [отказ] {description}: {error}")
        return error
    except Exception as error:
        got = type(error).__name__
        check(f"{description}: ждали {error_class.__name__}, пришла {got}", False)
        return None
    check(f"{description}: ждали {error_class.__name__}, ошибки не было", False)
    return None
