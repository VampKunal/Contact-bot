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
    """Job triggered daily to run the Apollo discovery pipeline and post Discord summary."""
    logger.info("Executing scheduled daily Apollo discovery run...")
    try:
        results = await run_full_pipeline()
        
        # Broadcast summary to notification channel
        channel_id = config.DISCORD_NOTIFICATION_CHANNEL_ID
        if channel_id and bot.is_ready():
            try:
                channel = bot.get_channel(int(channel_id))
                if channel:
                    stats = results.get("stats", {})
                    apollo_pool = results.get("apollo_pool", {})

                    embed = discord.Embed(
                        title="🚀 Daily Contact Discovery Complete",
                        description=f"Pulled new tech leads across Delhi/NCR/Noida/Gurgaon using active Apollo key pool.",
                        color=discord.Color.green(),
                        timestamp=datetime.datetime.now(datetime.timezone.utc)
                    )
                    embed.add_field(name="👥 Contacts Discovered Today", value=str(results.get("contacts_discovered_this_run", 0)), inline=True)
                    embed.add_field(name="📬 Verified Emails", value=str(results.get("emails_verified_this_run", 0)), inline=True)
                    embed.add_field(name="⏳ Pending Review Total", value=str(stats.get("contacts_pending", 0)), inline=True)
                    
                    embed.add_field(
                        name="🚀 Apollo Multi-Key Pool Status",
                        value=f"Used **{apollo_pool.get('total_used', 0)} / {apollo_pool.get('total_cap', 0)}** weekly credits across {apollo_pool.get('total_keys', 1)} account(s)",
                        inline=False
                    )

                    # Top pending preview
                    from database.db import get_pending_contacts
                    from bot.cogs.review import infer_target_hiring_roles
                    top_leads = await get_pending_contacts(limit=3)
                    if top_leads:
                        preview_cards = []
                        for p in top_leads:
                            verified_icon = "✅" if p.get("email_verified") else "⚠️"
                            roles = infer_target_hiring_roles(p.get("title_normalized") or p.get("title"), p.get("tech_stack_match"))
                            preview_cards.append(
                                f"• **{p['name']}** ({p.get('title') or 'Lead'})\n"
                                f"  🏢 **Company:** {p.get('company_name')} (`{p.get('company_domain')}`)\n"
                                f"  📬 **Email:** `{p.get('email')}` ({verified_icon})\n"
                                f"  💼 **Hiring For:** `{roles}`\n"
                                f"  👉 `/draft {p['id']}`"
                            )
                        embed.add_field(name="🎯 Latest Contacts Ready for Review", value="\n\n".join(preview_cards), inline=False)

                    embed.set_footer(text="Run /pending to review all candidate leads or /draft <id> to create outreach emails.")
                    await channel.send(embed=embed)
            except Exception as e:
                logger.error(f"Error posting daily summary to Discord channel: {e}")

    except Exception as e:
        logger.error(f"Error during daily pipeline run: {e}", exc_info=True)

def start_scheduler(bot: discord.Client):
    """Start the APScheduler background task (runs every day at 09:00 UTC)."""
    trigger = CronTrigger(hour=9, minute=0)
    scheduler.add_job(
        scheduled_daily_run,
        trigger=trigger,
        args=[bot],
        id="daily_discovery_job",
        replace_existing=True
    )
    scheduler.start()
    logger.info("APScheduler initialized for daily automated runs (Daily 09:00 UTC).")

