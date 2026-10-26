"""
Cog: Staff Tools — utilidades internas para el equipo de staff.

/whois  — resumen de un usuario en un solo embed: cuenta, roles,
          cumpleaños, advertencias, tickets y notas.
/nota   — notas internas de staff sobre un usuario (no son warnings
          oficiales, solo contexto para el equipo). Las respuestas son
          siempre ephemeral: solo las ve quien ejecuta el comando.
"""
import discord
from discord import app_commands
from discord.ext import commands, tasks
from database import db
from utils import checks
from config import COLOR_STAFF, COLOR_SUCCESS


class StaffToolsCog(commands.Cog, name="StaffTools"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.purge_notes_loop.start()

    def cog_unload(self):
        self.purge_notes_loop.cancel()

    # ── /whois ───────────────────────────────────────────────────────────────

    @app_commands.command(
        name="whois",
        description="[Staff] Ver el perfil completo de un usuario en un solo lugar",
    )
    @app_commands.describe(usuario="Usuario a consultar")
    @checks.is_staff()
    async def whois(self, interaction: discord.Interaction, usuario: discord.Member):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id

        warnings = await db.get_warnings(guild_id, usuario.id)
        warning_points = await db.get_warning_count(guild_id, usuario.id)
        tickets = await db.get_user_tickets(guild_id, usuario.id)
        notes = await db.get_staff_notes(guild_id, usuario.id)
        birthday = await db.get_birthday(usuario.id)

        open_tickets = sum(1 for t in tickets if t["status"] == "open")
        closed_tickets = sum(1 for t in tickets if t["status"] == "closed")

        roles = [r.mention for r in reversed(usuario.roles) if r.name != "@everyone"]
        roles_text = ", ".join(roles[:15]) if roles else "Ninguno"
        if len(roles) > 15:
            roles_text += f" *(+{len(roles) - 15} más)*"

        embed = discord.Embed(
            title=f"🔎 {usuario}",
            color=COLOR_STAFF,
        )
        embed.set_thumbnail(url=usuario.display_avatar.url)

        if usuario.joined_at:
            cuenta_value = (
                f"Creada: <t:{int(usuario.created_at.timestamp())}:R>\n"
                f"Se unió: <t:{int(usuario.joined_at.timestamp())}:R>"
            )
        else:
            cuenta_value = f"Creada: <t:{int(usuario.created_at.timestamp())}:R>"

        embed.add_field(name="📛 Cuenta", value=cuenta_value, inline=False)
        embed.add_field(name="🎭 Roles", value=roles_text, inline=False)

        embed.add_field(
            name="🎂 Cumpleaños",
            value=birthday["birthday"] if birthday else "No registrado",
            inline=True,
        )

        embed.add_field(
            name="⚠️ Advertencias",
            value=(
                f"{len(warnings)} activa(s) ({warning_points} pts)"
                if warnings else "Sin advertencias"
            ),
            inline=True,
        )

        embed.add_field(
            name="🎫 Tickets",
            value=f"{len(tickets)} totales — {open_tickets} abiertos, {closed_tickets} cerrados",
            inline=True,
        )

        if notes:
            preview = "\n".join(
                f"• {n['note'][:80]}{'…' if len(n['note']) > 80 else ''}"
                for n in notes[:3]
            )
            if len(notes) > 3:
                preview += f"\n*(+{len(notes) - 3} más — usa /nota ver)*"
            embed.add_field(name="📝 Notas de staff", value=preview, inline=False)
        else:
            embed.add_field(name="📝 Notas de staff", value="Sin notas.", inline=False)

        embed.set_footer(text=f"ID: {usuario.id}")
        await interaction.followup.send(embed=embed)

    # ── /nota ────────────────────────────────────────────────────────────────

    nota_group = app_commands.Group(
        name="nota",
        description="Notas internas de staff sobre un usuario (no son warnings oficiales)",
    )

    @nota_group.command(name="agregar", description="Agregar una nota interna sobre un usuario")
    @app_commands.describe(usuario="Usuario sobre el que dejar la nota", texto="Contenido de la nota")
    @checks.is_staff()
    async def nota_agregar(
        self, interaction: discord.Interaction, usuario: discord.Member, texto: str
    ):
        note_id = await db.add_staff_note(
            interaction.guild_id, usuario.id, interaction.user.id, texto
        )
        embed = discord.Embed(
            title="📝 Nota agregada",
            description=f"**Usuario:** {usuario.mention}\n**Nota:** {texto}",
            color=COLOR_SUCCESS,
        )
        embed.set_footer(text=f"Nota #{note_id} — por {interaction.user} — expira en 15 días")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @nota_group.command(name="ver", description="Ver las notas internas de un usuario")
    @app_commands.describe(usuario="Usuario a consultar")
    @checks.is_staff()
    async def nota_ver(self, interaction: discord.Interaction, usuario: discord.Member):
        notes = await db.get_staff_notes(interaction.guild_id, usuario.id)
        embed = discord.Embed(
            title=f"📝 Notas de staff — {usuario}",
            color=COLOR_STAFF,
        )
        if not notes:
            embed.description = "Este usuario no tiene notas registradas (o vencieron — duran 15 días)."
        else:
            for n in notes[:15]:
                embed.add_field(
                    name=n["created_at"][:10],
                    value=f"{n['note']}\n*Por <@{n['author_id']}>*",
                    inline=False,
                )
            footer = "Las notas se borran automáticamente a los 15 días de creadas"
            if len(notes) > 15:
                footer = f"Mostrando 15 de {len(notes)} · {footer}"
            embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Limpieza automática de notas vencidas ───────────────────────────────────

    @tasks.loop(hours=24)
    async def purge_notes_loop(self):
        try:
            deleted = await db.purge_expired_staff_notes()
            if deleted:
                print(f"[StaffTools] 🧹 {deleted} nota(s) de staff vencidas (>15 días) eliminadas.")
        except Exception as e:
            print(f"[StaffTools] ⚠️ Error limpiando notas vencidas: {e}")

    @purge_notes_loop.before_loop
    async def before_purge_notes_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(StaffToolsCog(bot))
