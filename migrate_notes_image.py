#!/usr/bin/env python3
"""
Migración: Agregar columna image_file_id a la tabla notes.
"""

import sqlite3
import os
import sys

def migrate():
    if not os.path.exists('reminders.db'):
        print("✓ BD no existe. Se creará con init_db().")
        return

    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(notes)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'image_file_id' in columns:
        print("✓ La columna image_file_id ya existe en notes.")
        conn.close()
        return

    print("Agregando columna image_file_id a notes...")
    cursor.execute('ALTER TABLE notes ADD COLUMN image_file_id TEXT DEFAULT NULL')
    conn.commit()
    conn.close()
    print("✓ Migración completada.")

if __name__ == '__main__':
    migrate()
