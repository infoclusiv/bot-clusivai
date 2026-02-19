import os
import logging
import json
import pytz
import logging
import json
from datetime import datetime
from dateutil import rrule
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CommandHandler

from brain import process_user_input
from database import (add_reminder, get_user_reminders, get_connection, 
                      delete_reminder_by_text, update_reminder_by_id, 
                      set_daily_summary, get_users_with_daily_summary, get_today_reminders)

load_dotenv()

WEBAPP_URL = os.getenv("WEBAPP_URL")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- REVISOR DE RECORDATORIOS (Bogotá Time) ---
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    tz_bogota = pytz.timezone('America/Bogota')
    now = datetime.now(tz_bogota)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Buscamos tareas pendientes cuya fecha ya pasó
    cursor.execute('SELECT id, user_id, message, recurrence FROM reminders WHERE remind_at <= ? AND status = "pending"', (now_str,))
    due_reminders = cursor.fetchall()
    
    for rem in due_reminders:
        rem_id, user_id, msg, recurrence = rem
        try:
            alert_text = f"⏰ ¡ALERTA (ID: {rem_id})!:\n📌 {msg}"
            
            # Botón para abrir la Web App de reprogramación
            if WEBAPP_URL:
                # Pasar datos iniciales por URL
                import urllib.parse
                encoded_msg = urllib.parse.quote(msg)
                # remind_at no está cargado en la query original, vamos a cargarlo para pasarlo
                # O mejor, usamos el valor que ya tenemos en la DB
                # Por ahora pasamos el ID y el mensaje
                webapp_url_with_params = f"{WEBAPP_URL}?user_id={user_id}&id={rem_id}&message={encoded_msg}"
                keyboard = [[InlineKeyboardButton("⏳ Reprogramar", web_app=WebAppInfo(url=webapp_url_with_params))]]
                reply_markup = InlineKeyboardMarkup(keyboard)
            else:
                reply_markup = None

            await context.bot.send_message(
                chat_id=user_id, 
                text=alert_text,
                reply_markup=reply_markup
            )
            
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

# --- MANEJADOR DE MENSAJES ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    user_id = update.effective_user.id
    
    logging.info(f"Mensaje recibido de usuario {user_id}: {user_text}")
    
    await update.message.reply_chat_action("typing")
    
    # Inicializar historial si no existe
    if 'history' not in context.user_data:
        context.user_data['history'] = []
        logging.info(f"Historial inicializado para usuario {user_id}")
    
    # Recuperar historial de conversación del usuario
    history = context.user_data.get('history', [])
    
    # Obtener recordatorios activos para contexto
    active_reminders = get_user_reminders(user_id)
    
    # La IA analiza el texto con contexto de historial y recordatorios activos
    res = process_user_input(user_text, history=history, active_reminders=active_reminders)
    
    if not res:
        logging.error(f"process_user_input retornó None para usuario {user_id}")
        await update.message.reply_text("Lo siento, tuve un problema con mi conexión cerebral.")
        # Limpiar historial ante error para evitar estados corruptos
        context.user_data['history'] = []
        return
    
    logging.info(f"Respuesta de IA para usuario {user_id}: {res}")

    action = res.get("action")
    reply_message = None
    
    try:
        if action == "CREATE":
            recurrence = res.get("recurrence")
            add_reminder(user_id, res.get("message"), res.get("date"), recurrence)
            msg_recurrence = f"\n🔁 Recurrencia: {recurrence}" if recurrence else ""
            reply_message = f"✅ ¡Perfecto! He guardado tu recordatorio:\n\n📍 {res.get('message')}\n📅 {res.get('date')}{msg_recurrence}"
            
        elif action == "LIST":
            # Cambiamos para mostrar solo el botón del calendario, no la lista larga de texto
            reply_message = "📅 *Tus recordatorios:*\n\nHaz clic abajo para verlos en el calendario interactivo."
            
            # Botón para abrir la Web App en modo calendario
            if WEBAPP_URL:
                webapp_calendar_url = f"{WEBAPP_URL}?user_id={user_id}&mode=calendar"
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
                await update.message.reply_text(reply_message, parse_mode="Markdown", reply_markup=reply_markup)
            else:
                await update.message.reply_text(reply_message)
        
        # Actualizar historial con el nuevo mensaje y respuesta
        context.user_data['history'].append({"role": "user", "content": user_text})
        context.user_data['history'].append({"role": "assistant", "content": json.dumps(res, ensure_ascii=False)})
        
        # Pruning: mantener solo los últimos 6-8 mensajes (12-16 elementos con rol)
        max_history_length = 16
        if len(context.user_data['history']) > max_history_length:
            context.user_data['history'] = context.user_data['history'][-max_history_length:]
            logging.info(f"Historial podado para usuario {user_id}, nuevo tamaño: {len(context.user_data['history'])}")
    
    except Exception as e:
        logging.error(f"Error en handle_message para usuario {user_id}: {e}", exc_info=True)
        await update.message.reply_text("Hubo un error procesando tu solicitud.")
        # Limpiar historial ante error
        context.user_data['history'] = []


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
    
    application = ApplicationBuilder().token(telegram_token).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
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
