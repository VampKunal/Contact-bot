import asyncio
import logging
import os
import datetime
import discord
from discord.ext import commands
from aiohttp import web

from config import config
from database.db import init_db, get_pipeline_stats

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("contact_bot")

class ContactDiscoveryBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        # Slash commands don't require privileged message_content intent
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )


    async def setup_hook(self):
        # 1. Initialize SQLite database schema
        logger.info("Initializing SQLite database...")
        await init_db()
        logger.info("Database initialized successfully.")

        # 2. Load Cogs
        cogs = [
            "bot.cogs.admin",
            "bot.cogs.review",
            "bot.cogs.pipeline"
        ]
        for cog in cogs:
            try:
                await self.load_extension(cog)
                logger.info(f"Loaded extension: {cog}")
            except Exception as e:
                logger.error(f"Failed to load extension {cog}: {e}", exc_info=True)

        # 3. Sync Slash Commands with graceful fallback
        try:
            if config.DISCORD_GUILD_ID:
                guild_obj = discord.Object(id=int(config.DISCORD_GUILD_ID))
                self.tree.copy_global_to(guild=guild_obj)
                synced = await self.tree.sync(guild=guild_obj)
                logger.info(f"Synced {len(synced)} slash commands to Guild ID: {config.DISCORD_GUILD_ID}")
            else:
                synced = await self.tree.sync()
                logger.info(f"Synced {len(synced)} global slash commands.")
        except discord.errors.Forbidden as e:
            logger.warning(
                f"Guild sync skipped (Missing Access / permissions): {e}. "
                "Attempting global slash command sync fallback..."
            )
            try:
                synced = await self.tree.sync()
                logger.info(f"Synced {len(synced)} global slash commands successfully.")
            except Exception as ex:
                logger.error(f"Global sync error: {ex}")
        except Exception as e:
            logger.error(f"Failed to sync slash commands: {e}")


    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guild(s).")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="tech jobs & contacts (/status)"
            )
        )
        
        # Start APScheduler for daily background runs
        try:
            from pipeline.scheduler import start_scheduler
            start_scheduler(self)
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")

# Global bot instance & start time for health reporting
bot_instance: Optional[ContactDiscoveryBot] = None
start_time = datetime.datetime.now(datetime.timezone.utc)

# --- Render Web Service Health Check Server ---
async def handle_health_check(request):
    """
    Honest health check endpoint for Render Web Service (supports GET and HEAD).
    Reports Discord gateway connection, last pipeline run, funnel metrics, and DB stats.
    """
    from pipeline.orchestrator import last_pipeline_run
    
    is_ready = bool(bot_instance and bot_instance.is_ready())
    uptime = int((datetime.datetime.now(datetime.timezone.utc) - start_time).total_seconds())

    db_ok = False
    pending_count = 0
    try:
        stats = await get_pipeline_stats()
        db_ok = True
        pending_count = stats.get("contacts_pending", 0)
    except Exception:
        pass

    status = "healthy" if (is_ready and db_ok) else "starting" if not is_ready else "degraded"

    payload = {
        "status": status,
        "service": "Contact Discovery Discord Bot",
        "gateway_connected": is_ready,
        "discord_user": str(bot_instance.user) if (bot_instance and bot_instance.user) else None,
        "guilds_count": len(bot_instance.guilds) if (bot_instance and bot_instance.guilds) else 0,
        "db_connected": db_ok,
        "pending_leads_count": pending_count,
        "last_run_timestamp": last_pipeline_run.get("timestamp"),
        "last_run_funnel": last_pipeline_run.get("funnel"),
        "uptime_seconds": uptime,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    return web.json_response(payload, status=200 if status != "degraded" else 503)

async def start_web_server(port: int = 10000):
    """Start asynchronous web server for Render port binding."""
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    app.router.add_get("/health", handle_health_check)
    app.router.add_head("/", handle_health_check)
    app.router.add_head("/health", handle_health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Render Web Service honest health check listening on port {port}")

async def main():
    global bot_instance
    if not config.DISCORD_BOT_TOKEN:
        logger.warning("DISCORD_BOT_TOKEN is not set in .env! Please configure your token before launching.")
        return

    # 1. Initialize SQLite database schema
    logger.info("Initializing SQLite database...")
    await init_db()
    logger.info("Database initialized successfully.")

    # 2. Start healthcheck web server for Render Web Service
    port = int(os.getenv("PORT", "10000"))
    await start_web_server(port)

    # 3. Start Discord Bot
    bot_instance = ContactDiscoveryBot()
    async with bot_instance:
        await bot_instance.start(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
