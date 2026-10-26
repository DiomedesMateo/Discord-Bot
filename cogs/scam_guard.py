"""
Cog: Anti-Scam Guard (canal trampa / canal de reportes de scams)

Comandos (requieren rango Admin o permiso nativo de Administrador):
- /antiscam canal <canal> [permitir_imagenes]  — configura el canal y publica el aviso
- /antiscam estado                             — configuración, permisos y contador
- /antiscam aviso                              — vuelve a publicar el aviso
- /antiscam activar / desactivar               — enciende o apaga el sistema

Funcionamiento: cuando un usuario (que no sea staff ni bot) envía un mensaje
en el canal configurado:
  1. Se borra el mensaje que disparó la detección.
  2. Se banea al usuario con delete_message_seconds=7 días, que es el mecanismo
     de Discord para borrar los mensajes del usuario en TODOS los canales del
     servidor (máximo permitido por la API: 7 días).
  3. Si el ban falla (jerarquía/permisos) se hace un barrido manual de sus
     mensajes recientes como plan B y se avisa en el log.
  4. Se incrementa el contador de baneados, se actualiza el aviso del canal,
     se crea un caso de moderación y se registra en el canal de logs de mod.
"""
import logging
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from config import COLOR_ERROR, COLOR_MOD
from database import db
from utils import checks, embeds

log = logging.getLogger(__name__)

PURGE_SECONDS = 7 * 24 * 60 * 60  # límite de la API de Discord para delete_message_seconds
RECENT_TTL = 120  # segundos que se recuerda a un usuario ya sancionado (anti doble conteo)
BAN_REASON = "Anti-Scam Guard: envió contenido en el canal de reportes de scams"
FOOTER_BRAND = "🌀MineOrbit Network — Keep our community safe."


# ── Aviso del canal ──────────────────────────────────────────────────────────


def build_notice_embeds(banned_count: int) -> list[discord.Embed]:
    """Aviso bilingüe (ES/EN) en dos embeds con el mismo color para que se
    vean como un solo bloque. El contador va aparte, como botón (ver
    build_counter_view)."""
    es = discord.Embed(
        title="🚨┃ADVERTENCIA — CANAL EXCLUSIVO",
        description=(
            "## 🇪🇸 ESPAÑOL\n"
            "🔒 Este canal es **EXCLUSIVAMENTE** para la recepción de imágenes de scams y "
            "**no está permitido enviar ningún tipo de mensaje**.\n\n"
            "⛔ Está **terminantemente prohibido** escribir, enviar mensajes de prueba, "
            "utilizar comandos o publicar cualquier contenido en este canal.\n\n"
            "🔨 Cualquier usuario que envíe un mensaje en este canal será "
            "**BANEADO PERMANENTEMENTE** de **MineOrbit**, sin previo aviso y sin excepciones.\n\n"
            "⚠️ Este canal se utiliza exclusivamente para detectar a los usuarios que envían imágenes de scams. No está destinado a reportes, conversaciones ni ningún otro tipo de interacción. "
            "correspondientes. Este canal no debe utilizarse para conversar ni interactuar.\n\n"
            "**Lee esta advertencia y respeta las normas. No se realizarán excepciones.**"
        ),
        color=COLOR_ERROR,
    )
    en = discord.Embed(
        description=(
            "## 🇺🇸 ENGLISH\n"
            "🔒 This channel is **EXCLUSIVELY** for receiving scam images. "
            "**Sending any type of message is strictly prohibited**.\n\n"
            "⛔ Writing, sending test messages, using commands, or posting any other "
            "content in this channel is **strictly forbidden**.\n\n"
            "🔨 Any user who sends a message in this channel will be "
            "**PERMANENTLY BANNED** from **MineOrbit**, without prior warning or exceptions.\n\n"
            "⚠️ This channel is used exclusively for detecting users who send images of scams. It is not intended for reports, conversations, or any other type of interaction. "
            "This channel must not be used for chatting or interacting.\n\n"
            "**Read this warning carefully and follow the rules. No exceptions will be made.**\n\n"
            "-# 🌀MineOrbit Network — Tolerancia cero ante el incumplimiento de las normas."
        ),
        color=COLOR_ERROR,
    )
    return [es, en]


def build_counter_view(banned_count: int) -> discord.ui.View:
    """Contador como botón gris deshabilitado bajo el aviso (ej. «🔨 Baneados: 623»).
    No tiene callback: es solo visual, así que no necesita registrarse como
    vista persistente y sigue mostrándose tras reiniciar el bot."""
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label=f"Baneados: {banned_count:,}",
            emoji="🔨",
            disabled=True,
        )
    )
    return view


# ── Cog ──────────────────────────────────────────────────────────────────────


class ScamGuard(commands.Cog, name="ScamGuard"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # guild_id -> {"channel_id", "enabled", "allow_images"} (evita consultar
        # SQLite en cada mensaje del servidor)
        self._cache: dict[int, dict] = {}
        # (guild_id, user_id) -> monotonic: usuarios ya procesados recientemente
        self._recent: dict[tuple[int, int], float] = {}

    async def cog_load(self):
        for row in await db.get_all_scamguard_configs():
            self._set_cache(row)

    def _set_cache(self, row: dict) -> None:
        self._cache[row["guild_id"]] = {
            "channel_id": row.get("channel_id"),
            "enabled": bool(row.get("enabled")),
            "allow_images": bool(row.get("allow_images")),
        }

    # ── Detección ────────────────────────────────────────────────────────────

    @staticmethod
    def _is_image_only(message: discord.Message) -> bool:
        """True si el mensaje no tiene texto y todos sus adjuntos son imágenes."""
        if (message.content or "").strip() or not message.attachments:
            return False
        return all((a.content_type or "").startswith("image/") for a in message.attachments)

    @staticmethod
    async def _is_exempt(member: discord.Member) -> bool:
        """Única excepción: el dueño del servidor (y bots). Staff y admins
        SÍ son sancionados; si el rol de alguien es igual o superior al del
        bot, Discord impide el ban y se avisa en el log de moderación."""
        return member.bot or member.id == member.guild.owner_id

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        guild = message.guild
        if guild is None or message.author.bot or message.webhook_id:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return

        cfg = self._cache.get(guild.id)
        if not cfg or not cfg["enabled"] or cfg["channel_id"] != message.channel.id:
            return
        if cfg["allow_images"] and self._is_image_only(message):
            return

        member = message.author
        if not isinstance(member, discord.Member):
            member = guild.get_member(member.id)
            if member is None:
                return
        if await self._is_exempt(member):
            return

        # Check-and-set sin awaits intermedios → atómico en asyncio. Evita que
        # varios mensajes simultáneos del mismo spammer cuenten como varios bans.
        now = time.monotonic()
        for k in [k for k, t in self._recent.items() if now - t > RECENT_TTL]:
            self._recent.pop(k, None)
        key = (guild.id, member.id)
        if key in self._recent:
            with suppress(discord.HTTPException):
                await message.delete()
            return
        self._recent[key] = now

        try:
            await self._punish(message, member)
        except Exception:
            log.exception("Anti-Scam Guard falló procesando a %s (%s)", member, member.id)

    # ── Sanción ──────────────────────────────────────────────────────────────

    async def _punish(self, message: discord.Message, member: discord.Member) -> None:
        guild = message.guild
        content = message.content or ""
        n_attachments = len(message.attachments)

        # 1) Borrar de inmediato el mensaje que disparó la detección.
        with suppress(discord.HTTPException):
            await message.delete()

        # 2) Ban + borrado de mensajes de los últimos 7 días en todos los canales.
        banned, error = False, None
        try:
            await guild.ban(
                member, reason=BAN_REASON, delete_message_seconds=PURGE_SECONDS
            )
            banned = True
        except discord.Forbidden:
            error = "Sin permisos o el rol del usuario es igual/superior al del bot."
        except discord.HTTPException as e:
            error = f"Error HTTP al banear: {e}"

        # 3) Plan B: si no se pudo banear, al menos limpiar sus mensajes.
        swept = 0
        if not banned:
            swept = await self._sweep_messages(guild, member)

        # 4) Contador, aviso, caso de mod y log.
        count = None
        if banned:
            count = await db.register_scamguard_ban(
                guild.id, member.id, message.channel.id, content, n_attachments
            )
            with suppress(Exception):
                await db.create_mod_case(
                    guild.id, "ban", member.id, self.bot.user.id, BAN_REASON
                )
            await self._refresh_notice(guild, count)

        await self._send_log(
            guild, member, message.channel, content, n_attachments,
            banned=banned, error=error, swept=swept, count=count,
        )
        (log.info if banned else log.warning)(
            "Anti-Scam Guard: %s (%s) en guild %s → %s",
            member, member.id, guild.id, "baneado" if banned else f"FALLÓ ({error})",
        )

    async def _sweep_messages(self, guild: discord.Guild, member: discord.Member) -> int:
        """Borra manualmente los mensajes recientes (≤7 días) del usuario.
        Solo se usa si el ban falló. Revisa hasta 200 mensajes por canal."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=PURGE_SECONDS)
        total = 0
        for channel in guild.text_channels:
            perms = channel.permissions_for(guild.me)
            if not (perms.read_message_history and perms.manage_messages):
                continue
            try:
                deleted = await channel.purge(
                    limit=200,
                    after=cutoff,
                    check=lambda m: m.author.id == member.id,
                    reason=BAN_REASON,
                )
                total += len(deleted)
            except discord.HTTPException:
                continue
        return total

    async def _refresh_notice(self, guild: discord.Guild, count: int) -> None:
        """Actualiza el contador en el aviso publicado (mejor esfuerzo)."""
        row = await db.get_scamguard_config(guild.id)
        channel = guild.get_channel(row.get("channel_id") or 0)
        notice_id = row.get("notice_message_id")
        if not channel or not notice_id:
            return
        with suppress(discord.HTTPException):
            await channel.get_partial_message(notice_id).edit(
                embeds=build_notice_embeds(count),
                view=build_counter_view(count),
            )

    async def _send_log(
        self, guild, member, channel, content, n_attachments,
        *, banned: bool, error: str | None, swept: int, count: int | None,
    ) -> None:
        config = await db.get_guild_config(guild.id)
        log_ch = guild.get_channel(config.get("mod_log_channel") or 0)
        if not log_ch:
            return
        embed = discord.Embed(
            title="🚨 Anti-Scam Guard — " + ("Usuario baneado" if banned else "⚠️ Ban fallido"),
            color=COLOR_MOD if banned else COLOR_ERROR,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Usuario", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Canal", value=channel.mention, inline=True)
        embed.add_field(name="Adjuntos", value=str(n_attachments), inline=True)
        embed.add_field(
            name="Contenido",
            value=(content[:900] or "*(sin texto)*"),
            inline=False,
        )
        if banned:
            embed.add_field(
                name="Resultado",
                value="Ban aplicado y mensajes de los últimos 7 días eliminados en todo el servidor.",
                inline=False,
            )
            embed.set_footer(text=f"Total baneados por el sistema: {count:,}")
        else:
            embed.add_field(name="Error", value=error or "Desconocido", inline=False)
            embed.add_field(
                name="Plan B",
                value=f"{swept} mensajes eliminados manualmente. **Requiere acción del staff.**",
                inline=False,
            )
        with suppress(discord.HTTPException):
            await log_ch.send(embed=embed)

    # ── Comandos ─────────────────────────────────────────────────────────────

    group = app_commands.Group(
        name="antiscam",
        description="Sistema anti-spammers para el canal de reportes de scams.",
        guild_only=True,
    )

    @staticmethod
    def _missing_perms(guild: discord.Guild, channel: discord.TextChannel) -> list[str]:
        me = guild.me
        guild_perms = me.guild_permissions
        ch_perms = channel.permissions_for(me)
        missing = []
        if not guild_perms.ban_members:
            missing.append("Banear miembros (servidor)")
        if not ch_perms.view_channel:
            missing.append("Ver canal")
        if not ch_perms.send_messages:
            missing.append("Enviar mensajes")
        if not ch_perms.embed_links:
            missing.append("Insertar enlaces")
        if not ch_perms.manage_messages:
            missing.append("Gestionar mensajes (canal)")
        return missing

    async def _post_notice(self, guild: discord.Guild, channel: discord.TextChannel, row: dict) -> int:
        """Publica el aviso (borrando el anterior si existe) y devuelve su ID."""
        old_id = row.get("notice_message_id")
        old_ch = guild.get_channel(row.get("channel_id") or 0)
        if old_id and old_ch:
            with suppress(discord.HTTPException):
                await old_ch.get_partial_message(old_id).delete()
        count = row.get("banned_count", 0)
        msg = await channel.send(
            embeds=build_notice_embeds(count), view=build_counter_view(count)
        )
        return msg.id

    @group.command(name="canal", description="Configura el canal anti-scam y publica el aviso.")
    @app_commands.describe(
        canal="Canal donde se vigilarán los mensajes",
        permitir_imagenes="Si es True, los mensajes con SOLO imágenes (sin texto) no se sancionan",
    )
    @checks.is_admin()
    async def canal(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
        permitir_imagenes: bool = False,
    ):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        missing = self._missing_perms(guild, canal)
        blocking = {"Ver canal", "Enviar mensajes", "Insertar enlaces"} & set(missing)
        if blocking:
            return await interaction.followup.send(
                embed=embeds.error(
                    f"No puedo publicar el aviso en {canal.mention}. Me falta: "
                    + ", ".join(f"**{m}**" for m in sorted(blocking))
                ),
                ephemeral=True,
            )

        row = await db.get_scamguard_config(guild.id)
        try:
            notice_id = await self._post_notice(guild, canal, row)
        except discord.HTTPException as e:
            return await interaction.followup.send(
                embed=embeds.error(f"No se pudo publicar el aviso: {e}"), ephemeral=True
            )

        await db.update_scamguard_config(
            guild.id,
            channel_id=canal.id,
            notice_message_id=notice_id,
            enabled=1,
            allow_images=int(permitir_imagenes),
        )
        self._set_cache(await db.get_scamguard_config(guild.id))

        desc = (
            f"Canal configurado en {canal.mention} y aviso publicado.\n"
            + (
                "Modo: **solo imágenes permitidas** — cualquier mensaje con texto u otro contenido será sancionado."
                if permitir_imagenes
                else "Modo: **estricto** — cualquier mensaje o imagen de un no-staff será sancionado."
            )
        )
        embed = embeds.success(desc, title="✅ Anti-Scam Guard activado")
        if missing:
            embed.add_field(
                name="⚠️ Permisos faltantes (el sistema no funcionará bien)",
                value="\n".join(f"• {m}" for m in missing),
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @group.command(name="estado", description="Muestra la configuración y el contador de baneados.")
    @checks.is_admin()
    async def estado(self, interaction: discord.Interaction):
        guild = interaction.guild
        row = await db.get_scamguard_config(guild.id)
        channel = guild.get_channel(row.get("channel_id") or 0)

        embed = discord.Embed(title="🚨 Anti-Scam Guard — Estado", color=COLOR_MOD)
        embed.add_field(
            name="Sistema", value="🟢 Activo" if row.get("enabled") else "🔴 Inactivo", inline=True
        )
        embed.add_field(
            name="Canal", value=channel.mention if channel else "*(no configurado)*", inline=True
        )
        embed.add_field(
            name="Modo",
            value="Solo imágenes permitidas" if row.get("allow_images") else "Estricto",
            inline=True,
        )
        embed.add_field(
            name="🔨 Usuarios baneados", value=f"**{row.get('banned_count', 0):,}**", inline=False
        )
        if channel:
            missing = self._missing_perms(guild, channel)
            embed.add_field(
                name="Permisos del bot",
                value="✅ Todos correctos" if not missing else "⚠️ Faltan:\n" + "\n".join(f"• {m}" for m in missing),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="aviso", description="Vuelve a publicar el aviso en el canal configurado.")
    @checks.is_admin()
    async def aviso(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        row = await db.get_scamguard_config(guild.id)
        channel = guild.get_channel(row.get("channel_id") or 0)
        if not channel:
            return await interaction.followup.send(
                embed=embeds.error("No hay canal configurado. Usa `/antiscam canal`."), ephemeral=True
            )
        try:
            notice_id = await self._post_notice(guild, channel, row)
        except discord.HTTPException as e:
            return await interaction.followup.send(
                embed=embeds.error(f"No se pudo publicar el aviso: {e}"), ephemeral=True
            )
        await db.update_scamguard_config(guild.id, notice_message_id=notice_id)
        await interaction.followup.send(
            embed=embeds.success(f"Aviso republicado en {channel.mention}."), ephemeral=True
        )

    @group.command(name="activar", description="Reactiva el sistema con el canal ya configurado.")
    @checks.is_admin()
    async def activar(self, interaction: discord.Interaction):
        row = await db.get_scamguard_config(interaction.guild_id)
        if not interaction.guild.get_channel(row.get("channel_id") or 0):
            return await interaction.response.send_message(
                embed=embeds.error("No hay canal configurado. Usa `/antiscam canal`."), ephemeral=True
            )
        await db.update_scamguard_config(interaction.guild_id, enabled=1)
        self._set_cache(await db.get_scamguard_config(interaction.guild_id))
        await interaction.response.send_message(
            embed=embeds.success("Anti-Scam Guard activado."), ephemeral=True
        )

    @group.command(name="desactivar", description="Desactiva el sistema sin borrar la configuración ni el contador.")
    @checks.is_admin()
    async def desactivar(self, interaction: discord.Interaction):
        await db.update_scamguard_config(interaction.guild_id, enabled=0)
        self._set_cache(await db.get_scamguard_config(interaction.guild_id))
        await interaction.response.send_message(
            embed=embeds.success("Anti-Scam Guard desactivado."), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ScamGuard(bot))