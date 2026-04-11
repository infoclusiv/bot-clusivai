import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'reminders.db')
UNCATEGORIZED_LABEL = 'Sin categoría'
AI_TEXT_CAPABILITY = 'text'
AI_VISION_CAPABILITY = 'vision'
SUPPORTED_AI_CAPABILITIES = (AI_TEXT_CAPABILITY, AI_VISION_CAPABILITY)
SUPPORTED_AI_PROVIDERS = ('openrouter', 'nvidia')


def get_connection():
    return sqlite3.connect(DB_PATH)


def normalize_note_category(category):
    """Normaliza una categoría de nota para persistencia y vistas."""
    if category is None:
        return None

    normalized = str(category).strip()
    if not normalized:
        return None

    return normalized


def normalize_note_category_for_storage(category):
    """Normaliza una categoría para guardarla, tratando 'Sin categoría' como vacío."""
    normalized = normalize_note_category(category)
    if normalized is None:
        return None

    if normalized.lower() == UNCATEGORIZED_LABEL.lower():
        return None

    return normalized


def normalize_note_subcategory_name(name):
    """Normaliza el nombre de una subcategoría."""
    if name is None:
        return None

    normalized = str(name).strip()
    if not normalized:
        return None

    return normalized


def normalize_note_subcategory_id(subcategory_id):
    """Normaliza la referencia a una subcategoría."""
    if subcategory_id in (None, '', 'null'):
        return None

    try:
        normalized = int(subcategory_id)
    except (TypeError, ValueError) as exc:
        raise ValueError('Subcategoría inválida.') from exc

    if normalized <= 0:
        raise ValueError('Subcategoría inválida.')

    return normalized


def normalize_ai_capability(capability):
    """Normaliza una capacidad de IA soportada por el bot."""
    if capability is None:
        raise ValueError('La capacidad de IA es obligatoria.')

    normalized = str(capability).strip().lower()
    if normalized not in SUPPORTED_AI_CAPABILITIES:
        raise ValueError('Capacidad de IA no soportada.')

    return normalized


def normalize_ai_provider(provider):
    """Normaliza un proveedor de IA soportado por el bot."""
    if provider is None:
        raise ValueError('El proveedor de IA es obligatorio.')

    normalized = str(provider).strip().lower()
    if normalized not in SUPPORTED_AI_PROVIDERS:
        raise ValueError('Proveedor de IA no soportado.')

    return normalized


def normalize_ai_model_name(model_name):
    """Normaliza el nombre exacto de un modelo de IA."""
    if model_name is None:
        raise ValueError('El nombre del modelo es obligatorio.')

    normalized = str(model_name).strip()
    if not normalized:
        raise ValueError('El nombre del modelo es obligatorio.')

    return normalized


def max_timestamp(first_value, second_value):
    """Retorna la fecha más reciente entre dos timestamps ISO o valores nulos."""
    if not first_value:
        return second_value
    if not second_value:
        return first_value
    return max(first_value, second_value)


def ensure_notes_category_column(cursor):
    """Agrega la columna category a notes si aún no existe."""
    cursor.execute('PRAGMA table_info(notes)')
    columns = [column[1] for column in cursor.fetchall()]

    if 'category' not in columns:
        cursor.execute('ALTER TABLE notes ADD COLUMN category TEXT DEFAULT NULL')


def ensure_notes_subcategory_column(cursor):
    """Agrega la columna subcategory_id a notes si aún no existe."""
    cursor.execute('PRAGMA table_info(notes)')
    columns = [column[1] for column in cursor.fetchall()]

    if 'subcategory_id' not in columns:
        cursor.execute('ALTER TABLE notes ADD COLUMN subcategory_id INTEGER DEFAULT NULL')


def ensure_note_subcategories_table(cursor):
    """Crea la tabla e índices de subcategorías si aún no existen."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS note_subcategories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category_name TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_note_subcategories_unique_name
        ON note_subcategories (user_id, category_name COLLATE NOCASE, name COLLATE NOCASE)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_note_subcategories_user_category
        ON note_subcategories (user_id, category_name COLLATE NOCASE)
    ''')


def ensure_ai_config_tables(cursor):
    """Crea las tablas e índices necesarios para la configuración global de IA."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_global_settings (
            capability TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model_name TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_model_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            capability TEXT NOT NULL,
            model_name TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_used_at DATETIME DEFAULT NULL,
            UNIQUE(provider, capability, model_name)
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_ai_model_catalog_lookup
        ON ai_model_catalog (capability, provider, model_name)
    ''')


def serialize_ai_setting_row(row):
    if row is None:
        return None

    return {
        'capability': row[0],
        'provider': row[1],
        'model_name': row[2],
        'updated_at': row[3],
    }


def serialize_ai_model_row(row):
    if row is None:
        return None

    return {
        'id': row[0],
        'provider': row[1],
        'capability': row[2],
        'model_name': row[3],
        'created_at': row[4],
        'updated_at': row[5],
        'last_used_at': row[6],
    }


def category_exists_for_user(cursor, user_id, category_name):
    """Valida si la categoría raíz existe por notas o por subcategorías ya creadas."""
    cursor.execute(
        '''
        SELECT 1
        FROM notes
        WHERE user_id = ? AND lower(COALESCE(NULLIF(TRIM(category), ''), ?)) = lower(?)
        LIMIT 1
        ''',
        (user_id, UNCATEGORIZED_LABEL, category_name)
    )
    if cursor.fetchone():
        return True

    cursor.execute(
        '''
        SELECT 1
        FROM note_subcategories
        WHERE user_id = ? AND lower(category_name) = lower(?)
        LIMIT 1
        ''',
        (user_id, category_name)
    )
    return cursor.fetchone() is not None


def resolve_subcategory_for_note(cursor, user_id, category_name, subcategory_id):
    """Valida que la subcategoría pertenezca al usuario y a la categoría seleccionada."""
    normalized_subcategory_id = normalize_note_subcategory_id(subcategory_id)
    if normalized_subcategory_id is None:
        return None

    if category_name is None:
        raise ValueError('No puedes asignar una subcategoría sin categoría.')

    cursor.execute(
        '''
        SELECT id
        FROM note_subcategories
        WHERE id = ? AND user_id = ? AND lower(category_name) = lower(?)
        ''',
        (normalized_subcategory_id, user_id, category_name)
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError('La subcategoría no pertenece a la categoría seleccionada.')

    return row[0]

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
            category TEXT DEFAULT NULL,
            subcategory_id INTEGER DEFAULT NULL,
            image_file_id TEXT DEFAULT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    ensure_notes_category_column(cursor)
    ensure_notes_subcategory_column(cursor)
    ensure_note_subcategories_table(cursor)
    ensure_ai_config_tables(cursor)
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


def ensure_default_ai_settings(default_settings):
    """Si no existe configuración activa, siembra los defaults iniciales."""
    conn = get_connection()
    cursor = conn.cursor()
    ensure_ai_config_tables(cursor)

    for capability, raw_config in (default_settings or {}).items():
        normalized_capability = normalize_ai_capability(capability)
        provider = normalize_ai_provider(raw_config.get('provider'))
        model_name = normalize_ai_model_name(raw_config.get('model_name'))

        cursor.execute(
            '''
            INSERT INTO ai_model_catalog (provider, capability, model_name)
            VALUES (?, ?, ?)
            ON CONFLICT(provider, capability, model_name) DO UPDATE SET
                updated_at = CURRENT_TIMESTAMP
            ''',
            (provider, normalized_capability, model_name)
        )
        cursor.execute(
            '''
            INSERT INTO ai_global_settings (capability, provider, model_name)
            VALUES (?, ?, ?)
            ON CONFLICT(capability) DO NOTHING
            ''',
            (normalized_capability, provider, model_name)
        )

    conn.commit()
    conn.close()
    return get_all_ai_settings()


def get_ai_setting(capability):
    """Obtiene la configuración activa para una capacidad de IA."""
    normalized_capability = normalize_ai_capability(capability)
    conn = get_connection()
    cursor = conn.cursor()
    ensure_ai_config_tables(cursor)
    cursor.execute(
        '''
        SELECT capability, provider, model_name, updated_at
        FROM ai_global_settings
        WHERE capability = ?
        ''',
        (normalized_capability,)
    )
    row = cursor.fetchone()
    conn.close()
    return serialize_ai_setting_row(row)


def get_all_ai_settings():
    """Obtiene todas las configuraciones activas de IA indexadas por capacidad."""
    conn = get_connection()
    cursor = conn.cursor()
    ensure_ai_config_tables(cursor)
    cursor.execute(
        '''
        SELECT capability, provider, model_name, updated_at
        FROM ai_global_settings
        ORDER BY capability ASC
        '''
    )
    rows = cursor.fetchall()
    conn.close()
    return {
        row[0]: serialize_ai_setting_row(row)
        for row in rows
    }


def save_ai_model(provider, capability, model_name):
    """Guarda un modelo en el catálogo reutilizable sin activarlo."""
    normalized_provider = normalize_ai_provider(provider)
    normalized_capability = normalize_ai_capability(capability)
    normalized_model_name = normalize_ai_model_name(model_name)

    conn = get_connection()
    cursor = conn.cursor()
    ensure_ai_config_tables(cursor)
    cursor.execute(
        '''
        INSERT INTO ai_model_catalog (provider, capability, model_name)
        VALUES (?, ?, ?)
        ON CONFLICT(provider, capability, model_name) DO UPDATE SET
            updated_at = CURRENT_TIMESTAMP
        ''',
        (normalized_provider, normalized_capability, normalized_model_name)
    )
    conn.commit()
    conn.close()


def get_saved_ai_models(capability=None, provider=None, limit=None):
    """Lista el catálogo de modelos guardados filtrando por capacidad o proveedor."""
    params = []
    conditions = []

    if capability is not None:
        conditions.append('capability = ?')
        params.append(normalize_ai_capability(capability))

    if provider is not None:
        conditions.append('provider = ?')
        params.append(normalize_ai_provider(provider))

    query = '''
        SELECT id, provider, capability, model_name, created_at, updated_at, last_used_at
        FROM ai_model_catalog
    '''

    if conditions:
        query += ' WHERE ' + ' AND '.join(conditions)

    query += '''
        ORDER BY capability ASC,
                 provider ASC,
                 COALESCE(last_used_at, updated_at, created_at) DESC,
                 model_name COLLATE NOCASE ASC
    '''

    if limit is not None:
        query += ' LIMIT ?'
        params.append(int(limit))

    conn = get_connection()
    cursor = conn.cursor()
    ensure_ai_config_tables(cursor)
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [serialize_ai_model_row(row) for row in rows]


def get_ai_model_by_id(model_id):
    """Busca un modelo guardado por su identificador interno."""
    try:
        normalized_model_id = int(model_id)
    except (TypeError, ValueError):
        return None

    conn = get_connection()
    cursor = conn.cursor()
    ensure_ai_config_tables(cursor)
    cursor.execute(
        '''
        SELECT id, provider, capability, model_name, created_at, updated_at, last_used_at
        FROM ai_model_catalog
        WHERE id = ?
        ''',
        (normalized_model_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return serialize_ai_model_row(row)


def activate_ai_model(capability, provider, model_name):
    """Activa una combinación proveedor/modelo para una capacidad de IA."""
    normalized_capability = normalize_ai_capability(capability)
    normalized_provider = normalize_ai_provider(provider)
    normalized_model_name = normalize_ai_model_name(model_name)

    conn = get_connection()
    cursor = conn.cursor()
    ensure_ai_config_tables(cursor)
    cursor.execute(
        '''
        INSERT INTO ai_model_catalog (provider, capability, model_name, last_used_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(provider, capability, model_name) DO UPDATE SET
            updated_at = CURRENT_TIMESTAMP,
            last_used_at = CURRENT_TIMESTAMP
        ''',
        (normalized_provider, normalized_capability, normalized_model_name)
    )
    cursor.execute(
        '''
        INSERT INTO ai_global_settings (capability, provider, model_name, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(capability) DO UPDATE SET
            provider = excluded.provider,
            model_name = excluded.model_name,
            updated_at = CURRENT_TIMESTAMP
        ''',
        (normalized_capability, normalized_provider, normalized_model_name)
    )
    conn.commit()
    conn.close()
    return get_ai_setting(normalized_capability)

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
def create_note(user_id, content, image_file_id=None, category=None):
    """Crea una nueva nota para el usuario, opcionalmente con imagen."""
    conn = get_connection()
    cursor = conn.cursor()
    normalized_category = normalize_note_category_for_storage(category)
    cursor.execute(
        'INSERT INTO notes (user_id, content, category, subcategory_id, image_file_id) VALUES (?, ?, ?, ?, ?)',
        (user_id, content, normalized_category, None, image_file_id)
    )
    conn.commit()
    conn.close()

def get_notes_by_user(user_id, category=None, subcategory_id=None):
    """Retorna las notas de un usuario, opcionalmente filtradas por categoría o subcategoría."""
    conn = get_connection()
    cursor = conn.cursor()
    normalized_category = normalize_note_category(category)
    normalized_subcategory_id = normalize_note_subcategory_id(subcategory_id)

    query = '''
        SELECT
            n.id,
            n.content,
            n.category,
            n.created_at,
            n.updated_at,
            n.image_file_id,
            n.subcategory_id,
            ns.name
        FROM notes n
        LEFT JOIN note_subcategories ns ON ns.id = n.subcategory_id
        WHERE n.user_id = ?
    '''
    params = [user_id]

    if category is not None:
        if normalized_category is None or normalized_category.lower() == UNCATEGORIZED_LABEL.lower():
            query += '''
                AND (n.category IS NULL OR TRIM(n.category) = '' OR lower(n.category) = lower(?))
            '''
            params.append(UNCATEGORIZED_LABEL)
        else:
            query += ' AND lower(n.category) = lower(?)'
            params.append(normalized_category)

    if normalized_subcategory_id is not None:
        query += ' AND n.subcategory_id = ?'
        params.append(normalized_subcategory_id)

    query += ' ORDER BY n.created_at DESC'
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_note_categories_by_user(user_id):
    """Retorna las categorías del usuario con subcategorías y contadores."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        '''
        SELECT
            MIN(COALESCE(NULLIF(TRIM(category), ''), ?)) AS category,
            COUNT(*) AS note_count,
            MAX(updated_at) AS last_updated_at
        FROM notes
        WHERE user_id = ?
        GROUP BY lower(COALESCE(NULLIF(TRIM(category), ''), ?))
        ORDER BY lower(MIN(COALESCE(NULLIF(TRIM(category), ''), ?))) ASC
        ''',
        (UNCATEGORIZED_LABEL, user_id, UNCATEGORIZED_LABEL, UNCATEGORIZED_LABEL)
    )
    category_rows = cursor.fetchall()

    cursor.execute(
        '''
        SELECT
            ns.id,
            ns.category_name,
            ns.name,
            COUNT(n.id) AS note_count,
            MAX(COALESCE(n.updated_at, ns.updated_at)) AS last_updated_at
        FROM note_subcategories ns
        LEFT JOIN notes n ON n.subcategory_id = ns.id
        WHERE ns.user_id = ?
        GROUP BY ns.id, ns.category_name, ns.name, ns.updated_at
        ORDER BY lower(ns.category_name) ASC, lower(ns.name) ASC
        ''',
        (user_id,)
    )
    subcategory_rows = cursor.fetchall()
    conn.close()

    categories_by_key = {}

    for category_name, note_count, last_updated_at in category_rows:
        categories_by_key[category_name.lower()] = {
            'name': category_name,
            'note_count': note_count,
            'last_updated_at': last_updated_at,
            'subcategories': []
        }

    for subcategory_id, category_name, subcategory_name, note_count, last_updated_at in subcategory_rows:
        category_key = category_name.lower()
        category_entry = categories_by_key.get(category_key)
        if category_entry is None:
            category_entry = {
                'name': category_name,
                'note_count': 0,
                'last_updated_at': last_updated_at,
                'subcategories': []
            }
            categories_by_key[category_key] = category_entry
        else:
            category_entry['last_updated_at'] = max_timestamp(category_entry['last_updated_at'], last_updated_at)

        category_entry['subcategories'].append({
            'id': subcategory_id,
            'name': subcategory_name,
            'note_count': note_count,
            'last_updated_at': last_updated_at
        })

    categories = list(categories_by_key.values())
    categories.sort(key=lambda item: item['name'].lower())
    for category in categories:
        category['subcategories'].sort(key=lambda item: item['name'].lower())

    return categories


def create_note_subcategory(user_id, category_name, name):
    """Crea una subcategoría para una categoría existente del usuario."""
    normalized_category = normalize_note_category_for_storage(category_name)
    normalized_name = normalize_note_subcategory_name(name)

    if normalized_category is None:
        raise ValueError('Debes elegir una categoría válida.')
    if normalized_name is None:
        raise ValueError('Debes escribir un nombre para la subcategoría.')

    conn = get_connection()
    cursor = conn.cursor()

    if not category_exists_for_user(cursor, user_id, normalized_category):
        conn.close()
        raise ValueError('La categoría seleccionada no existe.')

    cursor.execute(
        '''
        SELECT id
        FROM note_subcategories
        WHERE user_id = ? AND lower(category_name) = lower(?) AND lower(name) = lower(?)
        ''',
        (user_id, normalized_category, normalized_name)
    )
    if cursor.fetchone() is not None:
        conn.close()
        raise ValueError('La subcategoría ya existe dentro de esa categoría.')

    cursor.execute(
        '''
        INSERT INTO note_subcategories (user_id, category_name, name)
        VALUES (?, ?, ?)
        ''',
        (user_id, normalized_category, normalized_name)
    )
    subcategory_id = cursor.lastrowid
    conn.commit()

    cursor.execute(
        '''
        SELECT id, category_name, name, updated_at
        FROM note_subcategories
        WHERE id = ?
        ''',
        (subcategory_id,)
    )
    row = cursor.fetchone()
    conn.close()

    return {
        'id': row[0],
        'category_name': row[1],
        'name': row[2],
        'note_count': 0,
        'last_updated_at': row[3]
    }


def delete_note_subcategory(user_id, subcategory_id):
    """Elimina una subcategoría y deja sus notas sin subcategoría."""
    normalized_subcategory_id = normalize_note_subcategory_id(subcategory_id)
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        'SELECT id FROM note_subcategories WHERE id = ? AND user_id = ?',
        (normalized_subcategory_id, user_id)
    )
    if cursor.fetchone() is None:
        conn.close()
        return None

    cursor.execute(
        '''
        UPDATE notes
        SET subcategory_id = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE subcategory_id = ?
        ''',
        (normalized_subcategory_id,)
    )
    cleared_notes = cursor.rowcount
    cursor.execute(
        'DELETE FROM note_subcategories WHERE id = ? AND user_id = ?',
        (normalized_subcategory_id, user_id)
    )
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()

    if not deleted:
        return None

    return {
        'id': normalized_subcategory_id,
        'notes_cleared': cleared_notes
    }

def update_note(note_id, new_content, category=None, subcategory_id=None):
    """Actualiza el contenido, categoría y subcategoría de una nota."""
    conn = get_connection()
    cursor = conn.cursor()
    normalized_category = normalize_note_category_for_storage(category)

    cursor.execute('SELECT user_id FROM notes WHERE id = ?', (note_id,))
    note_row = cursor.fetchone()
    if note_row is None:
        conn.close()
        return False

    user_id = note_row[0]
    resolved_subcategory_id = resolve_subcategory_for_note(cursor, user_id, normalized_category, subcategory_id)
    cursor.execute(
        '''
        UPDATE notes
        SET content = ?, category = ?, subcategory_id = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        (new_content, normalized_category, resolved_subcategory_id, note_id)
    )
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
