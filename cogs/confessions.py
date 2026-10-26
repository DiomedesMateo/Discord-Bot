"""
Cog: Confesiones anónimas
- /confesar <mensaje>       — publica un mensaje anónimo en el canal configurado
- /confesion setup          — (owner) configura el canal de confesiones
- /confesion desactivar     — (owner) desactiva el sistema sin borrar la config
- /confesion revelar        — (owner) revela quién envió una confesión

Anonimato: el autor nunca se muestra en el mensaje público ni en ningún otro
lugar visible para miembros normales. El user_id se guarda en la base de
datos únicamente para que el owner pueda identificar al autor si una
confesión viola las reglas del servidor (acoso, doxxing, contenido ilegal,
etc.) mediante /confesion revelar — no se expone por ningún otro medio.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from utils import checks, embeds
from config import COLOR_PRIMARY

log = logging.getLogger(__name__)

MAX_LENGTH = 1000
COOLDOWN_SECONDS = 60


class Confessions(commands.Cog, name="Confessions"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    confesion_group = app_commands.Group(
        name="confesion", description="Configuración del sistema de confesiones anónimas."
    )

    # ── Comando público ──────────────────────────────────────────────────────

    @app_commands.command(
        name="confesar",
        description="Envía una confesión anónima al canal configurado del servidor.",
    )
    @app_commands.describe(mensaje="Tu confesión (se publicará sin tu nombre)")
    @app_commands.checks.cooldown(1, COOLDOWN_SECONDS, key=lambda i: (i.guild_id, i.user.id))
    async def confesar(self, interaction: discord.Interaction, mensaje: str):
        if len(mensaje) > MAX_LENGTH:
            return await interaction.response.send_message(
                embed=embeds.error(f"Tu confesión es muy larga (máximo {MAX_LENGTH} caracteres)."),
                ephemeral=True,
            )

        config = await db.get_confession_config(interaction.guild_id)
        channel_id = config.get("channel_id")
        if not config.get("enabled", 1) or not channel_id:
            return await interaction.response.send_message(
                embed=embeds.error(
                    "El sistema de confesiones no está configurado en este servidor. "
                    "Un owner puede activarlo con `/confesion setup`."
                ),
                ephemeral=True,
            )

        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            return await interaction.response.send_message(
                embed=embeds.error("El canal de confesiones configurado ya no existe. Avísale al staff."),
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        number = await db.create_confession(interaction.guild_id, interaction.user.id, mensaje)

        embed = discord.Embed(
            title=f"🤫 Confesión #{number}",
            description=mensaje,
            color=COLOR_PRIMARY,
        )
        embed.set_footer(text="Anónimo • Usa /confesar para enviar la tuya")

        try:
            sent = await channel.send(embed=embed)
        except discord.Forbidden:
            return await interaction.followup.send(
                embed=embeds.error("No tengo permisos para publicar en el canal de confesiones."),
                ephemeral=True,
            )

        await db.set_confession_message(interaction.guild_id, number, sent.id)
        await interaction.followup.send(
            embed=embeds.success(f"Tu confesión fue publicada de forma anónima como **#{number}**."),
            ephemeral=True,
        )

    # ── Configuración (owner) ────────────────────────────────────────────────

    @confesion_group.command(name="setup", description="(Owner) Configura el canal de confesiones anónimas.")
    @app_commands.describe(canal="Canal donde se publicarán las confesiones")
    @checks.is_owner()
    async def confesion_setup(self, interaction: discord.Interaction, canal: discord.TextChannel):
        await db.update_confession_config(interaction.guild_id, channel_id=canal.id, enabled=1)
        await interaction.response.send_message(
            embed=embeds.success(f"Canal de confesiones configurado en {canal.mention}."), ephemeral=True
        )

    @confesion_group.command(
        name="desactivar",
        description="(Owner) Desactiva el sistema de confesiones sin borrar la configuración.",
    )
    @checks.is_owner()
    async def confesion_desactivar(self, interaction: discord.Interaction):
        await db.update_confession_config(interaction.guild_id, enabled=0)
        await interaction.response.send_message(
            embed=embeds.success("Sistema de confesiones desactivado."), ephemeral=True
        )

    @confesion_group.command(
        name="revelar",
        description="(Owner) Revela el autor de una confesión (uso moderado, para casos de abuso).",
    )
    @app_commands.describe(numero="Número de la confesión (ej. para #12 escribe 12)")
    @checks.is_owner()
    async def confesion_revelar(self, interaction: discord.Interaction, numero: int):
        confession = await db.get_confession_by_number(interaction.guild_id, numero)
        if not confession:
            return await interaction.response.send_message(
                embed=embeds.error(f"No existe ninguna confesión con el número #{numero}."), ephemeral=True
            )
        embed = discord.Embed(
            title=f"🔍 Confesión #{numero} — Revelada",
            description=confession["content"],
            color=COLOR_PRIMARY,
        )
        embed.add_field(name="Autor", value=f"<@{confession['user_id']}> (`{confession['user_id']}`)", inline=False)
        embed.add_field(name="Fecha", value=confession["created_at"], inline=False)
        embed.set_footer(text="Información sensible — úsala solo para casos de abuso o incumplimiento de reglas.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Confessions(bot))
