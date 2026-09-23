import asyncio
import logging
import discord
from discord.ext import commands

from config import config
from database.db import init_db

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

        # 3. Sync Slash Commands
        if config.DISCORD_GUILD_ID:
            guild_obj = discord.Object(id=int(config.DISCORD_GUILD_ID))
            self.tree.copy_global_to(guild=guild_obj)
            synced = await self.tree.sync(guild=guild_obj)
            logger.info(f"Synced {len(synced)} slash commands to Guild ID: {config.DISCORD_GUILD_ID}")
        else:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} global slash commands.")

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guild(s).")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="tech jobs & contacts (/status)"
            )
        )
        
        # Start APScheduler for weekly background runs
        try:
            from pipeline.scheduler import start_scheduler
            start_scheduler(self)
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")


async def main():
    if not config.DISCORD_BOT_TOKEN:
        logger.warning("DISCORD_BOT_TOKEN is not set in .env! Please configure your token before launching.")
        return

    bot = ContactDiscoveryBot()
    async with bot:
        await bot.start(config.DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
