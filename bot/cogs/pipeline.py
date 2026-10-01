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

            await interaction.followup.send(embed=embed)

            # Post interactive cards for newly added leads immediately
            new_leads = results.get("newly_added_leads", [])
            from bot.cogs.review import ContactActionView, infer_target_hiring_roles
            from database.db import get_pending_contacts
            
            leads_to_post = list(new_leads[:20])
            if len(leads_to_post) < 10:
                more_pending = await get_pending_contacts(limit=10)
                for p in more_pending:
                    if p["id"] not in [l["id"] for l in leads_to_post] and len(leads_to_post) < 15:
                        leads_to_post.append(p)

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
                await interaction.channel.send(embed=card, view=view)
                await asyncio.sleep(0.3)

        except Exception as e:
            await interaction.followup.send(f"❌ Pipeline failed with error: `{str(e)}`")

    @app_commands.command(name="blast", description="Rapid high-volume discovery: fetch 20-50 tech leads and verified emails immediately.")
    @app_commands.describe(company="Optional specific company name or domain (e.g. Swiggy, Razorpay, Zepto)")
    async def blast_cmd(self, interaction: discord.Interaction, company: Optional[str] = None):
        await interaction.response.defer()
        await interaction.followup.send(f"⚡ **Initiating High-Yield Blast Discovery** {'for ' + company if company else 'across top tech hubs'}...")
        
        from pipeline.contact_discovery import discover_raw_contacts, verify_survivor_contact_email
        from pipeline.llm_validator import validate_contact_batch, calculate_calibrated_confidence
        from database.db import add_company, add_contact, get_company_by_domain
        from bot.cogs.review import ContactActionView, infer_target_hiring_roles

        if company:
            clean_dom = company.lower().replace("http://", "").replace("https://", "").strip().rstrip("/")
            if "." not in clean_dom:
                clean_dom = f"{clean_dom.replace(' ', '')}.com"
            comp_name = company.split(".")[0].capitalize()
            comp_id = await add_company(name=comp_name, domain=clean_dom, region="India", tier=1)
            target_list = [{"id": comp_id, "name": comp_name, "domain": clean_dom, "region": "India"}]
        else:
            from database.db import get_uncontacted_companies, get_companies
            target_list = await get_uncontacted_companies(limit=15)
            if not target_list:
                target_list = await get_companies()
                target_list = target_list[:15]

        raw_leads = []
        for c in target_list:
            leads = await discover_raw_contacts(c["id"], c["name"], c["domain"])
            raw_leads.extend(leads)

        validated = await validate_contact_batch(raw_leads)
        survivors = [v for v in validated if v.get("is_llm_passed", True)]

        posted_count = 0
        for cand in survivors[:25]:
            verified_cand = await verify_survivor_contact_email(cand)
            confidence = calculate_calibrated_confidence(
                llm_relevant=True,
                llm_score=verified_cand.get("raw_llm_score", 0.8),
                red_flags=verified_cand.get("red_flags", []),
                email_verified=verified_cand.get("email_verified", False),
                source_type=verified_cand.get("source", "scraped")
            )
            cid = await add_contact(
                company_id=verified_cand["company_id"],
                name=verified_cand["name"],
                title=verified_cand.get("title"),
                title_normalized=verified_cand.get("title_normalized"),
                email=verified_cand.get("email"),
                source=verified_cand.get("source", "blast_discovery"),
                email_verified=verified_cand.get("email_verified", False),
                llm_confidence=confidence,
                llm_reasoning=verified_cand.get("llm_reasoning")
            )
            if cid:
                posted_count += 1
                verified_badge = "✅ Verified Mailbox" if verified_cand.get("email_verified") else "⚠️ Pattern-Guessed"
                conf_str = f"{(confidence * 100):.0f}%"
                roles = infer_target_hiring_roles(verified_cand.get("title_normalized") or verified_cand.get("title"), "GenAI / Full-Stack")

                card = discord.Embed(
                    title=f"🎯 Lead: {verified_cand['name']} ({verified_cand.get('title_normalized') or verified_cand.get('title')}) @ {verified_cand.get('company_name', 'Tech')}",
                    color=discord.Color.teal(),
                    timestamp=datetime.datetime.now(datetime.timezone.utc)
                )
                card.add_field(name="🏢 Company", value=f"{verified_cand.get('company_name')} (`{verified_cand.get('domain')}`)", inline=False)
                card.add_field(name="📬 Email", value=f"`{verified_cand.get('email')}` ({verified_badge})", inline=True)
                card.add_field(name="💼 Can Hire For", value=f"`{roles}`", inline=True)
                card.add_field(name="🧠 Calibrated Score", value=f"**{conf_str}** — {verified_cand.get('llm_reasoning') or 'Standard match'}", inline=False)
                card.set_footer(text=f"Contact ID #{cid} • Click Approve & Draft below or use /draft {cid}")

                view = ContactActionView(cid)
                await interaction.channel.send(embed=card, view=view)
                await asyncio.sleep(0.3)

        await interaction.channel.send(f"✅ **Blast Complete!** Successfully delivered **{posted_count} fresh verified leads** to this channel.")

async def setup(bot: commands.Bot):
    await bot.add_cog(PipelineCog(bot))
