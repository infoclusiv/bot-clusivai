import os
import logging
import json
import pytz
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CommandHandler

from brain import process_user_input
from database import add_reminder, get_user_reminders, get_connection, delete_reminder_by_text, update_reminder_by_id

load_dotenv()

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- REVISOR DE RECORDATORIOS (Bogotá Time) ---
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    tz_bogota = pytz.timezone('America/Bogota')
    now_str = datetime.now(tz_bogota).strftime("%Y-%m-%d %H:%M:%S")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Buscamos tareas pendientes cuya fecha ya pasó
    cursor.execute('SELECT id, user_id, message FROM reminders WHERE remind_at <= ? AND status = "pending"', (now_str,))
    due_reminders = cursor.fetchall()
    
    for rem in due_reminders:
        rem_id, user_id, msg = rem
        try:
            await context.bot.send_message(
                chat_id=user_id, 
                text=f"⏰ ¡HOLA! Tienes este recordatorio pendiente:\n\n📌 {msg}"
            )
            cursor.execute('UPDATE reminders SET status = "sent" WHERE id = ?', (rem_id,))
        except Exception as e:
            logging.error(f"Error enviando mensaje: {e}")
            
    conn.commit()
    conn.close()

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
    
    # La IA analiza el texto con contexto de historial
    res = process_user_input(user_text, history=history)
    
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
            add_reminder(user_id, res.get("message"), res.get("date"))
            reply_message = f"✅ ¡Perfecto! He guardado tu recordatorio:\n\n📍 {res.get('message')}\n📅 {res.get('date')}"
            
        elif action == "LIST":
            reminders = get_user_reminders(user_id)
            if not reminders:
                reply_message = "No tienes recordatorios activos."
            else:
                txt = "📝 *Tus recordatorios:*\n\n"
                for r in reminders:
                    txt += f"• `{r[0]}`: {r[1]} _({r[2]})_\n"
                reply_message = txt
                
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
            
        else:
            reply_message = "No estoy seguro de qué hacer. ¿Puedes repetirlo?"
        
        # Enviar respuesta
        if reply_message:
            if action == "LIST":
                await update.message.reply_text(reply_message, parse_mode="Markdown")
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
    
    logging.info("Bot Clusivai encendido y sincronizado con Bogotá.")
    application.run_polling()
