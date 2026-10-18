"""Exercise checkbox selection, submission, and normal Anki grading."""
import curses
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from aktui.multiselect import parse_multiselect
from aktui.ui import UI


FORM = """<form data-aktui-multiselect="1" data-answer="alpha,beta">
<div data-mc-prompt>Left &emsp; Right</div>
<label><input type="checkbox" value="alpha"><span>A. Alpha</span></label>
<label><input type="checkbox" value="beta"><span>B. Beta</span></label>
<label><input type="checkbox" value="none" data-exclusive>C. None</label>
<button type="button">Submit</button></form>"""


class MultiSelectTests(unittest.TestCase):
    def make_ui(self, keys, html=FORM):
        ui = object.__new__(UI)
        iterator = iter(keys)
        ui.win = SimpleNamespace(getch=lambda: next(iterator), getmaxyx=lambda: (30, 100))
        ui.did = 1
        ui.frames = []
        ui.draw = lambda title, lines, footer, offset=0: ui.frames.append(list(lines))
        ui.notice = Mock()
        card = SimpleNamespace(question=lambda: html, answer=lambda: '<div>Reference answer</div>')
        states = object()
        ui.s = SimpleNamespace(
            col=SimpleNamespace(decks=SimpleNamespace(name=lambda did: 'Study'),
                                sched=SimpleNamespace(describe_next_states=lambda state: ['1m'] * 4)),
            select_deck=Mock(), next_card=Mock(side_effect=[(card, states), None]), answer=Mock(),
        )
        return ui, card, states

    def test_only_explicitly_supported_forms_are_parsed(self):
        self.assertIsNone(parse_multiselect('<p>Question</p><input type="checkbox">'))
        self.assertIsNone(parse_multiselect('<form><input type="checkbox"></form>'))
        form = parse_multiselect(FORM)
        self.assertEqual(form.prompt, 'Left \u2003 Right')
        self.assertEqual([choice.label for choice in form.choices], ['A. Alpha', 'B. Beta', 'C. None'])
        self.assertEqual(form.correct, {'alpha', 'beta'})
        self.assertEqual(form.submit_label, 'Submit')

    def test_toggling_supports_multiple_answers_and_exclusive_none(self):
        form = parse_multiselect(FORM)
        selected = set()
        form.toggle(selected, 0)
        form.toggle(selected, 1)
        self.assertEqual(selected, {'alpha', 'beta'})
        form.toggle(selected, 2)
        self.assertEqual(selected, {'none'})
        form.toggle(selected, 1)
        self.assertEqual(selected, {'beta'})
        form.toggle(selected, 1)
        self.assertEqual(selected, set())

    def test_feedback_requires_the_exact_answer_set(self):
        form = parse_multiselect(FORM)
        self.assertEqual(form.feedback({'alpha', 'beta'})[0], 'Correct.')
        self.assertEqual(form.feedback({'alpha'})[0], 'Incorrect.')
        self.assertEqual(form.feedback({'alpha', 'beta', 'none'})[0], 'Incorrect.')

    def test_declared_separator_and_exclusive_answer(self):
        form = parse_multiselect(FORM.replace('data-answer="alpha,beta"', 'data-answer="none" data-separator=";"'))
        self.assertEqual(form.correct, {'none'})
        self.assertEqual(form.feedback({'none'})[0], 'Correct.')

    def test_malformed_forms_fail_before_review(self):
        variants = [
            FORM.replace('alpha,beta', 'missing'),
            FORM.replace('alpha,beta', ''),
            FORM.replace('alpha,beta', 'alpha,none'),
            FORM.replace('value="beta"', 'value="alpha"'),
            FORM.replace('</form>', ''),
            FORM + FORM,
            FORM.replace('data-answer="alpha,beta"', 'data-answer="alpha,beta" data-separator=""'),
        ]
        for html in variants:
            with self.subTest(html=html), self.assertRaises(ValueError):
                parse_multiselect(html)

    def test_selection_does_not_reveal_or_grade_before_submit(self):
        ui, card, states = self.make_ui([
            ord('1'), ord('2'), curses.KEY_DOWN, curses.KEY_DOWN, 10, ord('3'),
        ])
        ui.study()
        self.assertTrue(any('[x] A. Alpha' in '\n'.join(frame) and '[x] B. Beta' in '\n'.join(frame) for frame in ui.frames))
        self.assertTrue(any('Correct.' in frame and 'Selected: alpha, beta' in frame for frame in ui.frames))
        self.assertTrue(all('Reference answer' not in '\n'.join(frame) for frame in ui.frames[:5]))
        ui.s.answer.assert_called_once_with(card, states, 3)

    def test_empty_submit_stays_on_question(self):
        ui, _, _ = self.make_ui([curses.KEY_UP, 10, ord('1'), curses.KEY_UP, 10])
        selected = ui.multiselect(parse_multiselect(FORM))
        self.assertEqual(selected, {'alpha'})
        self.assertTrue(any('Choose at least one option before submitting.' in frame for frame in ui.frames))
        ui.s.answer.assert_not_called()

    def test_back_from_selection_leaves_review_unchanged(self):
        ui, _, _ = self.make_ui([ord('1'), ord('q')])
        ui.study()
        ui.s.answer.assert_not_called()

    def test_basic_cards_keep_the_existing_reveal_and_rating_keys(self):
        ui, card, states = self.make_ui([32, ord('3')], '<div>Question</div>')
        ui.study()
        self.assertTrue(any('Question' in frame for frame in ui.frames))
        self.assertTrue(any('Reference answer' in frame for frame in ui.frames))
        ui.s.answer.assert_called_once_with(card, states, 3)


if __name__ == '__main__':
    unittest.main()
