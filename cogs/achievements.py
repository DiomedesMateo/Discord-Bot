"""
Cog: Logros (achievements)

Define los 25 logros del servidor y toda la lógica para desbloquearlos:
- Mensajes totales (9 logros)
- Horas en canales de voz (5 logros)
- Sugerencias con votos positivos (2 logros)
- Reacciones dadas / recibidas (2 logros)
- Archivos subidos (1 logro)
- Mensajes fijados (1 logro)
- Invitados traídos al servidor (1 logro)
- Racha de días activo consecutivos (3 logros)
- Antigüedad en el servidor (1 logro)

Comandos:
- /logros perfil [usuario]  — tarjeta visual con el progreso de logros
- /logros lista [usuario]   — lista de texto con todos los logros y su estado
- /logros ver <logro>       — muestra la insignia completa de un logro
- /logros canal <canal>     — [Owner] fija el canal donde se anuncian los
  logros desbloqueados y las subidas de nivel (en vez de anunciarse en el
  canal donde ocurrió la acción)

NOTA IMPORTANTE: los umbrales exactos (números de mensajes, horas de voz,
etc.) se transcribieron a partir de las imágenes de logros que se
proporcionaron. Si algún número no coincide exactamente con el que
aparece en la imagen original, se puede ajustar fácilmente editando la
lista ACHIEVEMENTS más abajo — cada logro tiene su "threshold" en un solo
lugar.
"""
import asyncio
import io
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from database import db
from utils import checks, helpers, embeds
from config import COLOR_GOLD, COLOR_PRIMARY, COLOR_SUCCESS

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "achievements"
ICONS_DIR = Path(__file__).resolve().parent.parent / "assets" / "achievement_icons"
FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

# Rango de caracteres emoji/símbolos que la fuente DejaVuSans no puede
# dibujar (aparecen como un cuadrito vacío "tofu"). Se eliminan del texto
# antes de dibujarlo en la tarjeta de perfil.
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # símbolos y pictogramas variados, emoticonos, transporte, etc.
    "\U00002600-\U000027BF"  # símbolos misceláneos y dingbats (☀️ ✂️ etc.)
    "\U0001F1E6-\U0001F1FF"  # banderas (regional indicators)
    "\U00002190-\U000021FF"  # flechas
    "\U00002300-\U000023FF"  # símbolos técnicos misceláneos (⏰ ⌚ etc.)
    "\U0000FE0F"             # variation selector (modificador de emoji)
    "\U0000200D"             # zero-width joiner
    "]+",
    flags=re.UNICODE,
)


def _strip_emoji(text: str) -> str:
    """Quita emojis/símbolos no soportados por la fuente y limpia espacios sobrantes."""
    cleaned = _EMOJI_PATTERN.sub("", text)
    return re.sub(r"\s+", " ", cleaned).strip()

DIFFICULTY_COLORS = {
    "Fácil": (90, 170, 255),
    "Medio": (60, 190, 120),
    "Difícil": (200, 150, 60),
    "Muy Difícil": (210, 70, 70),
}

# ─── Definición de los 25 logros ─────────────────────────────────────────────
# category define qué contador se compara contra threshold:
#   messages, voice_hours, suggestion_best, suggestion_total,
#   reactions_given, reactions_received, files, pins, invites, streak, veteran
ACHIEVEMENTS = [
    {"key": "primer_contacto", "file": "01_primer_contacto.png", "name": "Primer Contacto",
     "desc": "Envía tu primer mensaje en el servidor.", "difficulty": "Fácil",
     "category": "messages", "threshold": 1},
    {"key": "conversador_iniciado", "file": "02_conversador_iniciado.png", "name": "Conversador Iniciado",
     "desc": "Envía 50 mensajes en total.", "difficulty": "Fácil",
     "category": "messages", "threshold": 50},
    {"key": "conversador_activo", "file": "03_conversador_activo.png", "name": "Conversador Activo",
     "desc": "Envía 250 mensajes en total.", "difficulty": "Medio",
     "category": "messages", "threshold": 250},
    {"key": "conversador_frecuente", "file": "04_conversador_frecuente.png", "name": "Conversador Frecuente",
     "desc": "Envía 1.000 mensajes en total.", "difficulty": "Medio",
     "category": "messages", "threshold": 1000},
    {"key": "charlista_dedicado", "file": "05_charlista_dedicado.png", "name": "Charlista Dedicado",
     "desc": "Envía 5.000 mensajes en total.", "difficulty": "Difícil",
     "category": "messages", "threshold": 5000},
    {"key": "fuente_inagotable", "file": "06_fuente_inagotable.png", "name": "Fuente Inagotable",
     "desc": "Envía 10.000 mensajes en total.", "difficulty": "Difícil",
     "category": "messages", "threshold": 10000},
    {"key": "leyenda_del_chat", "file": "07_leyenda_del_chat.png", "name": "Leyenda del Chat",
     "desc": "Envía 25.000 mensajes en total.", "difficulty": "Muy Difícil",
     "category": "messages", "threshold": 25000},
    {"key": "mito_viviente", "file": "08_mito_viviente.png", "name": "Mito Viviente",
     "desc": "Envía 50.000 mensajes en total.", "difficulty": "Muy Difícil",
     "category": "messages", "threshold": 50000},
    {"key": "deidad_del_teclado", "file": "09_deidad_del_teclado.png", "name": "Deidad del Teclado",
     "desc": "Envía 100.000 mensajes en total.", "difficulty": "Muy Difícil",
     "category": "messages", "threshold": 100000},

    {"key": "voz_activa", "file": "10_voz_activa.png", "name": "Voz Activa",
     "desc": "Pasa 1 hora en canales de voz.", "difficulty": "Fácil",
     "category": "voice_hours", "threshold": 1},
    {"key": "voz_constante", "file": "11_voz_constante.png", "name": "Voz Constante",
     "desc": "Pasa 10 horas en canales de voz.", "difficulty": "Medio",
     "category": "voice_hours", "threshold": 10},
    {"key": "voz_dedicada", "file": "12_voz_dedicada.png", "name": "Voz Dedicada",
     "desc": "Pasa 50 horas en canales de voz.", "difficulty": "Medio",
     "category": "voice_hours", "threshold": 50},
    {"key": "maratonista_de_voz", "file": "13_maratonista_de_voz.png", "name": "Maratonista de Voz",
     "desc": "Pasa 150 horas en canales de voz.", "difficulty": "Difícil",
     "category": "voice_hours", "threshold": 150},
    {"key": "voz_de_hierro", "file": "14_voz_de_hierro.png", "name": "Voz de Hierro",
     "desc": "Pasa 500 horas en canales de voz.", "difficulty": "Muy Difícil",
     "category": "voice_hours", "threshold": 500},

    {"key": "idea_con_peso", "file": "15_idea_con_peso.png", "name": "Idea con Peso",
     "desc": "Consigue 10 votos positivos en una sola sugerencia.", "difficulty": "Medio",
     "category": "suggestion_best", "threshold": 10},
    {"key": "mente_influyente", "file": "16_mente_influyente.png", "name": "Mente Influyente",
     "desc": "Consigue 50 votos positivos en total en tus sugerencias.", "difficulty": "Difícil",
     "category": "suggestion_total", "threshold": 50},

    {"key": "reaccion_rapida", "file": "17_reaccion_rapida.png", "name": "Reacción Rápida",
     "desc": "Reacciona a 100 mensajes.", "difficulty": "Fácil",
     "category": "reactions_given", "threshold": 100},
    {"key": "el_mas_querido", "file": "18_el_mas_querido.png", "name": "El Más Querido",
     "desc": "Recibe 500 reacciones en tus mensajes.", "difficulty": "Difícil",
     "category": "reactions_received", "threshold": 500},

    {"key": "subida_de_archivos", "file": "19_subida_de_archivos.png", "name": "Subida de Archivos",
     "desc": "Sube 25 archivos o imágenes al chat.", "difficulty": "Fácil",
     "category": "files", "threshold": 25},

    {"key": "primer_pin", "file": "20_primer_pin.png", "name": "Primer Pin",
     "desc": "Consigue que uno de tus mensajes sea fijado.", "difficulty": "Medio",
     "category": "pins", "threshold": 1},

    {"key": "invitador", "file": "21_invitador.png", "name": "Invitador",
     "desc": "Invita a 5 nuevos miembros al servidor.", "difficulty": "Medio",
     "category": "invites", "threshold": 5},

    {"key": "racha_de_una_semana", "file": "22_racha_de_una_semana.png", "name": "Racha de una Semana",
     "desc": "Mantén una racha de 7 días consecutivos activo.", "difficulty": "Fácil",
     "category": "streak", "threshold": 7},
    {"key": "racha_de_un_mes", "file": "23_racha_de_un_mes.png", "name": "Racha de un Mes",
     "desc": "Mantén una racha de 30 días consecutivos activo.", "difficulty": "Medio",
     "category": "streak", "threshold": 30},
    {"key": "racha_imparable", "file": "24_racha_imparable.png", "name": "Racha Imparable",
     "desc": "Mantén una racha de 100 días consecutivos activo.", "difficulty": "Difícil",
     "category": "streak", "threshold": 100},

    {"key": "veterano", "file": "25_veterano.png", "name": "Veterano",
     "desc": "Lleva 1 año (365 días) en el servidor.", "difficulty": "Muy Difícil",
     "category": "veteran", "threshold": 365},
]

ACHIEVEMENTS_BY_KEY = {a["key"]: a for a in ACHIEVEMENTS}

# Ícono nuevo (círculo + etiqueta de dificultad) usado en la tarjeta de perfil.
# El numerado 01..25 sigue el mismo orden que ACHIEVEMENTS.
ACHIEVEMENT_ICON_FILE = {
    a["key"]: f"{i + 1:02d}_{a['key']}.png" for i, a in enumerate(ACHIEVEMENTS)
}

LOGRO_CHOICES = [
    app_commands.Choice(name=a["name"], value=a["key"]) for a in ACHIEVEMENTS
][:25]  # Discord limita los choices a 25


class AchievementsCog(commands.Cog, name="Logros"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # user_id -> timestamp (epoch) de cuándo entró a un canal de voz.
        self._voice_join_ts: dict[int, float] = {}

    # ── Núcleo: comprobar y desbloquear logros ───────────────────────────

    async def check_progress(self, member: discord.Member, category: str, value: int,
                              channel: discord.abc.Messageable = None) -> list[dict]:
        """Revisa todos los logros de `category` cuyo threshold ya se
        alcanzó con `value`, y desbloquea + anuncia los que falten.
        Devuelve la lista de logros desbloqueados en ESTA llamada."""
        guild_id = member.guild.id
        unlocked_now = []
        candidates = [a for a in ACHIEVEMENTS if a["category"] == category and a["threshold"] <= value]
        for ach in candidates:
            already = await db.has_achievement(guild_id, member.id, ach["key"])
            if already:
                continue
            got_it = await db.unlock_achievement(guild_id, member.id, ach["key"])
            if got_it:
                unlocked_now.append(ach)
                await self._announce(member, ach, channel)
        return unlocked_now

    async def _announce(self, member: discord.Member, ach: dict, channel: discord.abc.Messageable = None):
        target = await helpers.get_announce_channel(member.guild) or channel
        if target is None:
            target = member.guild.system_channel
        if target is None:
            return

        image_path = ASSETS_DIR / ach["file"]
        embed = discord.Embed(
            title="🏆┃¡LOGRO DESBLOQUEADO!",
            description=(
                f"🎉 {member.mention} ha desbloqueado un nuevo logro.\n"
                f"🥇 **{ach['name']}**\n\n"
                f"⭐ ¡Sigue explorando MineOrbit y completa más desafíos para convertirte "
                f"en una leyenda de la Network!"
            ),
            color=COLOR_GOLD,
        )
        try:
            if image_path.exists():
                file = discord.File(image_path, filename=ach["file"])
                embed.set_image(url=f"attachment://{ach['file']}")
                await target.send(embed=embed, file=file)
            else:
                await target.send(embed=embed)
        except discord.HTTPException:
            pass

    # ── Voz: acumular tiempo conectado ───────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                     before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return
        import time

        if before.channel is None and after.channel is not None:
            self._voice_join_ts[member.id] = time.time()
        elif before.channel is not None and after.channel is None:
            joined = self._voice_join_ts.pop(member.id, None)
            if joined is None:
                return
            elapsed = int(time.time() - joined)
            if elapsed <= 0:
                return
            total_seconds = await db.increment_progress(member.guild.id, member.id, "voice_seconds", elapsed)
            hours = total_seconds // 3600
            await self.check_progress(member, "voice_hours", hours)

    # ── Archivos subidos ──────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if not message.attachments:
            return
        total = await db.increment_progress(
            message.guild.id, message.author.id, "files_uploaded", len(message.attachments)
        )
        await self.check_progress(message.author, "files", total, message.channel)

    # ── Mensajes fijados ──────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_pins_update(self, channel, last_pin):
        try:
            pins = await channel.pins()
        except (discord.Forbidden, discord.HTTPException):
            return
        for pinned_message in pins:
            if pinned_message.author.bot:
                continue
            recorded = await db.try_record_pin(pinned_message.id, channel.guild.id, pinned_message.author.id)
            if not recorded:
                continue
            total = await db.increment_progress(channel.guild.id, pinned_message.author.id, "pins_earned", 1)
            member = channel.guild.get_member(pinned_message.author.id)
            if member:
                await self.check_progress(member, "pins", total, channel)

    # ── Reacciones dadas / recibidas ──────────────────────────────────────

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.member is None or payload.member.bot:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        # Reacción "dada" — cuenta 1 vez por (mensaje, usuario).
        recorded = await db.try_record_reaction_given(payload.message_id, payload.user_id)
        if recorded:
            total_given = await db.increment_progress(payload.guild_id, payload.user_id, "reactions_given", 1)
            await self.check_progress(payload.member, "reactions_given", total_given)

        # Reacción "recibida" por el autor del mensaje.
        try:
            channel = guild.get_channel(payload.channel_id) or await guild.fetch_channel(payload.channel_id)
            msg = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
        if msg.author.bot or msg.author.id == payload.user_id:
            return
        total_received = await db.increment_progress(payload.guild_id, msg.author.id, "reactions_received", 1)
        author_member = guild.get_member(msg.author.id)
        if author_member:
            await self.check_progress(author_member, "reactions_received", total_received)

    # ── Invitados traídos ──────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        # Pequeña espera para dar tiempo a que el cog de invites.py
        # resuelva y guarde quién invitó a este miembro.
        await asyncio.sleep(3)
        inviter_row = await db.get_inviter_of(member.guild.id, member.id)
        if not inviter_row or not inviter_row.get("inviter_id"):
            return
        inviter_id = inviter_row["inviter_id"]
        uses = await db.get_invite_uses(member.guild.id, inviter_id)
        inviter_member = member.guild.get_member(inviter_id)
        if inviter_member:
            await self.check_progress(inviter_member, "invites", uses)

    # ── Comandos ──────────────────────────────────────────────────────────

    logros_group = app_commands.Group(name="logros", description="Sistema de logros del servidor")

    @logros_group.command(name="perfil", description="Muestra tu tarjeta de logros")
    @app_commands.describe(usuario="Miembro a consultar (por defecto, tú mismo)")
    async def logros_perfil(self, interaction: discord.Interaction, usuario: discord.Member = None):
        await interaction.response.defer()
        member = usuario or interaction.user
        unlocked = await db.get_user_achievements(interaction.guild_id, member.id)
        avatar_bytes = await member.display_avatar.replace(size=256, format="png").read()

        buffer = await asyncio.to_thread(
            self._build_profile_card, member.display_name, interaction.guild.name, unlocked, avatar_bytes
        )
        file = discord.File(buffer, filename="perfil_logros.png")
        await interaction.followup.send(file=file)

    @logros_group.command(name="lista", description="Lista de texto de todos los logros y su estado")
    @app_commands.describe(usuario="Miembro a consultar (por defecto, tú mismo)")
    async def logros_lista(self, interaction: discord.Interaction, usuario: discord.Member = None):
        member = usuario or interaction.user
        unlocked = await db.get_user_achievements(interaction.guild_id, member.id)

        lines = []
        for ach in ACHIEVEMENTS:
            mark = "✅" if ach["key"] in unlocked else "🔒"
            lines.append(f"{mark} **{ach['name']}** _({ach['difficulty']})_ — {ach['desc']}")

        embed = discord.Embed(
            title=f"📋 Logros de {member.display_name}",
            description=f"Desbloqueados: **{len(unlocked)}/{len(ACHIEVEMENTS)}**\n\n" + "\n".join(lines),
            color=COLOR_PRIMARY,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @logros_group.command(name="ver", description="Muestra la insignia completa de un logro")
    @app_commands.describe(logro="Logro a mostrar")
    @app_commands.choices(logro=LOGRO_CHOICES)
    async def logros_ver(self, interaction: discord.Interaction, logro: app_commands.Choice[str]):
        ach = ACHIEVEMENTS_BY_KEY[logro.value]
        unlocked = await db.has_achievement(interaction.guild_id, interaction.user.id, ach["key"])
        image_path = ASSETS_DIR / ach["file"]

        status = "✅ Desbloqueado" if unlocked else "🔒 Bloqueado"
        embed = discord.Embed(
            title=ach["name"],
            description=f"{ach['desc']}\n\n**Dificultad:** {ach['difficulty']}\n**Estado:** {status}",
            color=COLOR_GOLD if unlocked else COLOR_PRIMARY,
        )
        if image_path.exists():
            file = discord.File(image_path, filename=ach["file"])
            embed.set_image(url=f"attachment://{ach['file']}")
            await interaction.response.send_message(embed=embed, file=file)
        else:
            await interaction.response.send_message(embed=embed)

    @logros_group.command(
        name="dar",
        description="(Staff) Da manualmente un logro a un usuario, por si algo falló.",
    )
    @app_commands.describe(
        usuario="Usuario al que darle el logro",
        logro="Logro a otorgar",
        anunciar="Publicar el anuncio de logro desbloqueado (por defecto sí)",
    )
    @app_commands.choices(logro=LOGRO_CHOICES)
    @checks.is_staff()
    async def logros_dar(self, interaction: discord.Interaction, usuario: discord.Member,
                          logro: app_commands.Choice[str], anunciar: bool = True):
        ach = ACHIEVEMENTS_BY_KEY[logro.value]
        got_it = await db.unlock_achievement(interaction.guild_id, usuario.id, ach["key"])

        if not got_it:
            await interaction.response.send_message(
                embed=embeds.warning(f"{usuario.mention} ya tenía el logro **{ach['name']}**."),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=embeds.success(
                f"Le diste el logro **{ach['name']}** a {usuario.mention}."
                + ("" if anunciar else "\n(No se publicó el anuncio.)")
            ),
            ephemeral=True,
        )
        if anunciar:
            await self._announce(usuario, ach)

    @logros_group.command(
        name="quitar",
        description="(Staff) Quita manualmente un logro a un usuario, por si se dio por error.",
    )
    @app_commands.describe(usuario="Usuario al que quitarle el logro", logro="Logro a quitar")
    @app_commands.choices(logro=LOGRO_CHOICES)
    @checks.is_staff()
    async def logros_quitar(self, interaction: discord.Interaction, usuario: discord.Member,
                             logro: app_commands.Choice[str]):
        ach = ACHIEVEMENTS_BY_KEY[logro.value]
        removed = await db.remove_achievement(interaction.guild_id, usuario.id, ach["key"])

        if not removed:
            await interaction.response.send_message(
                embed=embeds.warning(f"{usuario.mention} no tenía el logro **{ach['name']}**."),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=embeds.success(f"Le quitaste el logro **{ach['name']}** a {usuario.mention}."),
            ephemeral=True,
        )

    @logros_group.command(
        name="canal",
        description="Configura el canal donde se anuncian los logros y las subidas de nivel",
    )
    @app_commands.describe(
        canal="Canal donde se publicarán los logros desbloqueados y las subidas de nivel"
    )
    @checks.is_owner()
    async def logros_canal(self, interaction: discord.Interaction, canal: discord.TextChannel):
        perms = canal.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                f"⚠️ No tengo permisos para enviar mensajes con embeds en {canal.mention}. "
                "Revisa mis permisos en ese canal e inténtalo de nuevo.",
                ephemeral=True,
            )
            return

        await db.set_guild_config(interaction.guild_id, achievements_channel=canal.id)

        embed = discord.Embed(
            title="✅ Canal de logros y niveles configurado",
            description=(
                f"A partir de ahora, los logros desbloqueados y las subidas de nivel "
                f"se anunciarán en {canal.mention}."
            ),
            color=COLOR_SUCCESS,
        )
        await interaction.response.send_message(embed=embed)

    # ── Generación de la tarjeta de perfil (PIL) ──────────────────────────

    def _build_profile_card(self, display_name: str, guild_name: str,
                             unlocked_keys: set, avatar_bytes: bytes) -> io.BytesIO:
        # Solo se muestran los logros que el jugador ya desbloqueó (como
        # logo/icono). Si no tiene ninguno, el área de logros queda vacía.
        unlocked_achievements = [ach for ach in ACHIEVEMENTS if ach["key"] in unlocked_keys]

        COLS = 5
        BADGE_W, BADGE_H = 150, 220  # proporción real de los nuevos íconos (~0.68:1)
        CELL_W, CELL_H = BADGE_W + 30, BADGE_H + 20
        ROWS = (len(unlocked_achievements) + COLS - 1) // COLS  # 0 si no hay ninguno
        PADDING = 40
        HEADER_H = 190
        EMPTY_MSG_H = 60  # espacio para el mensaje cuando no hay logros

        width = PADDING * 2 + COLS * CELL_W
        height = HEADER_H + (ROWS * CELL_H if ROWS else EMPTY_MSG_H) + PADDING

        bg = (17, 15, 28, 255)
        card = Image.new("RGBA", (width, height), bg)
        draw = ImageDraw.Draw(card)

        # Borde dorado redondeado
        draw.rounded_rectangle(
            [(4, 4), (width - 5, height - 5)], radius=28, outline=(212, 175, 55, 255), width=4
        )

        try:
            font_bold_big = ImageFont.truetype(str(FONTS_DIR / "DejaVuSans-Bold.ttf"), 34)
            font_bold_mid = ImageFont.truetype(str(FONTS_DIR / "DejaVuSans-Bold.ttf"), 24)
            font_regular = ImageFont.truetype(str(FONTS_DIR / "DejaVuSans.ttf"), 20)
            font_small = ImageFont.truetype(str(FONTS_DIR / "DejaVuSans-Bold.ttf"), 16)
        except OSError:
            font_bold_big = font_bold_mid = font_regular = font_small = ImageFont.load_default()

        # Avatar circular
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((110, 110))
        mask = Image.new("L", avatar.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, *avatar.size), fill=255)
        avatar_x, avatar_y = PADDING, 45
        card.paste(avatar, (avatar_x, avatar_y), mask)

        text_x = avatar_x + 130
        draw.text((text_x, 55), _strip_emoji(guild_name), font=font_bold_big, fill=(230, 230, 240, 255))
        draw.text(
            (text_x, 100),
            f"Logros desbloqueados  |  {len(unlocked_keys)}  |  {len(ACHIEVEMENTS)}",
            font=font_regular, fill=(180, 180, 195, 255),
        )
        draw.text((PADDING, HEADER_H - 30), "LOGROS", font=font_small, fill=(150, 150, 165, 255))

        if not unlocked_achievements:
            # Área de logros vacía: sin insignias bloqueadas ni de relleno.
            msg = "Aún no ha desbloqueado ningún logro."
            bbox = draw.textbbox((0, 0), msg, font=font_regular)
            msg_w = bbox[2] - bbox[0]
            draw.text(
                ((width - msg_w) / 2, HEADER_H + (EMPTY_MSG_H - (bbox[3] - bbox[1])) / 2),
                msg, font=font_regular, fill=(120, 120, 135, 255),
            )
        else:
            # Grid con el ícono nuevo de cada logro desbloqueado: solo el
            # círculo del logro y su etiqueta de dificultad debajo (sin
            # nombre, sin descripción, sin bloqueados de relleno).
            for idx, ach in enumerate(unlocked_achievements):
                col, row = idx % COLS, idx // COLS
                cell_x = PADDING + col * CELL_W
                cell_y = HEADER_H + row * CELL_H

                icon_name = ACHIEVEMENT_ICON_FILE.get(ach["key"])
                source_path = ICONS_DIR / icon_name if icon_name else None
                if source_path and source_path.exists():
                    logo = Image.open(source_path).convert("RGBA")
                else:
                    logo = Image.new("RGBA", (BADGE_W, BADGE_H), (60, 60, 70, 255))

                # Redimensiona conservando la proporción para que quepa en
                # BADGE_W x BADGE_H sin deformarse ni recortarse.
                logo = logo.copy()
                logo.thumbnail((BADGE_W, BADGE_H), Image.LANCZOS)
                logo_x = cell_x + (CELL_W - logo.width) // 2
                logo_y = cell_y + (CELL_H - logo.height) // 2
                card.paste(logo, (logo_x, logo_y), logo)

        buffer = io.BytesIO()
        card.convert("RGB").save(buffer, format="PNG")
        buffer.seek(0)
        return buffer


async def setup(bot: commands.Bot):
    await bot.add_cog(AchievementsCog(bot))
