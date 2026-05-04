"""Tests for compact module."""

import unittest
from src.compact import (
    compact_messages, should_compact, compact,
    CompactionStrategy, AUTOCOMPACT_BUFFER_TOKENS,
)


class TestCompact(unittest.TestCase):
    def test_autocompact_buffer_tokens_exists(self):
        self.assertEqual(AUTOCOMPACT_BUFFER_TOKENS, 150000)

    def test_compact_messages_short_list(self):
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
        ]
        result = compact_messages(messages)
        self.assertEqual(len(result), 2)

    def test_compact_messages_reduces_long_list(self):
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "tool", "content": "result"},
            {"role": "user", "content": "Again"},
            {"role": "assistant", "content": "Again too"},
        ]
        result = compact_messages(messages)
        self.assertLess(len(result), len(messages))

    def test_should_compact(self):
        messages = [{"content": "x" * 10000}] * 20
        # Even with some content, should_compact checks against threshold
        # 20 * 10000 chars / 4 = 50000 tokens, still below 150000
        self.assertFalse(should_compact(messages, threshold=150000))


if __name__ == "__main__":
    unittest.main()