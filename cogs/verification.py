"""
Cog: Verification
- /verification setup  — configure channel, role, and mode
- /verification panel  — send the verification panel
- /verification status — view current config
- Button mode: click to verify
- Captcha mode: solve a math captcha via modal

"""

import random
import discord
from discord import app_commands
from discord.ext import commands
from database import db
from utils import checks
from config import COLOR_SUCCESS, COLOR_ERROR, COLOR_INFO

COLOR_MINEORBIT = 0x00FFFF


class CaptchaModal(discord.ui.Modal):
    def __init__(self, answer: int, role_id: int):
        super().__init__(title="Verificación — Resuelve el captcha")
        self.answer  = answer
        self.role_id = role_id
        self.response_input = discord.ui.TextInput(
            label="Respuesta",
            placeholder="Escribe el resultado…",
            max_length=6,
        )
        self.add_item(self.response_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_answer = int(self.response_input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                "❌ Respuesta inválida. Por favor inténtalo de nuevo.", ephemeral=True
            )

        if user_answer != self.answer:
            return await interaction.response.send_message(
                "❌ Respuesta incorrecta. Inténtalo de nuevo.", ephemeral=True
            )

        role = interaction.guild.get_role(self.role_id)
        if not role:
            return await interaction.response.send_message(
                "❌ Rol de verificación no encontrado.", ephemeral=True
            )
        try:
            await interaction.user.add_roles(role, reason="Verificación completada")
        except discord.Forbidden:
            return await interaction.response.send_message(
                "❌ No tengo permisos para añadir ese rol.", ephemeral=True
            )

        embed = discord.Embed(
            title="✅ Verificación completada",
            description=f"¡Bienvenido/a al servidor! Se te ha asignado el rol {role.mention}.",
            color=COLOR_SUCCESS,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


def _generate_captcha() -> tuple[int, int, str, int]:
    """Genera una operación matemática simple y devuelve (a, b, op, answer).

    FIX 1: la resta siempre pone el mayor primero para garantizar
    resultado >= 0. Multiplicación usa números pequeños (2-9) para
    que el resultado sea manejable mentalmente.
    """
    op = random.choice(["+", "-", "*"])
    if op == "+":
        a = random.randint(1, 20)
        b = random.randint(1, 20)
        answer = a + b
    elif op == "-":
        # FIX 1: max primero → resta siempre positiva o cero
        a = random.randint(1, 20)
        b = random.randint(1, 20)
        a, b = max(a, b), min(a, b)  # garantiza a >= b
        answer = a - b
    else:  # "*"
        a = random.randint(2, 9)
        b = random.randint(2, 9)
        answer = a * b
    return a, b, op, answer


# ─────────────────────────────────────────────────────────────────────────────
# Embed builder — panel de verificación
# ─────────────────────────────────────────────────────────────────────────────

def build_verification_embed(mode: str) -> discord.Embed:
    """Template del panel de verificación (MineOrbit Network)."""
    description = (
        "¡Bienvenido/a a MineOrbit! 🚀\n"
        "Estás a un solo paso de acceder a toda nuestra comunidad. Para comenzar tu "
        "aventura, completa el proceso de verificación presionando el botón que "
        "aparece debajo de este mensaje.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "## ¿QUÉ OBTENDRÁS?\n"
        "🔹 Acceso a todos los canales del servidor.\n"
        "🔹 Posibilidad de hablar e interactuar con la comunidad.\n"
        "🔹 Acceso a eventos, novedades y todas las modalidades de MineOrbit.\n"
        "🔹 Participación en sorteos, recompensas y futuras actividades exclusivas.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "## 📜 INFORMACIÓN IMPORTANTE\n"
        "⚠️ Al verificarte, aceptas automáticamente las normas oficiales del servidor.\n"
        "📖 Te recomendamos leer cuidadosamente <#1486944040099512550> antes de comenzar.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✨ ¡Gracias por formar parte de MineOrbit! 🌠 Esperamos que disfrutes tu "
        "estancia y vivas una experiencia inolvidable junto a nuestra comunidad."
    )

    embed = discord.Embed(
        title="🌀┃VERIFICACIÓN — MINEORBIT NETWORK",
        description=description,
        color=COLOR_MINEORBIT,
    )
    return embed


class VerificationView(discord.ui.View):
    def __init__(self):
        # FIX 2: ya no recibe mode ni role_id en el constructor.
        # Esos valores se leen desde la DB en el callback para que
        # funcionen correctamente tras un reinicio del bot.
        super().__init__(timeout=None)

    @discord.ui.button(
        label="✅ Verificarme",
        style=discord.ButtonStyle.success,
        custom_id="verify_button",
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        # FIX 2: leer config desde la DB en cada interacción, no desde
        # el estado del constructor que se pierde al reiniciar.
        config = await db.get_verification_config(interaction.guild_id)
        role_id = config.get("role_id")
        mode    = config.get("mode") or "button"

        if not role_id:
            return await interaction.response.send_message(
                "❌ Rol de verificación no configurado. Avisa a un administrador.", ephemeral=True
            )

        role = interaction.guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message(
                "❌ Rol de verificación no encontrado.", ephemeral=True
            )

        if role in interaction.user.roles:
            return await interaction.response.send_message(
                "Ya estás verificado.", ephemeral=True
            )

        if mode == "captcha":
            # FIX 1: usa _generate_captcha() que garantiza resta positiva
            a, b, op, answer = _generate_captcha()
            modal = CaptchaModal(answer=answer, role_id=role_id)
            modal.title = f"Verificación — ¿Cuánto es {a} {op} {b}?"
            await interaction.response.send_modal(modal)
        else:
            try:
                await interaction.user.add_roles(role, reason="Verificación completada")
                embed = discord.Embed(
                    title="✅ Verificación completada",
                    description=f"¡Bienvenido/a! Se te ha asignado el rol {role.mention}.",
                    color=COLOR_SUCCESS,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message(
                    "❌ No tengo permisos para añadir el rol.", ephemeral=True
                )


class VerificationCog(commands.Cog, name="Verification"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    verify_group = app_commands.Group(
        name="verification", description="Sistema de verificación"
    )

    @verify_group.command(name="setup", description="Configurar el sistema de verificación")
    @app_commands.describe(
        canal="Canal de verificación",
        rol="Rol asignado al verificarse",
        modo="Modo: button (click) o captcha (math)",
    )
    @checks.is_owner()
    async def verify_setup(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
        rol: discord.Role,
        modo: str = "button",
    ):
        await interaction.response.defer(ephemeral=True)
        if modo not in ("button", "captcha"):
            return await interaction.followup.send(
                "❌ Modo inválido. Usa `button` o `captcha`."
            )

        await db.update_verification_config(
            interaction.guild_id,
            channel_id=canal.id,
            role_id=rol.id,
            mode=modo,
        )
        await db.set_guild_config(
            interaction.guild_id,
            verification_channel=canal.id,
            verification_role=rol.id,
        )
        await interaction.followup.send(
            f"✅ Verificación configurada:\n"
            f"Canal: {canal.mention} | Rol: {rol.mention} | Modo: `{modo}`",
            ephemeral=True,
        )

    @verify_group.command(name="panel", description="Enviar el panel de verificación")
    @app_commands.describe(canal="Canal donde enviar el panel")
    @checks.is_owner()
    async def verify_panel(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel = None,
    ):
        await interaction.response.defer(ephemeral=True)
        config  = await db.get_verification_config(interaction.guild_id)
        role_id = config.get("role_id")
        mode    = config.get("mode") or "button"
        target  = canal or interaction.channel

        if not role_id:
            return await interaction.followup.send(
                "❌ Primero configura la verificación con `/verification setup`."
            )

        # [NUEVO] Template MineOrbit Network
        embed = build_verification_embed(mode)

        # FIX 2: VerificationView ya no recibe argumentos — los lee de la DB
        view = VerificationView()
        await target.send(embed=embed, view=view)
        await interaction.followup.send(
            f"✅ Panel de verificación enviado en {target.mention}."
        )

    @verify_group.command(
        name="status", description="Ver configuración actual de verificación"
    )
    @checks.is_owner()
    async def verify_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        config  = await db.get_verification_config(interaction.guild_id)
        embed   = discord.Embed(
            title="🔐 Configuración de Verificación", color=COLOR_INFO
        )
        ch_id   = config.get("channel_id")
        role_id = config.get("role_id")
        embed.add_field(
            name="Canal",
            value=f"<#{ch_id}>" if ch_id else "No configurado",
            inline=True,
        )
        embed.add_field(
            name="Rol",
            value=f"<@&{role_id}>" if role_id else "No configurado",
            inline=True,
        )
        embed.add_field(
            name="Modo",
            value=config.get("mode") or "button",
            inline=True,
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(VerificationCog(bot))
    # FIX 2: re-registrar la View persistente al arrancar el bot.
    # Sin esto, el botón "Verificarme" deja de funcionar tras un reinicio.
    # Ya no se necesitan argumentos porque el callback los lee de la DB.
    bot.add_view(VerificationView())