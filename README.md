## ✨ Características principales

### 👥 Comunidad
| Módulo | Funcionalidad |
|--------|--------------|
| **Tickets** | Sistema completo con categorías, prioridades, cierre, rating y transcripciones (HTML/TXT) |
| **Moderación** | Warn, mute, ban, kick, lockdown/unlock con roles escalonados |
| **Automod** | Detección de spam, caps, mentions excesivas y dominios maliciosos/phishing |
| **Verificación** | Flujo de verificación de nuevos miembros con canales asignables |
| **Bienvenida/Botada** | Mensajes automáticos al entrar/salir de servidores |
| **Confesiones** | Anonimato en envíos; aprobación por staff para publicación |
| **Invites** | Tracker de invitaciones — quién trajo a quién |
| **Roles** | Auto-asignación o gestión de roles por jerarquía configurada |
| **Birthdays** | Recordatorio de cumpleaños con integración a BirthdayAPI |
| **Announcements** | Paneles de anuncios por categoría |

### 🎮 Entretenimiento & Economy
| Módulo | Funcionalidad |
|--------|--------------|
| **Economy** | Sistema de economía con dinero, inventario, balance y tienda |
| **Shop** | Tienda integrada para comprar items virtuales |
| **Giveaways** | Sorteos con reacciones, countdown y selección aleatoria de ganadores |
| **Tournaments** | Gestión de torneos con brackets y registro de participantes |
| **VoiceHubs** | Canales de voz temporales organizados por temática |
| **Suggestions** | Canal de sugerencias con upvote/downvote y seguimiento |
| **Statistics** | Panel de estadísticas del servidor (mensajes, actividad, etc.) |

### ⚙️ Avanzado
| Módulo | Funcionalidad |
|--------|--------------|
| **Logros (Achievements)** | 25 logros visuales con tarjetas generadas por Pillow, barras de progreso |
| **Ranks** | Jerarquía configurable de 10 niveles: Staff → Trial-Mod → Mod → SrMod → JrAdmin → Admin → SrAdmin → Platform-Admin → Manager → Owner |
| **Scam Guard** | Protección contra scams y enlaces peligrosos automatizada |
| **Staff Tools / Weekly** | Herramientas administrativas y reportes semanales de staff |
| **Owner** | Comandos exclusivos del propietario (reiniciar DB, logs del sistema) |

### 🌐 Integraciones externas
- **Minecraft Server Ping**: Muestra jugadores conectados en la presencia del bot (`mcstatus`)
- **BirthdayAPI**: Consulta fechas de nacimiento de usuarios Discord
- **AI Gateway (Vercel)**: Respuestas inteligentes con fallback a respuestas por defecto

---

## 🏗️ Arquitectura

```
Discord-Bot/
├── bot.py                 # Entry point — inicializa intents, carga cogs, presence loop
├── config.py              # Constantes globales: colores, emojis, settings de Automod, Tickets, Giveaways
├── requirements.txt       # Dependencias de Python
├── discord_bot.db         # Base de datos SQLite (WAL mode, busy_timeout 5s)
├── .env                   # Token, MC_SERVER_HOST, MC_SERVER_PORT (no commitear)
│
├── database/
│   └── db.py              # Capa DAO async con aiosqlite — todas las tablas y queries
│
├── cogs/                  # 21 módulos extensibles
├── utils/
│   ├── checks.py          # Jerarquía de permisos basada en rangos configurables
│   ├── embeds.py          # Constructores reutilizables de Embeds
│   └── helpers.py         # Funciones generales: parse_duration, format_duration, generate_code, progress_bar, build_ticket_html/txt
└── assets/
    ├── achievements/      # 25 insignias PNG para logros (versión grande)
    ├── achievement_icons/ # 25 íconos PNG compactos para UI
    └── fonts/             # DejaVuSans.ttf, DejaVuSans-Bold.ttf
```

---

## 📋 Stack tecnológico

| Componente | Tecnologia | Versión |
|------------|-----------|---------|
| Lenguaje | Python | 3.10+ |
| Framework Discord | discord.py | 2.6.4 |
| BD asíncrona | aiosqlite | 0.19.0 |
| HTTP assíncrono | aiohttp | 3.9.1 |
| Template engine | Jinja2 | 3.1.2 |
| Imágenes (logros) | Pillow | 10.4.0 |
| Estado Minecraft | mcstatus | 14.0.0 |
| Variables de entorno | python-dotenv | 1.0.0 |

---

## 🚀 Instalación y uso

### 1. Clonar el repositorio
```bash
git clone https://github.com/DiomedesMateo/Discord-Bot.git
cd Discord-Bot
```

### 2. Crear archivo `.env`
```env
DISCORD_BOT_TOKEN=tu_token_aquí
MC_SERVER_HOST=nokbox.es
MC_SERVER_PORT=25565
BIRTHDAY_API_KEY=tu_api_key_opcional
```

### 3. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 4. Ejecutar el bot
```bash
python bot.py
```

---

## 📄 Licencia
Este proyecto es software propietario de la comunidad asociada.
