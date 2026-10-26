"""
Cog: Rangos
- /rangos set     — asigna un rol de Discord a un rango del bot
- /rangos quitar  — quita la asignación de un rango
- /rangos ver     — muestra la configuración actual de todos los rangos

Esto reemplaza los IDs de rol hardcodeados por una configuración real,
guardada en la tabla `rank_roles` (ver database/db.py y utils/checks.py).
"""

import discord
from discord import app_commands
from discord.ext import commands
from database import db
from utils import checks

RANGO_CHOICES = [
    app_commands.Choice(name=label, value=key)
    for key, label in checks.RANK_LABELS.items()
]


class RanksCog(commands.Cog, name="Rangos"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    rangos_group = app_commands.Group(
        name="rangos", description="Configura qué rol de Discord corresponde a cada rango del bot"
    )

    @rangos_group.command(name="set", description="Asigna un rol de Discord a un rango")
    @app_commands.describe(
        rango="Rango del bot a configurar",
        rol="Rol de Discord que va a tener ese rango",
    )
    @app_commands.choices(rango=RANGO_CHOICES)
    @checks.is_owner()
    async def rangos_set(
        self,
        interaction: discord.Interaction,
        rango: app_commands.Choice[str],
        rol: discord.Role,
    ):
        await db.set_rank_role(interaction.guild_id, rango.value, rol.id)
        await interaction.response.send_message(
            f"✅ El rango **{rango.name}** ahora corresponde al rol {rol.mention}.\n"
            f"-# Cualquiera con este rol (o un rango superior configurado) ya puede usar "
            f"los comandos que requieren **{rango.name}**.",
            ephemeral=True,
        )

    @rangos_group.command(name="quitar", description="Quita la asignación de rol de un rango")
    @app_commands.describe(rango="Rango del bot a desconfigurar")
    @app_commands.choices(rango=RANGO_CHOICES)
    @checks.is_owner()
    async def rangos_quitar(
        self, interaction: discord.Interaction, rango: app_commands.Choice[str]
    ):
        await db.unset_rank_role(interaction.guild_id, rango.value)
        await interaction.response.send_message(
            f"✅ Se quitó la configuración del rango **{rango.name}**. "
            f"Nadie con solo ese rol podrá usar los comandos que lo requieran "
            f"(salvo que tengan permiso de Administrador de Discord).",
            ephemeral=True,
        )

    @rangos_group.command(name="ver", description="Ver qué rol está asignado a cada rango")
    async def rangos_ver(self, interaction: discord.Interaction):
        configured = await db.get_rank_roles(interaction.guild_id)
        lines = []
        for key in checks.RANK_ORDER:
            label = checks.RANK_LABELS[key]
            role_id = configured.get(key)
            if role_id:
                role = interaction.guild.get_role(role_id)
                value = role.mention if role else f"`{role_id}` (rol eliminado)"
            else:
                value = "_sin configurar_"
            lines.append(f"**{label}:** {value}")

        embed = discord.Embed(
            title="📋 Configuración de rangos",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text="Un rango superior siempre puede hacer lo que puede hacer uno inferior. "
                 "Quien tenga permiso de Administrador de Discord siempre puede usar todo."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RanksCog(bot))
