"""
General utility functions used across cogs.
"""
import re
import asyncio
import random
import string
import discord
from datetime import datetime, timedelta
from typing import Optional


async def get_announce_channel(guild: discord.Guild) -> Optional[discord.abc.Messageable]:
    """Devuelve el canal configurado con /logros canal para anunciar logros
    desbloqueados y subidas de nivel, o None si el servidor no configuró
    ninguno (en cuyo caso cada cog cae de vuelta a su comportamiento por
    defecto, ej. el canal donde ocurrió la acción)."""
    from database import db  # import diferido para evitar ciclos de import

    config = await db.get_guild_config(guild.id)
    channel_id = config.get("achievements_channel")
    if not channel_id:
        return None

    channel = guild.get_channel(channel_id)
    if channel:
        return channel

    try:
        return await guild.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


async def notify_owner(bot: discord.Client, title: str, description: str,
                        color: int = 0xFEE75C) -> None:
    """
    Manda un DM al dueño de la aplicación del bot (bot.owner_id) avisando
    de una acción sensible. Usado para /reiniciar-tickets y /reiniciar-bot.

    Silenciosamente no hace nada si el owner_id no está seteado (por
    ejemplo si application_info() falló en setup_hook) o si el DM falla
    (el dueño tiene los DMs cerrados, etc.) — nunca debe romper el comando
    que lo llama.
    """
    if not bot.owner_id:
        return
    try:
        owner = bot.get_user(bot.owner_id) or await bot.fetch_user(bot.owner_id)
        embed = discord.Embed(title=title, description=description, color=color)
        embed.timestamp = datetime.utcnow()
        await owner.send(embed=embed)
    except Exception:
        pass  # nunca interrumpir el comando original por un DM fallido


def parse_duration(text: str) -> Optional[timedelta]:
    """Parse a duration string like '1h', '30m', '2d' into a timedelta."""
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    match = re.fullmatch(r"(\d+)([smhdw])", text.strip().lower())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    return timedelta(seconds=value * units[unit])


def format_duration(td: timedelta) -> str:
    """Format a timedelta as a human-readable string."""
    total = int(td.total_seconds())
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h {(total % 3600) // 60}m"
    return f"{total // 86400}d {(total % 86400) // 3600}h"


def timestamp_to_unix(dt: datetime) -> int:
    return int(dt.timestamp())


def pick_winners(entries: list, count: int) -> list:
    """Randomly pick `count` unique winners from entries list."""
    count = min(count, len(entries))
    return random.sample(entries, count)


def generate_code(length: int = 6) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def truncate(text: str, max_len: int = 1024) -> str:
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def has_any_role(member: discord.Member, role_ids: list[int]) -> bool:
    return any(r.id in role_ids for r in member.roles)


def ordinal(n: int) -> str:
    suffixes = {1: "st", 2: "nd", 3: "rd"}
    if 11 <= n % 100 <= 13:
        suffix = "th"
    else:
        suffix = suffixes.get(n % 10, "th")
    return f"{n}{suffix}"


async def safe_send(channel: discord.abc.Messageable, **kwargs) -> Optional[discord.Message]:
    """Send a message, silently ignoring missing permissions."""
    try:
        return await channel.send(**kwargs)
    except (discord.Forbidden, discord.HTTPException):
        return None


async def safe_delete(message: discord.Message) -> None:
    """Delete a message, silently ignoring missing permissions."""
    try:
        await message.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass


def progress_bar(value: int, maximum: int, length: int = 10) -> str:
    """Return a text progress bar."""
    filled = round((value / maximum) * length) if maximum else 0
    return "█" * filled + "░" * (length - filled)


def format_timestamp(iso: str) -> str:
    """Format an ISO timestamp string for display."""
    try:
        dt = datetime.fromisoformat(iso)
        return f"<t:{int(dt.timestamp())}:F>"
    except Exception:
        return iso


def build_ticket_html(ticket: dict, messages: list[dict], guild_name: str) -> str:
    """Build an HTML transcript for a ticket."""
    rows = ""
    for m in messages:
        rows += f"""
        <div class="message">
            <span class="author">{m['author_name']}</span>
            <span class="time">{m['created_at']}</span>
            <p>{m['content'].replace('<', '&lt;').replace('>', '&gt;')}</p>
        </div>"""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Ticket #{ticket['id']} — {guild_name}</title>
  <style>
    body {{ font-family: sans-serif; background: #36393f; color: #dcddde; margin: 0; padding: 20px; }}
    h1 {{ color: #7289da; }}
    .meta {{ background: #2f3136; padding: 12px; border-radius: 8px; margin-bottom: 20px; }}
    .message {{ border-bottom: 1px solid #40444b; padding: 10px 0; }}
    .author {{ font-weight: bold; color: #7289da; margin-right: 8px; }}
    .time {{ font-size: 0.8em; color: #72767d; }}
    p {{ margin: 4px 0 0; }}
  </style>
</head>
<body>
  <h1>🎫 Ticket #{ticket['id']} — {guild_name}</h1>
  <div class="meta">
    <b>Categoría:</b> {ticket['category']} |
    <b>Prioridad:</b> {ticket['priority']} |
    <b>Estado:</b> {ticket['status']} |
    <b>Abierto:</b> {ticket['opened_at']} |
    <b>Cerrado:</b> {ticket.get('closed_at', 'N/A')}
  </div>
  <div class="messages">{rows}</div>
</body>
</html>"""


def build_ticket_txt(ticket: dict, messages: list[dict], guild_name: str) -> str:
    """Build a plain-text transcript for a ticket."""
    lines = [
        f"=== Ticket #{ticket['id']} — {guild_name} ===",
        f"Categoría : {ticket['category']}",
        f"Prioridad : {ticket['priority']}",
        f"Estado    : {ticket['status']}",
        f"Abierto   : {ticket['opened_at']}",
        f"Cerrado   : {ticket.get('closed_at', 'N/A')}",
        "",
        "── Mensajes ──────────────────────────────────────────────",
    ]
    for m in messages:
        lines.append(f"[{m['created_at']}] {m['author_name']}: {m['content']}")
    return "\n".join(lines)
