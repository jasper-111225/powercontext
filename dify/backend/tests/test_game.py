from __future__ import annotations

import unittest

from backend.game import (
    _core_text,
    _rank,
    build_context,
    clean_message,
    normalize_retell,
    resolve_npc,
)
from backend.personas import NPC_BY_ID


class ResolveNpcTest(unittest.TestCase):
    def test_alias(self) -> None:
        self.assertEqual(resolve_npc("阿烛，你听说了吗？"), "azhu")
        self.assertEqual(resolve_npc("老板娘，结账"), "rosa")
        self.assertEqual(resolve_npc("酒保，再来一杯"), "momo")

    def test_longest_alias_wins_when_same_position(self) -> None:
        # “老板娘”与“老板”同起点，应选更长的那个，仍然指向 rosa
        self.assertEqual(resolve_npc("老板娘，你说呢"), "rosa")

    def test_fallback_to_last_npc(self) -> None:
        self.assertEqual(resolve_npc("然后呢？", "momo"), "momo")
        self.assertEqual(resolve_npc("然后呢？", ""), "rosa")
        self.assertEqual(resolve_npc("然后呢？", "unknown"), "rosa")

    def test_earliest_mention_wins(self) -> None:
        self.assertEqual(resolve_npc("老默，罗莎刚才说什么？"), "momo")


class CleanMessageTest(unittest.TestCase):
    def test_strip_address(self) -> None:
        self.assertEqual(clean_message("罗莎：今晚有酒吗"), "今晚有酒吗")
        self.assertEqual(clean_message("阿烛, 你听说了吗"), "你听说了吗")

    def test_strip_secret_lead(self) -> None:
        text = "罗莎，我告诉你一个秘密：城主的金库其实早就空了。"
        self.assertEqual(clean_message(text), "城主的金库其实早就空了。")

    def test_keep_plain_text(self) -> None:
        self.assertEqual(clean_message("城主的金库空了"), "城主的金库空了")


class RetellTest(unittest.TestCase):
    def test_empty_retell_uses_core_once(self) -> None:
        heard = {"text": "金库空了。", "is_secret": True}
        first = normalize_retell("", npc_id="rosa", heard=heard)
        self.assertIn("金库空了", first)
        # 第二代不应把第一代的尾巴再抄一遍
        second = normalize_retell("", npc_id="rosa", heard={"text": first, "is_secret": False})
        self.assertEqual(_core_text(second), _core_text(first))

    def test_identical_retell_gets_style_marker(self) -> None:
        heard = {"text": "金库空了。", "is_secret": False}
        text = normalize_retell("金库空了。", npc_id="azhu", heard=heard)
        self.assertNotEqual(text, "金库空了。")
        self.assertLessEqual(len(text), 220)

    def test_too_long_is_cut(self) -> None:
        text = normalize_retell("啊" * 500, npc_id="momo", heard=None)
        self.assertLessEqual(len(text), 220)


class RankTest(unittest.TestCase):
    def test_thresholds(self) -> None:
        self.assertEqual(_rank(90), "S")
        self.assertEqual(_rank(85), "S")
        self.assertEqual(_rank(70), "A")
        self.assertEqual(_rank(55), "B")
        self.assertEqual(_rank(54), "C")


class ContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.npc = NPC_BY_ID["rosa"]
        self.memories = [
            {"kind": "player_said", "text": "金库空了", "round_no": 1, "_round": 1},
            {"kind": "npc_said", "text": "这话值钱", "round_no": 1, "_round": 1},
        ]

    def test_heard_is_included_for_later_turn(self) -> None:
        context = build_context(
            npc=self.npc,
            trust=55,
            turn=2,
            turn_limit=6,
            message="然后呢？",
            memories=self.memories,
            heard={"generation": 1, "text": "金库空得能跑老鼠", "speaker": "rosa", "is_secret": False},
            first_secret=False,
            returning=False,
            new_round=False,
            last_round_echo=None,
            gap_minutes=0,
        )
        self.assertIn("第 1 代", context)
        self.assertIn("金库空得能跑老鼠", context)
        self.assertIn("你记得的", context)

    def test_first_secret_does_not_show_heard(self) -> None:
        context = build_context(
            npc=self.npc,
            trust=55,
            turn=1,
            turn_limit=6,
            message="城主的金库空了",
            memories=[],
            heard=None,
            first_secret=True,
            returning=False,
            new_round=False,
            last_round_echo=None,
            gap_minutes=0,
        )
        self.assertIn("玩家第一次开口", context)
        self.assertNotIn("你最近听来的传闻", context)

    def test_returning_with_new_round_shows_echo(self) -> None:
        context = build_context(
            npc=self.npc,
            trust=50,
            turn=1,
            turn_limit=6,
            message="我又来了",
            memories=[],
            heard=None,
            first_secret=True,
            returning=True,
            new_round=True,
            last_round_echo={"text": "上一局传成仓鼠了"},
            gap_minutes=90,
        )
        self.assertIn("上次来访", context)
        self.assertIn("上一局传成仓鼠了", context)


if __name__ == "__main__":
    unittest.main()
