from flask import Blueprint, render_template, request, flash, redirect, url_for, abort
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash
from sqlalchemy import or_
from models import db, User, Ticket
from config import TIMEZONES

user_mgmt_bp = Blueprint('user_mgmt', __name__)

@user_mgmt_bp.route('/admin/users')
@login_required
def admin_users():
    if not current_user.is_admin:
        abort(403)
    
    query = User.query
    
    search = request.args.get('search', '').strip()
    department = request.args.get('department', '').strip()
    position = request.args.get('position', '').strip()
    office = request.args.get('office', '').strip()
    role = request.args.get('role', '').strip()
    
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
    if role == 'admin':
        query = query.filter(User.is_admin == True)
    elif role == 'user':
        query = query.filter(User.is_admin == False)
    
    users = query.order_by(User.full_name).all()
    
    departments = sorted(set(u.department for u in User.query.all() if u.department))
    positions = sorted(set(u.position for u in User.query.all() if u.position))
    offices = sorted(set(u.office for u in User.query.all() if u.office))
    
    return render_template('admin_users.html', 
                         users=users, 
                         departments=departments,
                         positions=positions,
                         offices=offices)

@user_mgmt_bp.route('/admin/user/create', methods=['GET', 'POST'])
@login_required
def admin_user_create():
    if not current_user.is_admin:
        abort(403)
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        full_name = request.form.get('full_name')
        phone = request.form.get('phone')
        department = request.form.get('department')
        position = request.form.get('position')
        office = request.form.get('office')
        is_admin = request.form.get('is_admin') == 'on'
        notify_email = request.form.get('notify_email') == 'on'
        
        if User.query.filter_by(username=username).first():
            flash('Имя пользователя уже занято', 'danger')
            return render_template('admin_user_form.html')
        
        if User.query.filter_by(email=email).first():
            flash('Email уже используется', 'danger')
            return render_template('admin_user_form.html')
        
        user = User(
            username=username,
            email=email,
            password=generate_password_hash(password),
            full_name=full_name,
            phone=phone,
            department=department,
            position=position,
            office=office,
            is_admin=is_admin,
            notify_email=notify_email
        )
        db.session.add(user)
        db.session.commit()
        flash(f'Пользователь "{username}" создан!', 'success')
        return redirect(url_for('user_mgmt.admin_users'))
    
    return render_template('admin_user_form.html', timezones=TIMEZONES)

@user_mgmt_bp.route('/admin/user/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_user_edit(user_id):
    if not current_user.is_admin:
        abort(403)
    
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        full_name = request.form.get('full_name')
        phone = request.form.get('phone')
        department = request.form.get('department')
        position = request.form.get('position')
        office = request.form.get('office')
        timezone = request.form.get('timezone', 'Europe/Moscow')
        is_admin = request.form.get('is_admin') == 'on'
        notify_email = request.form.get('notify_email') == 'on'
        
        existing_username = User.query.filter(User.username == username, User.id != user_id).first()
        if existing_username:
            flash('Имя пользователя уже занято', 'danger')
            return render_template('admin_user_form.html', user=user)
        
        existing_email = User.query.filter(User.email == email, User.id != user_id).first()
        if existing_email:
            flash('Email уже используется', 'danger')
            return render_template('admin_user_form.html', user=user)
        
        user.username = username
        user.email = email
        user.full_name = full_name
        user.phone = phone
        user.department = department
        user.position = position
        user.office = office
        user.timezone = timezone
        user.is_admin = is_admin
        user.notify_email = notify_email
        
        password = request.form.get('password')
        if password:
            user.password = generate_password_hash(password)
        
        db.session.commit()
        flash(f'Пользователь "{username}" обновлен!', 'success')
        return redirect(url_for('user_mgmt.admin_users'))
    
    return render_template('admin_user_form.html', user=user, timezones=TIMEZONES)

@user_mgmt_bp.route('/admin/user/<int:user_id>/delete', methods=['POST'])
@login_required
def admin_user_delete(user_id):
    if not current_user.is_admin:
        abort(403)
    
    if user_id == current_user.id:
        flash('Нельзя удалить самого себя!', 'danger')
        return redirect(url_for('user_mgmt.admin_users'))
    
    user = User.query.get_or_404(user_id)
    username = user.username
    
    if Ticket.query.filter_by(created_by_id=user_id).first():
        flash(f'Нельзя удалить пользователя "{username}", у него есть созданные заявки!', 'danger')
        return redirect(url_for('user_mgmt.admin_users'))
    
    db.session.delete(user)
    db.session.commit()
    flash(f'Пользователь "{username}" удален!', 'success')
    return redirect(url_for('user_mgmt.admin_users'))
