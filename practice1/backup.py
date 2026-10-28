"""Полные резервные копии, ротация, проверка и безопасное восстановление."""
import argparse
import hashlib
import json
import shutil
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "company_data"
BACKUP_DIR = ROOT / "backups"
RESTORE_TEST = ROOT / "restore_test"
KEEP_LAST = 5


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def make_manifest(folder):
    """Словарь относительный путь -> SHA-256. Ссылки не копируем."""
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError(f"Нет обычной папки: {folder}")
    result = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Символическая ссылка не поддерживается: {path}")
        if path.is_file():
            result[path.relative_to(folder).as_posix()] = sha256(path)
        elif not path.is_dir():
            raise ValueError(f"Специальный файл не поддерживается: {path}")
    return result


def manifest_path(archive):
    return archive.with_suffix("").with_suffix(".json")


def create_backup():
    """Создать архив и манифест; проверить, что источник не менялся."""
    files = make_manifest(SOURCE)
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    archive = BACKUP_DIR / f"backup_{stamp}.tar.gz"
    partial = archive.with_name(archive.name + ".part")
    try:
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(SOURCE, arcname=SOURCE.name)
        if make_manifest(SOURCE) != files:
            raise RuntimeError("Данные изменились во время копирования; повторите позже.")
        manifest = {
            "created": stamp,
            "source": SOURCE.name,
            "archive_sha256": sha256(partial),
            "source_bytes": sum((SOURCE / name).stat().st_size for name in files),
            "archive_bytes": partial.stat().st_size,
            "files": files,
        }
        partial.rename(archive)
        manifest_path(archive).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        partial.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
        manifest_path(archive).unlink(missing_ok=True)
        raise
    print(f"[OK] Создан {archive.name}: {len(files)} файлов, {archive.stat().st_size} байт")
    return archive


def rotate():
    """Удалить старые архивы вместе с их JSON, оставить KEEP_LAST пар."""
    if KEEP_LAST < 1:
        raise ValueError("KEEP_LAST должен быть не меньше 1")
    archives = sorted(BACKUP_DIR.glob("backup_*.tar.gz"))
    deleted = []
    for old in archives[:-KEEP_LAST]:
        old.unlink()
        manifest_path(old).unlink(missing_ok=True)
        deleted.append(old.name)
        print(f"[ROTATE] Удалены {old.name} и его JSON")
    return deleted


def extract_checked(archive, destination):
    """Проверить архив до распаковки. Распаковать только в новую папку."""
    manifest = json.loads(manifest_path(archive).read_text(encoding="utf-8"))
    if sha256(archive) != manifest["archive_sha256"]:
        raise ValueError("Архив повреждён: контрольная сумма SHA-256 не совпадает")
    if manifest.get("source") != SOURCE.name or not isinstance(manifest["files"], dict):
        raise ValueError("Неверная структура манифеста")
    if destination.exists():
        raise FileExistsError("Распаковка разрешена только в новую папку")
    with tarfile.open(archive, "r:gz") as tar:
        names = set()
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or not path.parts
                    or path.parts[0] != SOURCE.name
                    or not (member.isfile() or member.isdir())
                    or member.name in names):
                raise ValueError(f"Небезопасный элемент архива: {member.name}")
            names.add(member.name)
        destination.mkdir()
        tar.extractall(destination, filter="data")  # Python 3.12+
    return manifest, make_manifest(destination / SOURCE.name)


def compare_manifests(expected, restored):
    """Missing: есть в JSON, нет на диске. Extra: наоборот."""
    return {
        "missing": sorted(expected.keys() - restored.keys()),
        "extra": sorted(restored.keys() - expected.keys()),
        "changed": sorted(name for name in expected.keys() & restored.keys()
                          if expected[name] != restored[name]),
    }


def show_differences(differences):
    for kind, label in (("missing", "Отсутствуют"), ("extra", "Лишние"),
                        ("changed", "Изменены")):
        names = differences[kind]
        print(f"  {kind} / {label} ({len(names)}): {', '.join(names) or 'нет'}")


def verify_restore(archive):
    """Проверить реальную распаковку и перечислить все различия."""
    RESTORE_TEST.mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=RESTORE_TEST, prefix="check_") as tmp:
            manifest, restored = extract_checked(Path(archive), Path(tmp) / "unpacked")
            differences = compare_manifests(manifest["files"], restored)
            show_differences(differences)
            if any(differences.values()):
                print("[FAIL] Восстановленные файлы не совпадают с манифестом")
                return False
            print(f"[OK] Восстановление проверено: {len(restored)} файлов совпадают")
            return True
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        print(f"[FAIL] {error}")
        return False


def restore(archive):
    """Вначале проверить копию, затем заменить повреждённый company_data."""
    started = time.perf_counter()
    if SOURCE.is_symlink():
        raise ValueError("Нельзя заменять company_data, если это ссылка")
    with tempfile.TemporaryDirectory(dir=ROOT, prefix=".restore_") as tmp:
        stage = Path(tmp) / "unpacked"
        manifest, restored = extract_checked(Path(archive), stage)
        differences = compare_manifests(manifest["files"], restored)
        if any(differences.values()):
            show_differences(differences)
            raise ValueError("Восстановление отменено: файлы не совпадают с манифестом")
        # Не удаляем исходник заранее: плохая копия не должна лишить нас остатка данных.
        previous = Path(tmp) / "previous_company_data"
        if SOURCE.exists():
            SOURCE.rename(previous)
        try:
            (stage / SOURCE.name).rename(SOURCE)
        except OSError:
            if previous.exists():
                previous.rename(SOURCE)
            raise
        if previous.exists():
            shutil.rmtree(previous)
    elapsed = time.perf_counter() - started
    print(f"[OK] company_data восстановлена: {len(restored)} файлов; {elapsed:.6f} с")
    return elapsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("backup", "verify", "restore"),
                        default="backup")
    parser.add_argument("archive", nargs="?", type=Path)
    args = parser.parse_args()
    if args.action == "backup":
        archive = create_backup()
        # Сначала проверяем новую копию; только после успеха удаляем старые.
        if not verify_restore(archive):
            return 1
        rotate()
        return 0
    if args.archive is None:
        parser.error("Для verify/restore укажите путь к архиву")
    if args.action == "verify":
        return 0 if verify_restore(args.archive) else 1
    restore(args.archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
