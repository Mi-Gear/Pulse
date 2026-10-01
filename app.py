from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort, send_from_directory, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from models import db, User, Ticket, Comment, Queue, Attachment, Car, Booking
from datetime import datetime, timedelta
from sqlalchemy import or_
import traceback, json
import requests

from updater import update_checker


from core.utils import can_manage_queue
from flask_app import app_core as app
from core.login import login_bp
from core.my_queues import my_q_bp
from core.user_mgmt import user_mgmt_bp
from core.statistics import stat_bp
from core.queue_mgmt import queue_mgmt_bp
from core.queue_detail import q_detail_bp
from core.tickets import tickets_bp
from core.api import api_bp


from config import (
    SERVER_TIMEZONE,
    TIMEZONES,
    get_server_timezone,
    get_current_server_time,
    format_datetime
)
import sqlite3
import os
import uuid
import io
import pandas as pd
import pytz

from modules import *


app.config['SECRET_KEY'] = 'super-secret-key-change-this'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///servicedesk.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app.app)
login_manager = LoginManager()
login_manager.init_app(app.app)
login_manager.login_view = 'login.login'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.context_processor
def inject_globals():
    return {
        'User': User,
        'can_manage_queue': can_manage_queue,
        'format_datetime': format_datetime,
        'get_current_server_time': get_current_server_time,
        'SERVER_TIMEZONE': SERVER_TIMEZONE
    }
    
@app.app.errorhandler(400)
def handle_bad_request(e):
    if request.path.startswith('/api/'):
        return jsonify({
            'error': 'Bad request',
            'status': 400
        }), 400

    flash("Некорректный запрос.", "danger")
    return redirect(url_for('index'))


@app.app.errorhandler(404)
def handle_not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({
            'error': 'Not found',
            'status': 404
        }), 404
    flash("Страница не найдена.", "danger")
    return redirect(url_for('index'))
@app.app.errorhandler(403)
def handle_bad_request(e):
    flash("У вас нет прав на выполнение этой операции", "danger")
    return redirect(request.path)

@app.app.template_filter('from_json')
def from_json(value):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []






# === Главная ===
@app.route('/')
@login_required
def index():
    queue_ids = [q.id for q in current_user.queues]
    if 10 in queue_ids:
        queue_tickets_count = Ticket.query.filter(Ticket.queue_id.in_(queue_ids)).count()
        new_tickets_count = Ticket.query.filter(Ticket.queue_id.in_(queue_ids)).filter_by(status="Новая").count()
        work_tickets_count = Ticket.query.filter(Ticket.queue_id.in_(queue_ids)).filter_by(status="В работе").count()
        closed_tickets_count = Ticket.query.filter(Ticket.queue_id.in_(queue_ids)).filter_by(status="Закрыта").count()
    else:
        queue_tickets_count = Ticket.query.filter_by(created_by_id=current_user.id).count()
        new_tickets_count = Ticket.query.filter_by(created_by_id=current_user.id).filter_by(status="Новая").count()
        work_tickets_count = Ticket.query.filter_by(created_by_id=current_user.id).filter_by(status="В работе").count()
        closed_tickets_count = Ticket.query.filter_by(created_by_id=current_user.id).filter_by(status="Закрыта").count()
    return render_template('index.html', queue_tickets_count=queue_tickets_count, new_tickets_count=new_tickets_count, work_tickets_count=work_tickets_count,closed_tickets_count=closed_tickets_count, Ticket=Ticket)



# === Список пользователей ===
@app.route('/users')
@login_required
def users_list():
    query = User.query
    
    search = request.args.get('search', '').strip()
    department = request.args.get('department', '').strip()
    position = request.args.get('position', '').strip()
    office = request.args.get('office', '').strip()
    
    if search:
        query = query.filter(
            or_(
                User.username.ilike(f'%{search}%'),
                User.full_name.ilike(f'%{search}%')
            )
        )
    if department:
        query = query.filter(User.department == department)
    if position:
        query = query.filter(User.position == position)
    if office:
        query = query.filter(User.office == office)
    
    users = query.order_by(User.full_name).all()
    
    departments = sorted(set(u.department for u in User.query.all() if u.department))
    positions = sorted(set(u.position for u in User.query.all() if u.position))
    offices = sorted(set(u.office for u in User.query.all() if u.office))
    
    return render_template('users_list.html', 
                         users=users, 
                         departments=departments,
                         positions=positions,
                         offices=offices)

# === Карточка пользователя ===
@app.route('/user/<int:user_id>')
@login_required
def user_profile(user_id):
    user = User.query.get_or_404(user_id)
    return render_template('user_profile.html', user=user)



# === Профиль ===
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        full_name = request.form.get('full_name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        department = request.form.get('department')
        position = request.form.get('position')
        office = request.form.get('office')
        timezone = request.form.get('timezone')
        notify_email = request.form.get('notify_email') == 'on'
        
        if email:
            existing = User.query.filter(User.email == email, User.id != current_user.id).first()
            if existing:
                flash('Email уже используется', 'danger')
                return render_template('profile.html')
        
        current_user.full_name = full_name
        current_user.email = email
        current_user.phone = phone
        current_user.department = department
        current_user.position = position
        current_user.office = office
        current_user.notify_email = notify_email
        
        if timezone:
            try:
                pytz.timezone(timezone)
                current_user.timezone = timezone
            except:
                flash('Некорректный часовой пояс', 'danger')
        
        db.session.commit()
        flash('Профиль обновлен!', 'success')
    
    return render_template('profile.html', timezones=TIMEZONES)


# === Запуск ===
if __name__ == '__main__':
    with app.app_context():
        db.create_all()

        app.register_blueprints([
            login_bp,
            my_q_bp,
            user_mgmt_bp,
            stat_bp,
            queue_mgmt_bp,
            q_detail_bp,
            tickets_bp,
            api_bp
        ])

        if User.query.count() == 0:
            admin = User(
                username='admin',
                email='admin@servicedesk.local',
                password=generate_password_hash('Qq123456'),
                full_name='Администратор',
                is_admin=True,
                notify_email=True
            )

            db.session.add(admin)
            db.session.commit()

            admin_user = User.query.get(1)

            for queue in Queue.query.all():
                if admin_user:
                    queue.admins.append(admin_user)

            db.session.commit()

            print('✅ Созданы тестовые очереди')

        # Запускаем автоматическое обновление
        if update_checker:
            update_checker.start()
            print('🔄 Автообновление запущено')

    app.run(
        debug=True,
        host='0.0.0.0',
        port=80,
        use_reloader=False
    )
    