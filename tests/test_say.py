"""Checks /character say: the webhook is looked up once per channel, the message posts
without waiting, mentions are limited, and no "Sent." reply is left behind.

Run all tests from the repo root with:  python -m unittest discover tests
"""
import asyncio
import os
import sys
import unittest
import warnings
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
warnings.simplefilter("ignore")

import discord  # noqa: E402

import cogs.character as char_cog  # noqa: E402


def make_channel(channel_id=1):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    hook = MagicMock()
    hook.name = char_cog.WEBHOOK_NAME
    hook.send = AsyncMock()
    channel.webhooks = AsyncMock(return_value=[hook])
    channel.create_webhook = AsyncMock()
    return channel, hook


def make_interaction(channel):
    interaction = MagicMock()
    interaction.channel = channel
    interaction.user.id = 42
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.delete_original_response = AsyncMock()
    return interaction


class SayTests(unittest.TestCase):
    def setUp(self):
        char_cog._WEBHOOK_CACHE.clear()
        self.character = SimpleNamespace(name="Tester", owner_id=42, avatar_url=None)
        self.cog = char_cog.CharacterCog(MagicMock())
        patcher = patch.object(char_cog, "load_character", return_value=self.character)
        patcher.start()
        self.addCleanup(patcher.stop)

    def say(self, interaction, text="hello"):
        asyncio.run(self.cog.say.callback(self.cog, interaction, "Tester", text))

    def test_webhook_is_looked_up_once_per_channel(self):
        channel, hook = make_channel()
        self.say(make_interaction(channel))
        self.say(make_interaction(channel))
        self.assertEqual(channel.webhooks.await_count, 1)
        self.assertEqual(hook.send.await_count, 2)

    def test_posts_without_waiting_and_limits_mentions(self):
        channel, hook = make_channel()
        self.say(make_interaction(channel), "@everyone hi")
        kwargs = hook.send.await_args.kwargs
        self.assertIs(kwargs["wait"], False)
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertFalse(kwargs["allowed_mentions"].roles)

    def test_no_sent_reply_is_left_behind(self):
        channel, _ = make_channel()
        interaction = make_interaction(channel)
        self.say(interaction)
        interaction.followup.send.assert_not_awaited()
        interaction.delete_original_response.assert_awaited_once()

    def test_deleted_webhook_is_looked_up_again(self):
        channel, hook = make_channel()
        self.say(make_interaction(channel))
        gone = discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Webhook")
        hook.send = AsyncMock(side_effect=[gone, None])
        interaction = make_interaction(channel)
        self.say(interaction)
        self.assertEqual(channel.webhooks.await_count, 2)
        self.assertEqual(hook.send.await_count, 2)
        interaction.followup.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
