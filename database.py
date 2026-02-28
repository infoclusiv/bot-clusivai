import sqlite3

def get_connection():
    return sqlite3.connect('reminders.db')

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            remind_at DATETIME NOT NULL,
            recurrence TEXT DEFAULT NULL,
            status TEXT DEFAULT 'pending',
            image_file_id TEXT DEFAULT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            daily_summary_enabled INTEGER DEFAULT 0,
            daily_summary_time TEXT DEFAULT '07:45:00'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def add_reminder(user_id, message, remind_at, recurrence=None, image_file_id=None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO reminders (user_id, message, remind_at, recurrence, image_file_id) VALUES (?, ?, ?, ?, ?)', 
                   (user_id, message, remind_at, recurrence, image_file_id))
    conn.commit()
    conn.close()

def get_user_reminders(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, message, remind_at, recurrence, image_file_id FROM reminders WHERE user_id = ? AND status = "pending"', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def delete_reminder_by_text(user_id, search_text):
    """Elimina recordatorios por ID numérico o coincidencia parcial de texto.
    
    Args:
        user_id: ID del usuario propietario del recordatorio
        search_text: Puede ser un ID numérico o parte del texto del recordatorio
    
    Returns:
        Número de recordatorios eliminados (solo los con status=pending)
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Intenta convertir search_text a ID (entero)
        reminder_id = int(search_text)
        cursor.execute('DELETE FROM reminders WHERE user_id = ? AND id = ? AND status = "pending"', 
                       (user_id, reminder_id))
    except ValueError:
        # Si no es un ID numérico, busca por palabra clave (búsqueda parcial con LIKE)
        cursor.execute('DELETE FROM reminders WHERE user_id = ? AND message LIKE ? AND status = "pending"', 
                       (user_id, f"%{search_text}%"))
    
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted_count

def update_reminder_by_id(user_id, reminder_id, new_message=None, new_date=None, new_recurrence=None):
    """Actualiza un recordatorio existente por su ID.
    
    Args:
        user_id: ID del usuario propietario del recordatorio
        reminder_id: ID numérico del recordatorio a actualizar
        new_message: Nuevo texto del recordatorio (opcional)
        new_date: Nueva fecha/hora en formato YYYY-MM-DD HH:MM:SS (opcional)
        new_recurrence: Nueva regla de recurrencia (opcional)
    
    Returns:
        True si el recordatorio se actualizó exitosamente, False en caso contrario
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        reminder_id = int(reminder_id)
    except (ValueError, TypeError):
        conn.close()
        return False
    
    updates = []
    params = []
    
    if new_message is not None:
        updates.append("message = ?")
        params.append(new_message)
    if new_date is not None:
        updates.append("remind_at = ?")
        params.append(new_date)
        updates.append("status = 'pending'") # Reactivar si cambia la fecha
    if new_recurrence is not None:
        updates.append("recurrence = ?")
        params.append(new_recurrence)
    
    if not updates:
        conn.close()
        return False
        
    query = f"UPDATE reminders SET {', '.join(updates)} WHERE id = ? AND user_id = ?"
    params.extend([reminder_id, user_id])
    
    cursor.execute(query, params)
    
    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return success

def delete_reminder_by_id(user_id, reminder_id):
    """Elimina definitivamente un recordatorio por su ID."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        reminder_id = int(reminder_id)
    except (ValueError, TypeError):
        conn.close()
        return False
        
    cursor.execute('DELETE FROM reminders WHERE id = ? AND user_id = ?', (reminder_id, user_id))
    
    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return success

def set_daily_summary(user_id, enabled, time='07:45:00'):
    """Activa o desactiva el resumen diario para un usuario."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO user_settings (user_id, daily_summary_enabled, daily_summary_time)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET 
            daily_summary_enabled=excluded.daily_summary_enabled,
            daily_summary_time=excluded.daily_summary_time
    ''', (user_id, 1 if enabled else 0, time))
    conn.commit()
    conn.close()

def get_users_with_daily_summary():
    """Retorna lista de usuarios con resumen diario activo."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, daily_summary_time FROM user_settings WHERE daily_summary_enabled = 1')
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_today_reminders(user_id):
    """Obtiene los recordatorios programados para hoy para un usuario."""
    import pytz
    from datetime import datetime
    tz_bogota = pytz.timezone('America/Bogota')
    now = datetime.now(tz_bogota)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999).strftime("%Y-%m-%d %H:%M:%S")
    
    conn = get_connection()
    cursor = conn.cursor()
    # Buscamos recordatorios con status pending que caigan hoy
    cursor.execute('''
        SELECT id, message, remind_at FROM reminders 
        WHERE user_id = ? AND status = "pending" 
        AND remind_at >= ? AND remind_at <= ?
        ORDER BY remind_at ASC
    ''', (user_id, today_start, today_end))
    rows = cursor.fetchall()
    conn.close()
    return rows

# --- FUNCIONES DE NOTAS ---
def create_note(user_id, content):
    """Crea una nueva nota para el usuario."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO notes (user_id, content) VALUES (?, ?)', (user_id, content))
    conn.commit()
    conn.close()

def get_notes_by_user(user_id):
    """Retorna todas las notas de un usuario, ordenadas por fecha de creación descendente."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, content, created_at, updated_at FROM notes WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def update_note(note_id, new_content):
    """Actualiza el contenido de una nota y su fecha de modificación."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE notes SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', (new_content, note_id))
    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return success

def delete_note(note_id):
    """Elimina una nota por su ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM notes WHERE id = ?', (note_id,))
    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return success


if __name__ == "__main__":
    init_db()
    print("✅ Base de datos lista.")
