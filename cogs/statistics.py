"""
Cog: Statistics
- /stats server — server overview

"""

import discord
from discord import app_commands
from discord.ext import commands
from database import db
from config import COLOR_PRIMARY


class StatisticsCog(commands.Cog, name="Statistics"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    stats_group = app_commands.Group(
        name="stats", description="Estadísticas del servidor"
    )

    # ── Server ────────────────────────────────────────────────────────────────

    @stats_group.command(name="server", description="Estadísticas generales del servidor")
    async def stats_server(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild = interaction.guild
        bots = sum(1 for m in guild.members if m.bot)
        humans = guild.member_count - bots
        text_ch = len(guild.text_channels)
        voice_ch = len(guild.voice_channels)
        online = sum(1 for m in guild.members
                     if m.status != discord.Status.offline and not m.bot)

        ticket_stats = await db.get_ticket_stats(guild.id)
        mod_stats = await db.get_mod_stats(guild.id)

        embed = discord.Embed(
            title=f"📊 Estadísticas — {guild.name}",
            color=COLOR_PRIMARY,
        )
        # FIX: guard explícito en vez de pasar url=None directo a set_thumbnail.
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        embed.add_field(name="👥 Miembros", value=str(guild.member_count), inline=True)
        embed.add_field(name="🤖 Bots", value=str(bots), inline=True)
        embed.add_field(name="🟢 En línea", value=str(online), inline=True)

        embed.add_field(name="💬 Canales texto", value=str(text_ch), inline=True)
        embed.add_field(name="🔊 Canales voz", value=str(voice_ch), inline=True)
        embed.add_field(name="🏷️ Roles", value=str(len(guild.roles)), inline=True)

        embed.add_field(name="🎫 Tickets totales", value=str(ticket_stats["total"]), inline=True)
        embed.add_field(name="🎫 Tickets abiertos", value=str(ticket_stats["open"]), inline=True)
        # FIX: avg_rating puede ser None si no hay tickets calificados aún.
        avg_rating = ticket_stats["avg_rating"] or 0
        embed.add_field(name="⭐ Rating promedio",
                        value=f"{avg_rating:.1f}/5", inline=True)

        total_sanctions = sum(mod_stats.values())
        embed.add_field(name="🛡️ Sanciones totales", value=str(total_sanctions), inline=True)
        embed.add_field(name="🔨 Bans", value=str(mod_stats.get("ban", 0)), inline=True)
        embed.add_field(name="👢 Kicks", value=str(mod_stats.get("kick", 0)), inline=True)

        embed.add_field(
            name="📅 Servidor creado",
            value=f"<t:{int(guild.created_at.timestamp())}:F>", inline=False)

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(StatisticsCog(bot))
