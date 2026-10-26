"""
Cog: Economía — MineOrbit Créditos 🪙
- /balance [usuario]     — ver saldo
- /daily                 — reclamo diario (cantidad fija y pequeña)
- /trabajar              — "trabajo" con cooldown, cantidad pequeña variable
- /robar <usuario>       — intenta robarle parte del saldo a otro miembro (con riesgo)
- /topricos              — leaderboard de los que más tienen
- /economia dar/quitar   — (staff) ajustar el saldo de alguien manualmente

Diseño deliberado: la economía es LENTA. Las cantidades por /daily y /trabajar
son pequeñas a propósito, y todo tiene cooldown largo, para que acumular lo
suficiente para comprar algo caro en /tienda tome bastante tiempo real. Esto
es intencional — ver cogs/shop.py para los precios de los rangos.
"""
import random
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from utils import checks, embeds
from config import COLOR_INFO, COLOR_GOLD

CURRENCY_NAME = "Créditos"
CURRENCY_EMOJI = "🪙"

# ── Cantidades — deliberadamente pequeñas / cooldowns largos ──────────────────
DAILY_AMOUNT = 60
DAILY_COOLDOWN = timedelta(hours=24)

WORK_MIN, WORK_MAX = 20, 45
WORK_COOLDOWN = timedelta(hours=2)
WORK_FLAVOR = [
    "Ayudaste a construir una granja de hierro y te pagaron",
    "Guiaste a un nuevo jugador por el spawn y te dio propina",
    "Vendiste diamantes en el mercado del servidor",
    "Hiciste un turno de moderación voluntaria en el chat",
    "Encontraste un cofre perdido con algo de dinero",
]

ROB_COOLDOWN = timedelta(hours=6)
ROB_SUCCESS_CHANCE = 0.35
ROB_STEAL_MIN_PCT, ROB_STEAL_MAX_PCT = 0.10, 0.20
ROB_STEAL_CAP = 300
ROB_FAIL_PENALTY = 40
ROB_MIN_TARGET_BALANCE = 100


def fmt(amount: int) -> str:
    return f"{CURRENCY_EMOJI} **{amount:,}** {CURRENCY_NAME}"


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _remaining(last: str | None, cooldown: timedelta) -> timedelta | None:
    last_dt = _parse_ts(last)
    if not last_dt:
        return None
    elapsed = datetime.now(timezone.utc) - last_dt
    if elapsed >= cooldown:
        return None
    return cooldown - elapsed


def _fmt_timedelta(td: timedelta) -> str:
    total = int(td.total_seconds())
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="balance", description=f"Ver el saldo de {CURRENCY_NAME}.")
    @app_commands.describe(usuario="Usuario a consultar (vacío = tú mismo)")
    async def balance(self, interaction: discord.Interaction, usuario: discord.Member = None):
        target = usuario or interaction.user
        bal = await db.get_balance(interaction.guild_id, target.id)
        embed = discord.Embed(
            title=f"{CURRENCY_EMOJI} Saldo de {target.display_name}",
            description=fmt(bal),
            color=COLOR_INFO,
        )
        await interaction.response.send_message(embed=embed, ephemeral=(target == interaction.user))

    @app_commands.command(name="daily", description="Reclama tu recompensa diaria.")
    async def daily(self, interaction: discord.Interaction):
        row = await db.get_economy_row(interaction.guild_id, interaction.user.id)
        remaining = _remaining(row["last_daily"], DAILY_COOLDOWN)
        if remaining:
            return await interaction.response.send_message(
                embed=embeds.warning(f"Ya reclamaste tu diario. Vuelve en **{_fmt_timedelta(remaining)}**."),
                ephemeral=True,
            )
        now = datetime.now(timezone.utc).isoformat()
        await db.set_economy_cooldown(interaction.guild_id, interaction.user.id, "last_daily", now)
        new_balance = await db.deposit(interaction.guild_id, interaction.user.id, DAILY_AMOUNT)
        await interaction.response.send_message(
            embed=embeds.success(
                f"Reclamaste tu diario: {fmt(DAILY_AMOUNT)}.\nSaldo actual: {fmt(new_balance)}"
            ),
            ephemeral=True,
        )

    @app_commands.command(name="trabajar", description="Trabaja un rato para ganar algo de dinero.")
    async def trabajar(self, interaction: discord.Interaction):
        row = await db.get_economy_row(interaction.guild_id, interaction.user.id)
        remaining = _remaining(row["last_work"], WORK_COOLDOWN)
        if remaining:
            return await interaction.response.send_message(
                embed=embeds.warning(f"Estás cansado del último trabajo. Vuelve en **{_fmt_timedelta(remaining)}**."),
                ephemeral=True,
            )
        amount = random.randint(WORK_MIN, WORK_MAX)
        flavor = random.choice(WORK_FLAVOR)
        now = datetime.now(timezone.utc).isoformat()
        await db.set_economy_cooldown(interaction.guild_id, interaction.user.id, "last_work", now)
        new_balance = await db.deposit(interaction.guild_id, interaction.user.id, amount)
        await interaction.response.send_message(
            embed=embeds.success(f"{flavor}: ganaste {fmt(amount)}.\nSaldo actual: {fmt(new_balance)}"),
            ephemeral=True,
        )

    @app_commands.command(name="robar", description="Intenta robarle dinero a otro miembro (con riesgo).")
    @app_commands.describe(usuario="A quién intentar robarle")
    async def robar(self, interaction: discord.Interaction, usuario: discord.Member):
        if usuario.id == interaction.user.id:
            return await interaction.response.send_message(
                embed=embeds.error("No puedes robarte a ti mismo."), ephemeral=True
            )
        if usuario.bot:
            return await interaction.response.send_message(
                embed=embeds.error("No puedes robarle a un bot."), ephemeral=True
            )

        row = await db.get_economy_row(interaction.guild_id, interaction.user.id)
        remaining = _remaining(row["last_rob"], ROB_COOLDOWN)
        if remaining:
            return await interaction.response.send_message(
                embed=embeds.warning(f"Todavía te están buscando por el último robo. Espera **{_fmt_timedelta(remaining)}**."),
                ephemeral=True,
            )

        target_balance = await db.get_balance(interaction.guild_id, usuario.id)
        if target_balance < ROB_MIN_TARGET_BALANCE:
            return await interaction.response.send_message(
                embed=embeds.warning(f"{usuario.mention} no tiene suficiente dinero como para valer la pena robarle."),
                ephemeral=True,
            )

        now = datetime.now(timezone.utc).isoformat()
        await db.set_economy_cooldown(interaction.guild_id, interaction.user.id, "last_rob", now)

        if random.random() <= ROB_SUCCESS_CHANCE:
            pct = random.uniform(ROB_STEAL_MIN_PCT, ROB_STEAL_MAX_PCT)
            stolen = min(ROB_STEAL_CAP, max(1, round(target_balance * pct)))
            await db.withdraw(interaction.guild_id, usuario.id, stolen)
            new_balance = await db.deposit(interaction.guild_id, interaction.user.id, stolen)
            await interaction.response.send_message(
                embed=embeds.success(
                    f"🕵️ ¡Le robaste {fmt(stolen)} a {usuario.mention}!\nTu saldo ahora: {fmt(new_balance)}"
                )
            )
        else:
            took = await db.withdraw(interaction.guild_id, interaction.user.id, ROB_FAIL_PENALTY)
            penalty_text = fmt(ROB_FAIL_PENALTY) if took else "todo lo que tenías (no llegaba a la multa completa)"
            await interaction.response.send_message(
                embed=embeds.error(
                    f"🚨 Te atraparon intentando robarle a {usuario.mention} y pagaste una multa de {penalty_text}."
                )
            )

    @app_commands.command(name="topricos", description="Ver el ranking de los más ricos del servidor.")
    async def topricos(self, interaction: discord.Interaction):
        rows = await db.get_economy_leaderboard(interaction.guild_id, limit=10)
        if not rows:
            return await interaction.response.send_message(
                embed=embeds.info("Todavía nadie tiene saldo registrado."), ephemeral=True
            )
        lines = []
        for i, row in enumerate(rows, start=1):
            member = interaction.guild.get_member(row["user_id"])
            name = member.display_name if member else f"<@{row['user_id']}>"
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"`#{i}`")
            lines.append(f"{medal} {name} — {fmt(row['balance'])}")
        embed = discord.Embed(
            title=f"{CURRENCY_EMOJI} Ranking de {CURRENCY_NAME}",
            description="\n".join(lines),
            color=COLOR_GOLD,
        )
        await interaction.response.send_message(embed=embed)

    # ─── Staff: ajustar saldo manualmente ────────────────────────────────────
    economia_group = app_commands.Group(
        name="economia", description="Administración de la economía del servidor (staff)."
    )

    @economia_group.command(name="dar", description="(Staff) Da dinero a un usuario.")
    @app_commands.describe(usuario="Usuario a quien dar dinero", cantidad="Cantidad a dar")
    @checks.is_staff()
    async def economia_dar(self, interaction: discord.Interaction, usuario: discord.Member, cantidad: app_commands.Range[int, 1]):
        new_balance = await db.deposit(interaction.guild_id, usuario.id, cantidad)
        await interaction.response.send_message(
            embed=embeds.success(f"Le diste {fmt(cantidad)} a {usuario.mention}. Nuevo saldo: {fmt(new_balance)}"),
            ephemeral=True,
        )

    @economia_group.command(name="quitar", description="(Staff) Quita dinero a un usuario.")
    @app_commands.describe(usuario="Usuario al que quitar dinero", cantidad="Cantidad a quitar")
    @checks.is_staff()
    async def economia_quitar(self, interaction: discord.Interaction, usuario: discord.Member, cantidad: app_commands.Range[int, 1]):
        ok = await db.withdraw(interaction.guild_id, usuario.id, cantidad)
        if not ok:
            bal = await db.get_balance(interaction.guild_id, usuario.id)
            return await interaction.response.send_message(
                embed=embeds.error(f"{usuario.mention} no tiene suficiente saldo (tiene {fmt(bal)})."),
                ephemeral=True,
            )
        new_balance = await db.get_balance(interaction.guild_id, usuario.id)
        await interaction.response.send_message(
            embed=embeds.success(f"Le quitaste {fmt(cantidad)} a {usuario.mention}. Nuevo saldo: {fmt(new_balance)}"),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
