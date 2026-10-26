"""
Cog: Staff List — Lista automática de Staff para MineOrbit Network.

Mantiene un único Embed (editado periódicamente, nunca reenviado) en un
canal configurado, agrupando a los miembros del servidor que tengan
alguno de los rangos de Staff configurados, ordenados por jerarquía.

DISEÑO / DECISIONES:

  - Los rangos de Staff se identifican SIEMPRE por role_id, nunca por
    nombre (ver database.py: staff_list_roles). Esto permite renombrar
    roles en Discord o reutilizar el mismo bot en varios servidores con
    nombres de rol distintos sin romper la configuración.

  - Un miembro con varios rangos de Staff aparece SOLO bajo el de mayor
    jerarquía (menor valor de `priority`), nunca duplicado. Se resuelve
    comparando `priority` entre todos los rangos configurados que el
    miembro posea (ver _collect_staff_groups).

  - El intervalo de actualización es configurable POR SERVIDOR
    (staff_list_config.update_interval), así que no se puede usar un
    simple @tasks.loop(seconds=N) de intervalo fijo para todos los
    guilds. En su lugar, un único loop corre cada 10s y decide, guild
    por guild, si ya venció su propio intervalo (_next_update) o si fue
    marcado "sucio" por un evento de rol (_dirty) — ver staff_list_loop.
    10s es también el piso permitido para `intervalo_segundos` en
    /staff setup, para evitar ediciones de mensaje demasiado frecuentes
    (rate limits de Discord).

  - Los eventos on_member_update / on_member_join / on_member_remove NO
    disparan una edición inmediata del embed (evitaría ráfagas de
    ediciones si muchos roles cambian a la vez, ej. un role sync masivo).
    Solo marcan el guild como "sucio"; el loop de 10s se encarga de
    aplicar el cambio en su próximo tick. El intervalo periódico normal
    sigue funcionando igual como red de seguridad/sincronización.

  - Paginación: cada rango con miembros genera uno o más "fields" de
    Embed (si supera ~1000 caracteres se parte en "(cont.)"). Los fields
    se agrupan en páginas respetando el límite real de Discord (25
    fields, 6000 caracteres por embed) con margen de seguridad. Si hay
    una sola página, no se adjunta ninguna vista (sin botones), tal
    como pide la especificación.

  - La vista de paginación (StaffListPagerView) NO es persistente entre
    reinicios del bot: guarda las páginas ya renderizadas en memoria de
    la propia instancia de la View. Como el embed se re-genera y
    re-envía con una View nueva en cada actualización periódica, esto
    es aceptable — en el peor caso, tras un reinicio del bot los
    botones de la última versión del mensaje no responden hasta el
    siguiente ciclo de actualización, que los reemplaza por unos
    funcionales.

  - El thumbnail usa el ícono del propio servidor de Discord (guild.icon)
    como aproximación práctica del "logo de MineOrbit": el bot sirve a
    un único ecosistema de servidores de MineOrbit, así que el ícono del
    guild ES el logo. No se agregó un campo de configuración aparte
    porque el JSON de persistencia mínimo pedido en la especificación
    no lo incluye.

  - Los comandos /staff usan el mismo check de permisos que ya usan los
    comandos administrativos de tickets (@checks.is_admin() — admin de
    Discord o el admin_role configurable de guild_config), en vez de
    reinventar un sistema de permisos separado.
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone
from database import db
from utils import checks
from config import COLOR_TICKET, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING

# ── Constantes ─────────────────────────────────────────────────────────────────

MIN_UPDATE_INTERVAL = 10   # segundos — piso duro, protege contra rate limits
LOOP_TICK_SECONDS    = 10  # cada cuánto el loop revisa qué guilds están vencidos
MAX_FIELD_CHARS       = 1000  # margen bajo el límite real de 1024 por field
MAX_FIELDS_PER_PAGE   = 20    # margen bajo el límite real de 25 fields/embed
MAX_PAGE_CHARS        = 5000  # margen bajo el límite real de 6000 chars/embed

STATUS_EMOJIS = {
    discord.Status.online:    "🟢",
    discord.Status.idle:      "🌙",
    discord.Status.dnd:       "🔴",
    discord.Status.offline:   "⚫",
    discord.Status.invisible: "⚫",
}

EMBED_TITLE = "🌌 MINEORBIT — STAFF"
EMBED_DESCRIPTION = (
    "Equipo oficial de MineOrbit encargado de mantener y administrar la comunidad."
)


# ── Helpers de formateo ───────────────────────────────────────────────────────

def _fmt_member_line(member: discord.Member, show_status: bool) -> str:
    if show_status:
        emoji = STATUS_EMOJIS.get(member.status, "⚫")
        return f"{emoji} {member.mention}"
    return f"• {member.mention}"


def _chunk_lines(lines: list[str], max_chars: int = MAX_FIELD_CHARS) -> list[str]:
    """Junta líneas con '\\n' sin superar max_chars, sin cortar una línea
    a la mitad (evitar romper una mención de Discord)."""
    if not lines:
        return [""]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        add_len = len(line) + (1 if current else 0)
        if current and current_len + add_len > max_chars:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += add_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def _paginate_fields(fields: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Agrupa fields (name, value) en páginas respetando los límites de
    Discord (con margen de seguridad) para fields por embed y caracteres
    totales por embed."""
    pages: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_chars = 0
    for name, value in fields:
        field_len = len(name) + len(value)
        if current and (
            len(current) >= MAX_FIELDS_PER_PAGE
            or current_chars + field_len > MAX_PAGE_CHARS
        ):
            pages.append(current)
            current = []
            current_chars = 0
        current.append((name, value))
        current_chars += field_len
    if current:
        pages.append(current)
    return pages or [[]]


# ── Vista de paginación ────────────────────────────────────────────────────────

class StaffListPagerView(discord.ui.View):
    """Botones ◀ Anterior / ▶ Siguiente. Ver nota de diseño en el header
    del módulo sobre por qué esta View no es persistente entre reinicios."""

    def __init__(self, pages: list[discord.Embed], current: int = 0):
        super().__init__(timeout=600)
        self.pages = pages
        self.current = current
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.prev_btn.disabled = self.current <= 0
        self.next_btn.disabled = self.current >= len(self.pages) - 1

    @discord.ui.button(label="◀ Anterior", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.current = max(0, self.current - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)

    @discord.ui.button(label="▶ Siguiente", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.current = min(len(self.pages) - 1, self.current + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        # No hay mensaje guardado en la View para editar tras el timeout;
        # se deja que la próxima actualización periódica reemplace todo
        # el mensaje (embed + view) de todos modos.


# ── Cog ───────────────────────────────────────────────────────────────────────

class StaffListCog(commands.GroupCog, name="staff", description="Sistema de lista automática de Staff"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Guilds que cambiaron desde la última actualización (por un
        # evento de rol/miembro) y deben refrescarse en el próximo tick,
        # sin esperar a que venza su intervalo configurado.
        self._dirty: set[int] = set()
        # Próximo momento (UTC) en que cada guild debe refrescarse según
        # su propio update_interval configurado.
        self._next_update: dict[int, datetime] = {}
        self.staff_list_loop.start()

    def cog_unload(self):
        self.staff_list_loop.cancel()

    # ── Lógica compartida: detección + agrupación ─────────────────────────────

    async def _collect_staff_groups(
        self, guild: discord.Guild, roles_config: list[dict],
    ) -> tuple[dict[int, list[discord.Member]], int, int, int]:
        """Recorre los miembros del guild y los agrupa por su rango de
        Staff de mayor jerarquía. Devuelve (groups, total, online, offline).

        Un rol configurado que ya no existe en el servidor se ignora sin
        detener el resto del sistema (manejo de error de la spec)."""
        valid_roles = {rc["role_id"]: rc for rc in roles_config if guild.get_role(rc["role_id"])}
        groups: dict[int, list[discord.Member]] = {rid: [] for rid in valid_roles}
        total = online = offline = 0

        for member in guild.members:
            if member.bot:
                continue
            matched: dict | None = None
            member_role_ids = {r.id for r in member.roles}
            for rid, rc in valid_roles.items():
                if rid in member_role_ids and (matched is None or rc["priority"] < matched["priority"]):
                    matched = rc
            if matched is None:
                continue
            groups[matched["role_id"]].append(member)
            total += 1
            if member.status in (discord.Status.offline, discord.Status.invisible):
                offline += 1
            else:
                online += 1

        return groups, total, online, offline

    async def _build_staff_pages(self, guild: discord.Guild) -> list[discord.Embed]:
        config = await db.get_staff_list_config(guild.id)
        roles_config = await db.get_staff_list_roles(guild.id)
        show_status = bool(config.get("show_status"))

        groups, total, online, offline = await self._collect_staff_groups(guild, roles_config)

        fields: list[tuple[str, str]] = []
        for rc in roles_config:
            members = groups.get(rc["role_id"])
            if not members:
                continue  # rangos sin miembros no se muestran, para no ensuciar el embed
            header = f"{rc['emoji']} {rc['name'].upper()}"
            lines = [_fmt_member_line(m, show_status) for m in members]
            for i, chunk in enumerate(_chunk_lines(lines)):
                name = header if i == 0 else f"{header} (cont.)"
                fields.append((name, chunk))

        if not fields:
            fields = [(
                "Sin Staff detectado",
                "No hay miembros con rangos de Staff configurados en este servidor.",
            )]

        pages_fields = _paginate_fields(fields)
        total_pages = len(pages_fields)

        embeds: list[discord.Embed] = []
        for idx, page_fields in enumerate(pages_fields, start=1):
            description = EMBED_DESCRIPTION
            if total_pages > 1:
                description += f"\n\n**Página {idx}/{total_pages}**"
            embed = discord.Embed(
                title=EMBED_TITLE, description=description, color=COLOR_TICKET,
            )
            for name, value in page_fields:
                embed.add_field(name=name, value=value, inline=False)

            footer_parts = [f"👥 Staff total: {total}"]
            if show_status:
                footer_parts.append(f"🟢 Online: {online}   ⚫ Offline: {offline}")
            footer_parts.append("🔄 Última actualización")
            embed.set_footer(text="\n".join(footer_parts))
            embed.timestamp = datetime.now(timezone.utc)

            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)

            embeds.append(embed)

        return embeds

    # ── Actualización del mensaje ──────────────────────────────────────────────

    async def _update_guild_staff_list(self, guild: discord.Guild) -> None:
        try:
            config = await db.get_staff_list_config(guild.id)
            if not config.get("enabled"):
                return
            channel_id = config.get("channel_id")
            if not channel_id:
                return
            channel = guild.get_channel(channel_id)
            if channel is None:
                print(f"[StaffListCog] ⚠️ Canal configurado no encontrado (guild {guild.id}).")
                return

            pages = await self._build_staff_pages(guild)
            view = StaffListPagerView(pages) if len(pages) > 1 else None

            message: discord.Message | None = None
            message_id = config.get("message_id")
            if message_id:
                try:
                    message = await channel.fetch_message(message_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    message = None  # el mensaje ya no existe (o no se pudo leer) — se recrea abajo

            try:
                if message:
                    await message.edit(embed=pages[0], view=view)
                else:
                    message = await channel.send(embed=pages[0], view=view)
                    await db.set_staff_list_config(guild.id, message_id=message.id)
            except discord.Forbidden:
                print(f"[StaffListCog] ❌ Sin permisos para actualizar el Embed (guild {guild.id}).")
            except Exception as e:
                print(f"[StaffListCog] error editando/enviando el embed (guild {guild.id}): {e}")
        except Exception as e:
            # Red de seguridad: un fallo en un guild no debe tumbar el loop
            # ni afectar la actualización de los demás guilds.
            print(f"[StaffListCog] error inesperado actualizando staff list (guild {guild.id}): {e}")

    # ── Loop de actualización periódica ───────────────────────────────────────

    @tasks.loop(seconds=LOOP_TICK_SECONDS)
    async def staff_list_loop(self):
        try:
            now = datetime.utcnow()
            for guild in self.bot.guilds:
                try:
                    config = await db.get_staff_list_config(guild.id)
                    if not config.get("enabled"):
                        continue
                    interval = max(int(config.get("update_interval") or 60), MIN_UPDATE_INTERVAL)
                    due = self._next_update.get(guild.id)
                    is_dirty = guild.id in self._dirty
                    if is_dirty or due is None or now >= due:
                        await self._update_guild_staff_list(guild)
                        self._next_update[guild.id] = now + timedelta(seconds=interval)
                        self._dirty.discard(guild.id)
                except Exception as e:
                    print(f"[StaffListCog] error en tick del loop (guild {guild.id}): {e}")
        except Exception as e:
            print(f"[StaffListCog] staff_list_loop error: {e}")

    @staff_list_loop.before_loop
    async def before_staff_list_loop(self):
        await self.bot.wait_until_ready()

    # ── Listeners: cambios que ensucian el estado (ver nota de diseño) ────────

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles != after.roles:
            self._dirty.add(after.guild.id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        self._dirty.add(member.guild.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        self._dirty.add(member.guild.id)

    # ── /staff setup ───────────────────────────────────────────────────────────

    @app_commands.command(name="setup", description="Configurar el sistema de Staff List")
    @app_commands.describe(
        canal="Canal donde se publicará y mantendrá el Embed de Staff",
        intervalo_segundos=f"Cada cuántos segundos se actualiza el Embed (mínimo {MIN_UPDATE_INTERVAL})",
        mostrar_estado="Mostrar el estado online/idle/dnd/offline de cada miembro",
    )
    @checks.is_admin()
    async def staff_setup(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
        intervalo_segundos: app_commands.Range[int, MIN_UPDATE_INTERVAL, 3600] = 60,
        mostrar_estado: bool = False,
    ):
        await db.set_staff_list_config(
            interaction.guild_id,
            channel_id=canal.id,
            update_interval=intervalo_segundos,
            show_status=1 if mostrar_estado else 0,
            enabled=1,
        )
        # Forzar actualización en el próximo tick del loop (hasta 10s),
        # en vez de esperar el intervalo completo recién configurado.
        self._dirty.add(interaction.guild_id)

        embed = discord.Embed(
            description=(
                f"✅ Staff List configurado en {canal.mention}.\n"
                f"⏱️ Intervalo: **{intervalo_segundos}s**\n"
                f"🟢 Mostrar estado: **{'Sí' if mostrar_estado else 'No'}**"
            ),
            color=COLOR_SUCCESS,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /staff addrole ─────────────────────────────────────────────────────────

    @app_commands.command(name="addrole", description="Agregar (o reconfigurar) un rango de Staff")
    @app_commands.describe(
        role="Rol de Discord que representa el rango de Staff",
        nombre="Nombre a mostrar (por defecto: el nombre del rol de Discord)",
        emoji="Emoji a mostrar junto al rango",
        posicion="Posición en la jerarquía — menor número = rango más alto. Si se omite, va al final.",
    )
    @checks.is_admin()
    async def staff_addrole(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        nombre: str = None,
        emoji: str = "🛡️",
        posicion: int = None,
    ):
        await interaction.response.defer(ephemeral=True)
        display_name = nombre or role.name
        await db.add_staff_list_role(interaction.guild_id, role.id, display_name, emoji, posicion)
        self._dirty.add(interaction.guild_id)

        embed = discord.Embed(
            description=f"✅ Rango configurado: {emoji} **{display_name}** ({role.mention}).",
            color=COLOR_SUCCESS,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /staff removerole ──────────────────────────────────────────────────────

    @app_commands.command(name="removerole", description="Eliminar un rango de Staff de la lista")
    @app_commands.describe(role="Rol de Discord a eliminar del sistema de Staff List")
    @checks.is_admin()
    async def staff_removerole(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        removed = await db.remove_staff_list_role(interaction.guild_id, role.id)
        if not removed:
            return await interaction.followup.send(
                f"❌ {role.mention} no está configurado como rango de Staff.", ephemeral=True
            )
        self._dirty.add(interaction.guild_id)

        embed = discord.Embed(
            description=f"🗑️ Rango eliminado: {role.mention}.", color=COLOR_WARNING,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /staff roles ───────────────────────────────────────────────────────────

    @app_commands.command(name="roles", description="Mostrar todos los rangos de Staff configurados")
    @checks.is_admin()
    async def staff_roles(self, interaction: discord.Interaction):
        roles_config = await db.get_staff_list_roles(interaction.guild_id, enabled_only=False)
        if not roles_config:
            return await interaction.response.send_message(
                "❌ No hay rangos de Staff configurados todavía. Usa `/staff addrole`.",
                ephemeral=True,
            )

        lines = []
        for rc in roles_config:
            role = interaction.guild.get_role(rc["role_id"])
            role_text = role.mention if role else f"⚠️ Rol eliminado (`{rc['role_id']}`)"
            lines.append(f"{rc['emoji']} **{rc['name']}** — {role_text}")

        embed = discord.Embed(
            title="🌌 MineOrbit Staff Roles",
            description="\n".join(lines),
            color=COLOR_TICKET,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /staff update ──────────────────────────────────────────────────────────

    @app_commands.command(name="update", description="Forzar una actualización inmediata del Staff List")
    @checks.is_admin()
    async def staff_update(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        config = await db.get_staff_list_config(interaction.guild_id)
        if not config.get("enabled"):
            return await interaction.followup.send(
                "❌ El sistema de Staff List no está configurado. Usa `/staff setup` primero.",
                ephemeral=True,
            )

        await self._update_guild_staff_list(interaction.guild)

        interval = max(int(config.get("update_interval") or 60), MIN_UPDATE_INTERVAL)
        self._next_update[interaction.guild_id] = datetime.utcnow() + timedelta(seconds=interval)
        self._dirty.discard(interaction.guild_id)

        await interaction.followup.send("✅ Staff List actualizado.", ephemeral=True)

    # ── /staff status ──────────────────────────────────────────────────────────

    @app_commands.command(name="status", description="Ver el estado del sistema de Staff List")
    @checks.is_admin()
    async def staff_status(self, interaction: discord.Interaction):
        config = await db.get_staff_list_config(interaction.guild_id)
        roles_config = await db.get_staff_list_roles(interaction.guild_id)
        channel = (
            interaction.guild.get_channel(config["channel_id"])
            if config.get("channel_id") else None
        )

        _, total, online, offline = await self._collect_staff_groups(interaction.guild, roles_config)

        embed = discord.Embed(title="🌌 MineOrbit Staff System", color=COLOR_TICKET)
        embed.add_field(
            name="Estado",
            value="🟢 Activo" if config.get("enabled") else "🔴 Inactivo",
            inline=False,
        )
        embed.add_field(name="Canal", value=channel.mention if channel else "❌ No configurado", inline=True)
        embed.add_field(name="Intervalo", value=f"{config.get('update_interval') or 60} segundos", inline=True)
        embed.add_field(name="Rangos configurados", value=str(len(roles_config)), inline=True)
        embed.add_field(name="Staff detectado", value=str(total), inline=True)
        if config.get("show_status"):
            embed.add_field(name="Online", value=str(online), inline=True)
            embed.add_field(name="Offline", value=str(offline), inline=True)
        if config.get("updated_at"):
            embed.add_field(name="Última configuración", value=config["updated_at"], inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(StaffListCog(bot))