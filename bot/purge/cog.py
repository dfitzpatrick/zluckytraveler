import asyncio
import logging
from datetime import datetime, timedelta, timezone

from typing import Optional, Dict, List, Callable

import discord
from discord import app_commands, ui
from discord.ext import commands
import os
import re
import pathlib
from bot.pagination import LongDescriptionPaginator
import gc

SIMULATE = os.environ.get('SIMULATE', 'True').lower() == 'true'
log = logging.getLogger(__name__)


def is_owner(itx: discord.Interaction) -> bool:
    return itx.user.id == itx.guild.owner_id


def get_new_logger(name, log_file, level=logging.INFO):
    """To setup as many loggers as you want"""
    LOG_DIR = pathlib.Path(__file__).parents[1] / 'logs'
    LOG_DIR.mkdir(parents=True, exist_ok=True)


    handler = logging.FileHandler(str(LOG_DIR / log_file))
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)6s | %(message)s"))

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(handler)
    return logger


def text_timedelta(text: str) -> Optional[timedelta]:
    """Takes a text representation of time like url timestamps and converts it to a timedelta
    Format is 1w2d1h18m2s
    """
    pattern = '^(?:(?P<weeks>\d+)[w.])?(?:(?P<days>\d+)[d.])?(?:(?P<hours>\d+)[h.])?(?:(?P<minutes>\d+)[m.])?(?:(?P<seconds>\d+)[s.])?$'
    pattern = re.compile(pattern)
    matches = re.search(pattern, text)
    if matches is None:
        raise InvalidTimeStringException("Input must be in the format of 1w2d3h4m5s")
    args = {k: int(v) for k, v in matches.groupdict().items() if v and v.isdigit()}
    return timedelta(**args)


class InvalidTimeStringException(ValueError):
    pass


class PurgeSession:

    def __init__(self, interaction: discord.Interaction, callback: Callable, members: List[discord.Member], reason: str, simulate=SIMULATE):
        self.interaction = interaction
        self.callback = callback
        self.members = members
        self.reason = reason
        self.task: Optional[asyncio.Task] = None
        self.kicked_members: List[discord.Member] = []
        self._log = None

        self.simulate = simulate
        self.mode = "Simulated" if self.simulate else ""
        self._timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H-%M-%S-%p-Z")
        self._name = f"session_{self._timestamp}"


    @property
    def log(self):
        if self._log is None:
            self._log = get_new_logger(self._name, f"{self._name}.log", logging.INFO)
        return self._log

    def release_resources(self):
        for handler in self.log.handlers:
            handler.close()
        self._log = None
        self.task = None
        self.interaction = None
        gc.collect()

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
        guild_id = self.interaction.guild_id
        self.log.info(f"Starting {self.mode.lower()} kick session in {guild_name} (id: {guild_id})")
        self.task = asyncio.create_task(self._kick_members())
        self.task.add_done_callback(self.task_callback)

    def stop(self, release=False):
        if self.task:
            self.task.cancel()
        if release:
            self.release_resources()

    async def _kick_members(self):
        for m in self.members:
            g = m.guild
            self.log.info(f"{self.mode} Kicked Guild: {g.name} (id: {g.id}) / Member: {m.display_name} (id: {m.id})")
            if not SIMULATE:
                await m.kick(reason=self.reason)
            self.kicked_members.append(m)


class ConfirmView(ui.View):
    message: discord.Message

    def __init__(self, session: PurgeSession, cog: 'Purge', **kwargs):
        super(ConfirmView, self).__init__(**kwargs)
        self.session = session
        self.cog = cog

    @ui.button(label="Start", style=discord.ButtonStyle.red)
    async def start_kicking(self, itx: discord.Interaction, button: ui.Button):
        self.session.start()
        self.cog.sessions[itx.guild_id] = self.session
        await itx.response.defer()
        await self.message.delete()

    async def destroy(self):
        if self.message:
            try:
                await self.message.delete()
                self.session = None
                self.stop()
                gc.collect()
            except (discord.HTTPException, discord.NotFound):
                log.debug("Message already dismissed")

class Purge(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: Dict[int, PurgeSession] = {}
        self.confirm_views: Dict[int, ConfirmView] = {}

    @app_commands.command(name='start-kicking', description='Kicks all members based on time or roles')
    @app_commands.check(is_owner)
    @app_commands.checks.bot_has_permissions(kick_members=True)
    @app_commands.describe(joined_after="Kicks members that have been in the guild longer than this time. Specified in a 1w2d3h4m5s format. Default 24 hours.")
    @app_commands.describe(role='Kicks all users that have a specific role. Defaults to users without roles.')
    async def start_cmd(self, itx: discord.Interaction, joined_after: str = '24h', role: Optional[discord.Role] = None):
        joined_after = text_timedelta(joined_after)
        if role is not None:
            log.debug(f"Role is default: {role.is_default()}")
        time_requirement = datetime.now(timezone.utc) - joined_after
        if role is None or role.is_default():
            role = None

        def predicates(member: discord.Member):
            log.debug(f"{member.display_name} {member.joined_at}")
            log.debug(time_requirement)
            return all([
                len(member.roles) == 1 if role is None else role in member.roles,
                member.joined_at < time_requirement,
                member.id != member.guild.owner_id,
                member.id != self.bot.user.id,
            ])
        audit_log_reason = '/start-kicking command was used'
        await itx.response.defer(ephemeral=True, thinking=True)
        key = itx.guild_id
        existing_session = self.sessions.get(key)
        if existing_session:
            await itx.followup.send("This is already running. Use /stop to stop progress.", ephemeral=True)
            return

        members = [m for m in itx.guild.members if predicates(m)]
        session = PurgeSession(interaction=itx, members=members, reason=audit_log_reason, callback=self.on_purge_completion)
        role_name = role.name if role is not None else "No Roles Assigned"
        config_embed = discord.Embed(title="Settings", description=f"Calculated {session.mode} Kicking {len(members)} members from the Guild. Click Start to continue.")
        config_embed.add_field(name="Joined After", value=joined_after)
        config_embed.add_field(name="Role", value=role_name)
        embed = self.member_list_embed(members, status=f"This will affect {len(members)} member(s).")
        old_view = self.confirm_views.get(itx.guild_id)
        if old_view is not None:
            await old_view.destroy()
        view = ConfirmView(session, self)
        self.confirm_views[itx.guild_id] = view

        await itx.followup.send(embeds=[config_embed, embed], view=view, ephemeral=True)
        view.message = await itx.original_response()

    @app_commands.command(name='stop-kicking')
    @app_commands.check(is_owner)
    async def stop_cmd(self, itx: discord.Interaction):
        await itx.response.defer()
        key = itx.guild_id
        existing_session = self.sessions.get(key)
        if not existing_session:
            await itx.followup.send("There is no current kick running.", ephemeral=True)
            return
        log.info(f"Stopping Kick in {itx.guild.name}")
        existing_session.stop(release=True)
        del self.sessions[existing_session.interaction.guild_id]
        await itx.followup.send(f"Cancelling {existing_session.mode} Kick Session", ephemeral=True)

    @start_cmd.error
    @stop_cmd.error
    async def error_handler(self, itx: discord.Interaction, error):
        if isinstance(error.original, InvalidTimeStringException):
            await itx.response.send_message("joined_after must be in the format of 1w2d3h4m5s", ephemeral=True)
            return
        if isinstance(error, app_commands.errors.BotMissingPermissions):
            await itx.response.send_message("The bot requires kick permissions to use this command.", ephemeral=True)
        if isinstance(error, app_commands.CheckFailure):
            await itx.response.send_message("Only the Guild Owner may use this command.", ephemeral=True)
        else:
            raise error

    def member_list_embed(self, members: List[discord.Member], status: str, description_header: str = ''):
        MAX_SIZE = 4000
        members = '\n'.join([f"{i + 1}. {m.display_name}" for i, m in enumerate(members)])
        description = f"{description_header} \n\n {members}"
        if len(description) > MAX_SIZE:
            description = description[:MAX_SIZE-min(20, MAX_SIZE)] + "\n... (truncated)"
        embed = discord.Embed(title=status, description=description)
        return embed

    async def respond_safe_embed(self, itx: discord.Interaction, embed: discord.Embed, ephemeral: bool = False, view: Optional[discord.ui.View] = None):
        await itx.response.defer()
        try:
            if len(embed.description) > 4000:
                content = "The result is too large to display in one embed. Please click the Start Button to allow pagination."
                await itx.response.send_message(
                    content=content,
                    view=await LongDescriptionPaginator(itx.client, itx.user, embed.title, embed.description, 2000).run(),
                    ephemeral=ephemeral
                )
            else:
                await itx.followup.send(embed=embed, ephemeral=ephemeral, view=None)
        except (discord.HTTPException, discord.NotFound):
            author = itx.user
            await author.send(embed=embed)

    async def on_purge_completion(self, session: PurgeSession, cancelled: bool):
        del self.sessions[session.interaction.guild_id]
        condition = "was Cancelled" if cancelled else "Completed Successfully"
        status = f"{session.mode} Kick Session {condition}"
        session.log.info(status)
        embed = self.member_list_embed(session.kicked_members, status, description_header=f"Kicked {len(session.kicked_members)} members")
        await session.interaction.followup.send(embed=embed)
        session.release_resources()




async def setup(bot):
    await bot.add_cog(Purge(bot))
