#!/usr/bin/env python3
"""
Script de migración para agregar columnas faltantes en la base de datos existente.
Actualmente asegura:
- reminders.image_file_id
- notes.category
- notes.subcategory_id
- note_subcategories
"""

import sqlite3
import os
import sys

from database import ensure_note_subcategories_table, ensure_notes_subcategory_column

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'reminders.db')

def migrate_db():
    """Agrega columnas faltantes a tablas existentes si aún no existen."""
    
    if not os.path.exists(DB_PATH):
        print("✓ Base de datos no existe. Se creará una nueva con init_db().")
        return
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Verificar si la columna image_file_id ya existe en reminders
        cursor.execute("PRAGMA table_info(reminders)")
        reminder_columns = [col[1] for col in cursor.fetchall()]
        migrated_any = False
        
        if 'image_file_id' not in reminder_columns:
            print("Agregando columna image_file_id a la tabla reminders...")
            cursor.execute('''
                ALTER TABLE reminders 
                ADD COLUMN image_file_id TEXT DEFAULT NULL
            ''')
            migrated_any = True
        else:
            print("✓ La columna image_file_id ya existe en la tabla reminders.")
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='notes'")
        notes_table_exists = cursor.fetchone() is not None

        if notes_table_exists:
            cursor.execute("PRAGMA table_info(notes)")
            note_columns = [col[1] for col in cursor.fetchall()]

            if 'category' not in note_columns:
                print("Agregando columna category a la tabla notes...")
                cursor.execute('''
                    ALTER TABLE notes
                    ADD COLUMN category TEXT DEFAULT NULL
                ''')
                migrated_any = True
            else:
                print("✓ La columna category ya existe en la tabla notes.")

            if 'subcategory_id' not in note_columns:
                print("Agregando columna subcategory_id a la tabla notes...")
                ensure_notes_subcategory_column(cursor)
                migrated_any = True
            else:
                print("✓ La columna subcategory_id ya existe en la tabla notes.")
        else:
            print("✓ La tabla notes no existe todavía. Se creará con init_db().")

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='note_subcategories'")
        subcategories_table_exists = cursor.fetchone() is not None
        ensure_note_subcategories_table(cursor)
        if subcategories_table_exists:
            print("✓ La tabla note_subcategories ya existe.")
        else:
            print("Creando tabla note_subcategories...")
            migrated_any = True
        
        conn.commit()
        conn.close()
        if migrated_any:
            print("✓ Migración completada exitosamente.")
        else:
            print("✓ No había cambios pendientes en la base de datos.")
        
    except sqlite3.OperationalError as e:
        print(f"✗ Error durante la migración: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Error inesperado: {e}")
        sys.exit(1)

if __name__ == '__main__':
    migrate_db()
