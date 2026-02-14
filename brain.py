import os
import requests
import json
import pytz
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("MODEL_NAME")

# Configurar logging para este módulo
logger = logging.getLogger(__name__)

def process_user_input(text, history=None):
    # Verificar que las variables de entorno estén configuradas
    if not API_KEY:
        logger.error("ERROR: OPENROUTER_API_KEY no está configurado")
        return None
    if not MODEL:
        logger.error("ERROR: MODEL_NAME no está configurado")
        return None
    
    logger.info(f"Procesando input con modelo: {MODEL}")
    
    # Obtener hora actual de Bogotá
    tz_bogota = pytz.timezone('America/Bogota')
    now = datetime.now(tz_bogota).strftime("%Y-%m-%d %H:%M:%S")
    
    system_prompt = f"""
    Eres 'Clusivai', un asistente personal inteligente. 
    CONTEXTO IMPORTANTE:
    - Hora actual en Bogotá, Colombia: {now}.
    - Si el usuario te pide algo para "mañana", "luego" o "en X minutos", calcula la fecha exacta basándote en la hora de Bogotá que te di.
    - TIENES ACCESO AL HISTORIAL DE CONVERSACIÓN. Úsalo para entender el contexto y resolver ambigüedades.
      Por ejemplo: Si el usuario preguntó "¿Qué recordatorio quieres eliminar?" y respondió con un nombre,
      la acción correcta es DELETE, no UPDATE.
    
    Debes responder ÚNICAMENTE con un objeto JSON con esta estructura:
    {{
        "action": "CREATE" | "LIST" | "DELETE" | "UPDATE" | "CHAT",
        "id": número de ID (solo para UPDATE),
        "message": "descripción de la tarea (solo para recordatorios)",
        "date": "YYYY-MM-DD HH:MM:SS" (fecha calculada para CREATE o UPDATE),
        "reply": "Tu respuesta directa si la acción es CHAT o confirmación de acción"
    }}

    Reglas:
    - Si el usuario saluda o pregunta algo general (como "¿qué hora es?"), usa action: "CHAT".
    - Si quiere ver sus recordatorios ("mis recordatorios", "lista", "cuáles tengo"), usa action: "LIST".
    - Si quiere crear un recordatorio, usa action: "CREATE" con la descripción de la tarea.
    - Si quiere borrar un recordatorio, usa action: "DELETE" y en el campo "message" extrae SOLO el identificador (ID numérico o palabra clave principal), sin verbos como "borra", "quitar", "elimina", etc.
      Ejemplo: Si dice "Borra el recordatorio con ID 5" → "message": "5"
      Ejemplo: Si dice "Elimina la tarea de la leche" → "message": "leche"
    - Si quiere cambiar, corregir, posponer o modificar un recordatorio existente, usa action: "UPDATE".
      Extrae el ID del recordatorio y los campos a cambiar. Incluye en el JSON solo los campos que cambian.
      IMPORTANTE: El campo "id" DEBE ser un número entero (INTEGER), no una cadena de texto.
      El ID debe extraerse de los resultados previos de LIST (ejemplo: si LIST mostró "• `5`: comprar leche", el ID es 5).
      Ejemplo: Si dice "Cambia el recordatorio 5 a las 3 PM" → {{"action": "UPDATE", "id": 5, "date": "2026-02-14 15:00:00", "reply": "..."}}
      Ejemplo: Si dice "Edita la tarea 3 a comprar pan" → {{"action": "UPDATE", "id": 3, "message": "comprar pan", "reply": "..."}}
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
        
        # Intentar parsear el JSON
        try:
            parsed_result = json.loads(content)
            logger.info(f"JSON parseado exitosamente: {parsed_result.get('action', 'UNKNOWN')}")
            
            # Validación adicional: asegurar que 'id' sea integer si existe
            if 'id' in parsed_result and parsed_result['id'] is not None:
                try:
                    parsed_result['id'] = int(parsed_result['id'])
                except (ValueError, TypeError):
                    logger.warning(f"ID no es un número válido: {parsed_result['id']}")
            
            return parsed_result
        except json.JSONDecodeError as je:
            logger.error(f"Error decodificando JSON: {je}. Contenido: {content}")
            return None
            
    except requests.exceptions.Timeout:
        logger.error("Timeout al conectar con OpenRouter (30s)")
        return None
    except requests.exceptions.RequestException as re:
        logger.error(f"Error de conexión con OpenRouter: {re}")
        return None
    except Exception as e:
        logger.error(f"Error inesperado procesando IA: {e}", exc_info=True)
        return None
