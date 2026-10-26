"""
Reusable embed builders to keep cogs clean.
"""
import discord
from datetime import datetime
from config import (
    COLOR_PRIMARY, COLOR_SUCCESS, COLOR_WARNING, COLOR_ERROR,
    COLOR_INFO, COLOR_GOLD, COLOR_TICKET, COLOR_MOD, COLOR_STAFF,
    EMOJI_SUCCESS, EMOJI_ERROR, EMOJI_WARNING, EMOJI_INFO,
    PRIORITY_LABELS,
)


def _base(color: int, title: str = None, description: str = None) -> discord.Embed:
    e = discord.Embed(color=color, timestamp=datetime.utcnow())
    if title:
        e.title = title
    if description:
        e.description = description
    return e


def success(description: str, title: str = None) -> discord.Embed:
    return _base(COLOR_SUCCESS, title or f"{EMOJI_SUCCESS} Éxito", description)


def error(description: str, title: str = None) -> discord.Embed:
    return _base(COLOR_ERROR, title or f"{EMOJI_ERROR} Error", description)


def warning(description: str, title: str = None) -> discord.Embed:
    return _base(COLOR_WARNING, title or f"{EMOJI_WARNING} Advertencia", description)


def info(description: str, title: str = None) -> discord.Embed:
    return _base(COLOR_INFO, title or f"{EMOJI_INFO} Información", description)


def mod_action(action: str, user: discord.User, moderator: discord.User,
               reason: str, case_number: int, duration: str = None, color: int = COLOR_MOD) -> discord.Embed:
    e = _base(color, f"🛡️ {action} — Caso #{case_number}")
    e.add_field(name="Usuario", value=f"{user.mention} (`{user}`)", inline=True)
    e.add_field(name="Moderador", value=f"{moderator.mention} (`{moderator}`)", inline=True)
    e.add_field(name="Razón", value=reason or "Sin razón especificada", inline=False)
    if duration:
        e.add_field(name="Duración", value=duration, inline=True)
    e.set_thumbnail(url=user.display_avatar.url)
    return e


def ticket_opened(ticket_id: int, user: discord.User, category: str,
                  priority: str, subject: str = None) -> discord.Embed:
    p_label, p_color = PRIORITY_LABELS.get(priority, ("🟢 Baja", COLOR_SUCCESS))
    e = _base(COLOR_TICKET, f"🎫 Ticket #{ticket_id}")
    e.description = f"Bienvenido/a {user.mention}.\nEl staff te atenderá lo antes posible."
    e.add_field(name="Categoría", value=category.capitalize(), inline=True)
    e.add_field(name="Prioridad", value=p_label, inline=True)
    if subject:
        e.add_field(name="Asunto", value=subject, inline=False)
    e.set_footer(text=f"Abierto por {user} • ID: {ticket_id}")
    return e


def ticket_closed(ticket_id: int, closed_by: discord.User,
                  open_time: str, rating: int = None) -> discord.Embed:
    e = _base(COLOR_TICKET, f"🔒 Ticket #{ticket_id} cerrado")
    e.add_field(name="Cerrado por", value=closed_by.mention, inline=True)
    if rating:
        stars = "⭐" * rating
        e.add_field(name="Valoración", value=stars, inline=True)
    e.set_footer(text=f"Abierto: {open_time}")
    return e


def giveaway(prize: str, host: discord.User, winners: int,
             end_time: str, entries: int = 0, ended: bool = False,
             winner_mentions: list[str] = None) -> discord.Embed:
    if ended and winner_mentions:
        e = _base(COLOR_GOLD, "🎉 ¡Sorteo finalizado!")
        e.description = f"**Premio:** {prize}\n**Ganador(es):** {', '.join(winner_mentions)}"
    elif ended:
        e = _base(COLOR_GOLD, "🎉 Sorteo finalizado")
        e.description = f"**Premio:** {prize}\nNo hubo participantes suficientes."
    else:
        e = _base(COLOR_GOLD, f"🎉 ¡SORTEO! — {prize}")
        e.description = (
            f"Reacciona con 🎉 para participar.\n\n"
            f"**Premio:** {prize}\n"
            f"**Ganadores:** {winners}\n"
            f"**Finaliza:** <t:{end_time}:R>"
        )
    e.add_field(name="Anfitrión", value=host.mention, inline=True)
    e.add_field(name="Participantes", value=str(entries), inline=True)
    e.set_footer(text=f"ID del sorteo • Termina: {end_time}")
    return e


def stats_embed(title: str, stats: dict) -> discord.Embed:
    e = _base(COLOR_PRIMARY, f"📊 {title}")
    for k, v in stats.items():
        e.add_field(name=k, value=str(v), inline=True)
    return e


def staff_action_log(action: str, target: discord.User,
                     staff: discord.User, details: str = None) -> discord.Embed:
    e = _base(COLOR_STAFF, f"👥 Staff — {action}")
    e.add_field(name="Miembro de staff", value=staff.mention, inline=True)
    if target:
        e.add_field(name="Objetivo", value=target.mention, inline=True)
    if details:
        e.add_field(name="Detalles", value=details, inline=False)
    return e


def user_profile(member: discord.Member, cases: list, warns: list) -> discord.Embed:
    e = _base(COLOR_PRIMARY, f"👤 Perfil de {member}")
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="ID", value=str(member.id), inline=True)
    e.add_field(name="Se unió", value=f"<t:{int(member.joined_at.timestamp())}:R>", inline=True)
    e.add_field(name="Cuenta creada", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
    e.add_field(name="Sanciones", value=str(len(cases)), inline=True)
    e.add_field(name="Advertencias activas", value=str(len(warns)), inline=True)
    return e
