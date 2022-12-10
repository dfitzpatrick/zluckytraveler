import asyncio
import logging
from typing import Optional, Dict, List, Callable

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)


def is_owner(itx: discord.Interaction) -> bool:
    return itx.user.id == itx.guild.owner_id


class PurgeSession:

    def __init__(self, interaction: discord.Interaction, callback: Callable, members: List[discord.Member], reason: str):
        self.interaction = interaction
        self.callback = callback
        self.members = members
        self.reason = reason
        self.task: Optional[asyncio.Task] = None
        self.kicked_members: List[discord.Member] = []

    def task_callback(self, future: asyncio.Future):
        cancelled = future.cancelled()
        # destroy weak reference
        self.task = None
        if not cancelled and future.exception():
            raise future.exception()
        asyncio.create_task(self.callback(self, cancelled))

    @property
    def remaining_members(self):
        return set(self.members) - set(self.kicked_members)

    def start(self):
        guild_name = self.interaction.guild.name
        log.info(f"Starting kick session in {guild_name}")
        self.task = asyncio.create_task(self._kick_members())
        self.task.add_done_callback(self.task_callback)

    def stop(self):
        if self.task:
            self.task.cancel()

    async def _kick_members(self):
        for m in self.members:
            g = m.guild
            log.info(f"Kicked Guild: {g.name} (id: {g.id}) / Member: {m.display_name} (id: {m.id})")
            await m.kick(reason=self.reason)
            self.kicked_members.append(m)


class Purge(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: Dict[int, PurgeSession] = {}

    @app_commands.command(name='start')
    @app_commands.check(is_owner)
    @app_commands.checks.bot_has_permissions(kick_members=True)
    async def start_cmd(self, itx: discord.Interaction, *, reason: str):
        await itx.response.defer(ephemeral=True)
        key = itx.guild_id
        existing_session = self.sessions.get(key)
        if existing_session:
            await itx.followup.send("This is already running. Use /stop to stop progress.", ephemeral=True)
            return
        # everyone role is always present
        members = [m for m in itx.guild.members if len(m.roles) == 1 and not m.id == m.guild.owner_id]
        session = PurgeSession(interaction=itx, members=members, reason=reason, callback=self.on_purge_completion)
        self.sessions[key] = session
        session.start()
        await itx.followup.send(f"Kicking {len(members)} members from the Guild. Type /stop to stop.", ephemeral=True)

    @app_commands.command(name='stop')
    @app_commands.check(is_owner)
    async def stop_cmd(self, itx: discord.Interaction):
        await itx.response.defer()
        key = itx.guild_id
        existing_session = self.sessions.get(key)
        if not existing_session:
            await itx.followup.send("There is no current kick running.", ephemeral=True)
            return
        log.info(f"Stopping Kick in {itx.guild.name}")
        existing_session.stop()
        await itx.followup.send("Cancelling Kick Session", ephemeral=True)

    @start_cmd.error
    @stop_cmd.error
    async def error_handler(self, itx: discord.Interaction, error):
        if isinstance(error, app_commands.errors.BotMissingPermissions):
            await itx.response.send_message("The bot requires kick permissions to use this command.", ephemeral=True)
        if isinstance(error, app_commands.CheckFailure):
            await itx.response.send_message("Only the Guild Owner may use this command.", ephemeral=True)
        else:
            raise error

    async def on_purge_completion(self, session: PurgeSession, cancelled: bool):
        del self.sessions[session.interaction.guild_id]
        condition = "was Cancelled" if cancelled else "Completed Successfully"
        kicked_member_names = ', '.join([m.display_name for m in session.kicked_members])
        description = f"""{len(session.kicked_members)} were removed\n\n {kicked_member_names}"""
        embed = discord.Embed(title=f"Kick Session {condition}", description=description)
        try:
            await session.interaction.followup.send(embed=embed, ephemeral=True)
        except (discord.HTTPException, discord.NotFound):
            author = session.interaction.user
            await author.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Purge(bot))
