"""Сводка полка по боям между ежемесячными замерами."""

import argparse
import csv
import html
import io
import re
from datetime import date
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TITLE = "Таблица игроков"
TABLE = f"{TITLE}.csv"
TRIAL_TABLE = "Проверка таблицы.csv"
SOURCE = "История замеров.csv"
MARKER = f"<!-- Сформировано по файлу «{SOURCE}». -->"
MODES = ("РБ", "СБ")
FIELDS = ("месяц", "дата", "игрок", "РБ_бои", "СБ_бои")
MONTH_NAMES = "Январь Февраль Март Апрель Май Июнь Июль Август Сентябрь Октябрь Ноябрь Декабрь".split()
DASH = "—"


def adjacent(month, step):
    year, number = map(int, month.split("-"))
    year, index = divmod(year * 12 + number - 1 + step, 12)
    return f"{year:04d}-{index + 1:02d}"


def month_name(month):
    year, number = month.split("-")
    return f"{MONTH_NAMES[int(number) - 1]} {year}"


def parse_day(raw):
    try:
        taken = date.fromisoformat(raw)
    except ValueError:
        taken = None
    if taken is None or taken.isoformat() != raw:
        raise ValueError("укажите дату в формате ГГГГ-ММ-ДД")
    return taken


def load_history(path):
    players = {}
    by_day = {}
    trials = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(FIELDS).issubset(reader.fieldnames or ()):
            raise ValueError(f"Проверьте заголовок файла «{SOURCE}».")
        for row in reader:
            try:
                if None in row:
                    raise ValueError("лишний столбец")
                month, day, nick, rb, sb = ((row[name] or "").strip() for name in FIELDS)
                trial_from = (row.get("проверка_от") or "").strip()
                if not any((month, day, nick, rb, sb, trial_from)):
                    continue
                if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month):
                    raise ValueError("укажите отчётный месяц в формате ГГГГ-ММ")
                if not nick or any(ord(c) < 32 for c in nick):
                    raise ValueError("укажите игровой ник в одну строку")
                taken = parse_day(day)
                key = nick.casefold()
                daily = by_day.setdefault(key, {})
                if taken in daily:
                    raise ValueError(f"повтор снимка игрока {nick} за {day}")
                reading = {"ник": (taken, nick)}
                for mode, raw in zip(MODES, (rb, sb)):
                    if not raw:
                        continue
                    if not re.fullmatch(r"[0-9]+", raw):
                        raise ValueError(f"счётчик {mode} должен быть целым и неотрицательным")
                    reading[mode] = (taken, int(raw))
                daily[taken] = reading
                if trial_from:
                    start = parse_day(trial_from)
                    if start >= taken:
                        raise ValueError("дата начала проверки должна предшествовать дате замера")
                    trials.add((taken, start))
                    continue
                # Для каждого режима берём последний основной замер отчётного месяца.
                period = players.setdefault(key, {}).setdefault(month, {})
                for field, value in reading.items():
                    if field not in period or taken > period[field][0]:
                        period[field] = value
            except ValueError as exc:
                raise ValueError(f"Строка {reader.line_num}. {exc}") from exc
    return players, by_day, trials


def cell(value):
    return DASH if value is None else str(value)


def snapshot_cells(snapshot):
    return [cell(snapshot[mode][1]) if mode in snapshot else DASH for mode in MODES]


def played(before, after):
    if not before or not after:
        return None
    if after[0] <= before[0] or after[1] < before[1]:
        return None
    return after[1] - before[1]


def mode_result(rb, sb, markdown):
    if rb is None or sb is None:
        return cell(sb)
    if rb == sb:
        return f"{sb} (поровну)"
    lead = "СБ" if sb > rb else "РБ"
    if markdown:
        color = "green" if sb > rb else "red"
        number = r"$\color{" + color + "}" + str(sb) + "$"
    else:
        mark = "🟩" if sb > rb else "🟥"
        number = f"{mark} {sb}"
    return f"{number} ({lead} +{abs(sb - rb)})"


def escape_nick(nick):
    return re.sub(r"[|\\\x60*_~@!\[\]()$]", lambda m: f"&#{ord(m[0])};", html.escape(nick))


def column_title(players, period, mode, fallback):
    if not period:
        return f"{fallback} {mode}"
    if isinstance(period, date):
        return f"{period:%d.%m.%Y} {mode}"
    dates = set()
    for history in players.values():
        reading = history.get(period, {}).get(mode)
        if reading:
            dates.add(reading[0])
    label = next(iter(dates)).strftime("%d.%m.%Y") if len(dates) == 1 else month_name(period)
    return f"{label} {mode}"


def period_table(players, start, end, duration="за месяц"):
    headers = ["Игрок"]
    for period, label in ((start, "Первый замер"), (end, "Второй замер")):
        headers.extend(column_title(players, period, mode, label) for mode in MODES)
    headers.extend(f"{mode} {duration}" for mode in MODES)

    records = []
    for history in players.values():
        known = [key for key in history if key <= end]
        if not known:
            continue
        nick = history[max(known)]["ник"][1]
        before, after = history.get(start, {}), history.get(end, {})
        rb, sb = (played(before.get(mode), after.get(mode)) for mode in MODES)
        records.append((nick, snapshot_cells(before) + snapshot_cells(after), rb, sb))

    known_sb = [sb for _, _, _, sb in records if sb is not None]
    count, total = len(known_sb), sum(known_sb)
    mean = DASH
    if count:
        mean = f"{Decimal(total) / count:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    average_row = [f"Среднее СБ на игрока {duration}", "", "", "", "", "", mean]
    plain = [average_row]
    rich = [[f"**{value}**" if value and value != DASH else value for value in average_row]]
    for nick, snapshots, rb, sb in records:
        badge = ""
        if sb is not None:
            # Сравниваем с точным средним до округления для вывода.
            badge = "🟢 " if sb * count >= total else "🔴 "
        base = [*snapshots, cell(rb)]
        plain.append([badge + nick, *base, mode_result(rb, sb, False)])
        rich.append([badge + escape_nick(nick), *base, mode_result(rb, sb, True)])
    return headers, plain, rich, count


def csv_text(headers, rows):
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerows([headers, *rows])
    return out.getvalue()


def markdown_table(headers, rows):
    def line(items):
        return "| " + " | ".join(items) + " |"
    return "\n".join([line(headers), line(["---", *["---:"] * (len(headers) - 1)]),
                      *(line(row) for row in rows)])


LEGEND = (
    "Для каждого режима вычитаем первый накопительный счётчик из второго. "
    "Пропущенный замер или уменьшение счётчика даёт прочерк в итоге этого режима. "
    "Число СБ зелёное при перевесе СБ и красное при перевесе РБ. "
    "При равенстве оба итога нейтральны. В скобках указан перевес в боях.\n\n"
    "В первой строке складываем СБ, сыгранные за месяц, и делим на число игроков "
    "с двумя сопоставимыми замерами СБ за этот период. Подтверждённый ноль входит в расчёт. "
    "У ника 🟢 означает, что игрок достиг среднего или превысил его, а 🔴 — сыграл меньше."
)


def build(root=ROOT, publish=False):
    players, by_day, trials = load_history(root / SOURCE)
    if not publish and not (root / TABLE).is_file():
        return False
    months = sorted({month for history in players.values() for month in history})
    completed = months[1:]
    if completed:
        displayed = completed[-1]
    else:
        displayed = adjacent(months[0], 1) if months else None
    outputs = {}
    reports = root / "reports"
    for period in completed or [displayed]:
        start = adjacent(period, -1) if period else None
        headers, plain, rich, count = period_table(players, start, period)
        table = markdown_table(headers, rich)
        coverage = f"Среднее по СБ рассчитано для {count} из {len(plain) - 1} игроков."
        if not count:
            coverage = "Пока ни у одного игрока нет двух сопоставимых замеров СБ за этот период."
        source_link = f"[История замеров](<{SOURCE}>)"
        if period in completed:
            parts = [MARKER, f"# {month_name(period)}", "[На главную](../README.md)", table, coverage, LEGEND]
            outputs[reports / f"{period}.md"] = "\n\n".join(parts) + "\n"
        if period == displayed:
            outputs[root / TABLE] = csv_text(headers, plain)
            parts = [MARKER, f"# {TITLE}", table, coverage, LEGEND,
                     "Для следующего замера добавьте в историю одну строку на игрока и дату. "
                     "На вкладках РБ и СБ возьмите счётчик «Миссий завершено» из раздела «Случайные бои».",
                     f"[Таблица в CSV](<{TABLE}>) · {source_link}"]
            if trials:
                last, first = max(trials)
                trial_headers, trial_plain, trial_rows, trial_count = period_table(by_day, first, last, "за период")
                outputs[root / TRIAL_TABLE] = csv_text(trial_headers, trial_plain)
                parts.extend(["## Проверка алгоритма", markdown_table(trial_headers, trial_rows),
                              f"Игроков с двумя замерами СБ за этот период — {trial_count} из {len(trial_rows) - 1}. "
                              "Для пробы укажите в истории дату первого замера в столбце «проверка_от». "
                              "Для основного замера оставьте этот столбец пустым.",
                              f"[Пробная таблица в CSV](<{TRIAL_TABLE}>)"])
            if completed:
                parts.extend(["## Отчёты по месяцам", "\n".join(
                    f"- [{month_name(m)}](reports/{m}.md)" for m in reversed(completed))])
            outputs[root / "README.md"] = "\n\n".join(parts) + "\n"
    if completed:
        reports.mkdir(exist_ok=True)
    for path, body in outputs.items():
        path.write_text(body, encoding="utf-8")
    if not trials:
        (root / TRIAL_TABLE).unlink(missing_ok=True)
    for path in reports.glob("????-??.md"):
        if path not in outputs and path.read_text(encoding="utf-8").startswith(MARKER + "\n"):
            path.unlink()
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="опубликовать таблицу после паузы")
    try:
        if not build(publish=parser.parse_args().publish):
            print("Публикация таблицы приостановлена.")
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
