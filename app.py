import os
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta

from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    jsonify, send_from_directory
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename

# ====================== Configuration compatible Render Free ======================

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Sur Render (plan gratuit), on persiste dans ./data
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "cats.db")
UPLOAD_FOLDER = os.path.join(DATA_DIR, "uploads")
try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
except PermissionError:
    # fallback si jamais
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.secret_key = os.environ.get("SECRET_KEY", "change_me")

db = SQLAlchemy(app)

# ====================== Tables d'association (Many-to-Many) ======================

appointment_cats = db.Table(
    'appointment_cats',
    db.Column('appointment_id', db.Integer, db.ForeignKey('appointment.id'), primary_key=True),
    db.Column('cat_id', db.Integer, db.ForeignKey('cat.id'), primary_key=True)
)

appointment_employees = db.Table(
    'appointment_employees',
    db.Column('appointment_id', db.Integer, db.ForeignKey('appointment.id'), primary_key=True),
    db.Column('employee_id', db.Integer, db.ForeignKey('employee.id'), primary_key=True)
)

# ====================== Modèles ======================

class Cat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    birthdate = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(30), nullable=False, default='normal')
    photo_filename = db.Column(db.String(200), nullable=True)

    notes = db.relationship('Note', backref='cat', cascade='all, delete-orphan', order_by='desc(Note.timestamp)')
    vaccines = db.relationship('VaccineRecord', backref='cat', cascade='all, delete-orphan')
    appointments = db.relationship('Appointment', secondary=appointment_cats, back_populates='cats')

    def age_str(self):
        if not self.birthdate:
            return "Inconnu"
        rd = relativedelta(date.today(), self.birthdate)
        parts = []
        if rd.years:
            parts.append(f"{rd.years} an{'s' if rd.years > 1 else ''}")
        if rd.months:
            parts.append(f"{rd.months} mois")
        return ", ".join(parts) if parts else "0 mois"


class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(120), nullable=True)
    appointments = db.relationship('Appointment', secondary=appointment_employees, back_populates='employees')


class VaccineType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)


class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cat_id = db.Column(db.Integer, db.ForeignKey('cat.id'), nullable=False)
    author = db.Column(db.String(120), nullable=False)
    content = db.Column(db.Text, nullable=False)
    attachment_filename = db.Column(db.String(300), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


class VaccineRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cat_id = db.Column(db.Integer, db.ForeignKey('cat.id'), nullable=False)
    vaccine_name = db.Column(db.String(200), nullable=False)  # texte libre (lié à VaccineType côté UI)
    date_given = db.Column(db.Date, nullable=False)
    lot = db.Column(db.String(120), nullable=True)
    vet_name = db.Column(db.String(200), nullable=True)
    reaction = db.Column(db.String(300), nullable=True)

    def next_due(self):
        try:
            return self.date_given + relativedelta(years=1)
        except Exception:
            return None


class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    location = db.Column(db.String(200), nullable=True)
    date = db.Column(db.DateTime, nullable=False)
    notes = db.Column(db.String(500), nullable=True)
    cats = db.relationship('Cat', secondary=appointment_cats, back_populates='appointments')
    employees = db.relationship('Employee', secondary=appointment_employees, back_populates='appointments')

# ====================== Helpers ======================

DEFAULT_VACCINES = ['Rage', 'Typhus', 'Leucose', 'Coryza', 'Chlamydiose']

def get_vaccine_types():
    names = [v.name for v in VaccineType.query.order_by(VaccineType.name).all()]
    if not names:
        for n in DEFAULT_VACCINES:
            db.session.add(VaccineType(name=n))
        db.session.commit()
        names = [v.name for v in VaccineType.query.order_by(VaccineType.name).all()]
    return names

def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}

def allowed_pdf(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'pdf'}

def compute_vaccine_alerts(days=30):
    """
    Retourne {'overdue': [...], 'due_soon': [...]}.
    IMPORTANT : ne considère QUE les types pour lesquels le chat a AU MOINS un enregistrement.
    """
    today = date.today()
    end = today + timedelta(days=days)
    overdue, due_soon = [], []

    def latest_for_type(cat, vname):
        recs = [v for v in cat.vaccines if v.vaccine_name == vname]
        return max(recs, key=lambda r: r.date_given) if recs else None

    for cat in Cat.query.all():
        existing_types = {v.vaccine_name for v in cat.vaccines}
        for vname in existing_types:
            latest = latest_for_type(cat, vname)
            if not latest:
                continue
            nd = latest.next_due()
            if not nd:
                continue
            item = {'cat': cat, 'vaccine': vname, 'last_date': latest.date_given, 'next_due': nd}
            if nd < today:
                overdue.append(item)
            elif today <= nd <= end:
                due_soon.append(item)
    return {'overdue': overdue, 'due_soon': due_soon}

def cat_missing_vaccine_q(query, vaccine_name):
    """
    Filtre 'vaccin manquant' = pas de dose dans les 12 derniers mois (inclut les "jamais vaccinés").
    """
    one_year_ago = date.today() - relativedelta(years=1)
    sub = VaccineRecord.query.filter(
        VaccineRecord.vaccine_name == vaccine_name,
        VaccineRecord.date_given >= one_year_ago,
        VaccineRecord.cat_id == Cat.id
    ).exists()
    return query.filter(~sub)

# ====================== Fichiers upload ======================

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ====================== Accueil + API recherche ======================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/cats')
def api_cats():
    q = request.args.get('q', '').strip()
    query = Cat.query
    if q:
        query = query.filter(Cat.name.ilike(f"%{q}%"))
    cats = query.order_by(Cat.name).all()
    return jsonify([
        {
            'id': c.id,
            'name': c.name,
            'age': c.age_str(),
            'photo': url_for('uploaded_file', filename=c.photo_filename) if c.photo_filename else None
        } for c in cats
    ])

# ====================== Chats: création / liste / vue / édition / suppression ======================

@app.route('/chats/new', methods=['GET', 'POST'])
def new_cat():
    if request.method == 'POST':
        name = request.form.get('name')
        birthdate = request.form.get('birthdate') or None
        status = request.form.get('status') or 'normal'
        photo = request.files.get('photo')
        filename = None
        if photo and allowed_image(photo.filename):
            filename = secure_filename(f"{int(datetime.utcnow().timestamp())}_{photo.filename}")
            photo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        cat = Cat(
            name=name,
            status=status,
            birthdate=datetime.strptime(birthdate, '%Y-%m-%d').date() if birthdate else None,
            photo_filename=filename
        )
        db.session.add(cat)
        db.session.commit()
        flash("Chat ajouté", "success")
        return redirect(url_for('view_cat', cat_id=cat.id))
    return render_template('chat_new.html', statuses=['normal', 'adoptable', 'en_soin', 'quarantaine'])

@app.route('/chats')
def chats_list():
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    missing_vaccine = request.args.get('missing_vaccine', '').strip()
    vet_filter = request.args.get('vet', '').strip()

    query = Cat.query
    if q:
        query = query.filter(Cat.name.ilike(f"%{q}%"))
    if status:
        query = query.filter(Cat.status == status)
    if missing_vaccine:
        query = cat_missing_vaccine_q(query, missing_vaccine)
    if vet_filter:
        from sqlalchemy import or_
        v_exists = VaccineRecord.query.filter(
            VaccineRecord.vet_name.ilike(f"%{vet_filter}%"),
            VaccineRecord.cat_id == Cat.id
        ).exists()
        a_exists = db.session.query(appointment_cats) \
            .filter(appointment_cats.c.cat_id == Cat.id) \
            .join(Appointment, Appointment.id == appointment_cats.c.appointment_id) \
            .filter(Appointment.location.ilike(f"%{vet_filter}%")).exists()
        query = query.filter(or_(v_exists, a_exists))

    cats = query.order_by(Cat.name).all()
    return render_template('chats.html', cats=cats,
                           statuses=['normal', 'adoptable', 'en_soin', 'quarantaine'],
                           vaccine_types=get_vaccine_types(),
                           cur_status=status, cur_missing=missing_vaccine, cur_vet=vet_filter, q=q)

@app.route('/chats/<int:cat_id>')
def view_cat(cat_id):
    cat = Cat.query.get_or_404(cat_id)
    last3 = sorted(cat.vaccines, key=lambda v: v.date_given, reverse=True)[:3]
    return render_template('chat.html', cat=cat, last_vaccines=last3,
                           vaccine_types=get_vaccine_types(),
                           statuses=['normal', 'adoptable', 'en_soin', 'quarantaine'])

@app.route('/chats/<int:cat_id>/edit', methods=['GET', 'POST'])
def edit_cat(cat_id):
    cat = Cat.query.get_or_404(cat_id)
    if request.method == 'POST':
        cat.name = request.form.get('name')
        cat.status = request.form.get('status') or 'normal'
        b = request.form.get('birthdate') or None
        cat.birthdate = datetime.strptime(b, '%Y-%m-%d').date() if b else None
        photo = request.files.get('photo')
        if photo and allowed_image(photo.filename):
            filename = secure_filename(f"{int(datetime.utcnow().timestamp())}_{photo.filename}")
            photo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            cat.photo_filename = filename
        db.session.commit()
        flash("Chat mis à jour", "success")
        return redirect(url_for('view_cat', cat_id=cat.id))
    return render_template('chat_edit.html', cat=cat,
                           statuses=['normal', 'adoptable', 'en_soin', 'quarantaine'])

@app.route('/chats/<int:cat_id>/delete', methods=['POST'])
def delete_cat(cat_id):
    cat = Cat.query.get_or_404(cat_id)
    db.session.delete(cat)
    db.session.commit()
    flash("Chat supprimé", "success")
    return redirect(url_for('index'))

# ====================== Notes: ajout / édition / suppression ======================

def secure_filename(name):
    return werkzeug_secure(name) if 'werkzeug_secure' in globals() else name

# (Werkzeug secure_filename déjà importé en haut sous le nom secure_filename)

@app.route('/chats/<int:cat_id>/notes/add', methods=['POST'])
def add_note(cat_id):
    cat = Cat.query.get_or_404(cat_id)
    author = request.form.get('author') or 'Anonyme'
    content = request.form.get('content') or ''
    attach = request.files.get('attachment')
    filename = None
    if attach and (allowed_image(attach.filename) or allowed_pdf(attach.filename)):
        filename = secure_filename(f"{int(datetime.utcnow().timestamp())}_{attach.filename}")
        attach.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    n = Note(cat=cat, author=author, content=content, attachment_filename=filename)
    db.session.add(n)
    db.session.commit()
    flash("Note ajoutée", "success")
    return redirect(url_for('view_cat', cat_id=cat.id))

@app.route('/notes/<int:note_id>/edit', methods=['GET', 'POST'])
def edit_note(note_id):
    n = Note.query.get_or_404(note_id)
    if request.method == 'POST':
        n.author = request.form.get('author') or n.author
        n.content = request.form.get('content') or n.content
        attach = request.files.get('attachment')
        if attach and (allowed_image(attach.filename) or allowed_pdf(attach.filename)):
            filename = secure_filename(f"{int(datetime.utcnow().timestamp())}_{attach.filename}")
            attach.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            n.attachment_filename = filename
        db.session.commit()
        flash("Note mise à jour", "success")
        return redirect(url_for('view_cat', cat_id=n.cat_id))
    return render_template('note_edit.html', note=n)

@app.route('/notes/<int:note_id>/delete', methods=['POST'])
def delete_note(note_id):
    n = Note.query.get_or_404(note_id)
    cat_id = n.cat_id
    if n.attachment_filename:
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], n.attachment_filename))
        except Exception:
            pass
    db.session.delete(n)
    db.session.commit()
    flash("Note supprimée", "success")
    return redirect(url_for('view_cat', cat_id=cat_id))

# ====================== Vaccins: types + enregistrements ======================

@app.route('/vaccines', methods=['GET', 'POST'])
def vaccines():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        if name:
            exists = VaccineType.query.filter(VaccineType.name.ilike(name)).first()
            if exists:
                flash("Ce vaccin existe déjà.", "warning")
            else:
                db.session.add(VaccineType(name=name))
                db.session.commit()
                flash("Vaccin ajouté.", "success")
        return redirect(url_for('vaccines'))
    items = VaccineType.query.order_by(VaccineType.name).all()
    return render_template('vaccines.html', vaccines=items)

@app.route('/vaccines/<int:vid>/delete', methods=['POST'])
def delete_vaccine_type(vid):
    v = VaccineType.query.get_or_404(vid)
    db.session.delete(v)
    db.session.commit()
    flash("Vaccin supprimé de la liste.", "success")
    return redirect(url_for('vaccines'))

@app.route('/chats/<int:cat_id>/vaccines/add', methods=['POST'])
def add_vaccine(cat_id):
    cat = Cat.query.get_or_404(cat_id)
    vname = request.form.get('vaccine_name')
    d = request.form.get('date_given')
    lot = request.form.get('lot')
    vet_name = request.form.get('vet_name')
    reaction = request.form.get('reaction')
    if vname and d:
        vr = VaccineRecord(
            cat=cat, vaccine_name=vname,
            date_given=datetime.strptime(d, '%Y-%m-%d').date(),
            lot=lot, vet_name=vet_name, reaction=reaction
        )
        db.session.add(vr)
        db.session.commit()
        flash("Vaccin enregistré", "success")
    return redirect(url_for('view_cat', cat_id=cat.id))

# ====================== Employés ======================

@app.route('/employees', methods=['GET', 'POST'])
def employees():
    if request.method == 'POST':
        name = request.form.get('name')
        role = request.form.get('role')
        if name:
            db.session.add(Employee(name=name, role=role))
            db.session.commit()
            flash("Employé ajouté", "success")
        return redirect(url_for('employees'))
    emps = Employee.query.order_by(Employee.name).all()
    return render_template('employees.html', employees=emps)

@app.route('/employees/<int:emp_id>/delete', methods=['POST'])
def delete_employee(emp_id):
    e = Employee.query.get_or_404(emp_id)
    db.session.delete(e)
    db.session.commit()
    flash("Employé supprimé", "success")
    return redirect(url_for('employees'))

# ====================== Rendez-vous (liste / création / édition / suppression) ======================

@app.route('/appointments')
def appointments_page():
    now = datetime.utcnow()
    upcoming = Appointment.query.filter(Appointment.date >= now).order_by(Appointment.date).all()
    past = Appointment.query.filter(Appointment.date < now).order_by(Appointment.date.desc()).all()
    cats = Cat.query.order_by(Cat.name).all()
    emps = Employee.query.order_by(Employee.name).all()
    return render_template('appointments.html', upcoming=upcoming, past=past, cats=cats, employees=emps)

@app.route('/appointments/new', methods=['GET', 'POST'])
def new_appointment():
    if request.method == 'POST':
        location = request.form.get('location')
        date_str = request.form.get('date')
        time_str = request.form.get('time')
        notes = request.form.get('notes')
        cat_ids = request.form.getlist('cat_ids')
        emp_ids = request.form.getlist('employee_ids')
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        appt = Appointment(location=location, date=dt, notes=notes)
        for cid in cat_ids:
            c = Cat.query.get(int(cid))
            if c:
                appt.cats.append(c)
        for eid in emp_ids:
            e = Employee.query.get(int(eid))
            if e:
                appt.employees.append(e)
        db.session.add(appt)
        db.session.commit()
        flash("Rendez-vous ajouté", "success")
        return redirect(url_for('appointments_page'))
    default_dt = datetime.now() + timedelta(hours=2)
    return render_template(
        'appointment_new.html',
        cats=Cat.query.order_by(Cat.name).all(),
        employees=Employee.query.order_by(Employee.name).all(),
        default_date=default_dt.strftime('%Y-%m-%d'),
        default_time=default_dt.strftime('%H:%M')
    )

@app.route('/appointments/<int:appt_id>/edit', methods=['GET', 'POST'])
def edit_appointment(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    if request.method == 'POST':
        appt.location = request.form.get('location')
        appt.notes = request.form.get('notes')
        date_str = request.form.get('date')
        time_str = request.form.get('time')
        appt.date = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        # cats
        appt.cats = []
        for cid in request.form.getlist('cat_ids'):
            c = Cat.query.get(int(cid))
            if c:
                appt.cats.append(c)
        # employees
        appt.employees = []
        for eid in request.form.getlist('employee_ids'):
            e = Employee.query.get(int(eid))
            if e:
                appt.employees.append(e)
        db.session.commit()
        flash("Rendez-vous mis à jour", "success")
        return redirect(url_for('appointments_page'))
    cats = Cat.query.order_by(Cat.name).all()
    emps = Employee.query.order_by(Employee.name).all()
    return render_template('appointment_edit.html', appt=appt, cats=cats, employees=emps)

@app.route('/appointments/<int:appt_id>/delete', methods=['POST'])
def delete_appointment(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    db.session.delete(appt)
    db.session.commit()
    flash("Rendez-vous supprimé", "success")
    return redirect(url_for('appointments_page'))

# ====================== Calendrier ======================

@app.route('/calendar')
def calendar_view():
    return render_template('calendar.html')

@app.route('/api/appointments', methods=['GET', 'POST', 'PATCH'])
def api_appointments():
    if request.method == 'GET':
        appts = Appointment.query.order_by(Appointment.date).all()
        events = []
        for a in appts:
            title = a.location or "Rendez-vous"
            events.append({
                "id": a.id,
                "title": title,  # on n’affiche que le lieu ; détails via tooltip (extendedProps)
                "start": a.date.strftime('%Y-%m-%dT%H:%M:%S'),
                "allDay": False,
                "url": url_for('edit_appointment', appt_id=a.id),
                "extendedProps": {
                    "time": a.date.strftime('%d/%m/%Y %H:%M'),
                    "cats": [c.name for c in a.cats],
                    "employees": [e.name for e in a.employees],
                    "notes": a.notes or ""
                }
            })
        return jsonify(events)

    if request.method == 'POST':
        # création par sélection de plage (drag select) dans FullCalendar
        start = request.json.get('start')
        try:
            dt = datetime.fromisoformat(start.replace('Z', ''))
        except ValueError:
            dt = datetime.strptime(start, '%Y-%m-%dT%H:%M:%S')
        appt = Appointment(date=dt, location="(à préciser)", notes=None)
        db.session.add(appt)
        db.session.commit()
        return jsonify({"ok": True, "id": appt.id}), 201

    if request.method == 'PATCH':
        # drag & drop d’un event -> mise à jour date/heure
        appt_id = request.json.get('id')
        start = request.json.get('start')
        appt = Appointment.query.get_or_404(appt_id)
        try:
            dt = datetime.fromisoformat(start.replace('Z', ''))
        except ValueError:
            dt = datetime.strptime(start, '%Y-%m-%dT%H:%M:%S')
        appt.date = dt
        db.session.commit()
        return jsonify({"ok": True})

# ====================== Recherche plein texte dans les notes ======================

@app.route('/search/notes')
def search_notes():
    q = request.args.get('q', '').strip()
    results = []
    if q:
        results = Note.query.filter(
            (Note.content.ilike(f"%{q}%")) | (Note.author.ilike(f"%{q}%"))
        ).order_by(Note.timestamp.desc()).all()
    return render_template('search_notes.html', q=q, results=results)

# ====================== Dashboard ======================

@app.route('/dashboard')
def dashboard():
    alerts = compute_vaccine_alerts(30)
    upcoming = Appointment.query.filter(Appointment.date >= datetime.utcnow()).order_by(Appointment.date).all()
    stats = {
        "cats": Cat.query.count(),
        "appointments": Appointment.query.count(),
        "vaccines_due": len(alerts['due_soon']) + len(alerts['overdue'])
    }
    return render_template('dashboard.html',
                           upcoming=upcoming,
                           alerts=alerts,
                           stats=stats)

# ====================== Initialisation DB au démarrage ======================

def init_db():
    with app.app_context():
        db.create_all()
        if VaccineType.query.count() == 0:
            for n in DEFAULT_VACCINES:
                db.session.add(VaccineType(name=n))
            db.session.commit()

# Important : initialiser à l'import (utile sous Gunicorn/Render)
init_db()

# ====================== Lancement local ======================

if __name__ == "__main__":
    app.run(debug=True)
