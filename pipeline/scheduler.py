import logging
import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import datetime

from config import config
from pipeline.orchestrator import run_full_pipeline
from database.db import get_pending_contacts
from bot.cogs.review import ContactActionView, infer_target_hiring_roles

logger = logging.getLogger("scheduler")

scheduler = AsyncIOScheduler()

async def scheduled_daily_run(bot: discord.Client):
    """Job triggered daily to run autonomous discovery and post fresh leads to Discord."""
    logger.info("Executing scheduled morning autonomous discovery run...")
    try:
        results = await run_full_pipeline()
        
        channel_id = config.DISCORD_NOTIFICATION_CHANNEL_ID
        if channel_id and bot.is_ready():
            try:
                channel = bot.get_channel(int(channel_id))
                if channel:
                    stats = results.get("stats", {})
                    funnel = results.get("funnel", {})

                    # 1. Funnel Summary Embed Header
                    header_embed = discord.Embed(
                        title="🌅 Morning Tech-Job Discovery Report (Delhi NCR)",
                        description="Autonomous morning run complete. Here is today's discovery funnel and newly verified leads ready for review.",
                        color=discord.Color.gold(),
                        timestamp=datetime.datetime.now(datetime.timezone.utc)
                    )
                    header_embed.add_field(name="🏢 Companies Crawled", value=str(funnel.get("companies_searched", 0)), inline=True)
                    header_embed.add_field(name="👥 Raw Contacts Sourced", value=str(funnel.get("raw_contacts_found", 0)), inline=True)
                    header_embed.add_field(name="🧠 LLM Filter Passed", value=str(funnel.get("llm_passed", 0)), inline=True)
                    header_embed.add_field(name="📬 Verified Mailboxes", value=str(funnel.get("smtp_verified", 0)), inline=True)
                    header_embed.add_field(name="⏳ Pending Review Total", value=str(stats.get("contacts_pending", 0)), inline=True)
                    header_embed.add_field(name="✅ Total Approved", value=str(stats.get("contacts_approved", 0)), inline=True)
                    header_embed.set_footer(text="Click 'Approve & Draft' below on any lead to instantly generate a tailored outreach email!")
                    await channel.send(embed=header_embed)

                    # 2. Post all newly discovered leads with interactive Approve & Draft buttons
                    post_limit = config.DISCORD_DAILY_POST_LIMIT
                    new_leads = results.get("newly_added_leads", [])
                    
                    # If we have newly added leads from today's run, prioritize them!
                    leads_to_post = list(new_leads[:post_limit])
                    
                    # If fewer new leads than post limit, fetch remaining un-reviewed pending leads
                    if len(leads_to_post) < post_limit:
                        remaining_slots = post_limit - len(leads_to_post)
                        already_ids = {l["id"] for l in leads_to_post}
                        db_pending = await get_pending_contacts(limit=remaining_slots + len(already_ids))
                        for p in db_pending:
                            if p["id"] not in already_ids and len(leads_to_post) < post_limit:
                                leads_to_post.append(p)
                                already_ids.add(p["id"])

                    logger.info(f"Delivering {len(leads_to_post)} high-priority leads to Discord channel...")
                    
                    for lead in leads_to_post:
                        verified_badge = "✅ Verified Mailbox" if lead.get("email_verified") else "⚠️ Pattern-Guessed"
                        conf_val = lead.get("llm_confidence")
                        conf_str = f"{(conf_val * 100):.0f}%" if conf_val is not None else "Unrated"
                        clean_title = lead.get("title_normalized") or lead.get("title", "Engineering Leader")
                        roles = infer_target_hiring_roles(clean_title, lead.get("tech_stack_match"))

                        card = discord.Embed(
                            title=f"🎯 Lead: {lead['name']} ({clean_title}) @ {lead.get('company_name', 'Tech Company')}",
                            color=discord.Color.teal(),
                            timestamp=datetime.datetime.now(datetime.timezone.utc)
                        )
                        card.add_field(name="🏢 Company", value=f"{lead.get('company_name')} (`{lead.get('company_domain')}`) • 📍 {lead.get('company_region', 'Delhi NCR')}", inline=False)
                        card.add_field(name="📬 Email", value=f"`{lead.get('email')}` ({verified_badge})", inline=True)
                        card.add_field(name="💼 Can Hire For", value=f"`{roles}`", inline=True)
                        card.add_field(name="🧠 Calibrated Score", value=f"**{conf_str}** — {lead.get('llm_reasoning') or 'Standard match'}", inline=False)
                        card.set_footer(text=f"Contact ID #{lead['id']} • Click Approve & Draft below or use /draft {lead['id']}")

                        view = ContactActionView(lead["id"])
                        await channel.send(embed=card, view=view)
                        await asyncio.sleep(0.4)  # Smooth rate limiting

                    # 3. If there are more pending leads than posted, send guidance note
                    total_pending = stats.get("contacts_pending", 0)
                    if total_pending > len(leads_to_post):
                        more_embed = discord.Embed(
                            description=f"📊 **{total_pending - len(leads_to_post)} more leads** are waiting in the queue! Use `/pending` to browse all pending leads or `/export` to download them.",
                            color=discord.Color.blue()
                        )
                        await channel.send(embed=more_embed)

            except Exception as e:
                logger.error(f"Error posting daily summary to Discord channel: {e}")

    except Exception as e:
        logger.error(f"Error during daily autonomous run: {e}", exc_info=True)

def start_scheduler(bot: discord.Client):
    """
    Start the APScheduler background task.
    Defaults to 03:30 UTC (09:00 AM IST) for fresh morning leads.
    """
    hour = config.SCHEDULE_CRON_HOUR
    minute = config.SCHEDULE_CRON_MINUTE
    trigger = CronTrigger(hour=hour, minute=minute)
    scheduler.add_job(
        scheduled_daily_run,
        trigger=trigger,
        args=[bot],
        id="daily_autonomous_discovery_job",
        replace_existing=True
    )
    scheduler.start()
    logger.info(f"APScheduler active: Auto-delivering daily verified contacts at {hour:02d}:{minute:02d} UTC (Morning IST).")
