"""
API HTTP interna para canjear códigos de cumpleaños desde Minecraft (Skript).

Autenticación: sin Replit ni tokens externos. La clave que usa el script se
DERIVA del token del bot (SHA-256), así el token real nunca sale del bot:

    clave = sha256("mineorbit-birthday-api:" + DISCORD_BOT_TOKEN)

Variables opcionales en .env:
    BIRTHDAY_API_HOST (default 0.0.0.0; usa 127.0.0.1 si el MC está en la misma máquina)
    BIRTHDAY_API_PORT (default 8080)

Respuestas: 200 canjeado | 400 inválido/vencido/formato | 409 ya usado
            401 clave incorrecta | 500 error.
"""
import hashlib
import hmac
import os
import re

import aiosqlite
from aiohttp import web
from discord.ext import commands

import database.db as db

CODE_RE = re.compile(r"^[A-HJ-NP-Z2-9]{16}$")
PLAYER_RE = re.compile(r"^[A-Za-z0-9_.]{1,32}$")


def derive_key(token: str) -> str:
    return hashlib.sha256(f"mineorbit-birthday-api:{token}".encode()).hexdigest()


async def redeem_code(code: str, player: str) -> str:
    """Canje atómico. Devuelve 'ok', 'invalid', 'used' o 'expired'.
    Autocontenido: no requiere modificar database/db.py."""
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(f"PRAGMA busy_timeout={db.BUSY_TIMEOUT_MS}")
        cur = await conn.execute(
            """UPDATE birthday_reward_codes
               SET redeemed_at=datetime('now'), minecraft_player=?
               WHERE code=? AND redeemed_at IS NULL AND expires_at > datetime('now')""",
            (player, code),
        )
        changed = cur.rowcount
        await conn.commit()
        if changed == 1:
            return "ok"
        async with conn.execute(
            "SELECT redeemed_at FROM birthday_reward_codes WHERE code=?", (code,)
        ) as c2:
            row = await c2.fetchone()
        if not row:
            return "invalid"
        return "used" if row["redeemed_at"] else "expired"


class BirthdayAPI(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.runner: web.AppRunner | None = None
        self.key = derive_key(os.environ.get("DISCORD_BOT_TOKEN", ""))

    async def cog_load(self):
        app = web.Application(client_max_size=4096)
        app.router.add_post("/api/minecraft/birthday/redeem", self.redeem)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        host = os.environ.get("BIRTHDAY_API_HOST", "0.0.0.0")
        port = int(os.environ.get("BIRTHDAY_API_PORT", "8080"))
        await web.TCPSite(self.runner, host, port).start()
        print(f"[BirthdayAPI] Escuchando en {host}:{port}")

    async def cog_unload(self):
        if self.runner:
            await self.runner.cleanup()

    async def redeem(self, request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        supplied = auth[7:].strip() if auth.startswith("Bearer ") else ""
        if not hmac.compare_digest(supplied, self.key):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            data = await request.json()
            code = str(data.get("code", "")).replace("-", "").upper()
            player = str(data.get("player", ""))
        except Exception:
            return web.json_response({"error": "bad_request"}, status=400)
        if not CODE_RE.match(code) or not PLAYER_RE.match(player):
            return web.json_response({"error": "invalid"}, status=400)
        try:
            result = await redeem_code(code, player)
        except Exception as e:
            print(f"[BirthdayAPI] Error: {e}")
            return web.json_response({"error": "server_error"}, status=500)
        if result == "ok":
            return web.json_response({"ok": True})
        return web.json_response({"error": result}, status=409 if result == "used" else 400)


async def setup(bot: commands.Bot):
    await bot.add_cog(BirthdayAPI(bot))
