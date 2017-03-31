from discord.ext import commands
from .utils import config

from collections import Counter

import discord
import re

BLOB_GUILD_ID = '272885620769161216'
EMOJI_REGEX = re.compile(r'<:.+?:([0-9]{15,21})>')

class BlobEmoji(commands.Converter):
    def convert(self):
        guild = self.ctx.bot.get_server(BLOB_GUILD_ID)
        emojis = {e.id: e for e in guild.emojis}

        m = EMOJI_REGEX.match(self.argument)
        if m is not None:
            emoji = emojis.get(m.group(1))
        elif self.argument.isdigit():
            emoji = emojis.get(self.argument)
        else:
            emoji = discord.utils.find(emojis.values(), lambda e: e.name == self.argument)

        if emoji is None:
            raise commands.BadArgument('Not a valid blob emoji.')
        return emoji

class Emoji:
    """Custom emoji tracking statistics for Wolfiri"""

    def __init__(self, bot):
        self.bot = bot

        # guild_id: data
        # where data is
        # emoji_id: count
        self.config = config.Config('emoji_statistics.json')

    async def on_message(self, message):
        if message.server is None:
            return

        matches = EMOJI_REGEX.findall(message.content)
        if not matches:
            return

        db = self.config.get(message.server.id, {})
        for emoji_id in matches:
            try:
                count = db[emoji_id]
            except KeyError:
                db[emoji_id] = 1
            else:
                db[emoji_id] = count + 1

        await self.config.put(message.server.id, db)

    def get_all_blob_stats(self):
        blob_guild = self.bot.get_server(BLOB_GUILD_ID)
        blob_ids = {e.id: e for e in blob_guild.emojis}
        total_usage = Counter()
        for data in self.config.all().values():
            total_usage.update(data)

        blob_usage = Counter({e: 0 for e in blob_ids})
        blob_usage.update(x for x in total_usage.elements() if x in blob_ids)

        e = discord.Embed(title='Blob Statistics', colour=0xf1c40f)

        common = blob_usage.most_common()
        total_blob_usage = sum(blob_usage.values())
        total_global_usage = sum(total_usage.values())
        e.add_field(name='Total Usage', value=total_blob_usage)
        e.add_field(name='Percentage', value=format(total_blob_usage / total_global_usage, '.2%'))

        def elem_to_string(key, count):
            return '{0}: {1} times ({2:.2%})'.format(blob_ids.get(key), count, count / total_blob_usage)

        top = [elem_to_string(key, count) for key, count in common[0:7]]
        bottom = [elem_to_string(key, count) for key, count in common[-7:]]
        e.add_field(name='Most Common', value='\n'.join(top), inline=False)
        e.add_field(name='Least Common', value='\n'.join(bottom), inline=False)
        return e

    def get_blob_stats_for(self, emoji):
        blob_guild = self.bot.get_server(BLOB_GUILD_ID)
        blob_ids = {e.id: e for e in blob_guild.emojis}

        e = discord.Embed(colour=0xf1c40f, title='Statistics')
        total_usage = Counter()
        for data in self.config.all().values():
            total_usage.update(data)

        blob_usage = Counter({e: 0 for e in blob_ids})
        blob_usage.update(x for x in total_usage.elements() if x in blob_ids)
        usage = blob_usage.get(emoji.id)
        total = sum(blob_usage.values())

        rank = None
        for (index, (x, _)) in enumerate(blob_usage.most_common()):
            if x == emoji.id:
                rank = index + 1
                break

        e.description = str(emoji)
        e.add_field(name='Usage', value=usage)
        e.add_field(name='Rank', value=rank)
        e.add_field(name='Percentage', value=format(usage / total, '.2%'))
        return e

    @commands.command(hidden=True)
    async def blobstats(self, *, emoji: BlobEmoji = None):
        """Usage statistics of blobs."""
        if emoji is None:
            e = self.get_all_blob_stats()
        else:
            e = self.get_blob_stats_for(emoji)

        await self.bot.say(embed=e)

    @blobstats.error
    async def blobstats_error(self, error, ctx):
        if isinstance(error, commands.BadArgument):
            await self.bot.say(str(e))

def setup(bot):
    bot.add_cog(Emoji(bot))
