import os
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename

# ================== Chemins compatibles Render (disque persistant) ==================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.environ.get('DATA_DIR', BASE_DIR)

DB_PATH = os.path.join(DATA_DIR, 'cats.db')
UPLOAD_FOLDER = os.path.join(DATA_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + DB_PATH
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.secret_key = os.environ.get('SECRET_KEY', 'change_me_in_production')

db = SQLAlchemy(app)
# ===============================================================================

# ====================== Associations Many-to-Many ======================
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
        today = date.today()
        rd = relativedelta(today, self.birthdate)
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
    vaccine_name = db.Column(db.String(200), nullable=False)
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
            if nd < today:
                overdue.append({'cat': cat, 'vaccine': vname, 'last_date': latest.date_given, 'next_due': nd})
            elif today <= nd <= end:
                due_soon.append({'cat': cat, 'vaccine': vname, 'last_date': latest.date_given, 'next_due': nd})
    return {'overdue': overdue, 'due_soon': due_soon}

# ====================== Routes principales (abrégées ici pour clarté) ======================
# 👉 Remets tes routes ici : chats, notes, vaccins, employés, rendez-vous, calendrier, dashboard, etc.
# (elles ne changent pas du tout — seule la fin du fichier est modifiée)

# ====================== Main / Initialisation DB ======================
def init_db():
    """Création des tables et seed des types de vaccins (marche en local et sous Gunicorn)."""
    with app.app_context():
        db.create_all()
        if VaccineType.query.count() == 0:
            for n in DEFAULT_VACCINES:
                db.session.add(VaccineType(name=n))
            db.session.commit()

# On initialise la DB même si Flask tourne sous Gunicorn (Render)
init_db()

if __name__ == '__main__':
    app.run(debug=True)
