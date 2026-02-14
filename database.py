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
            status TEXT DEFAULT 'pending'
        )
    ''')
    conn.commit()
    conn.close()

def add_reminder(user_id, message, remind_at):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO reminders (user_id, message, remind_at) VALUES (?, ?, ?)', 
                   (user_id, message, remind_at))
    conn.commit()
    conn.close()

def get_user_reminders(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, message, remind_at FROM reminders WHERE user_id = ? AND status = "pending"', (user_id,))
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

def update_reminder_by_id(user_id, reminder_id, new_message=None, new_date=None):
    """Actualiza un recordatorio existente por su ID.
    
    Args:
        user_id: ID del usuario propietario del recordatorio
        reminder_id: ID numérico del recordatorio a actualizar
        new_message: Nuevo texto del recordatorio (opcional)
        new_date: Nueva fecha/hora en formato YYYY-MM-DD HH:MM:SS (opcional)
    
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
    
    # Construir la consulta UPDATE dinámicamente según qué campos se actualizan
    if new_message is not None and new_date is not None:
        cursor.execute('UPDATE reminders SET message = ?, remind_at = ? WHERE id = ? AND user_id = ? AND status = "pending"',
                       (new_message, new_date, reminder_id, user_id))
    elif new_message is not None:
        cursor.execute('UPDATE reminders SET message = ? WHERE id = ? AND user_id = ? AND status = "pending"',
                       (new_message, reminder_id, user_id))
    elif new_date is not None:
        cursor.execute('UPDATE reminders SET remind_at = ? WHERE id = ? AND user_id = ? AND status = "pending"',
                       (new_date, reminder_id, user_id))
    else:
        conn.close()
        return False
    
    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return success

if __name__ == "__main__":
    init_db()
    print("✅ Base de datos lista.")
