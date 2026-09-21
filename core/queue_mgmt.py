from flask import Blueprint, render_template, request, flash, redirect, url_for, abort
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash
from sqlalchemy import or_
from models import db, User, Ticket, Queue 
from config import TIMEZONES

queue_mgmt_bp = Blueprint('queue_mgmt', __name__)

@queue_mgmt_bp.route('/admin/queue/create', methods=['GET', 'POST'])
@login_required
def admin_queue_create():
    if not current_user.is_admin:
        abort(403)
    
    if request.method == 'POST':        
        name = request.form.get('name')
        description = request.form.get('description')
        queue_type = request.form.get('queue_type', 'default')
        admin_ids = request.form.getlist('admin_ids')
        
        if Queue.query.filter_by(name=name).first():
            flash('Очередь с таким названием уже существует!', 'danger')
            return render_template('admin_queue_form.html', users=User.query.all())
        
        queue = Queue(
            name=name,
            description=description,
            queue_type=queue_type,
            created_by_id=current_user.id
        )
        db.session.add(queue)
        db.session.commit()
        
        for admin_id in admin_ids:
            user = User.query.get(admin_id)
            if user:
                queue.admins.append(user)
        db.session.commit()
        
        flash(f'Очередь "{name}" создана!', 'success')
        return redirect(url_for('my_queues.my_queues'))
    
    return render_template('admin_queue_form.html', users=User.query.all())

@queue_mgmt_bp.route('/admin/queue/<int:queue_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_queue_edit(queue_id):
    if not current_user.is_admin:
        abort(403)
    
    queue = Queue.query.get_or_404(queue_id)
    print(queue)
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        is_active = request.form.get('is_active') == 'on'
        queue_type = request.form.get('queue_type', 'default')
        admin_ids = request.form.getlist('admin_ids')
        
        existing = Queue.query.filter(Queue.name == name, Queue.id != queue_id).first()
        if existing:
            flash('Очередь с таким названием уже существует!', 'danger')
            return render_template('admin_queue_form.html', queue=queue, users=User.query.all())
        
        queue.name = name
        queue.description = description
        queue.is_active = is_active
        queue.queue_type = queue_type
        
        queue.admins = []
        for admin_id in admin_ids:
            user = User.query.get(admin_id)
            if user:
                queue.admins.append(user)
        
        db.session.commit()
        flash(f'Очередь "{name}" обновлена!', 'success')
        return redirect(url_for('my_queues.my_queues'))
    
    return render_template('admin_queue_form.html', queue=queue, users=User.query.all())

@queue_mgmt_bp.route('/admin/queue/<int:queue_id>/delete', methods=['POST'])
@login_required
def admin_queue_delete(queue_id):
    if not current_user.is_admin:
        abort(403)
    
    queue = Queue.query.get_or_404(queue_id)
    name = queue.name
    
    if queue.tickets:
        flash(f'Нельзя удалить очередь "{name}", в ней есть заявки!', 'danger')
        return redirect(url_for('my_queues.my_queues'))
    
    db.session.delete(queue)
    db.session.commit()
    flash(f'Очередь "{name}" удалена!', 'success')
    return redirect(url_for('my_queues.my_queues'))

@queue_mgmt_bp.route('/admin/queue/<int:queue_id>/members', methods=['POST'])
@login_required
def admin_queue_members(queue_id):
    if not current_user.is_admin:
        abort(403)
    
    queue = Queue.query.get_or_404(queue_id)
    user_ids = request.form.getlist('members')
    
    queue.members = []
    for user_id in user_ids:
        user = User.query.get(user_id)
        if user:
            queue.members.append(user)
    
    db.session.commit()
    flash(f'Состав очереди "{queue.name}" обновлен!', 'success')
    return redirect(url_for('my_queues.my_queues'))

@queue_mgmt_bp.route('/admin/queue/<int:queue_id>/add-admin', methods=['POST'])
@login_required
def admin_queue_add_admin(queue_id):
    if not current_user.is_admin:
        abort(403)
    
    queue = Queue.query.get_or_404(queue_id)
    user_id = request.form.get('user_id')
    
    if user_id:
        user = User.query.get(user_id)
        if user and user not in queue.admins:
            queue.admins.append(user)
            db.session.commit()
            flash(f'Пользователь "{user.full_name or user.username}" добавлен как администратор', 'success')
        else:
            flash('Пользователь уже является администратором', 'warning')
    
    return redirect(url_for('my_queues.my_queues_edit', queue_id=queue.id))

@queue_mgmt_bp.route('/admin/queue/<int:queue_id>/remove-admin/<int:user_id>', methods=['POST'])
@login_required
def admin_queue_remove_admin(queue_id, user_id):
    if not current_user.is_admin:
        abort(403)
    
    queue = Queue.query.get_or_404(queue_id)
    user = User.query.get_or_404(user_id)
    
    if user in queue.admins:
        queue.admins.remove(user)
        db.session.commit()
        flash(f'Пользователь "{user.full_name or user.username}" удален из администраторов', 'success')
    else:
        flash('Пользователь не является администратором', 'warning')
    
    return redirect(url_for('my_queues.my_queues_edit', queue_id=queue.id))