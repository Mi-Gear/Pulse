from flask import Blueprint, render_template, request, flash, redirect, url_for, abort, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_
from models import db, User, Ticket, Queue, Car, Booking
import requests


api_bp = Blueprint("api",__name__)

@api_bp.route("/api/medical-product")
def medical_product():
    reg_number = request.args.get("reg_number", "").strip()

    if not reg_number:
        return jsonify({
            "success": False,
            "error": "Не указан регистрационный номер"
        }), 400

    api_url = (
        "https://elk.roszdravnadzor.gov.ru"
        "/public-gateway/registered-med-product/api/v1/"
        "med-product/filter-public"
    )

    try:
        response = requests.post(
            api_url,
            params={
                "page": 0,
                "size": 10
            },
            json={
                "legalSystem": "RUSSIA",
                "textSearch": reg_number
            },
            verify=False,
            timeout=15
        )

        response.raise_for_status()

        data = response.json()
        content = data.get("content", [])

        if not content:
            return jsonify({
                "success": False,
                "error": "Медицинское изделие не найдено"
            }), 404

        product_id = content[0]["id"]

        return redirect(
            f"https://elk.roszdravnadzor.gov.ru/widget/med-product/{product_id}"
        )

    except requests.RequestException as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 502

@api_bp.route('/api/tickets')
def api_tickets():
    if not current_user.is_authenticated:
        return jsonify({'error': 'Unauthorized'}), 401
    
    tickets = Ticket.query.all()
    return jsonify([{
        'id': t.id,
        'number': t.ticket_number,
        'title': t.title,
        'status': t.status,
        'priority': t.priority,
        'queue': t.queue.name if t.queue else None,
        'assignees': [u.username for u in t.assignees],
        'deadline': t.deadline.isoformat() if t.deadline else None,
        'created_at': t.created_at.isoformat()
    } for t in tickets])

@api_bp.route('/api/users')
def api_users():
    if not current_user.is_authenticated:
        return jsonify({'error': 'Unauthorized'}), 401
    
    users = User.query.all()
    return jsonify([{
        'id': u.id,
        'username': u.username,
        'full_name': u.full_name,
        'email': u.email,
        'department': u.department,
        'position': u.position,
        'office': u.office,
        'timezone': u.timezone,
        'is_admin': u.is_admin
    } for u in users])