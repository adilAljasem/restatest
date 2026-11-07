from unittest import TestCase

from ..models.stock_request import StockRequest


class MoveSet:
    """Minimal recordset double for the read-only graph traversal."""

    def __init__(self, graph, ids=()):
        self.graph = graph
        self.ids = frozenset(ids)

    def browse(self):
        return MoveSet(self.graph)

    def __bool__(self):
        return bool(self.ids)

    def __or__(self, other):
        return MoveSet(self.graph, self.ids | other.ids)

    def __sub__(self, other):
        return MoveSet(self.graph, self.ids - other.ids)

    def mapped(self, name):
        assert name == "move_orig_ids"
        return MoveSet(self.graph, (
            origin for move in self.ids for origin in self.graph.get(move, ())
        ))


class TestOriginMoveGraph(TestCase):
    def check_graph(self, graph, roots, expected):
        result = StockRequest._get_all_origin_moves(None, MoveSet(graph, roots))
        self.assertEqual(result.ids, frozenset(expected))

    def test_empty(self):
        self.check_graph({}, [], [])

    def test_shared_origin_and_disconnected_move(self):
        self.check_graph({1: [2, 3], 2: [4], 3: [4], 9: [10]}, [1], [1, 2, 3, 4])

    def test_cycle(self):
        self.check_graph({1: [2], 2: [3], 3: [1]}, [1], [1, 2, 3])

    def test_self_reference(self):
        self.check_graph({1: [1]}, [1], [1])

    def test_deep_chain(self):
        self.check_graph({i: [i + 1] for i in range(2000)}, [0], range(2001))
