from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_user, logout_user, login_required
from werkzeug.security import check_password_hash

from models import db, User
from core.ad_auth import authenticate_ad


login_bp = Blueprint("login", __name__, url_prefix='')


@login_bp.route('/login', methods=['GET', 'POST'])
def login():

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = User.query.filter_by(username=username).first()

        if not user:
            flash('Неверное имя пользователя или пароль', 'danger')
            return render_template('login.html')

        # 1. Сначала проверяем учётные данные в AD
        if authenticate_ad(username, password):
            login_user(user)

            flash('Вы успешно вошли!', 'success')
            return redirect(url_for('index'))

        # 2. Если AD не подтвердил пароль,
        # проверяем старый пароль из БД
        if user.password and check_password_hash(user.password, password):
            login_user(user)

            flash('Вы успешно вошли!', 'success')
            return redirect(url_for('index'))

        # 3. Оба варианта не подошли
        flash('Неверное имя пользователя или пароль', 'danger')

    return render_template('login.html')


@login_bp.route('/logout')
@login_required
def logout():
    logout_user()

    flash('Вы вышли из системы', 'info')

    return redirect(url_for('login.login'))