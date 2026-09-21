import pytz
from datetime import datetime

# ============================================
# ГЛОБАЛЬНАЯ НАСТРОЙКА ЧАСОВОГО ПОЯСА СЕРВЕРА
# ============================================

# Часовой пояс сервера - меняется ТОЛЬКО здесь в коде
SERVER_TIMEZONE = 'Asia/Magadan'  # Можно менять на любой другой
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx', 'zip', 'rar', '7z'}
# Список доступных поясов (только для справки, не для изменения)
TIMEZONES = [
    ('UTC', 'UTC'),
    ('Europe/Kaliningrad', 'Калининград (UTC+2)'),
    ('Europe/Moscow', 'Москва (UTC+3)'),
    ('Europe/Volgograd', 'Волгоград (UTC+3)'),
    ('Europe/Samara', 'Самара (UTC+4)'),
    ('Asia/Yekaterinburg', 'Екатеринбург (UTC+5)'),
    ('Asia/Omsk', 'Омск (UTC+6)'),
    ('Asia/Novosibirsk', 'Новосибирск (UTC+7)'),
    ('Asia/Krasnoyarsk', 'Красноярск (UTC+7)'),
    ('Asia/Irkutsk', 'Иркутск (UTC+8)'),
    ('Asia/Yakutsk', 'Якутск (UTC+9)'),
    ('Asia/Vladivostok', 'Владивосток (UTC+10)'),
    ('Asia/Magadan', 'Магадан (UTC+11)'),
    ('Asia/Kamchatka', 'Камчатка (UTC+12)'),
]


def get_server_timezone():
    """Получить объект часового пояса сервера"""
    try:
        return pytz.timezone(SERVER_TIMEZONE)
    except:
        return pytz.timezone('Europe/Moscow')


def get_current_server_time():
    """Получить текущее время на сервере с учетом часового пояса"""
    tz = get_server_timezone()
    return datetime.now(tz)


def format_datetime(dt, format='%d.%m.%Y %H:%M'):
    """Форматировать дату с учетом часового пояса сервера"""
    if dt is None:
        return '—'
    if dt.tzinfo is None:
        tz = get_server_timezone()
        dt = pytz.UTC.localize(dt).astimezone(tz)
    else:
        tz = get_server_timezone()
        dt = dt.astimezone(tz)
    return dt.strftime(format)