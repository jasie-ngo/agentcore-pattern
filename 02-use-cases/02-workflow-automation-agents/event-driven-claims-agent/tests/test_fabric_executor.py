"""GraphExecutor: topological wave execution, concurrency, edge gating, and
cycle/validation failures (ADR-0015 decision 1)."""

import asyncio
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

from fabric import registry  # noqa: E402
from fabric.executor import GraphExecutor  # noqa: E402
from fabric.schema import EdgeSpec, FabricConfig, FabricConfigError, NodeSpec, WorkflowSpec  # noqa: E402


def _config(node_ids, edges, start):
    nodes = tuple(NodeSpec(id=n, type="deterministic", implementation=n) for n in node_ids)
    edge_specs = tuple(EdgeSpec(**e) for e in edges)
    return FabricConfig(agents={}, workflow=WorkflowSpec(start=start, nodes=nodes, edges=edge_specs))


class GraphExecutorTests(unittest.TestCase):
    def setUp(self):
        registry.reset()
        self._registered_nodes: list[str] = []
        self._registered_routers: list[str] = []

    def tearDown(self):
        for name in self._registered_nodes:
            registry.DETERMINISTIC_NODES.pop(name, None)
        for name in self._registered_routers:
            registry.ROUTERS.pop(name, None)
        registry.reset()

    def _node(self, name, fn):
        registry.DETERMINISTIC_NODES[name] = fn
        self._registered_nodes.append(name)

    def _router(self, name, fn):
        registry.ROUTERS[name] = fn
        self._registered_routers.append(name)

    def test_linear_chain_runs_in_order(self):
        order = []

        async def a(state):
            order.append("a")
            return {}

        async def b(state):
            order.append("b")
            return {}

        self._node("a", a)
        self._node("b", b)
        config = _config(["a", "b"], [{"source": "a", "target": "b"}], start="a")

        asyncio.run(GraphExecutor(config).run({}))
        self.assertEqual(order, ["a", "b"])

    def test_independent_children_run_concurrently(self):
        async def root(state):
            return {}

        async def slow(state):
            await asyncio.sleep(0.05)
            return {}

        self._node("root", root)
        self._node("x", slow)
        self._node("y", slow)
        config = _config(
            ["root", "x", "y"],
            [{"source": "root", "target": "x"}, {"source": "root", "target": "y"}],
            start="root",
        )

        start = time.monotonic()
        asyncio.run(GraphExecutor(config).run({}))
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.09, "x and y should run concurrently, not sequentially")

    def test_conditional_edge_skips_node_when_router_false(self):
        async def root(state):
            return {}

        async def optional(state):
            return {"ran": True}

        self._node("root", root)
        self._node("optional", optional)
        self._router("never", lambda state: False)
        config = _config(
            ["root", "optional"], [{"source": "root", "target": "optional", "router": "never"}], start="root"
        )

        state = asyncio.run(GraphExecutor(config).run({}))
        self.assertNotIn("ran", state)

    def test_conditional_edge_runs_node_when_router_true(self):
        async def root(state):
            return {}

        async def optional(state):
            return {"ran": True}

        self._node("root", root)
        self._node("optional", optional)
        self._router("always", lambda state: True)
        config = _config(
            ["root", "optional"], [{"source": "root", "target": "optional", "router": "always"}], start="root"
        )

        state = asyncio.run(GraphExecutor(config).run({}))
        self.assertTrue(state.get("ran"))

    def test_root_mismatch_raises(self):
        async def a(state):
            return {}

        self._node("a", a)
        config = _config(["a"], [], start="wrong")

        with self.assertRaises(FabricConfigError):
            asyncio.run(GraphExecutor(config).run({}))

    def test_unregistered_router_raises_fabric_config_error(self):
        """Consistent with _adapter_for: a typo'd router name must surface as a
        FabricConfigError, not a bare KeyError."""

        async def root(state):
            return {}

        async def optional(state):
            return {}

        self._node("root", root)
        self._node("optional", optional)
        config = _config(
            ["root", "optional"],
            [{"source": "root", "target": "optional", "router": "no_such_router"}],
            start="root",
        )

        with self.assertRaises(FabricConfigError) as ctx:
            asyncio.run(GraphExecutor(config).run({}))
        self.assertIn("no_such_router", str(ctx.exception))

    def test_unregistered_node_implementation_raises_fabric_config_error(self):
        config = _config(["missing_impl"], [], start="missing_impl")

        with self.assertRaises(FabricConfigError) as ctx:
            asyncio.run(GraphExecutor(config).run({}))
        self.assertIn("missing_impl", str(ctx.exception))

    def test_cycle_raises(self):
        async def noop(state):
            return {}

        self._node("c", noop)
        self._node("a", noop)
        self._node("b", noop)
        config = _config(
            ["c", "a", "b"],
            [
                {"source": "c", "target": "a"},
                {"source": "a", "target": "b"},
                {"source": "b", "target": "a"},
            ],
            start="c",
        )

        with self.assertRaises(FabricConfigError):
            asyncio.run(GraphExecutor(config).run({}))

    def test_on_step_called_per_node(self):
        async def a(state):
            return {}

        self._node("a", a)
        config = _config(["a"], [], start="a")

        seen = []

        def on_step(node_id, state):
            seen.append(node_id)

        asyncio.run(GraphExecutor(config).run({}, on_step=on_step))
        self.assertEqual(seen, ["a"])

    def test_downstream_node_runs_after_skipped_predecessor(self):
        order = []

        async def root(state):
            return {}

        async def optional(state):
            order.append("optional")
            return {}

        async def downstream(state):
            order.append("downstream")
            return {"reached": True}

        self._node("root", root)
        self._node("optional", optional)
        self._node("downstream", downstream)
        self._router("never", lambda state: False)
        config = _config(
            ["root", "optional", "downstream"],
            [
                {"source": "root", "target": "optional", "router": "never"},
                {"source": "optional", "target": "downstream"},
            ],
            start="root",
        )

        state = asyncio.run(GraphExecutor(config).run({}))
        self.assertNotIn("optional", order)
        self.assertIn("downstream", order)
        self.assertTrue(state.get("reached"))


if __name__ == "__main__":
    unittest.main()
