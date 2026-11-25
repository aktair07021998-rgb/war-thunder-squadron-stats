"""Формирует таблицу игроков и отчёты полка по ежемесячным замерам."""

import csv
import html
import io
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TITLE = "Таблица игроков"
TABLE_FILE = f"{TITLE}.csv"
SOURCE_FILE = "История замеров.csv"
GENERATED = f"<!-- Сформировано по файлу «{SOURCE_FILE}». -->"
OLD_MARKER = f"<!-- Отчёт сформирован по файлу «{TABLE_FILE}». -->"
MODES = ("РБ", "СБ")
METRICS = ("бои", "земля", "авиация")
FIELDS = ("месяц", "дата", "игрок", *(f"{mode}_{key}" for mode in MODES for key in METRICS))
BASE_HEADERS = ["Игрок", "РБ всего", "СБ всего", "Земля, ч:мин", "Воздух, ч:мин"]
MONTHS = "Январь Февраль Март Апрель Май Июнь Июль Август Сентябрь Октябрь Ноябрь Декабрь".split()
SHORT_MONTHS = "Янв. Фев. Март Апр. Май Июнь Июль Авг. Сент. Окт. Нояб. Дек.".split()
DASH = "—"


@dataclass
class Reading:
    taken: date
    values: dict


@dataclass
class Snapshot:
    nick: str
    taken: date
    modes: dict = field(default_factory=dict)


@dataclass
class Change:
    values: dict
    note: str = ""


def adjacent(month, step):
    year, number = map(int, month.split("-"))
    year, index = divmod(year * 12 + number - 1 + step, 12)
    return f"{year:04d}-{index + 1:02d}"


def month_name(month, short=False):
    year, number = month.split("-")
    names = SHORT_MONTHS if short else MONTHS
    return f"{names[int(number) - 1]} {year}"


def display_months(recorded):
    if not recorded:
        return []
    first = adjacent(recorded[0], 1)
    last = max(adjacent(first, 1), recorded[-1])
    months = [first]
    while months[-1] < last:
        months.append(adjacent(months[-1], 1))
    return months


def read_metric(key, raw):
    if not raw:
        return None
    if key == "бои":
        if not re.fullmatch(r"[0-9]+", raw):
            raise ValueError("число боёв должно быть целым и неотрицательным")
        return int(raw)
    match = re.fullmatch(r"([0-9]+):([0-5][0-9])", raw)
    if not match:
        raise ValueError("время указывается в часах и минутах, например 127:05")
    return int(match[1]) * 60 + int(match[2])


def load_snapshots(path):
    players = {}
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = csv.DictReader(source)
        if rows.fieldnames != list(FIELDS):
            raise ValueError("Проверьте заголовки в истории замеров.")
        for row in rows:
            try:
                if None in row:
                    raise ValueError("лишняя колонка, проверьте разделители")
                fields = {key: (value or "").strip() for key, value in row.items()}
                if not any(fields.values()):
                    continue
                month, day, nick = (fields[key] for key in FIELDS[:3])
                if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month):
                    raise ValueError("месяц указывается в формате ГГГГ-ММ")
                try:
                    taken = date.fromisoformat(day)
                except ValueError:
                    raise ValueError("укажите действительную дату в формате ГГГГ-ММ-ДД") from None
                if taken.isoformat() != day:
                    raise ValueError("дата указывается в формате ГГГГ-ММ-ДД")
                if not nick or any(ord(char) < 32 for char in nick):
                    raise ValueError("нужен игровой ник в одну строку")
                identity = (nick.casefold(), taken)
                if identity in seen:
                    raise ValueError(f"повтор замера {nick} за {day}")
                seen.add(identity)
                history = players.setdefault(nick.casefold(), {})
                period = history.setdefault(month, Snapshot(nick, taken))
                if taken > period.taken:
                    period.nick, period.taken = nick, taken
                for mode in MODES:
                    values = {key: read_metric(key, fields[f"{mode}_{key}"]) for key in METRICS}
                    existing = period.modes.get(mode)
                    if any(v is not None for v in values.values()) and (not existing or taken > existing.taken):
                        period.modes[mode] = Reading(taken, values)
            except ValueError as exc:
                raise ValueError(f"Строка {rows.line_num}. {exc}") from exc
    return players


def show(value, duration=False):
    if value is None:
        return DASH
    if duration:
        hours, minutes = divmod(value, 60)
        return f"{hours}:{minutes:02d}"
    return str(value)


def sum_known(values):
    return sum(values) if all(value is not None for value in values) else None


def current_totals(snapshot):
    values = [snapshot.modes[mode].values if mode in snapshot.modes else {} for mode in MODES]
    counts = [show(mode.get("бои")) for mode in values]
    times = [show(sum_known([mode.get(key) for mode in values]), duration=True) for key in METRICS[1:]]
    return counts + times


def change(start, end):
    if not start or not end:
        return Change({}, "пропущен замер")
    if end.taken <= start.taken:
        return Change({}, "проверьте даты")
    result = {}
    decreased = []
    for key in METRICS:
        before, after = start.values[key], end.values[key]
        if before is None or after is None:
            continue
        if after < before:
            decreased.append(key)
        else:
            result[key] = after - before
    note = "уменьшился счётчик (" + ", ".join(decreased) + ")" if decreased else ""
    return Change(result, note)


def trend(before, after):
    if before is None or after is None:
        return DASH
    difference = after - before
    if difference > 0:
        return f"🟩 +{difference}"
    return "🟥 0" if difference == 0 else f"🟥 −{abs(difference)}"


def nickname(text):
    # Сохраняем подчёркивания и другие символы ника при выводе в Markdown.
    return re.sub(r"[|\\`*_~@!\[\]()]", lambda m: f"&#{ord(m[0])};", html.escape(text))


def table(headers, rows):
    def line(items):
        return "| " + " | ".join(items) + " |"
    formatted = (line([nickname(row[0]), *row[1:]]) for row in rows)
    return "\n".join([line(headers), line(["---", *["---:"] * (len(headers) - 1)]), *formatted])


def csv_table(headers, rows):
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return out.getvalue()


def number_cells(values):
    return [show(values.get(key), duration=key != "бои") for key in METRICS]


def date_text(reading):
    return reading.taken.strftime("%d.%m.%Y") if reading else DASH


def mode_headers(first_label):
    headers = ["Игрок"]
    for mode in MODES:
        headers.extend([f"{first_label} {mode}", f"Бои {mode}", f"Земля {mode}, ч:мин", f"Воздух {mode}, ч:мин"])
    return headers


def make_rows(players, recorded, months):
    summary = []
    report_rows = {m: ([], []) for m in recorded}
    for history in players.values():
        first, last = min(history), max(history)
        latest = history[last]
        row = [latest.nick, *current_totals(latest)]
        counts = {}
        for month in sorted(set(recorded) | set(months)):
            prior = history.get(adjacent(month, -1))
            current = history.get(month)
            nick = current.nick if current else latest.nick
            source, activity, notes = [nick], [nick], []
            counts[month] = {}
            for mode in MODES:
                start = prior.modes.get(mode) if prior else None
                end = current.modes.get(mode) if current else None
                delta = change(start, end)
                counts[month][mode] = delta.values.get("бои")
                source.extend([date_text(end), *number_cells(end.values if end else {})])
                span = f"{date_text(start)} – {date_text(end)}" if start and end else DASH
                activity.extend([span, *number_cells(delta.values)])
                if delta.note:
                    notes.append(f"{mode} — {delta.note}")
            if month in report_rows and month >= first:
                sources, changes = report_rows[month]
                if current:
                    sources.append(source)
                if month != recorded[0]:
                    changes.append([*activity, ". ".join(notes) or DASH])
        for month in months:
            row.extend(show(counts[month][mode]) for mode in MODES)
        row.extend(trend(counts[months[-2]][mode], counts[months[-1]][mode]) for mode in MODES)
        summary.append(row)
    return summary, report_rows


GUIDE = f"""<details>
<summary>Как добавить следующий замер</summary>

Добавьте в [историю замеров](<{SOURCE_FILE}>) одну строку на игрока и дату. Обе вкладки, РБ и СБ, записываются рядом. В поле «месяц» укажите отчётный месяц в формате ГГГГ-ММ, в поле «дата» — день замера. Если дата на скриншоте неизвестна, используйте день получения. При уточнении замера за тот же месяц учитывается самая свежая запись каждого режима.

В разделе «Случайные бои» возьмите число из строки «Миссий завершено». Для наземного времени сложите танки, тяжёлые танки, САУ и ЗСУ. Для воздушного — истребители, бомбардировщики, штурмовики и вертолёты. Флот, полигон и отдельные миссии в сумму времени не входят. Время вводится в часах и минутах. Пустое поле означает, что значения нет, `0` и `0:00` — подтверждённый ноль.

Первый месяц наблюдения задаёт исходные значения. Бои за месяц считаются по разнице накопительных счётчиков двух соседних отчётных месяцев. Даты обоих снимков видны в месячном отчёте. Для полного календарного месяца снимайте показания на его границах. Например, снимок на 1 декабря можно отнести к закрытию ноября. При пропущенном замере или уменьшении накопительного счётчика в соответствующей ячейке появится прочерк. Для общей суммы времени нужны показания обеих вкладок игрока за один отчётный месяц.

GitHub Actions обновит таблицу после сохранения истории в основной ветке. Для пересчёта на компьютере выполните `python3 build_report.py`.

</details>"""


def build(root=ROOT):
    players = load_snapshots(root / SOURCE_FILE)
    recorded = sorted({month for history in players.values() for month in history})
    months = display_months(recorded)
    headers = [*BASE_HEADERS, *(f"{month_name(m, short=True)} {mode}" for m in months for mode in MODES),
               "Δ РБ", "Δ СБ"]
    summary, report_rows = make_rows(players, recorded, months)
    contents = {root / TABLE_FILE: csv_table(headers, summary)}
    intro = [GENERATED, f"# {TITLE}"]
    if players:
        latest = max(p.taken for history in players.values() for p in history.values())
        intro.append(f"Самая свежая запись от **{latest:%d.%m.%Y}**. Время на земле и в воздухе суммируется по РБ и СБ. Если карточка показывает только целые дни, сумма времени приблизительная.")
    intro.extend([table(headers, summary),
                  "Δ РБ и Δ СБ сравнивают бои за два последних месяца в таблице. 🟩 Рост, 🟥 столько же или меньше, — пока нет двух сопоставимых значений.",
                  f"[Открыть таблицу в CSV](<{TABLE_FILE}>) · [История замеров](<{SOURCE_FILE}>)"])
    reports = root / "reports"
    for month, (sources, changes) in report_rows.items():
        parts = [GENERATED, f"# {month_name(month)}", "[Таблица игроков](../README.md)"]
        if changes:
            parts.extend(["## Активность между замерами", table([*mode_headers("Период"), "Примечание"], changes)])
        parts.extend(["## Накопительные показания карточек", table(mode_headers("Дата"), sources)])
        contents[reports / f"{month}.md"] = "\n\n".join(parts) + "\n"
    if recorded:
        intro.extend(["## Замеры по месяцам",
                      "\n".join(f"- [{month_name(m)}](reports/{m}.md)" for m in reversed(recorded))])
    contents[root / "README.md"] = "\n\n".join([*intro, GUIDE]) + "\n"
    if recorded:
        reports.mkdir(exist_ok=True)
    for path, body in contents.items():
        path.write_text(body, encoding="utf-8")
    for path in reports.glob("????-??.md"):
        if path not in contents and path.read_text(encoding="utf-8").startswith((GENERATED + "\n", OLD_MARKER + "\n")):
            path.unlink()


if __name__ == "__main__":
    try:
        build()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
