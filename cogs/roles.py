"""
Cog: Reaction Roles / Auto Roles
- /reactionrole add    — bind an emoji on a message to a role
- /reactionrole remove — remove a binding
- /reactionrole list   — list all bindings
- /reactionrole panel  — create a managed role-selection panel
- Listens on raw reaction add/remove events (works after restart)

"""

import discord
from discord import app_commands
from discord.ext import commands
from database import db
from utils import checks
from config import COLOR_SUCCESS, COLOR_ERROR, COLOR_INFO, COLOR_PRIMARY


def _normalize_emoji(emoji: str) -> str:
    return emoji.replace("\ufe0f", "")


# ── Panel View ────────────────────────────────────────────────────────────────

class RolesPanelView(discord.ui.View):
    """Select-menu persistente para un panel de roles."""

    def __init__(self, options: list[tuple[str, str, int]]):
        # options: [(label, emoji, role_id), ...]
        super().__init__(timeout=None)
        select_options = [
            discord.SelectOption(
                label=label,
                emoji=emoji,
                value=str(role_id),
            )
            for label, emoji, role_id in options[:25]
        ]
        select = discord.ui.Select(
            placeholder="Selecciona un rol…",
            options=select_options,
            min_values=0,
            max_values=min(len(select_options), 5),
            custom_id="roles_panel_select",
        )
        select.callback = self._select_callback
        self.add_item(select)

    async def _select_callback(self, interaction: discord.Interaction):
        select         = self.children[0]
        chosen_role_ids = [int(v) for v in select.values]
        all_role_ids    = [int(o.value) for o in select.options]

        added, removed = [], []
        for rid in all_role_ids:
            role = interaction.guild.get_role(rid)
            if not role:
                continue
            if rid in chosen_role_ids:
                if role not in interaction.user.roles:
                    try:
                        await interaction.user.add_roles(role, reason="Panel de roles")
                        added.append(role.mention)
                    except discord.Forbidden:
                        pass
            else:
                if role in interaction.user.roles:
                    try:
                        await interaction.user.remove_roles(role, reason="Panel de roles")
                        removed.append(role.mention)
                    except discord.Forbidden:
                        pass

        parts = []
        if added:
            parts.append(f"✅ Añadidos: {', '.join(added)}")
        if removed:
            parts.append(f"❌ Eliminados: {', '.join(removed)}")
        msg = "\n".join(parts) if parts else "Sin cambios."
        await interaction.response.send_message(msg, ephemeral=True)


class RolesCog(commands.Cog, name="Roles"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    rr_group = app_commands.Group(
        name="reactionrole", description="Roles por reacción"
    )

    # ── Add ───────────────────────────────────────────────────────────────────

    @rr_group.command(
        name="add", description="Vincular un emoji a un rol en un mensaje"
    )
    @app_commands.describe(
        message_id="ID del mensaje",
        canal="Canal donde está el mensaje",
        emoji="Emoji a usar",
        rol="Rol a asignar",
    )
    @checks.is_owner()
    async def rr_add(
        self,
        interaction: discord.Interaction,
        message_id: str,
        canal: discord.TextChannel,
        emoji: str,
        rol: discord.Role,
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            mid = int(message_id)
            msg = await canal.fetch_message(mid)
        except Exception:
            return await interaction.followup.send("❌ Mensaje no encontrado.")

        # FIX 3: normalizar emoji antes de guardar en DB
        emoji_normalized = _normalize_emoji(emoji)

        await db.add_reaction_role(
            interaction.guild_id, canal.id, mid, emoji_normalized, rol.id
        )

        try:
            await msg.add_reaction(emoji)
        except discord.HTTPException:
            return await interaction.followup.send(
                "✅ Vínculo guardado, pero no pude reaccionar con ese emoji "
                "(¿es personalizado y no está en el servidor?)"
            )

        await interaction.followup.send(
            f"✅ {emoji} → {rol.mention} vinculado en [ese mensaje]({msg.jump_url})."
        )

    # ── Remove ────────────────────────────────────────────────────────────────

    @rr_group.command(
        name="remove", description="Eliminar un vínculo de rol por reacción"
    )
    @app_commands.describe(id="ID del vínculo (ver /reactionrole list)")
    @checks.is_owner()
    async def rr_remove(self, interaction: discord.Interaction, id: int):
        await interaction.response.defer(ephemeral=True)
        await db.remove_reaction_role(id)
        await interaction.followup.send(f"✅ Vínculo **#{id}** eliminado.")

    # ── List ──────────────────────────────────────────────────────────────────

    @rr_group.command(name="list", description="Listar todos los vínculos de roles")
    @checks.is_mod()
    async def rr_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        rows = await db.get_reaction_roles(interaction.guild_id)
        if not rows:
            return await interaction.followup.send("No hay vínculos configurados.")
        embed = discord.Embed(title="🎭 Reaction Roles", color=COLOR_INFO)
        for r in rows[:25]:
            embed.add_field(
                name=f"#{r['id']} — {r['emoji']}",
                value=f"Rol: <@&{r['role_id']}>\nMensaje: <#{r['channel_id']}>",
                inline=True,
            )
        await interaction.followup.send(embed=embed)

    # ── Panel ─────────────────────────────────────────────────────────────────

    @rr_group.command(
        name="panel", description="Crear un panel de selección de roles"
    )
    @app_commands.describe(
        canal="Canal donde publicar",
        titulo="Título del panel",
        descripcion="Descripción del panel",
        roles_emojis=(
            "Lista: emoji:rol_id,emoji:rol_id… "
            "Soporta emojis Unicode (🎮:123) y custom (<:gg:456>:789)"
        ),
    )
    @checks.is_owner()
    async def rr_panel(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
        titulo: str,
        descripcion: str,
        roles_emojis: str,
    ):
        await interaction.response.defer(ephemeral=True)

        # FIX 2: parsear separando por el ÚLTIMO ":" de cada item para
        # soportar emojis custom con formato <:nombre:ID> que contienen
        # dos puntos internos.
        try:
            pairs = []
            for item in roles_emojis.split(","):
                item = item.strip()
                last_colon = item.rfind(":")
                if last_colon == -1:
                    raise ValueError(f"Item sin ':' separador: {item!r}")
                emoji_part   = item[:last_colon].strip()
                role_id_str  = item[last_colon + 1:].strip()
                role = interaction.guild.get_role(int(role_id_str))
                if role:
                    pairs.append((role.name, emoji_part, role.id))
            if not pairs:
                raise ValueError("Sin pares válidos")
        except Exception:
            return await interaction.followup.send(
                "❌ Formato inválido.\n"
                "Emojis Unicode: `🎮:123456789,🎵:987654321`\n"
                "Emojis custom:  `<:gg:111>:123456789,<a:wave:222>:987654321`"
            )

        embed = discord.Embed(
            title=f"🎭 {titulo}",
            description=descripcion + "\n\n" + "\n".join(
                f"{emoji} — <@&{rid}>" for _, emoji, rid in pairs
            ),
            color=COLOR_PRIMARY,
        )
        embed.set_footer(text="Selecciona roles del menú desplegable.")

        view = RolesPanelView(pairs)
        await canal.send(embed=embed, view=view)

        # FIX 1: registrar la View recién creada para que sobreviva reinicios
        self.bot.add_view(view)

        await interaction.followup.send(
            f"✅ Panel de roles publicado en {canal.mention}."
        )

    # ── Reaction listeners ────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        # FIX 3: normalizar emoji antes de comparar con lo guardado en DB
        emoji_str = _normalize_emoji(str(payload.emoji))
        rr = await db.get_reaction_role(payload.message_id, emoji_str)
        if not rr:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member:
            return
        role = guild.get_role(rr["role_id"])
        if role:
            try:
                await member.add_roles(role, reason="Reaction role")
            except discord.Forbidden:
                pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        # FIX 3: normalizar emoji antes de comparar con lo guardado en DB
        emoji_str = _normalize_emoji(str(payload.emoji))
        rr = await db.get_reaction_role(payload.message_id, emoji_str)
        if not rr:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member:
            return
        role = guild.get_role(rr["role_id"])
        if role:
            try:
                await member.remove_roles(role, reason="Reaction role removed")
            except discord.Forbidden:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(RolesCog(bot))

    # FIX 1: re-registrar RolesPanelView para todos los paneles activos
    # al arrancar el bot, leyendo sus opciones desde la DB.
    # Sin esto, el select menu deja de responder tras un reinicio.
    try:
        panels = await db.fetchall(
            "SELECT DISTINCT message_id, channel_id FROM reaction_roles "
            "WHERE panel=1 GROUP BY message_id"
        )
        for panel in panels:
            rows = await db.fetchall(
                "SELECT emoji, role_id, label FROM reaction_roles WHERE message_id=?",
                (panel["message_id"],),
            )
            if rows:
                options = [
                    (r.get("label") or f"Rol {r['role_id']}", r["emoji"], r["role_id"])
                    for r in rows
                ]
                bot.add_view(RolesPanelView(options))
    except Exception as e:
        print(f"[RolesCog] Error restaurando panel views: {e}")
