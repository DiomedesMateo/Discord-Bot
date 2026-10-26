"""
Cog: Tournaments — Sistema de torneos con eliminación simple para MineOrbit.

Comandos:
  /torneo crear     — Crea un torneo y abre inscripciones  (Mod)
  /torneo cancelar  — Cancela el torneo activo             (Mod)
  /torneo resultado — Registra el ganador de un partido    (Mod)
  /torneo bracket   — Muestra el bracket actual
  /torneo lista     — Historial de los últimos torneos
  /torneo info      — Información del torneo en curso

Flujo:
  1. /torneo crear → embed de inscripciones con botón "Inscribirme"
  2. Jugadores se inscriben (validación: verificado, MC vinculado, sin sanciones)
  3. Al llenarse el cupo O llegar la hora de inicio:
       → embed "El torneo ha comenzado" + bracket Ronda 1
  4. /torneo resultado <id_partido> <ganador>
       → actualiza bracket; si la ronda terminó genera la siguiente
  5. Final completada → embed campeón + rol 🏆 Campeón asignado

"""

import logging
import random
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks
from database import db
from utils import checks

logger = logging.getLogger(__name__)

COLOR_MINEORBIT = 0x00FFFF
COLOR_SUCCESS   = 0x57F287
COLOR_ERROR     = 0xED4245
COLOR_GOLD      = 0xFFD700
SERVER_NAME     = "MineOrbit"
SEP             = "━━━━━━━━━━━━━━━━━━━━━━"
MAX_SLOTS       = 100


def _mode_label(mode: str) -> str:
    return {"eliminacion_simple": "Eliminación Simple"}.get(mode, mode)


def _member_name(guild: discord.Guild, user_id: int | None) -> str:
    if not user_id:
        return "BYE"
    m = guild.get_member(user_id)
    return m.display_name if m else f"Usuario {user_id}"


# ─────────────────────────────────────────────────────────────────────────────
# Embed builders
# ─────────────────────────────────────────────────────────────────────────────

def build_inscription_embed(t: dict, registered: int) -> discord.Embed:
    start_ts   = int(datetime.fromisoformat(t["start_time"]).timestamp())
    prize_line = f"\n🎁 **Premio:** {t['prize']}" if t.get("prize") else ""
    desc = (
        f"## 🎮 {t['name']}\n"
        f"Las inscripciones para este torneo ya están disponibles.\n\n"
        f"{SEP}\n\n"
        f"📅 **Fecha de inicio:** <t:{start_ts}:F>\n"
        f"👥 **Participantes:** {registered}/{t['slots']}\n"
        f"🎯 **Modo:** {_mode_label(t['mode'])}"
        f"{prize_line}\n\n"
        f"{SEP}\n\n"
        f"## 📋 ¿CÓMO PARTICIPAR?\n"
        f"🎮 Pulsa el botón **Inscribirme** para reservar tu lugar.\n"
        f"El sistema verificará automáticamente que cumplas los requisitos necesarios.\n\n"
        f"{SEP}\n\n"
        f"⚠️ **Requisitos**\n"
        f"🔹 Estar verificado en Discord.\n"
        f"🔹 Tener tu cuenta de Minecraft vinculada.\n"
        f"🔹 No encontrarte sancionado.\n"
        f"🔹 Contar con un espacio disponible en el torneo.\n\n"
        f"{SEP}\n\n"
        f"⏳ **Las inscripciones cerrarán automáticamente cuando se complete el cupo o llegue la hora de inicio.**\n\n"
        f"🌀 ¡Demuestra quién es el mejor jugador de {SERVER_NAME}!"
    )
    embed = discord.Embed(title="🏆┃NUEVO TORNEO", description=desc, color=COLOR_MINEORBIT)
    embed.set_footer(text=f"ID del torneo: #{t['id']}")
    return embed


def build_joined_embed(t: dict, registered: int) -> discord.Embed:
    start_ts = int(datetime.fromisoformat(t["start_time"]).timestamp())
    desc = (
        f"¡Has sido inscrito correctamente en el torneo!\n\n"
        f"🏆 **Torneo:** {t['name']}\n"
        f"👥 **Participantes:** {registered}/{t['slots']}\n"
        f"⏳ **Comienza:** <t:{start_ts}:R>\n\n"
        f"🚀 ¡Mucha suerte!"
    )
    embed = discord.Embed(title="✅┃INSCRIPCIÓN COMPLETADA", description=desc, color=COLOR_SUCCESS)
    embed.set_footer(text=f"ID del torneo: #{t['id']}")
    return embed


def build_rejected_embed() -> discord.Embed:
    desc = (
        f"No cumples uno o más requisitos para participar en este torneo.\n\n"
        f"Verifica que:\n\n"
        f"• Estás correctamente verificado.\n"
        f"• Tu cuenta de Minecraft está vinculada.\n"
        f"• El torneo aún tiene plazas disponibles.\n"
        f"• No tienes restricciones para participar."
    )
    return discord.Embed(title="❌┃NO HA SIDO POSIBLE INSCRIBIRTE", description=desc, color=COLOR_ERROR)


def build_started_embed(t: dict) -> discord.Embed:
    desc = (
        f"Las inscripciones han finalizado.\n"
        f"El sistema ha generado automáticamente los enfrentamientos de la primera ronda.\n\n"
        f"🏆 **Torneo:** {t['name']}\n"
        f"👥 **Participantes:** {t['slots']}\n"
        f"🎯 **Modo:** {_mode_label(t['mode'])}\n\n"
        f"{SEP}\n\n"
        f"📢 Consulta el bracket publicado más abajo para conocer a tu rival.\n"
        f"¡Buena suerte a todos!"
    )
    embed = discord.Embed(title="⚔️┃¡EL TORNEO HA COMENZADO!", description=desc, color=COLOR_MINEORBIT)
    embed.set_footer(text=f"ID del torneo: #{t['id']}")
    return embed


def build_bracket_embed(t: dict, matches: list[dict], guild: discord.Guild) -> discord.Embed:
    rnd     = t["current_round"]
    normal  = [m for m in matches if not m.get("is_third_place")]
    third_m = next((m for m in matches if m.get("is_third_place")), None)

    def _line(m: dict) -> str:
        p1 = _member_name(guild, m["player1_id"])
        p2 = _member_name(guild, m["player2_id"]) if m.get("player2_id") else "BYE"
        if m.get("winner_id"):
            w = _member_name(guild, m["winner_id"])
            return f"✅ #{m['match_number']}  {p1}  🆚  {p2}  ›  {w}"
        return f"🎮 #{m['match_number']}  {p1}  🆚  {p2}"

    block = "```\n" + "\n\n".join(_line(m) for m in normal) + "\n```"

    third_text = ""
    if third_m:
        t3l = _line(third_m)
        third_text = f"\n🥉 **Partido por el 3.er puesto:**\n```\n{t3l}\n```\n"

    desc = (
        f"## ⚔️ RONDA {rnd}\n\n"
        f"{block}\n"
        f"{third_text}\n"
        f"{SEP}\n\n"
        f"📌 Usa el número de partido mostrado arriba (ej. #1, #2...) en `/torneo resultado`. "
        f"El partido por el 3.er puesto se registra con el número **#99**.\n"
        f"Los ganadores avanzarán automáticamente a la siguiente ronda "
        f"una vez el Staff registre los resultados."
    )
    embed = discord.Embed(title="🏆┃BRACKET OFICIAL", description=desc, color=COLOR_MINEORBIT)
    embed.set_footer(text=f"ID del torneo: #{t['id']}")
    return embed


def build_result_embed(
    winner: discord.Member,
    loser: discord.Member | None,
    match_id: int,
) -> discord.Embed:
    loser_mention = loser.mention if loser else "—"
    desc = (
        f"🏆 **Ganador:** {winner.mention}\n"
        f"❌ **Eliminado:** {loser_mention}\n"
        f"🎮 **Partido:** #{match_id}\n\n"
        f"📈 El bracket ha sido actualizado correctamente.\n\n"
        f"⏳ Espera a que finalicen los demás enfrentamientos para conocer tu próximo rival."
    )
    return discord.Embed(title="📋┃RESULTADO REGISTRADO", description=desc, color=COLOR_MINEORBIT)


def build_new_round_embed(rnd: int) -> discord.Embed:
    desc = (
        f"Los enfrentamientos de la **Ronda {rnd}** ya están disponibles.\n\n"
        f"📢 Revisa el bracket actualizado para conocer a tu próximo rival.\n\n"
        f"¡Cada victoria te acerca más al campeonato!"
    )
    return discord.Embed(title="🚀┃NUEVA RONDA", description=desc, color=COLOR_MINEORBIT)


def build_champion_embed(
    t: dict,
    champion: discord.Member | None,
    runner_up: discord.Member | None,
    third: discord.Member | None,
) -> discord.Embed:
    def _m(member): return member.mention        if member else "—"
    def _n(member): return member.display_name   if member else "—"

    desc = (
        f"Después de una increíble competición...\n\n"
        f"# 🏆 {_n(champion)}\n\n"
        f"se ha convertido en el **Campeón del torneo {t['name']}**.\n\n"
        f"{SEP}\n\n"
        f"🥇 **Primer Lugar:** {_m(champion)}\n"
        f"🥈 **Segundo Lugar:** {_m(runner_up)}\n"
        f"🥉 **Tercer Lugar:** {_m(third)}\n\n"
        f"{SEP}\n"
    )
    if t.get("prize"):
        desc += f"\n🎁 **Premio recibido**\n> {t['prize']}\n"
    desc += (
        f"\n👑 El rol **🏆 Campeón** ha sido otorgado automáticamente.\n\n"
        f"🌀 ¡Gracias a todos por participar! "
        f"Nos vemos en el próximo torneo de **{SERVER_NAME}**."
    )
    embed = discord.Embed(title="👑┃¡TENEMOS CAMPEÓN!", description=desc, color=COLOR_GOLD)
    embed.set_footer(text=f"ID del torneo: #{t['id']}")
    return embed


def build_history_embed(tournaments: list[dict], guild: discord.Guild) -> discord.Embed:
    if not tournaments:
        desc = (
            "No hay torneos finalizados aún.\n\n"
            f"🌀 ¡Sigue participando para convertirte en el próximo campeón de **{SERVER_NAME}**!"
        )
        return discord.Embed(title="📖┃HISTORIAL DE TORNEOS", description=desc, color=COLOR_MINEORBIT)

    lines = []
    for t in tournaments[:10]:
        champ = "—"
        if t.get("champion_id"):
            m = guild.get_member(t["champion_id"])
            champ = m.display_name if m else f"<@{t['champion_id']}>"
        ts = 0
        if t.get("created_at"):
            try:
                ts = int(datetime.fromisoformat(t["created_at"]).timestamp())
            except Exception:
                pass
        lines.append(f"🥇 **{t['name']}** `#{t['id']}`\n👑 Campeón: {champ}\n📅 <t:{ts}:D>")

    desc = (
        "## 🏆 Últimos torneos finalizados\n\n"
        + "\n\n".join(lines)
        + f"\n\n{SEP}\n\n"
        + f"🌀 Sigue participando para convertirte en el próximo campeón de **{SERVER_NAME}**."
    )
    return discord.Embed(title="📖┃HISTORIAL DE TORNEOS", description=desc, color=COLOR_MINEORBIT)


# ─────────────────────────────────────────────────────────────────────────────
# Persistent View — Inscription
# ─────────────────────────────────────────────────────────────────────────────

class TournamentInscriptionView(discord.ui.View):
    """
    Persistent inscription view. Register on bot startup:
      bot.add_view(TournamentInscriptionView(t["id"]), message_id=t["message_id"])
    """

    def __init__(self, tournament_id: int):
        super().__init__(timeout=None)
        self.tournament_id = tournament_id

        btn_join = discord.ui.Button(
            label="Inscribirme 🎮",
            style=discord.ButtonStyle.success,
            custom_id=f"trn_join_{tournament_id}",
        )
        btn_join.callback = self._join_cb
        self.add_item(btn_join)

        btn_list = discord.ui.Button(
            label="Participantes 👥",
            style=discord.ButtonStyle.secondary,
            custom_id=f"trn_list_{tournament_id}",
        )
        btn_list.callback = self._list_cb
        self.add_item(btn_list)

    # ── Join ──────────────────────────────────────────────────────────────────

    async def _join_cb(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        t = await db.get_tournament(self.tournament_id)
        if not t or t["status"] != "open":
            return await interaction.followup.send(
                "❌ Las inscripciones para este torneo ya están cerradas.", ephemeral=True
            )

        participants    = await db.get_tournament_participants(self.tournament_id)
        participant_ids = {p["user_id"] for p in participants}

        # Toggle: already inscribed → remove
        if interaction.user.id in participant_ids:
            await db.remove_tournament_participant(self.tournament_id, interaction.user.id)
            participants = await db.get_tournament_participants(self.tournament_id)
            try:
                await interaction.message.edit(embed=build_inscription_embed(t, len(participants)))
            except Exception:
                pass
            return await interaction.followup.send(
                "❌ Te has desinscrito del torneo.", ephemeral=True
            )

        # Slots full? (soft pre-check before requirement validation)
        if len(participants) >= t["slots"]:
            return await interaction.followup.send(embed=build_rejected_embed(), ephemeral=True)

        # ── Register (atomic: enforces cap at DB level) ───────────────────────
        added = await db.add_tournament_participant_if_open(
            self.tournament_id, interaction.user.id, t["slots"]
        )
        if not added:
            # Slot was taken by a concurrent press or tournament closed
            return await interaction.followup.send(embed=build_rejected_embed(), ephemeral=True)

        participants = await db.get_tournament_participants(self.tournament_id)
        registered   = len(participants)
        t            = await db.get_tournament(self.tournament_id)

        # Update inscription embed counter
        try:
            await interaction.message.edit(embed=build_inscription_embed(t, registered))
        except Exception:
            pass

        await interaction.followup.send(
            embed=build_joined_embed(t, registered), ephemeral=True
        )

        # Auto-start when full
        if registered >= t["slots"]:
            cog = interaction.client.get_cog("Tournaments")
            if cog:
                await cog._start_tournament(t, interaction.channel)

    # ── Participants list ─────────────────────────────────────────────────────

    async def _list_cb(self, interaction: discord.Interaction):
        participants = await db.get_tournament_participants(self.tournament_id)
        t            = await db.get_tournament(self.tournament_id)
        if not participants:
            return await interaction.response.send_message(
                "No hay participantes inscritos aún.", ephemeral=True
            )
        mentions = [f"<@{p['user_id']}>" for p in participants[:25]]
        extra    = f"\n…y {len(participants) - 25} más" if len(participants) > 25 else ""
        slots    = t["slots"] if t else "?"
        await interaction.response.send_message(
            f"**Inscritos ({len(participants)}/{slots}):**\n" + ", ".join(mentions) + extra,
            ephemeral=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Cog
# ─────────────────────────────────────────────────────────────────────────────

class TournamentsCog(commands.Cog, name="Tournaments"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_tournaments.start()

    def cog_unload(self):
        self.check_tournaments.cancel()

    # ── Command group ─────────────────────────────────────────────────────────

    torneo = app_commands.Group(name="torneo", description="Sistema de torneos")

    # ── /torneo crear ─────────────────────────────────────────────────────────

    @torneo.command(name="crear", description="Crear un torneo nuevo y abrir inscripciones")
    @app_commands.describe(
        nombre  = "Nombre del torneo",
        cupo    = "Número de participantes (2–100)",
        inicio  = "Fecha y hora de inicio — formato: AAAA-MM-DD HH:MM",
        canal   = "Canal donde se publicará el torneo",
        premio  = "Premio del torneo (opcional)",
        modo    = "Modo de eliminación",
    )
    @app_commands.choices(modo=[
        app_commands.Choice(name="Eliminación Simple", value="eliminacion_simple"),
    ])
    @checks.is_admin()
    async def torneo_crear(
        self,
        interaction: discord.Interaction,
        nombre: str,
        cupo: int,
        inicio: str,
        canal: discord.TextChannel,
        premio: str = None,
        modo: str   = "eliminacion_simple",
    ):
        await interaction.response.defer(ephemeral=True)

        if not (2 <= cupo <= MAX_SLOTS):
            return await interaction.followup.send(
                f"❌ El cupo debe ser entre 2 y {MAX_SLOTS} participantes."
            )

        active = await db.get_active_tournament(interaction.guild_id)
        if active:
            return await interaction.followup.send(
                f"❌ Ya hay un torneo activo: **{active['name']}**. "
                f"Cancélalo primero con `/torneo cancelar`."
            )

        try:
            start_time = datetime.strptime(inicio, "%Y-%m-%d %H:%M").isoformat()
        except ValueError:
            return await interaction.followup.send(
                "❌ Formato de fecha inválido. Usa `AAAA-MM-DD HH:MM` — ej: `2025-07-20 18:00`."
            )

        t_id = await db.create_tournament(
            guild_id   = interaction.guild_id,
            channel_id = canal.id,
            host_id    = interaction.user.id,
            name       = nombre,
            mode       = modo,
            prize      = premio,
            slots      = cupo,
            start_time = start_time,
        )

        t    = await db.get_tournament(t_id)
        view = TournamentInscriptionView(t_id)
        msg  = await canal.send(embed=build_inscription_embed(t, 0), view=view)
        await db.update_tournament(t_id, message_id=msg.id)

        await interaction.followup.send(
            f"✅ Torneo **{nombre}** creado en {canal.mention}. ID: `{t_id}`."
        )

    # ── /torneo cancelar ──────────────────────────────────────────────────────

    @torneo.command(name="cancelar", description="Cancelar el torneo activo")
    @checks.is_admin()
    async def torneo_cancelar(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        t = await db.get_active_tournament(interaction.guild_id)
        if not t:
            return await interaction.followup.send("❌ No hay ningún torneo activo.")

        await db.update_tournament(t["id"], status="cancelled")

        try:
            ch = self.bot.get_channel(t["channel_id"])
            if ch and t.get("message_id"):
                msg = await ch.fetch_message(t["message_id"])
                await msg.delete()
        except Exception:
            pass

        await interaction.followup.send(f"✅ Torneo **{t['name']}** cancelado.")

    # ── /torneo resultado ─────────────────────────────────────────────────────

    @torneo.command(name="resultado", description="Registrar el ganador de un partido")
    @app_commands.describe(
        partido = "Número del partido de la ronda actual (ver /torneo bracket; 99 = partido por el 3er puesto)",
        ganador = "Miembro que ganó el partido",
    )
    @checks.is_admin()
    async def torneo_resultado(
        self,
        interaction: discord.Interaction,
        partido: int,
        ganador: discord.Member,
    ):
        await interaction.response.defer(ephemeral=True)

        t = await db.get_active_tournament(interaction.guild_id)
        if not t or t["status"] != "active":
            return await interaction.followup.send(
                "❌ El torneo no está activo en este momento."
            )

        match = await db.get_match_by_round_number(t["id"], t["current_round"], partido)
        if not match:
            return await interaction.followup.send(
                f"❌ No encontré el partido **#{partido}** en la ronda actual (ronda {t['current_round']}). "
                "Revisa el número con `/torneo bracket`."
            )
        if match.get("winner_id"):
            return await interaction.followup.send(
                "❌ Este partido ya tiene un resultado registrado."
            )

        valid_ids = {match["player1_id"], match.get("player2_id")}
        if ganador.id not in valid_ids:
            return await interaction.followup.send(
                f"❌ {ganador.mention} no es uno de los jugadores de este partido."
            )

        loser_id = (
            match["player1_id"] if ganador.id == match.get("player2_id")
            else match.get("player2_id")
        )

        # Atomic claim: only proceeds if winner_id is still NULL
        # (usa match["id"], la clave real en la BD — "partido" es solo el
        # número de display dentro de la ronda, no la clave primaria).
        claimed = await db.claim_match_result(match["id"], winner_id=ganador.id, loser_id=loser_id)
        if not claimed:
            return await interaction.followup.send(
                "⚠️ Este partido ya fue registrado por otra acción simultánea."
            )

        loser   = interaction.guild.get_member(loser_id) if loser_id else None
        channel = self.bot.get_channel(t["channel_id"]) or interaction.channel

        await channel.send(embed=build_result_embed(ganador, loser, partido))
        await interaction.followup.send("✅ Resultado registrado.")

        # Advance round if complete
        t_fresh = await db.get_tournament(t["id"])
        await self._maybe_advance_round(t_fresh, channel)

    # ── /torneo bracket ───────────────────────────────────────────────────────

    @torneo.command(name="bracket", description="Ver el bracket del torneo activo")
    async def torneo_bracket(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        t = await db.get_active_tournament(interaction.guild_id)
        if not t or t["status"] != "active":
            return await interaction.followup.send(
                "No hay ningún torneo activo con bracket disponible."
            )

        matches = await db.get_round_matches(t["id"], t["current_round"])
        if not matches:
            return await interaction.followup.send("No hay partidos generados aún.")

        await interaction.followup.send(
            embed=build_bracket_embed(t, matches, interaction.guild)
        )

    # ── /torneo info ──────────────────────────────────────────────────────────

    @torneo.command(name="info", description="Información del torneo en curso")
    async def torneo_info(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        t = await db.get_active_tournament(interaction.guild_id)
        if not t:
            return await interaction.followup.send(
                "No hay ningún torneo activo actualmente."
            )

        participants = await db.get_tournament_participants(t["id"])
        start_ts     = int(datetime.fromisoformat(t["start_time"]).timestamp())
        host         = interaction.guild.get_member(t["host_id"])

        lines = [
            f"📅 **Inicio:** <t:{start_ts}:F>",
            f"👥 **Participantes:** {len(participants)}/{t['slots']}",
            f"🎯 **Modo:** {_mode_label(t['mode'])}",
            f"📊 **Estado:** {t['status']}",
            f"🔢 **Ronda actual:** {t['current_round']}",
        ]
        if t.get("prize"):
            lines.append(f"🎁 **Premio:** {t['prize']}")
        if host:
            lines.append(f"👤 **Creado por:** {host.mention}")

        embed = discord.Embed(
            title       = f"🏆 {t['name']}",
            description = "\n".join(lines),
            color       = COLOR_MINEORBIT,
        )
        await interaction.followup.send(embed=embed)

    # ── /torneo lista ─────────────────────────────────────────────────────────

    @torneo.command(name="lista", description="Ver historial de torneos finalizados")
    async def torneo_lista(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tournaments = await db.get_finished_tournaments(interaction.guild_id, limit=10)
        await interaction.followup.send(
            embed=build_history_embed(tournaments, interaction.guild)
        )

    # ── Background loop ───────────────────────────────────────────────────────

    @tasks.loop(seconds=30)
    async def check_tournaments(self):
        try:
            open_tournaments = await db.get_open_tournaments()
            now = datetime.utcnow()
            for t in open_tournaments:
                start        = datetime.fromisoformat(t["start_time"])
                participants = await db.get_tournament_participants(t["id"])
                if now >= start or len(participants) >= t["slots"]:
                    channel = self.bot.get_channel(t["channel_id"])
                    if channel:
                        await self._start_tournament(t, channel)
        except Exception as e:
            logger.error(f"[Tournaments loop] {e}")

    @check_tournaments.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    # ── Internal: start tournament ────────────────────────────────────────────

    async def _start_tournament(self, t: dict, channel: discord.abc.Messageable):
        """
        Atomically transitions open → active and generates Round 1.
        Safe to call from both the button callback and the background loop.
        """
        claimed = await db.claim_tournament_start(t["id"])
        if not claimed:
            return  # Another execution already started it

        participants = await db.get_tournament_participants(t["id"])
        player_ids   = [p["user_id"] for p in participants]

        if len(player_ids) < 2:
            await db.update_tournament(t["id"], status="cancelled")
            await channel.send(
                f"⚠️ El torneo **{t['name']}** fue cancelado por falta de participantes (mínimo 2)."
            )
            return

        await db.update_tournament(t["id"], status="active", current_round=1)
        t = await db.get_tournament(t["id"])

        # Disable inscription button
        try:
            if t.get("message_id"):
                orig = await channel.fetch_message(t["message_id"])
                closed = build_inscription_embed(t, len(player_ids))
                closed.set_footer(text="Inscripciones cerradas.")
                await orig.edit(embed=closed, view=None)
        except Exception as e:
            logger.warning(f"[Tournaments] No se pudo deshabilitar botón de inscripción: {e}")

        await channel.send(embed=build_started_embed(t))

        matches = await self._generate_round(t["id"], player_ids, 1)
        await channel.send(embed=build_bracket_embed(t, matches, channel.guild))

    # ── Internal: generate round ──────────────────────────────────────────────

    async def _generate_round(
        self, tournament_id: int, player_ids: list[int], round_num: int
    ) -> list[dict]:
        shuffled = player_ids[:]
        random.shuffle(shuffled)
        matches  = []
        for i in range(0, len(shuffled), 2):
            p1  = shuffled[i]
            p2  = shuffled[i + 1] if i + 1 < len(shuffled) else None
            m_id = await db.create_tournament_match(
                tournament_id = tournament_id,
                round_num     = round_num,
                match_number  = (i // 2) + 1,
                player1_id    = p1,
                player2_id    = p2,
            )
            if p2 is None:
                # Bye: auto-advance
                await db.set_match_result(m_id, winner_id=p1, loser_id=None)
            m = await db.get_tournament_match(m_id)
            if m:
                matches.append(m)
        return matches

    # ── Internal: advance round / finish ─────────────────────────────────────

    async def _maybe_advance_round(self, t: dict, channel: discord.abc.Messageable):
        """
        Check if all matches in the current round are done; if so, advance.
        Uses an atomic DB claim so concurrent calls only advance once.
        """
        rnd     = t["current_round"]
        matches = await db.get_round_matches(t["id"], rnd)
        normal  = [m for m in matches if not m.get("is_third_place")]
        third_m = next((m for m in matches if m.get("is_third_place")), None)

        # Round not done yet?
        if not all(m["winner_id"] for m in normal):
            return

        winners = [m["winner_id"] for m in normal]
        losers  = [m["loser_id"]  for m in normal if m.get("loser_id")]
        guild   = channel.guild

        # ── Final: exactly 1 normal match in this round ───────────────────────
        if len(normal) == 1:
            # Wait for 3rd-place match too before announcing champion
            if third_m and not third_m.get("winner_id"):
                return

            # Atomic claim: only the first call sets status → finished
            claimed = await db.claim_tournament_finish(t["id"])
            if not claimed:
                return

            champion_id  = winners[0]
            runner_up_id = losers[0] if losers else None
            third_id     = third_m["winner_id"] if third_m else None

            await db.update_tournament(
                t["id"],
                champion_id    = champion_id,
                runner_up_id   = runner_up_id,
                third_place_id = third_id,
            )

            champion  = guild.get_member(champion_id)
            runner_up = guild.get_member(runner_up_id) if runner_up_id else None
            third     = guild.get_member(third_id)     if third_id     else None

            await self._assign_champion_role(guild, champion)

            t_fresh = await db.get_tournament(t["id"])
            await channel.send(embed=build_champion_embed(t_fresh, champion, runner_up, third))
            return

        # ── Intermediate round: generate next ────────────────────────────────
        next_round = rnd + 1

        # Atomic claim: only the first concurrent call advances the round
        claimed = await db.claim_round_advance(t["id"], rnd, next_round)
        if not claimed:
            return

        t = await db.get_tournament(t["id"])

        # Si la siguiente ronda es la final (2 ganadores reales), crear partido por 3er lugar
        if len(winners) == 2 and len(losers) == 2:
            await db.create_tournament_match(
                tournament_id  = t["id"],
                round_num      = next_round,
                match_number   = 99,
                player1_id     = losers[0],
                player2_id     = losers[1],
                is_third_place = 1,
            )

        await self._generate_round(t["id"], winners, next_round)

        # Include 3rd-place match in the bracket display if it exists
        all_next = await db.get_round_matches(t["id"], next_round)

        await channel.send(embed=build_new_round_embed(next_round))
        await channel.send(embed=build_bracket_embed(t, all_next, guild))

    # ── Internal: champion role ───────────────────────────────────────────────

    async def _assign_champion_role(
        self, guild: discord.Guild, member: discord.Member | None
    ):
        if not member:
            return
        role = discord.utils.get(guild.roles, name="🏆 Campeón")
        if role:
            try:
                await member.add_roles(role, reason="Campeón del torneo MineOrbit")
            except discord.Forbidden:
                logger.warning(
                    f"[Tournaments] Sin permisos para asignar rol campeón a {member}"
                )


async def setup(bot: commands.Bot):
    await bot.add_cog(TournamentsCog(bot))
