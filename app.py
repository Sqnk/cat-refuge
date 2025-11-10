import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect
from werkzeug.utils import secure_filename

# --- Configuration de base ---
app = Flask(__name__)

# Render n'autorise pas /var/data, donc on stocke en local dans /tmp ou /app/uploads
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///cats.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- Définition des modèles ---
class Cat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    birthdate = db.Column(db.Date)
    status = db.Column(db.String(50))
    photo_filename = db.Column(db.String(200))

    vaccinations = db.relationship('Vaccination', backref='cat', lazy=True)
    notes = db.relationship('Note', backref='cat', lazy=True)
    appointments = db.relationship('AppointmentCat', back_populates='cat')


class VaccineType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    vaccinations = db.relationship('Vaccination', backref='vaccine_type', lazy=True)


class Vaccination(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cat_id = db.Column(db.Integer, db.ForeignKey('cat.id'), nullable=False)
    vaccine_type_id = db.Column(db.Integer, db.ForeignKey('vaccine_type.id'), nullable=False)
    date = db.Column(db.Date, default=datetime.utcnow)
    lot = db.Column(db.String(100))
    veterinarian = db.Column(db.String(100))
    reaction = db.Column(db.String(255))


class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cat_id = db.Column(db.Integer, db.ForeignKey('cat.id'), nullable=False)
    content = db.Column(db.Text)
    file_name = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)


class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, nullable=False)
    location = db.Column(db.String(200))
    employees = db.relationship('AppointmentEmployee', back_populates='appointment')
    cats = db.relationship('AppointmentCat', back_populates='appointment')


class AppointmentEmployee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'))
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'))
    appointment = db.relationship('Appointment', back_populates='employees')
    employee = db.relationship('Employee')


class AppointmentCat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'))
    cat_id = db.Column(db.Integer, db.ForeignKey('cat.id'))
    appointment = db.relationship('Appointment', back_populates='cats')
    cat = db.relationship('Cat', back_populates='appointments')


# --- Initialisation automatique de la base ---
with app.app_context():
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    if not tables:
        print("🔧 Initialisation de la base de données (premier lancement)...")
        db.create_all()

        # Données par défaut
        for name in ['Typhus', 'Coryza', 'Leucose']:
            db.session.add(VaccineType(name=name))
        for emp in ['Alice', 'Bob']:
            db.session.add(Employee(name=emp))
        db.session.commit()
        print("✅ Base de données initialisée avec succès.")


# --- Routes principales ---
@app.route('/')
def index():
    cats = Cat.query.all()
    alerts = []
    return render_template('index.html', cats=cats, alerts=alerts)


@app.route('/dashboard')
def dashboard():
    total_cats = Cat.query.count()
    total_appointments = Appointment.query.count()
    total_employees = Employee.query.count()
    total_vaccines = VaccineType.query.count()

    return render_template('dashboard.html',
                           total_cats=total_cats,
                           total_appointments=total_appointments,
                           total_employees=total_employees,
                           total_vaccines=total_vaccines)


@app.route('/calendrier')
def calendrier():
    return render_template('calendrier.html')


# --- API Chats ---
@app.route('/api/cats', methods=['GET', 'POST'])
def api_cats():
    if request.method == 'POST':
        name = request.form['name']
        status = request.form.get('status')
        birthdate_str = request.form.get('birthdate')
        photo = request.files.get('photo')

        birthdate = datetime.strptime(birthdate_str, '%Y-%m-%d').date() if birthdate_str else None
        filename = None
        if photo:
            filename = secure_filename(photo.filename)
            photo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        cat = Cat(name=name, status=status, birthdate=birthdate, photo_filename=filename)
        db.session.add(cat)
        db.session.commit()
        return redirect(url_for('index'))

    cats = Cat.query.order_by(Cat.name).all()
    return jsonify([{
        "id": c.id,
        "name": c.name,
        "status": c.status,
        "birthdate": c.birthdate.isoformat() if c.birthdate else None,
        "photo": c.photo_filename
    } for c in cats])


# --- API Rendez-vous ---
@app.route('/api/appointments', methods=['GET', 'POST'])
def api_appointments():
    if request.method == 'POST':
        date_str = request.form['date']
        location = request.form['location']
        date = datetime.strptime(date_str, '%Y-%m-%dT%H:%M')
        appointment = Appointment(date=date, location=location)
        db.session.add(appointment)
        db.session.commit()
        return jsonify({"success": True})

    appointments = Appointment.query.all()
    return jsonify({
        "count": len(appointments),
        "items": [
            {
                "id": a.id,
                "date_db": a.date.strftime('%Y-%m-%d %H:%M:%S'),
                "date_iso": a.date.isoformat(),
                "location": a.location,
                "cats": [ac.cat.name for ac in a.cats],
                "employees": [ae.employee.name for ae in a.employees]
            } for a in appointments
        ]
    })


@app.route('/api/employees', methods=['GET', 'POST'])
def api_employees():
    if request.method == 'POST':
        name = request.form['name']
        emp = Employee(name=name)
        db.session.add(emp)
        db.session.commit()
        return jsonify({"success": True})

    emps = Employee.query.all()
    return jsonify([{"id": e.id, "name": e.name} for e in emps])


@app.route('/api/vaccines', methods=['GET', 'POST'])
def api_vaccines():
    if request.method == 'POST':
        name = request.form['name']
        v = VaccineType(name=name)
        db.session.add(v)
        db.session.commit()
        return jsonify({"success": True})
    vaccines = VaccineType.query.all()
    return jsonify([{"id": v.id, "name": v.name} for v in vaccines])


# --- Lancement ---
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
