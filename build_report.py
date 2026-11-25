"""Build the squadron's GitHub tables from cumulative monthly snapshots."""

import csv
import html
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MODES = ("РБ", "СБ")
METRICS = ("бои_земля", "время_земля", "бои_воздух", "время_воздух", "бои_всего")
DURATIONS = {"время_земля", "время_воздух"}
COLUMNS = ("месяц", "дата", "игрок", "режим", *METRICS)
LABELS = ("Земля, бои", "Земля, ч:мин", "Воздух, бои", "Воздух, ч:мин", "Всего боёв", "Всего, ч:мин")
MONTHS = "Январь Февраль Март Апрель Май Июнь Июль Август Сентябрь Октябрь Ноябрь Декабрь".split()
DASH = "—"
GENERATED = "<!-- Generated from snapshots.csv by build_report.py -->"


@dataclass
class Snapshot:
    nick: str
    taken: date
    values: dict


def previous(month):
    return (date.fromisoformat(month + "-01") - timedelta(days=1)).strftime("%Y-%m")


def month_name(month):
    year, number = month.split("-")
    return f"{MONTHS[int(number) - 1]} {year}"


def read_metric(field, raw):
    if raw == "":
        return None
    if field in DURATIONS:
        match = re.fullmatch(r"([0-9]+):([0-5][0-9])", raw)
        if match:
            return int(match[1]) * 60 + int(match[2])
        raise ValueError(f"поле {field} ожидает часы и минуты, например 127:05")
    if not re.fullmatch(r"[0-9]+", raw):
        raise ValueError(f"поле {field} ожидает целое число от нуля")
    return int(raw)


def load_snapshots(path):
    players = {}
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = csv.DictReader(source)
        if rows.fieldnames != list(COLUMNS):
            raise ValueError("Проверьте заголовки CSV: " + ",".join(COLUMNS))
        for row in rows:
            try:
                if None in row:
                    raise ValueError("лишняя колонка, проверьте разделители")
                fields = {key: (value or "").strip() for key, value in row.items()}
                if not any(fields.values()):
                    continue
                month, nick, mode = fields["месяц"], fields["игрок"], fields["режим"].upper()
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
                    raise ValueError("месяц указывается в формате ГГГГ-ММ")
                date.fromisoformat(month + "-01")
                taken = date.fromisoformat(fields["дата"])
                if taken.isoformat() != fields["дата"]:
                    raise ValueError("дату замера указывают в формате ГГГГ-ММ-ДД")
                if not nick or any(ord(char) < 32 for char in nick):
                    raise ValueError("нужен игровой ник в одну строку")
                if mode not in MODES:
                    raise ValueError("укажите режим РБ или СБ")
                values = {key: read_metric(key, fields[key]) for key in METRICS}
                by_mode = players.setdefault(nick.casefold(), {}).setdefault(month, {})
                if mode in by_mode:
                    raise ValueError(f"повтор замера {nick} / {month} / {mode}")
                by_mode[mode] = Snapshot(nick, taken, values)
            except ValueError as exc:
                raise ValueError(f"Строка {rows.line_num}: {exc}") from exc
    return players


def sum_known(values):
    return sum(values) if all(value is not None for value in values) else None


def by_mode_total(values):
    return {key: sum_known([values.get(mode, {}).get(key) for mode in MODES]) for key in METRICS}


def show(value, duration=False):
    if value is None:
        return DASH
    if duration:
        hours, minutes = divmod(value, 60)
        return f"{hours}:{minutes:02d}"
    return str(value)


def cells(values):
    result = [show(values.get(key), key in DURATIONS) for key in METRICS]
    return result + [show(sum_known([values.get(key) for key in DURATIONS]), duration=True)]


def change(old, new):
    if new is None:
        return {}, "Нет замера"
    if old is None:
        return {}, "Нет прошлого замера"
    if old.taken >= new.taken:
        return {}, "Сверить даты"
    values = {}
    decreased = []
    for key, label in zip(METRICS, LABELS):
        start, end = old.values[key], new.values[key]
        if start is None or end is None:
            values[key] = None
        elif end < start:
            values[key] = None
            decreased.append(label.lower())
        else:
            values[key] = end - start
    if decreased:
        return values, "Сверить (" + ", ".join(decreased) + ")"
    return values, ""


def activity(history, month, changes, notes):
    if month not in history:
        return "Нет замера"
    if not any(earlier < month for earlier in history):
        return "Первый замер"
    if previous(month) not in history:
        return "Пропущен прошлый замер"
    if any(note.startswith("Сверить") for note in notes.values()):
        return "Сверить данные"
    played = any(value for mode in MODES for value in changes[mode].values() if value is not None)
    known = all(
        changes[mode].get("бои_всего") is not None
        or all(changes[mode].get(key) is not None for key in METRICS[:4])
        for mode in MODES
    )
    if played:
        return "Играл" if known else "Играл (часть данных)"
    return "Без активности" if known else "Недостаточно данных"


def nickname(text):
    # Numeric entities preserve the visible nickname inside GitHub Markdown tables.
    return re.sub(r"[|\\`*_~@!\[\]()]", lambda m: f"&#{ord(m[0])};", html.escape(text))


def table(headers, rows):
    def line(items):
        return "| " + " | ".join(items) + " |"
    return "\n".join([line(headers), line(["---"] * len(headers)), *(line(row) for row in rows)])


def date_text(snapshot):
    return snapshot.taken.strftime("%d.%m.%Y") if snapshot else DASH


def date_range(snapshots):
    dates = sorted({sample.taken for sample in snapshots.values()})
    if not dates:
        return DASH
    first, last = (day.strftime("%d.%m.%Y") for day in (dates[0], dates[-1]))
    return first if first == last else f"{first} – {last}"


def period_tables(players, month):
    summary, details, cumulative, source_detail = [], [], [], []
    for key, history in sorted(players.items()):
        seen = [period for period in history if period <= month]
        if not seen:
            continue
        last_month = max(seen)
        latest = history[last_month]
        player = nickname(max(latest.values(), key=lambda sample: sample.taken).nick)
        current = history.get(month, {})
        prior = history.get(previous(month), {})
        changes, notes = {}, {}
        for mode in MODES:
            old, new = prior.get(mode), current.get(mode)
            values, note = change(old, new)
            changes[mode], notes[mode] = values, note
            details.append([player, mode, date_text(old), date_text(new), *cells(values), note or DASH])
            if new:
                source_detail.append([player, mode, date_text(new), *cells(new.values)])
        state = activity(history, month, changes, notes)
        summary.append([player, state, *cells(by_mode_total(changes))])
        totals = by_mode_total({mode: sample.values for mode, sample in latest.items()})
        cumulative.append([player, date_range(latest), *cells(totals)])
    return (
        table(["Игрок", "Активность", *LABELS], summary),
        table(["Игрок", "Режим", "С", "По", *LABELS, "Примечание"], details),
        table(["Игрок", "Последний замер", *LABELS], cumulative),
        table(["Игрок", "Режим", "Дата", *LABELS], source_detail),
    )


KEY = (
    "Бои и время показаны отдельно для наземной и воздушной техники. "
    "«Всего боёв» берётся из общего счётчика каждого режима. "
    "В одном бою игрок может использовать несколько видов техники. "
    "Прочерк означает, что для расчёта не хватает данных. "
    "Ноль означает, что счётчик проверен."
)

GUIDE = """## Как внести замер

Откройте [таблицу замеров](snapshots.csv). Для каждого игрока добавьте по одной строке РБ и СБ. Перепишите накопительные показатели из карточки игрока. GitHub Actions пересчитает главную таблицу и историю после сохранения CSV в основной ветке.

| Поле | Что вписать |
| --- | --- |
| месяц | Месяц, который закрывает замер, в формате ГГГГ-ММ. Для отчёта за декабрь стартовый снимок на 1 декабря закрывает ноябрь, итоговый на 31 декабря или 1 января закрывает декабрь. |
| дата | Фактическая дата снимка в формате ГГГГ-ММ-ДД. При сборе в разные дни отчёт покажет даты каждого замера. |
| игрок, режим | Игровой ник и РБ или СБ. При смене ника обновите и старые строки этого игрока. |
| бои_земля, время_земля | Суммарные показатели наземной техники, включая танки, САУ и зенитки. |
| бои_воздух, время_воздух | Суммарные показатели авиации, включая истребители, штурмовики, бомбардировщики и вертолёты. |
| бои_всего | Общее число боёв в этом режиме из карточки игрока, если оно есть. |

Время вводится в часах и минутах. Например, 2 суток 3 часа 15 минут записываются как `51:15`. Если показатель на снимке отсутствует, оставьте ячейку пустой. Подтверждённый ноль вводится как `0` боёв или `0:00` времени.

Первый замер даёт исходные значения. Ежемесячный отчёт вычитает из нового замера значения за предыдущий отчётный месяц у того же игрока в том же режиме. Если месяц пропущен, нового игрока ещё не с чем сравнить или счётчик уменьшился, таблица покажет причину. Сводка РБ + СБ складывает показатель, когда известны оба режима. Данные одного режима видны в подробном отчёте.

**Пример.** У игрока было 100 наземных боёв и `40:50` в СБ. На следующем снимке — 112 боёв и `44:10`. За период в СБ он сыграл на земле 12 боёв и провёл 3 ч 20 мин.

Для пересчёта на компьютере выполните `python3 build_report.py` из папки репозитория. Требуется Python 3.8 или новее."""


def write(path, parts):
    path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")


def build(root=ROOT):
    players = load_snapshots(root / "snapshots.csv")
    months = sorted({month for history in players.values() for month in history})
    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    readme = [GENERATED, "# Статистика полка War Thunder"]
    for month in months:
        monthly, details, cumulative, source = period_tables(players, month)
        report = [GENERATED, f"# {month_name(month)}", "[На главную](../README.md)",
                  "## Активность за месяц", monthly, KEY, "## Изменения по режимам", details,
                  "## Показания карточек за этот месяц", source]
        write(reports / f"{month}.md", report)
    if months:
        latest = months[-1]
        readme.extend([f"## {month_name(latest)} · активность за месяц", monthly,
                       f"[Открыть разбивку по РБ и СБ и даты замеров](reports/{latest}.md)",
                       "## Накопительная статистика", cumulative, KEY,
                       "## Отчёты по месяцам",
                       "\n".join(f"- [{month_name(m)}](reports/{m}.md)" for m in reversed(months))])
    else:
        readme.extend(["## Активность за месяц",
                       "Таблица ждёт первых карточек игроков. После второго ежемесячного замера здесь появится прирост.",
                       table(["Игрок", "Активность", *LABELS], []),
                       "## Накопительная статистика",
                       table(["Игрок", "Последний замер", *LABELS], []), KEY])
    write(root / "README.md", [*readme, GUIDE])
    for path in reports.glob("????-??.md"):
        if path.stem not in months and path.read_text(encoding="utf-8").startswith(GENERATED + "\n"):
            path.unlink()


if __name__ == "__main__":
    try:
        build()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
