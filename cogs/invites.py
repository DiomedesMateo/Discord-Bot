"""
Cog: Rastreo de invitaciones
- /invites ver [usuario]          — cuántos miembros ha traído un usuario
- /invites top                    — leaderboard de quienes más han invitado
- /invites quieninvito <usuario>  — (staff) quién invitó a un miembro concreto

Filtro antifraude (anti-multicuenta):
Para que una invitación cuente en el contador (/invites ver, /invites top,
y el logro "Invitador"), el invitado debe cumplir DOS condiciones:
  1. Su cuenta de Discord debe tener 3 meses (90 días) o más de creada.
  2. Debe estar verificado con el rol configurado en /verification setup
     (si el servidor no tiene un sistema de verificación configurado, esta
     segunda condición se ignora y solo se exige la antigüedad de cuenta).

Como la verificación normalmente ocurre DESPUÉS del join (el usuario debe
presionar el botón o resolver el captcha), el join siempre se registra en
el historial de inmediato, pero el conteo real puede quedar pendiente
hasta que el usuario se verifique — se resuelve en el listener
`on_member_update` de más abajo, cuando se le asigna el rol de
verificación.
"""
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from utils import checks, embeds
from config import COLOR_PRIMARY

log = logging.getLogger(__name__)

MIN_ACCOUNT_AGE = timedelta(days=90)  # 3 meses


class Invites(commands.Cog, name="Invites"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # guild_id -> {invite_code: uses}
        self.invite_cache: dict[int, dict[str, int]] = {}

    async def cog_load(self):
        for guild in self.bot.guilds:
            await self._cache_guild_invites(guild)

    async def _cache_guild_invites(self, guild: discord.Guild) -> None:
        try:
            invites = await guild.invites()
        except discord.Forbidden:
            log.warning(
                "[Invites] Sin permiso 'Gestionar servidor' en %s — no se puede rastrear invitaciones.",
                guild.name,
            )
            self.invite_cache[guild.id] = {}
            return
        except Exception as e:
            log.warning("[Invites] Error cacheando invitaciones de %s: %s", guild.name, e)
            self.invite_cache[guild.id] = {}
            return

        self.invite_cache[guild.id] = {inv.code: (inv.uses or 0) for inv in invites}

    async def _passes_fraud_filters(self, member: discord.Member) -> bool:
        """True si el join de `member` cumple los requisitos para contar
        como invitación válida: cuenta de 3+ meses Y (si el servidor tiene
        verificación configurada) el rol de verificación."""
        account_age = datetime.now(timezone.utc) - member.created_at
        if account_age < MIN_ACCOUNT_AGE:
            return False

        ver_config = await db.get_verification_config(member.guild.id)
        role_id = ver_config.get("role_id")
        if not role_id:
            # El servidor no usa el sistema de verificación —
            # ese requisito no aplica, solo se exige la antigüedad.
            return True

        role = member.guild.get_role(role_id)
        if not role:
            # El rol configurado ya no existe — se ignora el requisito.
            return True

        return role in member.roles

    async def _try_count_invite(self, member: discord.Member, inviter_id: int | None) -> None:
        """Si el join ya cumple ambos filtros, lo marca como contado y
        actualiza el progreso del logro 'Invitador' del invitador."""
        if not inviter_id:
            return
        if not await self._passes_fraud_filters(member):
            return

        counted_now = await db.mark_invite_counted(member.guild.id, member.id, inviter_id)
        if not counted_now:
            return  # ya estaba contado — no duplicar el check de logros

        inviter_member = member.guild.get_member(inviter_id)
        if inviter_member:
            uses = await db.get_invite_uses(member.guild.id, inviter_id)
            achievements_cog = self.bot.get_cog("Logros")
            if achievements_cog:
                await achievements_cog.check_progress(inviter_member, "invites", uses)

    invites_group = app_commands.Group(name="invites", description="Rastreo de invitaciones del servidor.")

    # ── Listeners ─────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await self._cache_guild_invites(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        self.invite_cache.setdefault(invite.guild.id, {})[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        self.invite_cache.get(invite.guild.id, {}).pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        guild = member.guild
        before = self.invite_cache.get(guild.id, {})
        account_created_at = member.created_at.isoformat()

        try:
            current_invites = await guild.invites()
        except discord.Forbidden:
            # Sin permiso para leer invitaciones — registramos el join igual,
            # solo que sin poder identificar quién invitó.
            await db.record_invite_join(guild.id, member.id, None, None, account_created_at)
            return
        except Exception as e:
            log.warning("[Invites] Error leyendo invitaciones tras el join de %s en %s: %s", member, guild.name, e)
            return

        used_invite = None
        for inv in current_invites:
            prev_uses = before.get(inv.code, 0)
            if (inv.uses or 0) > prev_uses:
                used_invite = inv
                break

        # Refrescar el cache siempre, haya coincidencia o no.
        self.invite_cache[guild.id] = {inv.code: (inv.uses or 0) for inv in current_invites}

        inviter_id = used_invite.inviter.id if (used_invite and used_invite.inviter) else None
        invite_code = used_invite.code if used_invite else None

        # Fallback para invitación vanity (discord.gg/nombre-personalizado):
        # no siempre aparece reflejada en guild.invites() de la misma forma.
        if inviter_id is None and "VANITY_URL" in (guild.features or []):
            try:
                vanity = await guild.vanity_invite()
                if vanity:
                    invite_code = vanity.code
            except Exception:
                pass

        await db.record_invite_join(guild.id, member.id, inviter_id, invite_code, account_created_at)

        # Filtro antifraude: si ya cumple antigüedad de cuenta (y
        # verificación, si el servidor la exige) se cuenta de inmediato.
        # Si no, queda pendiente y se cuenta más tarde en on_member_update
        # cuando el usuario se verifique.
        await self._try_count_invite(member, inviter_id)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Si un join quedó pendiente de contar (le faltaba la
        verificación), este listener detecta cuándo el miembro recibe el
        rol de verificación y reintenta contar la invitación."""
        added_roles = set(after.roles) - set(before.roles)
        if not added_roles:
            return

        ver_config = await db.get_verification_config(after.guild.id)
        role_id = ver_config.get("role_id")
        if not role_id or not any(r.id == role_id for r in added_roles):
            return

        join_row = await db.get_inviter_of(after.guild.id, after.id)
        if not join_row or join_row.get("counted") or not join_row.get("inviter_id"):
            return

        await self._try_count_invite(after, join_row["inviter_id"])

    # ── Comandos ──────────────────────────────────────────────────────────────

    @invites_group.command(name="ver", description="Ver cuántos miembros ha invitado alguien.")
    @app_commands.describe(usuario="Usuario a consultar (vacío = tú mismo)")
    async def invites_ver(self, interaction: discord.Interaction, usuario: discord.Member = None):
        target = usuario or interaction.user
        uses = await db.get_invite_uses(interaction.guild_id, target.id)
        embed = discord.Embed(
            title=f"📨 Invitaciones de {target.display_name}",
            description=f"Ha traído **{uses}** miembro(s) al servidor.",
            color=COLOR_PRIMARY,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @invites_group.command(name="top", description="Ver el ranking de quienes más han invitado.")
    async def invites_top(self, interaction: discord.Interaction):
        rows = await db.get_invite_leaderboard(interaction.guild_id, limit=10)
        if not rows:
            return await interaction.response.send_message(
                embed=embeds.info("Todavía no hay invitaciones registradas."), ephemeral=True
            )
        lines = []
        for i, row in enumerate(rows, start=1):
            member = interaction.guild.get_member(row["inviter_id"])
            name = member.display_name if member else f"<@{row['inviter_id']}>"
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"`#{i}`")
            lines.append(f"{medal} {name} — **{row['uses']}** invitado(s)")
        embed = discord.Embed(
            title="📨 Ranking de invitaciones", description="\n".join(lines), color=COLOR_PRIMARY
        )
        await interaction.response.send_message(embed=embed)

    @invites_group.command(
        name="quieninvito",
        description="(Staff) Ver quién invitó a un miembro concreto.",
    )
    @app_commands.describe(usuario="Miembro del que quieres saber quién lo invitó")
    @checks.is_staff()
    async def invites_quieninvito(self, interaction: discord.Interaction, usuario: discord.Member):
        record = await db.get_inviter_of(interaction.guild_id, usuario.id)
        if not record or not record.get("inviter_id"):
            return await interaction.response.send_message(
                embed=embeds.info(
                    f"No se pudo rastrear quién invitó a {usuario.mention} (invitación vanity, "
                    "Discovery/onboarding, o se unió antes de instalar este sistema)."
                ),
                ephemeral=True,
            )
        embed = discord.Embed(
            title="🔍 Rastreo de invitación",
            description=(
                f"{usuario.mention} fue invitado por <@{record['inviter_id']}>"
                + (f" usando el código `{record['invite_code']}`" if record.get("invite_code") else "")
            ),
            color=COLOR_PRIMARY,
        )

        # Info antifraude: antigüedad de cuenta + verificación + si se
        # llegó a contar para el ranking/logro de invitaciones.
        counted = bool(record.get("counted"))
        account_age_str = "Desconocida"
        meets_age = False
        raw_created = record.get("account_created_at")
        if raw_created:
            try:
                created_dt = datetime.fromisoformat(raw_created)
                age = datetime.now(timezone.utc) - created_dt
                meets_age = age >= MIN_ACCOUNT_AGE
                account_age_str = f"{age.days} días (creada <t:{int(created_dt.timestamp())}:D>)"
            except Exception:
                pass

        ver_config = await db.get_verification_config(interaction.guild_id)
        role_id = ver_config.get("role_id")
        if role_id:
            role = interaction.guild.get_role(role_id)
            is_verified = bool(role and role in usuario.roles)
            verif_str = "✅ Sí" if is_verified else "❌ No"
        else:
            verif_str = "— (servidor sin verificación configurada)"

        embed.add_field(name="Antigüedad de la cuenta", value=account_age_str, inline=False)
        embed.add_field(
            name="Requisitos antifraude",
            value=(
                f"{'✅' if meets_age else '❌'} Cuenta de 3+ meses\n"
                f"{verif_str if role_id else '—'} Verificado\n"
                f"{'✅ Sí' if counted else '❌ No'} — Contó para el ranking/logro"
            ),
            inline=False,
        )
        embed.set_footer(text=f"Se unió: {record['joined_at']}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Invites(bot))
