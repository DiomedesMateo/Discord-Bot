"""
Cog: Canales de voz temporales ("Join to Create")
- /canalvoz setup <canal_ancla> [categoria]  — (owner) configura el canal ancla
- /canalvoz desactivar                       — (owner) desactiva el sistema
- /canalvoz nombre <nuevo_nombre>            — renombra tu canal
- /canalvoz limite <cantidad>                — cambia el límite de usuarios
- /canalvoz bloquear / desbloquear           — abre o cierra tu canal a nuevas entradas
- /canalvoz expulsar <usuario>               — saca a alguien de tu canal
- /canalvoz transferir <usuario>             — le pasa la propiedad del canal a otro miembro

Cómo funciona:
1. El owner configura un canal de voz "ancla" (ej. "➕ Crear canal").
2. Cuando alguien entra a ese canal ancla, el bot le crea su propio canal de
   voz nuevo (en la categoría configurada, o la misma del ancla por defecto)
   y lo mueve ahí automáticamente. Ese miembro queda como "dueño" del canal.
3. Si el miembro ya tiene un canal temporal activo, se le mueve ahí en vez
   de crearle uno nuevo.
4. Cuando un canal temporal se queda vacío, el bot espera 15 segundos de
   gracia antes de borrarlo (por si alguien salió por error o se reconecta).
   Si alguien vuelve a entrar dentro de esos 15s, el borrado se cancela y
   el canal se conserva.
5. El dueño puede administrar su canal con los subcomandos de /canalvoz
   (nombre, límite, bloquear/desbloquear, expulsar, transferir). Un
   administrador del servidor también puede usarlos sobre cualquier canal
   temporal, por si hace falta intervenir.

Requisito: el bot necesita permiso para crear/borrar canales de voz y mover
miembros en la categoría donde se generarán los canales temporales.
"""
import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from utils import checks, embeds

log = logging.getLogger(__name__)

DEFAULT_TEMPLATE = "🔊 Canal de {user}"
EMPTY_CHANNEL_DELETE_DELAY = 15  # segundos de gracia antes de borrar un canal vacío


class VoiceHubs(commands.Cog, name="VoiceHubs"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.pending_deletions: dict[int, asyncio.Task] = {}

    voicechannel_group = app_commands.Group(
        name="voicechannel", description="Canales de voz temporales (Join to Create)."
    )

    async def cog_unload(self):
        for task in self.pending_deletions.values():
            task.cancel()
        self.pending_deletions.clear()

    # ── Helper interno ───────────────────────────────────────────────────────

    async def _get_owned_temp_channel(self, interaction: discord.Interaction) -> discord.VoiceChannel | None:
        """Devuelve el canal de voz temporal del que el usuario es dueño (o
        que puede administrar por ser Administrador del servidor). Si no
        aplica, responde con el error correspondiente y devuelve None."""
        voice_state = interaction.user.voice
        if not voice_state or not voice_state.channel:
            await interaction.response.send_message(
                embed=embeds.error("Debes estar conectado a tu canal de voz temporal para usar esto."),
                ephemeral=True,
            )
            return None

        channel = voice_state.channel
        record = await db.get_temp_voice_channel(channel.id)
        if not record:
            await interaction.response.send_message(
                embed=embeds.error("El canal en el que estás no es un canal de voz temporal."),
                ephemeral=True,
            )
            return None

        is_owner_of_channel = record["owner_id"] == interaction.user.id
        is_admin = bool(interaction.user.guild_permissions.administrator)
        if not (is_owner_of_channel or is_admin):
            await interaction.response.send_message(
                embed=embeds.error(f"Solo <@{record['owner_id']}> (el dueño de este canal) puede hacer eso."),
                ephemeral=True,
            )
            return None

        return channel

    # ── Listener ──────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        guild = member.guild

        # ── Entrando a un canal → cancelar cualquier borrado pendiente ─────
        # (si alguien vuelve a entrar dentro de los 15s de gracia, el canal
        # se salva y no hace falta recrearlo).
        if after.channel:
            pending = self.pending_deletions.pop(after.channel.id, None)
            if pending:
                pending.cancel()

        # ── Entrando al canal ancla → crear/mover a un canal temporal ──────
        if after.channel and (not before.channel or before.channel.id != after.channel.id):
            config = await db.get_voice_hub_config(guild.id)
            anchor_id = config.get("anchor_channel_id")
            if anchor_id and after.channel.id == anchor_id:
                await self._handle_anchor_join(member, after.channel, config)

        # ── Saliendo de un canal temporal → programar borrado con gracia ───
        if before.channel and (not after.channel or before.channel.id != after.channel.id):
            record = await db.get_temp_voice_channel(before.channel.id)
            if record and len(before.channel.members) == 0 and before.channel.id not in self.pending_deletions:
                task = asyncio.create_task(self._delayed_delete(guild.id, before.channel.id))
                self.pending_deletions[before.channel.id] = task

    async def _delayed_delete(self, guild_id: int, channel_id: int) -> None:
        """Espera el período de gracia y borra el canal si sigue vacío. Si
        la tarea se cancela antes (porque alguien volvió a entrar), no hace
        nada — simplemente no llega a este punto."""
        try:
            await asyncio.sleep(EMPTY_CHANNEL_DELETE_DELAY)
        except asyncio.CancelledError:
            return
        finally:
            self.pending_deletions.pop(channel_id, None)

        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(channel_id) if guild else None

        if not channel:
            # Ya no existe (se borró de otra forma) — limpiar el registro igual.
            await db.delete_temp_voice_channel(channel_id)
            return

        if len(channel.members) > 0:
            # Alguien entró justo en el margen entre el cancel-check y esta
            # comprobación — no lo borramos.
            return

        try:
            await channel.delete(reason=f"Canal de voz temporal vacío por {EMPTY_CHANNEL_DELETE_DELAY}s")
        except Exception as e:
            log.warning("[VoiceHubs] No se pudo borrar el canal temporal %s: %s", channel_id, e)
        await db.delete_temp_voice_channel(channel_id)

    async def _handle_anchor_join(
        self, member: discord.Member, anchor_channel: discord.VoiceChannel, config: dict
    ) -> None:
        guild = member.guild

        # Si ya tiene un canal temporal activo, lo movemos ahí en vez de
        # crearle uno nuevo.
        existing = await db.get_temp_voice_channel_by_owner(guild.id, member.id)
        if existing:
            existing_channel = guild.get_channel(existing["channel_id"])
            if existing_channel:
                try:
                    await member.move_to(existing_channel, reason="Ya tiene un canal de voz temporal activo")
                except Exception as e:
                    log.warning("[VoiceHubs] No se pudo mover a %s a su canal existente: %s", member, e)
                return
            await db.delete_temp_voice_channel(existing["channel_id"])

        category = None
        category_id = config.get("category_id")
        if category_id:
            category = guild.get_channel(category_id)
        if category is None:
            category = anchor_channel.category

        template = config.get("name_template") or DEFAULT_TEMPLATE
        name = template.replace("{user}", member.display_name)[:100]

        try:
            new_channel = await guild.create_voice_channel(
                name=name,
                category=category,
                reason=f"Canal de voz temporal para {member}",
            )
        except discord.Forbidden:
            log.warning("[VoiceHubs] Sin permisos para crear canales de voz en %s", guild.name)
            return
        except Exception as e:
            log.warning("[VoiceHubs] Error creando canal temporal en %s: %s", guild.name, e)
            return

        await db.create_temp_voice_channel(new_channel.id, guild.id, member.id)

        try:
            await member.move_to(new_channel, reason="Canal de voz temporal creado")
        except Exception as e:
            log.warning("[VoiceHubs] No se pudo mover a %s a su canal temporal nuevo: %s", member, e)

    # ── Configuración (owner) ────────────────────────────────────────────────

    @voicechannel_group.command(
        name="setup",
        description="(Owner) Configura el canal ancla para crear canales de voz temporales.",
    )
    @app_commands.describe(
        canal_ancla="Canal de voz al que la gente entra para generar su propio canal",
        categoria="Categoría donde se crearán los canales temporales (por defecto, la del ancla)",
    )
    @checks.is_owner()
    async def canalvoz_setup(
        self,
        interaction: discord.Interaction,
        canal_ancla: discord.VoiceChannel,
        categoria: discord.CategoryChannel = None,
    ):
        await db.update_voice_hub_config(
            interaction.guild_id,
            anchor_channel_id=canal_ancla.id,
            category_id=categoria.id if categoria else None,
        )
        await interaction.response.send_message(
            embed=embeds.success(
                f"Listo. Cualquiera que entre a {canal_ancla.mention} tendrá su propio canal de voz temporal."
            ),
            ephemeral=True,
        )

    @voicechannel_group.command(
        name="desactivar",
        description="(Owner) Desactiva la creación de canales de voz temporales.",
    )
    @checks.is_owner()
    async def canalvoz_desactivar(self, interaction: discord.Interaction):
        await db.update_voice_hub_config(interaction.guild_id, anchor_channel_id=None)
        await interaction.response.send_message(
            embed=embeds.success("Sistema de canales de voz temporales desactivado."), ephemeral=True
        )

    # ── Gestión del propio canal ─────────────────────────────────────────────

    @voicechannel_group.command(name="rename", description="Cambia el nombre de tu canal de voz temporal.")
    @app_commands.describe(nuevo_nombre="Nuevo nombre para tu canal")
    async def canalvoz_nombre(self, interaction: discord.Interaction, nuevo_nombre: str):
        channel = await self._get_owned_temp_channel(interaction)
        if not channel:
            return
        nuevo_nombre = nuevo_nombre[:100]
        try:
            await channel.edit(name=nuevo_nombre, reason=f"Renombrado por {interaction.user}")
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=embeds.error("No tengo permisos para renombrar ese canal."), ephemeral=True
            )
        await interaction.response.send_message(
            embed=embeds.success(f"Canal renombrado a **{nuevo_nombre}**."), ephemeral=True
        )

    @voicechannel_group.command(name="limit", description="Cambia el límite de usuarios de tu canal de voz temporal.")
    @app_commands.describe(cantidad="Número máximo de usuarios (0 = sin límite)")
    async def canalvoz_limite(self, interaction: discord.Interaction, cantidad: app_commands.Range[int, 0, 99]):
        channel = await self._get_owned_temp_channel(interaction)
        if not channel:
            return
        try:
            await channel.edit(user_limit=cantidad, reason=f"Límite cambiado por {interaction.user}")
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=embeds.error("No tengo permisos para editar ese canal."), ephemeral=True
            )
        texto = "sin límite" if cantidad == 0 else f"**{cantidad}** usuarios"
        await interaction.response.send_message(embed=embeds.success(f"Límite actualizado a {texto}."), ephemeral=True)

    @voicechannel_group.command(name="block", description="Bloquea tu canal para que nadie nuevo pueda entrar.")
    async def canalvoz_bloquear(self, interaction: discord.Interaction):
        channel = await self._get_owned_temp_channel(interaction)
        if not channel:
            return
        try:
            await channel.set_permissions(
                interaction.guild.default_role, connect=False, reason=f"Bloqueado por {interaction.user}"
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=embeds.error("No tengo permisos para editar ese canal."), ephemeral=True
            )
        await interaction.response.send_message(
            embed=embeds.success("🔒 Canal bloqueado. Nadie nuevo podrá entrar (los que ya estaban dentro no se ven afectados)."),
            ephemeral=True,
        )

    @voicechannel_group.command(name="unblock", description="Vuelve a abrir tu canal de voz temporal.")
    async def canalvoz_desbloquear(self, interaction: discord.Interaction):
        channel = await self._get_owned_temp_channel(interaction)
        if not channel:
            return
        try:
            await channel.set_permissions(
                interaction.guild.default_role, overwrite=None, reason=f"Desbloqueado por {interaction.user}"
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=embeds.error("No tengo permisos para editar ese canal."), ephemeral=True
            )
        await interaction.response.send_message(embed=embeds.success("🔓 Canal desbloqueado."), ephemeral=True)

    @voicechannel_group.command(name="kick", description="Expulsa a alguien de tu canal de voz temporal.")
    @app_commands.describe(usuario="Usuario a expulsar de tu canal")
    async def canalvoz_expulsar(self, interaction: discord.Interaction, usuario: discord.Member):
        channel = await self._get_owned_temp_channel(interaction)
        if not channel:
            return
        if not usuario.voice or not usuario.voice.channel or usuario.voice.channel.id != channel.id:
            return await interaction.response.send_message(
                embed=embeds.error(f"{usuario.mention} no está en tu canal."), ephemeral=True
            )
        try:
            await usuario.move_to(None, reason=f"Expulsado por {interaction.user}")
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=embeds.error("No tengo permisos para expulsar a ese usuario."), ephemeral=True
            )
        await interaction.response.send_message(
            embed=embeds.success(f"{usuario.mention} fue expulsado de tu canal."), ephemeral=True
        )

    @voicechannel_group.command(
        name="transfer",
        description="Transfiere la propiedad de tu canal de voz temporal a otro miembro.",
    )
    @app_commands.describe(usuario="Nuevo dueño del canal (debe estar dentro de él)")
    async def canalvoz_transferir(self, interaction: discord.Interaction, usuario: discord.Member):
        channel = await self._get_owned_temp_channel(interaction)
        if not channel:
            return
        if usuario.bot:
            return await interaction.response.send_message(
                embed=embeds.error("No puedes transferirle el canal a un bot."), ephemeral=True
            )
        if not usuario.voice or not usuario.voice.channel or usuario.voice.channel.id != channel.id:
            return await interaction.response.send_message(
                embed=embeds.error(f"{usuario.mention} debe estar dentro de tu canal para recibirlo."), ephemeral=True
            )
        await db.transfer_temp_voice_channel(channel.id, usuario.id)
        await interaction.response.send_message(
            embed=embeds.success(f"Le transferiste el canal a {usuario.mention}."), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceHubs(bot))
