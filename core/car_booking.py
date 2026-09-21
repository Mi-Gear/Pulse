from flask import Blueprint, render_template, request, flash, redirect, url_for, abort, jsonify
from flask_login import login_required, current_user
from models import db, User, Ticket, Queue, Car, Booking
from datetime import datetime
from core.utils import can_manage_queue
from flask_app import app_core as app

car_booking_bp = Blueprint("car_booking", __name__)

@car_booking_bp.route('/booking-gantt')
@login_required
def booking_gantt():
    booking_queues = Queue.query.filter_by(queue_type='booking', is_active=True).all()
    cars = []
    for queue in booking_queues:
        for car in Car.query.filter_by(queue_id=queue.id).all():
            cars.append({
                'id': car.id,
                'name': car.full_name,
                'queue_id': queue.id,
                'queue_name': queue.name,
                'is_available': car.is_available
            })
    return render_template('booking_gantt.html', cars=cars)

@car_booking_bp.route('/api/booking-gantt-data')
@login_required
def booking_gantt_data():
    bookings = Booking.query.filter_by(status='active').all()
    
    gantt_data = []
    for booking in bookings:
        gantt_data.append({
            'id': booking.id,
            'car_id': booking.car_id,
            'car_name': booking.car.full_name,
            'user_id': booking.user_id,
            'user_name': booking.user.full_name or booking.user.username,
            'start': booking.start_date,
            'end': booking.end_date,
            'is_mine': booking.user_id == current_user.id
        })
    
    return jsonify(gantt_data)

@car_booking_bp.route('/api/booking-gantt/cars')
@login_required
def booking_gantt_cars():
    booking_queues = Queue.query.filter_by(queue_type='booking', is_active=True).all()
    cars = []
    for queue in booking_queues:
        for car in Car.query.filter_by(queue_id=queue.id).all():
            active_booking = Booking.query.filter_by(car_id=car.id, status='active').first()
            cars.append({
                'id': car.id,
                'name': f"{car.brand} {car.model} ({car.license_plate})",
                'queue_id': queue.id,
                'queue_name': queue.name,
                'is_available': active_booking is None
            })
    return jsonify(cars)

@car_booking_bp.route('/api/booking-gantt/book', methods=['POST'])
@login_required
async def booking_gantt_book():
    data = request.get_json()
    car_id = data.get('car_id')
    start_str = data.get('start_date')
    end_str = data.get('end_date')
    purpose = data.get('purpose', '')
    
    if not car_id or not start_str or not end_str:
        return jsonify({'error': 'Не все поля заполнены'}), 400
    
    car = Car.query.get_or_404(car_id)
    start = datetime.fromisoformat(start_str)
    end = datetime.fromisoformat(end_str)
    
    if start >= end:
        return jsonify({'error': 'Дата начала должна быть раньше даты окончания'}), 400
    
    conflict = Booking.query.filter(
        Booking.car_id == car.id,
        Booking.status == 'active',
        Booking.start_date < end,
        Booking.end_date > start
    ).first()
    
    if conflict:
        return jsonify({'error': 'Автомобиль уже забронирован на этот период'}), 400
    
    booking = Booking(
        car_id=car.id,
        user_id=current_user.id,
        start_date=start,
        end_date=end,
        purpose=purpose
    )
    db.session.add(booking)
    db.session.commit()
    
    await app.execute_hook("after_book_car", 
                          booking_id=booking.id,
                          car_id=car.id,
                          user_id=current_user.id,
                          start_date=start.isoformat(),
                          end_date=end.isoformat(),
                          purpose=purpose)
    
    if current_user.notify_email and current_user.email:
        from modules.email_service import send_booking_notification
        send_booking_notification(car, current_user, start, end, purpose)
    
    return jsonify({
        'success': True,
        'booking_id': booking.id,
        'message': f'Автомобиль {car.full_name} успешно забронирован!'
    })

@car_booking_bp.route('/api/booking-gantt/cancel/<int:booking_id>', methods=['POST'])
@login_required
async def booking_gantt_cancel(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    
    if booking.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'error': 'У вас нет прав на отмену этого бронирования'}), 403
    
    booking.status = 'cancelled'
    db.session.commit()
    
    await app.execute_hook("after_cancel_booking", 
                          booking_id=booking.id,
                          car_id=booking.car_id,
                          user_id=current_user.id)
    
    return jsonify({'success': True, 'message': 'Бронирование отменено'})

@car_booking_bp.route('/queue/<int:queue_id>/cars')
@login_required
def queue_cars(queue_id):
    queue = Queue.query.get_or_404(queue_id)
    
    if queue.queue_type != 'booking':
        flash('Эта очередь не является очередью бронирования', 'warning')
        return redirect(url_for('queue_detail', queue_id=queue.id))
    
    if not queue.is_active:
        flash('Очередь неактивна', 'warning')
        return redirect(url_for('queue_detail', queue_id=queue.id))
    
    cars = Car.query.filter_by(queue_id=queue.id).all()
    bookings = Booking.query.filter(
        Booking.car_id.in_([c.id for c in cars]),
        Booking.status == 'active'
    ).all()
    
    booking_map = {}
    for booking in bookings:
        booking_map[booking.car_id] = booking
    
    return render_template('queue_cars.html', 
                         queue=queue, 
                         cars=cars,
                         booking_map=booking_map)

@car_booking_bp.route('/queue/<int:queue_id>/car/add', methods=['POST'])
@login_required
def add_car(queue_id):
    queue = Queue.query.get_or_404(queue_id)
    
    if not can_manage_queue(current_user, queue):
        abort(403)
    
    brand = request.form.get('brand')
    model = request.form.get('model')
    license_plate = request.form.get('license_plate')
    year = request.form.get('year')
    color = request.form.get('color')
    description = request.form.get('description')
    
    if not brand or not model or not license_plate:
        flash('Заполните обязательные поля (марка, модель, госномер)', 'danger')
        return redirect(url_for('queue_cars', queue_id=queue.id))
    
    existing = Car.query.filter_by(license_plate=license_plate).first()
    if existing:
        flash(f'Автомобиль с госномером {license_plate} уже существует', 'danger')
        return redirect(url_for('queue_cars', queue_id=queue.id))
    
    car = Car(
        brand=brand,
        model=model,
        license_plate=license_plate,
        year=int(year) if year else None,
        color=color,
        description=description,
        queue_id=queue.id,
        created_by_id=current_user.id
    )
    db.session.add(car)
    db.session.commit()
    
    flash(f'Автомобиль {car.full_name} добавлен!', 'success')
    return redirect(url_for('queue_cars', queue_id=queue.id))

@car_booking_bp.route('/car/<int:car_id>/delete', methods=['POST'])
@login_required
def delete_car(car_id):
    car = Car.query.get_or_404(car_id)
    queue_id = car.queue_id
    
    if not can_manage_queue(current_user, car.queue):
        abort(403)
    
    active_bookings = Booking.query.filter_by(car_id=car.id, status='active').first()
    if active_bookings:
        flash('Нельзя удалить автомобиль с активным бронированием', 'danger')
        return redirect(url_for('queue_cars', queue_id=queue_id))
    
    db.session.delete(car)
    db.session.commit()
    flash('Автомобиль удален', 'success')
    return redirect(url_for('queue_cars', queue_id=queue_id))

@car_booking_bp.route('/car/<int:car_id>/book', methods=['POST'])
@login_required
async def book_car(car_id):
    car = Car.query.get_or_404(car_id)
    
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')
    purpose = request.form.get('purpose')
    redirect_url = request.form.get('redirect', 'queue_cars')
    
    if not start_date or not end_date:
        flash('Укажите даты бронирования', 'danger')
        if redirect_url == 'booking_cars':
            return redirect(url_for('booking_cars'))
        return redirect(url_for('queue_cars', queue_id=car.queue_id))
    
    start = datetime.strptime(start_date, '%Y-%m-%dT%H:%M')
    end = datetime.strptime(end_date, '%Y-%m-%dT%H:%M')
    
    if start >= end:
        flash('Дата начала должна быть раньше даты окончания', 'danger')
        if redirect_url == 'booking_cars':
            return redirect(url_for('booking_cars'))
        return redirect(url_for('queue_cars', queue_id=car.queue_id))
    
    conflict = Booking.query.filter(
        Booking.car_id == car.id,
        Booking.status == 'active',
        Booking.start_date < end,
        Booking.end_date > start
    ).first()
    
    if conflict:
        flash('Автомобиль уже забронирован на этот период', 'danger')
        if redirect_url == 'booking_cars':
            return redirect(url_for('booking_cars'))
        return redirect(url_for('queue_cars', queue_id=car.queue_id))
    
    booking = Booking(
        car_id=car.id,
        user_id=current_user.id,
        start_date=start,
        end_date=end,
        purpose=purpose
    )
    db.session.add(booking)
    db.session.commit()
    
    await app.execute_hook("after_book_car", 
                          booking_id=booking.id,
                          car_id=car.id,
                          user_id=current_user.id,
                          start_date=start.isoformat(),
                          end_date=end.isoformat(),
                          purpose=purpose)
    
    if current_user.notify_email and current_user.email:
        from modules.email_service import send_booking_notification
        send_booking_notification(car, current_user, start, end, purpose)
    
    flash(f'Автомобиль {car.full_name} успешно забронирован!', 'success')
    
    if redirect_url == 'booking_cars':
        return redirect(url_for('booking_cars'))
    return redirect(url_for('queue_cars', queue_id=car.queue_id))

@car_booking_bp.route('/booking/<int:booking_id>/cancel', methods=['POST'])
@login_required
async def cancel_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    
    if booking.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    
    booking.status = 'cancelled'
    db.session.commit()
    
    await app.execute_hook("after_cancel_booking", 
                          booking_id=booking.id,
                          car_id=booking.car_id,
                          user_id=current_user.id)
    
    flash('Бронирование отменено', 'success')
    return redirect(url_for('booking_gantt'))