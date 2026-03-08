from flask import Flask, send_from_directory, request, jsonify, Response
from flask_cors import CORS
import os
import json
import requests
import logging
from database import get_connection, update_reminder_by_id, get_user_reminders, delete_reminder_by_id, get_notes_by_user, get_note_categories_by_user, update_note, delete_note, normalize_note_category, UNCATEGORIZED_LABEL
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
CORS(app)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'webapp')
SERVER_HOST = os.getenv('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.getenv('PORT', os.getenv('SERVER_PORT', '5000')))

@app.route('/')
def index():
    return send_from_directory(WEBAPP_DIR, 'index.html')

@app.route('/health', methods=['GET'])
def healthcheck():
    try:
        conn = get_connection()
        conn.execute('SELECT 1')
        conn.close()
        return jsonify({"success": True, "status": "ok"})
    except Exception as e:
        logging.error(f"Healthcheck failed: {e}")
        return jsonify({"success": False, "status": "error"}), 500

@app.route('/api/reprogram', methods=['POST'])
def reprogram():
    data = request.json
    user_id = data.get('user_id')
    reminder_id = data.get('id')
    message = data.get('message')
    new_date = data.get('date')
    new_recurrence = data.get('recurrence') # Opcional

    if not all([user_id, reminder_id, message, new_date]):
        return jsonify({"success": False, "error": "Missing data"}), 400

    try:
        success = update_reminder_by_id(user_id, reminder_id, message, new_date, new_recurrence)
        if success:
            # Enviar notificación via Telegram
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {
                "chat_id": user_id,
                "text": f"✅ ¡Hecho! Recordatorio #{reminder_id} actualizado con éxito:\n📌 {message}\n📅 {new_date}"
            }
            requests.post(url, json=payload, timeout=10)
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "Database update failed"}), 500
    except Exception as e:
        logging.error(f"Error in /api/reprogram: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/delete', methods=['POST'])
def delete_reminder():
    data = request.json
    user_id = data.get('user_id')
    reminder_id = data.get('id')

    if not all([user_id, reminder_id]):
        return jsonify({"success": False, "error": "Missing data"}), 400

    try:
        success = delete_reminder_by_id(user_id, reminder_id)
        if success:
            # Notificar al usuario
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {
                "chat_id": user_id,
                "text": f"🗑️ Recordatorio #{reminder_id} eliminado correctamente."
            }
            requests.post(url, json=payload, timeout=10)
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "Recordatorio no encontrado o no pertenece al usuario"}), 404
    except Exception as e:
        logging.error(f"Error in /api/delete: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/reminders', methods=['GET'])
def get_reminders():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "Missing user_id"}), 400

    try:
        reminders = get_user_reminders(user_id)
        # Convert list of tuples to list of dicts for JSON serialization
        # (id, message, remind_at, recurrence, image_file_id)
        reminders_list = []
        for r in reminders:
            reminders_list.append({
                "id": r[0],
                "message": r[1],
                "date": r[2],
                "recurrence": r[3],
                "image_file_id": r[4] if len(r) > 4 else None
            })
        return jsonify({"success": True, "reminders": reminders_list})
    except Exception as e:
        logging.error(f"Error in /api/reminders: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory(WEBAPP_DIR, path)

# --- ENDPOINTS DE NOTAS ---
@app.route('/api/telegram-image/<path:file_id>')
def telegram_image_proxy(file_id):
    """Proxy que descarga una imagen de Telegram por su file_id y la sirve al cliente.
    Esto evita exponer el token del bot en el frontend."""
    try:
        # 1. Obtener file_path de Telegram
        tg_response = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile",
            params={"file_id": file_id},
            timeout=10
        )
        tg_data = tg_response.json()

        if not tg_data.get('ok'):
            logging.warning(f"Telegram getFile failed for {file_id}: {tg_data}")
            return jsonify({"error": "Image not found"}), 404

        file_path = tg_data['result']['file_path']
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"

        # 2. Descargar la imagen
        img_response = requests.get(file_url, timeout=30)

        if img_response.status_code != 200:
            return jsonify({"error": "Could not download image"}), 502

        content_type = img_response.headers.get('Content-Type', 'image/jpeg')

        return Response(
            img_response.content,
            content_type=content_type,
            headers={
                'Cache-Control': 'public, max-age=86400'  # Cache 24 h
            }
        )
    except Exception as e:
        logging.error(f"Error proxying telegram image {file_id}: {e}")
        return jsonify({"error": "Internal error"}), 500

@app.route('/api/notes', methods=['GET'])
def get_notes():
    user_id = request.args.get('user_id')
    category = request.args.get('category')
    if not user_id:
        return jsonify({"success": False, "error": "Missing user_id"}), 400
    try:
        notes = get_notes_by_user(user_id, category=category)
        notes_list = []
        for n in notes:
            normalized_category = normalize_note_category(n[2]) or UNCATEGORIZED_LABEL
            notes_list.append({
                "id": n[0],
                "content": n[1],
                "category": normalized_category,
                "created_at": n[3],
                "updated_at": n[4],
                "image_file_id": n[5] if len(n) > 5 else None
            })
        return jsonify({
            "success": True,
            "category": normalize_note_category(category) or (UNCATEGORIZED_LABEL if category is not None else None),
            "notes": notes_list
        })
    except Exception as e:
        logging.error(f"Error in GET /api/notes: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/notes/categories', methods=['GET'])
def get_note_categories():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "Missing user_id"}), 400

    try:
        categories = get_note_categories_by_user(user_id)
        category_list = []
        for category_name, note_count, last_updated_at in categories:
            category_list.append({
                "name": category_name,
                "note_count": note_count,
                "last_updated_at": last_updated_at
            })
        return jsonify({"success": True, "categories": category_list})
    except Exception as e:
        logging.error(f"Error in GET /api/notes/categories: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/notes/<int:note_id>', methods=['PUT'])
def edit_note(note_id):
    data = request.json
    new_content = data.get('content')
    category = data.get('category')
    if not new_content:
        return jsonify({"success": False, "error": "Missing content"}), 400
    try:
        success = update_note(note_id, new_content, category)
        if success:
            return jsonify({
                "success": True,
                "category": normalize_note_category(category) or UNCATEGORIZED_LABEL
            })
        else:
            return jsonify({"success": False, "error": "Nota no encontrada"}), 404
    except Exception as e:
        logging.error(f"Error in PUT /api/notes/{note_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/notes/<int:note_id>', methods=['DELETE'])
def remove_note(note_id):
    try:
        success = delete_note(note_id)
        if success:
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "Nota no encontrada"}), 404
    except Exception as e:
        logging.error(f"Error in DELETE /api/notes/{note_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    logging.info("Starting web app server on %s:%s", SERVER_HOST, SERVER_PORT)
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)
