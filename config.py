import os
import discord

# ─── Minecraft server (Server List Ping) ─────────────────────────────────────
MC_SERVER_HOST = os.environ.get("MC_SERVER_HOST", "nokbox.es")
MC_SERVER_PORT = int(os.environ.get("MC_SERVER_PORT", "25565"))
MC_STATUS_REFRESH_MINUTES = int(os.environ.get("MC_STATUS_REFRESH_MINUTES", "5"))

# ─── Brand Colors ────────────────────────────────────────────────────────────
COLOR_PRIMARY = 0x5865F2  # Discord blurple
COLOR_SUCCESS = 0x00D9FF  # Green
COLOR_WARNING = 0xFEE75C  # Yellow
COLOR_ERROR = 0xED4245  # Red
COLOR_INFO = 0x00D9FF  # Light blue
COLOR_GOLD = 0xFFD700  # Gold (giveaways)
COLOR_TICKET = 0x00D9FF  # Ticket blue
COLOR_MOD = 0xFF6B35  # Moderation orange
COLOR_STAFF = 0x9B59B6  # Staff purple
COLOR_NEUTRAL = 0x2F3136  # Dark neutral

# ─── Emojis ──────────────────────────────────────────────────────────────────
EMOJI_SUCCESS = "✅"
EMOJI_ERROR = "❌"
EMOJI_WARNING = "⚠️"
EMOJI_INFO = "ℹ️"
EMOJI_TICKET = "🎫"
EMOJI_GIVEAWAY = "🎉"
EMOJI_MOD = "🛡️"
EMOJI_STAFF = "👥"
EMOJI_STATS = "📊"
EMOJI_LOCK = "🔒"
EMOJI_UNLOCK = "🔓"
EMOJI_CROWN = "👑"
EMOJI_CLOCK = "🕐"
EMOJI_STAR = "⭐"
EMOJI_FIRE = "🔥"
EMOJI_BELL = "🔔"
EMOJI_POLL = "📊"
EMOJI_SUGGEST = "💡"
EMOJI_VERIFY = "✔️"
EMOJI_WARN = "⚠️"
EMOJI_BAN = "🔨"
EMOJI_KICK = "👢"
EMOJI_MUTE = "🔇"
EMOJI_UP = "⬆️"
EMOJI_DOWN = "⬇️"

# ─── Ticket Priority ─────────────────────────────────────────────────────────
PRIORITY_LABELS = {
    "low": ("🟢 Baja", COLOR_SUCCESS),
    "medium": ("🟡 Media", COLOR_WARNING),
    "high": ("🟠 Alta", 0xFF8C00),
    "urgent": ("🔴 Urgente", COLOR_ERROR),
}

# ─── Ticket Categories ───────────────────────────────────────────────────────
TICKET_CATEGORIES = [
    discord.SelectOption(
        label="Tienda",
        value="tienda",
        emoji="🛒",
        description="Recibe ayuda relacionada con compras, pagos o problemas de la tienda.",
    ),
    discord.SelectOption(
        label="Soporte",
        value="soporte",
        emoji="🛰️",
        description="Obtén soporte general relacionado con el servidor.",
    ),
    discord.SelectOption(
        label="Apelaciones",
        value="apelaciones",
        emoji="⚖️",
        description="Solicita una revisión de una sanción recibida.",
    ),
    discord.SelectOption(
        label="Reportes",
        value="reportes",
        emoji="🎯",
        description="Reporta usuarios que estén utilizando hacks o haciendo trampas.",
    ),
    discord.SelectOption(
        label="Reportes Staff",
        value="reportes_staff",
        emoji="🛡️",
        description="Reporta comportamientos inadecuados de algún miembro del Staff.",
    ),
    discord.SelectOption(
        label="Media",
        value="media",
        emoji="🎥",
        description="Abre un ticket para postularte al equipo de Media.",
    ),
    discord.SelectOption(
        label="Revives",
        value="revives",
        emoji="❤️",
        description="Solicita un revive debido a bugs o acciones de hackers.",
    ),
]

# ─── Staff Rating Labels ──────────────────────────────────────────────────────
RATING_LABELS = {
    1: "😞 Muy malo",
    2: "😕 Malo",
    3: "😐 Regular",
    4: "😊 Bueno",
    5: "😍 Excelente",
}

# ─── AutoMod Settings ────────────────────────────────────────────────────────
AUTOMOD_SPAM_THRESHOLD = 5  # messages in window
AUTOMOD_SPAM_WINDOW = 5  # seconds
AUTOMOD_MENTION_LIMIT = 5  # mentions per message
AUTOMOD_CAPS_THRESHOLD = 0.7  # 70% caps = flagged

# Domains considered malicious or phishing (short list — extend as needed)
MALICIOUS_DOMAINS = [
    "grabify.link",
    "iplogger.org",
    "2no.co",
    "yip.su",
    "bit.ly/discord",
    "discordnitro.gift",
    "steamcommunity.ru",
]

# ─── Inactivity Reminder (tickets) ───────────────────────────────────────────
TICKET_INACTIVITY_HOURS = 24  # hours before first reminder
TICKET_ESCALATE_HOURS = 48  # hours before escalation to admin

# ─── Giveaway defaults ───────────────────────────────────────────────────────
DEFAULT_GIVEAWAY_COLOR = COLOR_GOLD
