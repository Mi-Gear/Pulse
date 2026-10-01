from flask import Blueprint, render_template, request, flash, redirect, url_for, abort, send_file, jsonify, send_from_directory, current_app
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import or_
from models import (
    db,
    User,
    Ticket,
    Comment,
    Queue,
    Attachment,
    Car,
    Booking,
    MetrologistRequest,
    Equipment,
    queue_links
)
from config import TIMEZONES
from datetime import datetime, timedelta
from core.utils import get_available_queues, allowed_file, can_manage_queue
from flask_app import app_core as app

from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

import sqlite3
import json
import os
import uuid
import io
import pandas as pd
import pytz

from io import BytesIO

tickets_bp = Blueprint('tickets', __name__)

@tickets_bp.route('/tickets/<int:ticket_id>/metrologist/docx')
@login_required
def metrologist_docx(ticket_id):
    from io import BytesIO
    import os

    from docx import Document
    from flask import send_file, abort

    ticket = Ticket.query.get_or_404(ticket_id)
    metro = ticket.metrologist_request

    if not metro:
        abort(404, description='Заявка метролога не найдена')

    rows = metro.get_data() or []

    template_path = os.path.join(
        tickets_bp.root_path,
        '..',
        'static',
        'templates',
        'ticket_template.docx'
    )

    if not os.path.exists(template_path):
        abort(500, description='Шаблон ticket_template.docx не найден')

    # ---------------------------------------------------------
    # Данные для обычных {placeholder}
    # ---------------------------------------------------------

    data = {
        'ticket_id': ticket.id,
        'ticket_number': ticket.ticket_number or '',
        'request_type': metro.request_type or '',
        'equipment_count': len(rows),
    }

    doc = Document(template_path)

    # ---------------------------------------------------------
    # Замена обычных placeholder
    # ---------------------------------------------------------

    def replace_placeholder(paragraph, placeholder, value):
        """
        Заменяет placeholder даже если Word разбил его
        на несколько runs.
        """

        value = '' if value is None else str(value)

        runs = paragraph.runs

        if not runs:
            return

        full_text = ''.join(run.text or '' for run in runs)

        if placeholder not in full_text:
            return

        start = full_text.find(placeholder)
        end = start + len(placeholder)

        # Определяем runs, которые затрагивает placeholder
        positions = []
        current = 0

        for index, run in enumerate(runs):
            text = run.text or ''
            run_start = current
            run_end = current + len(text)

            if run_end > start and run_start < end:
                positions.append(
                    (index, run_start, run_end)
                )

            current = run_end

        if not positions:
            return

        first_index = positions[0][0]
        last_index = positions[-1][0]

        first_run = runs[first_index]

        first_text = first_run.text or ''

        # Позиция начала placeholder внутри первого run
        first_run_start = positions[0][1]
        local_start = start - first_run_start

        # Если placeholder целиком в одном run
        if first_index == last_index:
            first_run.text = (
                first_text[:local_start]
                + value
                + first_text[local_start + len(placeholder):]
            )
            return

        # Текст после placeholder в последнем run
        last_run = runs[last_index]
        last_run_start = positions[-1][1]

        local_end = end - last_run_start

        suffix = (last_run.text or '')[local_end:]

        # Первый run:
        # оставляем всё до placeholder + значение
        first_run.text = (
            first_text[:local_start]
            + value
        )

        # Промежуточные runs очищаем
        for i in range(first_index + 1, last_index):
            runs[i].text = ''

        # Последний run оставляет только текст после placeholder
        last_run.text = suffix

    def replace_all_placeholders(paragraph):
        for key, value in data.items():
            replace_placeholder(
                paragraph,
                '{' + key + '}',
                value
            )

    # Обычные абзацы
    for paragraph in doc.paragraphs:
        replace_all_placeholders(paragraph)

    # Абзацы внутри существующих таблиц
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    replace_all_placeholders(paragraph)

    # ---------------------------------------------------------
    # Таблица оборудования
    # ---------------------------------------------------------

    labels = {
        'equipment_name': 'Наименование оборудования',
        'inventory_number': 'Инвентарный номер',
        'serial_number': 'Заводской номер',
        'production_year': 'Год выпуска',
        'quantity': 'Количество единиц',
        'mz_order': 'Приказ МЗ РФ',
        'nkmi': 'НКМИ (код вида медицинского изделия)',
        'fault_description': 'Описание неисправности',
        'registration_certificate': 'Регистрационное удостоверение',
        'hazard_class': 'Класс опасности',
        'service_period': 'Периодичность обслуживания согласно паспорта',
        'verification_period': 'Периодичность поверки согласно паспорта',
        'last_verification_date': 'Дата последней поверки',
        'writeoff_justification': 'Обоснование целесообразности списания',
        'purchase_justification': 'Обоснование целесообразности приобретения',
    }

    def find_equipment_table_placeholder():
        """
        Ищет абзац с {equipment_table}.
        """

        for paragraph in doc.paragraphs:
            if '{equipment_table}' in paragraph.text:
                return paragraph

        return None

    def add_table_after_paragraph(paragraph, rows):
        from docx.table import Table
        from docx.oxml import OxmlElement
        from docx.oxml.table import CT_Tbl
        from copy import deepcopy

        if not rows:
            return

        # -----------------------------------------------------
        # Определяем колонки
        # -----------------------------------------------------

        columns = []

        for key, label in labels.items():
            has_value = any(
                row.get(key) not in (None, '')
                for row in rows
            )

            if has_value:
                columns.append((key, label))

        if not columns:
            return

        # -----------------------------------------------------
        # Создаём временную таблицу
        # -----------------------------------------------------
        #
        # Важно:
        # создаём её в отдельном временном Document,
        # чтобы не было цикла XML.
        # -----------------------------------------------------

        from docx import Document

        temp_doc = Document()

        table = temp_doc.add_table(
            rows=1,
            cols=len(columns)
        )

        # -----------------------------------------------------
        # Заголовок
        # -----------------------------------------------------

        header_cells = table.rows[0].cells

        for index, (_, label) in enumerate(columns):

            header_cells[index].text = label

            for paragraph in header_cells[index].paragraphs:
                for run in paragraph.runs:
                    run.bold = True

        # -----------------------------------------------------
        # Данные
        # -----------------------------------------------------

        for item in rows:

            cells = table.add_row().cells

            for index, (key, _) in enumerate(columns):

                value = item.get(key, '')

                if value is None:
                    value = ''

                cells[index].text = str(value)

        # -----------------------------------------------------
        # Добавляем границы
        # -----------------------------------------------------

        tbl = table._tbl
        tblPr = tbl.tblPr

        borders = OxmlElement('w:tblBorders')

        for edge in (
            'top',
            'left',
            'bottom',
            'right',
            'insideH',
            'insideV'
        ):
            element = OxmlElement(f'w:{edge}')

            element.set(
                '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val',
                'single'
            )

            element.set(
                '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sz',
                '4'
            )

            element.set(
                '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}space',
                '0'
            )

            element.set(
                '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}color',
                '000000'
            )

            borders.append(element)

        tblPr.append(borders)

        # -----------------------------------------------------
        # Копируем XML таблицы
        # -----------------------------------------------------

        new_table_xml = deepcopy(table._tbl)

        # -----------------------------------------------------
        # Вставляем таблицу после {equipment_table}
        # -----------------------------------------------------

        paragraph._p.addnext(new_table_xml)
    # ---------------------------------------------------------
    # Поиск {equipment_table} в документе
    # ---------------------------------------------------------

    def find_equipment_placeholder():
        # Обычные абзацы
        for paragraph in doc.paragraphs:
            if '{equipment_table}' in paragraph.text:
                return paragraph

        # Абзацы внутри таблиц
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        if '{equipment_table}' in paragraph.text:
                            return paragraph

        return None


    # ---------------------------------------------------------
    # Создание таблицы оборудования
    # ---------------------------------------------------------

    def create_equipment_table(rows):
        from docx import Document
        from docx.oxml import OxmlElement
        from copy import deepcopy

        if not rows:
            return None

        columns = []

        for key, label in labels.items():

            if any(
                row.get(key) not in (None, '')
                for row in rows
            ):
                columns.append((key, label))

        if not columns:
            return None

        # Отдельный документ для создания таблицы
        temp_doc = Document()

        table = temp_doc.add_table(
            rows=1,
            cols=len(columns)
        )

        # -----------------------------------------------------
        # Заголовок
        # -----------------------------------------------------

        for index, (_, label) in enumerate(columns):

            cell = table.rows[0].cells[index]

            cell.text = str(label)

            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.bold = True

        # -----------------------------------------------------
        # Строки оборудования
        # -----------------------------------------------------

        for item in rows:

            cells = table.add_row().cells

            for index, (key, _) in enumerate(columns):

                value = item.get(key, '')

                if value is None:
                    value = ''

                cells[index].text = str(value)

        # -----------------------------------------------------
        # Границы
        # -----------------------------------------------------

        tbl = table._tbl
        tblPr = tbl.tblPr

        borders = OxmlElement('w:tblBorders')

        for edge in (
            'top',
            'left',
            'bottom',
            'right',
            'insideH',
            'insideV'
        ):

            border = OxmlElement(f'w:{edge}')

            border.set(
                '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val',
                'single'
            )

            border.set(
                '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sz',
                '4'
            )

            border.set(
                '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}space',
                '0'
            )

            border.set(
                '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}color',
                '000000'
            )

            borders.append(border)

        tblPr.append(borders)

        # Возвращаем XML-копию
        return deepcopy(table._tbl)


    # ---------------------------------------------------------
    # Вставка таблицы
    # ---------------------------------------------------------

    equipment_placeholder = find_equipment_placeholder()

    if equipment_placeholder:

        print('DEBUG: {equipment_table} найден')

        table_xml = create_equipment_table(rows)

        if table_xml is not None:

            # Вставляем таблицу после абзаца
            equipment_placeholder._p.addnext(table_xml)

            # Удаляем абзац с {equipment_table}
            parent = equipment_placeholder._p.getparent()

            parent.remove(equipment_placeholder._p)

            print(
                f'DEBUG: таблица оборудования создана: '
                f'{len(rows)} строк'
            )

        else:
            print('DEBUG: rows пустой или нет колонок')

    else:

        print('DEBUG: {equipment_table} НЕ найден')

    from io import BytesIO

    output = BytesIO()

    doc.save(output)

    output.seek(0)

    filename = f'Заявка_{ticket.ticket_number}.docx'

    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype=(
            'application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document'
        )
    )


def generate_ticket_number():
    year = datetime.now().year
    prefix = f"SD-{year}-"
    last_ticket = Ticket.query.filter(
        Ticket.ticket_number.like(f"{prefix}%")
    ).order_by(Ticket.id.desc()).first()
    if last_ticket:
        last_num = int(last_ticket.ticket_number.split('-')[-1])
        new_num = last_num + 1
    else:
        new_num = 1
    return f"{prefix}{str(new_num).zfill(3)}"

# === Мои заявки ===
@tickets_bp.route('/my-tickets')
@login_required
def my_tickets():
    query = Ticket.query.filter_by(created_by_id=current_user.id)
    
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
    
    total_count = Ticket.query.filter_by(created_by_id=current_user.id).count()
    active_count = Ticket.query.filter(
        Ticket.created_by_id == current_user.id,
        Ticket.status.in_(['Новая', 'В работе'])
    ).count()
    closed_count = Ticket.query.filter_by(created_by_id=current_user.id, status='Закрыта').count()
    
    return render_template('my_tickets.html', 
                         tickets=tickets,
                         total_count=total_count,
                         active_count=active_count,
                         closed_count=closed_count,
                         status_filter=status_filter,
                         search_query=search,
                         priority_filter=priority,
                         assignee_filter=assignee)

# === Назначенные мне ===
@tickets_bp.route('/assigned-tickets')
@login_required
def assigned_tickets():
    query = Ticket.query.filter(Ticket.assignees.any(id=current_user.id))
    
    status_filter = request.args.get('status', 'active')
    search = request.args.get('search', '').strip()
    priority = request.args.get('priority', '').strip()
    
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
    
    tickets = query.order_by(Ticket.created_at.desc()).all()
    
    total_count = Ticket.query.filter(Ticket.assignees.any(id=current_user.id)).count()
    active_count = Ticket.query.filter(
        Ticket.assignees.any(id=current_user.id),
        Ticket.status.in_(['Новая', 'В работе'])
    ).count()
    closed_count = Ticket.query.filter(
        Ticket.assignees.any(id=current_user.id),
        Ticket.status == 'Закрыта'
    ).count()
    
    return render_template('assigned_tickets.html', 
                         tickets=tickets,
                         total_count=total_count,
                         active_count=active_count,
                         closed_count=closed_count,
                         status_filter=status_filter,
                         search_query=search,
                         priority_filter=priority)

# === Заявки очереди ===
@tickets_bp.route('/queue-tickets')
@login_required
def queue_tickets():
    queue_ids = [q.id for q in current_user.queues]
    if not queue_ids:
        return render_template('queue_tickets.html', tickets=[], total_count=0, active_count=0, closed_count=0)
    
    query = Ticket.query.filter(Ticket.queue_id.in_(queue_ids))
    
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
    
    base_query = Ticket.query.filter(Ticket.queue_id.in_(queue_ids))
    total_count = base_query.count()
    active_count = base_query.filter(Ticket.status.in_(['Новая', 'В работе'])).count()
    closed_count = base_query.filter(Ticket.status == 'Закрыта').count()
    
    return render_template('queue_tickets.html', 
                         tickets=tickets,
                         total_count=total_count,
                         active_count=active_count,
                         closed_count=closed_count,
                         status_filter=status_filter,
                         search_query=search,
                         priority_filter=priority,
                         assignee_filter=assignee)

# === Детали заявки ===
@tickets_bp.route('/ticket/<int:ticket_id>')
@login_required
def ticket_detail(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)

    if not current_user.role == 0 :
        user_queue_ids = [q.id for q in current_user.queues]

        if ticket.queue_id not in user_queue_ids and ticket.created_by_id != current_user.id:
            abort(403)

    queues = get_available_queues()
    users = User.query.all()

    metro_request = MetrologistRequest.query.filter_by(
        ticket_id=ticket.id
    ).first()
    if metro_request is None: metro_request = None
    return render_template(
        'detail.html',
        ticket=ticket,
        queues=queues,
        users=users,
        metro_request=metro_request
    )

# === Поиск оборудования ===
@tickets_bp.route('/api/equipment/search')
@login_required
def search_equipment():
    query = (request.args.get('q') or '').strip()
    queue_id = (request.args.get('queue_id') or '').strip()

    if len(query) < 2:
        return jsonify([])

    rows = db.session.execute(
        db.text(f"""           
            SELECT
                id,
                equipment_name,
                model,
                serial_number,
                inventory_number,
                production_year,
                commissioning_year,
                certificate,
                registration_certificate,
                queue_id FROM equipment
            WHERE
                (inventory_number LIKE :query
                OR serial_number LIKE :query)
                AND queue_id = {queue_id}
            ORDER BY inventory_number
            LIMIT 10
        """),
        {'query': f'{query}%'}
    ).mappings().all()

    return jsonify([dict(row) for row in rows])

# === Создание заявки ===
METROLOGISTS_QUEUE_NAME = 'Инженеры-метрологи'
METROLOGIST_TYPES = {
    'writeoff': {
        'label': 'Списание',
        'title': 'Списание',
        'fields': [
            ('equipment_name', 'Наименование оборудования', 'text', True),
            ('inventory_number', 'Инвентарный номер', 'text', True),
            ('serial_number', 'Заводской номер', 'text', False),
            ('writeoff_justification', 'Обоснование целесообразности списания', 'textarea', True),
            ('production_year', 'Год выпуска', 'number', False),
        ]
    },
    'purchase': {
        'label': 'Заявка на приобретение',
        'title': 'Заявка на приобретение',
        'fields': [
            ('equipment_name', 'Наименование оборудования', 'text', True),
            ('quantity', 'Количество единиц', 'number', True),
            ('mz_order', 'Приказ МЗ РФ', 'text', False),
            ('purchase_justification', 'Обоснование целесообразности приобретения', 'textarea', True),
            ('registration_certificate', 'НКМИ (Код вида медицинского изделия)', 'text', False),
        ]
    },
    'repair': {
        'label': 'Заявка на ремонт',
        'title': 'Заявка на ремонт',
        'fields': [
            ('equipment_name', 'Наименование оборудования', 'text', True),
            ('inventory_number', 'Инвентарный номер', 'text', True),
            ('serial_number', 'Заводской номер', 'text', False),
            ('fault_description', 'Краткое описание неисправности', 'textarea', True),
            ('production_year', 'Год выпуска', 'number', False),
            ('registration_certificate', 'НКМИ (Код вида медицинского изделия)', 'text', False),
            ('registration_certificate', 'Регистрационное удостоверение', 'text', False),
            ('hazard_class', 'Класс опасности', 'text', False),
        ]
    },
    'service': {
        'label': 'Заявка на обслуживание',
        'title': 'Заявка на обслуживание',
        'fields': [
            ('equipment_name', 'Наименование оборудования', 'text', True),
            ('inventory_number', 'Инвентарный номер', 'text', True),
            ('serial_number', 'Заводской номер', 'text', False),
            ('production_year', 'Год выпуска', 'number', False),
            ('service_period', 'Периодичность обслуживания согласно паспорта', 'text', True),
            ('registration_certificate', 'НКМИ (Код вида медицинского изделия)', 'text', False),
        ]
    },
    'verification': {
        'label': 'Заявка на поверку',
        'title': 'Заявка на поверку',
        'fields': [
            ('equipment_name', 'Наименование оборудования', 'text', True),
            ('inventory_number', 'Инвентарный номер', 'text', True),
            ('serial_number', 'Заводской номер', 'text', False),
            ('verification_period', 'Периодичность поверки согласно паспорта', 'text', True),
            ('last_verification_date', 'Дата последней поверки', 'date', False),
        ]
    },
}

def is_metrologists_queue(queue):
    return bool(queue and queue.name.strip().casefold() == METROLOGISTS_QUEUE_NAME.casefold())

def _metro_rows_from_form(request):
    rows = []
    request_type = request.form.get('metrologist_type')
    config = METROLOGIST_TYPES.get(request_type)
    if not config:
        return None, 'Выберите тип заявки для очереди «Инженеры-метрологи».'

    # Форма передаёт JSON со строками. Это позволяет добавлять несколько единиц
    # оборудования одной заявкой.
    raw = request.form.get('metrologist_rows', '[]')
    try:
        submitted_rows = json.loads(raw)
    except (TypeError, ValueError):
        return None, 'Не удалось прочитать данные специальной заявки.'

    if not isinstance(submitted_rows, list) or not submitted_rows:
        return None, 'Добавьте хотя бы одну позицию оборудования.'

    for index, submitted in enumerate(submitted_rows, start=1):
        if not isinstance(submitted, dict):
            return None, f'Некорректная строка №{index}.'
        row = {}
        for key, label, field_type, required in config['fields']:
            value = submitted.get(key, '')
            if value is None:
                value = ''
            value = str(value).strip()
            if required and not value:
                return None, f'Строка №{index}: заполните поле «{label}».'
            if field_type == 'number' and value:
                try:
                    value = int(value)
                except ValueError:
                    return None, f'Строка №{index}: поле «{label}» должно быть числом.'
            row[key] = value
        rows.append(row)

    return rows, None

@tickets_bp.route('/ticket/create', methods=['GET', 'POST'])
@login_required
async def create_ticket():
    
        # Получаем ID очередей, в которых состоит пользователь
    user_queue_ids = [q.id for q in current_user.queues]
    print(user_queue_ids)
    
    if user_queue_ids:
        # Находим очереди, которые разрешены через queue_links
        # Используем прямой запрос к таблице queue_links
        available_queues = Queue.query.filter(
            db.or_(
                # Очереди, в которых пользователь состоит напрямую
                # Очереди, которые доступны через queue_links (queue_to)
                # где queue_from - очередь пользователя
                Queue.id.in_(
                    db.select(queue_links.c.queue_to).where(
                        queue_links.c.queue_from.in_(user_queue_ids)
                    )
                )
            ),
            Queue.is_active == True
        ).distinct().all()
    else:
        available_queues = []
    
    if not available_queues:
        flash('В системе пока нет активных очередей.', 'warning')
        return redirect(url_for('index'))
    
    # Получаем preselected_queue_id из URL параметров (если есть)
    preselected_queue_id = request.args.get('queue_id', type=int)

    if not available_queues:
        flash('В системе пока нет активных очередей.', 'warning')
        return redirect(url_for('index'))

    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        description = request.form.get('description') or ''
        priority = request.form.get('priority', 'Средний')
        queue_id = request.form.get('queue_id')
        start_date = request.form.get('start_date')
        deadline = request.form.get('deadline')
        
        if not queue_id:
            flash('Пожалуйста, выберите очередь для заявки.', 'danger')
            print(current_user.queues)
            return render_template('create.html', queues=available_queues,
                                   preselected_queue_id=preselected_queue_id,
                                   metrologist_types=METROLOGIST_TYPES,
                                   user_queue=current_user.queues[0].id)

        queue = Queue.query.get(queue_id)
        if not queue:
            flash('Выбранная очередь не найдена.', 'danger')
            return redirect(url_for('tickets.create_ticket'))

        metro_rows = None
        metro_type = None

        if is_metrologists_queue(queue):
            metro_type = request.form.get('metrologist_type')
            metro_rows, error = _metro_rows_from_form(request)
            if error:
                flash(error, 'danger')
                print(current_user.queues)
                return render_template('create.html', queues=available_queues,
                                        preselected_queue_id=preselected_queue_id,
                                        metrologist_types=METROLOGIST_TYPES,
                                        user_queue=current_user.queues[0].id)

            config = METROLOGIST_TYPES[metro_type]
            # Название заявки формируем из типа и первой позиции оборудования.
            first_name = metro_rows[0].get('equipment_name', '')
            title = config['title'] + (f' — {first_name}' if first_name else '')
            # Ticket.description остаётся заполненным для совместимости со старой системой.
            description = json.dumps(metro_rows, ensure_ascii=False, indent=2)

        if not title:
            flash('Пожалуйста, укажите название заявки.', 'danger')
            print(current_user.queues)
            return render_template('create.html', queues=available_queues,
                                               preselected_queue_id=preselected_queue_id,
                                               metrologist_types=METROLOGIST_TYPES,
                                               user_queue=current_user.queues[0].id)
            
        if not description.strip():
            flash('Пожалуйста, заполните описание заявки.', 'danger')
            print(current_user.queues)
            return render_template('create.html', queues=available_queues,
                                               preselected_queue_id=preselected_queue_id,
                                               metrologist_types=METROLOGIST_TYPES,
                                               user_queue=current_user.queues[0].id)

        ticket = Ticket(
            ticket_number=generate_ticket_number(),
            title=title,
            description=description,
            priority=priority,
            created_by_id=current_user.id,
            queue_id=queue.id
        )
        if start_date:
            ticket.start_date = datetime.strptime(start_date, '%Y-%m-%dT%H:%M')
        if deadline:
            ticket.deadline = datetime.strptime(deadline, '%Y-%m-%dT%H:%M')
        else:
            ticket.deadline = ticket.get_deadline_by_priority()

        db.session.add(ticket)
        db.session.flush()

        if metro_rows is not None:
            metro_request = MetrologistRequest(
                request_type=metro_type,
                data_json=json.dumps(
                    metro_rows,
                    ensure_ascii=False
                )
            )

            # Явно связываем заявку метролога с Ticket
            ticket.metrologist_request = metro_request

            db.session.add(metro_request)

        db.session.commit()

        if 'files' in request.files:
            files = request.files.getlist('files')
            for file in files:
                if file and file.filename != '' and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    unique_filename = f"{uuid.uuid4().hex}_{filename}"
                    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
                    file.save(filepath)
                    attachment = Attachment(
                        filename=filename,
                        filepath=filepath,
                        file_size=os.path.getsize(filepath),
                        mime_type=file.content_type,
                        ticket_id=ticket.id,
                        author_id=current_user.id
                    )
                    db.session.add(attachment)
            db.session.commit()

        flash(f'Заявка {ticket.ticket_number} создана!', 'success')

        await app.execute_hook('after_create_ticket',
                               ticket_id=ticket.id,
                               created_by_id=current_user.id)

        return redirect(url_for('tickets.my_tickets'))

    return render_template('create.html', queues=available_queues,
                                   preselected_queue_id=preselected_queue_id,
                                   metrologist_types=METROLOGIST_TYPES,
                                   user_queue=current_user.queues[0].id)

# === Редактирование заявки ===
@tickets_bp.route('/ticket/<int:ticket_id>/edit', methods=['GET', 'POST'])
@login_required
async def edit_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not can_manage_queue(current_user, ticket.queue):
        abort(403)
    
    if request.method == 'POST':
        old_title = ticket.title
        old_description = ticket.description
        old_priority = ticket.priority
        
        ticket.title = request.form.get('title')
        ticket.description = request.form.get('description')
        ticket.priority = request.form.get('priority')
        start_date = request.form.get('start_date')
        deadline = request.form.get('deadline')
        
        if start_date:
            ticket.start_date = datetime.strptime(start_date, '%Y-%m-%dT%H:%M')
        else:
            ticket.start_date = None
        if deadline:
            ticket.deadline = datetime.strptime(deadline, '%Y-%m-%dT%H:%M')
        else:
            ticket.deadline = None
        
        db.session.commit()
        
        await app.execute_hook("after_edit_ticket", 
                              ticket_id=ticket.id,
                              old_title=old_title,
                              old_description=old_description,
                              old_priority=old_priority)
        
        flash('Заявка обновлена!', 'success')
        return redirect(url_for('tickets.ticket_detail', ticket_id=ticket.id))
    
    return render_template('edit_ticket.html', ticket=ticket)

# === Загрузка файлов ===
@tickets_bp.route('/ticket/<int:ticket_id>/upload', methods=['POST'])
@login_required
async def upload_attachment(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not current_user.role == 0 :
        user_queue_ids = [q.id for q in current_user.queues]
        if ticket.queue_id not in user_queue_ids:
            abort(403)
    
    if 'file' not in request.files:
        flash('Файл не выбран', 'danger')
        return redirect(url_for('tickets.ticket_detail', ticket_id=ticket.id))
    
    file = request.files['file']
    if file.filename == '':
        flash('Файл не выбран', 'danger')
        return redirect(url_for('tickets.ticket_detail', ticket_id=ticket.id))
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4().hex}_{filename}"
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(filepath)
        
        attachment = Attachment(
            filename=filename,
            filepath=filepath,
            file_size=os.path.getsize(filepath),
            mime_type=file.content_type,
            ticket_id=ticket.id,
            author_id=current_user.id
        )
        db.session.add(attachment)
        db.session.commit()
        
        await app.execute_hook("after_upload_attachment", 
                              attachment_id=attachment.id,
                              ticket_id=ticket.id,
                              uploaded_by_id=current_user.id)
        
        flash('Файл загружен!', 'success')
    else:
        flash('Недопустимый тип файла', 'danger')
    
    return redirect(url_for('tickets.ticket_detail', ticket_id=ticket.id))

# === Закрепление файла ===
@tickets_bp.route('/attachment/<int:attachment_id>/pin', methods=['POST'])
@login_required
async def pin_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    ticket = attachment.ticket
    if not can_manage_queue(current_user, ticket.queue):
        abort(403)
    
    old_pinned = attachment.is_pinned
    attachment.is_pinned = True
    db.session.commit()
    
    await app.execute_hook("after_pin_attachment", 
                          attachment_id=attachment.id,
                          ticket_id=ticket.id,
                          pinned_by_id=current_user.id,
                          old_pinned=old_pinned)
    
    flash('Файл закреплен!', 'success')
    return redirect(url_for('tickets.ticket_detail', ticket_id=ticket.id))

@tickets_bp.route('/attachment/<int:attachment_id>/unpin', methods=['POST'])
@login_required
async def unpin_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    ticket = attachment.ticket
    if not can_manage_queue(current_user, ticket.queue):
        abort(403)
    
    old_pinned = attachment.is_pinned
    attachment.is_pinned = False
    db.session.commit()
    
    await app.execute_hook("after_unpin_attachment", 
                          attachment_id=attachment.id,
                          ticket_id=ticket.id,
                          unpinned_by_id=current_user.id,
                          old_pinned=old_pinned)
    
    flash('Файл откреплен', 'info')
    return redirect(url_for('tickets.ticket_detail', ticket_id=ticket.id))

@tickets_bp.route('/attachment/<int:attachment_id>/delete', methods=['POST'])
@login_required
async def delete_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    ticket = attachment.ticket
    
    if not current_user.role == 0  and attachment.author_id != current_user.id:
        abort(403)
    
    filename = attachment.filename
    filepath = attachment.filepath
    
    if os.path.exists(filepath):
        os.remove(filepath)
    
    db.session.delete(attachment)
    db.session.commit()
    
    await app.execute_hook("after_delete_attachment", 
                          attachment_id=attachment.id,
                          ticket_id=ticket.id if ticket else None,
                          deleted_by_id=current_user.id,
                          filename=filename)
    
    flash('Файл удален', 'success')
    return redirect(url_for('tickets.ticket_detail', ticket_id=ticket.id))

@tickets_bp.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)

# === Назначение ответственных ===
@tickets_bp.route('/ticket/<int:ticket_id>/assign', methods=['POST'])
@login_required
async def assign_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not can_manage_queue(current_user, ticket.queue):
        abort(403)
    
    old_assignees = [u.id for u in ticket.assignees]
    user_ids = request.form.getlist('assignees')
    ticket.assignees = []
    
    for user_id in user_ids:
        user = User.query.get(user_id)
        if user:
            ticket.assignees.append(user)
    
    db.session.commit()
    
    await app.execute_hook("after_assign_ticket", 
                          ticket_id=ticket.id,
                          assigned_by_id=current_user.id,
                          old_assignee_ids=old_assignees,
                          new_assignee_ids=[int(uid) for uid in user_ids])
    
    flash('Ответственные назначены!', 'success')
    return redirect(url_for('tickets.ticket_detail', ticket_id=ticket.id))

# === Передача заявки ===
@tickets_bp.route('/ticket/<int:ticket_id>/transfer', methods=['POST'])
@login_required
async def transfer_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if current_user not in ticket.assignees and not (ticket.queue and current_user in ticket.queue.admins):
        abort(403)
    
    old_assignees = [u.id for u in ticket.assignees]
    user_id = request.form.get('new_assignee')
    
    if user_id:
        user = User.query.get(user_id)
        if user and user in ticket.queue.members:
            if current_user in ticket.assignees:
                ticket.assignees.remove(current_user)
            if user not in ticket.assignees:
                ticket.assignees.append(user)
            db.session.commit()
            
            await app.execute_hook("after_transfer_ticket", 
                                  ticket_id=ticket.id,
                                  transferred_by_id=current_user.id,
                                  old_assignee_ids=old_assignees,
                                  new_assignee_ids=[u.id for u in ticket.assignees])
            
            flash(f'Заявка передана пользователю {user.username}', 'success')
    
    return redirect(url_for('tickets.ticket_detail', ticket_id=ticket.id))

# === Комментарии ===
@tickets_bp.route('/ticket/<int:ticket_id>/comment', methods=['POST'])
@login_required
async def add_comment(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not current_user.role == 0 :
        user_queue_ids = [q.id for q in current_user.queues]
        if ticket.queue_id not in user_queue_ids and ticket.created_by_id != current_user.id:
            abort(403)
    
    text = request.form.get('text')
    is_internal = request.form.get('is_internal') == 'on'
    
    comment = Comment(
        text=text,
        is_internal=is_internal,
        ticket_id=ticket.id,
        author_id=current_user.id
    )
    db.session.add(comment)
    db.session.commit()
    
    if 'files' in request.files:
        files = request.files.getlist('files')
        for file in files:
            if file and file.filename != '' and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4().hex}_{filename}"
                filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                attachment = Attachment(
                    filename=filename,
                    filepath=filepath,
                    file_size=os.path.getsize(filepath),
                    mime_type=file.content_type,
                    comment_id=comment.id,
                    author_id=current_user.id
                )
                db.session.add(attachment)
        db.session.commit()
    
    await app.execute_hook("after_add_comment", 
                          comment_id=comment.id,
                          ticket_id=ticket.id,
                          author_id=current_user.id,
                          is_internal=is_internal)
    
    flash('Комментарий добавлен!', 'success')
    return redirect(url_for('tickets.ticket_detail', ticket_id=ticket.id))

# === Изменение статуса ===
@tickets_bp.route('/ticket/<int:ticket_id>/status', methods=['POST'])
@login_required
async def change_status(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if not current_user.role == 0 :
        user_queue_ids = [q.id for q in current_user.queues]
        if ticket.queue_id not in user_queue_ids:
            abort(403)
    
    new_status = request.form.get('status')
    old_status = ticket.status
    
    if new_status == 'Закрыта' and ticket.status != 'Закрыта':
        ticket.closed_at = datetime.utcnow()
    
    ticket.status = new_status
    ticket.updated_at = datetime.utcnow()
    db.session.commit()
    
    await app.execute_hook("after_change_status", 
                          ticket_id=ticket.id,
                          changed_by_id=current_user.id,
                          old_status=old_status,
                          new_status=new_status)
    
    flash(f'Статус изменен на "{new_status}"', 'success')
    return redirect(url_for('tickets.ticket_detail', ticket_id=ticket.id))

# === Изменение очереди ===
@tickets_bp.route('/ticket/<int:ticket_id>/queue', methods=['POST'])
@login_required
async def assign_queue(ticket_id):
    if not current_user.role == 0 :
        abort(403)
    
    ticket = Ticket.query.get_or_404(ticket_id)
    old_queue_id = ticket.queue_id
    queue_id = request.form.get('queue_id')
    
    if queue_id:
        queue = Queue.query.get(queue_id)
        if queue:
            ticket.queue = queue
            db.session.commit()
            
            await app.execute_hook("after_change_queue", 
                                  ticket_id=ticket.id,
                                  changed_by_id=current_user.id,
                                  old_queue_id=old_queue_id,
                                  new_queue_id=queue.id)
            
            flash(f'Заявка перемещена в очередь "{queue.name}"', 'success')
    else:
        ticket.queue = None
        db.session.commit()
        
        await app.execute_hook("after_remove_from_queue", 
                              ticket_id=ticket.id,
                              changed_by_id=current_user.id,
                              old_queue_id=old_queue_id)
        
        flash('Заявка удалена из очереди', 'info')
    
    return redirect(url_for('tickets.ticket_detail', ticket_id=ticket.id))

# === Обратная связь ===
@tickets_bp.route('/ticket/<int:ticket_id>/feedback', methods=['POST'])
@login_required
async def add_feedback(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if ticket.created_by_id != current_user.id:
        abort(403)
    
    rating = request.form.get('rating')
    feedback_text = request.form.get('feedback_text')
    
    old_rating = ticket.rating
    old_feedback = ticket.feedback_text
    
    if rating:
        ticket.rating = int(rating)
    if feedback_text:
        ticket.feedback_text = feedback_text
    
    db.session.commit()
    
    await app.execute_hook("after_add_feedback", 
                          ticket_id=ticket.id,
                          user_id=current_user.id,
                          rating=int(rating) if rating else None,
                          feedback_text=feedback_text,
                          old_rating=old_rating,
                          old_feedback=old_feedback)
    
    flash('Спасибо за оценку!', 'success')
    return redirect(url_for('tickets.ticket_detail', ticket_id=ticket.id))