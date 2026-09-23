import discord
from discord import app_commands
from discord.ext import commands
import datetime

from database.db import (
    add_region,
    get_regions,
    add_company,
    get_pipeline_stats,
    get_apollo_credits_used,
    get_apollo_pool_status,
    get_current_week_start
)

from config import config

class AdminCog(commands.Cog):
    """Cog for administrative, region, company management and status reporting."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="addregion", description="Add a new target region for company discovery.")
    @app_commands.describe(name="Region name (e.g. Noida, Gurgaon, Delhi NCR)", query_template="Optional search template")
    async def add_region_cmd(self, interaction: discord.Interaction, name: str, query_template: str = None):
        success = await add_region(name, query_template)
        if success:
            embed = discord.Embed(
                title="✅ Region Added",
                description=f"Region **{name}** has been added to target locations.",
                color=discord.Color.green(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            if query_template:
                embed.add_field(name="Query Template", value=query_template, inline=False)
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                f"⚠️ Region **{name}** already exists or could not be added.",
                ephemeral=True
            )

    @app_commands.command(name="addcompany", description="Manually register a company.")
    @app_commands.describe(
        name="Company Name",
        domain="Website Domain (e.g. company.com)",
        region="Target Region (e.g. Gurgaon, Noida)",
        tier="Company Tier (1: High Priority, 2: Standard, 3: Backup)",
        tech_stack_match="Relevant stack keywords (e.g. Next.js, Node.js, Python, FastAPI)"
    )
    async def add_company_cmd(
        self,
        interaction: discord.Interaction,
        name: str,
        domain: str,
        region: str = "Delhi NCR",
        tier: int = 2,
        tech_stack_match: str = None
    ):
        if tier not in (1, 2, 3):
            await interaction.response.send_message("❌ Tier must be 1, 2, or 3.", ephemeral=True)
            return

        company_id = await add_company(
            name=name,
            domain=domain,
            region=region,
            tier=tier,
            source="manual",
            tech_stack_match=tech_stack_match
        )

        embed = discord.Embed(
            title="🏢 Company Registered",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(name="ID", value=str(company_id), inline=True)
        embed.add_field(name="Name", value=name, inline=True)
        embed.add_field(name="Domain", value=domain.lower(), inline=True)
        embed.add_field(name="Region", value=region, inline=True)
        embed.add_field(name="Tier", value=f"Tier {tier}", inline=True)
        if tech_stack_match:
            embed.add_field(name="Tech Stack", value=tech_stack_match, inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="status", description="Get the pipeline status and statistics.")
    async def status_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        stats = await get_pipeline_stats()
        regions = await get_regions(active_only=True)
        apollo_pool = await get_apollo_pool_status()
        week_start = get_current_week_start()

        embed = discord.Embed(
            title="📊 Contact Discovery Bot - Pipeline Status",
            color=discord.Color.purple(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )

        embed.add_field(name="🏢 Total Companies", value=f"**{stats['companies']}**", inline=True)
        embed.add_field(name="👥 Total Contacts", value=f"**{stats['contacts_total']}**", inline=True)
        embed.add_field(name="📬 Verified Emails", value=f"**{stats['emails_verified']}**", inline=True)

        embed.add_field(name="⏳ Pending Review", value=f"**{stats['contacts_pending']}**", inline=True)
        embed.add_field(name="✅ Approved Contacts", value=f"**{stats['contacts_approved']}**", inline=True)
        embed.add_field(name="❌ Rejected Contacts", value=f"**{stats['contacts_rejected']}**", inline=True)

        # Multi-key pool display
        apollo_lines = [
            f"**Total Pool Usage:** {apollo_pool['total_used']} / {apollo_pool['total_cap']} credits ({apollo_pool['total_keys']} active account(s))"
        ]
        for k in apollo_pool["keys"]:
            if k["configured"]:
                status_icon = "🔴 Exhausted" if k["exhausted"] else "🟢 Active"
                apollo_lines.append(f"• **Key #{k['key_index']+1}:** {k['credits_used']} / {k['cap']} ({status_icon})")
        
        embed.add_field(
            name="🚀 Apollo.io Multi-Key Pool (Weekly)",
            value="\n".join(apollo_lines),
            inline=False
        )

        active_region_names = ", ".join([r["name"] for r in regions]) if regions else "None"
        embed.add_field(name="📍 Active Target Regions", value=active_region_names, inline=False)
        embed.add_field(name="🧠 LLM Provider Active", value=f"`{config.LLM_PROVIDER.upper()}`", inline=True)


        await interaction.followup.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
