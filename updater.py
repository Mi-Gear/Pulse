"""Проверка и установка обновлений Pulse перед запуском Flask.

Обновление выполняется только до импорта и запуска приложения. Во время
работы сервера файлы проекта не изменяются.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Mi-Gear/Pulse")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
AUTO_UPDATE = os.environ.get("AUTO_UPDATE", "true").lower() in {
    "1", "true", "yes", "on"
}
LOCAL_FILES = ("config.py",)


def _git(*args, timeout=120):
    """Запускает git без интерактивных запросов и возвращает результат."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", *args],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def update_at_startup():
    """Синхронизирует проект с GitHub до запуска приложения.

    Возвращает False, если обновление не требуется или проверка недоступна.
    При успешном обновлении заменяет текущий процесс новым запуском app.py.
    """
    if not AUTO_UPDATE:
        print("[UPDATE] Автообновление отключено (AUTO_UPDATE=false).")
        return False

    try:
        check = _git("rev-parse", "--is-inside-work-tree", timeout=10)
        if check.returncode != 0 or check.stdout.strip() != "true":
            print("[UPDATE] Git-репозиторий не найден. Запускаю текущую версию.")
            return False

        remote_url = f"https://github.com/{GITHUB_REPO}.git"
        print(f"[UPDATE] Проверяю {GITHUB_REPO}@{GITHUB_BRANCH}...")

        fetch = _git(
            "fetch", "--no-tags", remote_url, GITHUB_BRANCH, timeout=180
        )
        if fetch.returncode != 0:
            print(
                "[UPDATE] Не удалось проверить GitHub; "
                "запускаю установленную версию.\n"
                + fetch.stderr.strip()
            )
            return False

        local_head = _git("rev-parse", "HEAD", timeout=10)
        remote_head = _git("rev-parse", "FETCH_HEAD", timeout=10)
        if local_head.returncode != 0 or remote_head.returncode != 0:
            print("[UPDATE] Не удалось определить версии. Запускаю текущую.")
            return False

        local_sha = local_head.stdout.strip()
        remote_sha = remote_head.stdout.strip()
        if local_sha == remote_sha:
            print("[UPDATE] Установлена актуальная версия.")
            return False

        print(
            f"[UPDATE] Найдено обновление: "
            f"{local_sha[:8]} -> {remote_sha[:8]}. Устанавливаю..."
        )

        # Локальный config.py содержит настройки конкретного компьютера.
        # run_settings.txt и другие ignored/untracked файлы git reset не удаляет.
        backups = {}
        for filename in LOCAL_FILES:
            path = REPO_DIR / filename
            if path.is_file():
                backups[filename] = (path.read_bytes(), path.stat().st_mode)

        reset = _git("reset", "--hard", "FETCH_HEAD", timeout=180)
        if reset.returncode != 0:
            print(
                "[UPDATE] Ошибка установки обновления. "
                "Приложение не будет запущено, чтобы не работать "
                "на частично обновлённом коде.\n"
                + reset.stderr.strip()
            )
            raise SystemExit(1)

        for filename, (content, mode) in backups.items():
            path = REPO_DIR / filename
            path.write_bytes(content)
            try:
                path.chmod(mode)
            except OSError:
                pass

        print("[UPDATE] Обновление установлено. Перезапускаю Pulse...")

        # Новый процесс перечитает app.py и все модули уже с диска.
        os.execv(sys.executable, [sys.executable, *sys.argv])

    except FileNotFoundError:
        print("[UPDATE] Git не установлен. Запускаю текущую версию.")
    except subprocess.TimeoutExpired:
        print("[UPDATE] Истекло время ожидания GitHub/Git. Запускаю текущую версию.")
    except OSError as exc:
        print(f"[UPDATE] Ошибка обновления: {exc}. Запускаю текущую версию.")

    return False
