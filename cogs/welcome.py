"""
Cog: Welcome / Goodbye / Boost
- Mensaje de bienvenida con embed al unirse un miembro
- /welcome canal — configura el canal de bienvenidas y auto-rol
- Auto-role on join (configurable)
- Mensaje de agradecimiento automático cuando alguien boostea el servidor
- /welcome boostcanal — configura el canal donde se agradece a los boosters
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands
from database import db
from utils import checks
from config import COLOR_SUCCESS

log = logging.getLogger(__name__)

BOOST_COLOR = 0xF47FFF  # rosa/morado — el color oficial de Nitro Boost

WELCOME_MESSAGE = (
    "## ¡Bienvenido a **{server}**, {mention}!\n\n"
    "Nos alegra que formes parte de nuestra comunidad. 🚀\n"
    "Prepárate para vivir una experiencia única, conocer nuevos jugadores y comenzar tu aventura en **MineOrbit**.\n\n"
    "## 📌 Canales importantes\n\n"
    "<#1486944040099512550> ・ Consulta las reglas del servidor.\n"
    "<#1503977056264913017> ・ Mantente informado sobre anuncios y novedades.\n"
    "<#1486944040099512541> ・ Participa en encuestas y ayúdanos a mejorar.\n"
    "<#1486944039575228487> ・ Descubre adelantos y contenido exclusivo.\n"
    "<#1503978502163005510> ・ Visita nuestra tienda oficial.\n"
    "<#1486944040292188172> ・ Conecta con la comunidad y haz nuevos amigos.\n"
    "<#1486944040292188177> ・ ¿Necesitas ayuda? Contacta con nuestro equipo de soporte.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "👥 **Ahora somos {count} miembros** explorando el universo de **{server}**.\n"
    "🌀 ¡Esperamos verte crecer junto a nosotros!\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "💙 **¡Disfruta tu estancia y que comience la aventura!**"
)

BOOST_TITLE = "🚀┃¡GRACIAS POR BOOSTEAR!"

BOOST_MESSAGE = (
    "¡Muchísimas gracias, {mention}, por apoyar a **MineOrbit Network** con un Server Boost! 💙\n"
    "Tu apoyo nos ayuda a seguir mejorando la comunidad, organizar nuevos eventos y ofrecer una "
    "mejor experiencia para todos los jugadores.\n\n"
    "## 🎁 ¿Qué sigue?\n\n"
    "🔹 Crea un ticket en la categoría de tienda (<#1486944040292188177>) para reclamar tus recompensas.\n"
    "🔹 Un miembro del Staff verificará tu Boost y te entregará todos los beneficios correspondientes.\n\n"
    "✨ ¡Apreciamos muchísimo tu apoyo!\n"
    "🌌 ¡Esperamos que disfrutes de tus nuevas recompensas y sigas formando parte de la aventura de MineOrbit!"
)


class WelcomeCog(commands.Cog, name="Welcome"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    welcome_group = app_commands.Group(
        name="welcome", description="Sistema de bienvenida"
    )

    # ── Listener ──────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        config = await db.get_welcome_config(member.guild.id)

        # [FIX] Auto-role: cualquier error aquí (no solo Forbidden) quedaba
        # sin capturar y cortaba el resto del listener, impidiendo que el
        # mensaje de bienvenida se enviara. Ahora se captura cualquier
        # excepción y se loggea, sin afectar el envío del mensaje.
        auto_role_id = config.get("auto_role")
        if auto_role_id:
            role = member.guild.get_role(auto_role_id)
            if role:
                try:
                    await member.add_roles(role, reason="Auto-role on join")
                except discord.Forbidden:
                    log.warning(
                        "Sin permisos para asignar auto-rol %s en guild %s",
                        auto_role_id, member.guild.id,
                    )
                except Exception:
                    log.exception(
                        "Error inesperado asignando auto-rol %s en guild %s",
                        auto_role_id, member.guild.id,
                    )
            else:
                log.warning(
                    "Auto-rol configurado (%s) ya no existe en guild %s",
                    auto_role_id, member.guild.id,
                )

        # Welcome message
        welcome_ch_id = config.get("welcome_channel")
        if not welcome_ch_id:
            return

        ch = member.guild.get_channel(welcome_ch_id)
        if not ch:
            # [FIX] get_channel depende de la caché; si el canal no está
            # cacheado (ej. justo tras un reinicio) devolvía None y se
            # abandonaba en silencio. Se agrega fetch_channel como respaldo.
            try:
                ch = await member.guild.fetch_channel(welcome_ch_id)
            except (discord.NotFound, discord.Forbidden):
                log.warning(
                    "Canal de bienvenida %s no encontrado/sin acceso en guild %s",
                    welcome_ch_id, member.guild.id,
                )
                return

        description = WELCOME_MESSAGE.format(
            mention=member.mention,
            server=member.guild.name,
            count=member.guild.member_count,
        )

        embed = discord.Embed(description=description, color=COLOR_SUCCESS)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(
            text=f"Miembro #{member.guild.member_count} • {member.guild.name}"
        )

        try:
            await ch.send(content=member.mention, embed=embed)
        except discord.Forbidden:
            log.warning(
                "Sin permisos para enviar mensaje de bienvenida en canal %s (guild %s)",
                ch.id, member.guild.id,
            )
        except Exception:
            log.exception(
                "Error inesperado enviando mensaje de bienvenida en guild %s",
                member.guild.id,
            )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        config = await db.get_welcome_config(member.guild.id)
        goodbye_ch_id = config.get("goodbye_channel")
        if not goodbye_ch_id:
            return

        ch = member.guild.get_channel(goodbye_ch_id)
        if not ch:
            try:
                ch = await member.guild.fetch_channel(goodbye_ch_id)
            except (discord.NotFound, discord.Forbidden):
                log.warning(
                    "Canal de despedida %s no encontrado/sin acceso en guild %s",
                    goodbye_ch_id, member.guild.id,
                )
                return

        embed = discord.Embed(
            description=f"**{member}** ha abandonado **{member.guild.name}**. ¡Hasta pronto!",
            color=0xED4245,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=member.guild.name)
        try:
            await ch.send(embed=embed)
        except discord.Forbidden:
            log.warning(
                "Sin permisos para enviar mensaje de despedida en canal %s (guild %s)",
                ch.id, member.guild.id,
            )
        except Exception:
            log.exception(
                "Error inesperado enviando mensaje de despedida en guild %s",
                member.guild.id,
            )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # `premium_since` pasa de None a una fecha justo cuando el miembro
        # EMPIEZA a boostear el servidor. Si ya boosteaba y sigue
        # boosteando, ambos valores son distintos de None y no se dispara.
        if before.premium_since is not None or after.premium_since is None:
            return

        config = await db.get_welcome_config(after.guild.id)
        boost_ch_id = config.get("boost_channel")
        if not boost_ch_id:
            return

        ch = after.guild.get_channel(boost_ch_id)
        if not ch:
            try:
                ch = await after.guild.fetch_channel(boost_ch_id)
            except (discord.NotFound, discord.Forbidden):
                log.warning(
                    "Canal de agradecimiento de boost %s no encontrado/sin acceso en guild %s",
                    boost_ch_id, after.guild.id,
                )
                return

        description = BOOST_MESSAGE.format(mention=after.mention)

        embed = discord.Embed(title=BOOST_TITLE, description=description, color=BOOST_COLOR)
        embed.set_thumbnail(url=after.display_avatar.url)
        embed.set_footer(text=after.guild.name)

        try:
            await ch.send(content=after.mention, embed=embed)
        except discord.Forbidden:
            log.warning(
                "Sin permisos para enviar mensaje de boost en canal %s (guild %s)",
                ch.id, after.guild.id,
            )
        except Exception:
            log.exception(
                "Error inesperado enviando mensaje de boost en guild %s",
                after.guild.id,
            )

    # ── Commands ──────────────────────────────────────────────────────────────

    @welcome_group.command(
        name="canal", description="Configura el canal de bienvenidas"
    )
    @app_commands.describe(
        canal="Canal donde se enviarán los mensajes de bienvenida",
        rol_automatico="Rol asignado automáticamente al unirse (opcional)",
    )
    @checks.is_owner()
    async def welcome_canal(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
        rol_automatico: discord.Role = None,
    ):
        await interaction.response.defer(ephemeral=True)

        kwargs = {"welcome_channel": canal.id}
        if rol_automatico:
            kwargs["auto_role"] = rol_automatico.id

        await db.update_welcome_config(interaction.guild_id, **kwargs)

        embed = discord.Embed(title="✅ Bienvenidas configuradas", color=COLOR_SUCCESS)
        embed.add_field(name="Canal", value=canal.mention, inline=True)
        if rol_automatico:
            embed.add_field(name="Auto-rol", value=rol_automatico.mention, inline=True)
        embed.add_field(
            name="Mensaje",
            value="Mensaje de bienvenida de MineOrbit activo ✅",
            inline=False,
        )
        await interaction.followup.send(embed=embed)

    @welcome_group.command(
        name="boostcanal", description="Configura el canal donde se agradece a quienes boostean el servidor"
    )
    @app_commands.describe(canal="Canal donde se publicará el mensaje de agradecimiento")
    @checks.is_owner()
    async def welcome_boostcanal(self, interaction: discord.Interaction, canal: discord.TextChannel):
        await db.update_welcome_config(interaction.guild_id, boost_channel=canal.id)
        embed = discord.Embed(
            title="✅ Canal de boosts configurado",
            description=f"A partir de ahora, cada vez que alguien boostee el servidor se le agradecerá en {canal.mention}.",
            color=COLOR_SUCCESS,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeCog(bot))