import pandas as pd
import sqlite3


EXCEL_FILE = "equipment.xlsx"
DB_FILE = "instance/servicedesk.db"


# Читаем Excel без заголовка
df = pd.read_excel(EXCEL_FILE, header=None)

# В твоём файле данные начинаются с 5-й строки
df = df.iloc[4:]


def clean(value):
    """Приводит значение Excel к нормальному виду."""
    if pd.isna(value):
        return None

    if isinstance(value, float) and value.is_integer():
        return str(int(value))

    value = str(value).strip()

    return value if value else None


# Создаём подключение к SQLite
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()


# Excel:
#
# 0  - номер
# 1  - наименование оборудования
# 2  - тип / марка
# 3  - заводской номер
# 4  - инвентарный номер
# 5  - НКМИ
# 6  - год выпуска
# 7  - год ввода в эксплуатацию
# 8  - период поверки
# 9  - период обслуживания
# 10 - РУ
# 11 - ...
# 12 - ...


insert_sql = """
INSERT INTO equipment (
    equipment_name,
    model,
    serial_number,
    inventory_number,
    production_year,
    commissioning_year,
    certificate,
    registration_certificate,
    maintenance_period,
    queue_id
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


count = 0

for _, row in df.iterrows():

    equipment_name = clean(row.iloc[1])

    # Пустые строки пропускаем
    if not equipment_name:
        continue

    model = clean(row.iloc[2])
    serial_number = clean(row.iloc[3])
    inventory_number = clean(row.iloc[4])

    certificate = clean(row.iloc[5])

    production_year = clean(row.iloc[6])
    commissioning_year = clean(row.iloc[7])

    # row[8] — период поверки
    # row[9] — период обслуживания
    maintenance_period = clean(row.iloc[9])

    registration_certificate = clean(row.iloc[10])

    cursor.execute(
        insert_sql,
        (
            equipment_name,
            model,
            serial_number,
            inventory_number,
            production_year,
            commissioning_year,
            certificate,
            registration_certificate,
            maintenance_period,
            8
        )
    )

    count += 1


conn.commit()
conn.close()

print(f"Готово. Добавлено записей: {count}")