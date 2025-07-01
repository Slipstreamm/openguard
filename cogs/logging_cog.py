import discord
from discord.ext import commands, tasks
from discord import AllowedMentions, ui, app_commands
import datetime
import asyncio
import aiohttp  # Added for webhook sending
import logging  # Use logging instead of print
from typing import Optional, Union

# Import our JSON-based settings manager
from .logging_helpers import settings_manager

log = logging.getLogger(__name__)  # Setup logger for this cog

# Mapping for consistent event styling
EVENT_STYLES = {
    "message_edit": ("✏️", discord.Color.light_grey()),
    "message_delete": ("🗑️", discord.Color.dark_grey()),
}

# Define all possible event keys for toggling
# Keep this list updated if new loggable events are added
ALL_EVENT_KEYS = sorted(
    [
        # Direct Events
        "member_join",
        "member_remove",
        "member_ban_event",
        "member_unban",
        "member_update",
        "role_create_event",
        "role_delete_event",
        "role_update_event",
        "channel_create_event",
        "channel_delete_event",
        "channel_update_event",
        "message_edit",
        "message_delete",
        "reaction_add",
        "reaction_remove",
        "reaction_clear",
        "reaction_clear_emoji",
        "voice_state_update",
        "guild_update_event",
        "emoji_update_event",
        "invite_create_event",
        "invite_delete_event",
        "command_error",  # Potentially noisy
        "thread_create",
        "thread_delete",
        "thread_update",
        "thread_member_join",
        "thread_member_remove",
        "webhook_update",
        # Audit Log Actions (prefixed with 'audit_')
        "audit_kick",
        "audit_prune",
        "audit_ban",
        "audit_unban",
        "audit_member_role_update",
        "audit_member_update_timeout",  # Specific member_update cases
        "audit_message_delete",
        "audit_message_bulk_delete",
        "audit_role_create",
        "audit_role_delete",
        "audit_role_update",
        "audit_channel_create",
        "audit_channel_delete",
        "audit_channel_update",
        "audit_emoji_create",
        "audit_emoji_delete",
        "audit_emoji_update",
        "audit_invite_create",
        "audit_invite_delete",
        "audit_guild_update",
        # Add more audit keys if needed, e.g., "audit_stage_instance_create"
    ]
)


class LoggingCog(commands.Cog):
    """Handles comprehensive server event logging via webhooks with granular toggling."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None  # Session for webhooks
        self.last_audit_log_ids: dict[int, Optional[int]] = (
            {}
        )  # Store last ID per guild
        # Start the audit log poller task if the bot is ready, otherwise wait
        if bot.is_ready():
            asyncio.create_task(self.initialize_cog())  # Use async init helper
        else:
            asyncio.create_task(
                self.start_audit_log_poller_when_ready()
            )  # Keep this for initial start

    # Slash command group for logging
    log_group = app_commands.Group(name="log", description="Logging configuration commands.")

    class LogView(ui.LayoutView):
        """View used for logging messages."""

        def __init__(
            self,
            bot: commands.Bot,
            title: str,
            description: str,
            color: discord.Color,
            author: Optional[discord.abc.User],
            footer: Optional[str],
        ) -> None:
            super().__init__(timeout=None)
            self.container = ui.Container(accent_colour=color)
            self.add_item(self.container)

            title_display = ui.TextDisplay(f"**{title}**")
            desc_display = ui.TextDisplay(description) if description else None
            self.header_items: list[ui.TextDisplay] = [title_display]
            if desc_display:
                self.header_items.append(desc_display)

            self.header_section: Optional[ui.Section] = None
            if author is not None:
                self.header_section = ui.Section(
                    accessory=ui.Thumbnail(media=author.display_avatar.url)
                )
                for item in self.header_items:
                    self.header_section.add_item(item)
                self.container.add_item(self.header_section)
            else:
                for item in self.header_items:
                    self.container.add_item(item)
            self.container.add_item(
                ui.Separator(spacing=discord.SeparatorSpacing.small)
            )

            # Use same container to avoid nesting issues and track separator
            self.content_container = self.container
            self.bottom_separator = ui.Separator(spacing=discord.SeparatorSpacing.small)
            self.container.add_item(self.bottom_separator)

            timestamp = discord.utils.format_dt(datetime.datetime.utcnow(), style="f")
            parts = [timestamp, footer or f"Bot ID: {bot.user.id}"]
            if author:
                parts.append(f"User ID: {author.id}")
            footer_text = " | ".join(parts)
            self.footer_display = ui.TextDisplay(footer_text)
            self.container.add_item(self.footer_display)

        def add_field(self, name: str, value: str, inline: bool = False) -> None:
            field = ui.TextDisplay(f"**{name}:** {value}")
            # Ensure the field is properly registered with the view by using
            # add_item first, then repositioning it before the bottom separator
            if hasattr(self.container, "_children"):
                self.container.add_item(field)
                try:
                    children = self.container._children
                    index = children.index(self.bottom_separator)
                    children.remove(field)
                    children.insert(index, field)
                except ValueError:
                    # Fallback to default behaviour if the separator is missing
                    pass
            else:
                self.content_container.add_item(field)

        def set_author(self, user: discord.abc.User) -> None:
            """Add or update the thumbnail and append the user ID to the footer."""
            if self.header_section is None:
                self.header_section = ui.Section(
                    accessory=ui.Thumbnail(media=user.display_avatar.url)
                )
                for item in self.header_items:
                    self.container.remove_item(item)
                    self.header_section.add_item(item)
                # Insert at the beginning to keep layout consistent
                if hasattr(self.container, "children"):
                    self.container.children.insert(0, self.header_section)
                else:
                    self.container.add_item(self.header_section)
            else:
                self.header_section.accessory = ui.Thumbnail(
                    media=user.display_avatar.url
                )
            if "User ID:" not in self.footer_display.content:
                self.footer_display.content += f" | User ID: {user.id}"

        def set_footer(self, text: str) -> None:
            """Replace the footer text while preserving the timestamp."""
            timestamp = discord.utils.format_dt(datetime.datetime.utcnow(), style="f")
            self.footer_display.content = f"{timestamp} | {text}"

    def _user_display(self, user: Union[discord.Member, discord.User]) -> str:
        """Return display name, username and ID string for a user."""
        display = user.display_name if isinstance(user, discord.Member) else user.name
        username = f"{user.name}#{user.discriminator}"
        return f"{display} ({username}) [ID: {user.id}]"

    async def initialize_cog(self):
        """Asynchronous initialization tasks."""
        log.info("Initializing LoggingCog...")
        self.session = aiohttp.ClientSession()
        log.info("aiohttp ClientSession created for LoggingCog.")
        await self.initialize_audit_log_ids()
        if not self.poll_audit_log.is_running():
            self.poll_audit_log.start()
            log.info("Audit log poller started during initialization.")

    async def initialize_audit_log_ids(self):
        """Fetch the latest audit log ID for each guild the bot is in."""
        log.info("Initializing last audit log IDs for guilds...")
        for guild in self.bot.guilds:
            if (
                guild.id not in self.last_audit_log_ids
            ):  # Only initialize if not already set
                try:
                    if guild.me.guild_permissions.view_audit_log:
                        async for entry in guild.audit_logs(limit=1):
                            self.last_audit_log_ids[guild.id] = entry.id
                            log.debug(
                                f"Initialized last_audit_log_id for guild {guild.id} to {entry.id}"
                            )
                            break  # Only need the latest one
                    else:
                        log.warning(
                            f"Missing 'View Audit Log' permission in guild {guild.id}. Cannot initialize audit log ID."
                        )
                        self.last_audit_log_ids[guild.id] = (
                            None  # Mark as unable to fetch
                        )
                except discord.Forbidden:
                    log.warning(
                        f"Forbidden error fetching initial audit log ID for guild {guild.id}."
                    )
                    self.last_audit_log_ids[guild.id] = None
                except discord.HTTPException as e:
                    log.error(
                        f"HTTP error fetching initial audit log ID for guild {guild.id}: {e}"
                    )
                    self.last_audit_log_ids[guild.id] = None
                except Exception as e:
                    log.exception(
                        f"Unexpected error fetching initial audit log ID for guild {guild.id}: {e}"
                    )
                    self.last_audit_log_ids[guild.id] = (
                        None  # Mark as unable on other errors
                    )
        log.info("Finished initializing audit log IDs.")

    async def start_audit_log_poller_when_ready(self):
        """Waits until bot is ready, then initializes and starts the poller."""
        await self.bot.wait_until_ready()
        await self.initialize_cog()  # Call the main init helper

    async def cog_unload(self):
        """Clean up resources when the cog is unloaded."""
        self.poll_audit_log.cancel()
        log.info("Audit log poller stopped.")
        if self.session and not self.session.closed:
            await self.session.close()
            log.info("aiohttp ClientSession closed for LoggingCog.")

    async def _send_log_embed(self, guild: discord.Guild, embed: ui.LayoutView) -> None:
        """Sends the log view via the configured webhook for the guild."""
        if not self.session or self.session.closed:
            log.error(
                f"aiohttp session not available or closed in LoggingCog for guild {guild.id}. Cannot send log."
            )
            return

        webhook_url = await settings_manager.get_logging_webhook(guild.id)

        if not webhook_url:
            # log.debug(f"Logging webhook not configured for guild {guild.id}. Skipping log.") # Can be noisy
            return

        try:
            webhook = discord.Webhook.from_url(
                webhook_url,
                session=self.session,
                client=self.bot,
            )
            await webhook.send(
                view=embed,
                username=f"{self.bot.user.name} Logs",
                avatar_url=self.bot.user.display_avatar.url,
                allowed_mentions=AllowedMentions.none(),
            )
            # log.debug(f"Sent log embed via webhook for guild {guild.id}") # Can be noisy
        except ValueError as e:
            log.exception(
                f"ValueError sending log via webhook for guild {guild.id}. Error: {e}"
            )
        except (discord.Forbidden, discord.NotFound):
            log.error(
                f"Webhook permissions error or webhook not found for guild {guild.id}. URL: {webhook_url}"
            )
        except discord.HTTPException as e:
            log.error(f"HTTP error sending log via webhook for guild {guild.id}: {e}")
        except aiohttp.ClientError as e:
            log.error(
                f"aiohttp client error sending log via webhook for guild {guild.id}: {e}"
            )
        except Exception as e:
            log.exception(
                f"Unexpected error sending log via webhook for guild {guild.id}: {e}"
            )

    def _create_log_embed(
        self,
        title: str,
        description: str = "",
        color: discord.Color = discord.Color.blue(),
        author: Optional[Union[discord.User, discord.Member]] = None,
        footer: Optional[str] = None,
    ) -> ui.LayoutView:
        """Creates a standardized log view."""
        return self.LogView(self.bot, title, description, color, author, footer)

    def _add_id_footer(
        self,
        embed: ui.LayoutView,
        obj: Union[
            discord.Member,
            discord.User,
            discord.Role,
            discord.abc.GuildChannel,
            discord.Message,
            discord.Invite,
            None,
        ] = None,
        obj_id: Optional[int] = None,
        id_name: str = "ID",
    ) -> None:
        """Adds an ID to the footer text if possible."""
        target_id = obj_id or (obj.id if obj else None)
        if target_id:
            existing_footer = getattr(embed, "footer_display", None)
            if existing_footer:
                parts = [f"{id_name}: {target_id}"]
                link = None
                if hasattr(obj, "jump_url"):
                    link = f"[Jump]({obj.jump_url})"
                elif isinstance(obj, discord.abc.GuildChannel):
                    link = obj.mention
                if link:
                    parts.append(link)
                sep = " | " if existing_footer.content else ""
                existing_footer.content += sep + " | ".join(parts)

    async def _check_log_enabled(self, guild_id: int, event_key: str) -> bool:
        """Checks if logging is enabled for a specific event key in a guild."""
        # First, check if the webhook is configured at all
        webhook_url = await settings_manager.get_logging_webhook(guild_id)
        if not webhook_url:
            return False
        # Then, check if the specific event is enabled (defaults to True if not set)
        enabled = await settings_manager.is_log_event_enabled(
            guild_id, event_key, default_enabled=True
        )
        return enabled

    async def _is_recent_audit_log_for_target(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        target_id: int,
        max_age: float = 5.0,
    ) -> bool:
        """Return True if the latest audit log entry matches the target within ``max_age`` seconds."""
        try:
            async for entry in guild.audit_logs(limit=1, action=action):
                if (
                    entry.target.id == target_id
                    and (discord.utils.utcnow() - entry.created_at).total_seconds()
                    <= max_age
                ):
                    return True
            return False
        except discord.Forbidden:
            return True
        except Exception:
            return False

    # --- Slash Commands ---

    @log_group.command(name="channel", description="Sets the channel for logging and creates/updates the webhook.")
    @app_commands.describe(channel="The text channel to send logs to")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Sets the channel for logging and creates/updates the webhook. (Admin Only)"""
        await interaction.response.defer(thinking=True)

        guild = interaction.guild
        me = guild.me

        # 1. Check bot permissions
        if not channel.permissions_for(me).manage_webhooks:
            await interaction.followup.send(
                f"❌ I don't have the 'Manage Webhooks' permission in {channel.mention}. Please grant it and try again.",
                allowed_mentions=AllowedMentions.none(),
                ephemeral=True
            )
            return
        if not channel.permissions_for(me).send_messages:
            await interaction.followup.send(
                f"❌ I don't have the 'Send Messages' permission in {channel.mention}. Please grant it and try again (needed for webhook creation confirmation).",
                allowed_mentions=AllowedMentions.none(),
                ephemeral=True
            )
            return

        # 2. Check existing webhook setting
        existing_url = await settings_manager.get_logging_webhook(guild.id)
        if existing_url:
            # Try to fetch the existing webhook to see if it's still valid and in the right channel
            try:
                if not self.session or self.session.closed:
                    self.session = aiohttp.ClientSession()  # Ensure session exists
                existing_webhook = await discord.Webhook.from_url(
                    existing_url, session=self.session
                ).fetch()
                if existing_webhook.channel_id == channel.id:
                    await interaction.followup.send(
                        f"✅ Logging is already configured for {channel.mention} using webhook `{existing_webhook.name}`.",
                        allowed_mentions=AllowedMentions.none(),
                        ephemeral=True
                    )
                    return
                else:
                    await interaction.followup.send(
                        f"⚠️ Logging webhook is currently set for a different channel (<#{existing_webhook.channel_id}>). I will create a new one for {channel.mention}.",
                        allowed_mentions=AllowedMentions.none(),
                        ephemeral=True
                    )
            except (
                discord.NotFound,
                discord.Forbidden,
                ValueError,
                aiohttp.ClientError,
            ):
                await interaction.followup.send(
                    f"⚠️ Could not verify the existing webhook URL. It might be invalid or deleted. I will create a new one for {channel.mention}.",
                    allowed_mentions=AllowedMentions.none(),
                    ephemeral=True
                )
            except Exception as e:
                log.exception(
                    f"Error fetching existing webhook during setup for guild {guild.id}"
                )
                await interaction.followup.send(
                    f"⚠️ An error occurred while checking the existing webhook. Proceeding to create a new one for {channel.mention}.",
                    allowed_mentions=AllowedMentions.none(),
                    ephemeral=True
                )

        # 3. Create new webhook
        try:
            webhook_name = f"{self.bot.user.name} Logger"
            # Use bot's avatar if possible
            avatar_bytes = None
            try:
                avatar_bytes = await self.bot.user.display_avatar.read()
            except Exception:
                log.warning(
                    f"Could not read bot avatar for webhook creation in guild {guild.id}."
                )

            new_webhook = await channel.create_webhook(
                name=webhook_name,
                avatar=avatar_bytes,
                reason=f"Logging setup by {interaction.user} ({interaction.user.id})",
            )
            log.info(
                f"Created logging webhook '{webhook_name}' in channel {channel.id} for guild {guild.id}"
            )
        except discord.HTTPException as e:
            log.error(
                f"Failed to create webhook in {channel.mention} for guild {guild.id}: {e}"
            )
            await interaction.followup.send(
                f"❌ Failed to create webhook. Error: {e}. This could be due to hitting the channel webhook limit (15).",
                allowed_mentions=AllowedMentions.none(),
                ephemeral=True
            )
            return
        except Exception as e:
            log.exception(
                f"Unexpected error creating webhook in {channel.mention} for guild {guild.id}"
            )
            await interaction.followup.send(
                "❌ An unexpected error occurred while creating the webhook.",
                allowed_mentions=AllowedMentions.none(),
                ephemeral=True
            )
            return

        # 4. Save webhook URL
        success = await settings_manager.set_logging_webhook(guild.id, new_webhook.url)
        if success:
            await interaction.followup.send(
                f"✅ Successfully configured logging to send messages to {channel.mention} via the new webhook `{new_webhook.name}`.",
                allowed_mentions=AllowedMentions.none(),
            )
            # Test send (optional)
            try:
                test_view = self._create_log_embed(
                    "✅ Logging Setup Complete",
                    f"Logs will now be sent to this channel via the webhook `{new_webhook.name}`.",
                    color=discord.Color.green(),
                )
                await new_webhook.send(
                    view=test_view,
                    username=webhook_name,
                    avatar_url=self.bot.user.display_avatar.url,
                    allowed_mentions=AllowedMentions.none(),
                )
            except Exception as e:
                log.error(
                    f"Failed to send test message via new webhook for guild {guild.id}: {e}"
                )
                await interaction.followup.send(
                    "⚠️ Could not send a test message via the new webhook, but the URL has been saved.",
                    allowed_mentions=AllowedMentions.none(),
                    ephemeral=True
                )
        else:
            log.error(
                f"Failed to save webhook URL {new_webhook.url} to database for guild {guild.id}"
            )
            await interaction.followup.send(
                "❌ Successfully created the webhook, but failed to save its URL to my settings. Please try again or contact support.",
                allowed_mentions=AllowedMentions.none(),
                ephemeral=True
            )
            # Attempt to delete the created webhook to avoid orphans
            try:
                await new_webhook.delete(reason="Failed to save URL to settings")
                log.info(
                    f"Deleted orphaned webhook '{new_webhook.name}' for guild {guild.id}"
                )
            except Exception as del_e:
                log.error(
                    f"Failed to delete orphaned webhook '{new_webhook.name}' for guild {guild.id}: {del_e}"
                )

    @log_group.command(name="toggle", description="Toggles logging for a specific event type (on/off).")
    @app_commands.describe(
        event_key="The event key to toggle (use /log list_keys to see available keys)",
        enabled_status="Enable or disable the event (leave empty to flip current status)"
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def log_toggle(
        self,
        interaction: discord.Interaction,
        event_key: str,
        enabled_status: Optional[bool] = None,
    ):
        """Toggles logging for a specific event type (on/off).

        Use '/log list_keys' to see available event keys.
        If enabled_status is not provided, the current status will be flipped.
        """
        guild_id = interaction.guild.id
        event_key = event_key.lower()  # Ensure case-insensitivity

        if event_key not in ALL_EVENT_KEYS:
            await interaction.response.send_message(
                f"❌ Invalid event key: `{event_key}`. Use `/log list_keys` to see valid keys.",
                allowed_mentions=AllowedMentions.none(),
                ephemeral=True
            )
            return

        # Determine the new status
        if enabled_status is None:
            # Fetch current status (defaults to True if not explicitly set)
            current_status = await settings_manager.is_log_event_enabled(
                guild_id, event_key, default_enabled=True
            )
            new_status = not current_status
        else:
            new_status = enabled_status

        # Save the new status
        success = await settings_manager.set_log_event_enabled(
            guild_id, event_key, new_status
        )

        if success:
            status_str = "ENABLED" if new_status else "DISABLED"
            await interaction.response.send_message(
                f"✅ Logging for event `{event_key}` is now **{status_str}**.",
                allowed_mentions=AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                f"❌ Failed to update setting for event `{event_key}`. Please check logs or try again.",
                allowed_mentions=AllowedMentions.none(),
                ephemeral=True
            )

    @log_group.command(name="status", description="Shows the current enabled/disabled status for all loggable events.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def log_status(self, interaction: discord.Interaction):
        """Shows the current enabled/disabled status for all loggable events."""
        guild_id = interaction.guild.id
        toggles = await settings_manager.get_all_log_event_toggles(guild_id)

        lines = []
        for key in ALL_EVENT_KEYS:
            is_enabled = toggles.get(key, True)
            status_emoji = "✅" if is_enabled else "❌"
            lines.append(f"{status_emoji} `{key}`")

        description = ""
        for line in lines:
            if len(description) + len(line) + 1 > 4000:
                view = self._create_log_embed(
                    title=f"Logging Status for {interaction.guild.name}",
                    description=description.strip(),
                    color=discord.Color.blue(),
                )
                await interaction.response.send_message(view=view, allowed_mentions=AllowedMentions.none())
                description = line + "\n"
            else:
                description += line + "\n"

        if description:
            view = self._create_log_embed(
                title=f"Logging Status for {interaction.guild.name}",
                description=description.strip(),
                color=discord.Color.blue(),
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(view=view, allowed_mentions=AllowedMentions.none())
            else:
                await interaction.followup.send(view=view, allowed_mentions=AllowedMentions.none())

    @log_group.command(name="list_keys", description="Lists all valid event keys for use with the 'log toggle' command.")
    async def log_list_keys(self, interaction: discord.Interaction):
        """Lists all valid event keys for use with the 'log toggle' command."""
        keys_text = "\n".join(f"`{key}`" for key in ALL_EVENT_KEYS)

        if len(keys_text) > 4000:
            parts = []
            current_part = ""
            for key in ALL_EVENT_KEYS:
                line = f"`{key}`\n"
                if len(current_part) + len(line) > 4000:
                    parts.append(current_part)
                    current_part = line
                else:
                    current_part += line
            if current_part:
                parts.append(current_part)

            first = True
            for part in parts:
                view = self._create_log_embed(
                    title="Available Logging Event Keys" if first else "",
                    description=part.strip(),
                    color=discord.Color.purple(),
                )
                if first and not interaction.response.is_done():
                    await interaction.response.send_message(view=view, allowed_mentions=AllowedMentions.none())
                else:
                    await interaction.followup.send(view=view, allowed_mentions=AllowedMentions.none())
                first = False
        else:
            view = self._create_log_embed(
                title="Available Logging Event Keys",
                description=keys_text,
                color=discord.Color.purple(),
            )
            await interaction.response.send_message(view=view, allowed_mentions=AllowedMentions.none())

    # Simple audit log poller (placeholder)
    @tasks.loop(minutes=5)
    async def poll_audit_log(self):
        """Simple audit log poller - placeholder for now."""
        # This is a simplified version - the original had complex audit log processing
        # For now, we'll just keep it as a placeholder
        pass

    @poll_audit_log.before_loop
    async def before_poll_audit_log(self):
        await self.bot.wait_until_ready()

    # --- Event Listeners ---

    @commands.Cog.listener()
    async def on_ready(self):
        """Initialize when the cog is ready (called after bot on_ready)."""
        log.info(f"{self.__class__.__name__} cog is ready.")
        # Ensure the poller is running if it wasn't started earlier
        if self.bot.is_ready() and not self.poll_audit_log.is_running():
            log.warning(
                "Poll audit log task was not running after on_ready, attempting to start."
            )
            await self.initialize_cog()  # Re-initialize just in case

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Initialize audit log ID when joining a new guild."""
        log.info(f"Joined guild {guild.id}. Initializing audit log ID.")
        if guild.id not in self.last_audit_log_ids:
            try:
                if guild.me.guild_permissions.view_audit_log:
                    async for entry in guild.audit_logs(limit=1):
                        self.last_audit_log_ids[guild.id] = entry.id
                        log.debug(
                            f"Initialized last_audit_log_id for new guild {guild.id} to {entry.id}"
                        )
                        break
                else:
                    log.warning(
                        f"Missing 'View Audit Log' permission in new guild {guild.id}."
                    )
                    self.last_audit_log_ids[guild.id] = None
            except Exception as e:
                log.exception(
                    f"Error fetching initial audit log ID for new guild {guild.id}: {e}"
                )
                self.last_audit_log_ids[guild.id] = None

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        """Remove guild data when leaving."""
        log.info(f"Left guild {guild.id}. Removing audit log ID.")
        self.last_audit_log_ids.pop(guild.id, None)

    # --- Member Events ---
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        event_key = "member_join"
        if not await self._check_log_enabled(guild.id, event_key):
            return

        embed = self._create_log_embed(
            title="📥 Member Joined",
            description=f"{self._user_display(member)} joined the server.",
            color=discord.Color.green(),
            author=member,
        )
        embed.add_field(
            name="Account Created",
            value=discord.utils.format_dt(member.created_at, style="F"),
            inline=False,
        )
        await self._send_log_embed(member.guild, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        event_key = "member_remove"
        if not await self._check_log_enabled(guild.id, event_key):
            return

        embed = self._create_log_embed(
            title="📤 Member Left",
            description=f"{self._user_display(member)} left the server.",
            color=discord.Color.orange(),
            author=member,
        )
        self._add_id_footer(embed, member, id_name="User ID")
        await self._send_log_embed(member.guild, embed)

    @commands.Cog.listener()
    async def on_member_ban(
        self, guild: discord.Guild, user: Union[discord.User, discord.Member]
    ):
        event_key = "member_ban_event"
        if not await self._check_log_enabled(guild.id, event_key):
            return

        embed = self._create_log_embed(
            title="🔨 Member Banned (Event)",
            description=f"{self._user_display(user)} was banned.\n*Audit log may contain moderator and reason.*",
            color=discord.Color.red(),
            author=user,
        )
        self._add_id_footer(embed, user, id_name="User ID")
        await self._send_log_embed(guild, embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        event_key = "member_unban"
        if not await self._check_log_enabled(guild.id, event_key):
            return

        embed = self._create_log_embed(
            title="🔓 Member Unbanned",
            description=f"{self._user_display(user)} was unbanned.",
            color=discord.Color.blurple(),
            author=user,
        )
        self._add_id_footer(embed, user, id_name="User ID")
        await self._send_log_embed(guild, embed)

    # --- Message Events ---
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        event_key = "message_delete"
        if not await self._check_log_enabled(message.guild.id, event_key):
            return

        embed = self._create_log_embed(
            title="🗑️ Message Deleted",
            description=f"Message by {self._user_display(message.author)} deleted in {message.channel.mention}",
            color=discord.Color.dark_grey(),
            author=message.author,
        )

        if message.content:
            content = message.content
            if len(content) > 1000:
                content = content[:997] + "..."
            embed.add_field(name="Content", value=content, inline=False)

        self._add_id_footer(embed, message, id_name="Message ID")
        await self._send_log_embed(message.guild, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild or after.author.bot or before.content == after.content:
            return

        event_key = "message_edit"
        if not await self._check_log_enabled(after.guild.id, event_key):
            return

        embed = self._create_log_embed(
            title="✏️ Message Edited",
            description=f"Message by {self._user_display(after.author)} edited in {after.channel.mention}",
            color=discord.Color.light_grey(),
            author=after.author,
        )

        if before.content:
            before_content = before.content
            if len(before_content) > 500:
                before_content = before_content[:497] + "..."
            embed.add_field(name="Before", value=before_content, inline=False)

        if after.content:
            after_content = after.content
            if len(after_content) > 500:
                after_content = after_content[:497] + "..."
            embed.add_field(name="After", value=after_content, inline=False)

        self._add_id_footer(embed, after, id_name="Message ID")
        await self._send_log_embed(after.guild, embed)


async def setup(bot: commands.Bot):
    """Setup function for the cog."""
    await bot.add_cog(LoggingCog(bot))
