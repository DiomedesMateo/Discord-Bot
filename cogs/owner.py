"""
Cog: Owner — Comandos de administración del propio proceso del bot
(distinto de comandos de moderación del servidor de Discord).

/reiniciar-bot — reinicia el proceso del bot por completo.

"""
import os
import sys
import discord
from discord import app_commands
from discord.ext import commands
from config import COLOR_WARNING
from utils import checks, helpers


class OwnerCog(commands.Cog, name="Owner"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="reiniciar-bot",
        description="⚠️ Reinicia el bot por completo (reconecta a Discord desde cero)",
    )
    @checks.is_owner()
    async def restart_bot(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🔄 Reiniciando el bot…",
            description=(
                f"Solicitado por {interaction.user.mention}.\n"
                "El bot se desconectará y volverá a estar en línea en unos "
                "segundos. Ningún dato de la base de datos se ve afectado."
            ),
            color=COLOR_WARNING,
        )
        # Se responde ANTES de reiniciar — una vez que el proceso se
        # reemplaza no hay forma de seguir usando esta interacción.
        await interaction.response.send_message(embed=embed)

        guild_name = interaction.guild.name if interaction.guild else "???"
        print(
            f"[Bot] Reinicio solicitado por {interaction.user} "
            f"({interaction.user.id}) en {guild_name}"
        )

        await helpers.notify_owner(
            self.bot,
            title="🔄 El bot fue reiniciado",
            description=(
                f"**Quién:** {interaction.user} (`{interaction.user.id}`)\n"
                f"**Servidor:** {guild_name}"
            ),
            color=COLOR_WARNING,
        )

        # Cierra la conexión con Discord de forma limpia antes de relanzar.
        await self.bot.close()

        # Reemplaza el proceso actual por uno nuevo (mismo intérprete,
        # mismos argumentos con los que se lanzó bot.py).
        os.execv(sys.executable, [sys.executable] + sys.argv)


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerCog(bot))
