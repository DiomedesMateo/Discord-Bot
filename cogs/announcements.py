"""
Cog: Announcements, Polls & Reminders
- /poll create      — multi-option poll with buttons
- /poll native      — native Discord Poll UI (discord.Poll)
- /poll end         — end a poll and show results
- /poll results     — view poll results
- /reminder set     — set a personal reminder
- /reminder list    — list your reminders
- /scheduled add    — schedule a message to a channel
- Background tasks handle reminders, scheduled messages and poll expiry

FIXES aplicados:
  [FIX 1] reminder_loop y scheduled_loop usaban bot.get_channel() sin
          verificar que el canal pertenece al guild correcto. get_channel()
          busca en TODOS los servidores del bot, así que un channel_id
          podía resolverse en un canal de otro servidor si el bot coincidía.
          Ahora se verifica ch.guild.id == r["guild_id"] antes de enviar.

  [FIX 2] reminder_loop marcaba el recordatorio como enviado DESPUÉS de
          intentar el envío. Si el bot se caía a medias, el recordatorio
          quedaba sin marcar y se reenviaba en el siguiente ciclo. Ahora
          se marca ANTES del envío — si el envío falla, el recordatorio
          no se reintenta (better safe than duplicate).

  [FIX 3] Race condition en votos de encuestas: dos usuarios votando
          simultáneamente podían leer los resultados antes de que el otro
          editara el mensaje, y el segundo edit sobreescribía el del
          primero con datos desactualizados. Se añade un asyncio.Lock
          por poll_id para serializar la secuencia votar → leer → editar.

  [FIX 4] PollView no se re-registraba al reiniciar el bot. Los botones
          de encuestas activas dejaban de responder. Se añade registro
          de Views activas en el loop de arranque (before_loops).
"""

import asyncio
import json
import discord
from discord import app_commands
from discord.ext import commands
from discord.ext import tasks
from datetime import datetime, timedelta
from database import db
from utils import helpers, checks
from config import COLOR_PRIMARY, COLOR_INFO, COLOR_SUCCESS, COLOR_WARNING, COLOR_ERROR

COLOR_MINEORBIT = 0x00FFFF

# FIX 3: locks por poll_id para serializar la secuencia votar→leer→editar
_poll_locks: dict[int, asyncio.Lock] = {}

def _get_poll_lock(poll_id: int) -> asyncio.Lock:
    if poll_id not in _poll_locks:
        _poll_locks[poll_id] = asyncio.Lock()
    return _poll_locks[poll_id]


# ── Poll helpers ──────────────────────────────────────────────────────────────

def _progress_bar(value: int, total: int, length: int = 10) -> str:
    filled = round((value / total) * length) if total > 0 else 0
    return "█" * filled + "░" * (length - filled)


def _poll_embed(poll: dict, votes: list[int]) -> discord.Embed:
    options = json.loads(poll["options"])
    total   = sum(votes)

    footer_parts = [f"ID: {poll['id']}", f"{total} votos totales"]
    if poll.get("ended"):
        footer_parts.append("Encuesta cerrada")

    embed = discord.Embed(
        title=f"📊 {poll['question']}",
        description=f"-# {' · '.join(footer_parts)}",
        color=COLOR_INFO,
    )

    letters = ["🇦", "🇧", "🇨", "🇩", "🇪", "🇫", "🇬", "🇭"]
    for i, (opt, cnt) in enumerate(zip(options, votes)):
        pct = f"{cnt / total * 100:.1f}%" if total > 0 else "0%"
        bar = _progress_bar(cnt, total or 1, 10)
        embed.add_field(
            name=f"{letters[i]} {opt}  ·  {pct}",
            value=f"{bar}  {cnt} votos",
            inline=False,
        )

    if poll.get("end_time") and not poll.get("ended"):
        end_unix = int(datetime.fromisoformat(poll["end_time"]).timestamp())
        embed.add_field(name="⏰ Finaliza", value=f"<t:{end_unix}:R>", inline=True)

    return embed


class PollView(discord.ui.View):
    def __init__(self, poll_id: int, options: list[str]):
        super().__init__(timeout=None)
        letters = ["🇦", "🇧", "🇨", "🇩", "🇪", "🇫", "🇬", "🇭"]
        for i, opt in enumerate(options[:8]):
            btn = discord.ui.Button(
                label=f"{letters[i]} {opt[:50]}",
                style=discord.ButtonStyle.secondary,
                custom_id=f"poll_{poll_id}_opt_{i}",
                row=i // 4,
            )
            btn.callback = self._make_callback(poll_id, i)
            self.add_item(btn)

    def _make_callback(self, poll_id: int, option_index: int):
        async def callback(interaction: discord.Interaction):
            poll = await db.get_poll(poll_id)
            if not poll or poll["ended"]:
                return await interaction.response.send_message(
                    "Esta encuesta ya ha finalizado.", ephemeral=True
                )

            # FIX 3: lock por poll_id — serializa votar → leer → editar
            async with _get_poll_lock(poll_id):
                is_new = await db.vote_poll(poll_id, interaction.user.id, option_index)
                votes  = await db.get_poll_results(poll_id)
                embed  = _poll_embed(poll, votes)
                await interaction.message.edit(embed=embed)

            action = "registrado" if is_new else "cambiado"
            await interaction.response.send_message(
                f"✅ Voto {action} a la opción {option_index + 1}.", ephemeral=True
            )
        return callback


# ── Cog ───────────────────────────────────────────────────────────────────────

class AnnouncementsCog(commands.Cog, name="Announcements"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reminder_loop.start()
        self.scheduled_loop.start()
        self.poll_end_loop.start()

    def cog_unload(self):
        self.reminder_loop.cancel()
        self.scheduled_loop.cancel()
        self.poll_end_loop.cancel()

    # ── Poll ──────────────────────────────────────────────────────────────────

    poll_group = app_commands.Group(name="poll", description="Sistema de encuestas")

    @poll_group.command(name="create", description="Crear una encuesta")
    @app_commands.describe(
        pregunta="Pregunta de la encuesta",
        opciones="Opciones separadas por | (ej: Sí|No|Tal vez)",
        duracion="Duración opcional (ej: 1h, 2d)",
        canal="Canal donde publicar (por defecto: aquí)",
    )
    @checks.is_mod()
    async def poll_create(
        self,
        interaction: discord.Interaction,
        pregunta: str,
        opciones: str,
        duracion: str = None,
        canal: discord.TextChannel = None,
    ):
        await interaction.response.defer(ephemeral=True)
        options = [o.strip() for o in opciones.split("|") if o.strip()]
        if len(options) < 2:
            return await interaction.followup.send(
                "❌ Necesitas al menos 2 opciones separadas por `|`."
            )
        if len(options) > 8:
            return await interaction.followup.send("❌ Máximo 8 opciones.")

        target   = canal or interaction.channel
        end_time = None
        if duracion:
            td = helpers.parse_duration(duracion)
            if td:
                end_time = (datetime.utcnow() + td).isoformat()

        poll_id = await db.create_poll(
            interaction.guild_id, target.id, interaction.user.id,
            pregunta, options, end_time,
        )
        poll  = await db.get_poll(poll_id)
        votes = [0] * len(options)
        embed = _poll_embed(poll, votes)
        view  = PollView(poll_id, options)
        msg   = await target.send(embed=embed, view=view)
        await db.set_poll_message(poll_id, msg.id)

        # FIX 4: registrar la View recién creada para que sobreviva reinicios
        self.bot.add_view(view)

        await interaction.followup.send(
            f"✅ Encuesta **#{poll_id}** publicada en {target.mention}."
        )

    @poll_group.command(name="end", description="Finalizar una encuesta")
    @app_commands.describe(id="ID de la encuesta")
    @checks.is_admin()
    async def poll_end(self, interaction: discord.Interaction, id: int):
        await interaction.response.defer(ephemeral=True)
        poll = await db.get_poll(id)
        if not poll or poll["guild_id"] != interaction.guild_id:
            return await interaction.followup.send("❌ Encuesta no encontrada.")
        if poll["ended"]:
            return await interaction.followup.send("❌ Ya finalizada.")
        await self._end_poll(poll)
        await interaction.followup.send(f"✅ Encuesta **#{id}** finalizada.")

    @poll_group.command(name="results", description="Ver resultados de una encuesta")
    @app_commands.describe(id="ID de la encuesta")
    async def poll_results(self, interaction: discord.Interaction, id: int):
        await interaction.response.defer(ephemeral=True)
        poll = await db.get_poll(id)
        if not poll or poll["guild_id"] != interaction.guild_id:
            return await interaction.followup.send("❌ Encuesta no encontrada.")
        votes = await db.get_poll_results(id)
        embed = _poll_embed(poll, votes)
        await interaction.followup.send(embed=embed)

    @poll_group.command(
        name="native", description="Crear una encuesta nativa de Discord (UI de Discord, no embed)"
    )
    @app_commands.describe(
        pregunta="Pregunta de la encuesta (máx. 300 caracteres)",
        opciones="Opciones separadas por | (ej: Sí|No|Tal vez) — máx. 10, 55 caracteres c/u",
        duracion_horas="Duración en horas (1-768, es decir hasta 32 días). Por defecto: 24",
        multiple="Permitir seleccionar varias respuestas",
        canal="Canal donde publicar (por defecto: aquí)",
    )
    @checks.is_mod()
    async def poll_native(
        self,
        interaction: discord.Interaction,
        pregunta: str,
        opciones: str,
        duracion_horas: app_commands.Range[int, 1, 768] = 24,
        multiple: bool = False,
        canal: discord.TextChannel = None,
    ):
        await interaction.response.defer(ephemeral=True)
        options = [o.strip() for o in opciones.split("|") if o.strip()]
        if len(options) < 2:
            return await interaction.followup.send(
                "❌ Necesitas al menos 2 opciones separadas por `|`."
            )
        if len(options) > 10:
            return await interaction.followup.send(
                "❌ Máximo 10 opciones (límite de las Polls nativas de Discord)."
            )
        if len(pregunta) > 300:
            return await interaction.followup.send(
                "❌ La pregunta supera los 300 caracteres permitidos por Discord."
            )
        largas = [o for o in options if len(o) > 55]
        if largas:
            return await interaction.followup.send(
                f"❌ Esta opción supera los 55 caracteres permitidos: `{largas[0][:60]}…`"
            )

        target = canal or interaction.channel

        poll = discord.Poll(
            question=pregunta,
            duration=timedelta(hours=duracion_horas),
            multiple=multiple,
        )
        for opt in options:
            poll.add_answer(text=opt)

        try:
            await target.send(poll=poll)
        except discord.HTTPException as e:
            return await interaction.followup.send(
                f"❌ Discord rechazó la encuesta: {e}"
            )

        await interaction.followup.send(
            f"✅ Encuesta nativa publicada en {target.mention}.\n"
            f"-# Nota: Discord gestiona los votos internamente; no se guarda en la base de datos del bot ni se puede editar/cerrar manualmente desde aquí."
        )

    async def _end_poll(self, poll: dict):
        await db.end_poll(poll["id"])
        # Limpiar el lock de este poll ya que no se necesitará más
        _poll_locks.pop(poll["id"], None)
        try:
            ch = self.bot.get_channel(poll["channel_id"])
            if ch and poll.get("message_id"):
                msg     = await ch.fetch_message(poll["message_id"])
                votes   = await db.get_poll_results(poll["id"])
                options = json.loads(poll["options"])
                max_votes = max(votes) if votes else 0
                winners   = [options[i] for i, v in enumerate(votes) if v == max_votes]
                embed     = _poll_embed(poll, votes)
                embed.title = f"📊 [CERRADA] {poll['question']}"
                if max_votes > 0:
                    embed.add_field(
                        name="🏆 Ganador(es)",
                        value=" | ".join(winners),
                        inline=False,
                    )
                await msg.edit(embed=embed, view=None)
                await ch.send(embed=discord.Embed(
                    title=f"📊 Encuesta #{poll['id']} finalizada",
                    description=(
                        f"**Pregunta:** {poll['question']}\n"
                        f"**Ganador(es):** {' | '.join(winners) if max_votes > 0 else 'Sin votos'}"
                    ),
                    color=COLOR_SUCCESS,
                ))
        except Exception as e:
            print(f"[Poll end error] {e}")

    # ── Reminders ─────────────────────────────────────────────────────────────

    reminder_group = app_commands.Group(
        name="reminder", description="Sistema de recordatorios"
    )

    @reminder_group.command(name="set", description="Establecer un recordatorio")
    @app_commands.describe(
        duracion="En cuánto tiempo (ej: 10m, 2h, 1d)",
        mensaje="Qué recordar",
    )
    async def reminder_set(
        self, interaction: discord.Interaction, duracion: str, mensaje: str
    ):
        await interaction.response.defer(ephemeral=True)
        td = helpers.parse_duration(duracion)
        if not td:
            return await interaction.followup.send(
                "❌ Duración inválida. Usa `10m`, `2h`, `1d`…"
            )
        remind_at = (datetime.utcnow() + td).isoformat()
        rid = await db.create_reminder(
            interaction.guild_id, interaction.user.id,
            interaction.channel_id, mensaje, remind_at,
        )
        end_unix = int((datetime.utcnow() + td).timestamp())
        await interaction.followup.send(
            f"✅ Recordatorio **#{rid}** establecido para <t:{end_unix}:F>.",
            ephemeral=True,
        )

    @reminder_group.command(
        name="list", description="Ver tus recordatorios pendientes"
    )
    async def reminder_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        reminders = await db.get_user_reminders(interaction.user.id)
        if not reminders:
            return await interaction.followup.send(
                "No tienes recordatorios pendientes."
            )
        embed = discord.Embed(title="🔔 Tus recordatorios", color=COLOR_INFO)
        for r in reminders[:10]:
            remind_unix = int(datetime.fromisoformat(r["remind_at"]).timestamp())
            embed.add_field(
                name=f"#{r['id']} — <t:{remind_unix}:F>",
                value=helpers.truncate(r["content"], 100),
                inline=False,
            )
        await interaction.followup.send(embed=embed)

    # ── Scheduled messages ────────────────────────────────────────────────────

    scheduled_group = app_commands.Group(
        name="scheduled", description="Mensajes programados"
    )

    @scheduled_group.command(
        name="add", description="Programar un mensaje para un canal"
    )
    @app_commands.describe(
        canal="Canal destino",
        cuando="Cuándo enviar (ej: 2h, 1d)",
        mensaje="Contenido del mensaje",
        repetir_cada="Repetir cada X segundos (0 = no repetir)",
    )
    @checks.is_mod()
    async def scheduled_add(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
        cuando: str,
        mensaje: str,
        repetir_cada: int = 0,
    ):
        await interaction.response.defer(ephemeral=True)
        td = helpers.parse_duration(cuando)
        if not td:
            return await interaction.followup.send("❌ Tiempo inválido.")
        send_at = (datetime.utcnow() + td).isoformat()
        repeat  = repetir_cada if repetir_cada > 0 else None
        await db.execute(
            "INSERT INTO scheduled_messages (guild_id, channel_id, content, send_at, repeat_interval) VALUES (?,?,?,?,?)",
            (interaction.guild_id, canal.id, mensaje, send_at, repeat),
        )
        send_unix = int((datetime.utcnow() + td).timestamp())
        await interaction.followup.send(
            f"✅ Mensaje programado para {canal.mention} a las <t:{send_unix}:F>."
        )

    # ── Background loops ──────────────────────────────────────────────────────

    @tasks.loop(seconds=30)
    async def reminder_loop(self):
        try:
            pending = await db.get_pending_reminders()
            for r in pending:
                # FIX 2: marcar ANTES de enviar para evitar reenvíos si el
                # bot se cae entre el envío y el mark. Un recordatorio que
                # falla al enviarse no se reintenta — mejor que duplicarlo.
                await db.mark_reminder_sent(r["id"])

                ch = self.bot.get_channel(r["channel_id"])

                # FIX 1: verificar que el canal pertenece al guild correcto
                if not ch or not hasattr(ch, "guild") or ch.guild.id != r["guild_id"]:
                    continue

                embed = discord.Embed(
                    title="🔔 Recordatorio",
                    description=r["content"],
                    color=COLOR_WARNING,
                )
                embed.set_footer(text=f"Recordatorio #{r['id']}")
                try:
                    await ch.send(content=f"<@{r['user_id']}>", embed=embed)
                except Exception:
                    pass
        except Exception as e:
            print(f"[Reminder loop error] {e}")

    @tasks.loop(seconds=60)
    async def scheduled_loop(self):
        try:
            rows = await db.fetchall(
                "SELECT * FROM scheduled_messages WHERE sent=0 AND send_at <= datetime('now')"
            )
            for r in rows:
                ch = self.bot.get_channel(r["channel_id"])

                # FIX 1: verificar que el canal pertenece al guild correcto
                if ch and hasattr(ch, "guild") and ch.guild.id == r["guild_id"]:
                    try:
                        await ch.send(r["content"])
                    except Exception:
                        pass

                if r.get("repeat_interval"):
                    next_send = (
                        datetime.utcnow() + timedelta(seconds=r["repeat_interval"])
                    ).isoformat()
                    await db.execute(
                        "UPDATE scheduled_messages SET send_at=? WHERE id=?",
                        (next_send, r["id"]),
                    )
                else:
                    await db.execute(
                        "UPDATE scheduled_messages SET sent=1 WHERE id=?", (r["id"],)
                    )
        except Exception as e:
            print(f"[Scheduled loop error] {e}")

    @tasks.loop(seconds=30)
    async def poll_end_loop(self):
        try:
            rows = await db.fetchall(
                "SELECT * FROM polls WHERE ended=0 AND end_time IS NOT NULL AND end_time <= datetime('now')"
            )
            for poll in rows:
                await self._end_poll(poll)
        except Exception as e:
            prin"""
            Cog: Announcements, Polls & Reminders
            - /poll create      — native Discord Poll (discord.Poll, UI nativa de Discord)
            - /reminder set     — set a personal reminder
            - /reminder list    — list your reminders
            - /scheduled add    — schedule a message to a channel
            - Background tasks handle reminders and scheduled messages

            FIXES aplicados:
              [FIX 1] reminder_loop y scheduled_loop usaban bot.get_channel() sin
                      verificar que el canal pertenece al guild correcto. get_channel()
                      busca en TODOS los servidores del bot, así que un channel_id
                      podía resolverse en un canal de otro servidor si el bot coincidía.
                      Ahora se verifica ch.guild.id == r["guild_id"] antes de enviar.

              [FIX 2] reminder_loop marcaba el recordatorio como enviado DESPUÉS de
                      intentar el envío. Si el bot se caía a medias, el recordatorio
                      quedaba sin marcar y se reenviaba en el siguiente ciclo. Ahora
                      se marca ANTES del envío — si el envío falla, el recordatorio
                      no se reintenta (better safe than duplicate).

              [Sistema de polls] Se eliminó el sistema de encuestas custom (embed +
                      botones + tabla propia en la DB) en favor del sistema nativo de
                      encuestas de Discord (discord.Poll). Discord gestiona votos,
                      cierre y resultados directamente; el bot solo la publica.
            """

            import discord
            from discord import app_commands
            from discord.ext import commands
            from discord.ext import tasks
            from datetime import datetime, timedelta
            from database import db
            from utils import helpers, checks
            from config import COLOR_PRIMARY, COLOR_INFO, COLOR_SUCCESS, COLOR_WARNING, COLOR_ERROR

            COLOR_MINEORBIT = 0x00FFFF


            # ── Cog ───────────────────────────────────────────────────────────────────────

            class AnnouncementsCog(commands.Cog, name="Announcements"):
                def __init__(self, bot: commands.Bot):
                    self.bot = bot
                    self.reminder_loop.start()
                    self.scheduled_loop.start()

                def cog_unload(self):
                    self.reminder_loop.cancel()
                    self.scheduled_loop.cancel()

                # ── Poll (sistema nativo de Discord) ────────────────────────────────────────

                poll_group = app_commands.Group(name="poll", description="Sistema de encuestas")

                @poll_group.command(name="create", description="Crear una encuesta nativa de Discord")
                @app_commands.describe(
                    pregunta="Pregunta de la encuesta (máx. 300 caracteres)",
                    opciones="Opciones separadas por | (ej: Sí|No|Tal vez) — máx. 10, 55 caracteres c/u",
                    duracion_horas="Duración en horas (1-768, hasta 32 días). Por defecto: 24",
                    multiple="Permitir seleccionar varias respuestas",
                    canal="Canal donde publicar (por defecto: aquí)",
                )
                @checks.is_mod()
                async def poll_create(
                    self,
                    interaction: discord.Interaction,
                    pregunta: str,
                    opciones: str,
                    duracion_horas: app_commands.Range[int, 1, 768] = 24,
                    multiple: bool = False,
                    canal: discord.TextChannel = None,
                ):
                    await interaction.response.defer(ephemeral=True)
                    options = [o.strip() for o in opciones.split("|") if o.strip()]
                    if len(options) < 2:
                        return await interaction.followup.send(
                            "❌ Necesitas al menos 2 opciones separadas por `|`."
                        )
                    if len(options) > 10:
                        return await interaction.followup.send(
                            "❌ Máximo 10 opciones (límite de las Polls nativas de Discord)."
                        )
                    if len(pregunta) > 300:
                        return await interaction.followup.send(
                            "❌ La pregunta supera los 300 caracteres permitidos por Discord."
                        )
                    largas = [o for o in options if len(o) > 55]
                    if largas:
                        return await interaction.followup.send(
                            f"❌ Esta opción supera los 55 caracteres permitidos: `{largas[0][:60]}…`"
                        )

                    target = canal or interaction.channel

                    poll = discord.Poll(
                        question=pregunta,
                        duration=timedelta(hours=duracion_horas),
                        multiple=multiple,
                    )
                    for opt in options:
                        poll.add_answer(text=opt)

                    try:
                        await target.send(poll=poll)
                    except discord.HTTPException as e:
                        return await interaction.followup.send(
                            f"❌ Discord rechazó la encuesta: {e}"
                        )

                    await interaction.followup.send(
                        f"✅ Encuesta publicada en {target.mention}."
                    )

                # ── Reminders ─────────────────────────────────────────────────────────────

                reminder_group = app_commands.Group(
                    name="reminder", description="Sistema de recordatorios"
                )

                @reminder_group.command(name="set", description="Establecer un recordatorio")
                @app_commands.describe(
                    duracion="En cuánto tiempo (ej: 10m, 2h, 1d)",
                    mensaje="Qué recordar",
                )
                async def reminder_set(
                    self, interaction: discord.Interaction, duracion: str, mensaje: str
                ):
                    await interaction.response.defer(ephemeral=True)
                    td = helpers.parse_duration(duracion)
                    if not td:
                        return await interaction.followup.send(
                            "❌ Duración inválida. Usa `10m`, `2h`, `1d`…"
                        )
                    remind_at = (datetime.utcnow() + td).isoformat()
                    rid = await db.create_reminder(
                        interaction.guild_id, interaction.user.id,
                        interaction.channel_id, mensaje, remind_at,
                    )
                    end_unix = int((datetime.utcnow() + td).timestamp())
                    await interaction.followup.send(
                        f"✅ Recordatorio **#{rid}** establecido para <t:{end_unix}:F>.",
                        ephemeral=True,
                    )

                @reminder_group.command(
                    name="list", description="Ver tus recordatorios pendientes"
                )
                async def reminder_list(self, interaction: discord.Interaction):
                    await interaction.response.defer(ephemeral=True)
                    reminders = await db.get_user_reminders(interaction.user.id)
                    if not reminders:
                        return await interaction.followup.send(
                            "No tienes recordatorios pendientes."
                        )
                    embed = discord.Embed(title="🔔 Tus recordatorios", color=COLOR_INFO)
                    for r in reminders[:10]:
                        remind_unix = int(datetime.fromisoformat(r["remind_at"]).timestamp())
                        embed.add_field(
                            name=f"#{r['id']} — <t:{remind_unix}:F>",
                            value=helpers.truncate(r["content"], 100),
                            inline=False,
                        )
                    await interaction.followup.send(embed=embed)

                # ── Scheduled messages ────────────────────────────────────────────────────

                scheduled_group = app_commands.Group(
                    name="scheduled", description="Mensajes programados"
                )

                @scheduled_group.command(
                    name="add", description="Programar un mensaje para un canal"
                )
                @app_commands.describe(
                    canal="Canal destino",
                    cuando="Cuándo enviar (ej: 2h, 1d)",
                    mensaje="Contenido del mensaje",
                    repetir_cada="Repetir cada X segundos (0 = no repetir)",
                )
                @checks.is_mod()
                async def scheduled_add(
                    self,
                    interaction: discord.Interaction,
                    canal: discord.TextChannel,
                    cuando: str,
                    mensaje: str,
                    repetir_cada: int = 0,
                ):
                    await interaction.response.defer(ephemeral=True)
                    td = helpers.parse_duration(cuando)
                    if not td:
                        return await interaction.followup.send("❌ Tiempo inválido.")
                    send_at = (datetime.utcnow() + td).isoformat()
                    repeat  = repetir_cada if repetir_cada > 0 else None
                    await db.execute(
                        "INSERT INTO scheduled_messages (guild_id, channel_id, content, send_at, repeat_interval) VALUES (?,?,?,?,?)",
                        (interaction.guild_id, canal.id, mensaje, send_at, repeat),
                    )
                    send_unix = int((datetime.utcnow() + td).timestamp())
                    await interaction.followup.send(
                        f"✅ Mensaje programado para {canal.mention} a las <t:{send_unix}:F>."
                    )

                # ── Background loops ──────────────────────────────────────────────────────

                @tasks.loop(seconds=30)
                async def reminder_loop(self):
                    try:
                        pending = await db.get_pending_reminders()
                        for r in pending:
                            # FIX 2: marcar ANTES de enviar para evitar reenvíos si el
                            # bot se cae entre el envío y el mark. Un recordatorio que
                            # falla al enviarse no se reintenta — mejor que duplicarlo.
                            await db.mark_reminder_sent(r["id"])

                            ch = self.bot.get_channel(r["channel_id"])

                            # FIX 1: verificar que el canal pertenece al guild correcto
                            if not ch or not hasattr(ch, "guild") or ch.guild.id != r["guild_id"]:
                                continue

                            embed = discord.Embed(
                                title="🔔 Recordatorio",
                                description=r["content"],
                                color=COLOR_WARNING,
                            )
                            embed.set_footer(text=f"Recordatorio #{r['id']}")
                            try:
                                await ch.send(content=f"<@{r['user_id']}>", embed=embed)
                            except Exception:
                                pass
                    except Exception as e:
                        print(f"[Reminder loop error] {e}")

                @tasks.loop(seconds=60)
                async def scheduled_loop(self):
                    try:
                        rows = await db.fetchall(
                            "SELECT * FROM scheduled_messages WHERE sent=0 AND send_at <= datetime('now')"
                        )
                        for r in rows:
                            ch = self.bot.get_channel(r["channel_id"])

                            # FIX 1: verificar que el canal pertenece al guild correcto
                            if ch and hasattr(ch, "guild") and ch.guild.id == r["guild_id"]:
                                try:
                                    await ch.send(r["content"])
                                except Exception:
                                    pass

                            if r.get("repeat_interval"):
                                next_send = (
                                    datetime.utcnow() + timedelta(seconds=r["repeat_interval"])
                                ).isoformat()
                                await db.execute(
                                    "UPDATE scheduled_messages SET send_at=? WHERE id=?",
                                    (next_send, r["id"]),
                                )
                            else:
                                await db.execute(
                                    "UPDATE scheduled_messages SET sent=1 WHERE id=?", (r["id"],)
                                )
                    except Exception as e:
                        print(f"[Scheduled loop error] {e}")

                @reminder_loop.before_loop
                @scheduled_loop.before_loop
                async def before_loops(self):
                    await self.bot.wait_until_ready()


            async def setup(bot: commands.Bot):
                await bot.add_cog(AnnouncementsCog(bot))
(f"[Poll end loop error] {e}")

    @reminder_loop.before_loop
    @scheduled_loop.before_loop
    @poll_end_loop.before_loop
    async def before_loops(self):
        await self.bot.wait_until_ready()

        # FIX 4: re-registrar Views de encuestas activas al arrancar
        # para que sus botones sigan funcionando tras un reinicio.
        try:
            active_polls = await db.fetchall(
                "SELECT * FROM polls WHERE ended=0"
            )
            for poll in active_polls:
                options = json.loads(poll["options"])
                self.bot.add_view(PollView(poll["id"], options))
        except Exception as e:
            print(f"[Poll view restore error] {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(AnnouncementsCog(bot))