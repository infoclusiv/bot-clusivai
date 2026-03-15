<div align="center">

# 🤖 Clusivai

**Tu asistente personal inteligente en Telegram**

*Recordatorios con IA · Notas persistentes · Análisis de videos · Repositorios GitHub*

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![Flask](https://img.shields.io/badge/Flask-WebApp-000000?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![OpenRouter](https://img.shields.io/badge/OpenRouter-LLM-FF6B35?style=for-the-badge)](https://openrouter.ai)
[![SQLite](https://img.shields.io/badge/SQLite-Database-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://sqlite.org)

</div>

---

## ✨ ¿Qué es Clusivai?

**Clusivai** es un bot de Telegram con inteligencia artificial que actúa como tu asistente personal. Entiende lenguaje natural en español para gestionar recordatorios, guardar notas, resumir videos y analizar repositorios de código — todo desde el chat.

```
Tú: "Recuérdame llamar al médico el próximo viernes a las 10am"
Clusivai: ✅ Recordatorio creado para el Viernes 2026-03-20 a las 10:00
```

---

## 🗺️ Arquitectura del Sistema

```
┌─────────────────────────────────────────────────────────────────┐
│                        USUARIO (Telegram)                       │
└───────────────────────────┬─────────────────────────────────────┘
                            │  Mensajes, fotos, URLs
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                         bot.py                                  │
│           Controlador principal del bot de Telegram             │
│  • Enruta mensajes de texto, imágenes, URLs de video/repo       │
│  • Ejecuta el scheduler de recordatorios (APScheduler)          │
│  • Envía el resumen diario automático                           │
└────────┬──────────────────┬──────────────────┬──────────────────┘
         │                  │                  │
         ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────────┐  ┌────────────────┐
│   brain.py   │  │  video_handler   │  │  repo_handler  │
│              │  │  youtube_handler │  │                │
│  LLM Core    │  │                  │  │  GitIngest +   │
│ OpenRouter   │  │  yt-dlp + Groq   │  │  Análisis LLM  │
│ Text/Vision  │  │  Whisper / RapidAPI  │                │
└──────┬───────┘  └──────────────────┘  └────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────┐
│                        database.py                              │
│                    SQLite · reminders.db                        │
│   reminders │ notes │ user_settings │ note_subcategories        │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                         server.py                               │
│                   Flask REST API + WebApp                       │
│      /api/reminders  /api/notes  /api/reprogram  /api/delete    │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│               webapp/ (Telegram Mini App)                       │
│         Calendario interactivo · Gestión de notas               │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Funcionalidades

### 🔔 Recordatorios Inteligentes

El motor de IA interpreta fechas en lenguaje natural, incluso expresiones ambiguas como "el próximo sábado", "en dos horas" o "mañana al mediodía".

| Comando | Ejemplo |
|---|---|
| Crear | `"Recuérdame tomar la pastilla mañana a las 8am"` |
| Listar | `"¿Qué recordatorios tengo?"` |
| Actualizar | `"Cambia el recordatorio 5 para el martes a las 3pm"` |
| Eliminar | `"Borra el recordatorio de la reunión"` |
| Recurrente | `"Recuérdame hacer ejercicio lunes a viernes a las 6am"` |
| Con imagen | Adjuntar foto + `"Recuérdame revisar esto mañana"` |

**Soporte de recurrencia (formato RRULE):**
```
"Todos los domingos"        → FREQ=WEEKLY;BYDAY=SU
"Lunes a viernes a las 5pm" → FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR
"Cada día"                  → FREQ=DAILY
```

---

### 📝 Notas Persistentes

Sistema de notas con categorías y subcategorías, separado completamente de los recordatorios.

```
/nota Trabajo | Contraseña del VPN: admin123
/nota Recetas | Lasaña | Ingredientes: carne, bechamel...
```

- Organización en **categorías** y **subcategorías**
- Soporte de **imágenes adjuntas**
- Consultables mediante lenguaje natural: *"¿Cuál era la contraseña del wifi?"*
- Editables y eliminables desde la **Mini App web**

---

### 🎬 Resumen de Videos

Envía un enlace de **X (Twitter)** o **YouTube** y recibe un análisis completo:

```
📋 Resumen: El video habla sobre...
🔑 Puntos clave:
  • Punto 1
  • Punto 2
💡 Datos relevantes: cifras, nombres, fechas...
```

| Fuente | Método de transcripción |
|---|---|
| X.com / Twitter | `yt-dlp` + Groq Whisper API |
| YouTube | YouTube Data API v3 + RapidAPI |

---

### 🐙 Análisis de Repositorios GitHub

Envía una URL de GitHub y Clusivai analiza el código fuente completo usando **GitIngest**:

- Propósito y objetivo del proyecto
- Estructura interna y módulos clave
- Flujo principal de ejecución
- Stack tecnológico y servicios externos
- Análisis por fragmentos para repositorios grandes

---

### 📅 Mini App Web (Telegram WebApp)

Interfaz visual accesible directamente desde Telegram:

```
📅  Calendario      →  Vista mensual con puntos de recordatorios
                        Toca un día para ver y editar
📝  Notas           →  Navega por categorías y subcategorías
                        Edita o elimina notas con un tap
```

---

### 🌅 Resumen Diario Automático

Activa un listado matutino personalizado:

```
"Activa el resumen diario a las 8am"
→ SET_SETTING: daily_summary=true, daily_summary_time=08:00:00
```

---

## 📁 Estructura del Proyecto

```
clusivai/
│
├── bot.py                  # Controlador principal del bot de Telegram
├── brain.py                # Motor de IA (OpenRouter LLM + visión)
├── database.py             # Capa de datos SQLite (recordatorios, notas)
├── server.py               # API REST Flask + servidor de la Mini App
├── video_handler.py        # Descarga y transcripción de videos de X.com
├── youtube_handler.py      # Transcripción de YouTube (RapidAPI)
├── repo_handler.py         # Análisis de repositorios GitHub (GitIngest)
│
├── migrate_db.py           # Migraciones de base de datos
├── migrate_notes_image.py  # Migración: imagen en notas
│
├── webapp/
│   ├── index.html          # Mini App: estructura HTML
│   ├── script.js           # Mini App: lógica del calendario y notas
│   └── style.css           # Mini App: estilos con variables del tema Telegram
│
├── clusivai-bot.service        # Servicio systemd para el bot
├── clusivai-webapp.service     # Servicio systemd para el servidor Flask
├── clusivai-ngrok.service      # Servicio systemd para el túnel ngrok
├── start-ngrok.sh              # Script de arranque de ngrok
│
├── requirements.txt        # Dependencias Python
└── .env.example            # Variables de entorno de ejemplo
```

---

## ⚙️ Instalación

### 1. Clonar el repositorio

```bash
git clone https://github.com/tu-usuario/clusivai.git
cd clusivai
```

### 2. Crear entorno virtual e instalar dependencias

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configurar variables de entorno

```bash
cp .env.example .env
```

Edita `.env` con tus credenciales:

```env
# Telegram
TELEGRAM_TOKEN=tu-token-del-bot

# LLM (OpenRouter)
OPENROUTER_API_KEY=tu-api-key
MODEL_NAME=stepfun/step-3.5-flash:free

# Transcripción de audio (Groq Whisper)
GROQ_API_KEY=tu-groq-api-key

# Transcripción de YouTube
YOUTUBE_API_KEY=tu-youtube-data-api-key
RAPIDAPI_KEY=tu-rapidapi-key

# Servidor web
SERVER_HOST=0.0.0.0
SERVER_PORT=5000

# Exposición pública (ngrok)
PUBLIC_WEBAPP_URL=https://tu-subdominio.ngrok.app
NGROK_AUTHTOKEN=tu-authtoken
NGROK_POOLING_ENABLED=true
```

### 4. Inicializar la base de datos

```bash
python database.py
python migrate_db.py
```

### 5. Ejecutar

```bash
# Terminal 1: Servidor web
python server.py

# Terminal 2: Bot de Telegram
python bot.py

# Terminal 3: Túnel ngrok (si no usas dominio propio)
bash start-ngrok.sh
```

---

## 🛠️ Despliegue en Producción (systemd)

Para ejecutar Clusivai como servicios del sistema en Linux:

```bash
# Copiar archivos de servicio
sudo cp clusivai-bot.service /etc/systemd/system/
sudo cp clusivai-webapp.service /etc/systemd/system/
sudo cp clusivai-ngrok.service /etc/systemd/system/

# Recargar y habilitar
sudo systemctl daemon-reload
sudo systemctl enable clusivai-bot clusivai-webapp clusivai-ngrok
sudo systemctl start clusivai-bot clusivai-webapp clusivai-ngrok

# Ver estado
sudo systemctl status clusivai-bot
```

> Los servicios se reinician automáticamente ante fallos y arrancan con el sistema.

---

## 🔌 Servicios Externos Requeridos

| Servicio | Propósito | Gratuito |
|---|---|---|
| [Telegram BotFather](https://t.me/BotFather) | Token del bot | ✅ |
| [OpenRouter](https://openrouter.ai) | LLM principal (texto y visión) | ✅ (modelos free) |
| [Groq](https://console.groq.com) | Transcripción de audio (Whisper) | ✅ |
| [Google Cloud](https://console.cloud.google.com) | YouTube Data API v3 | ✅ (cuota gratuita) |
| [RapidAPI](https://rapidapi.com) | Subtítulos de YouTube | ✅ (plan free) |
| [ngrok](https://ngrok.com) | Túnel HTTPS para la Mini App | ✅ (plan free) |

---

## 🗄️ Esquema de Base de Datos

```sql
-- Recordatorios con soporte de recurrencia e imágenes
reminders (id, user_id, message, remind_at, recurrence, status, image_file_id)

-- Notas con jerarquía de categorías
notes (id, user_id, content, category, subcategory_id, image_file_id, created_at, updated_at)

-- Subcategorías de notas
note_subcategories (id, user_id, category_name, name, created_at, updated_at)

-- Configuración por usuario
user_settings (user_id, daily_summary_enabled, daily_summary_time)
```

---

## 🤝 Contribuir

1. Haz un fork del repositorio
2. Crea una rama: `git checkout -b feature/nueva-funcionalidad`
3. Realiza tus cambios y haz commit: `git commit -m "feat: descripción"`
4. Sube los cambios: `git push origin feature/nueva-funcionalidad`
5. Abre un Pull Request

---

## 📄 Licencia

Este proyecto está bajo la licencia MIT. Consulta el archivo `LICENSE` para más detalles.

---

<div align="center">

Hecho con ❤️ y mucho ☕ · Bogotá, Colombia

</div>
