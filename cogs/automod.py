"""
Cog: AutoMod
- Anti-spam (rate limiting per user)
- Anti-mass mention
- Anti-caps
- Anti-links / malicious domains
- Anti-raid (join rate detection)
- Anti-bot
- Configurable actions (warn/delete/timeout/kick/ban)
- Exempt roles & channels

"""

import re
import json
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from collections import defaultdict, deque
from datetime import datetime, timedelta
from database import db
from utils import checks
from config import (
    AUTOMOD_SPAM_THRESHOLD, AUTOMOD_SPAM_WINDOW,
    AUTOMOD_MENTION_LIMIT, AUTOMOD_CAPS_THRESHOLD,
    MALICIOUS_DOMAINS, COLOR_ERROR, COLOR_WARNING,
)

# ── Trackers en memoria ───────────────────────────────────────────────────────

# {guild_id: {user_id: deque(timestamps)}}
_spam_tracker: dict[int, dict[int, deque]] = defaultdict(lambda: defaultdict(deque))

# FIX 3: locks por (guild_id, user_id) para evitar race condition en spam
_spam_locks: dict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)

# {guild_id: deque(timestamps)}
_join_tracker: dict[int, deque] = defaultdict(deque)

RAID_THRESHOLD = 10  # joins en 10 segundos


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_domains(text: str) -> list[str]:
    """Extrae hostnames de URLs con esquema http(s)://."""
    return re.findall(r"https?://([^\s/]+)", text)


def _is_malicious_domain(domain: str) -> bool:
    """FIX 6: comparación exacta o por sufijo con punto, no substring libre.
    Evita que 'nitro.com' en MALICIOUS_DOMAINS bloquee 'legit-nitro.com.mysite.org'."""
    domain = domain.lower()
    for bad in MALICIOUS_DOMAINS:
        bad = bad.lower()
        if domain == bad or domain.endswith("." + bad):
            return True
    return False


def _caps_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 10:
        return 0.0
    return sum(1 for c in letters if c.isupper()) / len(letters)


async def _automod_action(
    guild: discord.Guild,
    member: discord.Member,
    message: discord.Message,
    action: str,
    reason: str,
    log_channel: int = None,
):
    try:
        await message.delete()
    except Exception:
        pass

    # FIX 7: capturar Forbidden explícitamente y notificar al canal de log
    async def _try(coro, label: str):
        try:
            await coro
        except discord.Forbidden:
            if log_channel:
                ch = guild.get_channel(log_channel)
                if ch:
                    try:
                        await ch.send(
                            f"⚠️ **AutoMod — Sin permisos:** no pude ejecutar `{label}` "
                            f"sobre {member.mention}. Revisa la jerarquía de roles del bot."
                        )
                    except Exception:
                        pass
        except Exception:
            pass

    if action == "warn":
        await db.add_warning(guild.id, member.id, guild.me.id, reason)
        try:
            await message.channel.send(
                f"⚠️ {member.mention} Advertencia automática: {reason}",
                delete_after=8,
            )
        except Exception:
            pass

    elif action == "timeout":
        await _try(
            member.timeout(timedelta(hours=1), reason=reason),
            "timeout",
        )
        try:
            await message.channel.send(
                f"🔇 {member.mention} ha sido silenciado por **1 hora** por AutoMod: {reason}",
                delete_after=10,
            )
        except Exception:
            pass

    elif action == "kick":
        await _try(guild.kick(member, reason=reason), "kick")

    elif action == "ban":
        await _try(guild.ban(member, reason=reason), "ban")

    # Log al canal de AutoMod
    if log_channel:
        ch = guild.get_channel(log_channel)
        if ch:
            embed = discord.Embed(
                title=f"🤖 AutoMod — {action.upper()}",
                description=(
                    f"**Usuario:** {member.mention}\n"
                    f"**Razón:** {reason}\n"
                    f"**Canal:** {message.channel.mention}"
                ),
                color=COLOR_ERROR,
            )
            embed.set_footer(text=f"ID: {member.id}")
            try:
                await ch.send(embed=embed)
            except Exception:
                pass


# ── Cog ───────────────────────────────────────────────────────────────────────

class AutoModCog(commands.Cog, name="AutoMod"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._raid_notified: set[int] = set()

    # FIX 1: tarea separada para resetear el flag de raid
    async def _reset_raid_flag(self, guild_id: int):
        await asyncio.sleep(60)
        self._raid_notified.discard(guild_id)

    async def _is_exempt(
        self,
        config: dict,
        member: discord.Member,
        channel: discord.TextChannel,
    ) -> bool:
        if member.guild_permissions.manage_messages:
            return True
        exempt_roles    = json.loads(config.get("exempt_roles")    or "[]")
        exempt_channels = json.loads(config.get("exempt_channels") or "[]")
        if channel.id in exempt_channels:
            return True
        if any(r.id in exempt_roles for r in member.roles):
            return True
        return False

    # FIX 4: limpiar trackers cuando el bot es kickeado del servidor
    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        _spam_tracker.pop(guild.id, None)
        _join_tracker.pop(guild.id, None)
        # Limpiar locks de ese guild
        keys_to_del = [k for k in _spam_locks if k[0] == guild.id]
        for k in keys_to_del:
            _spam_locks.pop(k, None)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        member = message.author
        guild  = message.guild
        config = await db.get_automod_config(guild.id)

        if await self._is_exempt(config, member, message.channel):
            return

        log_ch  = config.get("log_channel")
        content = message.content or ""

        # FIX 5: extraer dominios una sola vez y reusar en ambos bloques
        domains = _extract_domains(content)

        # ── Anti-spam ─────────────────────────────────────────────────────
        if config.get("anti_spam"):
            # FIX 3: lock por (guild, user) para evitar race condition
            async with _spam_locks[(guild.id, member.id)]:
                now     = datetime.utcnow().timestamp()
                tracker = _spam_tracker[guild.id][member.id]
                tracker.append(now)
                while tracker and now - tracker[0] > AUTOMOD_SPAM_WINDOW:
                    tracker.popleft()
                if len(tracker) >= AUTOMOD_SPAM_THRESHOLD:
                    tracker.clear()
                    triggered = True
                else:
                    triggered = False

            if triggered:
                # FIX 9: spam siempre aplica timeout de 2 horas
                reason = f"Spam detectado ({AUTOMOD_SPAM_THRESHOLD} mensajes en {AUTOMOD_SPAM_WINDOW}s)"
                try:
                    await message.delete()
                except Exception:
                    pass
                try:
                    await member.timeout(timedelta(hours=2), reason=reason)
                    await message.channel.send(
                        f"🔇 {member.mention} ha sido silenciado por **2 horas** por AutoMod: {reason}",
                        delete_after=10,
                    )
                except discord.Forbidden:
                    if log_ch:
                        ch = guild.get_channel(log_ch)
                        if ch:
                            try:
                                await ch.send(
                                    f"⚠️ **AutoMod — Sin permisos:** no pude aplicar timeout de 2h "
                                    f"a {member.mention}. Revisa la jerarquía de roles del bot."
                                )
                            except Exception:
                                pass
                except Exception:
                    pass
                if log_ch:
                    ch = guild.get_channel(log_ch)
                    if ch:
                        embed = discord.Embed(
                            title="🤖 AutoMod — SPAM (TIMEOUT 2H)",
                            description=(
                                f"**Usuario:** {member.mention}\n"
                                f"**Razón:** {reason}\n"
                                f"**Canal:** {message.channel.mention}"
                            ),
                            color=COLOR_ERROR,
                        )
                        embed.set_footer(text=f"ID: {member.id}")
                        try:
                            await ch.send(embed=embed)
                        except Exception:
                            pass
                return

        # ── Anti-mass mention ─────────────────────────────────────────────
        if config.get("anti_mentions"):
            mention_count = len(message.mentions) + len(message.role_mentions)
            if mention_count >= AUTOMOD_MENTION_LIMIT:
                await _automod_action(
                    guild, member, message, "timeout",
                    f"Mención masiva ({mention_count} menciones)",
                    log_ch,
                )
                return

        # ── Anti-links ────────────────────────────────────────────────────
        if config.get("anti_links") and domains:
            await _automod_action(
                guild, member, message,
                config.get("link_action") or "delete",
                "Links no permitidos",
                log_ch,
            )
            return

        # ── Dominios maliciosos (independiente de anti_links) ─────────────
        # FIX 5: reutiliza `domains` ya extraído arriba
        # FIX 6: comparación exacta/sufijo en lugar de substring libre
        for d in domains:
            if _is_malicious_domain(d):
                await _automod_action(
                    guild, member, message, "ban",
                    f"Dominio malicioso/phishing: {d}",
                    log_ch,
                )
                return

        # ── Anti-caps ─────────────────────────────────────────────────────
        if config.get("anti_caps") and len(content) > 20:
            if _caps_ratio(content) >= AUTOMOD_CAPS_THRESHOLD:
                await _automod_action(
                    guild, member, message, "timeout",
                    "Exceso de mayúsculas",
                    log_ch,
                )
                return

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild  = member.guild
        config = await db.get_automod_config(guild.id)

        # ── Anti-bot ──────────────────────────────────────────────────────
        if config.get("anti_bot") and member.bot:
            # FIX 2: respetar whitelist de bots antes de banear
            whitelist = json.loads(config.get("bot_whitelist") or "[]")
            if member.id in whitelist:
                return

            try:
                await guild.ban(member, reason="AutoMod: bot no autorizado")
            except discord.Forbidden:
                if config.get("log_channel"):
                    ch = guild.get_channel(config["log_channel"])
                    if ch:
                        try:
                            await ch.send(
                                f"⚠️ **AutoMod — Sin permisos:** intenté banear el bot "
                                f"**{member}** (`{member.id}`) pero no tengo permisos suficientes."
                            )
                        except Exception:
                            pass
            except Exception:
                pass
            else:
                if config.get("log_channel"):
                    ch = guild.get_channel(config["log_channel"])
                    if ch:
                        try:
                            await ch.send(embed=discord.Embed(
                                title="🤖 Bot bloqueado",
                                description=f"Se bloqueó el bot **{member}** (`{member.id}`).",
                                color=COLOR_ERROR,
                            ))
                        except Exception:
                            pass
            return

        # ── Anti-raid ─────────────────────────────────────────────────────
        if config.get("anti_raid"):
            now     = datetime.utcnow().timestamp()
            tracker = _join_tracker[guild.id]
            tracker.append(now)
            while tracker and now - tracker[0] > 10:
                tracker.popleft()
            if len(tracker) >= RAID_THRESHOLD and guild.id not in self._raid_notified:
                self._raid_notified.add(guild.id)
                log_ch = config.get("log_channel")
                if log_ch:
                    ch = guild.get_channel(log_ch)
                    if ch:
                        embed = discord.Embed(
                            title="🚨 RAID DETECTADO",
                            description=(
                                f"**{len(tracker)} usuarios** se unieron en los últimos 10 segundos.\n"
                                "Considera activar el modo de verificación del servidor."
                            ),
                            color=COLOR_ERROR,
                        )
                        try:
                            await ch.send(content="@here ⚠️", embed=embed)
                        except Exception:
                            pass
                # FIX 1: tarea separada en vez de sleep dentro del handler
                asyncio.create_task(self._reset_raid_flag(guild.id))

    # ── Comandos de configuración ─────────────────────────────────────────────

    automod_group = app_commands.Group(name="automod", description="Configurar AutoMod")

    @automod_group.command(name="config", description="Configurar AutoMod")
    @app_commands.describe(
        anti_spam="Activar anti-spam (timeout 2h al detectar spam)",
        anti_links="Eliminar links automáticamente",
        anti_menciones="Limitar menciones masivas",
        anti_caps="Limitar uso de mayúsculas",
        anti_raid="Detectar raids",
        anti_bot="Bloquear bots no autorizados",
        canal_log="Canal de logs de AutoMod",
    )
    @checks.is_owner()
    async def automod_config(
        self,
        interaction: discord.Interaction,
        anti_spam: bool = None,
        anti_links: bool = None,
        anti_menciones: bool = None,
        anti_caps: bool = None,
        anti_raid: bool = None,
        anti_bot: bool = None,
        canal_log: discord.TextChannel = None,
    ):
        await interaction.response.defer(ephemeral=True)
        kwargs = {}
        if anti_spam      is not None: kwargs["anti_spam"]     = int(anti_spam)
        if anti_links     is not None: kwargs["anti_links"]    = int(anti_links)
        if anti_menciones is not None: kwargs["anti_mentions"] = int(anti_menciones)
        if anti_caps      is not None: kwargs["anti_caps"]     = int(anti_caps)
        if anti_raid      is not None: kwargs["anti_raid"]     = int(anti_raid)
        if anti_bot       is not None: kwargs["anti_bot"]      = int(anti_bot)
        if canal_log:                  kwargs["log_channel"]   = canal_log.id

        # FIX 8: si no se pasó ningún parámetro, avisar en lugar de
        # enviar un embed vacío con título "configurado"
        if not kwargs:
            return await interaction.followup.send(
                "ℹ️ No se cambió nada. Pasa al menos un parámetro para configurar el AutoMod.",
                ephemeral=True,
            )

        await db.update_automod_config(interaction.guild_id, **kwargs)

        embed = discord.Embed(title="🤖 AutoMod configurado", color=0x57F287)
        for k, v in kwargs.items():
            embed.add_field(name=k, value="✅" if v else "❌", inline=True)
        await interaction.followup.send(embed=embed)

    @automod_group.command(name="status", description="Ver estado del AutoMod")
    @checks.is_staff()
    async def automod_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        config = await db.get_automod_config(interaction.guild_id)
        embed  = discord.Embed(title="🤖 Estado de AutoMod", color=0x5865F2)
        checks_map = {
            "Anti-Spam (timeout 2h)": "anti_spam",
            "Anti-Links":             "anti_links",
            "Anti-Menciones":         "anti_mentions",
            "Anti-Caps":              "anti_caps",
            "Anti-Raid":              "anti_raid",
            "Anti-Bot":               "anti_bot",
        }
        for label, key in checks_map.items():
            val = config.get(key)
            embed.add_field(name=label, value="✅ Activo" if val else "❌ Inactivo", inline=True)
        log_ch = config.get("log_channel")
        embed.add_field(
            name="Canal de log",
            value=f"<#{log_ch}>" if log_ch else "No configurado",
            inline=False,
        )
        await interaction.followup.send(embed=embed)

    @automod_group.command(name="exempt_role", description="Eximir un rol del AutoMod")
    @app_commands.describe(rol="Rol a eximir")
    @checks.is_owner()
    async def automod_exempt_role(self, interaction: discord.Interaction, rol: discord.Role):
        await interaction.response.defer(ephemeral=True)
        config = await db.get_automod_config(interaction.guild_id)
        exempt = json.loads(config.get("exempt_roles") or "[]")
        if rol.id in exempt:
            exempt.remove(rol.id)
            msg = f"Rol {rol.mention} **eliminado** de exenciones."
        else:
            exempt.append(rol.id)
            msg = f"Rol {rol.mention} **añadido** a exenciones."
        await db.update_automod_config(interaction.guild_id, exempt_roles=json.dumps(exempt))
        await interaction.followup.send(f"✅ {msg}")

    # FIX 2: comando para gestionar la whitelist de bots
    @automod_group.command(name="bot_whitelist", description="Añadir/quitar un bot de la whitelist de anti-bot")
    @app_commands.describe(bot_id="ID del bot a añadir o quitar de la whitelist")
    @checks.is_owner()
    async def automod_bot_whitelist(self, interaction: discord.Interaction, bot_id: str):
        await interaction.response.defer(ephemeral=True)
        try:
            bid = int(bot_id)
        except ValueError:
            return await interaction.followup.send("❌ ID inválido.")

        config    = await db.get_automod_config(interaction.guild_id)
        whitelist = json.loads(config.get("bot_whitelist") or "[]")
        if bid in whitelist:
            whitelist.remove(bid)
            msg = f"Bot `{bid}` **eliminado** de la whitelist."
        else:
            whitelist.append(bid)
            msg = f"Bot `{bid}` **añadido** a la whitelist. No será baneado por anti-bot."
        await db.update_automod_config(interaction.guild_id, bot_whitelist=json.dumps(whitelist))
        await interaction.followup.send(f"✅ {msg}")


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoModCog(bot))