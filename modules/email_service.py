import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask_app import app_core as app
from models import *
import threading
import time

# ============================================
# НАСТРОЙКИ ПОЧТЫ
# ============================================
MAIL_SERVER = 'smtp.mail.ru'
MAIL_PORT = 465
MAIL_USE_SSL = True
MAIL_USERNAME = 'pulse_sd@mail.ru'
MAIL_PASSWORD = 'zg6kOlWMtwTYQwSqM0Wx'
MAIL_DEFAULT_SENDER = 'pulse_sd@mail.ru'



msg_list = []

async def add_to_mail_list(to_email, subject, body, html_body=None):
    if not to_email:
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = MAIL_DEFAULT_SENDER
        msg['To'] = to_email

        part1 = MIMEText(body, 'plain')
        part2 = MIMEText(html_body or body.replace('\n', '<br>'), 'html')
        msg.attach(part1)
        msg.attach(part2)
        msg_list.append(msg_list)
        
        return True
    except Exception as e:
        print(f'❌ Email error: {e}')
        return False

async def send_email_notification():
    if len(msg_list)>0:
        server = smtplib.SMTP_SSL(MAIL_SERVER, MAIL_PORT)
        server.login(MAIL_USERNAME, MAIL_PASSWORD)
        for msg in msg_list:
            server.send_message(msg)
            print(f'✅ Email отправлен на {msg['To']}')
            msg_list.remove(msg)
        server.close()
    threading.Timer(30, send_email_notification).start()

async def send_new_ticket_notification(ticket, queue):
    print(1)
    """Уведомление о новой заявке"""
    subject = f'🔔 Новая заявка #{ticket.ticket_number}: {ticket.title}'
    
    # Обрезаем описание если оно слишком длинное
    description = ticket.description
    if len(description) > 500:
        description = description[:500] + '...'
    
    body = f'''Создана новая заявка!

Номер: {ticket.ticket_number}
Название: {ticket.title}
Приоритет: {ticket.priority}
Дедлайн: {ticket.deadline.strftime('%d.%m.%Y %H:%M') if ticket.deadline else 'Не установлен'}
Создал: {User.query.get(ticket.created_by_id).full_name}
Очередь: {Queue.query.get(queue).name or 'Без очереди'}

Описание:
{description}

Ссылка: http://127.0.0.1:5000/ticket/{ticket.id}'''
    if Queue.query.get(queue).mail is None:
        users_q = Queue.query.get(queue).members
        for u in users_q:
            await add_to_mail_list(u.email, subject, body)
    else:
        await add_to_mail_list(Queue.query.get(queue).mail, subject, body)


def send_ticket_created_notification(ticket, user):
    """Уведомление создателю заявки"""
    subject = f'✅ Заявка {ticket.ticket_number} создана'
    body = f'''Ваша заявка успешно создана!

Номер: {ticket.ticket_number}
Название: {ticket.title}
Статус: {ticket.status}
Приоритет: {ticket.priority}
Очередь: {ticket.queue.name if ticket.queue else 'Без очереди'}
Дедлайн: {ticket.deadline.strftime('%d.%m.%Y %H:%M') if ticket.deadline else 'Не установлен'}

Ссылка: http://127.0.0.1:5000/ticket/{ticket.id}'''
    return add_to_mail_list(user.email, subject, body)


async def send_assigned_notification(ticket, assignee):
    """Уведомление о назначении ответственным"""
    subject = f'📋 Вам назначена заявка {ticket.ticket_number}'
    body = f'''Вам назначена заявка!

Номер: {ticket.ticket_number}
Название: {ticket.title}
Приоритет: {ticket.priority}
Дедлайн: {ticket.deadline.strftime('%d.%m.%Y %H:%M') if ticket.deadline else 'Не установлен'}

Ссылка: http://127.0.0.1:5000/ticket/{ticket.id}'''
    print(len(assignee))
    for ass in assignee:
        await add_to_mail_list(ass.email, subject, body)


def send_transfer_notification(ticket, new_assignee, transferer):
    """Уведомление о передаче заявки"""
    subject = f'📋 Вам передана заявка {ticket.ticket_number}'
    body = f'''Вам передана заявка!

Номер: {ticket.ticket_number}
Название: {ticket.title}
Приоритет: {ticket.priority}
Дедлайн: {ticket.deadline.strftime('%d.%m.%Y %H:%M') if ticket.deadline else 'Не установлен'}
Передал: {transferer.full_name or transferer.username}

Ссылка: http://127.0.0.1:5000/ticket/{ticket.id}'''
    return add_to_mail_list(new_assignee.email, subject, body)


def send_status_change_notification(ticket, user, old_status, new_status):
    """Уведомление об изменении статуса заявки"""
    subject = f'📌 Статус заявки {ticket.ticket_number} изменен'
    body = f'''Статус заявки "{ticket.title}" изменен.

Номер: {ticket.ticket_number}
Название: {ticket.title}
Старый статус: {old_status}
Новый статус: {new_status}
Обновил: {user.full_name or user.username}

Ссылка: http://127.0.0.1:5000/ticket/{ticket.id}'''
    return add_to_mail_list(user.email, subject, body)


def send_booking_notification(car, user, start, end, purpose):
    """Уведомление о бронировании автомобиля"""
    subject = f'🚗 Автомобиль {car.full_name} забронирован'
    body = f'''Вы успешно забронировали автомобиль!

Автомобиль: {car.brand} {car.model} ({car.license_plate})
Дата начала: {start.strftime('%d.%m.%Y %H:%M')}
Дата окончания: {end.strftime('%d.%m.%Y %H:%M')}
Цель: {purpose or 'Не указана'}
Очередь: {car.queue.name}

Ссылка: http://127.0.0.1:5000/queue/{car.queue_id}/cars'''
    return add_to_mail_list(user.email, subject, body)

app.add_callback("after_create_ticket",send_new_ticket_notification)
app.add_callback("after_assign_ticket",send_assigned_notification)



send_email_notification()