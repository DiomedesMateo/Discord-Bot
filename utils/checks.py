"""
Custom permission checks para slash commands — jerarquía de rangos
configurable por servidor.
"""
import discord
from discord import app_commands
from database import db

STAFF_ROLE_ID = 1486944039566573671
MOD_ROLE_ID   = 1486944039566573674

RANK_ORDER = [
    "staff",
    "trial_mod",
    "mod",
    "sr_mod",
    "jr_admin",
    "admin",
    "sr_admin",
    "platform_admin",
    "manager",
    "owner",
]

RANK_LABELS = {
    "staff": "Staff/Learner",
    "trial_mod": "Trial-Mod",
    "mod": "Mod",
    "sr_mod": "SrMod",
    "jr_admin": "JrAdmin",
    "admin": "Admin",
    "sr_admin": "SrAdmin",
    "platform_admin": "Platform-Admin",
    "manager": "Manager",
    "owner": "Owner",
}


def _has_admin_permission(interaction: discord.Interaction) -> bool:
    """True si el usuario tiene el permiso nativo de Discord 'Administrador'
    en este servidor (sin importar qué rol se lo dé)."""
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.administrator)


async def _meets_min_rank(interaction: discord.Interaction, min_rank: str) -> bool:
    if _has_admin_permission(interaction):
        return True

    if min_rank not in RANK_ORDER:
        return False

    user_role_ids = {r.id for r in interaction.user.roles}
    configured = await db.get_rank_roles(interaction.guild_id)

    start = RANK_ORDER.index(min_rank)
    for rank_key in RANK_ORDER[start:]:
        role_id = configured.get(rank_key)
        if role_id and role_id in user_role_ids:
            return True

    if min_rank == "staff" and (STAFF_ROLE_ID in user_role_ids or MOD_ROLE_ID in user_role_ids) \
            and "staff" not in configured and "mod" not in configured:
        return True
    if min_rank == "mod" and MOD_ROLE_ID in user_role_ids and "mod" not in configured:
        return True

    return False


def _require_rank(min_rank: str):
    async def predicate(interaction: discord.Interaction) -> bool:
        if await _meets_min_rank(interaction, min_rank):
            return True
        label = RANK_LABELS.get(min_rank, min_rank)
        raise app_commands.CheckFailure(
            f"Necesitas el rango **{label}** (o superior) para usar este comando."
        )
    return app_commands.check(predicate)


def is_staff():
    return _require_rank("staff")


def is_trial_mod():
    return _require_rank("trial_mod")


def is_mod():
    return _require_rank("mod")


def is_sr_mod():
    return _require_rank("sr_mod")


def is_jr_admin():
    return _require_rank("jr_admin")


def is_admin():
    return _require_rank("admin")


def is_sr_admin():
    return _require_rank("sr_admin")


def is_platform_admin():
    return _require_rank("platform_admin")


def is_manager():
    return _require_rank("manager")


def is_owner():
    return _require_rank("owner")


is_staff_member = is_staff


async def check_is_mod(interaction: discord.Interaction) -> bool:
    """Misma lógica que is_mod() pero sin levantar CheckFailure — pensado
    para usar dentro de callbacks de botones/vistas, donde no hay
    decorador (ver PollView, giveaways, etc.)."""
    return await _meets_min_rank(interaction, "mod")


def is_at_least(rank_key: str):
    """Chequeo genérico para cuando se necesita un rango que no tiene
    helper dedicado, o para usarlo dinámicamente. Ej: checks.is_at_least('platform_admin')"""
    return _require_rank(rank_key)
