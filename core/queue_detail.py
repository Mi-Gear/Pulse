from flask import Blueprint, render_template, request, flash, redirect, url_for, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_
from models import db, User, Ticket, Queue
from config import TIMEZONES

q_detail_bp = Blueprint("queue_detail",__name__)

@q_detail_bp.route('/queue/<int:queue_id>')
@login_required
def queue_detail(queue_id):
    queue = Queue.query.get_or_404(queue_id)
    
    if not current_user.is_admin and current_user not in queue.admins and current_user not in queue.members:
        abort(403)
    
    query = Ticket.query.filter_by(queue_id=queue.id)
    
    status_filter = request.args.get('status', 'active')
    search = request.args.get('search', '').strip()
    priority = request.args.get('priority', '').strip()
    assignee = request.args.get('assignee', '')
    
    if status_filter == 'active':
        query = query.filter(Ticket.status.in_(['Новая', 'В работе']))
    elif status_filter == 'closed':
        query = query.filter(Ticket.status == 'Закрыта')
    elif status_filter == 'all':
        pass
    else:
        query = query.filter(Ticket.status == status_filter)
    
    if search:
        query = query.filter(
            or_(
                Ticket.ticket_number.ilike(f'%{search}%'),
                Ticket.title.ilike(f'%{search}%')
            )
        )
    
    if priority:
        query = query.filter(Ticket.priority == priority)
    
    if assignee == 'me':
        query = query.filter(Ticket.assignees.any(id=current_user.id))
    elif assignee == 'unassigned':
        query = query.filter(~Ticket.assignees.any())
    
    tickets = query.order_by(Ticket.created_at.desc()).all()
    
    total_tickets = Ticket.query.filter_by(queue_id=queue.id).count()
    open_tickets = Ticket.query.filter_by(queue_id=queue.id).filter(Ticket.status.in_(['Новая', 'В работе'])).count()
    closed_tickets = Ticket.query.filter_by(queue_id=queue.id, status='Закрыта').count()
    high_priority = Ticket.query.filter_by(queue_id=queue.id, priority='Высокий').count()
    
    members = queue.members
    admins = queue.admins
    
    return render_template('queue_detail.html', 
                         queue=queue,
                         tickets=tickets,
                         members=members,
                         admins=admins,
                         total_tickets=total_tickets,
                         open_tickets=open_tickets,
                         closed_tickets=closed_tickets,
                         high_priority=high_priority,
                         status_filter=status_filter,
                         search_query=search,
                         priority_filter=priority,
                         assignee_filter=assignee)