# ========================
# 📦 IMPORTS
# ========================
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify
from pymongo import MongoClient
from bson.objectid import ObjectId
from bson.regex import Regex
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
from collections import defaultdict
from flask_cors import CORS

import os
import io
import pickle
import numpy as np
import pandas as pd
from PIL import Image
from sklearn.linear_model import LinearRegression
from qrcode import make as make_qr

import openai
from openai import OpenAIError, AuthenticationError, RateLimitError

from config import Config

# ========================
# 🚀 INIT APP
# ========================
app = Flask(__name__)

app.config["MONGO_URI"] = Config.MONGO_URI
app.config["SECRET_KEY"] = Config.SECRET_KEY
app.permanent_session_lifetime = timedelta(days=7)

CORS(app)

# ========================
# 🗄️ DATABASE
# ========================
client = MongoClient(app.config["MONGO_URI"])
db = client["konser"]

users = db["users"]
tickets = db["tickets"]
konserr = db["konserr"]
chats = db["chats"]


# Konfigurasi folder upload
UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# ==================== ROUTES ====================

@app.route('/')
def login():
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def do_login():
    username = request.form['username']
    password = request.form['password']
    user = users.find_one({'username': username})

    if user and check_password_hash(user['password'], password):
        session['user_id'] = str(user['_id'])
        session['username'] = user['username']
        session['role'] = user.get('role', 'user')
        session['avatar'] = user.get('avatar')  # ✅ Tambahkan avatar ke session

        if user['role'] == 'admin':
            return redirect('/admin/dashboard')
        return redirect('/home')

    flash('Login gagal. Coba lagi.')
    return redirect('/')

@app.route('/signup')
def signup():
    return render_template('signup.html')

@app.route('/signup', methods=['POST'])
def do_signup():
    username = request.form['username']
    email = request.form['email']
    password = request.form['password']
    avatar = request.form.get('avatar')
    role = request.form.get('role', 'user')  # default: user

    if users.find_one({'username': username}):
        flash('Username sudah dipakai.')
        return redirect('/signup')

    hashed_pw = generate_password_hash(password)
    users.insert_one({
        'username': username,
        'email': email,
        'password': hashed_pw,
        'avatar': avatar,
        'role': role,
        'created_at': datetime.now()
    })


    session['username'] = username
    session['avatar'] = avatar

    flash('Pendaftaran berhasil. Silakan login.')
    return redirect('/')

def get_current_user():
    if 'user_id' not in session:
        return None

    user = users.find_one({'_id': ObjectId(session['user_id'])})
    if not user:
        return None

    return {
        'username': user['username'],
        'avatar': user.get('avatar') or url_for('static', filename='profile.jpg'),
        'role': user.get('role', 'user')
    }


# ================ ADMIN ROUTES ===================


@app.route('/admin/konser', methods=['GET', 'POST'])
def tambah_konser():
    if session.get('role') != 'admin':
        return redirect('/')

    if request.method == 'POST':
        db.konserr.insert_one({
            'judul': request.form['judul'],
            'tanggal': request.form['tanggal'],
            'lokasi': request.form['lokasi'],
            'deskripsi': request.form['deskripsi'],
            'created_at': datetime.now()
        })
        flash('Konser berhasil ditambahkan.')
        return redirect('/admin/konser')

    konser_list = db.konserr.find().sort('tanggal', 1)  # urutkan berdasarkan tanggal
    return render_template('konser.html', konser_list=konser_list)

@app.route('/admin/delete_konser/<id>', methods=['POST'])
def hapus_konser(id):
    if session.get('role') != 'admin':
        return redirect('/')
        
    db.konserr.delete_one({'_id': ObjectId(id)})
    return redirect('/admin/konser')



@app.route('/admin/edit_konser/<id>', methods=['GET', 'POST'])
def edit_konser(id):
    if session.get('role') != 'admin':
        return redirect('/')

    konser = db.konserr.find_one({'_id': ObjectId(id)})

    if request.method == 'POST':
        db.konserr.update_one({'_id': ObjectId(id)}, {
            '$set': {
                'judul': request.form['judul'],
                'tanggal': request.form['tanggal'],
                'lokasi': request.form['lokasi'],
                'deskripsi': request.form['deskripsi']
            }
        })
        flash('Konser berhasil diperbarui.')
        return redirect('/admin/konser')

    return render_template('edit_konser.html', konser=konser)




@app.route('/admin/dashboard')
def admin_dashboard():
    if session.get('role') != 'admin': return redirect('/')
    return render_template('dashboard.html', username=session['username'])


@app.route('/admin/prediksi')
def prediksi_tiket():
    if session.get('role') != 'admin':
        return redirect('/')

    # Ambil konser terbaru (berdasarkan tanggal)
    konser = db['konserr'].find_one(sort=[('tanggal', -1)])
    if not konser:
        flash("Tidak ada konser ditemukan.")
        return redirect('/admin/dashboard')

    judul_konser = konser['judul']
    kapasitas = 320  # kapasitas total

    # Ambil semua tiket dengan status 'lunas' untuk konser ini
    tiket_lunas = tickets.find({
        'concert_name': judul_konser,
        'status': 'lunas'
    })

    # Hitung jumlah tiket terjual per hari
    penjualan_harian = defaultdict(int)
    for t in tiket_lunas:
        tanggal = t['booking_time'].date()
        penjualan_harian[tanggal] += 1

    # Urutkan berdasarkan tanggal
    sorted_data = sorted(penjualan_harian.items())
    if len(sorted_data) < 2:
        flash("Data penjualan terlalu sedikit untuk prediksi.")
        return redirect('/admin/dashboard')

    tanggal_list = [x[0] for x in sorted_data]
    jumlah_per_hari = [x[1] for x in sorted_data]

    # Hitung kumulatif penjualan
    x, y = [], []
    total = 0
    for i, jumlah in enumerate(jumlah_per_hari):
        total += jumlah
        x.append(i)  # hari ke-i
        y.append(total)

    # Latih model regresi linear
    model = LinearRegression()
    model.fit(np.array(x).reshape(-1, 1), np.array(y))

    # Cari hari ke-n di mana prediksi total penjualan = kapasitas
    # y = mx + c → x = (kapasitas - c) / m
    m = model.coef_[0]
    c = model.intercept_
    if m == 0:
        flash("Model tidak valid. Tidak ada tren pertumbuhan.")
        return redirect('/admin/dashboard')

    prediksi_hari_ke = int((kapasitas - c) / m)
    hari_mulai = tanggal_list[0]
    tanggal_prediksi = hari_mulai + timedelta(days=prediksi_hari_ke)

    return render_template(
        'prediksi.html',
        prediksi_habis=tanggal_prediksi.strftime("%d %B %Y"),
        total_terjual=total,
        kapasitas=kapasitas
    )


@app.route('/admin/forgot-admin', methods=['GET', 'POST'])
def forgot_admin():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        new_password = request.form['new_password']

        # Cari admin berdasarkan username dan email
        admin = db.users.find_one({'username': username, 'email': email, 'role': 'admin'})

        if admin:
            hashed_pw = generate_password_hash(new_password)
            db.users.update_one(
                {'_id': admin['_id']},
                {'$set': {'password': hashed_pw}}
            )
            flash('Password berhasil diubah. Silakan login kembali.')
            return redirect('/')
        else:
            flash('Username atau email tidak ditemukan.')
            return redirect('/admin/forgot-admin')

    return render_template('forgot-admin.html')


@app.route('/admin/pembayaran')
def kelola_pembayaran():
    if session.get('role') != 'admin':
        return redirect('/')

    transaksi = list(tickets.find({'status': 'menunggu verifikasi'}))

    for t in transaksi:
        user = users.find_one({'_id': t['user_id']})
        if user:
            t['username'] = user.get('username', 'N/A')
            t['email'] = user.get('email', 'N/A')
        else:
            t['username'] = 'Tidak ditemukan'
            t['email'] = '-'

        # Ambil data kursi dari seat_code
        t['kursi_display'] = t.get('seat_code', '-')  # jika seat_code tidak ada

    return render_template('pembayaran.html', transaksi=transaksi)



@app.route('/admin/verifikasi/<id>')
def verifikasi_pembayaran(id):
    if session.get('role') != 'admin':
        return redirect('/')
    tiket = db.tickets.find_one({'_id': ObjectId(id)})
    if not tiket:
        flash('Tiket tidak ditemukan.')
        return redirect('/admin/pembayaran')

    if tiket['status'] != 'menunggu verifikasi':
        flash('Tiket sudah diverifikasi.')
        return redirect('/admin/pembayaran')

    user = db.users.find_one({'_id': tiket['user_id']})
    if not user:
        flash('User tidak ditemukan.')
        return redirect('/admin/pembayaran')

    barcode = f"TIKET|{user['username']}|{tiket['seat_code']}|{tiket['concert_name']}"
    db.tickets.update_one(
        {'_id': ObjectId(id)},
        {'$set': {
            'status': 'lunas',
            'barcode': barcode
        }}
    )
    flash('Pembayaran berhasil diverifikasi.')
    return redirect('/admin/pembayaran')

@app.route('/admin/delete_verifikasi/<id>', methods=['POST', 'GET'])
def delete_verifikasi(id):
    if session.get('role') != 'admin':
        flash('Akses ditolak.')
        return redirect('/')

    tiket = db.tickets.find_one({'_id': ObjectId(id)})
    if not tiket:
        flash('Tiket tidak ditemukan.')
        return redirect('/admin/pembayaran')

    db.tickets.delete_one({'_id': ObjectId(id)})
    flash('Tiket berhasil dihapus.')
    return redirect('/admin/pembayaran')




@app.route('/admin/laporan', methods=['GET', 'POST'])
def laporan_penjualan():
    if session.get('role') != 'admin':
        return redirect('/')

    dari = request.args.get('dari')
    sampai = request.args.get('sampai')
    format_download = request.args.get('format')

    query_konser = {}
    if dari and sampai:
        try:
            tanggal_awal = datetime.strptime(dari, '%Y-%m-%d')
            tanggal_akhir = datetime.strptime(sampai, '%Y-%m-%d')
            query_konser['tanggal'] = {
                '$gte': tanggal_awal,
                '$lte': tanggal_akhir
            }
        except ValueError:
            pass

    konser_list = list(db['konserr'].find(query_konser).sort('tanggal', -1))
    laporan_konser = []

    total_vip = 0
    total_reguler = 0
    total_semua_tiket = 0
    total_pendapatan = 0

    for konser in konser_list:
        judul = konser.get('judul', 'Tanpa Judul')
        tanggal = konser.get('tanggal')

        tiket_lunas = list(tickets.find({
            'concert_name': judul,
            'status': 'lunas'
        }))

        vip_terjual = 0
        reguler_terjual = 0
        total_konser = 0

        for tiket in tiket_lunas:
            kursi = tiket.get('seat_code', '')
            harga = tiket.get('price', 0)

            if kursi:
                huruf = kursi[0].upper()
                if huruf in 'ABCDEFGHIJ':
                    vip_terjual += 1
                elif huruf in 'KLMNOP':
                    reguler_terjual += 1
                else:
                    print(f"Kursi tidak dikenali: {kursi}")

            total_konser += harga

        laporan_konser.append({
            'judul': judul,
            'tanggal': tanggal,
            'vip': vip_terjual,
            'reguler': reguler_terjual,
            'terjual': vip_terjual + reguler_terjual,
            'total': total_konser
        })

        total_vip += vip_terjual
        total_reguler += reguler_terjual
        total_semua_tiket += vip_terjual + reguler_terjual
        total_pendapatan += total_konser


    return render_template(
        'laporan.html',
        laporan_konser=laporan_konser,
        total_vip=total_vip,
        total_reguler=total_reguler,
        total_tiket_terjual=total_semua_tiket,
        total_pendapatan=total_pendapatan
    )

@app.route('/admin/laporan/detail/<judul>')
def detail_laporan_konser(judul):
    # Ambil semua tiket berdasarkan judul konser
    tiket_list = list(tickets.find({"concert_name": Regex(f"^{judul}$", "i")}))
    return render_template('laporan_detail.html', judul=judul, tiket_list=tiket_list)


@app.route('/admin/verifikasi/<id>', methods=['POST'])
def verifikasi_tiket(id):
    tiket = db.tickets.find_one({'_id': ObjectId(id)})
    if not tiket:
        flash('Tiket tidak ditemukan.')
        return redirect('/admin/verifikasi')

    if tiket['status'] != 'menunggu verifikasi':
        flash('Tiket sudah diverifikasi.')
        return redirect('/admin/verifikasi')

    # Ambil data yang dibutuhkan
    email = tiket.get('email', '')
    seat_code = tiket.get('seat_code', '')
    concert_name = tiket.get('concert_name', '')

    # Buat barcode string
    barcode = f"TIKET|{email}|{seat_code}|{concert_name}"

    # Update tiket: status jadi lunas, tambahkan barcode
    db.tickets.update_one(
        {'_id': ObjectId(id)},
        {'$set': {
            'status': 'lunas',
            'barcode': barcode
        }}
    )

    flash('Tiket berhasil diverifikasi dan barcode ditambahkan.')
    return redirect('/admin/verifikasi')

@app.route('/home')
def home():
    current_user = get_current_user()
    if not current_user:
        flash('Silakan login terlebih dahulu.')
        return redirect('/')

    # Ambil data pengguna dari database untuk memastikan avatar valid
    user = users.find_one({'username': session['username']})
    if user:
        avatar = user.get('avatar')  # Ambil avatar dari database, default kosong
    else:
        # Jika pengguna tidak ditemukan di database, logout dan redirect
        flash('Data pengguna tidak ditemukan. Silakan login kembali.')
        session.clear()
        return redirect('/')

    # Ambil daftar konser
    konser_list = list(konserr.find().sort('tanggal', 1))

    # Render template dengan username, avatar, dan konser_list
    return render_template('home.html',
                           username=current_user['username'],
                           avatar=current_user['avatar'],
                           konser_list=konser_list)


import os
from dotenv import load_dotenv
import openai
from flask_cors import CORS

# Load environment variables
load_dotenv()

# Ambil API key dari .env
openai.api_key = os.getenv("OPENAI_API_KEY")

# Validasi kalau belum di-set
if not openai.api_key:
    raise ValueError("OPENAI_API_KEY environment variable not set")

# Enable CORS
CORS(app)

CHATBOT_TEMPLATES = {
    'halo': 'Halo juga! Ada yang bisa saya bantu terkait tiket konser?',
    'hai': 'Hai! Apa kabar? Ingin tahu tentang konser atau cara beli tiket?',
    'beli tiket': 'Untuk membeli tiket, login ke akun Anda, pilih konser di halaman utama, klik "Pilih Kursi", pilih kursi yang diinginkan, lalu lanjutkan ke pembayaran. Ikuti instruksi untuk mengunggah bukti transfer. Butuh bantuan lebih lanjut?',
    'jadwal konser': 'Jadwal konser dapat dilihat di halaman utama. Pilih konser untuk melihat detail tanggal dan lokasi. Ingin tahu konser spesifik?',
    'harga tiket': 'Harga tiket bervariasi: VIP (baris A-J) Rp175.000, Reguler (baris K-P) Rp125.000. Silakan pilih kursi di halaman "Pilih Kursi" untuk detailnya.',
    'pembayaran': 'Pembayaran dilakukan via transfer bank. Setelah memilih kursi, unggah bukti transfer di halaman pembayaran. Admin akan memverifikasi dalam 1-2 hari.',
    'status tiket': 'Cek status tiket di menu "Status" setelah login. Anda bisa melihat apakah tiket sudah lunas atau menunggu verifikasi.',
    'kasih': 'terimakasih kembali senang bisa membantu'
}

# ==================== CHATBOT ROUTES ====================

@app.route('/api/chat', methods=['POST'])
def chat():
    if 'user_id' not in session:
        return jsonify({"error": "Silakan login terlebih dahulu."}), 401

    try:
        data = request.get_json()
        user_message = data.get("message", '').strip().lower()

        if not user_message:
            return jsonify({"error": "No message provided"}), 400

        # Store user message in MongoDB
        chats.insert_one({
            'user_id': ObjectId(session['user_id']),
            'sender': 'user',
            'message': user_message,
            'timestamp': datetime.now()
        })

        # Check for template response
        reply = None
        for key, template_response in CHATBOT_TEMPLATES.items():
            if key in user_message:
                reply = template_response
                break

        # If no template match, use OpenAI
        if not reply:
            try:
                response = openai.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "Kamu adalah asisten tiket konser yang membantu pengguna dalam bahasa Indonesia. Berikan jawaban yang akurat, singkat, dan relevan terkait pembelian tiket, informasi konser, atau pertanyaan umum tentang konser."},
                        {"role": "user", "content": user_message}
                    ],
                    max_tokens=150,
                    temperature=0.7
                )
                reply = response.choices[0].message.content.strip()
            except openai.error.AuthenticationError:
                print("OpenAI Authentication Error")
                return jsonify({"error": "Invalid API key"}), 401
            except openai.error.RateLimitError:
                print("OpenAI Rate Limit Error")
                return jsonify({"error": "Rate limit exceeded. Please try again later."}), 429
            except Exception as e:
                print(f"OpenAI Error: {str(e)}")
                reply = "Maaf, saya tidak dapat memproses pertanyaan ini sekarang. Coba lagi nanti."

        # Store bot reply in MongoDB
        chats.insert_one({
            'user_id': ObjectId(session['user_id']),
            'sender': 'bot',
            'message': reply,
            'timestamp': datetime.now()
        })

        return jsonify({"reply": reply})

    except Exception as e:
        print(f"Error in /api/chat: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/chat/history', methods=['GET'])
def chat_history():
    if 'user_id' not in session:
        return jsonify({"error": "Silakan login terlebih dahulu."}), 401

    try:
        chat_messages = list(chats.find({'user_id': ObjectId(session['user_id'])}).sort('timestamp', 1).limit(50))
        messages = [{'message': msg['message'], 'sender': msg['sender']} for msg in chat_messages]
        return jsonify({"messages": messages})
    except Exception as e:
        print(f"Error in /api/chat/history: {str(e)}")
        return jsonify({"error": "Failed to fetch chat history"}), 500

# Menampilkan halaman pemilihan kursi



@app.route('/seat')
def seat():
    if 'user_id' not in session: return redirect('/')
    konser = db['konserr'].find_one(sort=[('tanggal', 1)])
    booked = tickets.find({'status': {'$in': ['belum bayar', 'menunggu verifikasi', 'lunas']}})
    booked_seats = [t['seat_code'] for t in booked]
    return render_template('seat.html', username=session['username'], booked_seats=booked_seats, judul_konser=konser['judul'] if konser else 'Konser')

@app.route('/seat', methods=['POST'])
def post_seat():
    if 'user_id' not in session:
        return redirect('/')

    name = request.form['name']
    email = request.form['email']
    seats_selected = request.form.getlist('seats[]')

    if not seats_selected:
        flash("Silakan pilih kursi terlebih dahulu.")
        return redirect('/seat')

    konser = db.konserr.find_one(sort=[('tanggal', -1)])
    concert_name = konser['judul'] if konser else 'Swara Darmagita'

    already_booked = tickets.count_documents({
        'seat_code': {'$in': seats_selected},
        'status': {'$in': ['belum bayar', 'menunggu verifikasi', 'lunas']}
    })

    if already_booked > 0:
        flash('Beberapa kursi sudah dibooking orang lain. Silakan pilih ulang.')
        return redirect('/seat')

    user = db.users.find_one({'_id': ObjectId(session['user_id'])})
    if not user:
        flash('User tidak ditemukan.')
        return redirect('/seat')

    for seat in seats_selected:
        row_letter = seat[0].upper()
        if row_letter in 'ABCDEFGHIJ':
            harga = 175000
        elif row_letter in 'KLMNO':
            harga = 125000
        else:
            harga = 100000

        barcode = f"TIKET|{user['username']}|{seat}|{concert_name}"
        tickets.insert_one({
            'user_id': ObjectId(session['user_id']),
            'concert_name': concert_name,
            'seat_code': seat,
            'price': harga,
            'status': 'belum bayar',
            'payment_method': '',
            'booking_time': datetime.now(),
            'barcode': barcode
        })

    flash(f'{len(seats_selected)} kursi berhasil dibooking.')
    return redirect('/pay')
@app.route('/status')
def status():
    if 'user_id' not in session:
        return redirect('/')
    user_tickets = tickets.find({'user_id': ObjectId(session['user_id'])})
    return render_template('status.html', tickets=user_tickets)

@app.route('/setting')
def setting():
    if 'username' not in session:
        return redirect('/login')

    user = users.find_one({'username': session['username']})
    avatar = user.get('avatar') if user else None
    return render_template('setting.html', username=session['username'], avatar=avatar)



@app.route('/ganti-password', methods=['GET', 'POST'])
def ganti_password():
    if 'username' not in session:
        return redirect('/login')

    if request.method == 'POST':
        old_pw = request.form['old_password']
        new_pw = request.form['new_password']
        user = users.find_one({'username': session['username']})

        if user and check_password_hash(user['password'], old_pw):
            hashed_pw = generate_password_hash(new_pw)
            users.update_one({'_id': user['_id']}, {'$set': {'password': hashed_pw}})
            flash('Password berhasil diubah.')
            return redirect('/setting')
        else:
            flash('Password lama salah.')
            return redirect('/ganti-password')

    return render_template('ganti-password.html')


@app.route('/cek')
def cek():
    current_user = get_current_user()
    if not current_user:
        flash('Silakan login terlebih dahulu.')
        return redirect('/')

    user_id = ObjectId(session['user_id'])
    tiket_user = list(tickets.find({'user_id': user_id}).sort('booking_time', -1))

    tiket_data = []
    for t in tiket_user:
        konser = db.konserr.find_one({'judul': t.get('concert_name', '')})
        tiket_data.append({
            '_id': str(t['_id']),
            'concert_name': t.get('concert_name', ''),
            'status': t.get('status', ''),
            'seat_code': t.get('seat_code', ''),
            'price': t.get('price', 0),
            'tanggal': konser['tanggal'] if konser and 'tanggal' in konser else '-',
            'lokasi': konser['lokasi'] if konser and 'lokasi' in konser else '-'
        })

    return render_template('cek.html',
                           tiket_data=tiket_data,
                           username=current_user['username'],
                           avatar=current_user['avatar'])


@app.route('/tiket/qr/<ticket_id>')
def tiket_qr_individual(ticket_id):
    if 'user_id' not in session:
        return redirect('/')

    try:
        ticket_oid = ObjectId(ticket_id)
    except:
        flash('ID tiket tidak valid.')
        return redirect('/cek')

    tiket = tickets.find_one({
        '_id': ticket_oid,
        'user_id': ObjectId(session['user_id']),
        'status': 'lunas'
    })

    if not tiket:
        flash('Tiket tidak ditemukan atau belum lunas.')
        return redirect('/cek')

    user = users.find_one({'_id': tiket['user_id']})
    qr_data = f"TIKET|{user['username']}|{tiket['seat_code']}|{tiket['concert_name']}"

    img = make_qr(qr_data)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')



@app.route('/forgot', methods=['GET'])
def forgot_page():
    return render_template('forgot.html')

@app.route('/api/check-email', methods=['POST'])
def check_email():
    data = request.get_json()
    email = data.get('email')
    user = users.find_one({'email': email})
    if user:
        return jsonify({'exists': True})
    return jsonify({'exists': False, 'message': 'Email tidak ditemukan.'}), 404

@app.route('/api/change-password', methods=['POST'])
def change_password():
    data = request.get_json()
    email = data.get('email')
    new_password = data.get('newPassword')

    user = users.find_one({'email': email})
    if user:
        hashed_pw = generate_password_hash(new_password)
        users.update_one({'_id': user['_id']}, {'$set': {'password': hashed_pw}})
        return jsonify({'success': True, 'message': 'Password berhasil diubah.'})
    return jsonify({'success': False, 'message': 'User tidak ditemukan.'}), 404


@app.route('/tiket/qr')
def tiket_qr():
    if 'user_id' not in session: return redirect('/')
    user = users.find_one({'_id': ObjectId(session['user_id'])})
    tiket = tickets.find_one({'user_id': user['_id'], 'status': 'lunas'})
    if not tiket:
        flash('Tiket belum lunas.'); return redirect('/setting')
    qr_data = f"TIKET|{user['username']}|{tiket['seat_code']}|{tiket['concert_name']}"
    img = make_qr(qr_data)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

@app.route('/pay', methods=['GET', 'POST'])
def pay():
    if 'user_id' not in session:
        return redirect('/')

    user = users.find_one({'_id': ObjectId(session['user_id'])})
    if not user:
        flash('User tidak ditemukan.')
        return redirect('/')

    if request.method == 'POST':
        email = request.form.get('email', user.get('email'))
        file = request.files.get('bukti')

        if not file or file.filename == '':
            flash('Bukti transfer belum dipilih.')
            return redirect('/pay')

        # Generate nama file unik
        filename = secure_filename(f"{session['username']}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # Update tiket yang belum dibayar
        result = tickets.update_many(
            {'user_id': ObjectId(session['user_id']), 'status': 'belum bayar'},
            {
                '$set': {
                    'status': 'menunggu verifikasi',
                    'payment_method': 'VA',
                    'email': email,
                    'bukti_transfer': filepath
                }
            }
        )

        if result.modified_count == 0:
            flash('Tidak ada tiket yang diperbarui. Mungkin sudah dibayar sebelumnya.')
            return redirect('/status')

        flash('Konfirmasi pembayaran berhasil dikirim.')
        return redirect('/home')

    # GET method: tampilkan ringkasan
    user_ticket_list = list(tickets.find({
        'user_id': ObjectId(session['user_id']),
        'status': 'belum bayar'
    }))

    if not user_ticket_list:
        flash('Anda belum memilih kursi.')
        return redirect('/seat')

    seats = ', '.join([t['seat_code'] for t in user_ticket_list])

    # Hitung total berdasarkan kategori kursi
    total = 0
    for t in user_ticket_list:
        row_letter = t['seat_code'][0].upper()
        if row_letter in 'ABCDEFGHIJ':  # VIP
            total += 175000
        else:  # Reguler
            total += 125000

    # Ambil judul konser dari konser terbaru
    konser = db.konserr.find_one(sort=[('tanggal', -1)])
    judul_konser = konser['judul'] if konser else 'Swara Darmagita'

    return render_template(
        'pay.html',
        username=session.get('username', ''),
        email=user.get('email', ''),
        seats=seats,
        jumlah=len(user_ticket_list),
        total=total,
        judul_konser=judul_konser
    )




# ==================== TICKET VALIDATION ROUTES ====================
@app.route('/api/validate-ticket', methods=['GET'])
def validate_ticket():
    qr_data = request.args.get('q', '')

    if not qr_data.startswith('TIKET|') or len(qr_data.split('|')) != 4:
        return jsonify({'success': False, 'message': 'Format QR tidak valid'}), 400

    tiket = tickets.find_one({'barcode': qr_data, 'status': 'lunas'})
    if not tiket:
        return jsonify({'success': False, 'message': 'Tiket tidak ditemukan atau belum diverifikasi'}), 404

    user = users.find_one({'_id': tiket['user_id']})
    if not user:
        return jsonify({'success': False, 'message': 'User tidak ditemukan'}), 404

    ticket_data = {
        'username': user.get('username'),
        'email': user.get('email'),
        'concert_name': tiket.get('concert_name'),
        'seat_code': tiket.get('seat_code'),
        'price': tiket.get('price'),
        'status': tiket.get('status'),
    }

    return jsonify({'success': True, 'ticket': ticket_data}), 200


@app.route('/api/mark-present', methods=['POST'])
def mark_present():
    data = request.get_json()
    ticket_id = data.get('ticket_id')

    if not ticket_id:
        return jsonify({'success': False, 'message': 'ID tiket tidak ditemukan'}), 400

    result = tickets.update_one(
        {'_id': ObjectId(ticket_id), 'status': 'lunas'},
        {'$set': {'status': 'hadir'}}
    )

    if result.modified_count == 1:
        return jsonify({'success': True, 'message': 'Tiket ditandai hadir'}), 200
    else:
        return jsonify({'success': False, 'message': 'Tiket tidak ditemukan atau sudah digunakan'}), 404

    
# ==================== MAIN ====================

if __name__ == "__main__":
    app.run(debug=True)
