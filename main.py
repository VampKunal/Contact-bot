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
        intents.message_content = True
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

# --- Render Web Service Health Check Server ---
async def handle_health_check(request):
    """Health check endpoint for Render Web Service."""
    try:
        stats = await get_pipeline_stats()
        return web.json_response({
            "status": "online",
            "service": "Contact Discovery Discord Bot",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "pipeline_stats": stats
        })
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def start_web_server(port: int = 10000):
    """Start asynchronous web server for Render port binding."""
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    app.router.add_get("/health", handle_health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Render Web Service health check listening on port {port}")

async def main():
    if not config.DISCORD_BOT_TOKEN:
        logger.warning("DISCORD_BOT_TOKEN is not set in .env! Please configure your token before launching.")
        return

    # Start healthcheck web server for Render Web Service (binds to PORT env variable)
    port = int(os.getenv("PORT", "10000"))
    await start_web_server(port)

    # Start Discord Bot
    bot = ContactDiscoveryBot()
    async with bot:
        await bot.start(config.DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
