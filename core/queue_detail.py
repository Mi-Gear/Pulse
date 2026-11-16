from flask import Blueprint, render_template, request, flash, redirect, url_for, abort, send_file, current_app
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_
from io import BytesIO
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.worksheet.table import Table, TableStyleInfo
import re
from models import db, User, Ticket, Queue, Equipment
from config import TIMEZONES

q_detail_bp = Blueprint("queue_detail",__name__)

@q_detail_bp.route('/queue/<int:queue_id>')
@login_required
def queue_detail(queue_id):
    queue = Queue.query.get_or_404(queue_id)
    
    if not current_user.role == 0  and current_user not in queue.admins and current_user not in queue.members:
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

@q_detail_bp.route('/queue/<int:queue_id>/equipment')
@login_required
def queue_equipment(queue_id):
    queue = Queue.query.get_or_404(queue_id)

    if current_user.role != 0 and current_user not in queue.admins and current_user not in queue.members:
        abort(403)

    equipment = Equipment.query.filter_by(queue_id=queue.id).order_by(
        Equipment.equipment_name.asc(),
        Equipment.id.asc()
    ).all()

    return render_template(
        'queue_equipment.html',
        queue=queue,
        equipment=equipment
    )


EQUIPMENT_COLUMNS = [
    ("Наименование", "equipment_name"),
    ("Модель", "model"),
    ("Заводской номер", "serial_number"),
    ("Инвентарный номер", "inventory_number"),
    ("Год выпуска", "production_year"),
    ("Год ввода в эксплуатацию", "commissioning_year"),
    ("Период обслуживания", "maintenance_period"),
    ("Сертификат", "certificate"),
    ("Регистрационное удостоверение", "registration_certificate"),
]


def _equipment_value(value):
    if value is None:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    value = str(value).strip()
    return value or None


def _equipment_key(value):
    value = _equipment_value(value)
    return re.sub(r"\\s+", " ", value).strip().casefold() if value else None


@q_detail_bp.route('/queue/<int:queue_id>/equipment/template')
@login_required
def download_equipment_template(queue_id):
    queue = Queue.query.get_or_404(queue_id)
    if current_user.role != 0 and current_user not in queue.admins and current_user not in queue.members:
        abort(403)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Оборудование"
    headers = [label for label, _ in EQUIPMENT_COLUMNS]
    sheet.append(headers)
    sheet.append([None] * len(headers))
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{chr(64 + len(headers))}2"
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="34445B")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 34
    widths = [34, 22, 22, 22, 16, 23, 24, 24, 34]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width
    table = Table(displayName="EquipmentImport", ref=f"A1:{chr(64 + len(headers))}2")
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False
    )
    sheet.add_table(table)

    instructions = workbook.create_sheet("Инструкция")
    instructions.append(["Как заполнять шаблон"])
    instructions.append(["Заполняйте данные на листе «Оборудование», начиная со второй строки."])
    instructions.append(["Не изменяйте названия столбцов. Пустые строки будут пропущены."])
    instructions.append(["При совпадении инвентарного или заводского номера запись обновится."])
    instructions.append(["Если оба номера не указаны, совпадение определяется по наименованию и модели."])
    instructions.append(["Пустые ячейки при обновлении не стирают ранее сохранённые значения."])
    instructions.column_dimensions["A"].width = 110
    instructions["A1"].font = Font(bold=True, size=14, color="FFFFFF")
    instructions["A1"].fill = PatternFill("solid", fgColor="34445B")
    for row in instructions.iter_rows(min_row=2):
        row[0].alignment = Alignment(wrap_text=True, vertical="top")
    instructions.freeze_panes = "A2"

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name=f"Шаблон_импорта_оборудования_{queue.id}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@q_detail_bp.route('/queue/<int:queue_id>/equipment/import', methods=['POST'])
@login_required
def import_queue_equipment(queue_id):
    queue = Queue.query.get_or_404(queue_id)
    if current_user.role != 0 and current_user not in queue.admins:
        abort(403)

    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        flash("Выберите заполненный Excel-файл.", "danger")
        return redirect(url_for("queue_detail.queue_equipment", queue_id=queue.id))
    if not uploaded.filename.lower().endswith(".xlsx"):
        flash("Поддерживается только формат .xlsx.", "danger")
        return redirect(url_for("queue_detail.queue_equipment", queue_id=queue.id))

    created = updated = skipped = 0
    try:
        workbook = load_workbook(uploaded, read_only=True, data_only=True)
        if "Оборудование" not in workbook.sheetnames:
            raise ValueError("В файле нет листа «Оборудование».")
        sheet = workbook["Оборудование"]
        expected_headers = [label for label, _ in EQUIPMENT_COLUMNS]
        actual_headers = [_equipment_value(cell) for cell in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))]
        if actual_headers[:len(expected_headers)] != expected_headers:
            raise ValueError("Столбцы шаблона изменены или расположены не в том порядке. Скачайте актуальный шаблон.")

        existing = Equipment.query.filter_by(queue_id=queue.id).all()
        by_inventory = {}
        by_serial = {}
        by_name_model = {}
        for item in existing:
            inv = _equipment_key(item.inventory_number)
            serial = _equipment_key(item.serial_number)
            name_model = (_equipment_key(item.equipment_name), _equipment_key(item.model))
            if inv:
                by_inventory.setdefault(inv, item)
            if serial:
                by_serial.setdefault(serial, item)
            if all(name_model):
                by_name_model.setdefault(name_model, item)

        for values in sheet.iter_rows(min_row=2, values_only=True):
            row = list(values[:len(EQUIPMENT_COLUMNS)])
            row += [None] * (len(EQUIPMENT_COLUMNS) - len(row))
            data = {field: _equipment_value(value) for (_, field), value in zip(EQUIPMENT_COLUMNS, row)}
            if not any(data.values()):
                skipped += 1
                continue

            inventory_key = _equipment_key(data["inventory_number"])
            serial_key = _equipment_key(data["serial_number"])
            name_model_key = (_equipment_key(data["equipment_name"]), _equipment_key(data["model"]))
            item = (by_inventory.get(inventory_key) if inventory_key else None)
            if item is None and serial_key:
                item = by_serial.get(serial_key)
            if item is None and not inventory_key and not serial_key and all(name_model_key):
                item = by_name_model.get(name_model_key)

            if item is None:
                item = Equipment(queue_id=queue.id, **data)
                db.session.add(item)
                db.session.flush()
                created += 1
            else:
                for field, value in data.items():
                    if value is not None:
                        setattr(item, field, value)
                updated += 1

            inv = _equipment_key(item.inventory_number)
            serial = _equipment_key(item.serial_number)
            name_model = (_equipment_key(item.equipment_name), _equipment_key(item.model))
            if inv:
                by_inventory[inv] = item
            if serial:
                by_serial[serial] = item
            if all(name_model):
                by_name_model[name_model] = item

        db.session.commit()
        flash(f"Импорт завершён: добавлено — {created}, обновлено — {updated}, пустых строк пропущено — {skipped}.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Ошибка импорта оборудования для очереди %s", queue.id)
        flash("Не удалось прочитать файл или сохранить данные. Проверьте шаблон и содержимое файла.", "danger")
    finally:
        if "workbook" in locals():
            workbook.close()

    return redirect(url_for("queue_detail.queue_equipment", queue_id=queue.id))
