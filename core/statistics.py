from flask import Blueprint, render_template, request, flash, redirect, url_for, abort, send_file,jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_
from models import db, User, Ticket, Queue
from config import TIMEZONES
from datetime import datetime, timedelta

stat_bp = Blueprint("statistics",__name__)

@stat_bp.route('/statistics')
@login_required
def statistics():
    queues = Queue.query.all()
    users = User.query.all()
    return render_template('statistics.html', queues=queues, users=users, ticket = Ticket)

@stat_bp.route('/api/statistics/export')
@login_required
def export_statistics():
    import io
    import pandas as pd
    from datetime import datetime
    
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    queue_id = request.args.get('queue_id')
    status_filter = request.args.get('status', 'all')
    priority_filter = request.args.get('priority')
    
    query = Ticket.query
    
    if start_date:
        query = query.filter(Ticket.created_at >= datetime.strptime(start_date, '%Y-%m-%d'))
    if end_date:
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        query = query.filter(Ticket.created_at <= end_dt)
    if queue_id:
        query = query.filter(Ticket.queue_id == queue_id)
    if status_filter != 'all':
        if status_filter == 'active':
            query = query.filter(Ticket.status.in_(['Новая', 'В работе']))
        else:
            query = query.filter(Ticket.status == status_filter)
    if priority_filter:
        query = query.filter(Ticket.priority == priority_filter)
    
    tickets = query.all()
    
    data = []
    for ticket in tickets:
        assignees = ', '.join([u.username for u in ticket.assignees]) if ticket.assignees else '—'
        queue_name = ticket.queue.name if ticket.queue else '—'
        
        time_spent = ''
        if ticket.closed_at and ticket.created_at:
            delta = ticket.closed_at - ticket.created_at
            days = delta.days
            hours = delta.seconds // 3600
            minutes = (delta.seconds % 3600) // 60
            time_spent = f"{days}д {hours}ч {minutes}м"
        
        data.append({
            '№ заявки': ticket.ticket_number,
            'Название': ticket.title,
            'Статус': ticket.status,
            'Приоритет': ticket.priority,
            'Очередь': queue_name,
            'Ответственные': assignees,
            'Создал': ticket.creator.username if ticket.creator else '—',
            'Дата создания': ticket.created_at.strftime('%d.%m.%Y %H:%M') if ticket.created_at else '',
            'Дата закрытия': ticket.closed_at.strftime('%d.%m.%Y %H:%M') if ticket.closed_at else '',
            'Время выполнения': time_spent,
            'Оценка': ticket.rating if ticket.rating else ''
        })
    
    df = pd.DataFrame(data)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Заявки', index=False)
        
        worksheet = writer.sheets['Заявки']
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width
    
    output.seek(0)
    
    filename = f'statistics_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    return send_file(
        output,
        mimetype='stat_bplication/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename
    )

@stat_bp.route('/api/statistics/chart-data')
@login_required
def chart_data():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    queue_id = request.args.get('queue_id')
    status_filter = request.args.get('status', 'all')
    priority_filter = request.args.get('priority')

    query = Ticket.query
    if start_date:
        query = query.filter(Ticket.created_at >= datetime.strptime(start_date, '%Y-%m-%d'))
    if end_date:
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        query = query.filter(Ticket.created_at < end_dt)
    if queue_id:
        query = query.filter(Ticket.queue_id == queue_id)
    if status_filter == 'active':
        query = query.filter(Ticket.status.in_(['Новая', 'В работе']))
    elif status_filter != 'all':
        query = query.filter(Ticket.status == status_filter)
    if priority_filter:
        query = query.filter(Ticket.priority == priority_filter)

    tickets = query.all()
    status_data = {status: 0 for status in ['Новая', 'В работе', 'Закрыта']}
    priority_data = {priority: 0 for priority in ['Низкий', 'Средний', 'Высокий']}
    queue_data = {}
    queue_ids = {}
    for ticket in tickets:
        if ticket.status in status_data:
            status_data[ticket.status] += 1
        if ticket.priority in priority_data:
            priority_data[ticket.priority] += 1
        if ticket.queue:
            queue_data[ticket.queue.name] = queue_data.get(ticket.queue.name, 0) + 1
            queue_ids[ticket.queue.name] = ticket.queue_id

    today = datetime.now().date()
    if start_date or end_date:
        first_day = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else (
            datetime.strptime(end_date, '%Y-%m-%d').date() - timedelta(days=6)
        )
        last_day = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else today
        if (last_day - first_day).days > 30:
            first_day = last_day - timedelta(days=30)
    else:
        first_day = today - timedelta(days=6)
        last_day = today

    daily_data = {}
    for offset in range(max(0, (last_day - first_day).days) + 1):
        day = first_day + timedelta(days=offset)
        day_start = datetime.combine(day, datetime.min.time())
        day_end = day_start + timedelta(days=1)
        daily_data[day.strftime('%d.%m')] = query.filter(
            Ticket.created_at >= day_start,
            Ticket.created_at < day_end
        ).count()

    top_queues = dict(sorted(queue_data.items(), key=lambda item: item[1], reverse=True)[:5])
    top_queue_ids = {name: queue_ids[name] for name in top_queues}

    return jsonify({
        'status': status_data,
        'priority': priority_data,
        'queues': top_queues,
        'queue_ids': top_queue_ids,
        'daily': daily_data
    })
