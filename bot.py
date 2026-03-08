import os
import logging
import json
import urllib.parse
import pytz
import logging
import json
import base64
import requests
from datetime import datetime
from dateutil import rrule
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CommandHandler, CallbackQueryHandler

from brain import process_user_input, process_notes_query, process_vision_input, process_video_summary
from database import (add_reminder, get_user_reminders, get_connection, 
                      delete_reminder_by_text, update_reminder_by_id, 
                      set_daily_summary, get_users_with_daily_summary, get_today_reminders,
                      create_note, get_notes_by_user, normalize_note_category, UNCATEGORIZED_LABEL)
from video_handler import extract_x_url, download_audio, transcribe_audio, cleanup_audio

load_dotenv()

WEBAPP_URL = os.getenv("PUBLIC_WEBAPP_URL") or os.getenv("WEBAPP_URL")
VISION_MODEL = "nvidia/nemotron-nano-12b-v2-vl:free"
DEFAULT_MODEL = os.getenv("MODEL_NAME")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)


def build_webapp_url(**params):
    if not WEBAPP_URL:
        return None

    parsed_url = urllib.parse.urlsplit(WEBAPP_URL)
    merged_params = dict(urllib.parse.parse_qsl(parsed_url.query, keep_blank_values=True))

    for key, value in params.items():
        if value is not None:
            merged_params[key] = str(value)

    return urllib.parse.urlunsplit((
        parsed_url.scheme,
        parsed_url.netloc,
        parsed_url.path,
        urllib.parse.urlencode(merged_params),
        parsed_url.fragment,
    ))


if not WEBAPP_URL:
    logging.warning("No PUBLIC_WEBAPP_URL/WEBAPP_URL configured; Telegram WebApp buttons will be disabled")


def parse_note_category_and_content(raw_text):
    """Parsea el formato `/nota categoria | contenido` de forma retrocompatible."""
    content = (raw_text or '').strip()
    if not content:
        return None, ''

    if '|' not in content:
        return None, content

    raw_category, raw_content = content.split('|', 1)
    category = normalize_note_category(raw_category)
    parsed_content = raw_content.strip()

    if category and parsed_content:
        return category, parsed_content

    return None, content

# --- REVISOR DE RECORDATORIOS (Bogotá Time) ---
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    tz_bogota = pytz.timezone('America/Bogota')
    now = datetime.now(tz_bogota)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Buscamos tareas pendientes cuya fecha ya pasó - INCLUIMOS image_file_id
    cursor.execute('SELECT id, user_id, message, recurrence, image_file_id FROM reminders WHERE remind_at <= ? AND status = "pending"', (now_str,))
    due_reminders = cursor.fetchall()
    
    for rem in due_reminders:
        rem_id, user_id, msg, recurrence, image_file_id = rem
        try:
            alert_text = f"⏰ ¡ALERTA (ID: {rem_id})!:\n📌 {msg}"
            
            # Botón para abrir la Web App de reprogramación
            webapp_url_with_params = build_webapp_url(user_id=user_id, id=rem_id, message=msg)
            if webapp_url_with_params:
                keyboard = [[InlineKeyboardButton("⏳ Reprogramar", web_app=WebAppInfo(url=webapp_url_with_params))]]
                reply_markup = InlineKeyboardMarkup(keyboard)
            else:
                reply_markup = None

            # ENVÍO DE ALERTA: Si hay imagen, enviar foto; si no, enviar mensaje
            if image_file_id:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=image_file_id,
                    caption=alert_text,
                    reply_markup=reply_markup
                )
                logging.info(f"Alerta con foto enviada al usuario {user_id} para recordatorio {rem_id}")
            else:
                await context.bot.send_message(
                    chat_id=user_id, 
                    text=alert_text,
                    reply_markup=reply_markup
                )
                logging.info(f"Alerta de texto enviada al usuario {user_id} para recordatorio {rem_id}")
            
            if recurrence:
                # Calcular la siguiente ocurrencia
                try:
                    # Parsear la regla RRULE
                    rule = rrule.rrulestr(recurrence, dtstart=now.replace(tzinfo=None))
                    next_occurrence = rule.after(now.replace(tzinfo=None))
                    
                    if next_occurrence:
                        new_date_str = next_occurrence.strftime("%Y-%m-%d %H:%M:%S")
                        cursor.execute('UPDATE reminders SET remind_at = ? WHERE id = ?', (new_date_str, rem_id))
                        logging.info(f"Recordatorio recurrente {rem_id} reprogramado para {new_date_str}")
                    else:
                        cursor.execute('UPDATE reminders SET status = "sent" WHERE id = ?', (rem_id,))
                except Exception as ex:
                    logging.error(f"Error calculando recurrencia para {rem_id}: {ex}")
                    cursor.execute('UPDATE reminders SET status = "sent" WHERE id = ?', (rem_id,))
            else:
                cursor.execute('UPDATE reminders SET status = "sent" WHERE id = ?', (rem_id,))
            
            # Registrar el alerta en el historial del usuario para que la IA tenga contexto
            # Usamos context.application.user_data para acceder al historial fuera de un MessageHandler
            if user_id in context.application.user_data:
                user_data = context.application.user_data[user_id]
                if 'history' not in user_data:
                    user_data['history'] = []
                
                # Agregamos un mensaje ficticio del asistente que describe la alerta
                # Esto permite que la IA vea el ID y el mensaje enviado en el historial
                user_data['history'].append({
                    "role": "assistant", 
                    "content": json.dumps({
                        "action": "ALERT", 
                        "id": rem_id, 
                        "message": msg,
                        "reply": alert_text
                    }, ensure_ascii=False)
                })
                
                # Limitar historial
                if len(user_data['history']) > 16:
                    user_data['history'] = user_data['history'][-16:]
                    
        except Exception as e:
            logging.error(f"Error enviando mensaje: {e}")
            
    conn.commit()
    conn.close()

# --- JOB: RESUMEN DIARIO ---
async def send_daily_summaries(context: ContextTypes.DEFAULT_TYPE):
    """Envía el listado de recordatorios de hoy a los usuarios con la función activa."""
    users = get_users_with_daily_summary()
    tz_bogota = pytz.timezone('America/Bogota')
    now = datetime.now(tz_bogota)
    
    # El job se programa para Mon-Fri, pero validamos por si acaso
    if now.weekday() >= 5: # 5=Saturday, 6=Sunday
        return

    for user_id, summary_time in users:
        try:
            reminders = get_today_reminders(user_id)
            if not reminders:
                msg = "☀️ ¡Buenos días! Para hoy no tienes recordatorios programados. ¡Que tengas un excelente día!"
            else:
                msg = "☀️ *¡Buenos días! Aquí tienes tus recordatorios para hoy:*\n\n"
                for r_id, r_msg, r_time in reminders:
                    # Formatear la hora
                    dt = datetime.strptime(r_time, "%Y-%m-%d %H:%M:%S")
                    time_str = dt.strftime("%H:%M")
                    msg += f"• `{time_str}`: {r_msg}\n"
            
            await context.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
            logging.info(f"Resumen diario enviado a usuario {user_id}")
            
        except Exception as e:
            logging.error(f"Error enviando resumen diario a {user_id}: {e}")

# --- FUNCIÓN AUXILIAR: Descargar imagen de Telegram y convertir a base64 ---
async def download_image_to_base64(update: Update) -> str | None:
    """Descarga la imagen de un mensaje de Telegram y la convierte a base64.
    
    Returns:
        String con la imagen en formato data:image/jpeg;base64,... o None si falla
    """
    try:
        # Obtener la foto (tomar la última que es la de mayor resolución)
        photo = update.message.photo[-1] if update.message.photo else None
        
        if not photo:
            # Intentar con documento
            document = update.message.document
            if document:
                # Verificar si es una imagen
                if document.mime_type and document.mime_type.startswith('image/'):
                    photo = document
                else:
                    return None
            else:
                return None
        
        # Descargar la imagen
        bot = update.message.bot
        file = await bot.get_file(photo.file_id)
        
        # Descargar el contenido
        image_content = await file.download_as_bytearray()
        
        # Determinar el tipo MIME
        mime_type = "image/jpeg"
        if photo == update.message.document:
            # Es un documento
            mime_type = photo.mime_type or "image/jpeg"
        elif hasattr(photo, 'mime_type') and photo.mime_type:
            mime_type = photo.mime_type
        
        # Convertir a base64
        image_base64 = base64.b64encode(image_content).decode('utf-8')
        
        # Retornar en formato data URI
        return f"data:{mime_type};base64,{image_base64}"
        
    except Exception as e:
        logging.error(f"Error descargando imagen: {e}")
        return None

# --- PROCESAMIENTO DE VIDEOS DE X.COM ---
async def process_x_video(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, user_text: str):
    """Pipeline completo: descarga audio → transcribe → analiza → responde.
    
    Args:
        update: Update de Telegram
        context: Contexto del bot
        url: URL del tweet/post de X.com
        user_text: Texto completo del mensaje del usuario
    """
    user_id = update.effective_user.id
    audio_path = None
    
    logging.info(f"Procesando video de X.com para usuario {user_id}: {url}")
    
    # Inicializar historial si no existe
    if 'history' not in context.user_data:
        context.user_data['history'] = []
    
    history = context.user_data.get('history', [])
    
    try:
        # ── PASO 1: Descargar audio ──
        status_msg = await update.effective_message.reply_text("⏳ Descargando audio del video de X...")
        
        audio_result, info_or_error = download_audio(url)
        
        if audio_result is None:
            # info_or_error contiene el mensaje de error
            await status_msg.edit_text(f"❌ {info_or_error}")
            return
        
        audio_path = audio_result
        video_info = info_or_error  # En caso de éxito, es el dict con info del video
        
        # Mostrar info del video si está disponible
        duration_str = ""
        if video_info.get('duration'):
            mins = int(video_info['duration']) // 60
            secs = int(video_info['duration']) % 60
            duration_str = f" ({mins}:{secs:02d})"
        
        # ── PASO 2: Transcribir audio ──
        await status_msg.edit_text(f"🎙️ Transcribiendo audio{duration_str}...")
        
        transcript, error = transcribe_audio(audio_path)
        
        if transcript is None:
            await status_msg.edit_text(f"❌ {error}")
            return
        
        # ── PASO 3: Analizar con LLM ──
        await status_msg.edit_text("🧠 Analizando contenido del video...")
        
        # Extraer instrucción del usuario (quitar la URL del texto)
        user_instruction = user_text.replace(url, '').strip()
        # Limpiar instrucciones vacías o solo con espacios/signos
        if user_instruction and len(user_instruction.strip('., ')) < 3:
            user_instruction = None
        
        summary = process_video_summary(transcript, user_instruction or None, history)
        
        # ── PASO 4: Enviar resultado ──
        await status_msg.delete()
        
        if summary:
            # Construir respuesta final
            header = "🎬 **Análisis del video de X.com**\n"
            if video_info.get('uploader') and video_info['uploader'] != 'Desconocido':
                header += f"👤 _{video_info['uploader']}_"
                if duration_str:
                    header += f" • ⏱️ {duration_str.strip(' ()')}"
                header += "\n"
            header += "\n"
            
            response_text = header + summary
        else:
            # Fallback: si el LLM falla, enviar la transcripción directamente
            response_text = (
                "⚠️ No pude generar el análisis automático. "
                "Aquí tienes la transcripción del video:\n\n"
                f"📝 _{transcript[:3000]}_"
            )
            if len(transcript) > 3000:
                response_text += "\n\n_(Transcripción truncada)_"
        
        # Enviar respuesta (manejar mensajes largos)
        if len(response_text) > 4096:
            # Dividir en chunks respetando el límite de Telegram
            chunks = split_message(response_text, 4096)
            for chunk in chunks:
                await update.effective_message.reply_text(chunk, parse_mode="Markdown")
        else:
            try:
                await update.effective_message.reply_text(response_text, parse_mode="Markdown")
            except Exception:
                # Si falla el Markdown, enviar sin formato
                await update.effective_message.reply_text(response_text.replace('*', '').replace('_', ''))
        
        # ── Actualizar historial de conversación ──
        # Guardar contexto del video para que el usuario pueda hacer preguntas de seguimiento
        context.user_data['history'].append({
            "role": "user", 
            "content": f"[Compartí un video de X.com: {url}]. {user_instruction or 'Analízalo.'}"
        })
        
        # Guardar el transcript en el historial (truncado) para preguntas de seguimiento
        assistant_context = {
            "action": "VIDEO_ANALYSIS",
            "url": url,
            "transcript": transcript[:2000],  # Truncar para no llenar el historial
            "summary": (summary or "No disponible")[:1000],
            "reply": summary or "Transcripción enviada directamente."
        }
        context.user_data['history'].append({
            "role": "assistant", 
            "content": json.dumps(assistant_context, ensure_ascii=False)
        })
        
        # Podar historial
        max_history_length = 16
        if len(context.user_data['history']) > max_history_length:
            context.user_data['history'] = context.user_data['history'][-max_history_length:]
        
        logging.info(f"Video de X.com procesado exitosamente para usuario {user_id}")
        
    except Exception as e:
        logging.error(f"Error procesando video de X.com para usuario {user_id}: {e}", exc_info=True)
        if update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "❌ Hubo un error inesperado procesando el video. Intenta de nuevo."
                )
            except Exception:
                pass
    
    finally:
        # Siempre limpiar archivos temporales
        if audio_path:
            cleanup_audio(audio_path)


def split_message(text, max_length=4096):
    """Divide un texto largo en chunks que respeten el límite de Telegram.
    
    Intenta cortar en saltos de línea para no romper oraciones.
    """
    if len(text) <= max_length:
        return [text]
    
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        
        # Buscar el último salto de línea dentro del límite
        cut_point = text.rfind('\n', 0, max_length)
        if cut_point == -1 or cut_point < max_length // 2:
            # Si no hay salto de línea conveniente, cortar en espacio
            cut_point = text.rfind(' ', 0, max_length)
        if cut_point == -1:
            cut_point = max_length
        
        chunks.append(text[:cut_point])
        text = text[cut_point:].lstrip()
    
    return chunks

# --- MANEJADOR DE MENSAJES ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Intentar obtener el texto del mensaje o de la leyenda (caption) de una imagen/archivo
    user_text = update.message.text or update.message.caption
    user_id = update.effective_user.id
    
    # ── DETECCIÓN DE ENLACE DE TWITTER / X.COM ──
    # Si el mensaje contiene una URL de X.com/Twitter, mostramos opciones
    if user_text:
        x_url = extract_x_url(user_text)
        if x_url:
            logging.info(f"Enlace de Twitter/X detectado para usuario {user_id}: {x_url}")
            
            # Guardar la URL para usarla en el callback (callback_data tiene límite de 64 bytes)
            # Usamos un ID de mensaje o timestamp para evitar colisiones
            msg_id = update.message.message_id
            if 'x_urls' not in context.user_data:
                context.user_data['x_urls'] = {}
            context.user_data['x_urls'][str(msg_id)] = {
                'url': x_url,
                'text': user_text
            }
            
            keyboard = [
                [
                    InlineKeyboardButton("🎬 Analizar Video", callback_data=f"x_video:{msg_id}"),
                    InlineKeyboardButton("⏰ Recordatorio", callback_data=f"x_reminder:{msg_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "🐦 He detectado un enlace de Twitter/X. ¿Qué te gustaría hacer?",
                reply_markup=reply_markup
            )
            return

    await process_normal_message(update, context, user_text, user_id)

async def process_normal_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str, user_id: int):
    """Procesamiento normal de mensajes (IA, recordatorios, notas, etc.)"""
    
    # Verificar si hay una imagen adjunta
    msg = update.effective_message
    has_image = msg and (msg.photo or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith('image/')))
    
    if not user_text and not has_image:
        # Si no hay texto ni imagen, salir
        return
    
    # 1. CAPTURA DE IMAGEN: Guardar el file_id sin enviar a IA
    if has_image:
        # Obtener el file_id de la foto (tomamos la de mayor resolución)
        if msg.photo:
            photo_id = msg.photo[-1].file_id
        else:
            photo_id = msg.document.file_id
        
        context.user_data['pending_image_id'] = photo_id
        logging.info(f"Imagen recibida para usuario {user_id}: {photo_id}")
        
        # Si no hay caption de texto, solicitar que escriba qué quiere hacer
        if not user_text:
            await msg.reply_text("📸 Imagen recibida y guardada. Ahora escríbeme qué quieres hacer con ella.\n\nEjemplo: 'Recuérdame esto mañana a las 8am'")
            return
    
    # 2. RECUPERAR IMAGE_ID PENDIENTE (si existe de un mensaje anterior)
    image_to_save = context.user_data.get('pending_image_id')
    
    logging.info(f"Mensaje recibido de usuario {user_id}: {user_text} (con imagen_id: {bool(image_to_save)})")
    
    # Usar el chat efectivo para acciones que no dependen de un mensaje específico
    chat = update.effective_chat
    await chat.send_action("typing")
    
    # Inicializar historial si no existe
    if 'history' not in context.user_data:
        context.user_data['history'] = []
        logging.info(f"Historial inicializado para usuario {user_id}")
    
    # Recuperar historial de conversación del usuario
    history = context.user_data.get('history', [])
    
    # Obtener recordatorios activos para contexto
    active_reminders = get_user_reminders(user_id)
    
    # 3. PROCESAR TEXTO (si hay imagen pendiente, enriquecer el texto para que la IA lo sepa)
    text_to_process = user_text
    if image_to_save:
        text_to_process = f"{user_text}\n[📸 El usuario adjuntó una imagen a este mensaje]"
    
    try:
        res = process_user_input(text_to_process, history=history, active_reminders=active_reminders)
        
        if not res:
            logging.error(f"process_user_input retornó None para usuario {user_id}")
            await update.effective_message.reply_text("Lo siento, tuve un problema con mi conexión cerebral.")
            # Limpiar historial ante error para evitar estados corruptos
            context.user_data['history'] = []
            return
        
        logging.info(f"Respuesta de IA para usuario {user_id}: {res}")

        action = res.get("action")
        reply_message = None
        
        if action == "CREATE":
            recurrence = res.get("recurrence")
            date_str = res.get("date")
            message = res.get("message")

            # Validar que tengamos una fecha para el recordatorio
            if not date_str:
                # No intentamos guardar en base de datos para evitar errores de integridad
                await update.effective_message.reply_text(
                    "Necesito saber cuándo quieres que te recuerde esto. "
                    "Por ejemplo: \"mañana a las 9am\" o \"el viernes a las 18:00\"."
                )
                return

            # Asegurar que el mensaje no esté vacío
            if not message or not str(message).strip():
                message = "Revisar enlace compartido"
            
            # Recuperar imagen pendiente (si existe)
            image_file_id = context.user_data.get('pending_image_id')
            
            # Guardar recordatorio con imagen
            add_reminder(user_id, message, date_str, recurrence, image_file_id)
            
            # Limpiar imagen pendiente después de guardar
            if 'pending_image_id' in context.user_data:
                del context.user_data['pending_image_id']
            
            msg_recurrence = f"\n🔁 Recurrencia: {recurrence}" if recurrence else ""
            emoji = "🖼️" if image_file_id else "✅"
            
            # Formatear la fecha para mostrar el día de la semana
            try:
                dt_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                # Crear diccionario de días en español
                dias = {0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves", 4: "Viernes", 5: "Sábado", 6: "Domingo"}
                dia_semana = dias[dt_obj.weekday()]
                fecha_formateada = f"{dia_semana} {date_str}"
            except Exception:
                fecha_formateada = date_str
            
            reply_message = f"{emoji} ¡Perfecto! He guardado tu recordatorio:\n\n📍 {message}\n📅 {fecha_formateada}{msg_recurrence}"
            
        elif action == "LIST":
            # Cambiamos para mostrar solo el botón del calendario, no la lista larga de texto
            reply_message = "📅 *Tus recordatorios:*\n\nHaz clic abajo para verlos en el calendario interactivo."
            
            # Botón para abrir la Web App en modo calendario
            webapp_calendar_url = build_webapp_url(user_id=user_id, mode="calendar")
            if webapp_calendar_url:
                keyboard = [[InlineKeyboardButton("📅 Ver en Calendario", web_app=WebAppInfo(url=webapp_calendar_url))]]
                reply_markup = InlineKeyboardMarkup(keyboard)
            else:
                reply_markup = None
                
        elif action == "DELETE":
            search_identifier = res.get("message")
            deleted_count = delete_reminder_by_text(user_id, search_identifier)
            if deleted_count > 0:
                reply_message = f"🗑️ He eliminado {deleted_count} recordatorio(s) relacionado(s) con '{search_identifier}'."
            else:
                reply_message = f"No encontré ningún recordatorio activo que coincida con '{search_identifier}'."
            
        elif action == "UPDATE":
            reminder_id = res.get("id")
            new_message = res.get("message")
            new_date = res.get("date")
            
            logging.info(f"UPDATE request - User: {user_id}, ID: {reminder_id}, Message: {new_message}, Date: {new_date}")
            
            # Validar que el ID esté presente
            if reminder_id is None:
                reply_message = "❌ No pude identificar qué recordatorio deseas modificar. ¿Cuál es el ID?"
                logging.warning(f"UPDATE falló: ID no proporcionado para usuario {user_id}")
            # Validar que al menos un campo a cambiar esté presente
            elif new_message is None and new_date is None:
                reply_message = "❌ Necesito saber qué quieres cambiar (la descripción, la fecha/hora, o ambas)."
                logging.warning(f"UPDATE falló: Sin campos a actualizar para usuario {user_id}")
            else:
                # Asegurar que reminder_id sea integer
                try:
                    reminder_id = int(reminder_id)
                except (ValueError, TypeError) as e:
                    reply_message = f"❌ El ID '{reminder_id}' no es un número válido."
                    logging.error(f"UPDATE falló: ID inválido '{reminder_id}' para usuario {user_id}: {e}")
                else:
                    # Llamar a la función de actualización
                    try:
                        success = update_reminder_by_id(user_id, reminder_id, new_message, new_date)
                        if success:
                            changes = []
                            if new_message:
                                changes.append(f"descripción: {new_message}")
                            if new_date:
                                changes.append(f"fecha/hora: {new_date}")
                            reply_message = f"✏️ ¡Listo! He actualizado el recordatorio #{reminder_id}:\n- {' y '.join(changes)}"
                            logging.info(f"UPDATE exitoso: Recordatorio #{reminder_id} actualizado para usuario {user_id}")
                        else:
                            reply_message = f"❌ No encontré un recordatorio activo con ID {reminder_id}."
                            logging.warning(f"UPDATE falló: Recordatorio #{reminder_id} no encontrado para usuario {user_id}")
                    except Exception as e:
                        reply_message = f"❌ Error al actualizar el recordatorio: {str(e)}"
                        logging.error(f"UPDATE error en database para usuario {user_id}: {e}", exc_info=True)
            
        elif action == "CONSULTAR_NOTAS":
            # Recuperar notas del usuario y hacer segunda llamada al LLM
            user_notes = get_notes_by_user(user_id)
            notes_response = process_notes_query(user_text, user_notes, history)
            reply_message = notes_response if notes_response else "No pude consultar tus notas en este momento."
            
        elif action == "CHAT":
            # Respuesta directa de la IA (incluyendo preguntas como ¿qué hora es?)
            reply_message = res.get("reply")
            
        elif action == "SET_SETTING":
            setting_name = res.get("setting_name")
            value = res.get("value")
            
            if setting_name == "daily_summary":
                set_daily_summary(user_id, enabled=value)
                state = "activado" if value else "desactivado"
                reply_message = f"🔔 He {state} tu resumen diario de lunes a viernes."
            elif setting_name == "daily_summary_time":
                # Asumimos que si cambia la hora, quiere activarlo también
                set_daily_summary(user_id, enabled=True, time=value)
                reply_message = f"🕒 Listo, ahora recibirás tu resumen diario a las {value}."
            else:
                reply_message = "Entendido, he guardado ese ajuste."
            
        else:
            reply_message = "No estoy seguro de qué hacer. ¿Puedes repetirlo?"
        
        # Enviar respuesta
        if reply_message:
            if action == "LIST":
                await update.effective_message.reply_text(reply_message, parse_mode="Markdown", reply_markup=reply_markup)
            else:
                await update.effective_message.reply_text(reply_message)
        
        # Actualizar historial con el nuevo mensaje y respuesta
        context.user_data['history'].append({"role": "user", "content": user_text})
        context.user_data['history'].append({"role": "assistant", "content": json.dumps(res, ensure_ascii=False)})
        
        # Pruning: mantener solo los últimos 6-8 mensajes (12-16 elementos con rol)
        max_history_length = 16
        if len(context.user_data['history']) > max_history_length:
            context.user_data['history'] = context.user_data['history'][-max_history_length:]
            logging.info(f"Historial podado para usuario {user_id}, nuevo tamaño: {len(context.user_data['history'])}")
    
    except Exception as e:
        logging.error(f"Error en process_normal_message para usuario {user_id}: {e}", exc_info=True)
        try:
            if update.effective_message:
                await update.effective_message.reply_text("Hubo un error procesando tu solicitud.")
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="Hubo un error procesando tu solicitud."
                )
        except Exception:
            # Fallback si incluso el envío del mensaje de error falla
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="Hubo un error procesando tu solicitud."
                )
            except Exception:
                pass
        # Limpiar historial ante error
        context.user_data['history'] = []

async def x_link_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja las opciones elegidas para enlaces de Twitter/X."""
    query = update.callback_query
    await query.answer()
    
    try:
        data = query.data
        user_id = update.effective_user.id
        
        if ":" not in data:
            return
            
        action, msg_id = data.split(":", 1)
        
        x_data = context.user_data.get('x_urls', {}).get(msg_id)
        if not x_data:
            await query.edit_message_text("❌ Lo siento, la información de este enlace ya no está disponible.")
            return
            
        url = x_data['url']
        user_text = x_data['text']
        
        logging.info(f"Callback detectado: {action} para el mensaje {msg_id}")
        
        if action == "x_video":
            # Editar el mensaje para indicar que se está procesando
            await query.edit_message_text("⏳ Iniciando análisis del video...")
            await process_x_video(update, context, url, user_text)
        elif action == "x_reminder":
            # Editar el mensaje para indicar que se está procesando el recordatorio
            await query.edit_message_text("⏳ Preparando tu recordatorio...")
            
            # Enriquecer el texto para que la IA sepa que la intención es un recordatorio.
            # Incluimos una instrucción más clara con contexto de fecha.
            instruction = (
                f"Quiero agendar un recordatorio para revisar este enlace: {url}. "
                "Si no indico claramente cuándo, pregúntame para cuándo quiero el recordatorio."
            )
            
            # Extraer texto adicional del usuario (sin la URL) para dar más contexto
            extra_text = user_text.replace(url, '').strip()
            if extra_text and len(extra_text) > 2:
                instruction = f"Quiero agendar un recordatorio: {extra_text}. Enlace: {url}"
            
            await process_normal_message(update, context, instruction, user_id)
    except Exception as e:
        logging.error(f"Error en x_link_callback_handler: {e}", exc_info=True)
        try:
            await query.edit_message_text("❌ Ocurrió un error al procesar tu solicitud.")
        except Exception:
            # Si el mensaje ya fue editado o eliminado, intentar enviar uno nuevo
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="❌ Ocurrió un error al procesar tu solicitud."
                )
            except Exception:
                pass


async def post_init(application):
    """Configura el botón de menú después de que la aplicación haya iniciado."""
    from telegram import MenuButtonWebApp
    try:
        # Configurar el botón de menú para abrir la Web App en modo calendario
        url = build_webapp_url(mode="calendar")
        if not url:
            logging.warning("No webapp URL configured; skipping Telegram menu button setup")
            return

        await application.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Calendar", web_app=WebAppInfo(url=url))
        )
        logging.info(f"Botón de menú configurado con URL: {url}")
    except Exception as e:
        logging.error(f"Error configurando el botón de menú: {e}")

# --- HANDLER PARA COMANDO /nota ---
# --- HANDLER PARA /nota CON FOTO ---
async def nota_photo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda una nota con imagen cuando el usuario envía una foto con caption /nota."""
    user_id = update.effective_user.id
    caption = update.message.caption or ""

    # Extraer texto después de /nota
    parts = caption.split(None, 1)
    raw_note_text = parts[1].strip() if len(parts) > 1 else ""
    category, content = parse_note_category_and_content(raw_note_text)

    # Obtener file_id de la imagen
    image_file_id = None
    if update.message.photo:
        image_file_id = update.message.photo[-1].file_id
    elif update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith('image/'):
        image_file_id = update.message.document.file_id

    if not image_file_id:
        await update.message.reply_text("⚠️ No pude detectar la imagen. Intenta de nuevo.")
        return

    # Si no hay texto, usar placeholder
    if not content:
        content = "📸 Imagen"

    create_note(user_id, content, image_file_id, category)
    saved_category = category or UNCATEGORIZED_LABEL
    await update.message.reply_text(f"✅ Nota con imagen guardada en la categoría: {saved_category}.")
    logging.info(f"Nota con imagen guardada para usuario {user_id}: {content[:50]}... cat={saved_category} (img: {image_file_id[:20]}...)")

# --- HANDLER PARA COMANDO /nota ---
async def nota_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda una nota persistente directamente sin pasar por brain.py."""
    user_id = update.effective_user.id
    # Extraer texto después de /nota
    text = update.message.text
    raw_note_text = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
    category, content = parse_note_category_and_content(raw_note_text)

    # Verificar si hay una imagen pendiente de un mensaje anterior
    image_file_id = context.user_data.get('pending_image_id')

    if not content.strip() and not image_file_id:
        await update.message.reply_text(
            "⚠️ Por favor, escribe lo que deseas guardar después del comando.\n"
            "Ejemplo: /nota La clave del wifi es ABC123\n"
            "Con categoría: /nota Trabajo | Preparar informe\n"
            "También puedes enviar una foto con /nota como leyenda."
        )
        return

    # Si no hay texto pero sí imagen pendiente, usar placeholder
    if not content.strip():
        content = "📸 Imagen"

    create_note(user_id, content.strip(), image_file_id, category)

    # Limpiar imagen pendiente después de guardar
    if 'pending_image_id' in context.user_data:
        del context.user_data['pending_image_id']

    emoji = "🖼️" if image_file_id else "✅"
    saved_category = category or UNCATEGORIZED_LABEL
    await update.message.reply_text(f"{emoji} Nota guardada correctamente en la categoría: {saved_category}.")
    logging.info(f"Nota guardada para usuario {user_id}: {content[:50]}... cat={saved_category} (img: {bool(image_file_id)})")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 ¡Hola! Soy Clusivai. Puedo chatear contigo y gestionar tus recordatorios. ¡Pruébame!")

if __name__ == '__main__':
    # Verificar variables de entorno
    telegram_token = os.getenv("TELEGRAM_TOKEN")
    if not telegram_token:
        logging.error("ERROR: TELEGRAM_TOKEN no está configurado en las variables de entorno")
        exit(1)
    
    logging.info(f"TELEGRAM_TOKEN configurado: {telegram_token[:10]}...")
    
    # Inicializar DB
    from database import init_db
    init_db()
    logging.info("Base de datos inicializada correctamente")
    
    application = ApplicationBuilder().token(telegram_token).post_init(post_init).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("nota", nota_command))
    # Nuevo: fotos/documentos-imagen con caption /nota
    application.add_handler(MessageHandler(
        (filters.PHOTO | filters.Document.IMAGE) & filters.CaptionRegex(r'^/nota'),
        nota_photo_command
    ))
    application.add_handler(CallbackQueryHandler(x_link_callback_handler, pattern=r"^x_"))
    application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & (~filters.COMMAND), handle_message))
    
    # Programar el revisor cada 60 segundos
    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=60, first=10)
    
    # Programar resumen diario a las 7:45 AM Bogotá (Mon-Fri)
    from datetime import time
    tz_bogota = pytz.timezone('America/Bogota')
    daily_time = time(7, 45, 0, tzinfo=tz_bogota)
    job_queue.run_daily(send_daily_summaries, daily_time, days=(0, 1, 2, 3, 4))
    
    logging.info("Bot Clusivai encendido y sincronizado con Bogotá.")
    application.run_polling()
