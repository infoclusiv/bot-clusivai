import os
import requests
import json
import pytz
import logging
import re
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("MODEL_NAME")

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
    now = datetime.now(tz_bogota).strftime("%Y-%m-%d %H:%M:%S")
    
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
    CONTEXTO IMPORTANTE:
    - Hora actual en Bogotá, Colombia: {now}.
    - Si el usuario te pide algo para "mañana", "luego" o "en X minutos", calcula la fecha exacta basándote en la hora de Bogotá que te di.
    - TIENES ACCESO AL HISTORIAL DE CONVERSACIÓN. Úsalo para entender el contexto y resolver ambigüedades.
    
    {reminders_context}
    
    REGLA DE ORO SOBRE IDs:
    - Cuando el usuario quiera ACTUALIZAR, BORRAR o PREGUNTAR por un recordatorio, utiliza EXCLUSIVAMENTE los IDs listados arriba en 'RECORDATORIOS ACTIVOS ACTUALES'.
    - Si el usuario menciona un ID que NO está en la lista superior, dile amablemente que ese ID no existe y muéstrale los IDs que sí tiene disponibles.
    - No alucines IDs. Si la lista superior está vacía, el usuario no tiene nada que modificar o borrar.
    
    Debes responder ÚNICAMENTE con un objeto JSON con esta estructura:
    {{
        "action": "CREATE" | "LIST" | "DELETE" | "UPDATE" | "CHAT" | "SET_SETTING",
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
            timeout=30  # Timeout de 30 segundos
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
        logger.error("Timeout al conectar con OpenRouter (30s)")
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
