from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timedelta
import pytz, json

tz = pytz.timezone("Asia/Sakhalin")
db = SQLAlchemy()
current_time = datetime.now(tz)

# === Пользователи ===
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    
    full_name = db.Column(db.String(300), nullable=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    
    department = db.Column(db.String(200), nullable=True)
    position = db.Column(db.String(200), nullable=True)
    office = db.Column(db.String(50), nullable=True)
    
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now(tz))
    notify_email = db.Column(db.Boolean, default=True)
    
    # Связь с очередями где пользователь - участник
    queues = db.relationship('Queue', secondary='user_queue', back_populates='members')
    
    # Связь с очередями где пользователь - администратор
    admin_queues = db.relationship('Queue', secondary='queue_admins', back_populates='admins')
    
    # Заявки где пользователь - ответственный
    assigned_tickets = db.relationship('Ticket', secondary='ticket_assignees', back_populates='assignees')
    
    # Заявки созданные пользователем
    created_tickets = db.relationship('Ticket', foreign_keys='Ticket.created_by_id', backref='creator')
    allowed_queues = db.relationship(
        'Queue',
        secondary='queue_links',
        primaryjoin='Queue.id == queue_links.c.queue_from',
        secondaryjoin='Queue.id == queue_links.c.queue_to',
        backref='allowed_by_queues'
    )
    # Бронирования пользователя
    bookings = db.relationship('Booking', backref='user', lazy=True)
    

    @property
    def short_name(self):
        if self.full_name:
            parts = self.full_name.split()
            if len(parts) >= 2:
                return f"{parts[0]} {parts[1][0]}." if len(parts) >= 2 else self.full_name
        return self.username

    def __repr__(self):
        return f'<User {self.username}>'


# === Очереди ===
class Queue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    queue_type = db.Column(db.String(50), default='default')
    created_at = db.Column(db.DateTime, default=datetime.now(tz))
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    mail = db.Column(db.Text,default = None)
    
    # Администраторы очереди (многие-ко-многим)
    admins = db.relationship('User', secondary='queue_admins', back_populates='admin_queues')
    
    # Участники очереди (многие-ко-многим)
    members = db.relationship('User', secondary='user_queue', back_populates='queues')
    
    # Заявки в очереди
    tickets = db.relationship('Ticket', backref='queue', lazy=True)
    
    # Автомобили в очереди (для бронирования)
    cars = db.relationship('Car', backref='queue', lazy=True)
    
    created_by = db.relationship('User', foreign_keys=[created_by_id])


    def __repr__(self):
        return f'<Queue {self.name}>'


# === Связь пользователей с очередями (участники) ===
user_queue = db.Table('user_queue',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('queue_id', db.Integer, db.ForeignKey('queue.id'), primary_key=True),
    db.Column('joined_at', db.DateTime, default=datetime.now(tz))
)

queue_links = db.Table('queue_links',
    db.Column('queue_from', db.Integer, db.ForeignKey('queue.id'),primary_key=True),
    db.Column('queue_to', db.Integer, db.ForeignKey('queue.id'), primary_key=True))


# === Связь пользователей с очередями (администраторы) ===
queue_admins = db.Table('queue_admins',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('queue_id', db.Integer, db.ForeignKey('queue.id'), primary_key=True),
    db.Column('assigned_at', db.DateTime, default=datetime.now(tz))
)


# === Связь заявок с ответственными ===
ticket_assignees = db.Table('ticket_assignees',
    db.Column('ticket_id', db.Integer, db.ForeignKey('ticket.id'), primary_key=True),
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('assigned_at', db.DateTime, default=datetime.now(tz))
)


# === Заявки ===
class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_number = db.Column(db.String(20), unique=True, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), default='Новая')
    priority = db.Column(db.String(20), default='Средний')
    
    start_date = db.Column(db.DateTime, nullable=True)
    deadline = db.Column(db.DateTime, nullable=True)
    actual_deadline = db.Column(db.DateTime, nullable=True)
    
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    queue_id = db.Column(db.Integer, db.ForeignKey('queue.id'), nullable=True)
    
    rating = db.Column(db.Integer, nullable=True)
    feedback_text = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.now(tz))
    updated_at = db.Column(db.DateTime, default=datetime.now(tz), onupdate=datetime.now(tz))
    closed_at = db.Column(db.DateTime, nullable=True)

    assignees = db.relationship('User', secondary='ticket_assignees', back_populates='assigned_tickets')
    attachments = db.relationship('Attachment', backref='ticket', lazy=True)
    metrologist_request = db.relationship(
        'MetrologistRequest',
        back_populates='ticket',
        uselist=False,
        cascade='all, delete-orphan'
    )

    def get_deadline_by_priority(self):
        now = datetime.now(tz)()
        if self.priority == 'Высокий':
            return now + timedelta(hours=4)
        elif self.priority == 'Средний':
            return now + timedelta(days=1)
        else:
            return now + timedelta(days=3)

    def __repr__(self):
        return f'<Ticket {self.ticket_number}>'


# === Специальные заявки очереди «Инженеры-метрологи» ===
class MetrologistRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    ticket_id = db.Column(
        db.Integer,
        db.ForeignKey('ticket.id'),
        nullable=False,
        unique=True
    )
    data_json = db.Column(db.Text, nullable=True)

    # Списание / Приобретение / Ремонт / Обслуживание / Поверка
    request_type = db.Column(db.String(50), nullable=False)

    # Общие поля
    equipment_name = db.Column(db.String(500), nullable=True)
    inventory_number = db.Column(db.String(200), nullable=True)
    serial_number = db.Column(db.String(200), nullable=True)
    production_year = db.Column(db.Integer, nullable=True)

    # Списание
    writeoff_justification = db.Column(db.Text, nullable=True)

    # Приобретение
    quantity = db.Column(db.Integer, nullable=True)
    mz_order = db.Column(db.String(500), nullable=True)
    purchase_justification = db.Column(db.Text, nullable=True)
    nkmi = db.Column(db.String(300), nullable=True)

    # Ремонт
    fault_description = db.Column(db.Text, nullable=True)
    registration_certificate = db.Column(db.String(500), nullable=True)
    hazard_class = db.Column(db.String(100), nullable=True)

    # Обслуживание
    service_period = db.Column(db.String(200), nullable=True)

    # Поверка
    verification_period = db.Column(db.String(200), nullable=True)
    last_verification_date = db.Column(db.Date, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.now(tz))

    ticket = db.relationship('Ticket', back_populates='metrologist_request')

    def __repr__(self):
        return f'<MetrologistRequest {self.request_type} for Ticket {self.ticket_id}>'
    
    def get_data(self):
        if not self.data_json:
            return []

        try:
            return json.loads(self.data_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []


    def set_data(self, data):
        self.data_json = json.dumps(
            data,
            ensure_ascii=False
        )
    # === Справочник оборудования ===
class Equipment(db.Model):
    __tablename__ = 'equipment'

    id = db.Column(db.Integer, primary_key=True)

    equipment_name = db.Column(db.Text, nullable=True)
    model = db.Column(db.Text, nullable=True)

    serial_number = db.Column(db.Text, nullable=True)
    inventory_number = db.Column(db.Text, nullable=True)

    production_year = db.Column(db.Text, nullable=True)
    commissioning_year = db.Column(db.Text, nullable=True)

    certificate = db.Column(db.Text, nullable=True)
    registration_certificate = db.Column(db.Text, nullable=True)

    source_sheet = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f'<Equipment {self.inventory_number or self.serial_number}>'


# === Комментарии ===
class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    is_internal = db.Column(db.Boolean, default=False)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'))
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.now(tz))

    ticket = db.relationship('Ticket', backref='comments')
    author = db.relationship('User')
    attachments = db.relationship('Attachment', backref='comment', lazy=True)


# === Вложения ===
class Attachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    filepath = db.Column(db.String(500), nullable=False)
    file_size = db.Column(db.Integer, nullable=True)
    mime_type = db.Column(db.String(100), nullable=True)
    is_pinned = db.Column(db.Boolean, default=False)
    
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=True)
    comment_id = db.Column(db.Integer, db.ForeignKey('comment.id'), nullable=True)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    created_at = db.Column(db.DateTime, default=datetime.now(tz))
    author = db.relationship('User')

    def __repr__(self):
        return f'<Attachment {self.filename}>'


# === Автомобили ===
class Car(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    brand = db.Column(db.String(100), nullable=False)
    model = db.Column(db.String(100), nullable=False)
    license_plate = db.Column(db.String(20), nullable=False, unique=True)
    year = db.Column(db.Integer, nullable=True)
    color = db.Column(db.String(50), nullable=True)
    # is_available УДАЛЕНО — определяется динамически
    description = db.Column(db.Text, nullable=True)
    
    queue_id = db.Column(db.Integer, db.ForeignKey('queue.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now(tz))
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    bookings = db.relationship('Booking', backref='car', lazy=True)

    @property
    def full_name(self):
        return f"{self.brand} {self.model} ({self.license_plate})"
    
    def is_available(self, start_date, end_date):
        """Проверяет, свободна ли машина в указанный промежуток"""
        # Ищем активные бронирования, пересекающиеся с указанным периодом
        conflict = Booking.query.filter(
            Booking.car_id == self.id,
            Booking.status == 'active',
            Booking.start_date < end_date,
            Booking.end_date > start_date
        ).first()
        return conflict is None

    def __repr__(self):
        return f'<Car {self.full_name}>'


# === Бронирования ===
class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    car_id = db.Column(db.Integer, db.ForeignKey('car.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    start_date = db.Column(db.String(50), nullable=False)
    end_date = db.Column(db.String(50), nullable=False)
    purpose = db.Column(db.Text, nullable=True)
    
    status = db.Column(db.String(50), default='active')
    created_at = db.Column(db.DateTime, default=datetime.now(tz))
    updated_at = db.Column(db.DateTime, default=datetime.now(tz), onupdate=datetime.now(tz))

    def __repr__(self):
        return f'<Booking {self.car.full_name} by {self.user.username}>'

