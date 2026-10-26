"""
Cog: Moderation — Full slash-command moderation suite
- ban / unban / kick / mute / unmute / timeout / untimeout
- warn / warnings / clearwarn
- case history per user
- mod log channel
- user info / server info

"""

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from database import db
from utils import embeds as emb, helpers, checks
from config import (
    COLOR_MOD,
    COLOR_ERROR,
    COLOR_SUCCESS,
    COLOR_WARNING,
    EMOJI_BAN,
    EMOJI_KICK,
    EMOJI_MUTE,
    EMOJI_WARN,
)

logger = logging.getLogger(__name__)

DEFAULT_WARNING_THRESHOLD = 10

# Nombre del servidor e invitación usados en las plantillas de DM.
# Cámbialos aquí si cambia el nombre del servidor o el link de invitación.
SERVER_NAME = "MineOrbit"
INVITE_LINK = "https://discord.gg/XWJWZfk23c"


# ─────────────────────────────────────────────────────────────────────────
# Plantillas de LOG (canal de mod-log)
# ─────────────────────────────────────────────────────────────────────────
# "duration": si es True, el embed incluye la línea de Duración (solo si
# se pasa un valor de duración al construir el embed).
# "reason": si es False, la plantilla NO muestra línea de Motivo aunque se
# pase una razón (coincide con las plantillas de unmute/untimeout/unban,
# que no exponen el motivo en el log público).
_LOG_META = {
    "warn": {
        "emoji": "⚠️",
        "title": "ADVERTENCIA",
        "verb": "El usuario ha recibido una Advertencia (Warn).",
        "color": COLOR_WARNING,
        "duration": False,
        "reason": True,
    },
    "mute": {
        "emoji": "🔇",
        "title": "MUTE",
        "verb": "El usuario ha sido silenciado correctamente.",
        "color": COLOR_WARNING,
        "duration": True,
        "reason": True,
    },
    "timeout": {
        "emoji": "⏱️",
        "title": "TIMEOUT",
        "verb": "El usuario ha recibido un Timeout en Discord.",
        "color": COLOR_WARNING,
        "duration": True,
        "reason": True,
    },
    "kick": {
        "emoji": "👢",
        "title": "EXPULSIÓN",
        "verb": "El usuario ha sido expulsado del servidor.",
        "color": COLOR_WARNING,
        "duration": False,
        "reason": True,
    },
    "ban": {
        "emoji": "🔨",
        "title": "BAN",
        "verb": "El usuario ha sido baneado del servidor.",
        "color": COLOR_ERROR,
        "duration": True,
        "reason": True,
    },
    "unmute": {
        "emoji": "🔊",
        "title": "UNMUTE",
        "verb": "El silencio del usuario ha sido retirado.",
        "color": COLOR_SUCCESS,
        "duration": False,
        "reason": False,
    },
    "untimeout": {
        "emoji": "🔓",
        "title": "TIMEOUT REMOVIDO",
        "verb": "El Timeout del usuario ha sido eliminado.",
        "color": COLOR_SUCCESS,
        "duration": False,
        "reason": False,
    },
    "unban": {
        "emoji": "🌀",
        "title": "UNBAN",
        "verb": "El usuario ha sido desbaneado y puede volver a ingresar al servidor.",
        "color": COLOR_SUCCESS,
        "duration": False,
        "reason": False,
    },
}


def _discord_ts() -> str:
    """Timestamp dinámico de Discord (se ajusta a la zona horaria de cada usuario)."""
    return f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>"


def build_log_embed(
    kind: str,
    user: discord.abc.User,
    moderator: discord.abc.User,
    reason: str | None = None,
    duration: str | None = None,
) -> discord.Embed:
    """Construye el embed de log según la plantilla oficial para `kind`."""
    meta = _LOG_META[kind]

    lines = [
        f"👤 **Usuario:** {user.mention} ({user.id})",
        f"👮 **Moderador:** {moderator.mention}",
    ]
    if meta["duration"] and duration:
        lines.append(f"⏳ **Duración:** {duration}")
    if meta["reason"] and reason is not None:
        lines.append(f"📄 **Motivo:** {reason}")
    lines.append(f"🕒 **Fecha:** {_discord_ts()}")

    description = "\n".join(lines) + f"\n\n{meta['verb']}"

    return discord.Embed(
        title=f"{meta['emoji']} LOG | {meta['title']}",
        description=description,
        color=meta["color"],
    )


# ─────────────────────────────────────────────────────────────────────────
# Plantillas de DM (mensaje directo al usuario sancionado)
# ─────────────────────────────────────────────────────────────────────────
_DM_TEMPLATES = {
    "warn": {
        "title": "⚠️ Has recibido una Advertencia",
        "color": COLOR_WARNING,
        "body": (
            "Hola, **{user}**.\n\n"
            "Has recibido una **Advertencia (Warn)** en **{server}**.\n\n"
            "📄 **Motivo:** {reason}\n"
            "👮 **Moderador:** {moderator}\n\n"
            "Esta advertencia ha sido registrada en tu historial. Te recomendamos "
            "respetar las normas del servidor para evitar sanciones más severas.\n\n"
            "📜 Gracias por colaborar y mantener una comunidad sana."
        ),
    },
    "mute": {
        "title": "🔇 Has sido Silenciado",
        "color": COLOR_WARNING,
        "body": (
            "Hola, **{user}**.\n\n"
            "Has recibido un **Mute** en **{server}**.\n\n"
            "📄 **Motivo:** {reason}\n"
            "⏳ **Duración:** {duration}\n"
            "👮 **Moderador:** {moderator}\n\n"
            "Mientras dure esta sanción no podrás hablar en el chat. Una vez finalice, "
            "podrás volver a participar normalmente.\n\n"
            "📜 Gracias por respetar las normas del servidor."
        ),
    },
    "timeout": {
        "title": "⏱️ Has recibido un Timeout",
        "color": COLOR_WARNING,
        "body": (
            "Hola, **{user}**.\n\n"
            "Has recibido un **Timeout** en el servidor de Discord de **{server}**.\n\n"
            "📄 **Motivo:** {reason}\n"
            "⏳ **Duración:** {duration}\n"
            "👮 **Moderador:** {moderator}\n\n"
            "Durante este tiempo no podrás enviar mensajes ni interactuar en el servidor.\n\n"
            "📜 Esperamos verte nuevamente cuando finalice la sanción."
        ),
    },
    "kick": {
        "title": "👢 Has sido Expulsado",
        "color": COLOR_WARNING,
        "body": (
            "Hola, **{user}**.\n\n"
            "Has sido expulsado de **{server}**.\n\n"
            "📄 Motivo: {reason}\n"
            "👮 Moderador: {moderator}\n\n"
            "Podrás volver a unirte al servidor mediante una nueva invitación, siempre "
            "que no exista otra sanción activa.\n\n"
            "📜 Esperamos que la próxima vez respetes las normas de la comunidad."
        ),
    },
    "ban": {
        "title": "🔨 Has sido Baneado",
        "color": COLOR_ERROR,
        "body": (
            "Hola, **{user}**.\n\n"
            "Has sido baneado de **{server}**.\n\n"
            "📄 Motivo: {reason}\n"
            "⏳ Duración: {duration}\n"
            "👮 Moderador: {moderator}\n\n"
            "Si consideras que esta sanción fue aplicada por error, puedes presentar "
            "una apelación desde nuestro servidor de Discord.\n\n"
            "🌀 Gracias por tu comprensión."
        ),
    },
    "unban": {
        "title": "🌀 Tu Ban ha sido Retirado",
        "color": COLOR_SUCCESS,
        "body": (
            "Hola, **{user}**.\n\n"
            "Nos alegra informarte que tu **Ban** en **{server}** ha sido retirado.\n\n"
            "👮 **Moderador:** {moderator}\n\n"
            "🥳 **Invitación:** [Click aquí para entrar]({invite})\n\n"
            "Ya puedes volver a unirte al servidor y continuar tu aventura junto a la "
            "comunidad.\n\n"
            "🚀 Esperamos que disfrutes tu estancia y recuerdes respetar las normas "
            "para evitar futuras sanciones.\n"
            "¡Bienvenido de nuevo!"
        ),
    },
    "untimeout": {
        "title": "🔓 Tu Timeout ha Finalizado",
        "color": COLOR_SUCCESS,
        "body": (
            "Hola, **{user}**.\n\n"
            "Tu **Timeout** en el servidor de Discord de **{server}** ha sido retirado.\n\n"
            "👮 **Moderador:** {moderator}\n\n"
            "Ya puedes volver a enviar mensajes e interactuar normalmente con la comunidad.\n\n"
            "🌀 Gracias por tu paciencia y esperamos que continúes disfrutando de {server}."
        ),
    },
    "unmute": {
        "title": "🔊 Tu Mute ha sido Retirado",
        "color": COLOR_SUCCESS,
        "body": (
            "Hola, **{user}**.\n\n"
            "Tu **Mute** en **{server}** ha sido retirado.\n\n"
            "👮 **Moderador:** {moderator}\n\n"
            "Ya puedes volver a comunicarte con normalidad dentro del servidor.\n\n"
            "🚀 Gracias por formar parte de nuestra comunidad y recuerda respetar las "
            "normas para mantener un ambiente agradable para todos."
        ),
    },
}


def build_dm_embed(
    kind: str,
    user: discord.abc.User,
    moderator: discord.abc.User,
    reason: str | None = None,
    duration: str | None = None,
) -> discord.Embed:
    """Construye el embed de DM según la plantilla oficial para `kind`."""
    meta = _DM_TEMPLATES[kind]
    body = meta["body"].format(
        user=user.display_name if hasattr(user, "display_name") else str(user),
        server=SERVER_NAME,
        reason=reason or "Sin razón especificada",
        duration=duration or "Permanente",
        moderator=str(moderator),
        invite=INVITE_LINK,
    )
    return discord.Embed(title=meta["title"], description=body, color=meta["color"])


async def _dm_user(user: discord.abc.User, embed: discord.Embed) -> bool:
    """
    Intenta enviar un DM. Devuelve True si se entregó, False si no.
    No lanza excepción: es normal que un usuario tenga los DMs cerrados, o
    que Discord bloquee el mensaje porque el bot no comparte ningún
    servidor con él (esto pasa siempre en /unban, ya que el usuario no
    vuelve a ser miembro solo por desbanearlo). Los fallos se registran en
    el logger para poder diagnosticarlos desde la consola.
    """
    try:
        await user.send(embed=embed)
        return True
    except discord.Forbidden:
        logger.info(
            "No se pudo enviar DM a %s (%s): DMs cerrados o sin servidor en común.",
            user, user.id,
        )
        return False
    except discord.HTTPException as e:
        logger.warning("Error HTTP al enviar DM a %s (%s): %s", user, user.id, e)
        return False


async def _log_mod_action(guild: discord.Guild, embed: discord.Embed):
    config = await db.get_guild_config(guild.id)
    ch_id = config.get("mod_log_channel")
    if ch_id:
        ch = guild.get_channel(ch_id)
        if ch:
            await ch.send(embed=embed)


def _hierarchy_error(
    interaction: discord.Interaction, usuario: discord.Member, action: str
) -> str | None:
    """
    Devuelve un mensaje de error si la acción no debe proceder, o None si
    está permitida. Cubre: auto-moderación, moderación del bot, jerarquía
    de roles del staff y jerarquía de roles del bot.
    """
    guild = interaction.guild
    me = guild.me

    if usuario.id == interaction.user.id:
        return f"❌ No puedes {action}te a ti mismo."

    if usuario.id == guild.owner_id:
        return "❌ No puedes moderar al dueño del servidor."

    if usuario.id == me.id:
        return "❌ No puedo moderarme a mí mismo."

    if usuario.top_role >= interaction.user.top_role and guild.owner_id != interaction.user.id:
        return f"❌ No puedes {action} a alguien con un rol igual o superior al tuyo."

    if usuario.top_role >= me.top_role:
        return f"❌ No puedo {action} a este usuario: su rol es igual o superior al mío."

    return None


class LockdownUnlockView(discord.ui.View):
    """Botón de acceso rápido para desactivar un lockdown activo, sin tener
    que volver a escribir /lockdown. No requiere que sea el mismo mod que
    lo activó — cualquier mod puede desactivarlo. Es una vista persistente
    (sobrevive a reinicios del bot, ver bot.py) porque no guarda estado en
    la instancia — todo lo lee de la interacción / la base de datos."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🔓 Desbloquear ahora",
        style=discord.ButtonStyle.danger,
        custom_id="lockdown_unlock_button",
    )
    async def unlock(self, interaction: discord.Interaction, _):
        if not await checks.check_is_mod(interaction):
            return await interaction.response.send_message(
                "❌ Necesitas el rol de moderación para usar este botón.", ephemeral=True
            )

        state = await db.get_lockdown_state(interaction.guild_id)
        if not state:
            button = discord.utils.get(self.children, custom_id="lockdown_unlock_button")
            if button:
                button.disabled = True
            await interaction.response.edit_message(view=self)
            return await interaction.followup.send(
                "ℹ️ Este lockdown ya fue desactivado antes.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)
        cog: "ModerationCog" = interaction.client.get_cog("Moderation")
        await cog._lockdown_unlock(interaction.guild, state)

        button = discord.utils.get(self.children, custom_id="lockdown_unlock_button")
        if button:
            button.disabled = True
            button.label = "🔓 Desbloqueado"
        try:
            await interaction.message.edit(view=self)
        except (discord.NotFound, discord.HTTPException):
            pass

        embed = discord.Embed(
            title="🔓 Lockdown desactivado",
            description=(
                f"Se restauraron **{len(state['channel_ids'])} canales**.\n"
                f"Desactivado por {interaction.user.mention} (vía botón)."
            ),
            color=COLOR_SUCCESS,
        )
        await interaction.followup.send(embed=embed)


class ModerationCog(commands.Cog, name="Moderation"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Ban ──────────────────────────────────────────────────────────────────

    @app_commands.command(name="ban", description="Banear a un usuario")
    @app_commands.describe(
        usuario="Usuario a banear",
        razon="Razón del ban",
        eliminar_mensajes="Días de mensajes a eliminar (0-7)",
    )
    @checks.is_mod()
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member,
        razon: str = "Sin razón especificada",
        eliminar_mensajes: int = 0,
    ):
        await interaction.response.defer(ephemeral=True)

        error = _hierarchy_error(interaction, usuario, "banear")
        if error:
            return await interaction.followup.send(error)

        # NOTA: este bot no implementa bans temporales con auto-desban
        # programado; la duración mostrada en la plantilla siempre es
        # "Permanente". Si necesitas soporte real de bans temporales
        # (con una tarea en segundo plano que revise y desbanee
        # automáticamente), lo puedo añadir aparte.
        duration_text = "Permanente"

        # El DM se envía ANTES de banear: una vez baneado, el usuario deja
        # de compartir servidor con el bot y Discord bloquea el DM por
        # defecto (igual que ya pasaba en /kick).
        dm_sent = await _dm_user(
            usuario,
            build_dm_embed("ban", usuario, interaction.user, razon, duration_text),
        )

        try:
            await interaction.guild.ban(
                usuario,
                reason=f"{razon} — {interaction.user}",
                delete_message_seconds=max(0, min(7, eliminar_mensajes)) * 86400,
            )
        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ No tengo permisos suficientes para banear a este usuario."
            )
        except discord.HTTPException as e:
            return await interaction.followup.send(f"❌ Error al banear: {e}")

        case_id = await db.create_mod_case(
            interaction.guild_id, "ban", usuario.id, interaction.user.id, razon
        )

        log_embed = build_log_embed("ban", usuario, interaction.user, razon, duration_text)
        await _log_mod_action(interaction.guild, log_embed)
        await db.log_staff_action(
            interaction.guild_id, interaction.user.id, "ban", usuario.id, razon
        )

        confirm = emb.mod_action(
            f"{EMOJI_BAN} Ban", usuario, interaction.user, razon, case_id
        )
        if not dm_sent:
            confirm.set_footer(text="⚠️ No se pudo notificar al usuario por DM.")
        await interaction.followup.send(embed=confirm)

    # ── Unban ────────────────────────────────────────────────────────────────

    @app_commands.command(name="unban", description="Desbanear a un usuario por ID")
    @app_commands.describe(user_id="ID del usuario", razon="Razón del desban")
    @checks.is_mod()
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def unban(
        self, interaction: discord.Interaction, user_id: str, razon: str = "Sin razón"
    ):
        await interaction.response.defer(ephemeral=True)

        try:
            uid = int(user_id)
        except ValueError:
            return await interaction.followup.send("❌ ID inválido: debe ser numérico.")

        try:
            user = await self.bot.fetch_user(uid)
        except discord.NotFound:
            return await interaction.followup.send("❌ No existe ningún usuario con ese ID.")
        except discord.HTTPException as e:
            return await interaction.followup.send(f"❌ Error al buscar el usuario: {e}")

        try:
            await interaction.guild.unban(user, reason=razon)
        except discord.NotFound:
            return await interaction.followup.send("❌ El usuario no está baneado.")
        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ No tengo permisos suficientes para desbanear."
            )
        except discord.HTTPException as e:
            return await interaction.followup.send(f"❌ Error al desbanear: {e}")

        await _dm_user(user, build_dm_embed("unban", user, interaction.user))

        log_embed = build_log_embed("unban", user, interaction.user)
        await _log_mod_action(interaction.guild, log_embed)
        await db.log_staff_action(
            interaction.guild_id, interaction.user.id, "unban", user.id, razon
        )

        confirm = discord.Embed(
            title="🔓 Desban",
            description=f"**Usuario:** {user} (`{uid}`)\n**Razón:** {razon}\n**Moderador:** {interaction.user.mention}",
            color=COLOR_SUCCESS,
        )
        await interaction.followup.send(embed=confirm)

    # ── Kick ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="kick", description="Expulsar a un usuario")
    @app_commands.describe(usuario="Usuario a expulsar", razon="Razón")
    @checks.is_mod()
    @app_commands.checks.bot_has_permissions(kick_members=True)
    async def kick(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member,
        razon: str = "Sin razón especificada",
    ):
        await interaction.response.defer(ephemeral=True)

        error = _hierarchy_error(interaction, usuario, "expulsar")
        if error:
            return await interaction.followup.send(error)

        # El DM debe enviarse ANTES del kick: una vez expulsado, el bot ya
        # no comparte ningún servidor en común con el usuario (si no hay
        # otros mutuos) y el DM podría fallar silenciosamente igual, pero
        # es más consistente notificar mientras aún es miembro.
        await _dm_user(usuario, build_dm_embed("kick", usuario, interaction.user, razon))

        try:
            await interaction.guild.kick(usuario, reason=razon)
        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ No tengo permisos suficientes para expulsar a este usuario."
            )
        except discord.HTTPException as e:
            return await interaction.followup.send(f"❌ Error al expulsar: {e}")

        case_id = await db.create_mod_case(
            interaction.guild_id, "kick", usuario.id, interaction.user.id, razon
        )

        log_embed = build_log_embed("kick", usuario, interaction.user, razon)
        await _log_mod_action(interaction.guild, log_embed)
        await db.log_staff_action(
            interaction.guild_id, interaction.user.id, "kick", usuario.id, razon
        )

        confirm = emb.mod_action(
            f"{EMOJI_KICK} Kick", usuario, interaction.user, razon, case_id
        )
        await interaction.followup.send(embed=confirm)

    # ── Timeout ──────────────────────────────────────────────────────────────

    @app_commands.command(
        name="timeout", description="Silenciar temporalmente con timeout de Discord"
    )
    @app_commands.describe(
        usuario="Usuario", duracion="Duración (1m, 1h, 1d, max 28d)", razon="Razón"
    )
    @checks.is_mod()
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def timeout_cmd(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member,
        duracion: str,
        razon: str = "Sin razón",
    ):
        await interaction.response.defer(ephemeral=True)

        td = helpers.parse_duration(duracion)
        if not td or td.total_seconds() <= 0 or td.total_seconds() > 2419200:
            return await interaction.followup.send("❌ Duración inválida (máx 28d).")

        error = _hierarchy_error(interaction, usuario, "silenciar")
        if error:
            return await interaction.followup.send(error)

        duration_text = helpers.format_duration(td)

        try:
            await usuario.timeout(td, reason=razon)
        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ No tengo permisos suficientes para aplicar timeout a este usuario."
            )
        except discord.HTTPException as e:
            return await interaction.followup.send(f"❌ Error al aplicar timeout: {e}")

        case_id = await db.create_mod_case(
            interaction.guild_id,
            "timeout",
            usuario.id,
            interaction.user.id,
            razon,
            duration=int(td.total_seconds()),
        )

        await _dm_user(
            usuario,
            build_dm_embed("timeout", usuario, interaction.user, razon, duration_text),
        )

        log_embed = build_log_embed("timeout", usuario, interaction.user, razon, duration_text)
        await _log_mod_action(interaction.guild, log_embed)
        await db.log_staff_action(
            interaction.guild_id, interaction.user.id, "timeout", usuario.id, razon
        )

        confirm = emb.mod_action(
            "⏱️ Timeout",
            usuario,
            interaction.user,
            razon,
            case_id,
            duration=duration_text,
        )
        await interaction.followup.send(embed=confirm)

    @app_commands.command(
        name="untimeout", description="Eliminar timeout de un usuario"
    )
    @app_commands.describe(usuario="Usuario", razon="Razón")
    @checks.is_mod()
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def untimeout_cmd(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member,
        razon: str = "Sin razón",
    ):
        await interaction.response.defer(ephemeral=True)

        try:
            await usuario.timeout(None, reason=razon)
        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ No tengo permisos suficientes para quitar el timeout."
            )
        except discord.HTTPException as e:
            return await interaction.followup.send(f"❌ Error al quitar el timeout: {e}")

        await _dm_user(usuario, build_dm_embed("untimeout", usuario, interaction.user))

        log_embed = build_log_embed("untimeout", usuario, interaction.user)
        await _log_mod_action(interaction.guild, log_embed)
        await db.log_staff_action(
            interaction.guild_id, interaction.user.id, "untimeout", usuario.id, razon
        )

        confirm = discord.Embed(
            title="✅ Timeout eliminado",
            description=f"{usuario.mention} ya puede volver a escribir.\n**Razón:** {razon}",
            color=COLOR_SUCCESS,
        )
        await interaction.followup.send(embed=confirm)

    # ── Mute (role-based) ─────────────────────────────────────────────────────
    #
    # COMANDO DESACTIVADO A PETICIÓN DEL USUARIO.
    # El código se conserva comentado por si se quiere reactivar más
    # adelante: para reactivarlo, quita el "#" del bloque de abajo
    # (respetando la indentación) y vuelve a sincronizar los comandos del
    # bot (bot.tree.sync()).
    #
    # @app_commands.command(name="mute", description="Mutear usuario con rol de mute")
    # @app_commands.describe(usuario="Usuario", razon="Razón")
    # @checks.is_mod()
    # @app_commands.checks.bot_has_permissions(manage_roles=True)
    # async def mute(
    #     self,
    #     interaction: discord.Interaction,
    #     usuario: discord.Member,
    #     razon: str = "Sin razón",
    # ):
    #     await interaction.response.defer(ephemeral=True)
    #
    #     config = await db.get_guild_config(interaction.guild_id)
    #     mute_role_id = config.get("mute_role")
    #     if not mute_role_id:
    #         return await interaction.followup.send(
    #             "❌ No hay rol de mute configurado. Usa `/config mute_rol` o usa `/timeout`."
    #         )
    #     role = interaction.guild.get_role(mute_role_id)
    #     if not role:
    #         return await interaction.followup.send("❌ Rol de mute no encontrado.")
    #
    #     error = _hierarchy_error(interaction, usuario, "mutear")
    #     if error:
    #         return await interaction.followup.send(error)
    #
    #     if role >= interaction.guild.me.top_role:
    #         return await interaction.followup.send(
    #             "❌ El rol de mute está por encima de mi rol más alto; no puedo asignarlo."
    #         )
    #
    #     try:
    #         await usuario.add_roles(role, reason=razon)
    #     except discord.Forbidden:
    #         return await interaction.followup.send(
    #             "❌ No tengo permisos suficientes para asignar el rol de mute."
    #         )
    #     except discord.HTTPException as e:
    #         return await interaction.followup.send(f"❌ Error al mutear: {e}")
    #
    #     case_id = await db.create_mod_case(
    #         interaction.guild_id, "mute", usuario.id, interaction.user.id, razon
    #     )
    #
    #     duration_text = "Indefinida"
    #
    #     await _dm_user(
    #         usuario,
    #         build_dm_embed("mute", usuario, interaction.user, razon, duration_text),
    #     )
    #
    #     log_embed = build_log_embed("mute", usuario, interaction.user, razon, duration_text)
    #     await _log_mod_action(interaction.guild, log_embed)
    #     await db.log_staff_action(
    #         interaction.guild_id, interaction.user.id, "mute", usuario.id, razon
    #     )
    #
    #     confirm = emb.mod_action(
    #         f"{EMOJI_MUTE} Mute", usuario, interaction.user, razon, case_id
    #     )
    #     await interaction.followup.send(embed=confirm)

    @app_commands.command(name="unmute", description="Desmutear usuario")
    @app_commands.describe(usuario="Usuario", razon="Razón")
    @checks.is_mod()
    @app_commands.checks.bot_has_permissions(manage_roles=True)
    async def unmute(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member,
        razon: str = "Sin razón",
    ):
        await interaction.response.defer(ephemeral=True)

        config = await db.get_guild_config(interaction.guild_id)
        mute_role_id = config.get("mute_role")
        if not mute_role_id:
            return await interaction.followup.send("❌ No hay rol de mute configurado.")
        role = interaction.guild.get_role(mute_role_id)

        if role and role in usuario.roles:
            try:
                await usuario.remove_roles(role, reason=razon)
            except discord.Forbidden:
                return await interaction.followup.send(
                    "❌ No tengo permisos suficientes para quitar el rol de mute."
                )
            except discord.HTTPException as e:
                return await interaction.followup.send(f"❌ Error al desmutear: {e}")

        await _dm_user(usuario, build_dm_embed("unmute", usuario, interaction.user))

        log_embed = build_log_embed("unmute", usuario, interaction.user)
        await _log_mod_action(interaction.guild, log_embed)
        await db.log_staff_action(
            interaction.guild_id, interaction.user.id, "unmute", usuario.id, razon
        )

        confirm = discord.Embed(
            title="🔊 Desmute",
            description=f"{usuario.mention} puede hablar de nuevo.\n**Razón:** {razon}",
            color=COLOR_SUCCESS,
        )
        await interaction.followup.send(embed=confirm)

    # ── Warn ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="warn", description="Advertir a un usuario")
    @app_commands.describe(usuario="Usuario", razon="Razón de la advertencia")
    @checks.is_mod()
    async def warn(
        self, interaction: discord.Interaction, usuario: discord.Member, razon: str
    ):
        await interaction.response.defer(ephemeral=True)

        if usuario.id == interaction.user.id:
            return await interaction.followup.send("❌ No puedes advertirte a ti mismo.")
        if usuario.id == self.bot.user.id:
            return await interaction.followup.send("❌ No puedo advertirme a mí mismo.")

        warn_id = await db.add_warning(
            interaction.guild_id, usuario.id, interaction.user.id, razon
        )
        total = await db.get_warning_count(interaction.guild_id, usuario.id)

        await _dm_user(usuario, build_dm_embed("warn", usuario, interaction.user, razon))

        log_embed = build_log_embed("warn", usuario, interaction.user, razon)
        await _log_mod_action(interaction.guild, log_embed)
        await db.log_staff_action(
            interaction.guild_id, interaction.user.id, "warn", usuario.id, razon
        )

        confirm = discord.Embed(
            title=f"{EMOJI_WARN} Advertencia #{warn_id}",
            color=COLOR_WARNING,
        )
        confirm.add_field(name="Usuario", value=usuario.mention, inline=True)
        confirm.add_field(name="Moderador", value=interaction.user.mention, inline=True)
        confirm.add_field(name="Razón", value=razon, inline=False)
        confirm.add_field(name="Total de puntos", value=str(total), inline=True)
        confirm.set_thumbnail(url=usuario.display_avatar.url)
        await interaction.followup.send(embed=confirm)

        config = await db.get_guild_config(interaction.guild_id)
        threshold = config.get("warning_threshold", DEFAULT_WARNING_THRESHOLD)
        if total >= threshold:
            alert = discord.Embed(
                title="🚨 Atención: Usuario con muchas advertencias",
                description=f"{usuario.mention} tiene **{total} puntos** de advertencia (umbral: {threshold}). Considera una acción mayor.",
                color=COLOR_ERROR,
            )
            ch_id = config.get("mod_log_channel")
            if ch_id:
                ch = interaction.guild.get_channel(ch_id)
                if ch:
                    await ch.send(embed=alert)

    @app_commands.command(name="warnings", description="Ver advertencias de un usuario")
    @app_commands.describe(usuario="Usuario a consultar")
    @checks.is_mod()
    async def warnings_cmd(
        self, interaction: discord.Interaction, usuario: discord.Member
    ):
        await interaction.response.defer(ephemeral=True)
        warns = await db.get_warnings(interaction.guild_id, usuario.id)
        total = await db.get_warning_count(interaction.guild_id, usuario.id)
        embed = discord.Embed(
            title=f"⚠️ Advertencias de {usuario}",
            color=COLOR_WARNING,
        )
        if not warns:
            embed.description = "Sin advertencias activas."
        else:
            for w in warns[:10]:
                embed.add_field(
                    name=f"#{w['id']} — {w['created_at'][:10]}",
                    value=f"**Razón:** {w['reason']}\n**Moderador:** <@{w['moderator_id']}> | Puntos: {w['points']}",
                    inline=False,
                )
        embed.set_footer(text=f"Total puntos activos: {total}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="clearwarn", description="Eliminar una advertencia por ID"
    )
    @app_commands.describe(warn_id="ID de la advertencia")
    @checks.is_mod()
    async def clearwarn(self, interaction: discord.Interaction, warn_id: int):
        await interaction.response.defer(ephemeral=True)
        deleted = await db.clear_warning(warn_id)
        if not deleted:
            return await interaction.followup.send(
                f"❌ No se encontró ninguna advertencia con ID **#{warn_id}**."
            )
        await interaction.followup.send(
            f"✅ Advertencia **#{warn_id}** eliminada."
        )

    # ── Cases ─────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="modhistory", description="Ver historial de moderación de un usuario"
    )
    @app_commands.describe(usuario="Usuario a consultar")
    @checks.is_mod()
    async def history_cmd(
        self, interaction: discord.Interaction, usuario: discord.Member
    ):
        await interaction.response.defer(ephemeral=True)
        cases = await db.get_user_cases(interaction.guild_id, usuario.id)
        warns = await db.get_warnings(interaction.guild_id, usuario.id)
        embed = emb.user_profile(usuario, cases, warns)
        for c in cases[:8]:
            embed.add_field(
                name=f"#{c['case_number']} {c['type'].upper()} — {c['created_at'][:10]}",
                value=c.get("reason") or "Sin razón",
                inline=False,
            )
        await interaction.followup.send(embed=embed)

    # ── Purge ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="purge", description="Eliminar mensajes masivamente")
    @app_commands.describe(
        cantidad="Número de mensajes a eliminar (1-100)",
        usuario="Eliminar solo mensajes de este usuario (opcional)",
    )
    @checks.is_mod()
    @app_commands.checks.bot_has_permissions(manage_messages=True)
    async def purge(
        self,
        interaction: discord.Interaction,
        cantidad: int,
        usuario: discord.Member = None,
    ):
        await interaction.response.defer(ephemeral=True)

        if not 1 <= cantidad <= 100:
            return await interaction.followup.send(
                "❌ La cantidad debe estar entre 1 y 100."
            )

        try:
            if usuario:
                collected = []
                async for msg in interaction.channel.history(limit=500):
                    if msg.author.id == usuario.id:
                        collected.append(msg)
                    if len(collected) >= cantidad:
                        break
                if not collected:
                    return await interaction.followup.send(
                        f"⚠️ No encontré mensajes recientes de {usuario.mention} en este canal."
                    )
                deleted = await interaction.channel.delete_messages(collected)
                count = len(collected)
            else:
                deleted = await interaction.channel.purge(limit=cantidad, bulk=True)
                count = len(deleted)

        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ No tengo permisos de `Gestionar mensajes` en este canal."
            )
        except discord.HTTPException as e:
            if "14 days" in str(e) or getattr(e, "code", None) == 50034:
                return await interaction.followup.send(
                    "❌ No puedo eliminar mensajes con más de 14 días de antigüedad "
                    "(limitación de la API de Discord)."
                )
            return await interaction.followup.send(
                f"❌ Error al eliminar mensajes: {e}"
            )

        suffix = f" de {usuario.mention}" if usuario else ""
        await interaction.followup.send(
            f"🗑️ **{count}** mensaje{'s' if count != 1 else ''} eliminado{'s' if count != 1 else ''}{suffix}.",
        )

    # ── Config ────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="config", description="Configurar el bot para este servidor"
    )
    @app_commands.describe(
        mod_log="Canal de logs de moderación",
        admin_rol="Rol de administrador",
        mod_rol="Rol de moderador",
        staff_rol="Rol de staff",
        mute_rol="Rol de mute",
        umbral_advertencias="Puntos de advertencia para disparar la alerta (por defecto 10)",
    )
    @checks.is_owner()
    async def config_cmd(
        self,
        interaction: discord.Interaction,
        mod_log: discord.TextChannel = None,
        admin_rol: discord.Role = None,
        mod_rol: discord.Role = None,
        staff_rol: discord.Role = None,
        mute_rol: discord.Role = None,
        umbral_advertencias: int = None,
    ):
        await interaction.response.defer(ephemeral=True)

        if umbral_advertencias is not None and umbral_advertencias < 1:
            return await interaction.followup.send(
                "❌ El umbral de advertencias debe ser mayor a 0."
            )
        if mute_rol and mute_rol.is_default():
            return await interaction.followup.send(
                "❌ No puedes usar @everyone como rol de mute."
            )

        kwargs = {}
        if mod_log:
            kwargs["mod_log_channel"] = mod_log.id
        if admin_rol:
            kwargs["admin_role"] = admin_rol.id
        if mod_rol:
            kwargs["mod_role"] = mod_rol.id
        if staff_rol:
            kwargs["staff_role"] = staff_rol.id
        if mute_rol:
            kwargs["mute_role"] = mute_rol.id
        if umbral_advertencias is not None:
            kwargs["warning_threshold"] = umbral_advertencias

        if kwargs:
            await db.set_guild_config(interaction.guild_id, **kwargs)

        embed = discord.Embed(title="⚙️ Configuración actualizada", color=COLOR_SUCCESS)
        if mod_log:
            embed.add_field(name="Log de moderación", value=mod_log.mention, inline=True)
        if admin_rol:
            embed.add_field(name="Rol admin", value=admin_rol.mention, inline=True)
        if mod_rol:
            embed.add_field(name="Rol mod", value=mod_rol.mention, inline=True)
        if staff_rol:
            embed.add_field(name="Rol staff", value=staff_rol.mention, inline=True)
        if mute_rol:
            embed.add_field(name="Rol mute", value=mute_rol.mention, inline=True)
        if umbral_advertencias is not None:
            embed.add_field(
                name="Umbral de advertencias", value=str(umbral_advertencias), inline=True
            )
        if not kwargs:
            embed.description = "No se cambió nada."
        await interaction.followup.send(embed=embed)

    # ── Userinfo / Serverinfo ─────────────────────────────────────────────────

    @app_commands.command(name="userinfo", description="Ver información de un usuario")
    @app_commands.describe(usuario="Usuario a consultar")
    async def userinfo(
        self, interaction: discord.Interaction, usuario: discord.Member = None
    ):
        target = usuario or interaction.user
        await interaction.response.defer(ephemeral=True)
        cases = await db.get_user_cases(interaction.guild_id, target.id)
        warns = await db.get_warnings(interaction.guild_id, target.id)
        embed = emb.user_profile(target, cases, warns)
        roles = [r.mention for r in target.roles[1:]][:10]
        embed.add_field(name="Roles", value=", ".join(roles) or "Ninguno", inline=False)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="serverinfo", description="Ver información del servidor")
    async def serverinfo(self, interaction: discord.Interaction):
        g = interaction.guild

        # Conteo de miembros humanos vs bots
        bots = sum(1 for m in g.members if m.bot)
        humans = g.member_count - bots

        # Desglose de canales
        text_channels = len(g.text_channels)
        voice_channels = len(g.voice_channels)
        categories = len(g.categories)

        # Emojis y stickers
        animated_emojis = sum(1 for e in g.emojis if e.animated)
        static_emojis = len(g.emojis) - animated_emojis
        stickers = len(g.stickers)

        embed = discord.Embed(title=f"📋 {g.name}", color=COLOR_MOD)
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)

        embed.add_field(
            name="📋 Información general",
            value=(
                f"**ID:** {g.id}\n"
                f"**Dueño:** {g.owner.mention if g.owner else 'N/A'}\n"
                f"**Creado:** <t:{int(g.created_at.timestamp())}:R>"
            ),
            inline=False,
        )
        embed.add_field(
            name="👥 Miembros",
            value=(
                f"**Humanos:** {humans}\n"
                f"**Bots:** {bots}\n"
                f"**Totales:** {g.member_count}"
            ),
            inline=True,
        )
        embed.add_field(
            name="🔧 Canales",
            value=(
                f"**Texto:** {text_channels}\n"
                f"**Voz:** {voice_channels}\n"
                f"**Categorías:** {categories}\n"
                f"**Totales:** {len(g.channels)}"
            ),
            inline=True,
        )
        embed.add_field(
            name="🏷️ Emojis y stickers",
            value=(
                f"**Animados:** {animated_emojis}\n"
                f"**Estáticos:** {static_emojis}\n"
                f"**Stickers:** {stickers}\n"
                f"**Totales:** {len(g.emojis) + stickers}"
            ),
            inline=True,
        )
        embed.add_field(
            name="💎 Nitro y boosts",
            value=(
                f"**Nivel:** {g.premium_tier}\n"
                f"**Boosts:** {g.premium_subscription_count}"
            ),
            inline=True,
        )
        embed.add_field(
            name="🔒 Extra",
            value=f"**Roles:** {len(g.roles)}",
            inline=True,
        )

        embed.set_footer(
            text=f"Solicitado por {interaction.user.display_name}",
            icon_url=interaction.user.display_avatar.url,
        )
        embed.timestamp = discord.utils.utcnow()

        await interaction.response.send_message(embed=embed)

    # ── Lockdown ──────────────────────────────────────────────────────────────

    @app_commands.command(
        name="lockdown",
        description="🚨 Bloquea o desbloquea TODOS los canales de texto (modo pánico ante un raid)",
    )
    @checks.is_mod()
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    async def lockdown(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        state = await db.get_lockdown_state(guild.id)

        if state:
            await self._lockdown_unlock(guild, state)
            embed = discord.Embed(
                title="🔓 Lockdown desactivado",
                description=(
                    f"Se restauraron **{len(state['channel_ids'])} canales**.\n"
                    f"Desactivado por {interaction.user.mention}."
                ),
                color=COLOR_SUCCESS,
            )
            await interaction.followup.send(embed=embed)
            return

        channel_ids, previous_verification = await self._lockdown_lock(guild)
        await db.start_lockdown(guild.id, channel_ids, previous_verification, interaction.user.id)

        embed = discord.Embed(
            title="🔒 SERVIDOR EN LOCKDOWN",
            description=(
                f"Se bloquearon **{len(channel_ids)} canales de texto** "
                f"(nadie sin permisos de mod puede escribir).\n"
                f"Activado por {interaction.user.mention}.\n\n"
                "Volvé a usar `/lockdown` o tocá el botón de abajo para desactivarlo."
            ),
            color=COLOR_ERROR,
        )
        if not channel_ids:
            embed.add_field(
                name="⚠️ Nota",
                value="No se bloqueó ningún canal nuevo (ya estaban todos bloqueados, "
                      "o no tengo permisos en ninguno).",
                inline=False,
            )
        view = LockdownUnlockView()
        await interaction.followup.send(embed=embed, view=view)

    async def _lockdown_lock(self, guild: discord.Guild) -> tuple[list[int], int]:
        """Bloquea send_messages para @everyone en todos los canales de texto
        que no estuvieran ya bloqueados. Devuelve (ids bloqueados, nivel de
        verificación previo del servidor)."""
        locked_ids = []
        for channel in guild.text_channels:
            overwrite = channel.overwrites_for(guild.default_role)
            if overwrite.send_messages is False:
                continue  # ya estaba bloqueado — no lo tocamos ni lo restauramos después
            overwrite.send_messages = False
            try:
                await channel.set_permissions(
                    guild.default_role, overwrite=overwrite, reason="Lockdown de servidor"
                )
                locked_ids.append(channel.id)
            except (discord.Forbidden, discord.HTTPException):
                continue

        previous_verification = guild.verification_level.value
        try:
            if guild.verification_level.value < discord.VerificationLevel.high.value:
                await guild.edit(
                    verification_level=discord.VerificationLevel.high,
                    reason="Lockdown de servidor",
                )
        except (discord.Forbidden, discord.HTTPException):
            pass  # el bot puede no tener manage_guild — el bloqueo de canales sigue aplicando

        return locked_ids, previous_verification

    async def _lockdown_unlock(self, guild: discord.Guild, state: dict) -> None:
        """Revierte exactamente lo que tocó el lockdown: solo los canales
        registrados en el estado, y el nivel de verificación anterior."""
        for channel_id in state["channel_ids"]:
            channel = guild.get_channel(channel_id)
            if channel is None:
                continue
            overwrite = channel.overwrites_for(guild.default_role)
            overwrite.send_messages = None
            try:
                if overwrite.is_empty():
                    await channel.set_permissions(
                        guild.default_role, overwrite=None, reason="Fin del lockdown"
                    )
                else:
                    await channel.set_permissions(
                        guild.default_role, overwrite=overwrite, reason="Fin del lockdown"
                    )
            except (discord.Forbidden, discord.HTTPException):
                continue

        try:
            await guild.edit(
                verification_level=discord.VerificationLevel(state["previous_verification"]),
                reason="Fin del lockdown",
            )
        except (discord.Forbidden, discord.HTTPException, ValueError):
            pass

        await db.end_lockdown(guild.id)

    # ── Manejo de errores de permisos del bot ───────────────────────────────

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.BotMissingPermissions):
            perms = ", ".join(error.missing_permissions)
            msg = f"❌ Me faltan permisos para ejecutar esto: `{perms}`."
        elif isinstance(error, app_commands.MissingPermissions):
            msg = "❌ No tienes permisos suficientes para usar este comando."
        else:
            msg = f"❌ Ocurrió un error inesperado: {error}"

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))

# ─────────────────────────────────────────────────────────────────────────
# CHANGELOG
# ─────────────────────────────────────────────────────────────────────────
# Correcciones de robustez (ronda 1):
# 1. _hierarchy_error(): valida en un solo lugar auto-moderación,
#    moderación al dueño del servidor, moderación al propio bot y
#    jerarquía de roles staff/bot. Aplicada en ban, kick, timeout y mute.
# 2. Orden de operaciones: la API de Discord se llama ANTES de crear el
#    caso en la base de datos, para no dejar casos huérfanos si falla.
# 3. Try/except específicos (discord.Forbidden, discord.HTTPException)
#    alrededor de cada llamada a la API de Discord.
# 4. bot_has_permissions + cog_app_command_error para errores de permisos
#    legibles.
# 5. delete_message_days -> delete_message_seconds.
# 6. warn(): bloqueada auto-advertencia y advertencia al bot; umbral de
#    alerta configurable por servidor (warning_threshold).
# 7. unban(): excepciones específicas (ValueError / NotFound / HTTPException).
# 8. purge(): mensajes de error más claros (sin resultados, límite de 14 días).
# 9. clearwarn(): informa si el ID de advertencia no existe (requiere que
#    db.clear_warning devuelva un valor "truthy" en éxito — ajustar
#    database.py si hoy no lo hace).
# 10. config_cmd(): valida umbral positivo y prohíbe @everyone como mute_rol.
#
# Plantillas de log / DM (ronda 2):
# 11. build_log_embed() / build_dm_embed(): centralizan las plantillas
#     exactas proporcionadas para cada tipo de sanción, tanto para el
#     canal de mod-log como para el DM al usuario.
# 12. Se añadió notificación por DM que antes no existía en /mute, /unmute,
#     /untimeout y /unban, siguiendo las plantillas dadas.
# 13. SERVER_NAME e INVITE_LINK son constantes editables al inicio del
#     archivo — actualízalas si cambia el nombre del servidor o el link
#     de invitación permanente.
# 14. En /kick, el DM se envía ANTES de expulsar al usuario (una vez fuera
#     del servidor, el DM podría no entregarse igual, pero es más
#     consistente notificar mientras el usuario todavía es miembro).
# ─────────────────────────────────────────────────────────────────────────