from discord.ext import commands
from .utils import fuzzy, time
import asyncio
import discord
import re
import zlib
import io
import os
import lxml.etree as etree

DISCORD_PY_GUILD_ID = 336642139381301249
ROBODANNY_ID = 80528701850124288

class SphinxObjectFileReader:
    # Inspired by Sphinx's InventoryFileReader
    BUFSIZE = 16 * 1024

    def __init__(self, buffer):
        self.stream = io.BytesIO(buffer)

    def readline(self):
        return self.stream.readline().decode('utf-8')

    def skipline(self):
        self.stream.readline()

    def read_compressed_chunks(self):
        decompressor = zlib.decompressobj()
        while True:
            chunk = self.stream.read(self.BUFSIZE)
            if len(chunk) == 0:
                break
            yield decompressor.decompress(chunk)
        yield decompressor.flush()

    def read_compressed_lines(self):
        buf = b''
        for chunk in self.read_compressed_chunks():
            buf += chunk
            pos = buf.find(b'\n')
            while pos != -1:
                yield buf[:pos].decode('utf-8')
                buf = buf[pos + 1:]
                pos = buf.find(b'\n')

class BotUser(commands.Converter):
    async def convert(self, ctx, argument):
        if not argument.isdigit():
            raise commands.BadArgument('Not a valid bot user ID.')
        try:
            user = await ctx.bot.fetch_user(argument)
        except discord.NotFound:
            raise commands.BadArgument('Bot user not found (404).')
        except discord.HTTPException as e:
            raise commands.BadArgument(f'Error fetching bot user: {e}')
        else:
            if not user.bot:
                raise commands.BadArgument('This is not a bot.')
            return user

class API(commands.Cog):
    """Discord API exclusive things."""

    def __init__(self, bot):
        self.bot = bot
        self.issue = re.compile(r'##(?P<number>[0-9]+)')
        self._recently_blocked = set()

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild or message.guild.id != DISCORD_PY_GUILD_ID:
            return

        # this bot is only a backup for when R. Danny is offline
        robodanny = message.guild.get_member(ROBODANNY_ID)
        if robodanny and robodanny.status is not discord.Status.offline:
            return

        m = self.issue.search(message.content)
        if m is not None:
            url = 'https://github.com/Rapptz/discord.py/issues/'
            await message.channel.send(url + m.group('number'))

    def parse_object_inv(self, stream, url):
        # key: URL
        # n.b.: key doesn't have `discord` or `discord.ext.commands` namespaces
        result = {}

        # first line is version info
        inv_version = stream.readline().rstrip()

        if inv_version != '# Sphinx inventory version 2':
            raise RuntimeError('Invalid objects.inv file version.')

        # next line is "# Project: <name>"
        # then after that is "# Version: <version>"
        projname = stream.readline().rstrip()[11:]
        version = stream.readline().rstrip()[11:]

        # next line says if it's a zlib header
        line = stream.readline()
        if 'zlib' not in line:
            raise RuntimeError('Invalid objects.inv file, not z-lib compatible.')

        # This code mostly comes from the Sphinx repository.
        entry_regex = re.compile(r'(?x)(.+?)\s+(\S*:\S*)\s+(-?\d+)\s+(\S+)\s+(.*)')
        for line in stream.read_compressed_lines():
            match = entry_regex.match(line.rstrip())
            if not match:
                continue

            name, directive, prio, location, dispname = match.groups()
            domain, _, subdirective = directive.partition(':')
            if directive == 'py:module' and name in result:
                # From the Sphinx Repository:
                # due to a bug in 1.1 and below,
                # two inventory entries are created
                # for Python modules, and the first
                # one is correct
                continue

            # Most documentation pages have a label
            if directive == 'std:doc':
                subdirective = 'label'

            if location.endswith('$'):
                location = location[:-1] + name

            key = name if dispname == '-' else dispname
            prefix = f'{subdirective}:' if domain == 'std' else ''

            if projname == 'discord.py':
                key = key.replace('discord.ext.commands.', '').replace('discord.', '')

            result[f'{prefix}{key}'] = os.path.join(url, location)

        return result

    async def build_rtfm_lookup_table(self, page_types):
        cache = {}
        for key, page in page_types.items():
            sub = cache[key] = {}
            async with self.bot.session.get(page + '/objects.inv') as resp:
                if resp.status != 200:
                    raise RuntimeError('Cannot build rtfm lookup table, try again later.')

                stream = SphinxObjectFileReader(await resp.read())
                cache[key] = self.parse_object_inv(stream, page)

        self._rtfm_cache = cache

    async def do_rtfm(self, ctx, key, obj):
        page_types = {
            'latest': 'https://discordpy.readthedocs.io/en/latest',
            'latest-jp': 'https://discordpy.readthedocs.io/ja/latest',
            'python': 'https://docs.python.org/3',
            'python-jp': 'https://docs.python.org/ja/3',
        }

        if obj is None:
            await ctx.send(page_types[key])
            return

        if not hasattr(self, '_rtfm_cache'):
            await ctx.trigger_typing()
            await self.build_rtfm_lookup_table(page_types)

        obj = re.sub(r'^(?:discord\.(?:ext\.)?)?(?:commands\.)?(.+)', r'\1', obj)

        if key.startswith('latest'):
            # point the abc.Messageable types properly:
            q = obj.lower()
            for name in dir(discord.abc.Messageable):
                if name[0] == '_':
                    continue
                if q == name:
                    obj = f'abc.Messageable.{name}'
                    break

        cache = list(self._rtfm_cache[key].items())
        def transform(tup):
            return tup[0]

        matches = fuzzy.finder(obj, cache, key=lambda t: t[0], lazy=False)[:8]

        e = discord.Embed(colour=discord.Colour.blurple())
        if len(matches) == 0:
            return await ctx.send('Could not find anything. Sorry.')

        e.description = '\n'.join(f'[`{key}`]({url})' for key, url in matches)
        await ctx.send(embed=e)

    @commands.group(aliases=['rtfd'], invoke_without_command=True)
    async def rtfm(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a discord.py entity.

        Events, objects, and functions are all supported through a
        a cruddy fuzzy algorithm.
        """
        await self.do_rtfm(ctx, 'latest', obj)

    @rtfm.command(name='jp')
    async def rtfm_jp(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a discord.py entity (Japanese)."""
        await self.do_rtfm(ctx, 'latest-jp', obj)

    @rtfm.command(name='python', aliases=['py'])
    async def rtfm_python(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a Python entity."""
        await self.do_rtfm(ctx, 'python', obj)

    async def _member_stats(self, ctx, member, total_uses):
        e = discord.Embed(title='RTFM Stats')
        e.set_author(name=str(member), icon_url=member.avatar_url)

        query = 'SELECT count FROM rtfm WHERE user_id=$1;'
        record = await ctx.db.fetchrow(query, member.id)

        if record is None:
            count = 0
        else:
            count = record['count']

        e.add_field(name='Uses', value=count)
        e.add_field(name='Percentage', value=f'{count/total_uses:.2%} out of {total_uses}')
        e.colour = discord.Colour.blurple()
        await ctx.send(embed=e)

    @rtfm.command()
    async def stats(self, ctx, *, member: discord.Member = None):
        """Tells you stats about the ?rtfm command."""
        query = 'SELECT SUM(count) AS total_uses FROM rtfm;'
        record = await ctx.db.fetchrow(query)
        total_uses = record['total_uses']

        if member is not None:
            return await self._member_stats(ctx, member, total_uses)

        query = 'SELECT user_id, count FROM rtfm ORDER BY count DESC LIMIT 10;'
        records = await ctx.db.fetch(query)

        output = []
        output.append(f'**Total uses**: {total_uses}')

        # first we get the most used users
        if records:
            output.append(f'**Top {len(records)} users**:')

            for rank, (user_id, count) in enumerate(records, 1):
                user = self.bot.get_user(user_id)
                if rank != 10:
                    output.append(f'{rank}\u20e3 {user}: {count}')
                else:
                    output.append(f'\N{KEYCAP TEN} {user}: {count}')

        await ctx.send('\n'.join(output))

    def library_name(self, channel):
        # language_<name>
        name = channel.name
        index = name.find('_')
        if index != -1:
            name = name[index + 1:]
        return name.replace('-', '.')

    @commands.command()
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def tempblock(self, ctx, duration: time.FutureTime, *, member: discord.Member):
        """Temporarily blocks a user from your channel.

        The duration can be a a short time form, e.g. 30d or a more human
        duration such as "until thursday at 3PM" or a more concrete time
        such as "2017-12-31".

        Note that times are in UTC.
        """

        reminder = self.bot.get_cog('Reminder')
        if reminder is None:
            return await ctx.send('Sorry, this functionality is currently unavailable. Try again later?')

        timer = await reminder.create_timer(duration.dt, 'tempblock', ctx.guild.id, ctx.author.id,
                                                                      ctx.channel.id, member.id,
                                                                      connection=ctx.db,
                                                                      created=ctx.message.created_at)

        reason = f'Tempblock by {ctx.author} (ID: {ctx.author.id}) until {duration.dt}'

        await ctx.channel.set_permissions(member, send_messages=False, reason=reason)
        await ctx.send(f'Blocked {member} for {time.human_timedelta(duration.dt, source=timer.created_at)}.')

    @commands.Cog.listener()
    async def on_tempblock_timer_complete(self, timer):
        guild_id, mod_id, channel_id, member_id = timer.args

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            # RIP
            return

        channel = guild.get_channel(channel_id)
        if channel is None:
            # RIP x2
            return

        to_unblock = guild.get_member(member_id)
        if to_unblock is None:
            # RIP x3
            return

        moderator = guild.get_member(mod_id)
        if moderator is None:
            try:
                moderator = await self.bot.fetch_user(mod_id)
            except:
                # request failed somehow
                moderator = f'Mod ID {mod_id}'
            else:
                moderator = f'{moderator} (ID: {mod_id})'
        else:
            moderator = f'{moderator} (ID: {mod_id})'


        reason = f'Automatic unblock from timer made on {timer.created_at} by {moderator}.'

        try:
            await channel.set_permissions(to_unblock, send_messages=None, reason=reason)
        except:
            pass

    @rtfm.command(name='py-jp', aliases=['py-ja'])
    async def rtfm_python_jp(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a Python entity (Japanese)."""
        await self.do_rtfm(ctx, 'python-jp', obj)

    async def refresh_faq_cache(self):
        self.faq_entries = {}
        base_url = 'https://discordpy.readthedocs.io/en/latest/faq.html'
        async with self.bot.session.get(base_url) as resp:
            text = await resp.text(encoding='utf-8')

            root = etree.fromstring(text, etree.HTMLParser())
            nodes = root.findall(".//div[@id='questions']/ul[@class='simple']/li/ul//a")
            for node in nodes:
                self.faq_entries[''.join(node.itertext()).strip()] = base_url + node.get('href').strip()

    @commands.command()
    async def faq(self, ctx, *, query: str = None):
        """Shows an FAQ entry from the discord.py documentation"""
        if not hasattr(self, 'faq_entries'):
            await self.refresh_faq_cache()

        if query is None:
            return await ctx.send(f'https://discordpy.readthedocs.io/en/{branch}/faq.html')

        matches = fuzzy.extract_matches(query, self._faq_cache[branch], scorer=fuzzy.partial_ratio, score_cutoff=40)
        if len(matches) == 0:
            return await ctx.send('Nothing found…')

        fmt = '\n'.join(f'**{key}**\n{value}' for key, _, value in matches)
        await ctx.send(fmt)

def setup(bot):
    bot.add_cog(API(bot))
