from flask import Flask, send_from_directory, request, jsonify
from flask_cors import CORS
import os
import json
import requests
import logging
from database import update_reminder_by_id, get_user_reminders, delete_reminder_by_id, get_notes_by_user, update_note, delete_note
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'webapp')

@app.route('/')
def index():
    return send_from_directory(WEBAPP_DIR, 'index.html')

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
            requests.post(url, json=payload)
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
            requests.post(url, json=payload)
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
        # (id, message, remind_at, recurrence)
        reminders_list = []
        for r in reminders:
            reminders_list.append({
                "id": r[0],
                "message": r[1],
                "date": r[2],
                "recurrence": r[3]
            })
        return jsonify({"success": True, "reminders": reminders_list})
    except Exception as e:
        logging.error(f"Error in /api/reminders: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory(WEBAPP_DIR, path)

# --- ENDPOINTS DE NOTAS ---
@app.route('/api/notes', methods=['GET'])
def get_notes():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "Missing user_id"}), 400
    try:
        notes = get_notes_by_user(user_id)
        notes_list = []
        for n in notes:
            notes_list.append({
                "id": n[0],
                "content": n[1],
                "created_at": n[2],
                "updated_at": n[3]
            })
        return jsonify({"success": True, "notes": notes_list})
    except Exception as e:
        logging.error(f"Error in GET /api/notes: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/notes/<int:note_id>', methods=['PUT'])
def edit_note(note_id):
    data = request.json
    new_content = data.get('content')
    if not new_content:
        return jsonify({"success": False, "error": "Missing content"}), 400
    try:
        success = update_note(note_id, new_content)
        if success:
            return jsonify({"success": True})
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
    # El servidor corre en el puerto 5000 por defecto
    app.run(host='0.0.0.0', port=5000)
