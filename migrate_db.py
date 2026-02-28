#!/usr/bin/env python3
"""
Script de migración para agregar la columna image_file_id a la tabla reminders.
Ejecutar este script si la base de datos reminders.db ya existe.
"""

import sqlite3
import os
import sys

def migrate_db():
    """Agrega la columna image_file_id a la tabla reminders si no existe."""
    
    if not os.path.exists('reminders.db'):
        print("✓ Base de datos no existe. Se creará una nueva con init_db().")
        return
    
    try:
        conn = sqlite3.connect('reminders.db')
        cursor = conn.cursor()
        
        # Verificar si la columna ya existe
        cursor.execute("PRAGMA table_info(reminders)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'image_file_id' in columns:
            print("✓ La columna image_file_id ya existe en la tabla reminders.")
            conn.close()
            return
        
        # Agregar la columna si no existe
        print("Agregando columna image_file_id a la tabla reminders...")
        cursor.execute('''
            ALTER TABLE reminders 
            ADD COLUMN image_file_id TEXT DEFAULT NULL
        ''')
        
        conn.commit()
        conn.close()
        print("✓ Migración completada exitosamente. La columna image_file_id fue agregada.")
        
    except sqlite3.OperationalError as e:
        print(f"✗ Error durante la migración: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Error inesperado: {e}")
        sys.exit(1)

if __name__ == '__main__':
    migrate_db()
