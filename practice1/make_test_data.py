"""Вариант 1: три отдела, по 5 CSV-файлов, по 200 строк."""
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "company_data"
DEPARTMENTS = ("buh", "sales", "hr")
FILES_PER_DEPARTMENT = 5
LINES_PER_FILE = 200


def make_test_data():
    """Создать только новые учебные данные; существующие не перезаписывать."""
    if SOURCE.exists():
        raise FileExistsError(f"Папка уже существует: {SOURCE}. Данные не изменены.")
    rng = random.Random(1)  # одинаковые данные при повторении опыта с нуля
    for dept in DEPARTMENTS:
        (SOURCE / dept).mkdir(parents=True)
        for number in range(1, FILES_PER_DEPARTMENT + 1):
            lines = [f"{dept};doc{number};{rng.randint(1000, 99999)}"
                     for _ in range(LINES_PER_FILE)]
            (SOURCE / dept / f"report_{number}.csv").write_text(
                "\n".join(lines) + "\n", encoding="utf-8")
    print("[OK] company_data: 3 отдела, 15 CSV-файлов, по 200 строк (всего 3000)")


if __name__ == "__main__":
    make_test_data()
