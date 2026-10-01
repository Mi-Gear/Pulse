# updater.py
"""
Модуль автоматической синхронизации кода с GitHub-репозиторием.
Использует только стандартную библиотеку Python — без pip install.

Логика:
    • Раз в CHECK_INTERVAL секунд делается git fetch в FETCH_HEAD.
    • Сравнивается содержимое рабочего дерева с FETCH_HEAD.
    • Если есть расхождения (кроме IGNORE_FILES) — рабочее дерево
      принудительно приводится к состоянию FETCH_HEAD.
    • HEAD при этом не двигается (если HARD_RESET=false), но файлы
      на диске становятся ровно такими, как в репозитории.

Настройка через переменные окружения:
    GITHUB_REPO      — "owner/repo"          (обязательно)
    GITHUB_BRANCH    — ветка, по умолчанию "main"
    GITHUB_TOKEN     — personal access token (обязательно для приватного репо)
    CHECK_INTERVAL   — интервал в секундах, по умолчанию 60
    AUTO_UPDATE      — "true"/"false", по умолчанию true
    HARD_RESET       — "true"/"false", по умолчанию false
                       (если true — дополнительно git reset --hard FETCH_HEAD)
    REPO_DIR         — путь к локальному git-репозиторию
                       (по умолчанию — директория этого файла)

Использование:
    from updater import update_checker
    ...
    if update_checker:
        update_checker.start()
    ...
    update_checker.stop()
"""

import os
import logging
import subprocess
import threading
from datetime import datetime, timezone
import sqlite3
import tempfile


# ---------- Конфигурация из окружения ----------
GITHUB_TOKEN   = os.environ.get(
    "GITHUB_TOKEN",
    "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",
)

GITHUB_REPO = os.environ.get(
    "GITHUB_REPO",
    "Mi-Gear/Pulse",
)

GITHUB_BRANCH = os.environ.get(
    "GITHUB_BRANCH",
    "main",
)

GITHUB_TOKEN = os.environ.get(
    "GITHUB_TOKEN",
)

CHECK_INTERVAL = int(
    os.environ.get(
        "CHECK_INTERVAL",
        "60",
    )
)

AUTO_UPDATE = os.environ.get(
    "AUTO_UPDATE",
    "true",
).lower() in (
    "1",
    "true",
    "yes",
    "on",
)

HARD_RESET = os.environ.get(
    "HARD_RESET",
    "false",
).lower() in (
    "1",
    "true",
    "yes",
    "on",
)


# ============================================================
# Настройки базы данных
# ============================================================

DB_BRANCH = os.environ.get(
    "DB_BRANCH",
    "db",
)

DB_REPO_PATH = os.environ.get(
    "DB_REPO_PATH",
    "instance/servicedesk.db",
)

DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "instance",
        "servicedesk.db",
    ),
)


# ============================================================
# Git-репозиторий
# ============================================================

REPO_DIR = os.environ.get(
    "REPO_DIR",
    os.path.dirname(os.path.abspath(__file__)),
)


# ============================================================
# Файлы, которые updater не должен трогать
# ============================================================

IGNORE_FILES = {
    ".env",
    "config.py",
    "local_settings.py",
}


# Что сохранять при git clean
CLEAN_KEEP_PATTERNS = [
    ".env",
    "*.log",
    "__pycache__/",
    ".venv/",
    "venv/",
]


# ============================================================
# Логгер
# ============================================================

update_logger = logging.getLogger(
    "auto_update"
)

update_logger.setLevel(
    logging.INFO
)

if not update_logger.handlers:
    _h = logging.StreamHandler()

    _h.setFormatter(
        logging.Formatter(
            "[%(asctime)s] [UPDATE] %(levelname)s: %(message)s"
        )
    )

    update_logger.addHandler(_h)


# ============================================================
# Updater
# ============================================================

class GitHubUpdateChecker:
    """
    Периодически синхронизирует локальный код с GitHub.

    Код:
        main

    База:
        db

    Ветка db никогда не checkout-ится.
    """

    def __init__(
        self,
        repo,
        branch="main",
        token=None,
        interval=60,
        auto_update=True,
        hard_reset=False,
        repo_dir=None,
    ):

        if not repo:
            raise ValueError(
                "GITHUB_REPO не задан"
            )

        if not token:
            raise ValueError(
                "GITHUB_TOKEN обязателен для приватного репозитория"
            )

        self.repo = repo
        self.branch = branch
        self.token = token
        self.interval = interval
        self.auto_update = auto_update
        self.hard_reset = hard_reset

        self.repo_dir = (
            repo_dir
            or REPO_DIR
        )

        self.db_path = os.path.abspath(
            DB_PATH
        )

        self._stop_event = threading.Event()

        self._thread = None

        self._lock = threading.Lock()

        # Диагностическая информация

        self.last_check = None

        self.last_changed_files = []

        self.update_available = False

        self.last_db_revision = None


    # ========================================================
    # Git URL
    # ========================================================

    def _auth_url(self):
        return (
            f"https://x-access-token:"
            f"{self.token}"
            f"@github.com/"
            f"{self.repo}.git"
        )


    # ========================================================
    # Скрытие токена
    # ========================================================

    def _redact(self, text):

        if self.token and self.token in text:
            return text.replace(
                self.token,
                "***",
            )

        return text


    # ========================================================
    # Fetch основной ветки
    # ========================================================

    def _fetch(self):

        try:

            r = subprocess.run(
                [
                    "git",
                    "fetch",
                    "--prune",
                    self._auth_url(),
                    self.branch,
                ],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                timeout=120,
                env={
                    **os.environ,
                    "GIT_TERMINAL_PROMPT": "0",
                },
            )

            if r.returncode == 0:
                return True

            update_logger.error(
                "git fetch ошибка (rc=%s):\n"
                "STDOUT: %s\n"
                "STDERR: %s",
                r.returncode,
                r.stdout.strip(),
                self._redact(
                    r.stderr.strip()
                ),
            )

        except (
            subprocess.SubprocessError,
            FileNotFoundError,
        ) as e:

            update_logger.error(
                "git fetch исключение: %s",
                e,
            )

        return False


    # ========================================================
    # Список изменившихся файлов
    # ========================================================

    def _get_changed_files(self):

        try:

            r = subprocess.run(
                [
                    "git",
                    "diff",
                    "--name-only",
                    "FETCH_HEAD",
                ],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if r.returncode != 0:

                update_logger.error(
                    "git diff ошибка (rc=%s): %s",
                    r.returncode,
                    self._redact(
                        r.stderr.strip()
                    ),
                )

                return None

            changed = [
                f.strip()
                for f in r.stdout.splitlines()
                if (
                    f.strip()
                    and f.strip()
                    not in IGNORE_FILES
                )
            ]

            return changed

        except (
            subprocess.SubprocessError,
            FileNotFoundError,
        ) as e:

            update_logger.error(
                "git diff исключение: %s",
                e,
            )

            return None


    # ========================================================
    # Синхронизация кода
    # ========================================================

    def _sync_from_fetch_head(self):

        try:

            # ----------------------------------------------
            # Восстанавливаем отслеживаемые файлы
            # ----------------------------------------------

            r = subprocess.run(
                [
                    "git",
                    "restore",
                    "--source=FETCH_HEAD",
                    "--worktree",
                    "--staged",
                    "--",
                    ".",
                ],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                timeout=120,
                env={
                    **os.environ,
                    "GIT_TERMINAL_PROMPT": "0",
                },
            )

            if r.returncode != 0:

                update_logger.error(
                    "git restore ошибка (rc=%s):\n"
                    "STDOUT: %s\n"
                    "STDERR: %s",
                    r.returncode,
                    r.stdout.strip(),
                    self._redact(
                        r.stderr.strip()
                    ),
                )

                return False


            if r.stdout.strip():

                update_logger.info(
                    "git restore:\n%s",
                    r.stdout.strip(),
                )


            # ----------------------------------------------
            # Удаляем ненужные untracked-файлы
            # ----------------------------------------------

            clean_cmd = [
                "git",
                "clean",
                "-fd",
            ]

            for pattern in CLEAN_KEEP_PATTERNS:

                clean_cmd += [
                    "-e",
                    pattern,
                ]


            r2 = subprocess.run(
                clean_cmd,
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                timeout=60,
                env={
                    **os.environ,
                    "GIT_TERMINAL_PROMPT": "0",
                },
            )

            if (
                r2.returncode == 0
                and r2.stdout.strip()
            ):

                update_logger.info(
                    "git clean -fd:\n%s",
                    r2.stdout.strip(),
                )


            # ----------------------------------------------
            # Опциональный hard reset
            # ----------------------------------------------

            if self.hard_reset:

                r3 = subprocess.run(
                    [
                        "git",
                        "reset",
                        "--hard",
                        "FETCH_HEAD",
                    ],
                    cwd=self.repo_dir,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    env={
                        **os.environ,
                        "GIT_TERMINAL_PROMPT": "0",
                    },
                )

                if r3.returncode != 0:

                    update_logger.error(
                        "git reset --hard ошибка "
                        "(rc=%s): %s",
                        r3.returncode,
                        self._redact(
                            r3.stderr.strip()
                        ),
                    )

                    return False

                update_logger.info(
                    "git reset --hard FETCH_HEAD: %s",
                    r3.stdout.strip(),
                )


            return True


        except (
            subprocess.SubprocessError,
            FileNotFoundError,
        ) as e:

            update_logger.error(
                "git sync исключение: %s",
                e,
            )

            return False


    # ========================================================
    # Обновление базы
    # ========================================================

    def _update_database_from_git(self):
        """
        Обновляет ТОЛЬКО SQLite-базу из ветки db.

        Ветка db не checkout-ится.

        Никакие остальные файлы репозитория не изменяются.

        Последовательность:

            git fetch db
                ↓
            git show FETCH_HEAD:instance/servicedesk.db
                ↓
            временный файл
                ↓
            проверка SQLite
                ↓
            SQLite backup
                ↓
            существующая БД обновлена
        """

        temp_path = None

        source = None

        target = None

        try:

            # ------------------------------------------------
            # Получаем свежую ветку db
            # ------------------------------------------------

            r = subprocess.run(
                [
                    "git",
                    "fetch",
                    "--prune",
                    self._auth_url(),
                    DB_BRANCH,
                ],
                cwd=self.repo_dir,
                capture_output=True,
                timeout=120,
                env={
                    **os.environ,
                    "GIT_TERMINAL_PROMPT": "0",
                },
            )

            if r.returncode != 0:

                update_logger.error(
                    "git fetch базы ошибка (rc=%s):\n"
                    "STDOUT: %s\n"
                    "STDERR: %s",
                    r.returncode,
                    r.stdout.decode(
                        errors="replace"
                    ).strip(),
                    self._redact(
                        r.stderr.decode(
                            errors="replace"
                        ).strip()
                    ),
                )

                return False


            # ------------------------------------------------
            # Получаем SHA последнего коммита ветки db
            # ------------------------------------------------

            revision = subprocess.run(
                [
                    "git",
                    "rev-parse",
                    "FETCH_HEAD",
                ],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if revision.returncode != 0:

                update_logger.error(
                    "Не удалось определить ревизию базы: %s",
                    self._redact(
                        revision.stderr.strip()
                    ),
                )

                return False


            db_revision = (
                revision.stdout.strip()
            )

            if not db_revision:

                update_logger.error(
                    "git rev-parse FETCH_HEAD "
                    "вернул пустую ревизию базы."
                )

                return False


            # ------------------------------------------------
            # База уже применена
            # ------------------------------------------------

            if (
                db_revision
                == self.last_db_revision
            ):

                return False


            # ------------------------------------------------
            # Полный путь к локальной БД
            # ------------------------------------------------

            db_path = os.path.abspath(
                self.db_path
            )

            os.makedirs(
                os.path.dirname(db_path),
                exist_ok=True,
            )


            # ------------------------------------------------
            # Получаем только БД из ветки db
            #
            # Никакого checkout!
            # ------------------------------------------------

            blob = subprocess.run(
                [
                    "git",
                    "show",
                    f"FETCH_HEAD:{DB_REPO_PATH}",
                ],
                cwd=self.repo_dir,
                capture_output=True,
                timeout=120,
            )

            if blob.returncode != 0:

                update_logger.error(
                    "Не удалось получить базу %s "
                    "из ветки %s: %s",
                    DB_REPO_PATH,
                    DB_BRANCH,
                    self._redact(
                        blob.stderr.decode(
                            errors="replace"
                        ).strip()
                    ),
                )

                return False


            # ------------------------------------------------
            # Проверяем SQLite magic header
            # ------------------------------------------------

            if not blob.stdout.startswith(
                b"SQLite format 3\x00"
            ):

                update_logger.error(
                    "Полученный файл %s "
                    "не является SQLite-базой.",
                    DB_REPO_PATH,
                )

                return False


            # ------------------------------------------------
            # Создаём временную БД
            # ------------------------------------------------

            with tempfile.NamedTemporaryFile(
                prefix=".db_update_",
                suffix=".db",
                dir=os.path.dirname(db_path),
                delete=False,
            ) as tmp:

                tmp.write(
                    blob.stdout
                )

                temp_path = tmp.name


            # ------------------------------------------------
            # Открываем новую БД
            # ------------------------------------------------

            source = sqlite3.connect(
                temp_path,
                timeout=30,
            )


            # ------------------------------------------------
            # Проверяем целостность новой БД
            # ------------------------------------------------

            integrity = source.execute(
                "PRAGMA integrity_check"
            ).fetchone()

            if (
                not integrity
                or integrity[0] != "ok"
            ):

                raise sqlite3.DatabaseError(
                    "Проверка integrity_check "
                    f"не пройдена: {integrity}"
                )


            # ------------------------------------------------
            # Открываем текущую БД
            # ------------------------------------------------

            target = sqlite3.connect(
                db_path,
                timeout=30,
            )

            target.execute(
                "PRAGMA busy_timeout=30000"
            )


            # ------------------------------------------------
            # Копируем новую БД в существующую
            #
            # НЕ os.replace()
            #
            # Это важно для Windows, поскольку приложение
            # может уже держать SQLite-файл открытым.
            # ------------------------------------------------

            source.backup(
                target,
                pages=100,
                sleep=0.1,
            )

            target.commit()


            # ------------------------------------------------
            # Проверяем уже рабочую БД
            # ------------------------------------------------

            integrity = target.execute(
                "PRAGMA integrity_check"
            ).fetchone()

            if (
                not integrity
                or integrity[0] != "ok"
            ):

                raise sqlite3.DatabaseError(
                    "Проверка новой рабочей базы "
                    f"не пройдена: {integrity}"
                )


            # ------------------------------------------------
            # Всё успешно
            # ------------------------------------------------

            self.last_db_revision = (
                db_revision
            )

            update_logger.info(
                "База данных обновлена: "
                "%s@%s -> %s",
                self.repo,
                DB_BRANCH,
                db_path,
            )

            return True


        except (
            subprocess.SubprocessError,
            FileNotFoundError,
            sqlite3.Error,
            OSError,
        ) as e:

            update_logger.error(
                "Ошибка обновления базы данных: %s",
                e,
            )

            return False


        finally:

            if source is not None:

                source.close()


            if target is not None:

                target.close()


            if temp_path:

                try:

                    os.remove(
                        temp_path
                    )

                except OSError:

                    pass


    # ========================================================
    # Одна проверка
    # ========================================================

    def check_once(self):

        with self._lock:

            self.last_check = (
                datetime.now(timezone.utc)
            )


            # ------------------------------------------------
            # 1. Получаем main
            # ------------------------------------------------

            if not self._fetch():

                update_logger.warning(
                    "Не удалось получить "
                    "свежие данные с GitHub."
                )

                return False


            # ------------------------------------------------
            # 2. Проверяем код
            # ------------------------------------------------

            changed = (
                self._get_changed_files()
            )

            if changed is None:

                return False


            self.last_changed_files = (
                changed
            )


            # ------------------------------------------------
            # Если код изменился
            # ------------------------------------------------

            if changed:

                self.update_available = True

                update_logger.warning(
                    "Обнаружено расхождение. "
                    "Файлов: %d — %s",
                    len(changed),
                    ", ".join(changed),
                )


                # --------------------------------------------
                # AUTO_UPDATE=false
                # --------------------------------------------

                if not self.auto_update:

                    update_logger.info(
                        "AUTO_UPDATE=false — "
                        "синхронизация не выполняется."
                    )

                else:

                    # ----------------------------------------
                    # Обновляем только код из main
                    # ----------------------------------------

                    if self._sync_from_fetch_head():

                        still = (
                            self._get_changed_files()
                        )

                        if still:

                            update_logger.error(
                                "После синхронизации "
                                "остались расхождения: %s",
                                ", ".join(still),
                            )

                            return False


                        self.update_available = False

                        update_logger.info(
                            "Код синхронизирован "
                            "с GitHub (%s@%s).",
                            self.repo,
                            self.branch,
                        )

                    else:

                        update_logger.error(
                            "Не удалось синхронизировать "
                            "рабочее дерево с FETCH_HEAD."
                        )

                        return False

            else:

                self.update_available = False

                update_logger.info(
                    "Обновлений кода нет."
                )


            # ------------------------------------------------
            # 3. Отдельно обновляем БД
            #
            # Здесь FETCH_HEAD снова перезаписывается,
            # но это уже FETCH_HEAD ветки db.
            #
            # Мы используем его только через git show.
            # Рабочее дерево при этом НЕ изменяется.
            # ------------------------------------------------

            db_updated = False

            if self.auto_update:

                db_updated = (
                    self._update_database_from_git()
                )


            return bool(
                changed
                or db_updated
            )


    # ========================================================
    # Цикл проверки
    # ========================================================

    def _run(self):

        update_logger.info(
            "Сервис обновлений запущен: "
            "repo=%s branch=%s "
            "db_branch=%s "
            "interval=%ss "
            "auto=%s "
            "hard_reset=%s "
            "repo_dir=%s",
            self.repo,
            self.branch,
            DB_BRANCH,
            self.interval,
            self.auto_update,
            self.hard_reset,
            self.repo_dir,
        )


        while not self._stop_event.is_set():

            try:

                self.check_once()

            except Exception:

                update_logger.exception(
                    "Ошибка в цикле проверки"
                )


            self._stop_event.wait(
                self.interval
            )


        update_logger.info(
            "Сервис обновлений остановлен."
        )


    # ========================================================
    # Запуск
    # ========================================================

    def start(self):

        if (
            self._thread
            and self._thread.is_alive()
        ):

            return


        self._stop_event.clear()


        self._thread = threading.Thread(
            target=self._run,
            name="GitHubUpdateChecker",
            daemon=True,
        )


        self._thread.start()


    # ========================================================
    # Остановка
    # ========================================================

    def stop(self):

        self._stop_event.set()


        if self._thread:

            self._thread.join(
                timeout=5
            )


# ============================================================
# Готовый экземпляр
# ============================================================

update_checker = None


if GITHUB_REPO and GITHUB_TOKEN:

    update_checker = GitHubUpdateChecker(
        repo=GITHUB_REPO,
        branch=GITHUB_BRANCH,
        token=GITHUB_TOKEN,
        interval=CHECK_INTERVAL,
        auto_update=AUTO_UPDATE,
        hard_reset=HARD_RESET,
        repo_dir=REPO_DIR,
    )

else:

    update_logger.warning(
        "Сервис обновлений не запущен: "
        "задайте GITHUB_REPO и GITHUB_TOKEN."
    )