import os
import requests
import json
import pytz
import logging
import re
import base64
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("MODEL_NAME")
VISION_MODEL = "nvidia/nemotron-nano-12b-v2-vl:free"

# Configurar logging para este módulo
logger = logging.getLogger(__name__)

def extract_json_from_text(text):
    """
    Extrae JSON válido de texto que puede contener markdown o texto adicional.
    Busca el primer '{' y el último '}' para extraer el objeto JSON.
    """
    try:
        # Buscar el primer { y el último }
        start = text.find('{')
        end = text.rfind('}')
        
        if start == -1 or end == -1 or end <= start:
            return None
        
        json_str = text[start:end+1]
        return json.loads(json_str)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Error extrayendo JSON: {e} de texto: {text[:100]}...")
        return None

def process_user_input(text, history=None, active_reminders=None):
    # Verificar que las variables de entorno estén configuradas
    if not API_KEY:
        logger.error("ERROR: OPENROUTER_API_KEY no está configurado")
        return None
    if not MODEL:
        logger.error("ERROR: MODEL_NAME no está configurado")
        return None
    
    # Obtener hora actual de Bogotá
    tz_bogota = pytz.timezone('America/Bogota')
    now = datetime.now(tz_bogota)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    
    # Generar Mini-Calendario (Hoy + 14 días)
    # Esto ayuda a la IA a aterrizar "el sábado 21" a una fecha real sin calcular
    mini_calendar = []
    dias_espanol = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    
    for i in range(15):
        future_date = now + timedelta(days=i)
        day_name = dias_espanol[future_date.weekday()]
        date_iso = future_date.strftime("%Y-%m-%d")
        mini_calendar.append(f"- {day_name} {date_iso}")
    
    mini_calendar_str = "\n".join(mini_calendar)
    
    # Preparar contexto de recordatorios activos
    reminders_context = ""
    if active_reminders:
        reminders_context = "\nRECORDATORIOS ACTIVOS ACTUALES DEL USUARIO (Fuente de Verdad):\n"
        for r in active_reminders:
            recur_info = f" [Recurrente: {r[3]}]" if len(r) > 3 and r[3] else ""
            reminders_context += f"- ID {r[0]}: \"{r[1]}\" para el {r[2]}{recur_info}\n"
    else:
        reminders_context = "\nEl usuario no tiene recordatorios activos actualmente.\n"

    system_prompt = f"""
    Eres 'Clusivai', un asistente personal inteligente. 
    CONTEXTO CALENDARIO (ÚSALO COMO VERDAD ABSOLUTA PARA FECHAS):
    Hora actual en Bogotá: {now_str}
    
    PRÓXIMOS DÍAS (Mini-Calendario):
    {mini_calendar_str}
    
    - Si el usuario dice "el sábado 21", BUSCA en la lista de arriba qué día dice "Sábado ...-21" y usa ESA fecha exacta.
    - Si el usuario dice "mañana", toma la fecha del segundo renglón del calendario.
    - Si el usuario dice "el próximo viernes", busca el primer Viernes que aparezca en la lista (o el segundo si hoy es viernes y se refiere al siguiente).
    - TIENES ACCESO AL HISTORIAL DE CONVERSACIÓN. Úsalo para entender el contexto y resolver ambigüedades.
    
    {reminders_context}
    
    REGLA DE ORO SOBRE FECHAS:
    - NO calcules fechas mentalmente si puedes buscarlas en el Mini-Calendario.
    - Si el usuario menciona un día de la semana y un número (ej: "Lunes 4"), VERIFICA en el calendario que coincidan. Si en el calendario el día 4 es Martes, CORRIGE o usa la fecha del calendario que tenga sentido (prioriza el número si es específico).
    
    REGLA DE ORO SOBRE IDs:
    - Cuando el usuario quiera ACTUALIZAR, BORRAR o PREGUNTAR por un recordatorio, utiliza EXCLUSIVAMENTE los IDs listados arriba en 'RECORDATORIOS ACTIVOS ACTUALES'.
    - Si el usuario menciona un ID que NO está en la lista superior, dile amablemente que ese ID no existe y muéstrale los IDs que sí tiene disponibles.
    - No alucines IDs. Si la lista superior está vacía, el usuario no tiene nada que modificar o borrar.
    
    Debes responder ÚNICAMENTE con un objeto JSON con esta estructura:
    {{
        "action": "CREATE" | "LIST" | "DELETE" | "UPDATE" | "CHAT" | "SET_SETTING" | "CONSULTAR_NOTAS",
        "id": número de ID (solo para UPDATE y SET_SETTING si aplica),
        "setting_name": "nombre del ajuste (solo para SET_SETTING, ej: 'daily_summary')",
        "value": valor del ajuste (ej: true, false, o una hora '07:45:00'),
        "message": "descripción de la tarea (solo para recordatorios)",
        "date": "YYYY-MM-DD HH:MM:SS" (fecha calculada para CREATE o UPDATE),
        "recurrence": "cadena RRULE (solo si es recurrente, ej: FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR) o null",
        "reply": "Tu respuesta directa si la acción es CHAT o confirmación de acción"
    }}

    Reglas:
    - Si el usuario pide activar o desactivar el resumen diario (ej: "activa el resumen diario", "no quiero más el listado matutino"), usa action: "SET_SETTING" con setting_name: "daily_summary" y value: true/false.
    - Si el usuario especifica una hora para el resumen (ej: "listado a las 8am"), usa action: "SET_SETTING", setting_name: "daily_summary_time" y value: "HH:MM:SS".
    - Si el usuario saluda o pregunta algo general (como "¿qué hora es?"), usa action: "CHAT".
    - Si quiere ver sus recordatorios ("mis recordatorios", "lista", "cuáles tengo"), usa action: "LIST".
    - Si quiere crear un recordatorio, usa action: "CREATE" con la descripción de la tarea.
    - Si la instrucción implica repetición (ej: "diario", "todos los lunes", "cada semana", "lunes a viernes"), genera en el campo "recurrence" una regla RRULE válida (formato iCalendar).
      Ejemplos:
      "lunes a viernes a las 5pm" -> "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR"
      "todos los domingos" -> "FREQ=WEEKLY;BYDAY=SU"
      "cada día a las 10am" -> "FREQ=DAILY"
      Si no es recurrente, pon "recurrence": null.
    - Si quiere borrar un recordatorio, usa action: "DELETE" y en el campo "message" extrae SOLO el identificador (ID numérico o palabra clave principal), sin verbos como "borra", "quitar", "elimina", etc.
      Ejemplo: Si dice "Borra el recordatorio con ID 5" → "message": "5"
      Ejemplo: Si dice "Elimina la tarea de la leche" → "message": "leche"
    - Si quiere cambiar, corregir, posponer o modificar un recordatorio existente, usa action: "UPDATE".
      Extrae el ID del recordatorio y los campos a cambiar. Incluye en el JSON solo los campos que cambian.
      IMPORTANTE: El campo "id" DEBE ser un número entero (INTEGER), no una cadena de texto.
      El ID debe extraerse de los resultados previos de LIST o de ALERTAS recientes.
      Ejemplo: Si dice "Cambia el recordatorio 5 a las 3 PM" → {{"action": "UPDATE", "id": 5, "date": "2026-02-14 15:00:00", "reply": "..."}}
      Ejemplo: Si dice "Edita la tarea 3 a comprar pan" → {{"action": "UPDATE", "id": 3, "message": "comprar pan", "reply": "..."}}
    - IMPORTANTE SOBRE RECORDATORIOS ENVIADOS Y ALERTAS:
      1. Cuando un recordatorio suena y se envía al usuario, su estado cambia a 'sent' (NO se borra) y aparece en el historial como un alert JSON.
      2. Si el usuario pide "reprogramar", "posponer" o "cambiar" un recordatorio que acaba de sonar, usa action: "UPDATE".
      3. Confía en los IDs que el usuario mencione o que hayan aparecido en el historial reciente (especialmente en los objetos con action: "ALERT").
      4. Al reprogramar (cambiar la fecha), el recordatorio se reactivará automáticamente pasándolo a 'pending'.
    - REGLA CRÍTICA SOBRE PRONOMBRES:
      Si el usuario usa palabras como "este", "ese", "el anterior", "el último" o "el que acaba de sonar" 
      inmediatamente después de que el asistente haya enviado una alerta (action: "ALERT" con ID: X), 
      la acción es UPDATE y el ID debe ser el que aparece en la última alerta del historial.
    - Responde siempre de forma amable en español.
    - IMPORTANTE: Si en el historial existe una pregunta de confirmación o seguimiento, el siguiente mensaje del usuario es una RESPUESTA a esa pregunta, no una nueva acción.
    
    REGLA SOBRE IMÁGENES:
    - Si el mensaje del usuario incluye "[📸 El usuario adjuntó una imagen a este mensaje]", significa que hay una imagen adjunta que se guardará automáticamente con el recordatorio.
    - Cuando veas este indicador y el usuario diga "recuérdame esto", "guarda esto", o similar, crea un recordatorio con action: "CREATE".
    - En el campo "message", incluye la descripción que dio el usuario. No necesitas describir la imagen, ya se adjunta automáticamente.
    - Si el usuario solo adjuntó la imagen sin dar instrucciones claras de fecha/hora, pregúntale cuándo quiere ser recordado.
    
    NOTAS PERSISTENTES (son DIFERENTES de los recordatorios):
    - Las NOTAS se guardan con el comando /nota y NO tienen fecha/hora. Son datos que el usuario quiere recordar (contraseñas, datos, ideas, etc.).
    - Los RECORDATORIOS tienen fecha/hora y generan alertas.
    - Si el usuario pregunta por información guardada, datos personales, contraseñas, notas, o dice "¿qué notas tengo?", "¿cuál era la clave del wifi?", o cualquier consulta sobre información que pudo haber guardado como nota, usa action: "CONSULTAR_NOTAS".
    - NO necesitas ningún parámetro extra para CONSULTAR_NOTAS, solo pon: {{"action": "CONSULTAR_NOTAS", "reply": ""}}
    """

    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    messages = [
        {"role": "system", "content": system_prompt}
    ]
    
    # Extender con historial si existe
    if history:
        messages.extend(history)
    
    # Agregar mensaje actual del usuario
    messages.append({"role": "user", "content": text})
    
    data = {
        "model": MODEL,
        "messages": messages
    }

    try:
        logger.info(f"Enviando request a OpenRouter con {len(messages)} mensajes")
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions", 
            headers=headers, 
            json=data,
            timeout=60  # Aumentado a 60 segundos debido a lentitud en modelos gratuitos
        )
        
        # Verificar si la respuesta fue exitosa
        if response.status_code != 200:
            logger.error(f"Error en API OpenRouter: Status {response.status_code}, Response: {response.text}")
            return None
        
        response_data = response.json()
        
        # Verificar estructura de la respuesta
        if 'choices' not in response_data or not response_data['choices']:
            logger.error(f"Respuesta de API inválida: {response_data}")
            return None
        
        content = response_data['choices'][0]['message']['content']
        logger.info(f"Respuesta cruda de IA: {content[:200]}...")
        
        # Limpieza de formato markdown si la IA lo incluye
        content = content.replace("```json", "").replace("```", "").strip()
        
        # Intentar parsear el JSON con múltiples estrategias
        parsed_result = None
        
        # Estrategia 1: Parseo directo
        try:
            parsed_result = json.loads(content)
            logger.info(f"JSON parseado exitosamente (directo): {parsed_result.get('action', 'UNKNOWN')}")
        except json.JSONDecodeError:
            logger.warning(f"Parseo directo falló, intentando extracción...")
            
            # Estrategia 2: Extracción con regex
            parsed_result = extract_json_from_text(content)
            if parsed_result:
                logger.info(f"JSON extraído con regex: {parsed_result.get('action', 'UNKNOWN')}")
        
        if parsed_result is None:
            logger.error(f"No se pudo parsear JSON. Contenido: {content[:200]}...")
            return None
        
        # Validación adicional: asegurar que 'id' sea integer si existe
        if 'id' in parsed_result and parsed_result['id'] is not None:
            try:
                parsed_result['id'] = int(parsed_result['id'])
            except (ValueError, TypeError):
                logger.warning(f"ID no es un número válido: {parsed_result['id']}")
        
        return parsed_result
            
    except requests.exceptions.Timeout:
        logger.error("Timeout al conectar con OpenRouter (60s)")
        return None
    except requests.exceptions.RequestException as re:
        logger.error(f"Error de conexión con OpenRouter: {re}")
        return None
    except Exception as e:
        print(f"--- ERROR CRÍTICO EN BRAIN.PY ---")
        print(f"Tipo de error: {type(e).__name__}")
        print(f"Mensaje: {e}")
        # Si hay respuesta de la API, imprimirla
        if 'response' in locals() and response is not None:
            try:
                print(f"Status Code: {response.status_code}")
                print(f"Respuesta API: {response.text[:500]}...")
            except Exception as resp_error:
                print(f"No se pudo leer respuesta: {resp_error}")
        logger.error(f"Error inesperado procesando IA: {e}", exc_info=True)
        return None


def process_vision_input(text, image_base64, history=None, active_reminders=None):
    """Procesa mensajes con imágenes usando el modelo de visión.
    
    Args:
        text: Texto del usuario (puede ser caption o instrucción posterior)
        image_base64: Imagen codificada en base64 (formato: data:image/jpeg;base64,...)
        history: Historial de conversación
        active_reminders: Recordatorios activos del usuario
    
    Returns:
        Dict con la respuesta parseada o None si hay error
    """
    if not API_KEY:
        logger.error("ERROR: OPENROUTER_API_KEY no está configurado")
        return None
    if not VISION_MODEL:
        logger.error("ERROR: VISION_MODEL no está configurado")
        return None
    
    # Obtener hora actual de Bogotá
    tz_bogota = pytz.timezone('America/Bogota')
    now = datetime.now(tz_bogota)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    
    # Generar Mini-Calendario
    mini_calendar = []
    dias_espanol = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    
    for i in range(15):
        future_date = now + timedelta(days=i)
        day_name = dias_espanol[future_date.weekday()]
        date_iso = future_date.strftime("%Y-%m-%d")
        mini_calendar.append(f"- {day_name} {date_iso}")
    
    mini_calendar_str = "\n".join(mini_calendar)
    
    # Preparar contexto de recordatorios activos
    reminders_context = ""
    if active_reminders:
        reminders_context = "\nRECORDATORIOS ACTIVOS ACTUALES DEL USUARIO (Fuente de Verdad):\n"
        for r in active_reminders:
            recur_info = f" [Recurrente: {r[3]}]" if len(r) > 3 and r[3] else ""
            reminders_context += f"- ID {r[0]}: \"{r[1]}\" para el {r[2]}{recur_info}\n"
    else:
        reminders_context = "\nEl usuario no tiene recordatorios activos actualmente.\n"

    # Prompt del sistema para el modelo de visión
    system_prompt = f"""
    Eres 'Clusivai', un asistente personal inteligente con capacidad de ver imágenes.
    CONTEXTO CALENDARIO (ÚSALO COMO VERDAD ABSOLUTA PARA FECHAS):
    Hora actual en Bogotá: {now_str}
    
    PRÓXIMOS DÍAS (Mini-Calendario):
    {mini_calendar_str}
    
    {reminders_context}
    
    IMPORTANTE: Cuando analices una imagen, haz lo siguiente:
    1. Describe brevemente qué ves en la imagen (si es relevante para la tarea)
    2. Si el usuario pide crear un recordatorio basado en la imagen, extrae la información relevante
    3. Si la imagen contiene texto (captura de pantalla, documento, nota), transcríbelo
    
    Debes responder ÚNICAMENTE con un objeto JSON con esta estructura:
    {{
        "action": "CREATE" | "LIST" | "DELETE" | "UPDATE" | "CHAT" | "SET_SETTING" | "CONSULTAR_NOTAS",
        "id": número de ID (solo para UPDATE y SET_SETTING si aplica),
        "setting_name": "nombre del ajuste (solo para SET_SETTING)",
        "value": valor del ajuste (ej: true, false, o una hora '07:45:00'),
        "message": "descripción de la tarea (solo para recordatorios)",
        "date": "YYYY-MM-DD HH:MM:SS" (fecha calculada para CREATE o UPDATE),
        "recurrence": "cadena RRULE (solo si es recurrente) o null",
        "reply": "Tu respuesta directa si la acción es CHAT o confirmación de acción"
    }}

    Reglas para crear recordatorios desde imágenes:
    - Si el usuario dice "recuérdame esto", "guarda esto", o algo similar, crea un recordatorio con action: "CREATE"
    - La descripción del recordatorio debe incluir lo que vez en la imagen (texto, información relevante, etc.)
    - Si no hay instrucción clara pero hay una imagen, pregunta al usuario qué quiere hacer con ella
    
    Ejemplos:
    - Usuario envía imagen de una factura con texto "Pagar el 15" → CREATE con message: "Pagar factura (texto de imagen: [contenido])"
    - Usuario envía imagen y dice "recuérdame revisar esto mañana" → CREATE con message basado en la imagen
    """

    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    
    # Construir mensaje con contenido multimodal (texto + imagen)
    user_content = [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": image_base64}}
    ]
    
    messages = [
        {"role": "system", "content": system_prompt}
    ]
    
    # Extender con historial si existe (solo mensajes de texto para evitar problemas)
    if history:
        for msg in history:
            if isinstance(msg.get("content"), str):
                messages.append(msg)
    
    # Agregar mensaje actual con imagen
    messages.append({"role": "user", "content": user_content})
    
    data = {
        "model": VISION_MODEL,
        "messages": messages
    }

    try:
        logger.info(f"Enviando request a OpenRouter con imagen usando modelo: {VISION_MODEL}")
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions", 
            headers=headers, 
            json=data,
            timeout=90  # Más tiempo para modelos de visión
        )
        
        if response.status_code != 200:
            logger.error(f"Error en API OpenRouter (visión): Status {response.status_code}, Response: {response.text}")
            return None
        
        response_data = response.json()
        
        if 'choices' not in response_data or not response_data['choices']:
            logger.error(f"Respuesta de API inválida (visión): {response_data}")
            return None
        
        content = response_data['choices'][0]['message']['content']
        logger.info(f"Respuesta cruda de IA (visión): {content[:200]}...")
        
        # Limpieza de formato markdown
        content = content.replace("```json", "").replace("```", "").strip()
        
        # Intentar parsear el JSON
        parsed_result = None
        
        try:
            parsed_result = json.loads(content)
            logger.info(f"JSON parseado exitosamente (visión): {parsed_result.get('action', 'UNKNOWN')}")
        except json.JSONDecodeError:
            logger.warning(f"Parseo directo falló (visión), intentando extracción...")
            parsed_result = extract_json_from_text(content)
            if parsed_result:
                logger.info(f"JSON extraído con regex (visión): {parsed_result.get('action', 'UNKNOWN')}")
        
        if parsed_result is None:
            logger.error(f"No se pudo parsear JSON (visión). Contenido: {content[:200]}...")
            return None
        
        # Validación adicional: asegurar que 'id' sea integer si existe
        if 'id' in parsed_result and parsed_result['id'] is not None:
            try:
                parsed_result['id'] = int(parsed_result['id'])
            except (ValueError, TypeError):
                logger.warning(f"ID no es un número válido: {parsed_result['id']}")
        
        return parsed_result
            
    except requests.exceptions.Timeout:
        logger.error("Timeout al conectar con OpenRouter (visión) (90s)")
        return None
    except requests.exceptions.RequestException as re:
        logger.error(f"Error de conexión con OpenRouter (visión): {re}")
        return None
    except Exception as e:
        print(f"--- ERROR CRÍTICO EN PROCESS_VISION_INPUT ---")
        print(f"Tipo de error: {type(e).__name__}")
        print(f"Mensaje: {e}")
        logger.error(f"Error inesperado procesando visión: {e}", exc_info=True)
        return None


def process_notes_query(user_query, notes_data, history=None):
    """Genera una respuesta natural del LLM basada en las notas del usuario.
    
    Args:
        user_query: La pregunta original del usuario
        notes_data: Lista de tuplas (id, content, created_at, updated_at)
        history: Historial de conversación
    
    Returns:
        String con la respuesta natural, o None si hay error
    """
    if not API_KEY or not MODEL:
        logger.error("API_KEY o MODEL no configurados para process_notes_query")
        return None
    
    # Formatear notas como contexto
    if notes_data:
        notes_context = "NOTAS GUARDADAS POR EL USUARIO:\n"
        for note in notes_data:
            note_id, content, created_at, updated_at = note
            notes_context += f"- [Nota #{note_id}] {content} (guardada: {created_at})\n"
    else:
        notes_context = "El usuario NO tiene notas guardadas actualmente."
    
    system_prompt = f"""Eres 'Clusivai', un asistente personal inteligente.
El usuario te preguntó algo y necesitas responder basándote en sus notas guardadas.

{notes_context}

Reglas:
- Responde de forma natural y amable en español.
- Si el usuario pregunta por algo específico (como una contraseña o dato), busca en las notas y respóndele directamente.
- Si pide ver todas sus notas, lístalas de forma organizada.
- Si no tiene notas o no encuentras lo que busca, díselo amablemente y sugiere usar /nota para guardar información.
- Responde SOLO con texto plano (NO JSON). Tu respuesta se enviará directamente al usuario.
"""
    
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system_prompt}]
    
    if history:
        messages.extend(history)
    
    messages.append({"role": "user", "content": user_query})
    
    data = {"model": MODEL, "messages": messages}
    
    try:
        logger.info(f"Enviando consulta de notas a OpenRouter")
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=60
        )
        
        if response.status_code != 200:
            logger.error(f"Error en API OpenRouter (notas): Status {response.status_code}")
            return None
        
        response_data = response.json()
        if 'choices' not in response_data or not response_data['choices']:
            logger.error(f"Respuesta inválida de API (notas): {response_data}")
            return None
        
        content = response_data['choices'][0]['message']['content'].strip()
        logger.info(f"Respuesta de notas: {content[:100]}...")
        return content
        
    except requests.exceptions.Timeout:
        logger.error("Timeout en consulta de notas")
        return None
    except Exception as e:
        logger.error(f"Error en process_notes_query: {e}", exc_info=True)
        return None

def process_video_summary(transcript, user_instruction=None, history=None):
    """Analiza y resume la transcripción de un video usando el LLM de OpenRouter.
    
    Args:
        transcript: Texto transcrito del video.
        user_instruction: Instrucción adicional del usuario (ej: "¿de qué hablan?").
        history: Historial de conversación.
    
    Returns:
        String con el resumen/análisis, o None si hay error.
    """
    if not API_KEY or not MODEL:
        logger.error("API_KEY o MODEL no configurados para process_video_summary")
        return None
    
    # Truncar transcript si es muy largo para no exceder límites del modelo
    max_transcript_chars = 15000  # ~3750 tokens aprox
    truncated = False
    if len(transcript) > max_transcript_chars:
        transcript = transcript[:max_transcript_chars]
        truncated = True
    
    system_prompt = """Eres 'Clusivai', un asistente personal inteligente.
El usuario compartió un video de X.com (Twitter) y se ha transcrito automáticamente su audio.

Tu tarea es analizar la transcripción y proporcionar:

1. 📋 **Resumen**: Un resumen conciso y claro del contenido del video (2-4 oraciones).
2. 🔑 **Puntos clave**: Los puntos o ideas principales mencionados (lista con bullets).
3. 💡 **Datos relevantes**: Si hay nombres, cifras, fechas, o datos importantes, menciónalos.

Reglas:
- Responde en español, de forma clara y bien organizada.
- Usa emojis para hacer la respuesta visual y agradable.
- Si la transcripción tiene errores de reconocimiento de voz (palabras mal escritas, fragmentos inconexos), haz tu mejor esfuerzo para interpretar el contenido.
- Si el usuario hizo una pregunta específica sobre el video, enfócate en responderla ADEMÁS del resumen.
- Si la transcripción parece ser música, letra de canción, o contenido no hablado, indícalo.
- Responde SOLO con texto plano formateado (NO JSON). Tu respuesta se enviará directamente al usuario.
- No incluyas la transcripción completa en tu respuesta, solo el análisis.
"""

    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system_prompt}]
    
    # Agregar historial relevante (solo los últimos mensajes para contexto)
    if history:
        # Limitar historial para no exceder el contexto del modelo
        recent_history = history[-6:] if len(history) > 6 else history
        for msg in recent_history:
            if isinstance(msg.get("content"), str):
                messages.append(msg)
    
    # Construir mensaje del usuario con la transcripción
    user_content = "TRANSCRIPCIÓN DEL VIDEO DE X.COM:\n"
    user_content += "─" * 40 + "\n"
    user_content += transcript
    user_content += "\n" + "─" * 40
    
    if truncated:
        user_content += "\n⚠️ (La transcripción fue truncada por ser muy larga. Analiza lo disponible.)"
    
    if user_instruction:
        user_content += f"\n\nINSTRUCCIÓN DEL USUARIO: {user_instruction}"
    
    messages.append({"role": "user", "content": user_content})
    
    data = {"model": MODEL, "messages": messages}
    
    try:
        logger.info(f"Enviando transcripción ({len(transcript)} chars) a OpenRouter para análisis")
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=90
        )
        
        if response.status_code != 200:
            logger.error(f"Error en API OpenRouter (video summary): Status {response.status_code}, Response: {response.text[:300]}")
            return None
        
        response_data = response.json()
        if 'choices' not in response_data or not response_data['choices']:
            logger.error(f"Respuesta inválida de API (video summary): {response_data}")
            return None
        
        content = response_data['choices'][0]['message']['content'].strip()
        logger.info(f"Resumen de video generado: {len(content)} caracteres")
        return content
        
    except requests.exceptions.Timeout:
        logger.error("Timeout en solicitud de resumen de video (90s)")
        return None
    except requests.exceptions.RequestException as re:
        logger.error(f"Error de conexión en resumen de video: {re}")
        return None
    except Exception as e:
        logger.error(f"Error inesperado en process_video_summary: {e}", exc_info=True)
        return None