import discord
from discord import app_commands
from discord.ext import commands
import datetime
from typing import Optional

from database.db import (
    get_pending_contacts,
    get_contact_by_id,
    update_contact_status
)
from pipeline.draft_generator import generate_outreach_draft, create_gmail_draft
from config import config

class ContactActionView(discord.ui.View):
    """Action buttons for pending contacts."""
    def __init__(self, contact_id: int):
        super().__init__(timeout=300)
        self.contact_id = contact_id

    @discord.ui.button(label="Approve & Draft", style=discord.ButtonStyle.success, emoji="✅")
    async def approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        contact = await get_contact_by_id(self.contact_id)
        if not contact:
            await interaction.followup.send("❌ Contact not found.", ephemeral=True)
            return

        success = await update_contact_status(self.contact_id, "approved")
        if not success:
            await interaction.followup.send("❌ Failed to update contact status.", ephemeral=True)
            return

        # Automatically generate draft upon approval (saves tokens prior to approval)
        draft_res = await generate_outreach_draft(contact)
        subject = draft_res.get("subject", "Intern/SDE Opportunity")
        body = draft_res.get("body", "")
        mailto_url = draft_res.get("mailto_url", "")
        
        # Optional Gmail API draft creation
        gmail_draft_id = await create_gmail_draft(contact.get("email", ""), subject, body)

        embed = discord.Embed(
            title=f"✅ Approved: {contact['name']} @ {contact['company_name']}",
            description="Contact marked as **Approved**. Here is your tailored cold email draft ready to send:",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(name="📬 Recipient", value=f"`{contact.get('email', 'N/A')}`", inline=True)
        embed.add_field(name="🏢 Company", value=f"{contact['company_name']}", inline=True)
        if mailto_url:
            embed.add_field(name="⚡ 1-Click Send", value=f"[👉 Open Pre-filled Email in Mail Client]({mailto_url})", inline=False)
        if gmail_draft_id:
            embed.add_field(name="📨 Gmail Draft Created", value=f"Draft ID: `{gmail_draft_id}`", inline=False)
        embed.add_field(name="📌 Subject", value=f"**{subject}**", inline=False)
        embed.add_field(name="📝 Body (Ready to Copy)", value=f"```text\n{body}\n```", inline=False)
        embed.set_footer(text=f"Contact ID #{self.contact_id} • Click the link above or copy-paste body into Gmail")

        await interaction.followup.send(embed=embed)
        self.stop()

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def reject_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        success = await update_contact_status(self.contact_id, "rejected")
        if success:
            contact = await get_contact_by_id(self.contact_id)
            name = contact["name"] if contact else f"ID {self.contact_id}"
            await interaction.response.send_message(f"🗑️ Contact **{name}** (ID: {self.contact_id}) marked as **Rejected**.", ephemeral=False)
            self.stop()
        else:
            await interaction.response.send_message("❌ Failed to update contact status.", ephemeral=True)


def infer_target_hiring_roles(title: Optional[str], tech_stack: Optional[str]) -> str:
    """Infer the relevant hiring roles based on decision-maker title and company tech stack."""
    t = (title or "").lower()
    roles = []
    
    if any(k in t for k in ("cto", "chief technology", "vp", "head of eng", "director", "architect")):
        roles.append("SDE Intern (Backend/Full-Stack), Fresher SDE, GenAI Engineer")
    elif any(k in t for k in ("founder", "co-founder", "ceo")):
        roles.append("Founding SDE Intern, Early-Stage Full-Stack Developer")
    elif any(k in t for k in ("hr", "recruiter", "talent", "people")):
        roles.append("2026 Batch SDE Intern, Junior Software Engineer")
    elif any(k in t for k in ("manager", "lead")):
        roles.append("Software Engineering Intern, Junior Backend/Frontend Developer")
    else:
        roles.append("SDE Intern, Fresher SDE")

    if tech_stack and any(k in tech_stack.lower() for k in ("ai", "genai", "llm", "langgraph")):
        roles.append("AI / LLM Application Developer")

    return " | ".join(roles)


class ReviewCog(commands.Cog):
    """Cog for reviewing, approving, rejecting contacts and generating drafts."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="pending", description="List candidate hiring contacts awaiting review.")
    @app_commands.describe(page="Page number (default: 1)")
    async def pending_cmd(self, interaction: discord.Interaction, page: int = 1):
        if page < 1:
            page = 1
        limit = 5
        offset = (page - 1) * limit

        contacts = await get_pending_contacts(limit=limit, offset=offset)

        if not contacts:
            await interaction.response.send_message(
                f"🎉 No pending contacts found for page {page}. All caught up!",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"📋 Pending Contacts Review (Page {page})",
            description="Review candidate leads before outreach. Click **Approve & Draft** to generate personalized email, or run `/draft <id>`.",
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )

        for c in contacts:
            conf_val = c.get('llm_confidence')
            conf_str = f"{(conf_val * 100):.0f}%" if conf_val is not None else "Unrated"
            verified_badge = "✅ Verified Mailbox" if c.get('email_verified') else "⚠️ Pattern-Guessed"
            
            clean_title = c.get('title_normalized') or c.get('title') or 'Engineering Leader'
            hiring_for = infer_target_hiring_roles(clean_title, c.get('tech_stack_match'))

            value_lines = [
                f"🏢 **Company:** {c.get('company_name')} (`{c.get('company_domain')}`) • 📍 {c.get('company_region', 'Delhi NCR')}",
                f"👥 **Decision Maker:** **{c['name']}** — *{clean_title}*",
                f"📬 **Email:** `{c.get('email') or 'N/A'}` ({verified_badge})",
                f"💼 **Can Hire For:** `{hiring_for}`",
                f"🧠 **Calibrated Score ({conf_str}):** {c.get('llm_reasoning') or 'Standard multi-signal match'}",
                f"👉 **Actions:** `/approve {c['id']}` • `/reject {c['id']}` • `/draft {c['id']}`"
            ]

            embed.add_field(
                name=f"━━━━━━━━ ID #{c['id']} : {c['name']} @ {c.get('company_name')} ━━━━━━━━",
                value="\n".join(value_lines),
                inline=False
            )

        embed.set_footer(text=f"Showing contacts {offset+1} - {offset+len(contacts)} | Use /approve <id> to approve & generate draft")
        
        # Attach action view for top contact on current page
        first_contact_id = contacts[0]["id"]
        view = ContactActionView(first_contact_id)
        await interaction.response.send_message(embed=embed, view=view)


    @app_commands.command(name="approve", description="Approve a contact and immediately generate their outreach email draft.")
    @app_commands.describe(contact_id="ID of the contact to approve")
    async def approve_cmd(self, interaction: discord.Interaction, contact_id: int):
        await interaction.response.defer()
        contact = await get_contact_by_id(contact_id)
        if not contact:
            await interaction.followup.send(f"❌ Contact with ID {contact_id} not found.", ephemeral=True)
            return

        success = await update_contact_status(contact_id, "approved")
        if not success:
            await interaction.followup.send("❌ Failed to update contact status.", ephemeral=True)
            return

        # Generate personalized email draft on-demand (saving tokens for unapproved leads)
        draft_res = await generate_outreach_draft(contact)
        subject = draft_res.get("subject", "Intern/SDE Opportunity")
        body = draft_res.get("body", "")
        mailto_url = draft_res.get("mailto_url", "")
        gmail_draft_id = await create_gmail_draft(contact.get("email", ""), subject, body)

        embed = discord.Embed(
            title=f"✅ Contact Approved: {contact['name']}",
            description=f"**{contact['name']}** ({contact.get('title', 'Leader')}) at **{contact['company_name']}** is now approved!",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(name="📬 Recipient Email", value=f"`{contact.get('email', 'N/A')}`", inline=True)
        embed.add_field(name="🏢 Company", value=f"{contact['company_name']}", inline=True)
        if mailto_url:
            embed.add_field(name="⚡ 1-Click Send", value=f"[👉 Open Pre-filled Email in Mail Client]({mailto_url})", inline=False)
        if gmail_draft_id:
            embed.add_field(name="📨 Gmail Draft Created", value=f"Draft ID: `{gmail_draft_id}`", inline=False)
        embed.add_field(name="📌 Subject Line", value=f"**{subject}**", inline=False)
        embed.add_field(name="📝 Cold Outreach Email Body", value=f"```text\n{body}\n```", inline=False)
        embed.set_footer(text=f"Contact ID #{contact_id} • Copy & paste into your email client or click 1-Click Send")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="reject", description="Reject a contact.")
    @app_commands.describe(contact_id="ID of the contact to reject")
    async def reject_cmd(self, interaction: discord.Interaction, contact_id: int):
        contact = await get_contact_by_id(contact_id)
        if not contact:
            await interaction.response.send_message(f"❌ Contact with ID {contact_id} not found.", ephemeral=True)
            return

        success = await update_contact_status(contact_id, "rejected")
        if success:
            await interaction.response.send_message(
                f"🗑️ Contact **{contact['name']}** (ID: {contact_id}) at **{contact['company_name']}** has been marked as rejected.",
                ephemeral=False
            )
        else:
            await interaction.response.send_message("❌ Failed to update contact status.", ephemeral=True)

    @app_commands.command(name="draft", description="Generate or preview a personalized outreach email draft for a contact.")
    @app_commands.describe(contact_id="ID of the contact")
    async def draft_cmd(self, interaction: discord.Interaction, contact_id: int):
        await interaction.response.defer()
        contact = await get_contact_by_id(contact_id)
        if not contact:
            await interaction.followup.send(f"❌ Contact with ID {contact_id} not found.", ephemeral=True)
            return

        try:
            draft_res = await generate_outreach_draft(contact)
            subject = draft_res.get("subject", "Intern/SDE Opportunity")
            body = draft_res.get("body", "")
            mailto_url = draft_res.get("mailto_url", "")
        except Exception as e:
            await interaction.followup.send(f"⚠️ Error generating draft: {str(e)}", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"✉️ Outreach Draft for {contact['name']}",
            color=discord.Color.teal(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(name="🏢 Company", value=f"{contact['company_name']} ({contact.get('title', 'N/A')})", inline=True)
        embed.add_field(name="📬 Recipient Email", value=f"`{contact.get('email', 'N/A')}`", inline=True)
        if mailto_url:
            embed.add_field(name="⚡ 1-Click Send", value=f"[👉 Open Pre-filled Email in Mail Client]({mailto_url})", inline=False)
        embed.add_field(name="📌 Subject", value=f"**{subject}**", inline=False)
        embed.add_field(name="📝 Body", value=f"```text\n{body}\n```", inline=False)
        embed.set_footer(text=f"Contact ID #{contact_id} • Copy & paste into your email client or use 1-Click Send")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="export", description="Export all pending or approved verified contacts as a downloadable CSV list.")
    @app_commands.describe(status="Filter by status: 'pending', 'approved', or 'all' (default: 'pending')")
    async def export_cmd(self, interaction: discord.Interaction, status: str = "pending"):
        await interaction.response.defer()
        from database.db import get_db_connection
        import io
        import csv

        db = await get_db_connection()
        try:
            query = """
                SELECT c.id, c.name, c.title_normalized, c.title, c.email, c.email_verified, 
                       c.llm_confidence, c.status, comp.name as company_name, comp.domain as company_domain,
                       comp.region as company_region
                FROM contacts c
                JOIN companies comp ON c.company_id = comp.id
            """
            params = []
            if status.lower() != "all":
                query += " WHERE c.status = ?"
                params.append(status.lower())
            query += " ORDER BY c.llm_confidence DESC NULLS LAST, c.id DESC"
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
        finally:
            await db.close()

        if not rows:
            await interaction.followup.send(f"No contacts found with status '{status}'.", ephemeral=True)
            return

        # Generate CSV file in memory
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Name", "Title", "Company", "Domain", "Email", "Verified", "Calibrated Score", "Status"])
        for r in rows:
            writer.writerow([
                r["id"],
                r["name"],
                r["title_normalized"] or r["title"],
                r["company_name"],
                r["company_domain"],
                r["email"],
                "Yes" if r["email_verified"] else "No",
                f"{(r['llm_confidence']*100):.0f}%" if r["llm_confidence"] else "",
                r["status"]
            ])

        output.seek(0)
        file_data = discord.File(io.BytesIO(output.getvalue().encode("utf-8")), filename=f"contacts_{status}_{datetime.date.today()}.csv")

        embed = discord.Embed(
            title=f"📥 Contacts Export ({len(rows)} leads)",
            description=f"Exported **{len(rows)} {status} contacts** ready for your email client or outreach tool.",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(name="📊 Total Contacts", value=str(len(rows)), inline=True)
        embed.add_field(name="Filter", value=status.capitalize(), inline=True)
        await interaction.followup.send(embed=embed, file=file_data)

    @app_commands.command(name="approveall", description="Bulk approve top pending contacts and export pre-filled draft mailto links.")
    @app_commands.describe(limit="Number of top pending contacts to approve (default: 10, max: 30)")
    async def approveall_cmd(self, interaction: discord.Interaction, limit: int = 10):
        await interaction.response.defer()
        limit = min(30, max(1, limit))
        pending_list = await get_pending_contacts(limit=limit)

        if not pending_list:
            await interaction.followup.send("No pending contacts to approve.", ephemeral=True)
            return

        approved_count = 0
        summary_lines = []
        for c in pending_list:
            cid = c["id"]
            ok = await update_contact_status(cid, "approved")
            if ok:
                approved_count += 1
                draft_res = await generate_outreach_draft(c)
                mailto = draft_res.get("mailto_url", "")
                summary_lines.append(f"• **{c['name']}** ({c['company_name']}) — `{c.get('email')}` [👉 Send Email]({mailto})")

        embed = discord.Embed(
            title=f"⚡ Bulk Approved {approved_count} Contacts",
            description="\n".join(summary_lines[:15]),
            color=discord.Color.green(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        if len(summary_lines) > 15:
            embed.set_footer(text=f"...and {len(summary_lines) - 15} more approved! Run /export status:approved to download all.")
        else:
            embed.set_footer(text="Click 'Send Email' on any lead to open pre-filled outreach email.")

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReviewCog(bot))

