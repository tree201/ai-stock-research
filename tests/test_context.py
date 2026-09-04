from uuid import uuid4
import unittest

from stock_research.context import ContextBuilder
from stock_research.documents import EvidenceChunk


def _chunk(text: str, index: int = 0) -> EvidenceChunk:
    return EvidenceChunk(document_id=uuid4(), chunk_index=index, text=text, start_line=1, end_line=2)


class ContextBuilderTests(unittest.TestCase):
    def test_rejects_tiny_budget(self) -> None:
        with self.assertRaises(ValueError):
            ContextBuilder(max_chars=10)

    def test_empty_chunks_return_empty_selection(self) -> None:
        self.assertEqual(ContextBuilder().select([], "研究"), [])

    def test_budget_limits_total_selected_chars(self) -> None:
        chunks = [_chunk("operating overview " * 40, index) for index in range(10)]
        selected = ContextBuilder(max_chars=2000).select(chunks, "研究")
        self.assertTrue(selected)
        self.assertLessEqual(sum(len(chunk.text) for chunk in selected), 2000)
        self.assertLess(len(selected), 10)

    def test_question_relevance_wins_budget(self) -> None:
        relevant = _chunk("Revenue growth for the cloud division", 0)
        filler = _chunk("lorem ipsum " * 60, 1)
        selected = ContextBuilder(max_chars=100).select([filler, relevant], "cloud growth")
        self.assertEqual([chunk.text for chunk in selected], [relevant.text])

    def test_selection_preserves_document_order(self) -> None:
        first = _chunk("alpha risk", 0)
        second = _chunk("beta", 1)
        third = _chunk("gamma revenue", 2)
        selected = ContextBuilder(max_chars=1000).select([first, second, third], "revenue risk")
        self.assertEqual([chunk.text for chunk in selected], ["alpha risk", "beta", "gamma revenue"])

    def test_all_oversized_chunks_fall_back_to_best(self) -> None:
        big = _chunk("risk discussion " * 100, 0)
        selected = ContextBuilder(max_chars=1000).select([big], "风险")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].text, big.text)


if __name__ == "__main__":
    unittest.main()
