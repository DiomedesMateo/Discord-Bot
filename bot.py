"""
Main entry point for the Discord bot.
Loads all cogs, initializes the database, and starts the bot.
"""

import os
import sys
import asyncio
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from mcstatus import JavaServer
from database import db
from database.db import init_db
from config import MC_SERVER_HOST, MC_SERVER_PORT

load_dotenv()

# ── Intents ──────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.reactions = True
intents.presences = False


# ── Bot class ─────────────────────────────────────────────────────────────────
class DiscordBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )
        self.tree.on_error = self.on_app_command_error

    async def setup_hook(self):
        """Called before the bot connects. Load cogs and sync commands."""
        print("[Bot] Initialising database…")
        await init_db()

        try:
            app_info = await self.application_info()
            self.owner_id = app_info.owner.id
            print(f"[Bot] Owner detectado: {app_info.owner} ({self.owner_id})")
        except Exception as e:
            print(
                f"[Bot] ⚠️ No se pudo obtener el owner de la aplicación: {e}",
                file=sys.stderr,
            )

        cogs = [
            "cogs.giveaways",
            "cogs.tickets",
            "cogs.moderation",
            "cogs.automod",
            "cogs.tournaments",
            "cogs.statistics",
            "cogs.suggestions",
            "cogs.verification",
            "cogs.welcome",
            "cogs.roles",
            "cogs.birthdays",
            "cogs.utility",
            "cogs.owner",
            "cogs.staff_tools",
            "cogs.confessions",
            "cogs.invites",
            "cogs.voicehubs",
            "cogs.ranks",
            "cogs.scam_guard",
            "cogs.staff_weekly",
        ]
        for cog in cogs:
            try:
                await self.load_extension(cog)
                print(f"[Bot] ✅ Cog loaded: {cog}")
            except Exception as e:
                print(f"[Bot] ❌ Failed to load {cog}: {e}", file=sys.stderr)

        # Register persistent views so dropdowns/buttons survive bot restarts
        from cogs.tickets import TicketPanelView

        self.add_view(TicketPanelView())

        from cogs.moderation import LockdownUnlockView

        self.add_view(LockdownUnlockView())

        # Register the /sync admin command
        self.tree.add_command(sync_commands)

        print("[Bot] Borrando comandos globales antiguos (evita duplicados)…")
        try:
            await self.http.bulk_upsert_global_commands(self.application_id, payload=[])
            print(
                "[Bot] ✅ Comandos globales antiguos eliminados. Ahora solo se sincroniza por servidor."
            )
        except Exception as e:
            print(f"[Bot] ⚠️ No se pudo limpiar comandos globales: {e}", file=sys.stderr)

    async def on_ready(self):
        for guild in self.guilds:
            try:
                self.tree.clear_commands(guild=guild)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                print(f"[Bot] ✅ Comandos sincronizados en: {guild.name} ({guild.id})")
            except Exception as e:
                print(f"[Bot] ⚠️ No se pudo sincronizar en {guild.name}: {e}")

        print(f"\n{'=' * 50}")
        print(f"  Bot:    {self.user} ({self.user.id})")
        print(f"  Guilds: {len(self.guilds)}")
        print(f"  Latency: {self.latency * 1000:.1f}ms")
        print(f"{'=' * 50}\n")

        if not self.presence_loop.is_running():
            self.presence_loop.start()
        else:
            self.presence_loop.restart()

    async def on_guild_join(self, guild: discord.Guild):
        """Auto-create guild config on join."""
        from database.db import get_guild_config

        await get_guild_config(guild.id)
        print(f"[Bot] Joined guild: {guild.name} ({guild.id})")
        if self.presence_loop.is_running():
            self.presence_loop.restart()

    async def on_guild_remove(self, guild: discord.Guild):
        print(f"[Bot] Left guild: {guild.name} ({guild.id})")
        if self.presence_loop.is_running():
            self.presence_loop.restart()

    @tasks.loop(minutes=2)
    async def presence_loop(self):
        """Alterna el 'Watching' del bot entre dos mensajes en cada ciclo:
        1) Jugadores conectados en el servidor de Minecraft.
        2) Cantidad de tickets abiertos (sumados de todos los servidores
           de Discord donde está el bot).
        Se refresca cada 2 minutos (alternando), y además al instante
        cuando el bot entra/sale de un servidor de Discord."""
        self._presence_index = getattr(self, "_presence_index", 0)

        if self._presence_index == 0:
            try:
                server = await JavaServer.async_lookup(
                    f"{MC_SERVER_HOST}:{MC_SERVER_PORT}"
                )
                status = await asyncio.wait_for(server.async_status(), timeout=10)
                text = f"{status.players.online}/{status.players.max} jugadores"
            except Exception as e:
                print(
                    f"[Bot] ⚠️ No se pudo consultar el servidor de Minecraft para la presencia: {e}"
                )
                text = "servidor offline"
        else:
            try:
                total_open = 0
                for guild in self.guilds:
                    tickets = await db.get_open_tickets(guild.id)
                    total_open += len(tickets)
                text = f"{total_open} tickets"
            except Exception as e:
                print(
                    f"[Bot] ⚠️ No se pudo contar los tickets abiertos para la presencia: {e}"
                )
                text = f"{len(self.guilds)} servidore"

        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=text,
            )
        )

        self._presence_index = 1 - self._presence_index

    @presence_loop.before_loop
    async def before_presence_loop(self):
        await self.wait_until_ready()

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ):
        """Global slash command error handler."""
        msg = None
        if isinstance(error, discord.app_commands.CheckFailure):
            msg = f"❌ {error}"
        elif isinstance(error, discord.app_commands.CommandOnCooldown):
            msg = f"⏳ Espera {error.retry_after:.1f}s antes de usar este comando de nuevo."
        elif isinstance(error, discord.app_commands.MissingPermissions):
            msg = "❌ No tienes los permisos necesarios para usar este comando."
        elif isinstance(error, discord.app_commands.BotMissingPermissions):
            msg = f"❌ Me faltan permisos: {', '.join(error.missing_permissions)}"
        else:
            msg = f"❌ Error inesperado: {error}"
            print(f"[Error] Command: {interaction.command} | {error}", file=sys.stderr)

        embed = discord.Embed(description=msg, color=0xED4245)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            pass

    async def on_error(self, event_method: str, *args, **kwargs):
        import traceback

        print(f"[Error] Event: {event_method}")
        traceback.print_exc()


# ── Admin sync command (registered in setup_hook) ─────────────────────────────
@discord.app_commands.command(
    name="sync",
    description="[Admin] Sincroniza los slash commands al instante en este servidor.",
)
@discord.app_commands.default_permissions(administrator=True)
async def sync_commands(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    bot: DiscordBot = interaction.client  # type: ignore
    bot.tree.clear_commands(guild=interaction.guild)
    bot.tree.copy_global_to(guild=interaction.guild)
    synced = await bot.tree.sync(guild=interaction.guild)
    await interaction.followup.send(
        f"✅ **{len(synced)} comandos sincronizados** en **{interaction.guild.name}** al instante.",
        ephemeral=True,
    )


# ── Entry point ───────────────────────────────────────────────────────────────
async def main():
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print(
            "❌ DISCORD_BOT_TOKEN not set. Please add it to your environment secrets.",
            file=sys.stderr,
        )
        sys.exit(1)

    bot = DiscordBot()
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
