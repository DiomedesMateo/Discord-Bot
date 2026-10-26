"""
Cog: Suggestions
- /suggest new       — submit a suggestion (modal)
- /suggest approve   — approve with response
- /suggest deny      — deny with response
- /suggest implement — mark as implemented
- /suggest setup     — set suggestion channel
- /suggest list      — list pending suggestions
- Voting with 👍/👎 buttons on suggestion embed
- Cada sugerencia nueva abre automáticamente un hilo para discutirla

"""

import logging

import discord
from discord import app_commands
from discord.ext import commands
from database import db
from utils import checks
from config import COLOR_SUCCESS, COLOR_ERROR


logger = logging.getLogger(__name__)

SUGGEST_COLOR          = 0x5865F2
SUGGEST_APPROVED_COLOR = 0x57F287
SUGGEST_DENIED_COLOR   = 0xED4245


def _suggestion_embed(
    content: str,
    author: discord.abc.User,
    suggestion_id: int,
    upvotes: int = 0,
    downvotes: int = 0,
    status: str = "pending",
    response: str = None,
) -> discord.Embed:
    color_map = {
        "pending":     SUGGEST_COLOR,
        "approved":    SUGGEST_APPROVED_COLOR,
        "denied":      SUGGEST_DENIED_COLOR,
        "implemented": 0xFFD700,
    }
    status_labels = {
        "pending":     "⏳ Pendiente",
        "approved":    "✅ Aprobada",
        "denied":      "❌ Denegada",
        "implemented": "🚀 Implementada",
    }
    total = upvotes + downvotes
    ratio = f"{upvotes / total * 100:.0f}%" if total > 0 else "N/A"

    embed = discord.Embed(
        title=f"💡 Sugerencia #{suggestion_id}",
        description=content,
        color=color_map.get(status, SUGGEST_COLOR),
    )
    embed.set_author(name=str(author), icon_url=author.display_avatar.url)
    embed.add_field(name="👍 A favor",     value=str(upvotes),  inline=True)
    embed.add_field(name="👎 En contra",   value=str(downvotes), inline=True)
    embed.add_field(name="📊 Aprobación",  value=ratio,          inline=True)
    embed.add_field(name="Estado",         value=status_labels.get(status, status), inline=True)
    if response:
        embed.add_field(name="Respuesta del staff", value=response, inline=False)
    embed.set_footer(text=f"ID: {suggestion_id}")
    return embed


async def _resolve_author(
    guild: discord.Guild,
    bot: commands.Bot,
    user_id: int,
    fallback: discord.abc.User,
) -> discord.abc.User:
    """
    FIX 4: resuelve el autor real de una sugerencia sin usar por error al
    votante/moderador actual como fallback incorrecto.
    Orden: get_member -> fetch_member -> fetch_user -> fallback.
    """
    author = guild.get_member(user_id)
    if author:
        return author
    try:
        return await guild.fetch_member(user_id)
    except (discord.NotFound, discord.HTTPException):
        pass
    try:
        return await bot.fetch_user(user_id)
    except Exception:
        logger.exception("No se pudo resolver el usuario %s ni como miembro ni globalmente", user_id)
        return fallback


# ── FIX 3: helper compartido para upvote y downvote ──────────────────────────
# FIX 2b: ya no recibe suggestion_id de la vista; lo resuelve por message_id.

async def _apply_vote(
    interaction: discord.Interaction,
    value: int,          # 1 = upvote, -1 = downvote
    label: str,          # "👍 Voto registrado." o "👎 Voto registrado."
):
    suggestion = await db.fetchone(
        "SELECT * FROM suggestions WHERE message_id=?", (interaction.message.id,)
    )
    if not suggestion:
        return await interaction.response.send_message(
            "❌ Sugerencia no encontrada.", ephemeral=True
        )

    suggestion_id = suggestion["id"]
    up, dn = await db.vote_suggestion(suggestion_id, interaction.user.id, value)

    author = await _resolve_author(
        interaction.guild, interaction.client, suggestion["user_id"], interaction.user
    )

    embed = _suggestion_embed(
        suggestion["content"],
        author,
        suggestion_id,
        up,
        dn,
        suggestion["status"],
    )
    await interaction.message.edit(embed=embed)
    await interaction.response.send_message(label, ephemeral=True)

    # Logros: "Idea con Peso" (mejor sugerencia) y "Mente Influyente" (total).
    achievements_cog = interaction.client.get_cog("Logros")
    if achievements_cog and up > 0:
        stats = await db.get_user_suggestion_stats(interaction.guild_id, suggestion["user_id"])
        await db.set_suggestion_progress(interaction.guild_id, suggestion["user_id"], stats["best"], stats["total"])
        author_member = interaction.guild.get_member(suggestion["user_id"])
        if author_member:
            await achievements_cog.check_progress(author_member, "suggestion_best", stats["best"])
            await achievements_cog.check_progress(author_member, "suggestion_total", stats["total"])


class SuggestionVoteView(discord.ui.View):
    """
    Vista persistente (timeout=None). No depende de suggestion_id: la
    sugerencia se resuelve dentro de _apply_vote a partir del message_id
    del mensaje al que está adjunto el botón. Esto permite registrar una
    única instancia dummy en setup() que sirve para todos los mensajes
    existentes y futuros, incluso tras reinicios del bot.
    """

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        emoji="👍",
        label="A favor",
        style=discord.ButtonStyle.success,
        custom_id="suggest_upvote",
    )
    async def upvote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _apply_vote(interaction, 1, "👍 Voto registrado.")

    @discord.ui.button(
        emoji="👎",
        label="En contra",
        style=discord.ButtonStyle.danger,
        custom_id="suggest_downvote",
    )
    async def downvote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _apply_vote(interaction, -1, "👎 Voto registrado.")


class SuggestionModal(discord.ui.Modal, title="Enviar sugerencia"):
    content = discord.ui.TextInput(
        label="Tu sugerencia",
        style=discord.TextStyle.paragraph,
        placeholder="Describe tu sugerencia con el máximo detalle posible…",
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        # FIX 1: send_message() es la única respuesta válida para un modal submit.
        # defer() lanzaba HTTPException 400 silencioso en discord.py 2.x.
        await interaction.response.send_message("⏳ Enviando tu sugerencia…", ephemeral=True)

        config = await db.get_guild_config(interaction.guild_id)
        channel_id = config.get("suggestion_channel")
        if not channel_id:
            return await interaction.edit_original_response(
                content="❌ El canal de sugerencias no está configurado. Pide a un admin que use `/suggest setup`."
            )

        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            return await interaction.edit_original_response(
                content="❌ Canal de sugerencias no encontrado."
            )

        suggestion_id = await db.create_suggestion(
            interaction.guild_id, channel_id, interaction.user.id, self.content.value
        )

        embed = _suggestion_embed(self.content.value, interaction.user, suggestion_id)
        msg = await channel.send(embed=embed, view=SuggestionVoteView())
        await db.set_suggestion_message(suggestion_id, msg.id)

        # NUEVO: abrir un hilo público en el mensaje de la sugerencia para
        # que la discusión quede organizada. No es crítico: si falla
        # (permisos, tipo de canal que no soporta hilos, etc.) solo se
        # loguea y la sugerencia se da por creada igualmente.
        try:
            thread_name = f"💡 Sugerencia #{suggestion_id}"
            if len(thread_name) > 100:
                thread_name = thread_name[:100]
            await msg.create_thread(
                name=thread_name,
                auto_archive_duration=1440,  # 24h
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(
                "No se pudo crear el hilo para la sugerencia #%s en canal %s: %s",
                suggestion_id, channel_id, e,
            )

        await interaction.edit_original_response(
            content=f"✅ Tu sugerencia **#{suggestion_id}** ha sido enviada a {channel.mention}."
        )


class SuggestionsCog(commands.Cog, name="Suggestions"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    suggest_group = app_commands.Group(
        name="suggest", description="Sistema de sugerencias"
    )

    @suggest_group.command(name="new", description="Enviar una nueva sugerencia")
    async def suggest_new(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SuggestionModal())

    @suggest_group.command(name="setup", description="Configurar canal de sugerencias")
    @app_commands.describe(canal="Canal donde se enviarán las sugerencias")
    @checks.is_owner()
    async def suggest_setup(
        self, interaction: discord.Interaction, canal: discord.TextChannel
    ):
        await db.set_guild_config(interaction.guild_id, suggestion_channel=canal.id)
        await interaction.response.send_message(
            f"✅ Canal de sugerencias configurado: {canal.mention}", ephemeral=True
        )

    @suggest_group.command(name="approve", description="Aprobar una sugerencia")
    @app_commands.describe(id="ID de la sugerencia", respuesta="Respuesta del staff")
    @checks.is_owner()
    async def suggest_approve(
        self, interaction: discord.Interaction, id: int, respuesta: str = ""
    ):
        await self._respond_suggestion(interaction, id, "approved", respuesta)

    @suggest_group.command(name="deny", description="Denegar una sugerencia")
    @app_commands.describe(id="ID de la sugerencia", respuesta="Motivo del rechazo")
    @checks.is_owner()
    async def suggest_deny(
        self, interaction: discord.Interaction, id: int, respuesta: str = ""
    ):
        await self._respond_suggestion(interaction, id, "denied", respuesta)

    @suggest_group.command(
        name="implement", description="Marcar sugerencia como implementada"
    )
    @app_commands.describe(
        id="ID de la sugerencia", respuesta="Detalles de implementación"
    )
    @checks.is_owner()
    async def suggest_implement(
        self, interaction: discord.Interaction, id: int, respuesta: str = ""
    ):
        await self._respond_suggestion(interaction, id, "implemented", respuesta)

    async def _respond_suggestion(
        self,
        interaction: discord.Interaction,
        suggestion_id: int,
        status: str,
        response: str,
    ):
        await interaction.response.defer(ephemeral=True)
        suggestion = await db.fetchone(
            "SELECT * FROM suggestions WHERE id=? AND guild_id=?",
            (suggestion_id, interaction.guild_id),
        )
        if not suggestion:
            return await interaction.followup.send("❌ Sugerencia no encontrada.")

        await db.update_suggestion_status(
            suggestion_id, status, interaction.user.id, response
        )

        # Actualizar el mensaje original
        try:
            ch = interaction.guild.get_channel(suggestion["channel_id"])
            if ch and suggestion.get("message_id"):
                msg = await ch.fetch_message(suggestion["message_id"])
                author = await _resolve_author(
                    interaction.guild, self.bot, suggestion["user_id"], interaction.user
                )
                embed = _suggestion_embed(
                    suggestion["content"],
                    author,
                    suggestion_id,
                    suggestion["upvotes"],
                    suggestion["downvotes"],
                    status,
                    response or None,
                )
                await msg.edit(embed=embed, view=None)
        except Exception:
            # FIX 5: loguear en vez de tragarse el error silenciosamente.
            logger.exception(
                "No se pudo actualizar el mensaje de la sugerencia #%s", suggestion_id
            )

        # DM al autor
        author = interaction.guild.get_member(suggestion["user_id"])
        if not author:
            try:
                author = await self.bot.fetch_user(suggestion["user_id"])
            except Exception:
                logger.exception(
                    "No se pudo obtener el usuario %s para enviar DM", suggestion["user_id"]
                )
                author = None

        if author:
            status_labels = {
                "approved":    "✅ Aprobada",
                "denied":      "❌ Denegada",
                "implemented": "🚀 Implementada",
            }
            try:
                await author.send(
                    embed=discord.Embed(
                        title=f"💡 Tu sugerencia fue {status_labels.get(status, status)}",
                        description=(
                            f"**Sugerencia:** {suggestion['content'][:300]}\n"
                            f"**Respuesta:** {response or 'Sin respuesta'}"
                        ),
                        color=COLOR_SUCCESS if status == "approved" else COLOR_ERROR,
                    )
                )
            except Exception:
                # Es normal que falle si el usuario tiene los DMs cerrados;
                # no es un error a resaltar, pero sí queda registrado en debug.
                logger.debug(
                    "No se pudo enviar DM al autor %s de la sugerencia #%s",
                    suggestion["user_id"], suggestion_id,
                )

        await interaction.followup.send(
            f"✅ Sugerencia **#{suggestion_id}** marcada como **{status}**."
        )

    @suggest_group.command(name="list", description="Ver sugerencias pendientes")
    @checks.is_staff()
    async def suggest_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        rows = await db.fetchall(
            "SELECT * FROM suggestions WHERE guild_id=? AND status='pending' ORDER BY upvotes DESC LIMIT 10",
            (interaction.guild_id,),
        )
        if not rows:
            return await interaction.followup.send("No hay sugerencias pendientes.")
        embed = discord.Embed(title="💡 Sugerencias pendientes", color=SUGGEST_COLOR)
        for r in rows:
            embed.add_field(
                name=f"#{r['id']} — 👍{r['upvotes']} 👎{r['downvotes']}",
                value=r["content"][:100] + ("…" if len(r["content"]) > 100 else ""),
                inline=False,
            )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(SuggestionsCog(bot))
    # FIX 2 / FIX 2b: re-registrar la View persistente al arrancar el bot.
    # Una sola instancia sirve para TODOS los mensajes de sugerencias
    # (viejos y nuevos) porque _apply_vote resuelve la sugerencia por
    # message_id en vez de por un suggestion_id fijo en la vista.
    bot.add_view(SuggestionVoteView())