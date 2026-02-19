import sqlite3
import os

DB_PATH = '/root/bot-recordatorios/reminders.db'

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"Base de datos no encontrada en {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        print("Intentando agregar columna 'recurrence'...")
        cursor.execute("ALTER TABLE reminders ADD COLUMN recurrence TEXT DEFAULT NULL")
        conn.commit()
        print("✅ Columna 'recurrence' agregada exitosamente.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("ℹ️ La columna 'recurrence' ya existe.")
        else:
            print(f"❌ Error al migrar: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
