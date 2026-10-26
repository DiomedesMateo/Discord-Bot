"""
Cog: Niveles
- Cada mensaje otorga entre 15 y 25 XP al azar.
- Para evitar el spam, solo se puede ganar XP una vez por minuto por
  usuario (por servidor). Los mensajes enviados durante el cooldown NO dan
  XP, pero SÍ se siguen contando para los logros basados en mensajes
  totales y para la racha de días activos.
- /nivel   — muestra el nivel, XP y posición de un usuario.
- /ranking — top 10 de XP del servidor.
"""
import random
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from utils import helpers
from config import COLOR_GOLD, COLOR_PRIMARY

XP_MIN = 15
XP_MAX = 25
XP_COOLDOWN_SECONDS = 60


def total_xp_for_level(level: int) -> int:
    """XP acumulado necesario para ALCANZAR `level` (nivel 0 = 0 XP)."""
    total = 0
    for n in range(1, level + 1):
        total += 5 * n * n + 50 * n + 100
    return total


def level_from_xp(xp: int) -> int:
    level = 0
    while total_xp_for_level(level + 1) <= xp:
        level += 1
    return level


def _progress_bar(current: int, needed: int, length: int = 14) -> str:
    if needed <= 0:
        filled = length
    else:
        filled = max(0, min(length, round((current / needed) * length)))
    return "▰" * filled + "▱" * (length - filled)


class LevelsCog(commands.Cog, name="Niveles"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Ganancia de XP por mensaje ───────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        guild_id = message.guild.id
        user_id = message.author.id

        xp_amount = random.randint(XP_MIN, XP_MAX)
        result = await db.try_add_message_xp(
            guild_id, user_id, xp_amount, cooldown_seconds=XP_COOLDOWN_SECONDS
        )

        if result is None:
            # Seguía en cooldown de XP: no se le da XP, pero el mensaje
            # igual cuenta para los logros de mensajes totales.
            messages_count = await db.increment_message_count_only(guild_id, user_id)
        else:
            messages_count = result["messages_count"]
            new_level = level_from_xp(result["xp"])
            if new_level > result["level"]:
                await db.set_level(guild_id, user_id, new_level)
                await self._announce_level_up(message, new_level)

        # Racha de días activos (basta con 1 mensaje al día).
        streak_days = await db.update_streak(guild_id, user_id)

        # Antigüedad en el servidor (logro "Veterano").
        joined_at = message.author.joined_at
        days_in_server = 0
        if joined_at:
            days_in_server = (datetime.now(timezone.utc) - joined_at).days

        achievements_cog = self.bot.get_cog("Logros")
        if achievements_cog:
            await achievements_cog.check_progress(message.author, "messages", messages_count, message.channel)
            await achievements_cog.check_progress(message.author, "streak", streak_days, message.channel)
            if days_in_server:
                await achievements_cog.check_progress(message.author, "veteran", days_in_server, message.channel)

    async def _announce_level_up(self, message: discord.Message, new_level: int):
        embed = discord.Embed(
            title="🆙┃¡NIVEL SUPERADO!",
            description=(
                f"¡Felicidades, {message.author.mention}! 🎉\n"
                f"Has alcanzado el **Nivel {new_level}** y continúas avanzando en tu aventura "
                f"dentro de **MineOrbit Network**. 🚀\n\n"
                f"✨ Sigue participando en la comunidad, hablando en los canales y manteniéndote "
                f"activo para desbloquear nuevas recompensas y alcanzar niveles aún más altos.\n\n"
                f"🌌 ¡El universo de MineOrbit apenas comienza!"
            ),
            color=COLOR_GOLD,
        )
        embed.set_thumbnail(url=message.author.display_avatar.url)

        # Si el servidor configuró un canal con /logros canal, se anuncia
        # ahí en vez de en el canal donde se envió el mensaje.
        target = await helpers.get_announce_channel(message.guild) or message.channel
        try:
            await target.send(embed=embed)
        except (discord.HTTPException, discord.Forbidden):
            pass

    # ── Comandos ──────────────────────────────────────────────────────────

    @app_commands.command(name="nivel", description="Muestra tu nivel de XP o el de otro miembro")
    @app_commands.describe(usuario="Miembro a consultar (por defecto, tú mismo)")
    async def nivel(self, interaction: discord.Interaction, usuario: discord.Member = None):
        member = usuario or interaction.user
        row = await db.get_level_row(interaction.guild_id, member.id)
        rank = await db.get_level_rank(interaction.guild_id, member.id)

        xp = row.get("xp", 0) or 0
        level = row.get("level", 0) or 0
        messages_count = row.get("messages_count", 0) or 0

        current_level_xp = total_xp_for_level(level)
        next_level_xp = total_xp_for_level(level + 1)
        into_level = xp - current_level_xp
        needed = next_level_xp - current_level_xp

        embed = discord.Embed(
            title=f"📈 Nivel de {member.display_name}",
            color=COLOR_PRIMARY,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Nivel", value=str(level), inline=True)
        embed.add_field(name="XP total", value=f"{xp:,}", inline=True)
        embed.add_field(name="Posición", value=f"#{rank}", inline=True)
        embed.add_field(
            name=f"Progreso al nivel {level + 1}",
            value=f"{_progress_bar(into_level, needed)}  `{into_level:,}/{needed:,}` XP",
            inline=False,
        )
        embed.add_field(name="Mensajes enviados", value=f"{messages_count:,}", inline=True)
        embed.set_footer(text=f"{XP_MIN}-{XP_MAX} XP por mensaje • 1 vez por minuto")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="ranking", description="Muestra el top 10 de niveles del servidor")
    async def ranking(self, interaction: discord.Interaction):
        top = await db.get_level_leaderboard(interaction.guild_id, limit=10)
        if not top:
            await interaction.response.send_message("Todavía nadie tiene XP en este servidor.", ephemeral=True)
            return

        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, row in enumerate(top):
            medal = medals[i] if i < 3 else f"`#{i + 1}`"
            member = interaction.guild.get_member(row["user_id"])
            name = member.mention if member else f"<@{row['user_id']}>"
            lines.append(f"{medal} {name} — Nivel **{row['level']}** ({row['xp']:,} XP)")

        embed = discord.Embed(
            title="🏆 Ranking de niveles",
            description="\n".join(lines),
            color=COLOR_GOLD,
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(LevelsCog(bot))
