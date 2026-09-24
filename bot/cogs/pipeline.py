import discord
from discord import app_commands
from discord.ext import commands
import datetime
from typing import Optional

from pipeline.company_discovery import run_company_discovery
from pipeline.orchestrator import run_full_pipeline

class PipelineCog(commands.Cog):
    """Cog for triggering discovery and pipeline runs on demand."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="discover", description="Discover new tech companies in active regions via Google Places.")
    @app_commands.describe(region="Optional specific region filter (e.g. Gurgaon, Noida, Delhi NCR)")
    async def discover_cmd(self, interaction: discord.Interaction, region: Optional[str] = None):
        await interaction.response.defer()
        
        res = await run_company_discovery(region_filter=region)
        companies = res.get("companies", [])

        embed = discord.Embed(
            title="🔍 Company Discovery Complete",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(name="🏢 Total New Companies Found", value=f"**{len(companies)}**", inline=False)

        if companies:
            preview_lines = []
            for c in companies[:8]:
                preview_lines.append(f"• **{c['name']}** ({c['domain']}) — *Tier {c['tier']}* ({c['region']})")
            if len(companies) > 8:
                preview_lines.append(f"*...and {len(companies) - 8} more companies added to DB.*")
            embed.add_field(name="Newly Added", value="\n".join(preview_lines), inline=False)
        else:
            embed.description = "No new unique companies found for the specified region(s) or API key not set."

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="runpipeline", description="Execute full automated pipeline (Discovery -> Scraping -> Verification -> LLM Validation).")
    @app_commands.describe(region="Optional specific region filter")
    async def runpipeline_cmd(self, interaction: discord.Interaction, region: Optional[str] = None):
        await interaction.response.defer()
        
        await interaction.followup.send("🚀 **Pipeline execution started!** Searching companies, scraping contacts, verifying mailboxes, and running LLM validation...")
        
        try:
            results = await run_full_pipeline(region_filter=region)
            stats = results.get("stats", {})
            funnel = results.get("funnel", {})

            embed = discord.Embed(
                title="✨ Pipeline Execution Finished",
                description="End-to-end pipeline run completed successfully. Review the stage funnel below:",
                color=discord.Color.green(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            embed.add_field(name="🏢 Companies Searched", value=str(funnel.get("companies_searched", 0)), inline=True)
            embed.add_field(name="👥 Raw Contacts Sourced", value=str(funnel.get("raw_contacts_found", 0)), inline=True)
            embed.add_field(name="🧠 LLM Filter Passed", value=str(funnel.get("llm_passed", 0)), inline=True)
            embed.add_field(name="📬 Verified Mailboxes", value=str(funnel.get("smtp_verified", 0)), inline=True)
            embed.add_field(name="➕ New Leads Added", value=str(funnel.get("leads_added", 0)), inline=True)
            embed.add_field(name="⏳ Pending Review Total", value=str(stats.get("contacts_pending", 0)), inline=True)

            # Fetch top 3 pending contacts to give immediate actionable preview
            from database.db import get_pending_contacts
            from bot.cogs.review import infer_target_hiring_roles
            
            top_pending = await get_pending_contacts(limit=3)
            if top_pending:
                preview_cards = []
                for p in top_pending:
                    verified_icon = "✅ Verified" if p.get("email_verified") else "⚠️ Guessed"
                    conf_val = p.get("llm_confidence")
                    conf_str = f"{(conf_val * 100):.0f}%" if conf_val is not None else "Unrated"
                    roles = infer_target_hiring_roles(p.get("title_normalized") or p.get("title"), p.get("tech_stack_match"))
                    preview_cards.append(
                        f"• **{p['name']}** — *{p.get('title_normalized') or p.get('title') or 'Lead'}*\n"
                        f"  🏢 **Company:** {p.get('company_name')} (`{p.get('company_domain')}`)\n"
                        f"  📬 **Email:** `{p.get('email')}` ({verified_icon}) • Score: `{conf_str}`\n"
                        f"  💼 **Hiring For:** `{roles}`\n"
                        f"  👉 `/approve {p['id']}` • `/draft {p['id']}`"
                    )
                embed.add_field(name="🎯 Latest Contacts Ready for Review", value="\n\n".join(preview_cards), inline=False)

            embed.set_footer(text="Run /pending to review all candidate leads with 1-click Approve buttons.")
            await interaction.channel.send(embed=embed)


        except Exception as e:
            await interaction.channel.send(f"❌ Pipeline failed with error: `{str(e)}`")

async def setup(bot: commands.Bot):
    await bot.add_cog(PipelineCog(bot))
