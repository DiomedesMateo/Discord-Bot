"""
Database layer — all async SQLite via aiosqlite.
Call `init_db()` once at startup; then use helpers throughout.
"""

import json
import aiosqlite
import secrets

DB_PATH = "discord_bot.db"

# [FIX] Tiempo (ms) que SQLite espera a que se libere un lock antes de
# lanzar "database is locked". Sin esto, cualquier escritura concurrente
# (ej: muchas personas dándole a "Participar" mientras el loop de sorteos
# está terminando uno) puede fallar instantáneamente en vez de esperar,
# y esa excepción termina tragada en silencio en código que hace
# `except Exception: pass` (ver giveaways.py _finish_giveaway).
BUSY_TIMEOUT_MS = 5000


async def init_db() -> None:
    """Create all tables if they don't exist yet."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        await db.executescript("""
            PRAGMA journal_mode=WAL;

            -- ── Per-guild settings ─────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id              INTEGER PRIMARY KEY,
                mod_log_channel       INTEGER,
                ticket_category       INTEGER,
                ticket_log_channel    INTEGER,
                welcome_channel       INTEGER,
                goodbye_channel       INTEGER,
                suggestion_channel    INTEGER,
                staff_log_channel     INTEGER,
                verification_channel  INTEGER,
                verification_role     INTEGER,
                admin_role            INTEGER,
                mod_role              INTEGER,
                staff_role            INTEGER,
                mute_role             INTEGER,
                birthday_channel      INTEGER,
                warning_threshold     INTEGER DEFAULT 10,
                stats_channel_id      INTEGER,
                achievements_channel  INTEGER,
                updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- ── Rangos configurables (jerarquía de permisos) ─────────────────
            -- Permite mapear cada rango (trial_mod, mod, sr_mod, jr_admin,
            -- admin, sr_admin, platform_admin, manager, owner, y staff) a un
            -- rol real de Discord, por servidor, en vez de tener IDs fijos
            -- hardcodeados en el código.
            CREATE TABLE IF NOT EXISTS rank_roles (
                guild_id   INTEGER NOT NULL,
                rank_key   TEXT NOT NULL,
                role_id    INTEGER NOT NULL,
                PRIMARY KEY (guild_id, rank_key)
            );

            -- ── Giveaways ──────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS giveaways (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id          INTEGER NOT NULL,
                channel_id        INTEGER NOT NULL,
                message_id        INTEGER,
                host_id           INTEGER NOT NULL,
                prize             TEXT NOT NULL,
                winners_count     INTEGER DEFAULT 1,
                end_time          TEXT NOT NULL,
                ended             INTEGER DEFAULT 0,
                required_role     INTEGER,
                required_min_days INTEGER,
                required_invites  INTEGER,
                created_at        TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS giveaway_entries (
                giveaway_id  INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                entered_at   TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (giveaway_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS giveaway_winners (
                giveaway_id  INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                won_at       TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (giveaway_id, user_id)
            );

            -- ── Tickets ────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS tickets (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id          INTEGER NOT NULL,
                channel_id        INTEGER NOT NULL,
                user_id           INTEGER NOT NULL,
                category          TEXT NOT NULL,
                priority          TEXT DEFAULT 'low',
                status            TEXT DEFAULT 'open',
                claimed_by        INTEGER,
                assigned_to       INTEGER,
                subject           TEXT,
                description       TEXT,
                tags              TEXT DEFAULT '[]',
                close_reason      TEXT,
                added_staff       TEXT DEFAULT '[]',
                transcript_url    TEXT,
                dm_transcript_url TEXT,
                notified_inactive  INTEGER DEFAULT 0,
                notified_escalated INTEGER DEFAULT 0,
                opened_at         TEXT DEFAULT (datetime('now')),
                closed_at         TEXT,
                first_response_at TEXT,
                last_message_at   TEXT DEFAULT (datetime('now')),
                rating            INTEGER,
                rating_comment    TEXT
            );

            CREATE TABLE IF NOT EXISTS ticket_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id   INTEGER NOT NULL,
                author_id   INTEGER NOT NULL,
                author_name TEXT NOT NULL,
                content     TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (ticket_id) REFERENCES tickets(id)
            );

            -- ── Moderation ─────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS mod_cases (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     INTEGER NOT NULL,
                case_number  INTEGER NOT NULL,
                type         TEXT NOT NULL,
                user_id      INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason       TEXT,
                duration     INTEGER,
                active       INTEGER DEFAULT 1,
                created_at   TEXT DEFAULT (datetime('now')),
                expires_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS warnings (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason       TEXT NOT NULL,
                points       INTEGER DEFAULT 1,
                active       INTEGER DEFAULT 1,
                created_at   TEXT DEFAULT (datetime('now'))
            );

            -- ── Staff ──────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS staff_activity (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                target_id   INTEGER,
                details     TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS staff_strikes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                issued_by  INTEGER NOT NULL,
                reason     TEXT NOT NULL,
                active     INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );

            -- Marca de reinicio del contador de tickets reclamados por staff.
            -- No se borran las filas de staff_activity (se necesitan para
            -- otras estadísticas, p. ej. get_staff_leaderboard); en vez de
            -- eso se guarda desde cuándo debe contar get_ticket_claim_count.
            CREATE TABLE IF NOT EXISTS staff_claim_resets (
                guild_id   INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                reset_at   TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (guild_id, user_id)
            );

            -- ── Notas internas de staff sobre usuarios (/nota) ────────────────
            CREATE TABLE IF NOT EXISTS staff_notes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                author_id  INTEGER NOT NULL,
                note       TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS staff_applications (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                answers     TEXT NOT NULL,
                status      TEXT DEFAULT 'pending',
                reviewed_by INTEGER,
                feedback    TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            -- ── Suggestions ────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS suggestions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     INTEGER NOT NULL,
                channel_id   INTEGER NOT NULL,
                message_id   INTEGER,
                user_id      INTEGER NOT NULL,
                content      TEXT NOT NULL,
                status       TEXT DEFAULT 'pending',
                response     TEXT,
                responded_by INTEGER,
                upvotes      INTEGER DEFAULT 0,
                downvotes    INTEGER DEFAULT 0,
                created_at   TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS suggestion_votes (
                suggestion_id INTEGER NOT NULL,
                user_id       INTEGER NOT NULL,
                vote          INTEGER NOT NULL,
                PRIMARY KEY (suggestion_id, user_id)
            );

            -- ── Welcome / Goodbye ──────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS welcome_config (
                guild_id        INTEGER PRIMARY KEY,
                welcome_channel INTEGER,
                welcome_message TEXT,
                welcome_embed   INTEGER DEFAULT 1,
                goodbye_channel INTEGER,
                goodbye_message TEXT,
                auto_role       INTEGER,
                dm_welcome      INTEGER DEFAULT 0,
                dm_message      TEXT
            );

            -- ── AutoMod ────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS automod_config (
                guild_id        INTEGER PRIMARY KEY,
                anti_spam       INTEGER DEFAULT 1,
                anti_links      INTEGER DEFAULT 0,
                anti_mentions   INTEGER DEFAULT 1,
                anti_caps       INTEGER DEFAULT 0,
                anti_raid       INTEGER DEFAULT 0,
                anti_bot        INTEGER DEFAULT 0,
                log_channel     INTEGER,
                exempt_roles    TEXT DEFAULT '[]',
                exempt_channels TEXT DEFAULT '[]',
                spam_action     TEXT DEFAULT 'warn',
                link_action     TEXT DEFAULT 'delete',
                raid_action     TEXT DEFAULT 'lockdown',
                bot_whitelist   TEXT DEFAULT '[]'
            );

            -- ── Lockdown de servidor (/lockdown) ──────────────────────────
            CREATE TABLE IF NOT EXISTS lockdown_state (
                guild_id              INTEGER PRIMARY KEY,
                channel_ids           TEXT NOT NULL,
                previous_verification INTEGER,
                activated_by          INTEGER NOT NULL,
                created_at            TEXT DEFAULT (datetime('now'))
            );

            -- ── Reaction Roles ─────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS reaction_roles (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                emoji      TEXT NOT NULL,
                role_id    INTEGER NOT NULL,
                panel      INTEGER DEFAULT 0,
                label      TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            -- ── Reminders ──────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS reminders (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                content    TEXT NOT NULL,
                remind_at  TEXT NOT NULL,
                sent       INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );

            -- ── Scheduled Messages ─────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS scheduled_messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        INTEGER NOT NULL,
                channel_id      INTEGER NOT NULL,
                content         TEXT NOT NULL,
                send_at         TEXT NOT NULL,
                repeat_interval INTEGER,
                sent            INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            -- ── Appeals ────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS appeals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                case_id     INTEGER,
                reason      TEXT NOT NULL,
                status      TEXT DEFAULT 'pending',
                reviewed_by INTEGER,
                response    TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            -- ── Verification ───────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS verification_config (
                guild_id   INTEGER PRIMARY KEY,
                channel_id INTEGER,
                role_id    INTEGER,
                mode       TEXT DEFAULT 'button',
                message    TEXT
            );

            -- ── Announcements ──────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS announcements (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER,
                content    TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            -- ── Birthdays ──────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS birthdays (
                user_id  INTEGER PRIMARY KEY,
                birthday TEXT NOT NULL,
                set_at   TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS birthday_reward_codes (
                code             TEXT PRIMARY KEY,
                user_id          INTEGER NOT NULL,
                birthday_year    INTEGER NOT NULL,
                expires_at       TEXT NOT NULL,
                sent_at          TEXT,
                redeemed_at      TEXT,
                minecraft_player TEXT,
                created_at       TEXT DEFAULT (datetime('now')),
                UNIQUE (user_id, birthday_year)
            );

            -- ── Economía / Minijuegos ────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS economy (
                guild_id INTEGER NOT NULL,
                user_id  INTEGER NOT NULL,
                balance  INTEGER DEFAULT 0,
                wins     INTEGER DEFAULT 0,
                losses   INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );

            -- ── Vinculación Minecraft ─────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS minecraft_links (
                discord_id     INTEGER PRIMARY KEY,
                minecraft_nick TEXT NOT NULL,
                minecraft_uuid TEXT UNIQUE,
                linked_at      TEXT DEFAULT (datetime('now'))
            );

            -- ── Torneos ───────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS tournaments (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id       INTEGER NOT NULL,
                channel_id     INTEGER NOT NULL,
                message_id     INTEGER,
                host_id        INTEGER NOT NULL,
                name           TEXT    NOT NULL,
                mode           TEXT    DEFAULT 'eliminacion_simple',
                prize          TEXT,
                slots          INTEGER NOT NULL,
                start_time     TEXT    NOT NULL,
                status         TEXT    DEFAULT 'open',
                current_round  INTEGER DEFAULT 0,
                champion_id    INTEGER,
                runner_up_id   INTEGER,
                third_place_id INTEGER,
                created_at     TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS tournament_participants (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                user_id       INTEGER NOT NULL,
                joined_at     TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id),
                UNIQUE (tournament_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS tournament_matches (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id  INTEGER NOT NULL,
                round          INTEGER NOT NULL,
                match_number   INTEGER NOT NULL,
                player1_id     INTEGER,
                player2_id     INTEGER,
                winner_id      INTEGER,
                loser_id       INTEGER,
                is_third_place INTEGER DEFAULT 0,
                status         TEXT    DEFAULT 'pending',
                created_at     TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id)
            );

            -- ── Tienda de rangos/cosméticos ─────────────────────────────────
            CREATE TABLE IF NOT EXISTS shop_items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     INTEGER NOT NULL,
                name         TEXT    NOT NULL,
                description  TEXT,
                price        INTEGER NOT NULL,
                role_id      INTEGER,              -- rol de Discord a otorgar (opcional)
                mc_reward    TEXT,                  -- nombre libre del rango/perk en Minecraft (opcional)
                stock        INTEGER DEFAULT -1,    -- -1 = ilimitado
                active       INTEGER DEFAULT 1,
                created_by   INTEGER,
                created_at   TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS shop_purchases (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id         INTEGER NOT NULL,
                user_id          INTEGER NOT NULL,
                item_id          INTEGER NOT NULL,
                item_name        TEXT    NOT NULL,   -- copia del nombre al momento de compra
                price_paid       INTEGER NOT NULL,
                code             TEXT    UNIQUE NOT NULL,
                minecraft_uuid   TEXT,                -- copia del UUID vinculado al momento de compra (si tenía)
                redeemed_ingame  INTEGER DEFAULT 0,
                redeemed_by      INTEGER,             -- id del staff que marcó el canje manual
                redeemed_at      TEXT,
                created_at       TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (item_id) REFERENCES shop_items(id)
            );

            -- ── Confesiones anónimas ──────────────────────────────────────
            CREATE TABLE IF NOT EXISTS confession_config (
                guild_id     INTEGER PRIMARY KEY,
                channel_id   INTEGER,
                next_number  INTEGER DEFAULT 1,
                enabled      INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS confessions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     INTEGER NOT NULL,
                number       INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                content      TEXT    NOT NULL,
                message_id   INTEGER,
                created_at   TEXT    DEFAULT (datetime('now'))
            );

            -- ── Anti-Scam Guard (canal trampa) ────────────────────────────
            CREATE TABLE IF NOT EXISTS scamguard_config (
                guild_id          INTEGER PRIMARY KEY,
                channel_id        INTEGER,
                notice_message_id INTEGER,
                enabled           INTEGER DEFAULT 0,
                allow_images      INTEGER DEFAULT 0,
                banned_count      INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS scamguard_bans (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                channel_id  INTEGER,
                content     TEXT,
                attachments INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            -- ── Rastreo de invitaciones ───────────────────────────────────
            CREATE TABLE IF NOT EXISTS invite_uses (
                guild_id     INTEGER NOT NULL,
                inviter_id   INTEGER NOT NULL,
                uses         INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, inviter_id)
            );

            CREATE TABLE IF NOT EXISTS invite_joins (
                guild_id            INTEGER NOT NULL,
                member_id           INTEGER NOT NULL,
                inviter_id          INTEGER,
                invite_code         TEXT,
                joined_at           TEXT DEFAULT (datetime('now')),
                account_created_at  TEXT,
                counted             INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, member_id)
            );

            -- ── Canales de voz temporales (Join to Create) ────────────────
            CREATE TABLE IF NOT EXISTS voice_hub_config (
                guild_id          INTEGER PRIMARY KEY,
                anchor_channel_id INTEGER,
                category_id       INTEGER,
                name_template     TEXT DEFAULT '🔊 Canal de {user}'
            );

            CREATE TABLE IF NOT EXISTS temp_voice_channels (
                channel_id  INTEGER PRIMARY KEY,
                guild_id    INTEGER NOT NULL,
                owner_id    INTEGER NOT NULL,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            -- ── Niveles (XP por mensajes) ──────────────────────────────────
            CREATE TABLE IF NOT EXISTS levels (
                guild_id       INTEGER NOT NULL,
                user_id        INTEGER NOT NULL,
                xp             INTEGER DEFAULT 0,
                level          INTEGER DEFAULT 0,
                messages_count INTEGER DEFAULT 0,
                last_xp_at     TEXT,
                PRIMARY KEY (guild_id, user_id)
            );

            -- ── Logros desbloqueados ────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS user_achievements (
                guild_id        INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                achievement_key TEXT NOT NULL,
                unlocked_at     TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (guild_id, user_id, achievement_key)
            );

            -- ── Progreso auxiliar para logros que no tienen un contador propio ──
            CREATE TABLE IF NOT EXISTS achievement_progress (
                guild_id                 INTEGER NOT NULL,
                user_id                  INTEGER NOT NULL,
                voice_seconds            INTEGER DEFAULT 0,
                files_uploaded           INTEGER DEFAULT 0,
                pins_earned              INTEGER DEFAULT 0,
                reactions_given          INTEGER DEFAULT 0,
                reactions_received       INTEGER DEFAULT 0,
                suggestion_best_upvotes  INTEGER DEFAULT 0,
                suggestion_total_upvotes INTEGER DEFAULT 0,
                streak_days              INTEGER DEFAULT 0,
                streak_last_date         TEXT,
                PRIMARY KEY (guild_id, user_id)
            );

            -- Evita contar el mismo mensaje fijado más de una vez.
            CREATE TABLE IF NOT EXISTS pin_events (
                message_id INTEGER PRIMARY KEY,
                guild_id   INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            -- Evita contar la misma reacción "dada" dos veces por el mismo
            -- usuario en el mismo mensaje (quitar y volver a poner la reacción).
            CREATE TABLE IF NOT EXISTS reaction_given_events (
                message_id INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (message_id, user_id)
            );
        """)
        await db.commit()

        # Column migrations — safe (ignored if column already exists).
        for _migration in [
            # tickets
            "ALTER TABLE tickets ADD COLUMN close_reason TEXT",
            "ALTER TABLE tickets ADD COLUMN added_staff TEXT DEFAULT '[]'",
            "ALTER TABLE tickets ADD COLUMN transcript_url TEXT",
            "ALTER TABLE tickets ADD COLUMN dm_transcript_url TEXT",
            "ALTER TABLE tickets ADD COLUMN notified_inactive INTEGER DEFAULT 0",
            "ALTER TABLE tickets ADD COLUMN notified_escalated INTEGER DEFAULT 0",
            # guild_config
            "ALTER TABLE guild_config ADD COLUMN birthday_channel INTEGER",
            "ALTER TABLE guild_config ADD COLUMN warning_threshold INTEGER DEFAULT 10",
            # automod_config
            "ALTER TABLE automod_config ADD COLUMN bot_whitelist TEXT DEFAULT '[]'",
            # reaction_roles
            "ALTER TABLE reaction_roles ADD COLUMN panel INTEGER DEFAULT 0",
            "ALTER TABLE reaction_roles ADD COLUMN label TEXT",
            # guild_config — canal de voz de estadísticas
            "ALTER TABLE guild_config ADD COLUMN stats_channel_id INTEGER",
            # guild_config — modo mantenimiento
            "ALTER TABLE guild_config ADD COLUMN maintenance_active INTEGER DEFAULT 0",
            "ALTER TABLE guild_config ADD COLUMN maintenance_channel INTEGER",
            "ALTER TABLE guild_config ADD COLUMN maintenance_overrides TEXT",
            # economy
            "ALTER TABLE economy ADD COLUMN wins INTEGER DEFAULT 0",
            "ALTER TABLE economy ADD COLUMN losses INTEGER DEFAULT 0",
            "ALTER TABLE economy ADD COLUMN last_daily TEXT",
            "ALTER TABLE economy ADD COLUMN last_work TEXT",
            "ALTER TABLE economy ADD COLUMN last_rob TEXT",
            # guild_config — panel de estado del servidor de Minecraft (auto-update)
            "ALTER TABLE guild_config ADD COLUMN mc_status_channel_id INTEGER",
            "ALTER TABLE guild_config ADD COLUMN mc_status_message_id INTEGER",
            # welcome_config — canal de agradecimiento por boost
            "ALTER TABLE welcome_config ADD COLUMN boost_channel INTEGER",
            # guild_config — canal de anuncios de logros y subidas de nivel
            "ALTER TABLE guild_config ADD COLUMN achievements_channel INTEGER",
            # invite_joins — filtro antifraude (antigüedad de cuenta + verificación)
            "ALTER TABLE invite_joins ADD COLUMN account_created_at TEXT",
            "ALTER TABLE invite_joins ADD COLUMN counted INTEGER DEFAULT 0",
        ]:
            try:
                await db.execute(_migration)
                await db.commit()
            except Exception:
                pass  # columna ya existe — ignorar


# ─── Generic helpers ─────────────────────────────────────────────────────────


async def fetchone(query: str, params: tuple = ()) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        async with db.execute(query, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def fetchall(query: str, params: tuple = ()) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def execute(query: str, params: tuple = ()) -> int:
    """Execute a write query and return lastrowid."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        async with db.execute(query, params) as cur:
            await db.commit()
            return cur.lastrowid


# ─── Guild config ─────────────────────────────────────────────────────────────


async def get_guild_config(guild_id: int) -> dict:
    row = await fetchone("SELECT * FROM guild_config WHERE guild_id=?", (guild_id,))
    if not row:
        await execute(
            "INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)", (guild_id,)
        )
        row = await fetchone("SELECT * FROM guild_config WHERE guild_id=?", (guild_id,))
    return row or {}


async def set_guild_config(guild_id: int, **kwargs) -> None:
    if not kwargs:
        return
    await execute(
        "INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)", (guild_id,)
    )
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [guild_id]
    await execute(
        f"UPDATE guild_config SET {cols}, updated_at=datetime('now') WHERE guild_id=?",
        tuple(vals),
    )


# ─── Rangos configurables ───────────────────────────────────────────────────


async def set_rank_role(guild_id: int, rank_key: str, role_id: int) -> None:
    await execute(
        """INSERT INTO rank_roles (guild_id, rank_key, role_id) VALUES (?,?,?)
           ON CONFLICT(guild_id, rank_key) DO UPDATE SET role_id=excluded.role_id""",
        (guild_id, rank_key, role_id),
    )


async def unset_rank_role(guild_id: int, rank_key: str) -> None:
    await execute(
        "DELETE FROM rank_roles WHERE guild_id=? AND rank_key=?",
        (guild_id, rank_key),
    )


async def get_rank_roles(guild_id: int) -> dict[str, int]:
    """Devuelve {rank_key: role_id} con todos los rangos configurados en
    este servidor. Los rangos sin configurar simplemente no aparecen."""
    rows = await fetchall(
        "SELECT rank_key, role_id FROM rank_roles WHERE guild_id=?", (guild_id,)
    )
    return {r["rank_key"]: r["role_id"] for r in rows}


# ─── Giveaway helpers ─────────────────────────────────────────────────────────


async def create_giveaway(
    guild_id,
    channel_id,
    host_id,
    prize,
    winners,
    end_time,
    required_role=None,
    required_min_days=None,
    required_invites=None,
) -> int:
    return await execute(
        """INSERT INTO giveaways
           (guild_id, channel_id, host_id, prize, winners_count, end_time,
            required_role, required_min_days, required_invites)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            guild_id,
            channel_id,
            host_id,
            prize,
            winners,
            end_time,
            required_role,
            required_min_days,
            required_invites,
        ),
    )


async def get_giveaway(giveaway_id: int) -> dict | None:
    return await fetchone("SELECT * FROM giveaways WHERE id=?", (giveaway_id,))


async def get_active_giveaways(guild_id: int) -> list[dict]:
    return await fetchall(
        "SELECT * FROM giveaways WHERE guild_id=? AND ended=0 ORDER BY end_time",
        (guild_id,),
    )


async def get_all_active_giveaways() -> list[dict]:
    return await fetchall("SELECT * FROM giveaways WHERE ended=0 ORDER BY end_time")


async def set_giveaway_message(giveaway_id: int, message_id: int) -> None:
    await execute(
        "UPDATE giveaways SET message_id=? WHERE id=?", (message_id, giveaway_id)
    )


async def end_giveaway(giveaway_id: int) -> bool:
    """Marca el sorteo como terminado SOLO si no lo estaba ya (atómico).
    Devuelve True si ESTA llamada fue la que lo terminó; False si otra
    ejecución concurrente ya lo había terminado."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur = await db.execute(
            "UPDATE giveaways SET ended=1 WHERE id=? AND ended=0", (giveaway_id,)
        )
        await db.commit()
        return cur.rowcount > 0


async def add_giveaway_entry(giveaway_id: int, user_id: int) -> bool:
    try:
        await execute(
            "INSERT INTO giveaway_entries (giveaway_id, user_id) VALUES (?,?)",
            (giveaway_id, user_id),
        )
        return True
    except Exception:
        return False


async def remove_giveaway_entry(giveaway_id: int, user_id: int) -> None:
    await execute(
        "DELETE FROM giveaway_entries WHERE giveaway_id=? AND user_id=?",
        (giveaway_id, user_id),
    )


async def get_giveaway_entries(giveaway_id: int) -> list[dict]:
    return await fetchall(
        "SELECT * FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,)
    )


async def add_giveaway_winner(giveaway_id: int, user_id: int) -> None:
    await execute(
        "INSERT OR IGNORE INTO giveaway_winners (giveaway_id, user_id) VALUES (?,?)",
        (giveaway_id, user_id),
    )


async def get_giveaway_winners(giveaway_id: int) -> list[dict]:
    return await fetchall(
        "SELECT * FROM giveaway_winners WHERE giveaway_id=?", (giveaway_id,)
    )


# ─── Ticket helpers ───────────────────────────────────────────────────────────


async def create_ticket(
    guild_id, channel_id, user_id, category, subject=None, description=None
) -> int:
    return await execute(
        """INSERT INTO tickets (guild_id, channel_id, user_id, category, subject, description)
           VALUES (?,?,?,?,?,?)""",
        (guild_id, channel_id, user_id, category, subject, description),
    )


async def get_ticket(ticket_id: int) -> dict | None:
    return await fetchone("SELECT * FROM tickets WHERE id=?", (ticket_id,))


async def get_ticket_by_channel(channel_id: int) -> dict | None:
    return await fetchone("SELECT * FROM tickets WHERE channel_id=?", (channel_id,))


async def get_user_tickets(guild_id: int, user_id: int) -> list[dict]:
    return await fetchall(
        "SELECT * FROM tickets WHERE guild_id=? AND user_id=? ORDER BY opened_at DESC",
        (guild_id, user_id),
    )


async def get_open_tickets(guild_id: int) -> list[dict]:
    return await fetchall(
        "SELECT * FROM tickets WHERE guild_id=? AND status='open' ORDER BY opened_at DESC",
        (guild_id,),
    )


async def update_ticket(ticket_id: int, **kwargs) -> None:
    if not kwargs:
        return
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [ticket_id]
    await execute(f"UPDATE tickets SET {cols} WHERE id=?", tuple(vals))


async def claim_ticket(ticket_id: int, user_id: int) -> bool:
    """Reclama el ticket atómicamente SOLO si no tenía claimed_by asignado.
    Devuelve True si ESTA llamada lo reclamó; False si otro ya lo había hecho."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur = await conn.execute(
            "UPDATE tickets SET claimed_by=? WHERE id=? AND claimed_by IS NULL",
            (user_id, ticket_id),
        )
        await conn.commit()
        return cur.rowcount > 0


async def release_ticket(
    ticket_id: int, expected_claimed_by: int | None = None
) -> bool:
    """Libera el reclamo de un ticket (claimed_by → NULL).
    Si expected_claimed_by se especifica, solo libera si coincide con el claimer actual.
    Devuelve True si esta llamada liberó algo."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        if expected_claimed_by is None:
            cur = await conn.execute(
                "UPDATE tickets SET claimed_by=NULL WHERE id=?", (ticket_id,)
            )
        else:
            cur = await conn.execute(
                "UPDATE tickets SET claimed_by=NULL WHERE id=? AND claimed_by=?",
                (ticket_id, expected_claimed_by),
            )
        await conn.commit()
        return cur.rowcount > 0


async def log_ticket_message(
    ticket_id: int, author_id: int, author_name: str, content: str
) -> None:
    await execute(
        "INSERT INTO ticket_messages (ticket_id, author_id, author_name, content) VALUES (?,?,?,?)",
        (ticket_id, author_id, author_name, content),
    )


async def get_ticket_messages(ticket_id: int) -> list[dict]:
    return await fetchall(
        "SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY created_at",
        (ticket_id,),
    )


_VALID_NOTIFIED_COLUMNS = {"notified_inactive", "notified_escalated"}


async def get_inactive_tickets(
    hours: int, notified_column: str | None = None
) -> list[dict]:
    query = f"""SELECT * FROM tickets WHERE status='open'
                AND last_message_at < datetime('now', '-{hours} hours')"""
    if notified_column:
        if notified_column not in _VALID_NOTIFIED_COLUMNS:
            raise ValueError(f"notified_column inválido: {notified_column!r}")
        query += f" AND {notified_column}=0"
    return await fetchall(query)


async def mark_ticket_notified_inactive(ticket_id: int) -> None:
    await execute("UPDATE tickets SET notified_inactive=1 WHERE id=?", (ticket_id,))


async def mark_ticket_notified_escalated(ticket_id: int) -> None:
    await execute("UPDATE tickets SET notified_escalated=1 WHERE id=?", (ticket_id,))


async def reset_ticket_notifications(ticket_id: int) -> None:
    await execute(
        "UPDATE tickets SET notified_inactive=0, notified_escalated=0 WHERE id=?",
        (ticket_id,),
    )


async def get_ticket_message_counts(ticket_id: int) -> dict:
    rows = await fetchall(
        "SELECT author_id, COUNT(*) as cnt FROM ticket_messages WHERE ticket_id=? GROUP BY author_id",
        (ticket_id,),
    )
    return {r["author_id"]: r["cnt"] for r in rows}


# ─── Moderation helpers ───────────────────────────────────────────────────────


async def get_next_case_number(guild_id: int) -> int:
    row = await fetchone(
        "SELECT MAX(case_number) as max_case FROM mod_cases WHERE guild_id=?",
        (guild_id,),
    )
    return (row["max_case"] or 0) + 1 if row else 1


async def create_mod_case(
    guild_id,
    case_type,
    user_id,
    moderator_id,
    reason=None,
    duration=None,
    expires_at=None,
) -> int:
    case_num = await get_next_case_number(guild_id)
    return await execute(
        """INSERT INTO mod_cases (guild_id, case_number, type, user_id, moderator_id,
           reason, duration, expires_at) VALUES (?,?,?,?,?,?,?,?)""",
        (
            guild_id,
            case_num,
            case_type,
            user_id,
            moderator_id,
            reason,
            duration,
            expires_at,
        ),
    )


async def get_user_cases(guild_id: int, user_id: int) -> list[dict]:
    return await fetchall(
        "SELECT * FROM mod_cases WHERE guild_id=? AND user_id=? ORDER BY created_at DESC",
        (guild_id, user_id),
    )


async def get_case(guild_id: int, case_number: int) -> dict | None:
    return await fetchone(
        "SELECT * FROM mod_cases WHERE guild_id=? AND case_number=?",
        (guild_id, case_number),
    )


async def add_warning(
    guild_id: int, user_id: int, moderator_id: int, reason: str, points: int = 1
) -> int:
    return await execute(
        "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, points) VALUES (?,?,?,?,?)",
        (guild_id, user_id, moderator_id, reason, points),
    )


async def get_warnings(
    guild_id: int, user_id: int, active_only: bool = True
) -> list[dict]:
    q = "SELECT * FROM warnings WHERE guild_id=? AND user_id=?"
    p = [guild_id, user_id]
    if active_only:
        q += " AND active=1"
    return await fetchall(q + " ORDER BY created_at DESC", tuple(p))


async def clear_warning(warn_id: int) -> bool:
    row = await fetchone("SELECT id FROM warnings WHERE id=?", (warn_id,))
    if not row:
        return False
    await execute("UPDATE warnings SET active=0 WHERE id=?", (warn_id,))
    return True


async def get_warning_count(guild_id: int, user_id: int) -> int:
    row = await fetchone(
        "SELECT SUM(points) as total FROM warnings WHERE guild_id=? AND user_id=? AND active=1",
        (guild_id, user_id),
    )
    return row["total"] or 0 if row else 0


# ─── Staff helpers ────────────────────────────────────────────────────────────


async def log_staff_action(
    guild_id: int,
    user_id: int,
    action_type: str,
    target_id: int = None,
    details: str = None,
) -> None:
    await execute(
        """INSERT INTO staff_activity (guild_id, user_id, action_type, target_id, details)
           VALUES (?,?,?,?,?)""",
        (guild_id, user_id, action_type, target_id, details),
    )


async def get_staff_actions(guild_id: int, user_id: int, limit: int = 50) -> list[dict]:
    return await fetchall(
        """SELECT * FROM staff_activity
           WHERE guild_id=? AND user_id=?
           ORDER BY created_at DESC LIMIT ?""",
        (guild_id, user_id, limit),
    )


async def add_staff_strike(
    guild_id: int, user_id: int, issued_by: int, reason: str
) -> int:
    return await execute(
        "INSERT INTO staff_strikes (guild_id, user_id, issued_by, reason) VALUES (?,?,?,?)",
        (guild_id, user_id, issued_by, reason),
    )


async def get_staff_strikes(guild_id: int, user_id: int) -> list[dict]:
    return await fetchall(
        "SELECT * FROM staff_strikes WHERE guild_id=? AND user_id=? AND active=1",
        (guild_id, user_id),
    )


async def get_staff_leaderboard(guild_id: int, limit: int = 10) -> list[dict]:
    return await fetchall(
        """SELECT user_id, COUNT(*) as actions
           FROM staff_activity WHERE guild_id=?
           GROUP BY user_id ORDER BY actions DESC LIMIT ?""",
        (guild_id, limit),
    )


async def get_ticket_claim_count(guild_id: int, user_id: int) -> int:
    """Cuenta cuántos tickets ha reclamado (acción 'ticket_claim' en
    staff_activity) un miembro del staff desde el último reinicio de su
    contador (o desde siempre si nunca lo reinició)."""
    reset_row = await fetchone(
        "SELECT reset_at FROM staff_claim_resets WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    q = """SELECT COUNT(*) as total FROM staff_activity
           WHERE guild_id=? AND user_id=? AND action_type='ticket_claim'"""
    params = [guild_id, user_id]
    if reset_row:
        q += " AND created_at > ?"
        params.append(reset_row["reset_at"])
    row = await fetchone(q, tuple(params))
    return row["total"] if row else 0


async def get_all_ticket_claim_counts(guild_id: int) -> list[dict]:
    """Lista [{user_id, count}] con el conteo de tickets reclamados de TODO
    el staff que tenga al menos un reclamo, respetando el reinicio individual
    de cada uno (columna reset_at en staff_claim_resets), ordenado de mayor a
    menor."""
    return await fetchall(
        """SELECT sa.user_id AS user_id, COUNT(*) AS count
           FROM staff_activity sa
           LEFT JOIN staff_claim_resets scr
             ON scr.guild_id = sa.guild_id AND scr.user_id = sa.user_id
           WHERE sa.guild_id = ? AND sa.action_type = 'ticket_claim'
             AND (scr.reset_at IS NULL OR sa.created_at > scr.reset_at)
           GROUP BY sa.user_id
           ORDER BY count DESC""",
        (guild_id,),
    )


async def get_ticket_claim_reset_date(guild_id: int, user_id: int) -> str | None:
    row = await fetchone(
        "SELECT reset_at FROM staff_claim_resets WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    return row["reset_at"] if row else None


async def reset_ticket_claim_count(guild_id: int, user_id: int) -> None:
    """Reinicia el contador de tickets reclamados de un staff a 0 a partir de
    ahora. No borra el historial real en staff_activity, solo mueve el punto
    de corte desde el que cuenta get_ticket_claim_count."""
    await execute(
        """INSERT OR REPLACE INTO staff_claim_resets (guild_id, user_id, reset_at)
           VALUES (?, ?, datetime('now'))""",
        (guild_id, user_id),
    )


async def get_staff_ids_with_claims(guild_id: int) -> list[int]:
    """IDs de todos los usuarios que tienen al menos un 'ticket_claim'
    registrado en este servidor (hayan reclamado 1 o mil tickets, y sigan o
    no teniendo rol de staff ahora mismo)."""
    rows = await fetchall(
        """SELECT DISTINCT user_id FROM staff_activity
           WHERE guild_id=? AND action_type='ticket_claim'""",
        (guild_id,),
    )
    return [row["user_id"] for row in rows]


async def reset_all_ticket_claim_counts(guild_id: int) -> list[int]:
    """Reinicia a 0 el contador de tickets reclamados de TODOS los staff que
    tengan algún reclamo registrado. Devuelve la lista de user_id afectados."""
    user_ids = await get_staff_ids_with_claims(guild_id)
    now = None
    for user_id in user_ids:
        # Un solo timestamp compartido para que todos queden reiniciados
        # exactamente al mismo instante, en vez de llamar datetime('now')
        # por separado en cada INSERT.
        if now is None:
            row = await fetchone("SELECT datetime('now') as now")
            now = row["now"]
        await execute(
            """INSERT OR REPLACE INTO staff_claim_resets (guild_id, user_id, reset_at)
               VALUES (?, ?, ?)""",
            (guild_id, user_id, now),
        )
    return user_ids


# ─── Staff notes helpers (/nota) ──────────────────────────────────────────────


async def add_staff_note(guild_id: int, user_id: int, author_id: int, note: str) -> int:
    return await execute(
        "INSERT INTO staff_notes (guild_id, user_id, author_id, note) VALUES (?,?,?,?)",
        (guild_id, user_id, author_id, note),
    )


async def get_staff_notes(guild_id: int, user_id: int) -> list[dict]:
    """Notas de los últimos 15 días únicamente — son temporales por diseño
    (ver purge_expired_staff_notes)."""
    return await fetchall(
        """SELECT * FROM staff_notes
           WHERE guild_id=? AND user_id=? AND created_at >= datetime('now', '-15 days')
           ORDER BY created_at DESC""",
        (guild_id, user_id),
    )


async def purge_expired_staff_notes() -> int:
    """Borra permanentemente las notas de staff con más de 15 días.
    Devuelve la cantidad de notas borradas."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur = await db.execute(
            "DELETE FROM staff_notes WHERE created_at < datetime('now', '-15 days')"
        )
        await db.commit()
        return cur.rowcount


# ─── Suggestion helpers ───────────────────────────────────────────────────────


async def create_suggestion(
    guild_id: int, channel_id: int, user_id: int, content: str
) -> int:
    return await execute(
        "INSERT INTO suggestions (guild_id, channel_id, user_id, content) VALUES (?,?,?,?)",
        (guild_id, channel_id, user_id, content),
    )


async def set_suggestion_message(suggestion_id: int, message_id: int) -> None:
    await execute(
        "UPDATE suggestions SET message_id=? WHERE id=?", (message_id, suggestion_id)
    )


async def vote_suggestion(
    suggestion_id: int, user_id: int, vote: int
) -> tuple[int, int]:
    """Vote +1 or -1. Returns (upvotes, downvotes)."""
    existing = await fetchone(
        "SELECT vote FROM suggestion_votes WHERE suggestion_id=? AND user_id=?",
        (suggestion_id, user_id),
    )
    if existing:
        await execute(
            "UPDATE suggestion_votes SET vote=? WHERE suggestion_id=? AND user_id=?",
            (vote, suggestion_id, user_id),
        )
    else:
        await execute(
            "INSERT INTO suggestion_votes (suggestion_id, user_id, vote) VALUES (?,?,?)",
            (suggestion_id, user_id, vote),
        )
    row = await fetchone(
        """SELECT
               SUM(CASE WHEN vote=1  THEN 1 ELSE 0 END) as up,
               SUM(CASE WHEN vote=-1 THEN 1 ELSE 0 END) as dn
           FROM suggestion_votes WHERE suggestion_id=?""",
        (suggestion_id,),
    )
    up = row["up"] or 0
    dn = row["dn"] or 0
    await execute(
        "UPDATE suggestions SET upvotes=?, downvotes=? WHERE id=?",
        (up, dn, suggestion_id),
    )
    return up, dn


async def update_suggestion_status(
    suggestion_id: int, status: str, responded_by: int, response: str
) -> None:
    await execute(
        "UPDATE suggestions SET status=?, responded_by=?, response=? WHERE id=?",
        (status, responded_by, response, suggestion_id),
    )


# ─── AutoMod helpers ──────────────────────────────────────────────────────────


async def get_automod_config(guild_id: int) -> dict:
    row = await fetchone("SELECT * FROM automod_config WHERE guild_id=?", (guild_id,))
    if not row:
        await execute(
            "INSERT OR IGNORE INTO automod_config (guild_id) VALUES (?)", (guild_id,)
        )
        row = await fetchone(
            "SELECT * FROM automod_config WHERE guild_id=?", (guild_id,)
        )
    return row or {}


async def update_automod_config(guild_id: int, **kwargs) -> None:
    if not kwargs:
        return
    await execute(
        "INSERT OR IGNORE INTO automod_config (guild_id) VALUES (?)", (guild_id,)
    )
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [guild_id]
    await execute(f"UPDATE automod_config SET {cols} WHERE guild_id=?", tuple(vals))


# ─── Welcome helpers ──────────────────────────────────────────────────────────


async def get_welcome_config(guild_id: int) -> dict:
    row = await fetchone("SELECT * FROM welcome_config WHERE guild_id=?", (guild_id,))
    if not row:
        await execute(
            "INSERT OR IGNORE INTO welcome_config (guild_id) VALUES (?)", (guild_id,)
        )
        row = await fetchone(
            "SELECT * FROM welcome_config WHERE guild_id=?", (guild_id,)
        )
    return row or {}


async def update_welcome_config(guild_id: int, **kwargs) -> None:
    if not kwargs:
        return
    await execute(
        "INSERT OR IGNORE INTO welcome_config (guild_id) VALUES (?)", (guild_id,)
    )
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [guild_id]
    await execute(f"UPDATE welcome_config SET {cols} WHERE guild_id=?", tuple(vals))


# ─── Verification helpers ─────────────────────────────────────────────────────


async def get_verification_config(guild_id: int) -> dict:
    row = await fetchone(
        "SELECT * FROM verification_config WHERE guild_id=?", (guild_id,)
    )
    if not row:
        await execute(
            "INSERT OR IGNORE INTO verification_config (guild_id) VALUES (?)",
            (guild_id,),
        )
        row = await fetchone(
            "SELECT * FROM verification_config WHERE guild_id=?", (guild_id,)
        )
    return row or {}


async def update_verification_config(guild_id: int, **kwargs) -> None:
    if not kwargs:
        return
    await execute(
        "INSERT OR IGNORE INTO verification_config (guild_id) VALUES (?)", (guild_id,)
    )
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [guild_id]
    await execute(
        f"UPDATE verification_config SET {cols} WHERE guild_id=?", tuple(vals)
    )


# ─── Reminder helpers ─────────────────────────────────────────────────────────


async def create_reminder(guild_id, user_id, channel_id, content, remind_at) -> int:
    return await execute(
        "INSERT INTO reminders (guild_id, user_id, channel_id, content, remind_at) VALUES (?,?,?,?,?)",
        (guild_id, user_id, channel_id, content, remind_at),
    )


async def get_pending_reminders() -> list[dict]:
    return await fetchall(
        "SELECT * FROM reminders WHERE sent=0 AND remind_at <= datetime('now')"
    )


async def mark_reminder_sent(reminder_id: int) -> None:
    await execute("UPDATE reminders SET sent=1 WHERE id=?", (reminder_id,))


async def get_user_reminders(user_id: int) -> list[dict]:
    return await fetchall(
        "SELECT * FROM reminders WHERE user_id=? AND sent=0 ORDER BY remind_at",
        (user_id,),
    )


# ─── Reaction Roles ───────────────────────────────────────────────────────────


async def add_reaction_role(
    guild_id, channel_id, message_id, emoji, role_id, panel: int = 0, label: str = None
) -> int:
    return await execute(
        """INSERT INTO reaction_roles
           (guild_id, channel_id, message_id, emoji, role_id, panel, label)
           VALUES (?,?,?,?,?,?,?)""",
        (guild_id, channel_id, message_id, emoji, role_id, panel, label),
    )


async def get_reaction_role(message_id: int, emoji: str) -> dict | None:
    return await fetchone(
        "SELECT * FROM reaction_roles WHERE message_id=? AND emoji=?",
        (message_id, emoji),
    )


async def get_reaction_roles(guild_id: int) -> list[dict]:
    return await fetchall(
        "SELECT * FROM reaction_roles WHERE guild_id=? ORDER BY message_id",
        (guild_id,),
    )


async def remove_reaction_role(rr_id: int) -> None:
    await execute("DELETE FROM reaction_roles WHERE id=?", (rr_id,))


# ─── Appeals ─────────────────────────────────────────────────────────────────


async def create_appeal(
    guild_id: int, user_id: int, reason: str, case_id: int = None
) -> int:
    return await execute(
        "INSERT INTO appeals (guild_id, user_id, reason, case_id) VALUES (?,?,?,?)",
        (guild_id, user_id, reason, case_id),
    )


async def get_pending_appeals(guild_id: int) -> list[dict]:
    return await fetchall(
        "SELECT * FROM appeals WHERE guild_id=? AND status='pending' ORDER BY created_at",
        (guild_id,),
    )


async def update_appeal(
    appeal_id: int, status: str, reviewed_by: int, response: str
) -> None:
    await execute(
        "UPDATE appeals SET status=?, reviewed_by=?, response=? WHERE id=?",
        (status, reviewed_by, response, appeal_id),
    )


# ─── Birthday helpers ─────────────────────────────────────────────────────────


async def set_birthday(user_id: int, birthday: str) -> None:
    await execute(
        "INSERT OR REPLACE INTO birthdays (user_id, birthday) VALUES (?,?)",
        (user_id, birthday),
    )


async def get_birthday(user_id: int) -> dict | None:
    return await fetchone("SELECT * FROM birthdays WHERE user_id=?", (user_id,))


async def get_today_birthdays(today_str: str) -> list[dict]:
    return await fetchall("SELECT * FROM birthdays WHERE birthday=?", (today_str,))


async def create_or_get_birthday_reward_code(user_id: int, birthday_year: int) -> dict:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(3):
        code = "".join(secrets.choice(alphabet) for _ in range(16))
        await execute(
            """INSERT OR IGNORE INTO birthday_reward_codes
               (code, user_id, birthday_year, expires_at)
               VALUES (?, ?, ?, datetime('now', '+90 days'))""",
            (code, user_id, birthday_year),
        )
        row = await fetchone(
            "SELECT * FROM birthday_reward_codes WHERE user_id=? AND birthday_year=?",
            (user_id, birthday_year),
        )
        if row:
            return row
    raise RuntimeError("No se pudo generar un código de cumpleaños único.")


async def mark_birthday_reward_sent(code: str) -> None:
    await execute(
        "UPDATE birthday_reward_codes SET sent_at=datetime('now') WHERE code=? AND sent_at IS NULL",
        (code,),
    )


async def redeem_birthday_reward_code(code: str, player: str) -> str:
    """Canje atómico. Devuelve 'ok', 'invalid', 'used' o 'expired'."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur = await db.execute(
            """UPDATE birthday_reward_codes
               SET redeemed_at=datetime('now'), minecraft_player=?
               WHERE code=? AND redeemed_at IS NULL AND expires_at > datetime('now')""",
            (player, code),
        )
        changed = cur.rowcount
        await db.commit()
        if changed == 1:
            return "ok"
        async with db.execute(
            "SELECT redeemed_at, expires_at > datetime('now') AS valid FROM birthday_reward_codes WHERE code=?",
            (code,),
        ) as c2:
            row = await c2.fetchone()
        if not row:
            return "invalid"
        return "used" if row["redeemed_at"] else "expired"


# ─── Statistics helpers ───────────────────────────────────────────────────────


async def get_ticket_stats(guild_id: int) -> dict:
    total = await fetchone(
        "SELECT COUNT(*) as c FROM tickets WHERE guild_id=?", (guild_id,)
    )
    open_ = await fetchone(
        "SELECT COUNT(*) as c FROM tickets WHERE guild_id=? AND status='open'",
        (guild_id,),
    )
    closed = await fetchone(
        "SELECT COUNT(*) as c FROM tickets WHERE guild_id=? AND status='closed'",
        (guild_id,),
    )
    avg_rating = await fetchone(
        "SELECT AVG(rating) as r FROM tickets WHERE guild_id=? AND rating IS NOT NULL",
        (guild_id,),
    )
    return {
        "total": total["c"] if total else 0,
        "open": open_["c"] if open_ else 0,
        "closed": closed["c"] if closed else 0,
        "avg_rating": round(avg_rating["r"] or 0, 2) if avg_rating else 0,
    }


async def reset_tickets(guild_id: int) -> int:
    """
    Borra TODOS los tickets (y sus mensajes asociados) de un servidor y,
    si no queda ningún ticket de NINGÚN servidor en la tabla, reinicia el
    contador AUTOINCREMENT para que el próximo ticket vuelva a empezar en #1.

    (El contador de SQLite es global a la tabla, no por servidor, así que
    solo se reinicia cuando la tabla queda completamente vacía — evita
    romper la numeración de otros servidores en un bot multi-guild.)

    Devuelve la cantidad de tickets borrados.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")

        cur = await db.execute(
            "SELECT COUNT(*) FROM tickets WHERE guild_id=?", (guild_id,)
        )
        (deleted_count,) = await cur.fetchone()

        # Borra primero los mensajes de esos tickets (integridad referencial manual,
        # la tabla ticket_messages no tiene FK con ON DELETE CASCADE).
        await db.execute(
            "DELETE FROM ticket_messages WHERE ticket_id IN (SELECT id FROM tickets WHERE guild_id=?)",
            (guild_id,),
        )
        await db.execute("DELETE FROM tickets WHERE guild_id=?", (guild_id,))

        # Solo reinicia el contador si la tabla quedó totalmente vacía.
        cur = await db.execute("SELECT COUNT(*) FROM tickets")
        (remaining,) = await cur.fetchone()
        if remaining == 0:
            await db.execute("DELETE FROM sqlite_sequence WHERE name='tickets'")
            await db.execute("DELETE FROM sqlite_sequence WHERE name='ticket_messages'")

        await db.commit()
        return deleted_count


# ─── Lockdown helpers (/lockdown) ───────────────────────────a��──────────────────


async def get_lockdown_state(guild_id: int) -> dict | None:
    row = await fetchone("SELECT * FROM lockdown_state WHERE guild_id=?", (guild_id,))
    if row:
        row["channel_ids"] = json.loads(row["channel_ids"])
    return row


async def start_lockdown(
    guild_id: int, channel_ids: list[int], previous_verification: int, activated_by: int
) -> None:
    await execute(
        """INSERT OR REPLACE INTO lockdown_state
           (guild_id, channel_ids, previous_verification, activated_by)
           VALUES (?,?,?,?)""",
        (guild_id, json.dumps(channel_ids), previous_verification, activated_by),
    )


async def end_lockdown(guild_id: int) -> dict | None:
    """Borra el estado de lockdown y devuelve lo que había (o None si no había)."""
    state = await get_lockdown_state(guild_id)
    if state:
        await execute("DELETE FROM lockdown_state WHERE guild_id=?", (guild_id,))
    return state


async def get_mod_stats(guild_id: int) -> dict:
    rows = await fetchall(
        "SELECT type, COUNT(*) as cnt FROM mod_cases WHERE guild_id=? GROUP BY type",
        (guild_id,),
    )
    return {r["type"]: r["cnt"] for r in rows}


# ─── Economy / Minijuegos helpers ─────────────────────────────────────────────

STARTING_BALANCE = 0  # Economía deliberadamente lenta — sin regalo inicial grande.


async def get_balance(guild_id: int, user_id: int) -> int:
    row = await fetchone(
        "SELECT balance FROM economy WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    if row is not None:
        return row["balance"]
    await execute(
        "INSERT OR IGNORE INTO economy (guild_id, user_id, balance) VALUES (?,?,?)",
        (guild_id, user_id, STARTING_BALANCE),
    )
    row = await fetchone(
        "SELECT balance FROM economy WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    return row["balance"] if row else STARTING_BALANCE


async def deposit(guild_id: int, user_id: int, amount: int) -> int:
    if amount < 0:
        raise ValueError("deposit() no acepta montos negativos; usa withdraw().")
    await get_balance(guild_id, user_id)
    await execute(
        "UPDATE economy SET balance = balance + ? WHERE guild_id=? AND user_id=?",
        (amount, guild_id, user_id),
    )
    row = await fetchone(
        "SELECT balance FROM economy WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    return row["balance"] if row else amount


async def withdraw(guild_id: int, user_id: int, amount: int) -> bool:
    """Resta `amount` de forma atómica. Devuelve True si había fondos suficientes."""
    if amount <= 0:
        raise ValueError("withdraw() requiere un monto positivo.")
    await get_balance(guild_id, user_id)
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur = await conn.execute(
            """UPDATE economy SET balance = balance - ?
               WHERE guild_id=? AND user_id=? AND balance >= ?""",
            (amount, guild_id, user_id, amount),
        )
        await conn.commit()
        return cur.rowcount > 0


async def record_game_result(guild_id: int, user_id: int, won: bool) -> None:
    await get_balance(guild_id, user_id)
    column = "wins" if won else "losses"
    await execute(
        f"UPDATE economy SET {column} = {column} + 1 WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )


async def get_economy_leaderboard(guild_id: int, limit: int = 10) -> list[dict]:
    return await fetchall(
        "SELECT * FROM economy WHERE guild_id=? ORDER BY balance DESC LIMIT ?",
        (guild_id, limit),
    )


# ─── Minecraft link helpers ───────────────────────────────────────────────────


async def get_minecraft_link(discord_id: int) -> dict | None:
    return await fetchone(
        "SELECT * FROM minecraft_links WHERE discord_id=?", (discord_id,)
    )


async def get_minecraft_link_by_uuid(minecraft_uuid: str) -> dict | None:
    return await fetchone(
        "SELECT * FROM minecraft_links WHERE minecraft_uuid=?", (minecraft_uuid,)
    )


async def set_minecraft_link(
    discord_id: int, minecraft_nick: str, minecraft_uuid: str
) -> bool:
    """Crea o actualiza el vínculo Discord ↔ Minecraft.
    Devuelve True si se guardó; False si el UUID ya pertenece a otro discord_id."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur = await conn.execute(
            """INSERT INTO minecraft_links (discord_id, minecraft_nick, minecraft_uuid)
               VALUES (?, ?, ?)
               ON CONFLICT(discord_id) DO UPDATE SET
                   minecraft_nick=excluded.minecraft_nick,
                   minecraft_uuid=excluded.minecraft_uuid,
                   linked_at=datetime('now')
               ON CONFLICT(minecraft_uuid) DO NOTHING""",
            (discord_id, minecraft_nick, minecraft_uuid),
        )
        await conn.commit()
        return cur.rowcount > 0


async def set_economy_cooldown(
    guild_id: int, user_id: int, field: str, iso_timestamp: str
) -> None:
    """field debe ser 'last_daily', 'last_work' o 'last_rob'."""
    if field not in ("last_daily", "last_work", "last_rob"):
        raise ValueError(f"Campo de cooldown inválido: {field}")
    await get_balance(guild_id, user_id)  # asegura que la fila exista
    await execute(
        f"UPDATE economy SET {field}=? WHERE guild_id=? AND user_id=?",
        (iso_timestamp, guild_id, user_id),
    )


async def get_economy_row(guild_id: int, user_id: int) -> dict:
    await get_balance(guild_id, user_id)  # asegura que la fila exista
    return await fetchone(
        "SELECT * FROM economy WHERE guild_id=? AND user_id=?", (guild_id, user_id)
    )


# ─── Tienda: items ─────────────────────────────────────────────────────────────


async def create_shop_item(
    guild_id: int,
    name: str,
    price: int,
    description: str = None,
    role_id: int = None,
    mc_reward: str = None,
    stock: int = -1,
    created_by: int = None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur = await conn.execute(
            """INSERT INTO shop_items (guild_id, name, description, price, role_id, mc_reward, stock, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, name, description, price, role_id, mc_reward, stock, created_by),
        )
        await conn.commit()
        return cur.lastrowid


async def get_shop_items(guild_id: int, only_active: bool = True) -> list[dict]:
    query = "SELECT * FROM shop_items WHERE guild_id=?"
    if only_active:
        query += " AND active=1"
    query += " ORDER BY price ASC"
    return await fetchall(query, (guild_id,))


async def get_shop_item(guild_id: int, item_id: int) -> dict | None:
    return await fetchone(
        "SELECT * FROM shop_items WHERE guild_id=? AND id=?", (guild_id, item_id)
    )


async def get_shop_item_by_name(guild_id: int, name: str) -> dict | None:
    return await fetchone(
        "SELECT * FROM shop_items WHERE guild_id=? AND LOWER(name)=LOWER(?) AND active=1",
        (guild_id, name),
    )


async def deactivate_shop_item(guild_id: int, item_id: int) -> bool:
    row = await get_shop_item(guild_id, item_id)
    if not row:
        return False
    await execute("UPDATE shop_items SET active=0 WHERE id=?", (item_id,))
    return True


async def decrement_shop_stock(item_id: int) -> None:
    await execute(
        "UPDATE shop_items SET stock = stock - 1 WHERE id=? AND stock > 0", (item_id,)
    )


# ─── Tienda: compras / códigos de canje ────────────────────────────────────────


async def create_shop_purchase(
    guild_id: int,
    user_id: int,
    item_id: int,
    item_name: str,
    price_paid: int,
    code: str,
    minecraft_uuid: str = None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur = await conn.execute(
            """INSERT INTO shop_purchases
               (guild_id, user_id, item_id, item_name, price_paid, code, minecraft_uuid)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, user_id, item_id, item_name, price_paid, code, minecraft_uuid),
        )
        await conn.commit()
        return cur.lastrowid


async def get_purchase_by_code(guild_id: int, code: str) -> dict | None:
    return await fetchone(
        "SELECT * FROM shop_purchases WHERE guild_id=? AND code=?", (guild_id, code)
    )


async def get_user_purchases(guild_id: int, user_id: int) -> list[dict]:
    return await fetchall(
        "SELECT * FROM shop_purchases WHERE guild_id=? AND user_id=? ORDER BY created_at DESC",
        (guild_id, user_id),
    )


async def get_pending_purchases(guild_id: int) -> list[dict]:
    """Compras cuyo código todavía no ha sido marcado como canjeado por el
    staff (ver /tienda pendientes, /tienda verificarcodigo, /tienda marcarcanjeado)."""
    return await fetchall(
        """SELECT * FROM shop_purchases
           WHERE guild_id=? AND redeemed_ingame=0
           ORDER BY created_at ASC""",
        (guild_id,),
    )


async def mark_purchase_redeemed(guild_id: int, code: str, redeemed_by: int) -> bool:
    row = await get_purchase_by_code(guild_id, code)
    if not row:
        return False
    await execute(
        """UPDATE shop_purchases
           SET redeemed_ingame=1, redeemed_by=?, redeemed_at=datetime('now')
           WHERE id=?""",
        (redeemed_by, row["id"]),
    )
    return True


# ─── Tournament helpers ───────────────────────────────────────────────────────


async def create_tournament(
    guild_id: int,
    channel_id: int,
    host_id: int,
    name: str,
    mode: str,
    prize: str | None,
    slots: int,
    start_time: str,
) -> int:
    return await execute(
        """INSERT INTO tournaments
           (guild_id, channel_id, host_id, name, mode, prize, slots, start_time)
           VALUES (?,?,?,?,?,?,?,?)""",
        (guild_id, channel_id, host_id, name, mode, prize, slots, start_time),
    )


async def get_tournament(tournament_id: int) -> dict | None:
    return await fetchone("SELECT * FROM tournaments WHERE id=?", (tournament_id,))


async def get_active_tournament(guild_id: int) -> dict | None:
    """Devuelve el torneo abierto o activo más reciente del servidor."""
    return await fetchone(
        """SELECT * FROM tournaments
           WHERE guild_id=? AND status IN ('open', 'active')
           ORDER BY created_at DESC LIMIT 1""",
        (guild_id,),
    )


async def get_open_tournaments() -> list[dict]:
    """Todos los torneos con status='open' en todos los servidores (para el loop)."""
    return await fetchall("SELECT * FROM tournaments WHERE status='open'")


async def get_finished_tournaments(guild_id: int, limit: int = 10) -> list[dict]:
    return await fetchall(
        """SELECT * FROM tournaments
           WHERE guild_id=? AND status='finished'
           ORDER BY created_at DESC LIMIT ?""",
        (guild_id, limit),
    )


async def update_tournament(tournament_id: int, **kwargs) -> None:
    if not kwargs:
        return
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [tournament_id]
    await execute(f"UPDATE tournaments SET {cols} WHERE id=?", tuple(vals))


async def claim_tournament_start(tournament_id: int) -> bool:
    """Transición atómica open → active. Devuelve True solo si ESTA llamada la hizo."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur = await conn.execute(
            "UPDATE tournaments SET status='active' WHERE id=? AND status='open'",
            (tournament_id,),
        )
        await conn.commit()
        return cur.rowcount > 0


async def claim_tournament_finish(tournament_id: int) -> bool:
    """Marca el torneo como finished SOLO si sigue activo (atómico).
    Devuelve True si ESTA llamada lo finalizó."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur = await conn.execute(
            "UPDATE tournaments SET status='finished' WHERE id=? AND status='active'",
            (tournament_id,),
        )
        await conn.commit()
        return cur.rowcount > 0


async def claim_round_advance(
    tournament_id: int, current_round: int, next_round: int
) -> bool:
    """Avanza la ronda SOLO si current_round sigue siendo el valor esperado.
    Evita que dos llamadas concurrentes generen el siguiente bracket dos veces."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur = await conn.execute(
            """UPDATE tournaments
               SET current_round=?
               WHERE id=? AND current_round=? AND status='active'""",
            (next_round, tournament_id, current_round),
        )
        await conn.commit()
        return cur.rowcount > 0


# ── Participants ──────────────────────────────────────────────────────────────


async def add_tournament_participant(tournament_id: int, user_id: int) -> bool:
    """Inscribe al usuario sin guardia de cap (uso interno para byes).
    Devuelve False si ya estaba inscrito."""
    try:
        await execute(
            "INSERT INTO tournament_participants (tournament_id, user_id) VALUES (?,?)",
            (tournament_id, user_id),
        )
        return True
    except Exception:
        return False


async def add_tournament_participant_if_open(
    tournament_id: int, user_id: int, max_slots: int
) -> bool:
    """Inscribe al usuario SOLO si el torneo sigue abierto y hay cupo disponible.
    Aplica el cap de forma atómica para evitar sobrecupo con pulsaciones concurrentes.
    Devuelve True si la inscripción tuvo éxito."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        async with conn.execute(
            "SELECT COUNT(*) FROM tournament_participants WHERE tournament_id=?",
            (tournament_id,),
        ) as cur:
            row = await cur.fetchone()
            current = row[0] if row else 0

        if current >= max_slots:
            return False

        try:
            await conn.execute(
                "INSERT INTO tournament_participants (tournament_id, user_id) VALUES (?,?)",
                (tournament_id, user_id),
            )
            await conn.commit()
            return True
        except Exception:
            # UNIQUE constraint: usuario ya inscrito
            return False


async def remove_tournament_participant(tournament_id: int, user_id: int) -> None:
    await execute(
        "DELETE FROM tournament_participants WHERE tournament_id=? AND user_id=?",
        (tournament_id, user_id),
    )


async def get_tournament_participants(tournament_id: int) -> list[dict]:
    return await fetchall(
        "SELECT * FROM tournament_participants WHERE tournament_id=? ORDER BY joined_at",
        (tournament_id,),
    )


# ── Matches ───────────────────────────────────────────────────────────────────


async def create_tournament_match(
    tournament_id: int,
    round_num: int,
    match_number: int,
    player1_id: int,
    player2_id: int | None = None,
    is_third_place: int = 0,
) -> int:
    return await execute(
        """INSERT INTO tournament_matches
           (tournament_id, round, match_number, player1_id, player2_id, is_third_place)
           VALUES (?,?,?,?,?,?)""",
        (
            tournament_id,
            round_num,
            match_number,
            player1_id,
            player2_id,
            is_third_place,
        ),
    )


async def get_tournament_match(match_id: int) -> dict | None:
    return await fetchone("SELECT * FROM tournament_matches WHERE id=?", (match_id,))


async def get_match_by_round_number(
    tournament_id: int, round_num: int, match_number: int
) -> dict | None:
    """Busca un partido por su número dentro de una ronda (1, 2, 3... se
    reinicia cada ronda) en vez de por el id global autoincremental —
    match_number=99 es el partido especial por el tercer puesto."""
    return await fetchone(
        "SELECT * FROM tournament_matches WHERE tournament_id=? AND round=? AND match_number=?",
        (tournament_id, round_num, match_number),
    )


async def get_round_matches(tournament_id: int, round_num: int) -> list[dict]:
    return await fetchall(
        """SELECT * FROM tournament_matches
           WHERE tournament_id=? AND round=?
           ORDER BY is_third_place ASC, match_number ASC""",
        (tournament_id, round_num),
    )


async def set_match_result(match_id: int, winner_id: int, loser_id: int | None) -> None:
    """Escribe el resultado sin guardia (usado internamente para byes)."""
    await execute(
        """UPDATE tournament_matches
           SET winner_id=?, loser_id=?, status='completed'
           WHERE id=?""",
        (winner_id, loser_id, match_id),
    )


async def claim_match_result(
    match_id: int, winner_id: int, loser_id: int | None
) -> bool:
    """Registra el resultado SOLO si winner_id sigue siendo NULL (atómico).
    Devuelve True si ESTA llamada lo escribió; False si otra ya lo hizo."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur = await conn.execute(
            """UPDATE tournament_matches
               SET winner_id=?, loser_id=?, status='completed'
               WHERE id=? AND winner_id IS NULL""",
            (winner_id, loser_id, match_id),
        )
        await conn.commit()
        return cur.rowcount > 0


# ─── Confessions helpers ───────────────────────────────────────────────────


async def get_confession_config(guild_id: int) -> dict:
    row = await fetchone(
        "SELECT * FROM confession_config WHERE guild_id=?", (guild_id,)
    )
    if not row:
        await execute(
            "INSERT OR IGNORE INTO confession_config (guild_id) VALUES (?)", (guild_id,)
        )
        row = await fetchone(
            "SELECT * FROM confession_config WHERE guild_id=?", (guild_id,)
        )
    return row or {}


async def update_confession_config(guild_id: int, **kwargs) -> None:
    if not kwargs:
        return
    await execute(
        "INSERT OR IGNORE INTO confession_config (guild_id) VALUES (?)", (guild_id,)
    )
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [guild_id]
    await execute(f"UPDATE confession_config SET {cols} WHERE guild_id=?", tuple(vals))


async def create_confession(guild_id: int, user_id: int, content: str) -> int:
    """Crea la confesión asignando el siguiente número correlativo del
    guild de forma atómica (lee y aumenta next_number en la misma conexión)
    y devuelve ese número."""
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        await conn.execute(
            "INSERT OR IGNORE INTO confession_config (guild_id) VALUES (?)", (guild_id,)
        )
        cur = await conn.execute(
            "SELECT next_number FROM confession_config WHERE guild_id=?", (guild_id,)
        )
        row = await cur.fetchone()
        number = row["next_number"] if row else 1
        await conn.execute(
            "UPDATE confession_config SET next_number=? WHERE guild_id=?",
            (number + 1, guild_id),
        )
        await conn.execute(
            "INSERT INTO confessions (guild_id, number, user_id, content) VALUES (?, ?, ?, ?)",
            (guild_id, number, user_id, content),
        )
        await conn.commit()
        return number


async def set_confession_message(guild_id: int, number: int, message_id: int) -> None:
    await execute(
        "UPDATE confessions SET message_id=? WHERE guild_id=? AND number=?",
        (message_id, guild_id, number),
    )


async def get_confession_by_number(guild_id: int, number: int) -> dict | None:
    return await fetchone(
        "SELECT * FROM confessions WHERE guild_id=? AND number=?", (guild_id, number)
    )


# ─── Invite tracking helpers ───────────────────────────────────────────────


async def record_invite_join(
    guild_id: int,
    member_id: int,
    inviter_id: int | None,
    invite_code: str | None,
    account_created_at: str | None = None,
) -> bool:
    """Registra el join de un miembro (historial), sin sumar todavía al
    contador de invitaciones — eso lo decide `mark_invite_counted` en base
    a los filtros antifraude (antigüedad de cuenta + verificación).
    Usa INSERT OR IGNORE sobre (guild_id, member_id) para no duplicar el
    registro si el evento se dispara más de una vez (reconexiones, etc).
    Devuelve True si esta llamada fue la que realmente registró el join."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur = await conn.execute(
            "INSERT OR IGNORE INTO invite_joins "
            "(guild_id, member_id, inviter_id, invite_code, account_created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, member_id, inviter_id, invite_code, account_created_at),
        )
        await conn.commit()
        return cur.rowcount > 0


async def mark_invite_counted(guild_id: int, member_id: int, inviter_id: int) -> bool:
    """Marca el join de `member_id` como contado (pasó los filtros
    antifraude) y suma +1 al contador de invitaciones de `inviter_id`.
    Idempotente: si ese join ya estaba marcado como contado, no hace nada
    y devuelve False — así nunca se cuenta doble aunque el chequeo se
    dispare más de una vez (ej. el rol de verificación se quita y se
    vuelve a dar)."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur = await conn.execute(
            "UPDATE invite_joins SET counted=1 WHERE guild_id=? AND member_id=? AND counted=0",
            (guild_id, member_id),
        )
        if cur.rowcount == 0:
            await conn.commit()
            return False
        await conn.execute(
            "INSERT INTO invite_uses (guild_id, inviter_id, uses) VALUES (?, ?, 1) "
            "ON CONFLICT(guild_id, inviter_id) DO UPDATE SET uses = uses + 1",
            (guild_id, inviter_id),
        )
        await conn.commit()
        return True


async def get_invite_uses(guild_id: int, user_id: int) -> int:
    row = await fetchone(
        "SELECT uses FROM invite_uses WHERE guild_id=? AND inviter_id=?",
        (guild_id, user_id),
    )
    return row["uses"] if row else 0


async def get_invite_leaderboard(guild_id: int, limit: int = 10) -> list[dict]:
    return await fetchall(
        "SELECT inviter_id, uses FROM invite_uses WHERE guild_id=? AND uses > 0 "
        "ORDER BY uses DESC LIMIT ?",
        (guild_id, limit),
    )


async def get_inviter_of(guild_id: int, member_id: int) -> dict | None:
    return await fetchone(
        "SELECT * FROM invite_joins WHERE guild_id=? AND member_id=?",
        (guild_id, member_id),
    )


# ─── Voice hub (canales de voz temporales) helpers ─────────────────────────


async def get_voice_hub_config(guild_id: int) -> dict:
    row = await fetchone("SELECT * FROM voice_hub_config WHERE guild_id=?", (guild_id,))
    if not row:
        await execute(
            "INSERT OR IGNORE INTO voice_hub_config (guild_id) VALUES (?)", (guild_id,)
        )
        row = await fetchone(
            "SELECT * FROM voice_hub_config WHERE guild_id=?", (guild_id,)
        )
    return row or {}


async def update_voice_hub_config(guild_id: int, **kwargs) -> None:
    if not kwargs:
        return
    await execute(
        "INSERT OR IGNORE INTO voice_hub_config (guild_id) VALUES (?)", (guild_id,)
    )
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [guild_id]
    await execute(f"UPDATE voice_hub_config SET {cols} WHERE guild_id=?", tuple(vals))


async def create_temp_voice_channel(
    channel_id: int, guild_id: int, owner_id: int
) -> None:
    await execute(
        "INSERT OR REPLACE INTO temp_voice_channels (channel_id, guild_id, owner_id) VALUES (?, ?, ?)",
        (channel_id, guild_id, owner_id),
    )


async def get_temp_voice_channel(channel_id: int) -> dict | None:
    return await fetchone(
        "SELECT * FROM temp_voice_channels WHERE channel_id=?", (channel_id,)
    )


async def get_temp_voice_channel_by_owner(guild_id: int, owner_id: int) -> dict | None:
    return await fetchone(
        "SELECT * FROM temp_voice_channels WHERE guild_id=? AND owner_id=?",
        (guild_id, owner_id),
    )


async def delete_temp_voice_channel(channel_id: int) -> None:
    await execute("DELETE FROM temp_voice_channels WHERE channel_id=?", (channel_id,))


async def transfer_temp_voice_channel(channel_id: int, new_owner_id: int) -> None:
    await execute(
        "UPDATE temp_voice_channels SET owner_id=? WHERE channel_id=?",
        (new_owner_id, channel_id),
    )


# ─── Niveles (XP por mensajes) ─────────────────────────────────────────────


async def get_level_row(guild_id: int, user_id: int) -> dict:
    row = await fetchone(
        "SELECT * FROM levels WHERE guild_id=? AND user_id=?", (guild_id, user_id)
    )
    if not row:
        await execute(
            "INSERT OR IGNORE INTO levels (guild_id, user_id) VALUES (?,?)",
            (guild_id, user_id),
        )
        row = await fetchone(
            "SELECT * FROM levels WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        )
    return row or {}


async def try_add_message_xp(
    guild_id: int, user_id: int, xp_amount: int, cooldown_seconds: int = 60
) -> dict | None:
    """Suma `xp_amount` de XP y +1 a messages_count SOLO si ya pasó el
    cooldown desde el último mensaje que otorgó XP (evita ganar XP por
    spam). Devuelve el nuevo estado {xp, level, messages_count} si se
    otorgó XP en ESTA llamada, o None si el usuario seguía en cooldown
    (en cuyo caso el mensaje debe contarse aparte con
    `increment_message_count_only`, ya que sigue valiendo para logros)."""
    await get_level_row(guild_id, user_id)
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur = await conn.execute(
            """UPDATE levels
               SET xp = xp + ?, messages_count = messages_count + 1, last_xp_at = datetime('now')
               WHERE guild_id=? AND user_id=?
                 AND (last_xp_at IS NULL OR last_xp_at <= datetime('now', ?))""",
            (xp_amount, guild_id, user_id, f"-{cooldown_seconds} seconds"),
        )
        await conn.commit()
        if cur.rowcount == 0:
            return None
        cur2 = await conn.execute(
            "SELECT xp, level, messages_count FROM levels WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        row = await cur2.fetchone()
        return dict(row) if row else None


async def increment_message_count_only(guild_id: int, user_id: int) -> int:
    """Incrementa messages_count SIN dar XP (el mensaje seguía en cooldown
    de XP, pero igual cuenta para los logros basados en mensajes totales).
    Devuelve el nuevo total."""
    await get_level_row(guild_id, user_id)
    await execute(
        "UPDATE levels SET messages_count = messages_count + 1 WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    row = await fetchone(
        "SELECT messages_count FROM levels WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    return row["messages_count"] if row else 0


async def set_level(guild_id: int, user_id: int, level: int) -> None:
    await execute(
        "UPDATE levels SET level=? WHERE guild_id=? AND user_id=?",
        (level, guild_id, user_id),
    )


async def get_level_leaderboard(guild_id: int, limit: int = 10) -> list[dict]:
    return await fetchall(
        "SELECT * FROM levels WHERE guild_id=? ORDER BY xp DESC LIMIT ?",
        (guild_id, limit),
    )


async def get_level_rank(guild_id: int, user_id: int) -> int:
    row = await fetchone(
        """SELECT COUNT(*) + 1 as rank FROM levels
           WHERE guild_id=? AND xp > (SELECT xp FROM levels WHERE guild_id=? AND user_id=?)""",
        (guild_id, guild_id, user_id),
    )
    return row["rank"] if row else 1


# ─── Logros: progreso ───────────────────────────────────────────────────────


async def get_achievement_progress(guild_id: int, user_id: int) -> dict:
    row = await fetchone(
        "SELECT * FROM achievement_progress WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    if not row:
        await execute(
            "INSERT OR IGNORE INTO achievement_progress (guild_id, user_id) VALUES (?,?)",
            (guild_id, user_id),
        )
        row = await fetchone(
            "SELECT * FROM achievement_progress WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
    return row or {}


_VALID_PROGRESS_COLUMNS = {
    "voice_seconds",
    "files_uploaded",
    "pins_earned",
    "reactions_given",
    "reactions_received",
}


async def increment_progress(
    guild_id: int, user_id: int, column: str, amount: int = 1
) -> int:
    """Incrementa una columna numérica de achievement_progress y devuelve el
    nuevo valor."""
    if column not in _VALID_PROGRESS_COLUMNS:
        raise ValueError(f"Columna de progreso inválida: {column!r}")
    await get_achievement_progress(guild_id, user_id)
    await execute(
        f"UPDATE achievement_progress SET {column} = {column} + ? WHERE guild_id=? AND user_id=?",
        (amount, guild_id, user_id),
    )
    row = await fetchone(
        f"SELECT {column} as v FROM achievement_progress WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    return row["v"] if row else 0


async def set_suggestion_progress(
    guild_id: int, user_id: int, best: int, total: int
) -> None:
    await get_achievement_progress(guild_id, user_id)
    await execute(
        """UPDATE achievement_progress
           SET suggestion_best_upvotes = MAX(suggestion_best_upvotes, ?),
               suggestion_total_upvotes = ?
           WHERE guild_id=? AND user_id=?""",
        (best, total, guild_id, user_id),
    )


async def get_user_suggestion_stats(guild_id: int, user_id: int) -> dict:
    row = await fetchone(
        """SELECT COALESCE(MAX(upvotes), 0) as best, COALESCE(SUM(upvotes), 0) as total
           FROM suggestions WHERE guild_id=? AND user_id=?""",
        (guild_id, user_id),
    )
    return row or {"best": 0, "total": 0}


async def update_streak(guild_id: int, user_id: int) -> int:
    """Actualiza la racha de días consecutivos con actividad (1 mensaje
    basta para contar el día). Si ya escribió hoy, no cambia nada. Si
    escribió ayer, la racha sube en 1. Si no, se reinicia a 1. Devuelve la
    racha actual (después de la actualización)."""
    progress = await get_achievement_progress(guild_id, user_id)
    last = progress.get("streak_last_date")
    today_row = await fetchone("SELECT date('now') as d")
    today_str = today_row["d"]
    if last == today_str:
        return progress.get("streak_days", 0) or 0
    yesterday_row = await fetchone("SELECT date('now', '-1 day') as d")
    if last == yesterday_row["d"]:
        new_streak = (progress.get("streak_days", 0) or 0) + 1
    else:
        new_streak = 1
    await execute(
        "UPDATE achievement_progress SET streak_days=?, streak_last_date=? WHERE guild_id=? AND user_id=?",
        (new_streak, today_str, guild_id, user_id),
    )
    return new_streak


async def try_record_pin(message_id: int, guild_id: int, user_id: int) -> bool:
    """Devuelve True solo la primera vez que se registra el pin de este
    mensaje (evita contarlo dos veces si Discord dispara el evento más de
    una vez)."""
    try:
        await execute(
            "INSERT INTO pin_events (message_id, guild_id, user_id) VALUES (?,?,?)",
            (message_id, guild_id, user_id),
        )
        return True
    except Exception:
        return False


async def try_record_reaction_given(message_id: int, user_id: int) -> bool:
    """Devuelve True solo la primera vez que este usuario reacciona a este
    mensaje (quitar y volver a poner la reacción no cuenta dos veces)."""
    try:
        await execute(
            "INSERT INTO reaction_given_events (message_id, user_id) VALUES (?,?)",
            (message_id, user_id),
        )
        return True
    except Exception:
        return False


# ─── Logros: desbloqueados ──────────────────────────────────────────────────


async def has_achievement(guild_id: int, user_id: int, key: str) -> bool:
    row = await fetchone(
        "SELECT 1 FROM user_achievements WHERE guild_id=? AND user_id=? AND achievement_key=?",
        (guild_id, user_id, key),
    )
    return row is not None


async def unlock_achievement(guild_id: int, user_id: int, key: str) -> bool:
    """Devuelve True si ESTA llamada lo desbloqueó (atómico, gracias a la
    PK compuesta); False si el usuario ya lo tenía."""
    try:
        await execute(
            "INSERT INTO user_achievements (guild_id, user_id, achievement_key) VALUES (?,?,?)",
            (guild_id, user_id, key),
        )
        return True
    except Exception:
        return False


async def get_user_achievements(guild_id: int, user_id: int) -> set[str]:
    rows = await fetchall(
        "SELECT achievement_key FROM user_achievements WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    return {r["achievement_key"] for r in rows}


async def remove_achievement(guild_id: int, user_id: int, key: str) -> bool:
    """Quita manualmente un logro ya desbloqueado (para corregir errores).
    Devuelve True si el usuario lo tenía y se eliminó; False si no lo tenía."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cur = await conn.execute(
            "DELETE FROM user_achievements WHERE guild_id=? AND user_id=? AND achievement_key=?",
            (guild_id, user_id, key),
        )
        await conn.commit()
        return cur.rowcount > 0


async def get_achievement_leaderboard(guild_id: int, limit: int = 10) -> list[dict]:
    return await fetchall(
        """SELECT user_id, COUNT(*) as total
           FROM user_achievements WHERE guild_id=?
           GROUP BY user_id ORDER BY total DESC LIMIT ?""",
        (guild_id, limit),
    )


# ─── db object (module-level singleton for import convenience) ────────────────


# ─── Anti-Scam Guard helpers ───────────────────────────────────────────────


async def get_scamguard_config(guild_id: int) -> dict:
    row = await fetchone("SELECT * FROM scamguard_config WHERE guild_id=?", (guild_id,))
    if not row:
        await execute(
            "INSERT OR IGNORE INTO scamguard_config (guild_id) VALUES (?)", (guild_id,)
        )
        row = await fetchone(
            "SELECT * FROM scamguard_config WHERE guild_id=?", (guild_id,)
        )
    return row or {}


async def get_all_scamguard_configs() -> list[dict]:
    return await fetchall("SELECT * FROM scamguard_config")


async def update_scamguard_config(guild_id: int, **kwargs) -> None:
    if not kwargs:
        return
    await execute(
        "INSERT OR IGNORE INTO scamguard_config (guild_id) VALUES (?)", (guild_id,)
    )
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [guild_id]
    await execute(f"UPDATE scamguard_config SET {cols} WHERE guild_id=?", tuple(vals))


async def register_scamguard_ban(
    guild_id: int,
    user_id: int,
    channel_id: int,
    content: str | None,
    attachments: int,
) -> int:
    """Registra un ban hecho por el Anti-Scam Guard e incrementa el contador
    de forma atómica. Devuelve el nuevo total de baneados del servidor."""
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        await conn.execute(
            "INSERT OR IGNORE INTO scamguard_config (guild_id) VALUES (?)", (guild_id,)
        )
        await conn.execute(
            "INSERT INTO scamguard_bans (guild_id, user_id, channel_id, content, attachments)"
            " VALUES (?,?,?,?,?)",
            (guild_id, user_id, channel_id, (content or "")[:1000], attachments),
        )
        await conn.execute(
            "UPDATE scamguard_config SET banned_count = banned_count + 1 WHERE guild_id=?",
            (guild_id,),
        )
        async with conn.execute(
            "SELECT banned_count FROM scamguard_config WHERE guild_id=?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
        await conn.commit()
        return int(row["banned_count"]) if row else 0


class _DB:
    """Thin wrapper that exposes all module-level helpers as attributes,
    so cogs can do `from database import db` and call `db.get_ticket()` etc."""

    def __getattr__(self, name):
        import sys

        mod = sys.modules[__name__]
        if hasattr(mod, name):
            return getattr(mod, name)
        raise AttributeError(f"database has no helper '{name}'")


db = _DB()
