"""
Cog: Giveaways
- /giveaway start — create a giveaway with requirements
- /giveaway end   — end early
- /giveaway reroll — re-draw winners
- /giveaway list  — list active giveaways
- /giveaway delete — cancel
- Background loop picks winners when time expires

"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone
from database import db
from utils import helpers, checks
from config import EMOJI_GIVEAWAY

COLOR_MINEORBIT = 0x00FFFF


# ─────────────────────────────────────────────────────────────────────────────
# Embed builders (templates)
# ─────────────────────────────────────────────────────────────────────────────

def _requirement_lines(giveaway: dict, guild: discord.Guild) -> str:
    """Líneas extra opcionales de requisitos, insertadas antes del separador."""
    lines = []
    if giveaway.get("required_role"):
        role = guild.get_role(giveaway["required_role"]) if guild else None
        if role:
            lines.append(f"> 🔒 **Rol requerido:** {role.mention}")
    if giveaway.get("required_min_days"):
        lines.append(f"> 📅 **Días mínimos en el servidor:** {giveaway['required_min_days']}")
    return ("\n" + "\n".join(lines)) if lines else ""


def build_active_embed(
    giveaway: dict, host: discord.abc.User, entries: int, guild: discord.Guild = None
) -> discord.Embed:
    # FIX 8: datetime.fromisoformat() sobre un string guardado con
    # datetime.utcnow() devuelve un datetime "naive" (sin tzinfo). Sin el
    # .replace(tzinfo=timezone.utc), .timestamp() asume la zona horaria
    # LOCAL del sistema donde corre el bot, no UTC — causando que el
    # countdown en Discord se desfase exactamente por el offset horario
    # del servidor (ej. "6 horas" en vez de "1 minuto").
    end_unix = int(
        datetime.fromisoformat(giveaway["end_time"]).replace(tzinfo=timezone.utc).timestamp()
    )

    description = (
        f"> :small_blue_diamond: **Hosteado por:** {host.mention}\n"
        f"> :small_blue_diamond: **Finaliza:** <t:{end_unix}:R>\n"
        f"> :trophy: **Ganadores:** {giveaway['winners_count']}\n"
        # FIX 4: \n al final de la línea de participantes para que el
        # separador quede bien cuando no hay requisitos adicionales
        f"> :tickets: **Participantes:** {entries}\n"
        f"{_requirement_lines(giveaway, guild)}\n"
        f"{'⏸️ **Sorteo pausado — el conteo está congelado y no se puede participar.**' + chr(10) if giveaway.get('paused') else ''}"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f":tada: **¡Haz clic en el botón \"Participar\" para entrar al sorteo!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f":alarm_clock: **Cuando el sorteo finalice**, los ganadores serán seleccionados automáticamente.\n"
        f":cyclone: **¡Mucha suerte a todos!**"
    )

    embed = discord.Embed(
        title=f":tada:┃{giveaway['prize']}",
        description=description,
        color=COLOR_MINEORBIT,
    )
    embed.set_footer(text=f"ID: {giveaway['id']}")
    return embed


def build_ended_embed(
    giveaway: dict, host: discord.abc.User, entries: int, ended_at: datetime = None
) -> discord.Embed:
    ended_at = ended_at or datetime.now(timezone.utc)
    ended_unix = int(ended_at.timestamp())

    description = (
        f"> :bust_in_silhouette: **Hosteado por:** {host.mention}\n"
        f"> :clock3: **Finalizó:** <t:{ended_unix}:R>\n"
        f"> :trophy: **Ganadores:** {giveaway['winners_count']}\n"
        f"> :tickets: **Participantes:** {entries}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f":alarm_clock: **Este sorteo ha finalizado.**\n"
        f":trophy: Puedes consultar la lista de ganadores utilizando el botón **\"Ver Ganadores\"**.\n"
        f":cyclone: ¡Gracias a todos por participar y mucha suerte en el próximo sorteo!"
    )

    embed = discord.Embed(
        title=f":tada:┃{giveaway['prize']}",
        description=description,
        color=COLOR_MINEORBIT,
    )
    embed.set_footer(text=f"ID: {giveaway['id']}")
    return embed


# ─────────────────────────────────────────────────────────────────────────────
# Views
# ─────────────────────────────────────────────────────────────────────────────

class GiveawayEnterView(discord.ui.View):
    """
    FIX 1 + 2: Los botones se crean dinámicamente con custom_id único por
    sorteo (f"giveaway_enter_{giveaway_id}") en lugar de un string fijo.
    Esto evita que múltiples sorteos activos se interfieran entre sí.

    FIX 5: Los callbacks asignados directamente a un discord.ui.Item deben
    aceptar SOLO `interaction` como parámetro (además de `self`, que ya
    viene bound). Asignarles una firma con `button` de más provoca un
    TypeError no capturado que Discord reporta como "This interaction
    failed" — la interacción nunca recibe respuesta a tiempo porque el
    coroutine muere antes de llamar a interaction.response.*.
    """

    def __init__(self, giveaway_id: int):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id

        # Botón "Participar" con custom_id único
        enter_btn = discord.ui.Button(
            label="Participar 🎉",
            style=discord.ButtonStyle.success,
            custom_id=f"giveaway_enter_{giveaway_id}",
        )
        enter_btn.callback = self._enter_callback
        self.add_item(enter_btn)

        # Botón "Participantes" con custom_id único
        list_btn = discord.ui.Button(
            label="Participantes 👥",
            style=discord.ButtonStyle.secondary,
            custom_id=f"giveaway_list_{giveaway_id}",
        )
        list_btn.callback = self._list_callback
        self.add_item(list_btn)

    # FIX 5: firma corregida — solo (self, interaction), sin `button`.
    async def _enter_callback(self, interaction: discord.Interaction):
        giveaway = await db.get_giveaway(self.giveaway_id)
        if not giveaway or giveaway["ended"]:
            return await interaction.response.send_message(
                "Este sorteo ya terminó.", ephemeral=True
            )
        if giveaway.get("paused"):
            return await interaction.response.send_message(
                "⏸️ Este sorteo está pausado temporalmente. Inténtalo de nuevo más tarde.",
                ephemeral=True,
            )

        # Requirement checks
        if giveaway["required_role"]:
            role = interaction.guild.get_role(giveaway["required_role"])
            if role and role not in interaction.user.roles:
                return await interaction.response.send_message(
                    f"Necesitas el rol {role.mention} para participar.", ephemeral=True
                )

        if giveaway["required_min_days"]:
            days = (datetime.now(timezone.utc) - interaction.user.joined_at).days
            if days < giveaway["required_min_days"]:
                return await interaction.response.send_message(
                    f"Necesitas al menos {giveaway['required_min_days']} días en el servidor.",
                    ephemeral=True,
                )

        added = await db.add_giveaway_entry(self.giveaway_id, interaction.user.id)

        if added:
            # FIX 3: fetch entries DESPUÉS de la operación para que el
            # contador sea siempre exacto (tanto al entrar como al salir)
            entries = await db.get_giveaway_entries(self.giveaway_id)
            await interaction.response.send_message(
                f"✅ ¡Participando! Hay **{len(entries)}** participantes.",
                ephemeral=True,
            )
        else:
            # Toggle out: primero eliminar, luego contar
            await db.remove_giveaway_entry(self.giveaway_id, interaction.user.id)
            entries = await db.get_giveaway_entries(self.giveaway_id)
            await interaction.response.send_message(
                "❌ Has abandonado el sorteo.", ephemeral=True
            )

        # Actualizar el embed con el contador correcto
        await _refresh_giveaway_embed(
            interaction.message, self.giveaway_id, len(entries)
        )

    # FIX 5: firma corregida — solo (self, interaction), sin `button`.
    async def _list_callback(self, interaction: discord.Interaction):
        entries = await db.get_giveaway_entries(self.giveaway_id)
        if not entries:
            return await interaction.response.send_message(
                "No hay participantes aún.", ephemeral=True
            )
        mentions = [f"<@{e['user_id']}>" for e in entries[:20]]
        extra = f"\n…y {len(entries) - 20} más" if len(entries) > 20 else ""
        await interaction.response.send_message(
            f"**Participantes ({len(entries)}):**\n" + ", ".join(mentions) + extra,
            ephemeral=True,
        )


class GiveawayEndedView(discord.ui.View):
    """
    Botón de enlace directo al mensaje de ganadores enviado por el bot.
    ButtonStyle.link no necesita custom_id ni registro en setup_hook:
    la URL queda guardada en el propio mensaje de Discord.
    """

    def __init__(self, winner_message_url: str):
        super().__init__(timeout=None)

        self.add_item(discord.ui.Button(
            label="Ver Ganadores 🏆",
            style=discord.ButtonStyle.link,
            url=winner_message_url,
        ))


async def _refresh_giveaway_embed(
    message: discord.Message, giveaway_id: int, entries: int
):
    try:
        giveaway = await db.get_giveaway(giveaway_id)
        if not giveaway:
            return
        guild = message.guild
        host = guild.get_member(giveaway["host_id"]) or await guild.fetch_member(
            giveaway["host_id"]
        )
        embed = build_active_embed(giveaway, host, entries, guild)
        await message.edit(embed=embed)
    except Exception as e:
        # FIX 6: loguear en vez de tragar en silencio.
        print(f"[Giveaway] No se pudo refrescar embed del sorteo #{giveaway_id}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Cog
# ─────────────────────────────────────────────────────────────────────────────

class GiveawayCog(commands.Cog, name="Giveaways"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_giveaways.start()

    def cog_unload(self):
        self.check_giveaways.cancel()

    # ── Slash group ──────────────────────────────────────────────────────────
    giveaway_group = app_commands.Group(
        name="giveaway", description="Sistema de sorteos"
    )

    @giveaway_group.command(name="start", description="Iniciar un sorteo")
    @app_commands.describe(
        canal="Canal donde se publicará el sorteo",
        duracion="Duración (ej: 1h, 30m, 2d)",
        premio="Premio del sorteo",
        ganadores="Número de ganadores",
        rol_requerido="Rol necesario para participar",
        dias_minimos="Días mínimos en el servidor",
    )
    @checks.is_admin()
    async def giveaway_start(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
        duracion: str,
        premio: str,
        ganadores: int = 1,
        rol_requerido: discord.Role = None,
        dias_minimos: int = None,
    ):
        await interaction.response.defer(ephemeral=True)
        td = helpers.parse_duration(duracion)
        if not td:
            return await interaction.followup.send(
                "❌ Duración inválida. Usa formatos como `1h`, `30m`, `2d`."
            )

        end_time = datetime.utcnow() + td
        giveaway_id = await db.create_giveaway(
            guild_id=interaction.guild_id,
            channel_id=canal.id,
            host_id=interaction.user.id,
            prize=premio,
            winners=ganadores,
            end_time=end_time.isoformat(),
            required_role=rol_requerido.id if rol_requerido else None,
            required_min_days=dias_minimos,
        )

        giveaway = await db.get_giveaway(giveaway_id)
        embed = build_active_embed(giveaway, interaction.user, 0, interaction.guild)

        view = GiveawayEnterView(giveaway_id)
        msg = await canal.send(embed=embed, view=view)
        await db.set_giveaway_message(giveaway_id, msg.id)

        await interaction.followup.send(
            f"✅ Sorteo **#{giveaway_id}** iniciado en {canal.mention}.", ephemeral=True
        )

    @giveaway_group.command(
        name="end", description="Terminar un sorteo anticipadamente"
    )
    @app_commands.describe(id="ID del sorteo")
    @checks.is_admin()
    async def giveaway_end(self, interaction: discord.Interaction, id: int):
        await interaction.response.defer(ephemeral=True)
        giveaway = await db.get_giveaway(id)
        if not giveaway or giveaway["guild_id"] != interaction.guild_id:
            return await interaction.followup.send("❌ Sorteo no encontrado.")
        if giveaway["ended"]:
            return await interaction.followup.send("❌ El sorteo ya terminó.")
        finished = await self._finish_giveaway(giveaway)
        if not finished:
            # FIX 7: otra ejecución (el loop en background) ganó la carrera
            # y ya lo estaba terminando en este mismo instante.
            return await interaction.followup.send(
                f"⚠️ Sorteo **#{id}** ya estaba siendo finalizado (probablemente por el loop automático)."
            )
        await interaction.followup.send(f"✅ Sorteo **#{id}** finalizado.")

    @giveaway_group.command(name="reroll", description="Redraw de ganadores")
    @app_commands.describe(id="ID del sorteo", ganadores="Número de ganadores a elegir")
    @checks.is_admin()
    async def giveaway_reroll(
        self, interaction: discord.Interaction, id: int, ganadores: int = 1
    ):
        await interaction.response.defer(ephemeral=True)
        giveaway = await db.get_giveaway(id)
        if not giveaway or giveaway["guild_id"] != interaction.guild_id:
            return await interaction.followup.send("❌ Sorteo no encontrado.")
        if not giveaway["ended"]:
            return await interaction.followup.send("❌ El sorteo aún no ha terminado.")

        entries = await db.get_giveaway_entries(id)
        if not entries:
            return await interaction.followup.send("❌ No hay participantes.")

        winners = helpers.pick_winners([e["user_id"] for e in entries], ganadores)
        for w in winners:
            await db.add_giveaway_winner(id, w)
        mentions = [f"<@{w}>" for w in winners]
        channel = self.bot.get_channel(giveaway["channel_id"])

        embed = discord.Embed(
            title=f":tada: Re-roll — Sorteo #{id}",
            description=(
                f"**Nuevos ganadores:** {', '.join(mentions)}\n"
                f"**Premio:** {giveaway['prize']}"
            ),
            color=COLOR_MINEORBIT,
        )
        if channel:
            await channel.send(embed=embed)
        await interaction.followup.send(
            f"✅ Re-roll hecho. Ganadores: {', '.join(mentions)}"
        )

    @giveaway_group.command(name="list", description="Ver sorteos activos")
    async def giveaway_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        giveaways = await db.get_active_giveaways(interaction.guild_id)
        if not giveaways:
            return await interaction.followup.send("No hay sorteos activos.")

        embed = discord.Embed(title="🎉 Sorteos activos", color=COLOR_MINEORBIT)
        for g in giveaways:
            # FIX 8: mismo fix de zona horaria que en build_active_embed.
            end_unix = int(
                datetime.fromisoformat(g["end_time"]).replace(tzinfo=timezone.utc).timestamp()
            )
            embed.add_field(
                name=f"#{g['id']} — {g['prize']}",
                value=f"Termina: <t:{end_unix}:R> | Ganadores: {g['winners_count']}",
                inline=False,
            )
        await interaction.followup.send(embed=embed)

    @giveaway_group.command(name="pausar", description="Pausar temporalmente un sorteo activo")
    @app_commands.describe(id="ID del sorteo")
    @checks.is_admin()
    async def giveaway_pausar(self, interaction: discord.Interaction, id: int):
        await interaction.response.defer(ephemeral=True)
        giveaway = await db.get_giveaway(id)
        if not giveaway or giveaway["guild_id"] != interaction.guild_id:
            return await interaction.followup.send("❌ Sorteo no encontrado.")
        if giveaway["ended"]:
            return await interaction.followup.send("❌ Ese sorteo ya terminó.")
        if giveaway.get("paused"):
            return await interaction.followup.send("⏸️ Ese sorteo ya estaba pausado.")

        paused = await db.pause_giveaway(id)
        if not paused:
            return await interaction.followup.send(
                "❌ No se pudo pausar el sorteo (puede que haya terminado justo ahora)."
            )

        try:
            ch = self.bot.get_channel(giveaway["channel_id"])
            if ch and giveaway["message_id"]:
                msg = await ch.fetch_message(giveaway["message_id"])
                entries = await db.get_giveaway_entries(id)
                await _refresh_giveaway_embed(msg, id, len(entries))
        except Exception as e:
            print(f"[Giveaway] No se pudo refrescar embed al pausar #{id}: {e}")

        await interaction.followup.send(f"⏸️ Sorteo **#{id}** pausado. Nadie puede participar hasta que lo reanudes con `/giveaway reanudar`.")

    @giveaway_group.command(name="reanudar", description="Reanudar un sorteo pausado")
    @app_commands.describe(id="ID del sorteo")
    @checks.is_admin()
    async def giveaway_reanudar(self, interaction: discord.Interaction, id: int):
        await interaction.response.defer(ephemeral=True)
        giveaway = await db.get_giveaway(id)
        if not giveaway or giveaway["guild_id"] != interaction.guild_id:
            return await interaction.followup.send("❌ Sorteo no encontrado.")
        if not giveaway.get("paused"):
            return await interaction.followup.send("❌ Ese sorteo no está pausado.")

        resumed = await db.resume_giveaway(id)
        if not resumed:
            return await interaction.followup.send("❌ No se pudo reanudar el sorteo.")

        try:
            ch = self.bot.get_channel(giveaway["channel_id"])
            if ch and giveaway["message_id"]:
                msg = await ch.fetch_message(giveaway["message_id"])
                entries = await db.get_giveaway_entries(id)
                await _refresh_giveaway_embed(msg, id, len(entries))
        except Exception as e:
            print(f"[Giveaway] No se pudo refrescar embed al reanudar #{id}: {e}")

        await interaction.followup.send(f"▶️ Sorteo **#{id}** reanudado. Ya se puede participar de nuevo.")

    @giveaway_group.command(name="delete", description="Cancelar un sorteo")
    @app_commands.describe(id="ID del sorteo")
    @checks.is_admin()
    async def giveaway_delete(self, interaction: discord.Interaction, id: int):
        await interaction.response.defer(ephemeral=True)
        giveaway = await db.get_giveaway(id)
        if not giveaway or giveaway["guild_id"] != interaction.guild_id:
            return await interaction.followup.send("❌ Sorteo no encontrado.")
        await db.end_giveaway(id)
        try:
            ch = self.bot.get_channel(giveaway["channel_id"])
            if ch and giveaway["message_id"]:
                msg = await ch.fetch_message(giveaway["message_id"])
                await msg.delete()
        except Exception as e:
            print(f"[Giveaway] No se pudo borrar mensaje del sorteo #{id} al cancelar: {e}")
        await interaction.followup.send(f"✅ Sorteo **#{id}** cancelado.")

    # ── Background task ───────────────────────────────────────────────────────

    @tasks.loop(seconds=15)
    async def check_giveaways(self):
        try:
            active = await db.get_all_active_giveaways()
            now = datetime.utcnow()
            for g in active:
                if g.get("paused"):
                    continue
                end = datetime.fromisoformat(g["end_time"])
                if now >= end:
                    await self._finish_giveaway(g)
        except Exception as e:
            print(f"[Giveaway loop error] {e}")

    @check_giveaways.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    async def _finish_giveaway(self, giveaway: dict) -> bool:
        """Procesa el fin de un sorteo. Devuelve False sin hacer nada si
        otra ejecución concurrente (loop vs comando manual) ya se había
        adelantado a reclamarlo — ver FIX 7 / db.end_giveaway()."""
        giveaway_id = giveaway["id"]

        # FIX 7: reclamo atómico. Si otra ejecución ya marcó ended=1 entre
        # que leímos `giveaway` y llegamos aquí, esta llamada no hace nada
        # y devolvemos False — evita doble sorteo de ganadores, doble
        # anuncio y ediciones de embed pisándose entre sí.
        claimed = await db.end_giveaway(giveaway_id)
        if not claimed:
            return False

        entries = await db.get_giveaway_entries(giveaway_id)
        channel = self.bot.get_channel(giveaway["channel_id"])
        if not channel:
            return True

        guild = self.bot.get_guild(giveaway["guild_id"])
        host = guild.get_member(giveaway["host_id"]) if guild else None

        winner_ids = helpers.pick_winners(
            [e["user_id"] for e in entries], giveaway["winners_count"]
        )
        for wid in winner_ids:
            await db.add_giveaway_winner(giveaway_id, wid)

        winner_mentions = [f"<@{w}>" for w in winner_ids]
        ended_at = datetime.now(timezone.utc)

        embed = build_ended_embed(
            giveaway,
            host or await self.bot.fetch_user(giveaway["host_id"]),
            len(entries),
            ended_at,
        )

        if winner_ids:
            msg_content = (
                f"🎉 ¡Felicidades {', '.join(winner_mentions)}! "
                f"Ganaron **{giveaway['prize']}**"
            )
        else:
            msg_content = "No hubo ganadores (sin participantes suficientes)."

        # Enviar primero el mensaje de ganadores para obtener su URL
        winner_msg = await channel.send(msg_content)

        # Editar el embed original con el botón que enlaza al mensaje de ganadores
        try:
            if giveaway["message_id"]:
                orig = await channel.fetch_message(giveaway["message_id"])
                await orig.edit(embed=embed, view=GiveawayEndedView(winner_msg.jump_url))
        except Exception as e:
            # FIX 6: loguear en vez de tragar en silencio.
            print(f"[Giveaway] No se pudo editar embed final del sorteo #{giveaway_id}: {e}")

        return True


async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawayCog(bot))


# ─────────────────────────────────────────────────────────────────────────────
# FIX 2 — Restaurar vistas al arrancar el bot
# ─────────────────────────────────────────────────────────────────────────────
# Añade esto en tu clase principal del bot (en bot.py / main.py):
#
# class MyBot(commands.Bot):
#     async def setup_hook(self):
#         await self.load_extension("cogs.giveaways")
#
#         # Re-registrar vistas de sorteos ACTIVOS para que "Participar" y
#         # "Participantes" sigan funcionando después de reiniciar el bot.
#         active = await db.get_all_active_giveaways()
#         for g in active:
#             if g.get("message_id"):
#                 self.add_view(
#                     GiveawayEnterView(g["id"]),
#                     message_id=g["message_id"]
#                 )
#
#         # GiveawayEndedView NO necesita restaurarse: usa ButtonStyle.link,
#         # cuya URL queda grabada en el propio mensaje de Discord y funciona
#         # siempre sin intervención del bot.