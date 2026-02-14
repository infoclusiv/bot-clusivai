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
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Intenta convertir search_text a ID (entero)
        reminder_id = int(search_text)
        cursor.execute('DELETE FROM reminders WHERE user_id = ? AND id = ?', (user_id, reminder_id))
    except ValueError:
        # Si no es un ID numérico, busca por palabra clave
        cursor.execute('DELETE FROM reminders WHERE user_id = ? AND message LIKE ? AND status = "pending"', 
                       (user_id, f"%{search_text}%"))
    
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted_count

if __name__ == "__main__":
    init_db()
    print("✅ Base de datos lista.")
