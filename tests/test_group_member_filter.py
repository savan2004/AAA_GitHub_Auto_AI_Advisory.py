import unittest
from types import SimpleNamespace
from group_member_filter import is_member_message


def msg(**overrides):
    values = dict(chat=SimpleNamespace(type="supergroup"),
                  from_user=SimpleNamespace(id=123, is_bot=False),
                  sender_chat=None, is_automatic_forward=False,
                  forward_from_chat=None, forward_origin=None)
    values.update(overrides)
    return SimpleNamespace(**values)


class MemberFilterTests(unittest.TestCase):
    def test_real_member(self):
        self.assertTrue(is_member_message(msg()))

    def test_automatic_channel_forward(self):
        self.assertFalse(is_member_message(msg(is_automatic_forward=True)))

    def test_channel_sender(self):
        self.assertFalse(is_member_message(msg(sender_chat=SimpleNamespace(id=-100))))

    def test_anonymous_admin(self):
        self.assertFalse(is_member_message(msg(sender_chat=SimpleNamespace(type="supergroup"))))

    def test_other_bot(self):
        self.assertFalse(is_member_message(msg(from_user=SimpleNamespace(id=1, is_bot=True))))

    def test_forwarded_channel(self):
        self.assertFalse(is_member_message(msg(forward_origin=SimpleNamespace(type="channel"))))

    def test_private_chat(self):
        self.assertFalse(is_member_message(msg(chat=SimpleNamespace(type="private"))))

    def test_missing_sender(self):
        self.assertFalse(is_member_message(msg(from_user=None)))


if __name__ == "__main__":
    unittest.main()
