from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, current_user, logout_user
from datetime import datetime
import os
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv
from supabase import create_client, Client
from werkzeug.security import generate_password_hash, check_password_hash

# Cargar variables de entorno
load_dotenv()

# Configuración de la aplicación
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')

# Crear cliente de Supabase
url = os.getenv('SUPABASE_URL')
key = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(url, key)

# Configuración de LoginManager
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Modelo de Usuario para autenticación con LoginManager
class User(UserMixin):
    def __init__(self, id, nombre, email, es_admin):
        self.id = id
        self.nombre = nombre
        self.email = email
        self.es_admin = es_admin

@login_manager.user_loader
def load_user(user_id):
    user_data = supabase.table('user').select('*').eq('id', user_id).execute()
    if user_data.data:
        user = user_data.data[0]
        return User(user['id'], user['nombre'], user['email'], user['es_admin'])
    return None

# Funciones auxiliares
def enviar_correo(destinatario, asunto, mensaje):
    try:
        msg = MIMEText(mensaje)
        msg['Subject'] = asunto
        msg['From'] = os.getenv('EMAIL_USER')
        msg['To'] = destinatario

        with smtplib.SMTP(os.getenv('EMAIL_HOST'), os.getenv('EMAIL_PORT')) as server:
            server.starttls()
            server.login(os.getenv('EMAIL_USER'), os.getenv('EMAIL_PASSWORD'))
            server.sendmail(os.getenv('EMAIL_USER'), [destinatario], msg.as_string())
    except Exception as e:
        print(f"Error enviando correo: {e}")

# Rutas de usuario
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user_data = supabase.table('user').select('*').eq('email', email).execute()
        user = user_data.data[0] if user_data.data else None
        
        if user and check_password_hash(user['password'], password):
            login_user(User(user['id'], user['nombre'], user['email'], user['es_admin']))
            return redirect(url_for('index'))
        else:
            flash('Las credenciales son incorrectas.')
    
    return render_template('login.html')

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        email = request.form.get('email')
        password = request.form.get('password')

        if len(nombre) > 255 or len(email) > 255 or len(password) > 255:
            flash('El nombre, el email o la contraseña son demasiado largos. Los límites son 255 caracteres.')
            return render_template('registro.html')

        hashed_password = generate_password_hash(password)

        user_data = supabase.table('user').insert({
            'nombre': nombre,
            'email': email,
            'password': hashed_password,
            'es_admin': False
        }).execute()

        if user_data.data is not None:
            flash('Registro exitoso. Puedes iniciar sesión ahora.')
            return redirect(url_for('login'))
        else:
            if user_data.error:
                flash(f'Error al registrar el usuario: {user_data.error["message"]}')
            else:
                flash('Error desconocido al registrar el usuario.')

    return render_template('registro.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

# Rutas de clase
@app.route('/')
def index():
    hoy = datetime.now().date()  # Obtener la fecha actual sin la hora
    clases_data = supabase.table('clase').select('*').filter('horario', 'gte', hoy).order('horario').execute()
    clases = clases_data.data

    clases_disponibles = False

    for clase in clases:
        reservas_count = supabase.table('reserva').select('count(*)').eq('clase_id', clase['id']).execute()
        clase['ocupacion'] = reservas_count.data[0]['count'] if reservas_count.data else 0
        clase['cupos_disponibles'] = clase['cupos_maximos'] - clase['ocupacion']
        
        if clase['cupos_disponibles'] > 0:
            clases_disponibles = True

    # Pasar 'hoy' a la plantilla
    return render_template('index.html', clases=clases, clases_disponibles=clases_disponibles, hoy=hoy) 

@app.route('/get_clases_data')
@login_required
def get_clases_data():
    hoy = datetime.now().date()
    clases_data = supabase.table('clase').select('*').filter('horario', 'gte', hoy).order('horario').execute()
    clases = clases_data.data

    clases_disponibles = False

    for clase in clases:
        reservas_count = supabase.table('reserva').select('count(*)').eq('clase_id', clase['id']).execute()
        clase['ocupacion'] = reservas_count.data[0]['count'] if reservas_count.data else 0
        clase['cupos_disponibles'] = clase['cupos_maximos'] - clase['ocupacion']

        if clase['cupos_disponibles'] > 0:
            clases_disponibles = True

    if clases_disponibles:
        return jsonify(clases)
    else:
        return jsonify({"message": "Todas las clases están llenas."}), 200

@app.route('/reservar/<int:clase_id>', methods=['POST'])
@login_required
def reservar(clase_id):
    if clase_id <= 0:
        return jsonify({"error": "ID de clase inválido."}), 400

    clase_data = supabase.table('clase').select('*').eq('id', clase_id).execute()
    clase = clase_data.data[0] if clase_data.data else None
    
    if not clase:
        return jsonify({"error": "Clase no encontrada."}), 404
    
    hoy = datetime.now()

    # Verifica si es un día válido para reservar (domingo a jueves)
    if hoy.weekday() in [4, 5]:  # 4: Viernes, 5: Sábado
        if hoy.weekday() == 4 or (hoy.weekday() == 5 and hoy.hour < 18):
            return jsonify({"error": "Hoy el Box permanece cerrado, podrás reservar a partir del domingo por la tarde."}), 400

    # Verificar si ya existe una reserva para este usuario en esta clase
    reserva_existente = supabase.table('reserva').select('*').eq('usuario_id', current_user.id).eq('clase_id', clase_id).execute()
    
    if reserva_existente.data:
        return jsonify({"error": "Ya tienes una reserva para esta clase."}), 400
    
    # Obtener la cantidad actual de reservas para esa clase
    reservas_data = supabase.table('reserva').select('count(*)').eq('clase_id', clase_id).execute()
    reservas_count = reservas_data.data[0]['count'] if reservas_data.data and reservas_data.data[0]['count'] is not None else 0
    
    if reservas_count >= clase['cupos_maximos']:
        return jsonify({"error": "Lo sentimos, esta clase está llena."}), 400
    
    nueva_reserva = {
        'usuario_id': current_user.id,
        'clase_id': clase_id
    }
    supabase.table('reserva').insert(nueva_reserva).execute()
    
    enviar_correo(
        current_user.email,
        "Reserva confirmada",
        f"Has reservado la clase del {clase['horario'].strftime('%d/%m a las %H:%M')}."
    )
    
    return jsonify({"message": "Reserva realizada con éxito."}), 200

@app.route('/clases')
@login_required
def clases():
    clases_data = supabase.table('clase').select('*').execute()
    clases = clases_data.data

    # Llamar a la función que cuenta las reservas
    reservas_data = supabase.rpc('contar_reservas').execute()
    reservas_count_by_clase = {reserva['clase_id']: reserva['count'] for reserva in reservas_data.data}

    clases_disponibles = False

    for clase in clases:
        # Convertir el string a datetime si es necesario
        if isinstance(clase['horario'], str):
            clase['horario'] = datetime.fromisoformat(clase['horario'])

        reservas_count = reservas_count_by_clase.get(clase['id'], 0)
        clase['ocupacion'] = reservas_count
        clase['cupos_disponibles'] = clase['cupos_maximos'] - reservas_count

        if clase['cupos_disponibles'] > 0:
            clases_disponibles = True

    if not clases_disponibles:
        return render_template('clases.html', clases=clases, mensaje="Todas las clases están llenas.")
    
    return render_template('clases.html', clases=clases, clases_disponibles=clases_disponibles)

@app.route('/mis-reservas')
@login_required
def mis_reservas():
    reservas_data = supabase.table('reserva').select('*').eq('usuario_id', current_user.id).execute()
    reservas = reservas_data.data

    clases_disponibles = False

    for reserva in reservas:
        clase_data = supabase.table('clase').select('*').eq('id', reserva['clase_id']).execute()
        clase = clase_data.data[0] if clase_data.data else None
        if clase:
            reserva['clase'] = clase
            reservas_count = supabase.table('reserva').select('count(*)').eq('clase_id', clase['id']).execute()
            clase['ocupacion'] = reservas_count.data[0]['count'] if reservas_count.data else 0
            clase['cupos_disponibles'] = clase['cupos_maximos'] - clase['ocupacion']
            
            if clase['cupos_disponibles'] > 0:
                clases_disponibles = True

    if not reservas:
        return render_template('reservas.html', reservas=reservas, mensaje="No tienes reservas.")
    
    if not clases_disponibles:
        return render_template('reservas.html', reservas=reservas, mensaje="Todas las clases están llenas.")
    
    return render_template('reservas.html', reservas=reservas, clases_disponibles=clases_disponibles)

# Rutas de administración
@app.route('/admin/clases')
@login_required
def admin_clases():   
    clases_data = supabase.table('clase').select('*').execute()
    clases = clases_data.data

    return render_template('admin_clases.html', clases=clases)  

@app.route('/admin/clases/editar/<int:clase_id>', methods=['GET', 'POST'])
@login_required
def editar_clase(clase_id):
    clase_data = supabase.table('clase').select('*').eq('id', clase_id).execute()
    clase = clase_data.data[0] if clase_data.data else None

    if not clase:
        flash('Clase no encontrada.')
        return redirect(url_for('admin_clases'))

    if request.method == 'POST':
        nombre = request.form.get('nombre')
        horario = request.form.get('horario')
        cupos_maximos = request.form.get('cupos_maximos')

        if not nombre or not horario or not cupos_maximos:
            flash('Todos los campos son obligatorios.')
            return render_template('editar_clase.html', clase=clase)

        clase_actualizada = {
            'nombre': nombre,
            'horario': horario,
            'cupos_maximos': int(cupos_maximos)
        }

        update_response = supabase.table('clase').update(clase_actualizada).eq('id', clase_id).execute()

        if update_response.data:
            flash('Clase actualizada exitosamente.')
            return redirect(url_for('admin_clases'))
        else:
            flash('Error al actualizar la clase.')

    return render_template('editar_clase.html', clase=clase)

@app.route('/admin/clases/nueva', methods=['GET', 'POST'])
@login_required
def nueva_clase():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        horario = request.form.get('horario')
        cupos_maximos = request.form.get('cupos_maximos')

        if not nombre or not horario or not cupos_maximos:
            flash('Todos los campos son obligatorios.')
            return render_template('nueva_clase.html')

        nueva_clase_data = {
            'nombre': nombre,
            'horario': horario,
            'cupos_maximos': int(cupos_maximos)
        }

        clase_data = supabase.table('clase').insert(nueva_clase_data).execute()

        if clase_data.data:
            flash('Clase creada exitosamente.')
            return redirect(url_for('admin_clases'))
        else:
            flash('Error al crear la clase.')

    return render_template('nueva_clase.html')

@app.route('/admin/usuarios')
@login_required
def admin_usuarios():
    usuarios_data = supabase.table('user').select('*').execute()
    usuarios = usuarios_data.data

    return render_template('admin_usuarios.html', usuarios=usuarios)

@app.route('/admin/usuarios/editar/<int:usuario_id>', methods=['GET', 'POST'])
@login_required
def editar_usuario(usuario_id):
    usuario_data = supabase.table('user').select('*').eq('id', usuario_id).execute()
    usuario = usuario_data.data[0] if usuario_data.data else None

    if not usuario:
        flash('Usuario no encontrado.')
        return redirect(url_for('admin_usuarios'))

    if request.method == 'POST':
        nombre = request.form.get('nombre')
        email = request.form.get('email')
        es_admin = request.form.get('es_admin') == 'on'

        if not nombre or not email:
            flash('Nombre y email son obligatorios.')
            return render_template('editar_usuario.html', usuario=usuario)

        usuario_actualizado = {
            'nombre': nombre,
            'email': email,
            'es_admin': es_admin
        }

        update_response = supabase.table('user').update(usuario_actualizado).eq('id', usuario_id).execute()

        if update_response.data:
            flash('Usuario actualizado exitosamente.')
            return redirect(url_for('admin_usuarios'))
        else:
            flash('Error al actualizar el usuario.')

    return render_template('editar_usuario.html', usuario=usuario)

@app.route('/admin/usuarios/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_usuario():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        email = request.form.get('email')
        password = request.form.get('password')
        es_admin = request.form.get('es_admin') == 'on'

        if not nombre or not email or not password:
            flash('Todos los campos son obligatorios.')
            return render_template('nuevo_usuario.html')

        hashed_password = generate_password_hash(password)

        nuevo_usuario_data = {
            'nombre': nombre,
            'email': email,
            'password': hashed_password,
            'es_admin': es_admin
        }

        usuario_data = supabase.table('user').insert(nuevo_usuario_data).execute()

        if usuario_data.data:
            flash('Usuario creado exitosamente.')
            return redirect(url_for('admin_usuarios'))
        else:
            flash('Error al crear el usuario.')

    return render_template('nuevo_usuario.html')

# Inicialización
if __name__ == '__main__':
    app.run(debug=True)