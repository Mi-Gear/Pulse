import sqlite3
import sys
import pymysql
import re


# ============================================================
# НАСТРОЙКИ MARIADB
# ============================================================

DB_HOST = "188.113.183.125"
DB_PORT = 6033
DB_USER = "root"
DB_PASSWORD = "root"
DB_NAME = "pulse"


# ============================================================
# SQLITE
# ============================================================

if len(sys.argv) < 2:
    print("Использование:")
    print("python migrate.py servicedesk.db")
    sys.exit(1)

sqlite_file = sys.argv[1]


# ============================================================
# ТИПЫ SQLITE → MARIADB
# ============================================================

def convert_type(sqlite_type):
    """
    Преобразование типов SQLite в MariaDB.
    """

    t = (sqlite_type or "").upper().strip()

    # INTEGER
    if "INT" in t:
        return "BIGINT"

    # BLOB
    if "BLOB" in t:
        return "LONGBLOB"

    # TEXT
    if any(x in t for x in ("CHAR", "CLOB", "TEXT")):
        return "LONGTEXT"

    # FLOAT
    if any(x in t for x in ("REAL", "FLOA", "DOUB")):
        return "DOUBLE"

    # DECIMAL / NUMERIC
    if any(x in t for x in ("DECIMAL", "NUMERIC")):
        return "DECIMAL(20,6)"

    # BOOLEAN
    if "BOOL" in t:
        return "TINYINT(1)"

    # DATE / TIME
    if "DATE" in t or "TIME" in t:
        return "DATETIME"

    # Если тип неизвестен
    return "LONGTEXT"


# ============================================================
# ЭКРАНИРОВАНИЕ ИМЁН
# ============================================================

def quote_identifier(name):
    return "`" + str(name).replace("`", "``") + "`"


# ============================================================
# ПРОВЕРКА AUTOINCREMENT
# ============================================================

def table_has_autoincrement(sqlite_cursor, table):
    """
    SQLite хранит AUTOINCREMENT в оригинальном SQL таблицы.
    """

    sqlite_cursor.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table,)
    )

    row = sqlite_cursor.fetchone()

    if not row or not row["sql"]:
        return False

    return bool(
        re.search(
            r"\bAUTOINCREMENT\b",
            row["sql"],
            re.IGNORECASE
        )
    )


# ============================================================
# DEFAULT
# ============================================================

def convert_default(default):
    """
    Перенос DEFAULT из SQLite в MariaDB.

    SQLite часто хранит DEFAULT уже в SQL-виде:
        'text'
        0
        CURRENT_TIMESTAMP
        (datetime('now'))
    """

    if default is None:
        return None

    default = str(default).strip()

    if not default:
        return None

    # SQLite datetime('now') и подобные выражения
    # MariaDB понимает CURRENT_TIMESTAMP,
    # поэтому преобразуем распространённый вариант.
    lower = default.lower()

    if lower in (
        "current_timestamp",
        "current_date",
        "current_time",
    ):
        return default.upper()

    if "datetime('now')" in lower:
        return "CURRENT_TIMESTAMP"

    if 'datetime("now")' in lower:
        return "CURRENT_TIMESTAMP"

    # CURRENT_TIMESTAMP(...)
    if lower.startswith("current_timestamp"):
        return default.upper()

    # Числа
    if re.fullmatch(r"-?\d+(\.\d+)?", default):
        return default

    # NULL
    if default.upper() == "NULL":
        return "NULL"

    # TRUE / FALSE
    if default.upper() in ("TRUE", "FALSE"):
        return default.upper()

    # Строки и SQL-выражения
    return default


# ============================================================
# ПОЛУЧИТЬ ТАБЛИЦЫ
# ============================================================

def get_tables(sqlite_cursor):

    sqlite_cursor.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    )

    return [
        row["name"]
        for row in sqlite_cursor.fetchall()
    ]


# ============================================================
# ИНДЕКСЫ
# ============================================================

def get_indexes(sqlite_cursor, table):

    sqlite_cursor.execute(
        f"PRAGMA index_list({quote_identifier(table)})"
    )

    indexes = []

    for index in sqlite_cursor.fetchall():

        index_name = index["name"]

        # SQLite автоматически создаёт индекс для PRIMARY KEY / UNIQUE.
        # Его отдельно переносить не нужно.
        origin = index["origin"]

        if origin == "pk":
            continue

        indexes.append({
            "name": index_name,
            "unique": bool(index["unique"]),
        })

    return indexes


def get_index_columns(sqlite_cursor, index_name):

    sqlite_cursor.execute(
        f"PRAGMA index_info({quote_identifier(index_name)})"
    )

    rows = sqlite_cursor.fetchall()

    return [
        row["name"]
        for row in rows
        if row["name"] is not None
    ]


# ============================================================
# FOREIGN KEYS
# ============================================================

def get_foreign_keys(sqlite_cursor, table):

    sqlite_cursor.execute(
        f"PRAGMA foreign_key_list({quote_identifier(table)})"
    )

    rows = sqlite_cursor.fetchall()

    # SQLite может вернуть несколько строк для составного FK.
    foreign_keys = {}

    for row in rows:

        fk_id = row["id"]

        if fk_id not in foreign_keys:
            foreign_keys[fk_id] = {
                "table": row["table"],
                "on_delete": row["on_delete"],
                "on_update": row["on_update"],
                "columns": [],
                "ref_columns": [],
            }

        foreign_keys[fk_id]["columns"].append(row["from"])
        foreign_keys[fk_id]["ref_columns"].append(row["to"])

    return list(foreign_keys.values())


# ============================================================
# СОЗДАНИЕ ТАБЛИЦЫ
# ============================================================

def create_table(
    sqlite_cursor,
    maria_cursor,
    table
):

    print()
    print("=" * 70)
    print(f"Таблица: {table}")

    # --------------------------------------------------------
    # Структура
    # --------------------------------------------------------

    sqlite_cursor.execute(
        f"PRAGMA table_info({quote_identifier(table)})"
    )

    columns = sqlite_cursor.fetchall()

    if not columns:
        print("  Пустая структура, пропуск.")
        return

    # --------------------------------------------------------
    # AUTOINCREMENT
    # --------------------------------------------------------

    has_autoincrement = table_has_autoincrement(
        sqlite_cursor,
        table
    )

    # --------------------------------------------------------
    # PRIMARY KEY
    # --------------------------------------------------------

    primary_keys = []

    for column in columns:

        if column["pk"]:
            primary_keys.append(
                (column["pk"], column["name"])
            )

    primary_keys.sort(
        key=lambda x: x[0]
    )

    primary_keys = [
        name
        for _, name in primary_keys
    ]

    # --------------------------------------------------------
    # COLUMNS
    # --------------------------------------------------------

    definitions = []

    for column in columns:

        name = column["name"]
        sqlite_type = column["type"]
        not_null = bool(column["notnull"])
        default = column["dflt_value"]
        is_primary_key = bool(column["pk"])

        mariadb_type = convert_type(sqlite_type)

        definition = (
            f"{quote_identifier(name)} "
            f"{mariadb_type}"
        )

        # ----------------------------------------------------
        # PRIMARY KEY AUTOINCREMENT
        # ----------------------------------------------------

        if (
            has_autoincrement
            and len(primary_keys) == 1
            and is_primary_key
            and "INT" in sqlite_type.upper()
        ):
            definition += " AUTO_INCREMENT"

        # ----------------------------------------------------
        # NOT NULL
        # ----------------------------------------------------

        if not_null:
            definition += " NOT NULL"

        # ----------------------------------------------------
        # DEFAULT
        # ----------------------------------------------------

        converted_default = convert_default(default)

        if converted_default is not None:

            # Для AUTO_INCREMENT DEFAULT не нужен
            if not (
                has_autoincrement
                and is_primary_key
            ):
                definition += (
                    f" DEFAULT {converted_default}"
                )

        definitions.append(definition)

    # --------------------------------------------------------
    # PRIMARY KEY
    # --------------------------------------------------------

    if primary_keys:

        pk = ", ".join(
            quote_identifier(x)
            for x in primary_keys
        )

        definitions.append(
            f"PRIMARY KEY ({pk})"
        )

    # --------------------------------------------------------
    # UNIQUE
    # --------------------------------------------------------

    indexes = get_indexes(
        sqlite_cursor,
        table
    )

    for index in indexes:

        if not index["unique"]:
            continue

        index_columns = get_index_columns(
            sqlite_cursor,
            index["name"]
        )

        if not index_columns:
            continue

        columns_sql = ", ".join(
            quote_identifier(x)
            for x in index_columns
        )

        definitions.append(
            f"UNIQUE KEY "
            f"{quote_identifier(index['name'])} "
            f"({columns_sql})"
        )

    # --------------------------------------------------------
    # CREATE TABLE
    # --------------------------------------------------------

    create_sql = f"""
        CREATE TABLE IF NOT EXISTS
        {quote_identifier(table)}
        (
            {", ".join(definitions)}
        )
        ENGINE=InnoDB
        DEFAULT CHARACTER SET utf8mb4
        COLLATE=utf8mb4_unicode_ci
    """

    print("  Создание структуры...")

    maria_cursor.execute(create_sql)

    print("  ✓ Структура создана")

    # --------------------------------------------------------
    # ОБЫЧНЫЕ ИНДЕКСЫ
    # --------------------------------------------------------

    for index in indexes:

        if index["unique"]:
            continue

        index_columns = get_index_columns(
            sqlite_cursor,
            index["name"]
        )

        if not index_columns:
            continue

        columns_sql = ", ".join(
            quote_identifier(x)
            for x in index_columns
        )

        try:

            maria_cursor.execute(
                f"""
                CREATE INDEX
                {quote_identifier(index['name'])}
                ON {quote_identifier(table)}
                ({columns_sql})
                """
            )

        except pymysql.err.OperationalError as e:

            # Если индекс уже существует
            if e.args and e.args[0] == 1061:
                pass
            else:
                raise


# ============================================================
# ДОБАВЛЕНИЕ FOREIGN KEY
# ============================================================

def add_foreign_keys(
    sqlite_cursor,
    maria_cursor,
    table
):

    foreign_keys = get_foreign_keys(
        sqlite_cursor,
        table
    )

    if not foreign_keys:
        return

    print(
        f"  Внешних ключей: "
        f"{len(foreign_keys)}"
    )

    for number, fk in enumerate(
        foreign_keys,
        start=1
    ):

        columns_sql = ", ".join(
            quote_identifier(x)
            for x in fk["columns"]
        )

        ref_columns_sql = ", ".join(
            quote_identifier(x)
            for x in fk["ref_columns"]
        )

        # Уникальное имя ограничения
        constraint_name = (
            f"fk_{table}_{number}"
        )

        sql = f"""
            ALTER TABLE {quote_identifier(table)}
            ADD CONSTRAINT
            {quote_identifier(constraint_name)}
            FOREIGN KEY ({columns_sql})
            REFERENCES {quote_identifier(fk['table'])}
            ({ref_columns_sql})
        """

        on_delete = fk["on_delete"]
        on_update = fk["on_update"]

        if on_delete and on_delete.upper() != "NO ACTION":
            sql += (
                f" ON DELETE "
                f"{on_delete.upper()}"
            )

        if on_update and on_update.upper() != "NO ACTION":
            sql += (
                f" ON UPDATE "
                f"{on_update.upper()}"
            )

        try:

            maria_cursor.execute(sql)

            print(
                f"    ✓ {table} → "
                f"{fk['table']}"
            )

        except pymysql.err.OperationalError as e:

            # Если FK уже существует
            # или структура уже была создана
            if e.args and e.args[0] in (
                1005,
                1022,
            ):
                print(
                    f"    ! FK уже существует "
                    f"или конфликтует: "
                    f"{fk['table']}"
                )
            else:
                raise


# ============================================================
# ПЕРЕНОС ДАННЫХ
# ============================================================

def migrate_data(
    sqlite_cursor,
    maria_cursor,
    maria_conn,
    table
):

    sqlite_cursor.execute(
        f"PRAGMA table_info("
        f"{quote_identifier(table)})"
    )

    columns = sqlite_cursor.fetchall()

    if not columns:
        return

    sqlite_cursor.execute(
        f"SELECT * FROM "
        f"{quote_identifier(table)}"
    )

    rows = sqlite_cursor.fetchall()

    print(
        f"  Записей в SQLite: "
        f"{len(rows)}"
    )

    if not rows:
        print("  Данных нет.")
        return

    column_names = [
        column["name"]
        for column in columns
    ]

    columns_sql = ", ".join(
        quote_identifier(name)
        for name in column_names
    )

    placeholders = ", ".join(
        ["%s"] * len(column_names)
    )

    insert_sql = f"""
        INSERT INTO {quote_identifier(table)}
        ({columns_sql})
        VALUES ({placeholders})
    """

    data = []

    for row in rows:

        values = []

        for value in row:

            if isinstance(value, bytes):
                values.append(value)
            else:
                values.append(value)

        data.append(values)

    print("  Перенос данных...")

    try:

        maria_cursor.executemany(
            insert_sql,
            data
        )

        maria_conn.commit()

        print(
            f"  ✓ Перенесено: "
            f"{len(data)}"
        )

    except Exception as e:

        maria_conn.rollback()

        print(
            f"  ✗ Ошибка: {e}"
        )

        raise


# ============================================================
# КОРРЕКТИРОВКА AUTO_INCREMENT
# ============================================================

def fix_auto_increment(
    sqlite_cursor,
    maria_cursor,
    table
):

    if not table_has_autoincrement(
        sqlite_cursor,
        table
    ):
        return

    sqlite_cursor.execute(
        f"PRAGMA table_info("
        f"{quote_identifier(table)})"
    )

    columns = sqlite_cursor.fetchall()

    primary_keys = [
        column["name"]
        for column in columns
        if column["pk"]
    ]

    if len(primary_keys) != 1:
        return

    pk = primary_keys[0]

    sqlite_cursor.execute(
        f"""
        SELECT MAX(
            {quote_identifier(pk)}
        )
        FROM {quote_identifier(table)}
        """
    )

    row = sqlite_cursor.fetchone()

    max_id = row[0]

    if max_id is None:
        return

    next_id = int(max_id) + 1

    maria_cursor.execute(
        f"""
        ALTER TABLE
        {quote_identifier(table)}
        AUTO_INCREMENT = {next_id}
        """
    )

    print(
        f"  ✓ AUTO_INCREMENT = "
        f"{next_id}"
    )


# ============================================================
# ПРОВЕРКА КОЛИЧЕСТВА ЗАПИСЕЙ
# ============================================================

def verify_table(
    sqlite_cursor,
    maria_cursor,
    table
):

    sqlite_cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM {quote_identifier(table)}
        """
    )

    sqlite_count = sqlite_cursor.fetchone()[0]

    maria_cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM {quote_identifier(table)}
        """
    )

    maria_count = maria_cursor.fetchone()[0]

    if sqlite_count == maria_count:

        print(
            f"  ✓ Проверка: "
            f"{maria_count} записей"
        )

    else:

        print(
            f"  ! РАЗНОЕ КОЛИЧЕСТВО: "
            f"SQLite={sqlite_count}, "
            f"MariaDB={maria_count}"
        )


# ============================================================
# ПОДКЛЮЧЕНИЕ
# ============================================================

print("Подключение к SQLite...")

sqlite_conn = sqlite3.connect(
    sqlite_file
)

sqlite_conn.row_factory = sqlite3.Row

sqlite_cursor = sqlite_conn.cursor()

# Включаем проверку FK SQLite
sqlite_cursor.execute(
    "PRAGMA foreign_keys = ON"
)


print("Подключение к MariaDB...")

maria_conn = pymysql.connect(
    host=DB_HOST,
    port=DB_PORT,
    user=DB_USER,
    password=DB_PASSWORD,
    charset="utf8mb4",
    autocommit=False,
)

maria_cursor = maria_conn.cursor()


# ============================================================
# СОЗДАНИЕ БАЗЫ
# ============================================================

print(
    f"Создание базы данных "
    f"`{DB_NAME}`..."
)

maria_cursor.execute(
    f"""
    CREATE DATABASE IF NOT EXISTS
    {quote_identifier(DB_NAME)}
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci
    """
)

maria_cursor.execute(
    f"USE {quote_identifier(DB_NAME)}"
)


# ============================================================
# ОТКЛЮЧАЕМ FK НА ВРЕМЯ МИГРАЦИИ
# ============================================================

maria_cursor.execute(
    "SET FOREIGN_KEY_CHECKS = 0"
)


# ============================================================
# ПОЛУЧАЕМ ТАБЛИЦЫ
# ============================================================

tables = get_tables(
    sqlite_cursor
)

print()
print(
    f"Найдено таблиц: "
    f"{len(tables)}"
)
print()


# ============================================================
# ЭТАП 1 — СОЗДАНИЕ ВСЕХ ТАБЛИЦ
# ============================================================

print()
print("=" * 70)
print("ЭТАП 1: СОЗДАНИЕ СТРУКТУРЫ")
print("=" * 70)

for table in tables:

    create_table(
        sqlite_cursor,
        maria_cursor,
        table
    )

maria_conn.commit()


# ============================================================
# ЭТАП 2 — ПЕРЕНОС ДАННЫХ
# ============================================================

print()
print("=" * 70)
print("ЭТАП 2: ПЕРЕНОС ДАННЫХ")
print("=" * 70)

for table in tables:

    migrate_data(
        sqlite_cursor,
        maria_cursor,
        maria_conn,
        table
    )


# ============================================================
# ЭТАП 3 — AUTO_INCREMENT
# ============================================================

print()
print("=" * 70)
print("ЭТАП 3: AUTO_INCREMENT")
print("=" * 70)

for table in tables:

    fix_auto_increment(
        sqlite_cursor,
        maria_cursor,
        table
    )

maria_conn.commit()


# ============================================================
# ЭТАП 4 — FOREIGN KEY
# ============================================================

print()
print("=" * 70)
print("ЭТАП 4: FOREIGN KEY")
print("=" * 70)

for table in tables:

    add_foreign_keys(
        sqlite_cursor,
        maria_cursor,
        table
    )

maria_conn.commit()


# ============================================================
# ВКЛЮЧАЕМ FOREIGN KEY
# ============================================================

maria_cursor.execute(
    "SET FOREIGN_KEY_CHECKS = 1"
)

maria_conn.commit()


# ============================================================
# ЭТАП 5 — ПРОВЕРКА
# ============================================================

print()
print("=" * 70)
print("ЭТАП 5: ПРОВЕРКА")
print("=" * 70)

for table in tables:

    verify_table(
        sqlite_cursor,
        maria_cursor,
        table
    )


# ============================================================
# ЗАВЕРШЕНИЕ
# ============================================================

print()
print("=" * 70)
print("МИГРАЦИЯ ЗАВЕРШЕНА")
print("=" * 70)


sqlite_cursor.close()
sqlite_conn.close()

maria_cursor.close()
maria_conn.close()