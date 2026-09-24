import logging
import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import datetime

from config import config
from pipeline.orchestrator import run_full_pipeline

logger = logging.getLogger("scheduler")

scheduler = AsyncIOScheduler()

async def scheduled_daily_run(bot: discord.Client):
    """Job triggered daily to run autonomous discovery and post ready-to-send emails to Discord."""
    logger.info("Executing scheduled daily autonomous discovery run...")
    try:
        results = await run_full_pipeline()
        
        channel_id = config.DISCORD_NOTIFICATION_CHANNEL_ID
        if channel_id and bot.is_ready():
            try:
                channel = bot.get_channel(int(channel_id))
                if channel:
                    stats = results.get("stats", {})
                    leads = results.get("actionable_leads", [])

                    # 1. Summary Embed Header
                    header_embed = discord.Embed(
                        title="🌅 Morning Tech-Job Discovery Report",
                        description="Here are today's newly discovered hiring leaders, verified mailboxes, and pre-generated cold outreach emails.",
                        color=discord.Color.gold(),
                        timestamp=datetime.datetime.now(datetime.timezone.utc)
                    )
                    header_embed.add_field(name="🏢 Companies Crawled", value=str(results.get("new_companies_discovered", 0)), inline=True)
                    header_embed.add_field(name="👥 Contacts Sourced", value=str(results.get("contacts_discovered_this_run", 0)), inline=True)
                    header_embed.add_field(name="📬 Verified Emails", value=str(results.get("emails_verified_this_run", 0)), inline=True)
                    await channel.send(embed=header_embed)

                    # 2. Individual Ready-to-Send Action Cards
                    for lead in leads:
                        verified_badge = "✅ Verified" if lead.get("email_verified") else "⚠️ Guessed"
                        card = discord.Embed(
                            title=f"🎯 Lead: {lead['name']} ({lead['title']}) @ {lead['company_name']}",
                            color=discord.Color.teal(),
                            timestamp=datetime.datetime.now(datetime.timezone.utc)
                        )
                        card.add_field(name="🏢 Company", value=f"{lead['company_name']} (`{lead['domain']}`)", inline=True)
                        card.add_field(name="📬 Email", value=f"`{lead['email']}` ({verified_badge})", inline=True)
                        card.add_field(name="📌 Subject Line", value=f"**{lead['draft_subject']}**", inline=False)
                        card.add_field(name="📝 Pre-written Cold Email (Ready to Copy)", value=f"```text\n{lead['draft_body']}\n```", inline=False)
                        card.set_footer(text=f"Contact ID #{lead['contact_id']} | /approve {lead['contact_id']} to mark done")
                        await channel.send(embed=card)

            except Exception as e:
                logger.error(f"Error posting daily summary to Discord channel: {e}")

    except Exception as e:
        logger.error(f"Error during daily autonomous run: {e}", exc_info=True)

def start_scheduler(bot: discord.Client):
    """Start the APScheduler background task (runs every morning at 09:00 UTC)."""
    trigger = CronTrigger(hour=9, minute=0)
    scheduler.add_job(
        scheduled_daily_run,
        trigger=trigger,
        args=[bot],
        id="daily_autonomous_discovery_job",
        replace_existing=True
    )
    scheduler.start()
    logger.info("APScheduler active: Auto-delivering daily verified contacts and email drafts at 09:00 UTC.")
