"""Воспроизвести опыт один раз с нуля. Только собственные учебные каталоги."""
import json
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRACTICE = ROOT / "practice1"
sys.path.insert(0, str(PRACTICE))
import backup

EVIDENCE = ROOT / "evidence"
EXPERIMENTS = PRACTICE / "experiments"


def command(*arguments):
    print("$", " ".join(arguments), flush=True)
    subprocess.run(arguments, cwd=ROOT, check=True)


def main():
    if backup.SOURCE.exists() or backup.BACKUP_DIR.exists() or EXPERIMENTS.exists():
        raise SystemExit("Опыт уже запускался: исходные данные/копии не перезаписаны. "
                         "Для нового опыта распакуйте проект в новую пустую папку.")
    EVIDENCE.mkdir(exist_ok=True)
    EXPERIMENTS.mkdir()
    started = datetime.now(timezone.utc).isoformat()
    print("=== РЕАЛЬНЫЙ УЧЕБНЫЙ ЗАПУСК. ВАРИАНТ 1 ===", flush=True)
    command(sys.executable, "--version")
    print(f"UTC: {started}\nOS: {platform.platform()}\nCWD: {ROOT}", flush=True)
    command(sys.executable, "practice1/make_test_data.py")
    original = backup.make_manifest(backup.SOURCE)
    rows = {name: len((backup.SOURCE / name).read_text().splitlines()) for name in original}
    assert len(rows) == 15 and set(rows.values()) == {200}
    source_bytes = sum((backup.SOURCE / name).stat().st_size for name in original)
    print(f"[DATA] {len(rows)} файлов, {sum(rows.values())} строк, {source_bytes} байт", flush=True)
    runs = []
    for number in range(1, 9):
        print(f"\n=== КОПИЯ {number}/8 ===", flush=True)
        command(sys.executable, "practice1/backup.py")
        remaining = sorted(backup.BACKUP_DIR.glob("*.tar.gz"))
        runs.append({"run": number, "archive": remaining[-1].name,
                     "remaining": len(remaining)})
        print(f"[COUNT] Архивов после запуска: {len(remaining)}", flush=True)
        if number < 8:
            time.sleep(2)
    assert backup.make_manifest(backup.SOURCE) == original
    archives = sorted(backup.BACKUP_DIR.glob("*.tar.gz"))
    assert len(archives) == len(list(backup.BACKUP_DIR.glob("*.json"))) == 5
    for archive in archives:
        assert json.loads(backup.manifest_path(archive).read_text())["files"] == original
    latest = archives[-1]
    original_json = backup.manifest_path(latest).read_text(encoding="utf-8")
    manifest = json.loads(original_json)
    (EVIDENCE / "manifest_original.json").write_text(original_json, encoding="utf-8")
    archive_bytes = latest.stat().st_size
    print("\n=== РОТАЦИЯ И СЖАТИЕ ===", flush=True)
    print("[OK] После 8 запусков осталось 5 архивов и 5 JSON; 3 старые пары удалены.")
    print("[OK] Хеши всех 15 файлов совпадают во всех пяти сохранённых копиях.")
    print(f"[SIZE] Исходные файлы: {source_bytes} байт; последний архив: {archive_bytes} байт")
    print(f"[RATIO] {source_bytes} / {archive_bytes} = {source_bytes / archive_bytes:.4f} раза", flush=True)

    print("\n=== ЗАДАНИЕ 3: ИЗМЕНЁННЫЙ ХЕШ И УДАЛЁННАЯ ЗАПИСЬ JSON ===", flush=True)
    tampered = json.loads(original_json)
    changed_name, extra_name = sorted(tampered["files"])[:2]
    old_hash = tampered["files"][changed_name]
    tampered["files"][changed_name] = ("0" if old_hash[0] != "0" else "1") + old_hash[1:]
    del tampered["files"][extra_name]
    tampered_text = json.dumps(tampered, ensure_ascii=False, indent=2) + "\n"
    (EVIDENCE / "manifest_tampered.json").write_text(tampered_text, encoding="utf-8")
    try:
        backup.manifest_path(latest).write_text(tampered_text, encoding="utf-8")
        assert backup.verify_restore(latest) is False
        differences = backup.compare_manifests(tampered["files"], original)
        assert differences == {"missing": [], "extra": [extra_name], "changed": [changed_name]}
    finally:
        backup.manifest_path(latest).write_text(original_json, encoding="utf-8")
    print("[OK] Исправный исходный JSON возвращён; последняя копия не потеряна.", flush=True)

    print("\n=== ДОПОЛНИТЕЛЬНО: ПРОВЕРКА MISSING ===", flush=True)
    missing = dict(original)
    missing["buh/missing_demo.csv"] = "0" * 64
    missing_diff = backup.compare_manifests(missing, original)
    backup.show_differences(missing_diff)
    assert missing_diff == {"missing": ["buh/missing_demo.csv"], "extra": [], "changed": []}

    print("\n=== ЗАДАНИЕ 3: ПОВРЕЖДЕНИЕ КОПИИ АРХИВА ===", flush=True)
    corrupted = EXPERIMENTS / latest.name
    shutil.copy2(latest, corrupted)
    shutil.copy2(backup.manifest_path(latest), backup.manifest_path(corrupted))
    with corrupted.open("r+b") as stream:
        stream.seek(corrupted.stat().st_size // 2)
        location = stream.tell()
        old_bytes = stream.read(3)
        stream.seek(location)
        stream.write(bytes(value ^ 0xFF for value in old_bytes))
    assert backup.verify_restore(corrupted) is False
    source_before_failed_restore = backup.make_manifest(backup.SOURCE)
    try:
        backup.restore(corrupted)
    except ValueError as error:
        print(f"[OK] restore() отклонила повреждённый архив: {error}")
    else:
        raise AssertionError("Повреждённый архив не был отклонён")
    assert backup.make_manifest(backup.SOURCE) == source_before_failed_restore
    print("[OK] При отказе restore() исходные 15 файлов остались неизменными.", flush=True)

    print("\n=== ЗАДАНИЕ 4: УДАЛЕНИЕ BUH И ВОССТАНОВЛЕНИЕ ===", flush=True)
    assert backup.verify_restore(latest) is True
    incident_target = backup.SOURCE / "buh"
    assert incident_target.parent == PRACTICE / "company_data" and not incident_target.is_symlink()
    shutil.rmtree(incident_target)  # только созданный этим опытом учебный buh
    print("[INCIDENT] Удалена company_data/buh; осталось файлов:", len(backup.make_manifest(backup.SOURCE)))
    assert not incident_target.exists()
    incident_missing = backup.compare_manifests(original, backup.make_manifest(backup.SOURCE))
    backup.show_differences(incident_missing)
    restore_seconds = backup.restore(latest)
    restored = backup.make_manifest(backup.SOURCE)
    assert restored == original and incident_target.is_dir()
    print("[OK] buh вернулась: 5 файлов. Всего 15 файлов; SHA-256 каждого совпал с JSON.", flush=True)
    command(sys.executable, "practice1/backup.py", "verify", str(latest))

    result = {
        "started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "os": platform.platform(),
        "student": "Акишев Ақжан Бақытжанұлы", "variant": 1,
        "source_files": len(original), "rows_per_file": 200, "total_rows": sum(rows.values()),
        "source_bytes": source_bytes, "latest_archive_bytes": archive_bytes,
        "compression_ratio": source_bytes / archive_bytes,
        "space_saved_percent": (1 - archive_bytes / source_bytes) * 100,
        "backup_runs": runs, "interval_seconds": 2, "keep_last": backup.KEEP_LAST,
        "archives_remaining": len(archives), "json_remaining": 5,
        "deleted_old_pairs": 3, "latest_archive": latest.name,
        "latest_archive_sha256": backup.sha256(latest),
        "same_file_hashes_all_retained_backups": True,
        "tampered_manifest_differences": differences,
        "missing_demo_differences": missing_diff,
        "corrupt_archive_detected": True, "corrupt_restore_source_unchanged": True,
        "corrupted_archive_sha256": backup.sha256(corrupted),
        "incident_missing_files": incident_missing["missing"],
        "restore_seconds": restore_seconds, "restored_buh_files": 5,
        "restored_files": len(restored), "restored_matches_manifest": restored == manifest["files"],
        "rpo_hours": 24, "required_rpo_hours": 1,
        "restore_transfer_seconds": 300 * 1024 / 150, "preparation_seconds": 1800,
        "calculated_rto_seconds": 300 * 1024 / 150 + 1800, "required_rto_seconds": 7200,
    }
    (EVIDENCE / "evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n[COMPLETE] Все проверки демонстрации выполнены; evidence/evidence.json записан.")


if __name__ == "__main__":
    main()
