from flask import Blueprint, render_template, request, flash, redirect, url_for, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_
from models import db, User, Ticket, Queue
from config import TIMEZONES

my_q_bp = Blueprint("my_queues",__name__)

@my_q_bp.route('/my-queues')
@login_required
def my_queues():
    if not current_user.admin_queues and not current_user.is_admin:
        flash('У вас нет прав на просмотр этой страницы', 'danger')
        return redirect(url_for('index'))
    
    if current_user.is_admin:
        queues = Queue.query.order_by(Queue.created_at.desc()).all()
    else:
        queues = current_user.admin_queues
    
    users = User.query.all()
    
    queue_stats = []
    total_tickets = 0
    total_members = 0
    active_queues = 0
    total_queues = len(queues)
    
    for queue in queues:
        tickets_count = len(queue.tickets)
        members_count = len(queue.members)
        closed_count = len([t for t in queue.tickets if t.status == 'Закрыта'])
        
        queue_stats.append({
            'queue': queue,
            'tickets_count': tickets_count,
            'members_count': members_count,
            'closed_count': closed_count,
            'is_active': queue.is_active
        })
        
        total_tickets += tickets_count
        total_members += members_count
        if queue.is_active:
            active_queues += 1
    
    return render_template('my_queues.html', 
                         queues=queues, 
                         users=users,
                         queue_stats=queue_stats,
                         total_queues=total_queues,
                         total_tickets=total_tickets,
                         total_members=total_members,
                         active_queues=active_queues)