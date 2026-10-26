"""
Cog: Staff Weekly — Top 10 semanal de actividad del staff, enviado por DM
únicamente al responsable de backups (RECIPIENT_ID).

- Automático: cada domingo a partir de las 20:00 (hora de Caracas). Si el bot
  estaba apagado ese día, lo envía al volver a arrancar el mismo día.
- Cada envío exitoso guarda su momento. El siguiente cuenta desde ahí, así
  que no hay huecos ni solapes aunque el bot haya estado apagado. La primera
  vez cuenta los últimos 7 días.
- /topstaff — (solo RECIPIENT_ID) envía ahora el ranking desde el último envío
  automático, sin cerrar la semana.

Datos: tabla staff_activity (ya existente). Cada acción tiene un peso (PESOS):
  🔨 Bans        → 'ban'           (peso 3)
  🔇 Timeouts    → 'timeout'       (peso 2)
  🎫 Tickets     → 'ticket_claim'  (peso 1)
Puntaje = bans×3 + timeouts×2 + tickets×1. Ordena por puntaje y solo incluye
a quien tenga actividad. Para cambiar la prioridad, edita PESOS.
"""

import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import db

RECIPIENT_ID = 719935387056865393
PERIOD_DAYS = 7  # ventana usada solo la primera vez (sin envío previo)
TOP_N = 10
SEND_WEEKDAY = 6  # 6 = domingo
SEND_HOUR_LOCAL = 20  # 20:00 hora de Caracas
TZ_OFFSET = datetime.timedelta(
    hours=-4
)  # America/Caracas (UTC-4, sin horario de verano)
TABLE = "staff_weekly_cursor"

# Prioridad de cada acción en el ranking (puntaje = suma de cantidad × peso).
PESOS = {
    "ban": 3,
    "timeout": 2,
    "ticket_claim": 1,
}


def _utc_now() -> datetime.datetime:
    return datetime.datetime.utcnow().replace(microsecond=0)


def _fmt(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse(s: str | None) -> datetime.datetime | None:
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


async def _ensure_table() -> None:
    await db.execute(
        f"CREATE TABLE IF NOT EXISTS {TABLE} "
        "(id INTEGER PRIMARY KEY CHECK (id = 1), sent_day TEXT, sent_at TEXT)"
    )


async def _get_cursor() -> dict:
    return await db.fetchone(f"SELECT sent_day, sent_at FROM {TABLE} WHERE id=1") or {}


async def _set_cursor(day: str, sent_at: datetime.datetime) -> None:
    await db.execute(
        f"INSERT OR REPLACE INTO {TABLE} (id, sent_day, sent_at) VALUES (1, ?, ?)",
        (day, _fmt(sent_at)),
    )


async def build_ranking(
    guild_id: int, since: datetime.datetime, until: datetime.datetime
) -> list[dict]:
    return await db.fetchall(
        f"""SELECT user_id,
                  SUM(CASE WHEN action_type = 'ban' THEN 1 ELSE 0 END)           AS bans,
                  SUM(CASE WHEN action_type = 'timeout' THEN 1 ELSE 0 END)       AS timeouts,
                  SUM(CASE WHEN action_type = 'ticket_claim' THEN 1 ELSE 0 END)  AS tickets,
                  SUM(CASE action_type
                        WHEN 'ban' THEN {PESOS["ban"]}
                        WHEN 'timeout' THEN {PESOS["timeout"]}
                        WHEN 'ticket_claim' THEN {PESOS["ticket_claim"]}
                        ELSE 0 END) AS puntaje
           FROM staff_activity
           WHERE guild_id = ?
             AND action_type IN ('ban', 'timeout', 'ticket_claim')
             AND created_at >= ?
             AND created_at < ?
           GROUP BY user_id
           HAVING puntaje > 0
           ORDER BY puntaje DESC, bans DESC, timeouts DESC
           LIMIT ?""",
        (guild_id, _fmt(since), _fmt(until), TOP_N),
    )


def build_message(rows: list[dict], start: datetime.date, end: datetime.date) -> str:
    lines = [
        "🏆┃TOP 10 STAFF SEMANAL",
        "🌀 Ranking semanal de actividad del Staff Team de MineOrbit.",
        f"📅 Periodo: `{start:%d/%m/%Y}` — `{end:%d/%m/%Y}`",
        "🥇 TOP 10 — MIEMBROS MÁS ACTIVOS",
    ]
    if not rows:
        lines += ["", "*Sin actividad registrada esta semana.*"]
    for i, r in enumerate(rows, 1):
        lines += [
            f"`{i}.` <@{r['user_id']}>",
            "",
            f"* 🔨 Bans: `{r['bans']}`",
            f"* 🔇 Timeouts: `{r['timeouts']}`",
            f"* 🎫 Tickets atendidos: `{r['tickets']}`",
            "",
        ]
    lines.append(
        "-# 📊 Ranking actualizado automáticamente con los registros de actividad del Staff."
    )
    return "\n".join(lines)


async def send_ranking(bot: commands.Bot, advance: bool) -> None:
    """Envía el ranking por DM. Si advance=True, al terminar bien mueve el
    cursor para que el siguiente envío empiece justo aquí."""
    now = _utc_now()
    cursor = await _get_cursor()
    since = _parse(cursor.get("sent_at")) or (
        now - datetime.timedelta(days=PERIOD_DAYS)
    )

    start_local = (since + TZ_OFFSET).date()
    end_local = (now + TZ_OFFSET).date()
    user = bot.get_user(RECIPIENT_ID) or await bot.fetch_user(RECIPIENT_ID)
    for guild in bot.guilds:
        rows = await build_ranking(guild.id, since, now)
        await user.send(build_message(rows, start_local, end_local))

    if advance:
        await _set_cursor(end_local.isoformat(), now)


class StaffWeekly(commands.Cog, name="StaffWeekly"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.weekly_loop.start()

    def cog_unload(self):
        self.weekly_loop.cancel()

    @tasks.loop(minutes=30)
    async def weekly_loop(self):
        local_now = _utc_now() + TZ_OFFSET
        if local_now.weekday() != SEND_WEEKDAY or local_now.hour < SEND_HOUR_LOCAL:
            return
        today = local_now.date().isoformat()
        if (await _get_cursor()).get("sent_day") == today:
            return
        try:
            await send_ranking(self.bot, advance=True)
        except (discord.Forbidden, discord.HTTPException) as e:
            # El cursor no avanza: se reintenta en el siguiente ciclo (30 min).
            print(f"[StaffWeekly] no se pudo enviar el top semanal: {e}")

    @weekly_loop.before_loop
    async def before_weekly_loop(self):
        await self.bot.wait_until_ready()
        await _ensure_table()

    @app_commands.command(
        name="topstaff",
        description="(Solo responsable de backups) Envía ahora el top semanal del staff por DM",
    )
    async def topstaff(self, interaction: discord.Interaction):
        if interaction.user.id != RECIPIENT_ID:
            return await interaction.response.send_message(
                "❌ Este comando no está disponible para ti.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        try:
            await send_ranking(self.bot, advance=False)
        except (discord.Forbidden, discord.HTTPException) as e:
            return await interaction.followup.send(
                f"❌ No pude enviarte el DM: `{e}`", ephemeral=True
            )
        await interaction.followup.send(
            "✅ Top semanal enviado por DM.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(StaffWeekly(bot))
