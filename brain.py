import os
import requests
import json
import pytz
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("MODEL_NAME")

def process_user_input(text):
    # Obtener hora actual de Bogotá
    tz_bogota = pytz.timezone('America/Bogota')
    now = datetime.now(tz_bogota).strftime("%Y-%m-%d %H:%M:%S")
    
    system_prompt = f"""
    Eres 'Clusivai', un asistente personal inteligente. 
    CONTEXTO IMPORTANTE:
    - Hora actual en Bogotá, Colombia: {now}.
    - Si el usuario te pide algo para "mañana", "luego" o "en X minutos", calcula la fecha exacta basándote en la hora de Bogotá que te di.
    
    Debes responder ÚNICAMENTE con un objeto JSON con esta estructura:
    {{
        "action": "CREATE" | "LIST" | "DELETE" | "CHAT",
        "message": "descripción de la tarea (solo para recordatorios)",
        "date": "YYYY-MM-DD HH:MM:SS" (fecha calculada para CREATE),
        "reply": "Tu respuesta directa si la acción es CHAT o confirmación de acción"
    }}

    Reglas:
    - Si el usuario saluda o pregunta algo general (como "¿qué hora es?"), usa action: "CHAT".
    - Si quiere guardar un recordatorio, usa action: "CREATE".
    - Responde siempre de forma amable en español.
    """

    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ]
    }

    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data)
        content = response.json()['choices'][0]['message']['content']
        # Limpieza de formato markdown si la IA lo incluye
        content = content.replace("```json", "").replace("```", "").strip()
        return json.loads(content)
    except Exception as e:
        print(f"Error procesando IA: {e}")
        return None
