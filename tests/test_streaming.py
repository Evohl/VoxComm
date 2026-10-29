import threading
import unittest

from voxcomm.streaming import SentenceSplitter, SentenceStream, split_sentences


class SentenceSplitterTests(unittest.TestCase):
    def test_complete_sentences_are_released_as_soon_as_the_next_one_starts(self):
        splitter = SentenceSplitter()

        self.assertEqual(splitter.feed("Das Schiff ist bereit für den Start. Die"), ["Das Schiff ist bereit für den Start."])
        self.assertEqual(splitter.feed(" Triebwerke laufen stabil und ruhig."), [])
        self.assertEqual(splitter.flush(), ["Die Triebwerke laufen stabil und ruhig."])

    def test_text_is_chunked_identically_for_any_delta_size(self):
        text = "Erster langer Satz kommt hier. Zweiter langer Satz folgt dann! Und ein dritter langer Satz?"
        expected = split_sentences(text)

        for size in (1, 2, 5, 17):
            splitter = SentenceSplitter()
            sentences = []
            for index in range(0, len(text), size):
                sentences += splitter.feed(text[index:index + size])
            sentences += splitter.flush()
            self.assertEqual(sentences, expected, size)
        self.assertEqual(" ".join(expected), text)

    def test_abbreviations_decimals_and_short_fragments_do_not_split(self):
        self.assertEqual(
            split_sentences("Das kostet ca. 3.5 Millionen Credits laut dem Händler. Mehr nicht."),
            ["Das kostet ca. 3.5 Millionen Credits laut dem Händler.", "Mehr nicht."],
        )
        self.assertEqual(split_sentences("Ja. Nein."), ["Ja. Nein."])

    def test_lines_and_list_items_split_on_newlines(self):
        self.assertEqual(
            split_sentences("Erster Punkt der Liste ist wichtig\nZweiter Punkt der Liste ist ebenso wichtig"),
            ["Erster Punkt der Liste ist wichtig", "Zweiter Punkt der Liste ist ebenso wichtig"],
        )

    def test_flush_returns_unfinished_text_once(self):
        splitter = SentenceSplitter()
        splitter.feed("Kein Satzende")

        self.assertEqual(splitter.flush(), ["Kein Satzende"])
        self.assertEqual(splitter.flush(), [])


class SentenceStreamTests(unittest.TestCase):
    def test_first_sentence_triggers_start_once_and_all_sentences_are_iterated(self):
        starts = []
        stream = SentenceStream(threading.Event(), on_first=lambda: starts.append(True))

        stream.feed("Das Schiff ist bereit für den Start. Die Triebwerke")
        stream.feed(" laufen stabil und ruhig.")
        stream.finish()

        self.assertEqual(list(stream), [
            "Das Schiff ist bereit für den Start.", "Die Triebwerke laufen stabil und ruhig."])
        self.assertEqual(starts, [True])

    def test_short_answer_without_boundary_starts_on_finish(self):
        starts = []
        stream = SentenceStream(threading.Event(), on_first=lambda: starts.append(True))

        stream.feed("Verstanden")
        self.assertEqual(starts, [])
        stream.finish()

        self.assertEqual(list(stream), ["Verstanden"])
        self.assertEqual(starts, [True])

    def test_iteration_ends_when_cancelled_even_without_finish(self):
        cancel_event = threading.Event()
        stream = SentenceStream(cancel_event)
        cancel_event.set()

        self.assertEqual(list(stream), [])


if __name__ == "__main__":
    unittest.main()
