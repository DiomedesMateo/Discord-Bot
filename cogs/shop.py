"""
Cog: Tienda — canjea Créditos (ver cogs/economy.py) por rangos/roles del
servidor y otras recompensas.

- /tienda ver             — lista los items disponibles
- /tienda comprar         — compra un item (deduce saldo, da el rol de Discord
                             si aplica, y siempre genera un código único de
                             canje que se manda por DM al comprador)
- /tienda miscompras      — ver tu historial de compras y códigos
- /tienda crear           — (staff) crear un item nuevo
- /tienda eliminar        — (staff) desactivar un item
- /tienda verificarcodigo — (staff) revisar si un código ya fue canjeado
- /tienda marcarcanjeado  — (staff) marcar un código como ya canjeado
- /tienda pendientes      — (staff) ver todos los códigos aún no canjeados

Cómo se entrega la recompensa fuera de Discord (ej. un rango en Minecraft):
  Es un proceso manual. El comprador recibe su código único por DM, se lo
  muestra al staff (en el servidor de Minecraft, por ticket, etc.), el staff
  usa /tienda verificarcodigo para confirmar que es válido y no fue usado
  antes, entrega la recompensa a mano, y luego usa /tienda marcarcanjeado
  para que quede registrado y no se pueda reutilizar.
"""
import random

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from utils import checks, embeds
from config import COLOR_INFO, COLOR_GOLD

from cogs.economy import fmt, CURRENCY_NAME  # noqa: reutiliza formato/nombre de moneda

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O/1/I/L para evitar confusiones


def _generate_code() -> str:
    parts = ["".join(random.choices(CODE_ALPHABET, k=4)) for _ in range(3)]
    return "-".join(parts)


class Shop(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    tienda_group = app_commands.Group(name="tienda", description=f"Canjea tus {CURRENCY_NAME} por rangos y recompensas.")

    @tienda_group.command(name="ver", description="Ver los items disponibles en la tienda.")
    async def ver(self, interaction: discord.Interaction):
        items = await db.get_shop_items(interaction.guild_id)
        if not items:
            return await interaction.response.send_message(
                embed=embeds.info("La tienda está vacía por ahora."), ephemeral=True
            )
        embed = discord.Embed(
            title="🛒 Tienda del servidor",
            description="Usa `/tienda comprar` con el nombre exacto del item.",
            color=COLOR_GOLD,
        )
        for item in items:
            extras = []
            if item["role_id"]:
                extras.append(f"otorga <@&{item['role_id']}>")
            if item["mc_reward"]:
                extras.append(f"recompensa: **{item['mc_reward']}**")
            if item["stock"] >= 0:
                extras.append(f"stock: {item['stock']}")
            extra_text = f"\n_{' · '.join(extras)}_" if extras else ""
            embed.add_field(
                name=f"{item['name']} — {fmt(item['price'])}",
                value=(item["description"] or "Sin descripción") + extra_text,
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    @tienda_group.command(name="comprar", description="Compra un item de la tienda.")
    @app_commands.describe(item="Nombre exacto del item (ver /tienda ver)")
    async def comprar(self, interaction: discord.Interaction, item: str):
        await interaction.response.defer(ephemeral=True)
        shop_item = await db.get_shop_item_by_name(interaction.guild_id, item)
        if not shop_item:
            return await interaction.followup.send(
                embed=embeds.error(f"No encontré ningún item llamado `{item}`. Revisa `/tienda ver`."),
                ephemeral=True,
            )
        if shop_item["stock"] == 0:
            return await interaction.followup.send(
                embed=embeds.error(f"**{shop_item['name']}** está agotado."), ephemeral=True
            )

        # Rol de Discord (si aplica) — verificar que el bot pueda asignarlo antes de cobrar.
        role = None
        if shop_item["role_id"]:
            role = interaction.guild.get_role(shop_item["role_id"])
            if not role:
                return await interaction.followup.send(
                    embed=embeds.error(
                        f"El rol configurado para **{shop_item['name']}** ya no existe en el servidor. "
                        "Avísale al staff."
                    ),
                    ephemeral=True,
                )
            if role >= interaction.guild.me.top_role:
                return await interaction.followup.send(
                    embed=embeds.error(
                        f"No puedo asignar el rol {role.mention} porque está por encima de mi rol más alto. "
                        "Pide al staff que baje la posición del rol del bot, o del rol del item."
                    ),
                    ephemeral=True,
                )

        balance = await db.get_balance(interaction.guild_id, interaction.user.id)
        if balance < shop_item["price"]:
            faltante = shop_item["price"] - balance
            return await interaction.followup.send(
                embed=embeds.error(
                    f"No te alcanza. **{shop_item['name']}** cuesta {fmt(shop_item['price'])} y "
                    f"tienes {fmt(balance)} (te faltan {fmt(faltante)})."
                ),
                ephemeral=True,
            )

        ok = await db.withdraw(interaction.guild_id, interaction.user.id, shop_item["price"])
        if not ok:
            # Alguien gastó el saldo justo antes (condición de carrera) — no cobramos ni entregamos.
            return await interaction.followup.send(
                embed=embeds.error("Tu saldo cambió justo antes de confirmar la compra. Inténtalo de nuevo."),
                ephemeral=True,
            )

        if role:
            try:
                await interaction.user.add_roles(role, reason=f"Compra en tienda: {shop_item['name']}")
            except discord.Forbidden:
                # Revertir el cobro si no se pudo entregar el rol.
                await db.deposit(interaction.guild_id, interaction.user.id, shop_item["price"])
                return await interaction.followup.send(
                    embed=embeds.error(
                        "No tengo permiso para asignarte ese rol, así que te devolví el dinero. "
                        "Avísale al staff para revisar mis permisos."
                    ),
                    ephemeral=True,
                )

        # Siempre generamos un código único de canje, sin importar el tipo de item.
        code = None
        for _ in range(5):
            candidate = _generate_code()
            existing = await db.get_purchase_by_code(interaction.guild_id, candidate)
            if not existing:
                code = candidate
                break
        if not code:
            code = _generate_code()  # último intento, colisión extremadamente improbable

        await db.create_shop_purchase(
            interaction.guild_id, interaction.user.id, shop_item["id"], shop_item["name"],
            shop_item["price"], code,
        )
        if shop_item["stock"] > 0:
            await db.decrement_shop_stock(shop_item["id"])

        new_balance = await db.get_balance(interaction.guild_id, interaction.user.id)
        lines = [f"Compraste **{shop_item['name']}** por {fmt(shop_item['price'])}.", f"Saldo restante: {fmt(new_balance)}"]
        if role:
            lines.append(f"Se te asignó el rol {role.mention}.")
        lines.append(f"\n🧾 Tu código de canje es: `{code}`\nTe lo mandé también por DM — guárdalo, el staff lo necesita para entregarte la recompensa.")
        embed = embeds.success("\n".join(lines), title="✅ Compra realizada")
        await interaction.followup.send(embed=embed, ephemeral=True)

        try:
            extra = f" ({shop_item['mc_reward']})" if shop_item["mc_reward"] else ""
            await interaction.user.send(
                f"🧾 Compraste **{shop_item['name']}**{extra} en {interaction.guild.name}.\n"
                f"Tu código de canje es: `{code}`\n"
                "Muéstraselo al staff para que te entreguen la recompensa."
            )
        except discord.Forbidden:
            pass  # DMs cerrados — el código ya quedó en la respuesta ephemeral de arriba

    @tienda_group.command(name="miscompras", description="Ver tu historial de compras y códigos de canje.")
    async def miscompras(self, interaction: discord.Interaction):
        purchases = await db.get_user_purchases(interaction.guild_id, interaction.user.id)
        if not purchases:
            return await interaction.response.send_message(
                embed=embeds.info("Todavía no has comprado nada en la tienda."), ephemeral=True
            )
        lines = []
        for p in purchases[:15]:
            estado = "✅ canjeado" if p["redeemed_ingame"] else "⏳ pendiente"
            lines.append(f"**{p['item_name']}** — {fmt(p['price_paid'])} — código `{p['code']}` ({estado})")
        embed = discord.Embed(title="🧾 Tus compras", description="\n".join(lines), color=COLOR_INFO)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─── Staff ────────────────────────────────────────────────────────────────

    @tienda_group.command(name="crear", description="(Staff) Crear un item nuevo en la tienda.")
    @app_commands.describe(
        nombre="Nombre del item (debe ser único)",
        precio=f"Precio en {CURRENCY_NAME}",
        rol="Rol de Discord que se otorga al comprarlo (opcional)",
        recompensa_minecraft="Nota descriptiva de la recompensa a entregar manualmente (opcional)",
        descripcion="Descripción que se muestra en /tienda ver (opcional)",
        stock="Cantidad disponible, -1 para ilimitado (por defecto ilimitado)",
    )
    @checks.is_staff()
    async def crear(
        self,
        interaction: discord.Interaction,
        nombre: str,
        precio: app_commands.Range[int, 1],
        rol: discord.Role = None,
        recompensa_minecraft: str = None,
        descripcion: str = None,
        stock: int = -1,
    ):
        existing = await db.get_shop_item_by_name(interaction.guild_id, nombre)
        if existing:
            return await interaction.response.send_message(
                embed=embeds.error(f"Ya existe un item llamado **{nombre}**. Elige otro nombre o elimínalo primero."),
                ephemeral=True,
            )
        await db.create_shop_item(
            interaction.guild_id, nombre, precio, description=descripcion,
            role_id=rol.id if rol else None, mc_reward=recompensa_minecraft,
            stock=stock, created_by=interaction.user.id,
        )
        await interaction.response.send_message(
            embed=embeds.success(f"Item **{nombre}** creado por {fmt(precio)}."), ephemeral=True
        )

    @tienda_group.command(name="eliminar", description="(Staff) Desactivar un item de la tienda.")
    @app_commands.describe(item="Nombre exacto del item a eliminar")
    @checks.is_staff()
    async def eliminar(self, interaction: discord.Interaction, item: str):
        shop_item = await db.get_shop_item_by_name(interaction.guild_id, item)
        if not shop_item:
            return await interaction.response.send_message(
                embed=embeds.error(f"No encontré ningún item llamado `{item}`."), ephemeral=True
            )
        await db.deactivate_shop_item(interaction.guild_id, shop_item["id"])
        await interaction.response.send_message(
            embed=embeds.success(f"**{shop_item['name']}** fue eliminado de la tienda."), ephemeral=True
        )

    @tienda_group.command(name="verificarcodigo", description="(Staff) Revisar si un código de canje ya fue usado.")
    @app_commands.describe(codigo="El código de canje (ej. ABCD-EFGH-JKLM)")
    @checks.is_staff()
    async def verificarcodigo(self, interaction: discord.Interaction, codigo: str):
        purchase = await db.get_purchase_by_code(interaction.guild_id, codigo.upper())
        if not purchase:
            return await interaction.response.send_message(
                embed=embeds.error(f"No existe ninguna compra con el código `{codigo}`."), ephemeral=True
            )
        if purchase["redeemed_ingame"]:
            estado = f"✅ **Ya fue canjeado** el {purchase['redeemed_at']} (por <@{purchase['redeemed_by']}>)."
        else:
            estado = "⏳ **Todavía no ha sido canjeado.**"
        embed = discord.Embed(
            title=f"Código `{purchase['code']}`",
            description=(
                f"Comprador: <@{purchase['user_id']}>\n"
                f"Item: **{purchase['item_name']}**\n"
                f"Precio pagado: {fmt(purchase['price_paid'])}\n"
                f"Fecha de compra: {purchase['created_at']}\n\n"
                f"{estado}"
            ),
            color=COLOR_INFO,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tienda_group.command(name="marcarcanjeado", description="(Staff) Marcar un código como ya canjeado/entregado.")
    @app_commands.describe(codigo="El código de canje (ej. ABCD-EFGH-JKLM)")
    @checks.is_staff()
    async def marcarcanjeado(self, interaction: discord.Interaction, codigo: str):
        purchase = await db.get_purchase_by_code(interaction.guild_id, codigo.upper())
        if not purchase:
            return await interaction.response.send_message(
                embed=embeds.error(f"No existe ninguna compra con el código `{codigo}`."), ephemeral=True
            )
        if purchase["redeemed_ingame"]:
            return await interaction.response.send_message(
                embed=embeds.warning(f"El código `{codigo}` ya estaba marcado como canjeado."), ephemeral=True
            )
        await db.mark_purchase_redeemed(interaction.guild_id, codigo.upper(), interaction.user.id)
        await interaction.response.send_message(
            embed=embeds.success(f"Código `{codigo}` marcado como canjeado (**{purchase['item_name']}**, comprador <@{purchase['user_id']}>)."),
            ephemeral=True,
        )

    @tienda_group.command(name="pendientes", description="(Staff) Ver todos los códigos de canje aún no entregados.")
    @checks.is_staff()
    async def pendientes(self, interaction: discord.Interaction):
        pending = await db.get_pending_purchases(interaction.guild_id)
        if not pending:
            return await interaction.response.send_message(
                embed=embeds.info("No hay ningún código pendiente de canjear."), ephemeral=True
            )
        lines = [
            f"`{p['code']}` — <@{p['user_id']}> — **{p['item_name']}**"
            for p in pending[:20]
        ]
        embed = discord.Embed(
            title="⏳ Códigos pendientes de canjear",
            description="\n".join(lines),
            color=COLOR_INFO,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Shop(bot))
