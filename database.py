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
    # Intenta borrar por ID o por coincidencia de texto
    cursor.execute('DELETE FROM reminders WHERE user_id = ? AND (id = ? OR message LIKE ?)', 
                   (user_id, search_text, f"%{search_text}%"))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("✅ Base de datos lista.")
