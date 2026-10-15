import ast
import asyncio
import io
import json
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock, patch
from xml.etree import ElementTree

from labyrinth import (
    KEEP,
    ClosedMaze,
    Direction,
    EndGameEvent,
    Game,
    GameEvent,
    GameStatus,
    Grid,
    InteractionContext,
    InteractionEffect,
    InteractionPhase,
    Mouse,
    Place,
    PlaceObject,
    SpiralMaze,
    TrailLine,
)
from labyrinth_view import MazeView
from main import create_demo

NS = {"s": "http://www.w3.org/2000/svg"}


class PlaceTests(unittest.TestCase):
    def test_closed_is_cached_and_objects_removed_by_identity(self):
        reads = []

        class Block(PlaceObject):
            def __getattribute__(self, name):
                if name == "blocks_movement":
                    reads.append(name)
                return super().__getattribute__(name)

        place = Place()
        first, second = Block(blocks_movement=True), Block(blocks_movement=True)
        place.install(first).install(second)
        for _ in range(10):
            self.assertTrue(place.closed)
        self.assertEqual(len(reads), 2)
        place.remove(first)
        self.assertTrue(place.closed)
        place.remove(second)
        self.assertFalse(place.closed)
        self.assertEqual(len(reads), 2)

    def test_ttl_replacement_and_independent_installations(self):
        last = PlaceObject(ttl=1)
        middle = PlaceObject(ttl=1, next_object=last, blocks_movement=True)
        first = PlaceObject(ttl=1, next_object=middle, blocks_movement=True)
        a, b = Place(), Place()
        a.install(first)
        a.tick()
        b.install(first)
        self.assertEqual(a.objects, (middle,))
        a.tick()
        b.tick()
        self.assertEqual(a.objects, (last,))
        self.assertEqual(b.objects, (middle,))
        a.tick()
        self.assertEqual(a.objects, ())

    def test_interact_uses_snapshot_and_applies_replacements_after_callbacks(self):
        grid = Grid(2, 1)
        game = Game(grid)
        place = grid.root.place
        calls = []
        emitted = GameEvent()
        replacement = PlaceObject()

        def first_callback(context):
            calls.append(("first", context.phase, place.objects))
            with self.assertRaises(RuntimeError):
                place.install(PlaceObject())
            return InteractionEffect(replacement=replacement, events=(emitted,))

        def second_callback(context):
            calls.append(("second", context.phase, place.objects))
            return InteractionEffect(replacement=None)

        first = PlaceObject(on_interact=first_callback)
        second = PlaceObject(on_interact=second_callback)
        place.install(first).install(second)
        context = InteractionContext(
            game, game.mouse, place, (0, 0), (0, 0), None,
            InteractionPhase.DESTINATION,
        )
        self.assertEqual(place.interact(context), (emitted,))
        self.assertEqual([call[0] for call in calls], ["first", "second"])
        self.assertTrue(all(call[2] == (first, second) for call in calls))
        self.assertEqual(place.objects, (replacement,))

    def test_interact_validation_and_recursion_guard(self):
        grid = Grid(2, 1)
        game = Game(grid)
        place = grid.root.place
        context = InteractionContext(game, game.mouse, place, (0, 0), (0, 0), None,
                                     InteractionPhase.DESTINATION)

        def recursive(ctx):
            ctx.place.interact(ctx)

        place.install(PlaceObject(on_interact=recursive))
        with self.assertRaises(RuntimeError):
            place.interact(context)
        place.remove_where(lambda obj: obj.on_interact is recursive)
        place.install(PlaceObject(on_interact=lambda ctx: "invalid"))
        with self.assertRaises(TypeError):
            place.interact(context)
        with self.assertRaises(ValueError):
            Place().interact(context)

    def test_invalid_effect_does_not_corrupt_place(self):
        grid = Grid(2, 1)
        game = Game(grid)
        place = grid.root.place
        obj = PlaceObject(blocks_movement=True, ttl=2,
                          on_interact=lambda ctx: InteractionEffect(replacement="bad"))
        place.install(obj)
        context = InteractionContext(game, game.mouse, place, (0, 0), (0, 0), None,
                                     InteractionPhase.DESTINATION)
        with self.assertRaises(TypeError):
            place.interact(context)
        self.assertEqual(place.objects, (obj,))
        self.assertTrue(place.closed)
        self.assertTrue(place.needs_tick)

    def test_replacements_preserve_order_and_identity_uniqueness(self):
        grid = Grid(2, 1)
        game = Game(grid)
        place = grid.root.place
        calls = []
        successor = PlaceObject(on_interact=lambda ctx: calls.append("successor"))
        first = PlaceObject(on_interact=lambda ctx: (
            calls.append("first") or InteractionEffect(replacement=successor)
        ))
        second = PlaceObject(on_interact=lambda ctx: calls.append("second"))
        place.install(first).install(second)
        context = InteractionContext(game, game.mouse, place, (0, 0), (0, 0), None,
                                     InteractionPhase.DESTINATION)
        place.interact(context)
        place.interact(context)
        self.assertEqual(calls, ["first", "second", "successor", "second"])
        self.assertEqual(place.objects, (successor, second))

        shared = PlaceObject()
        duplicate_place = Place()
        duplicate_place.install(PlaceObject(
            on_interact=lambda ctx: InteractionEffect(replacement=shared),
        ))
        duplicate_place.install(PlaceObject(
            on_interact=lambda ctx: InteractionEffect(replacement=shared),
        ))
        duplicate_context = InteractionContext(
            game, game.mouse, duplicate_place, (0, 0), (0, 0), None,
            InteractionPhase.DESTINATION,
        )
        before = duplicate_place.objects
        with self.assertRaises(ValueError):
            duplicate_place.interact(duplicate_context)
        self.assertEqual(duplicate_place.objects, before)

        ttl_place = Place()
        ttl_place.install(PlaceObject(ttl=1, next_object=shared))
        ttl_place.install(PlaceObject(ttl=1, next_object=shared))
        before = ttl_place.objects
        with self.assertRaises(ValueError):
            ttl_place.tick()
        self.assertEqual(ttl_place.objects, before)

    def test_object_validation(self):
        for kwargs in ({"ttl": 0}, {"ttl": True}, {"layer": 4}, {"layer": True},
                       {"next_object": PlaceObject()}, {"on_interact": 1}):
            with self.assertRaises((ValueError, TypeError)):
                PlaceObject(**kwargs)
        for invalid in (object(), None):
            with self.assertRaises(TypeError):
                Place().install(invalid)


class GridTests(unittest.TestCase):
    def test_shared_places_and_toroidal_geometry(self):
        grid = Grid(3, 2)
        self.assertEqual(grid.adjacent((0, 0), "left"), (2, 0))
        self.assertIs(grid.root.left, grid.neighbor((0, 0), "left").right)
        self.assertIs(grid.root.up, grid.neighbor((0, 0), "up").down)
        self.assertEqual(len(grid.links), 24)
        self.assertEqual(len(grid.edge_places), 12)
        self.assertFalse(hasattr(grid, "add_wall"))
        with self.assertRaises(TypeError):
            Grid(2, 2, mode="closed")

    def test_parallel_edges_and_self_loops_are_distinct(self):
        grid = Grid(2, 1)
        self.assertIsNot(grid.root.right, grid.root.left)
        block = PlaceObject(blocks_movement=True)
        grid.place_between((0, 0), "right", block)
        self.assertFalse(grid.can_move((0, 0), "right"))
        self.assertTrue(grid.can_move((0, 0), "left"))
        single = Grid(1, 1)
        self.assertIs(single.root.left, single.root.right)
        self.assertIs(single.root.up, single.root.down)
        self.assertIsNot(single.root.left, single.root.up)

    def test_placement_tick_and_read_only_endpoints(self):
        grid = Grid(3, 1)
        successor = PlaceObject()
        temporary = PlaceObject(ttl=1, next_object=successor, blocks_movement=True)
        grid.place_between((0, 0), "right", temporary)
        self.assertIn(grid.root.right, grid.active_places)
        grid.tick()
        self.assertEqual(grid.root.right.objects, (successor,))
        self.assertNotIn(grid.root.right, grid.active_places)
        with self.assertRaises(AttributeError):
            grid.exit = (1, 0)
        with self.assertRaises(AttributeError):
            grid.entrance = (1, 0)


class ClosedMazeTests(unittest.TestCase):
    def test_closed_maze_and_wall_helpers(self):
        maze = ClosedMaze(3, 2)
        self.assertEqual(maze.links, ())
        self.assertEqual(len(maze.placements), 12)
        other = PlaceObject(blocks_movement=True)
        maze.place_between((0, 0), "right", other)
        maze.remove_wall((0, 0), "right")
        self.assertFalse(maze.has_wall((0, 0), "right"))
        self.assertFalse(maze.can_move((0, 0), "right"))
        maze.remove_between((0, 0), "right", other)
        self.assertTrue(maze.can_move((0, 0), "right"))

    def test_open_path_is_atomic(self):
        maze = ClosedMaze(3, 3)
        observer = Mock()
        maze.subscribe(observer)
        with self.assertRaises(ValueError):
            maze.open_path([(0, 0), (1, 0), (2, 1)])
        observer.assert_not_called()
        self.assertEqual(maze.links, ())
        maze.open_path([(0, 0), (1, 0), (1, 1)])
        observer.assert_called_once_with()


class SpiralMazeTests(unittest.TestCase):
    def test_clockwise_spiral_for_rectangle(self):
        maze = SpiralMaze(4, 3)
        self.assertEqual(maze.spiral_path, (
            (0, 0), (1, 0), (2, 0), (3, 0), (3, 1), (3, 2),
            (2, 2), (1, 2), (0, 2), (0, 1), (1, 1), (2, 1),
        ))
        self.assertEqual(maze.exit, (2, 1))

    def test_spiral_for_arbitrary_dimensions(self):
        for width in range(1, 8):
            for height in range(1, 8):
                maze = SpiralMaze(width, height)
                self.assertEqual(set(maze.spiral_path), set(maze.positions))
                self.assertEqual(len(maze.links), 2 * (width * height - 1))


class GameTests(unittest.TestCase):
    def test_game_is_only_public_movement_api(self):
        with self.assertRaises(TypeError):
            Mouse(Grid(2, 1))
        game = Game(Grid(3, 1))
        for name in ("move", "walk", "up", "right", "down", "left", "reset"):
            self.assertFalse(hasattr(game.mouse, name))
        self.assertFalse(hasattr(game, "move"))
        self.assertFalse(hasattr(game, "walk"))
        with self.assertRaises(TypeError):
            game.right(2)
        self.assertTrue(game.right())
        self.assertEqual(game.mouse.position, (1, 0))
        with self.assertRaises(ValueError):
            Game(game.grid)

    def test_blocked_or_finished_commands_return_false_without_tick(self):
        maze = ClosedMaze(2, 1)
        game = Game(maze)
        self.assertFalse(game.right())
        self.assertEqual(maze.current_tick, 0)
        maze.remove_wall((0, 0), "right")
        self.assertTrue(game.right())
        self.assertTrue(game.finished)
        self.assertEqual(maze.current_tick, 1)
        self.assertFalse(game.left())
        self.assertEqual(maze.current_tick, 1)

    def test_tick_edge_destination_order_and_context(self):
        grid = Grid(3, 1, exit=(2, 0))
        game = Game(grid)
        calls = []

        def edge_callback(context):
            calls.append(("edge", context.phase, context.actor.position,
                          context.source, context.target, grid.current_tick))

        def destination_callback(context):
            calls.append(("destination", context.phase, context.actor.position,
                          context.source, context.target, grid.current_tick))

        successor = PlaceObject(on_interact=edge_callback)
        grid.place_between((0, 0), "right", PlaceObject(ttl=1, next_object=successor))
        grid.place_on((1, 0), PlaceObject(on_interact=destination_callback))
        self.assertTrue(game.right())
        self.assertEqual(calls, [
            ("edge", InteractionPhase.EDGE, (0, 0), (0, 0), (1, 0), 1),
            ("destination", InteractionPhase.DESTINATION, (1, 0), (0, 0), (1, 0), 1),
        ])

    def test_reentrant_command_is_rejected_without_corrupting_trail(self):
        grid = Grid(3, 1, exit=(2, 0))
        game = Game(grid)

        def callback(context):
            with self.assertRaises(RuntimeError):
                context.game.right()

        grid.place_on((1, 0), PlaceObject(on_interact=callback))
        self.assertTrue(game.right())
        self.assertEqual(game.mouse.position, (1, 0))
        line = next(obj for obj in grid.root.right.objects if isinstance(obj, TrailLine))
        self.assertEqual((line.start, line.end), ((0, 0), (1, 0)))

    def test_callback_exception_notifies_committed_position_and_trail(self):
        grid = Grid(3, 1, exit=(2, 0))
        game = Game(grid)
        observer = Mock()
        grid.subscribe(observer)

        def fail(context):
            raise ValueError("interaction failed")

        grid.place_on((1, 0), PlaceObject(on_interact=fail))
        observer.reset_mock()
        with self.assertRaisesRegex(ValueError, "interaction failed"):
            game.right()
        self.assertEqual(game.mouse.position, (1, 0))
        self.assertEqual(grid.current_tick, 1)
        self.assertTrue(any(isinstance(obj, TrailLine) for obj in grid.root.right.objects))
        observer.assert_called_once_with()

    def test_endgame_event_is_published_once(self):
        game = Game(Grid(2, 1))
        listener = Mock()
        game.subscribe_event(listener)
        self.assertTrue(game.right())
        self.assertEqual(game.status, GameStatus.FINISHED)
        listener.assert_called_once()
        event = listener.call_args.args[0]
        self.assertIsInstance(event, EndGameEvent)
        self.assertEqual(event.position, (1, 0))
        self.assertFalse(game.left())
        listener.assert_called_once()

    def test_single_cell_finishes_during_initial_interaction(self):
        game = Game(SpiralMaze(1, 1))
        self.assertTrue(game.finished)
        self.assertTrue(game.mouse.at_exit)
        self.assertFalse(game.right())

    def test_exit_entrance_and_reset_are_owned_by_game(self):
        game = Game(Grid(4, 1))
        game.exit = (2, 0)
        self.assertEqual(game.exit, (2, 0))
        self.assertTrue(game.right())
        self.assertTrue(game.right())
        self.assertTrue(game.finished)
        game.entrance = (1, 0)
        game.reset()
        self.assertEqual(game.mouse.position, (1, 0))
        self.assertFalse(game.finished)
        self.assertEqual(game.mouse.trail, ((1, 0),))

    def test_invalid_exit_preserves_old_finish(self):
        game = Game(Grid(2, 1))
        with self.assertRaises(ValueError):
            game.exit = (2, 0)
        self.assertEqual(game.exit, (1, 0))
        self.assertTrue(game.right())
        self.assertTrue(game.finished)

    def test_all_event_listeners_run_after_committed_step(self):
        game = Game(Grid(2, 1))
        second = Mock()
        game.subscribe_event(lambda event: (_ for _ in ()).throw(ValueError("listener")))
        game.subscribe_event(second)
        with self.assertRaises(ExceptionGroup) as raised:
            game.right()
        self.assertTrue(game.finished)
        self.assertEqual(game.mouse.position, (1, 0))
        second.assert_called_once()
        self.assertIsInstance(raised.exception.exceptions[0], ValueError)

    def test_event_listener_cannot_start_nested_command(self):
        game = Game(Grid(2, 1))
        attempts = []

        def listener(event):
            with self.assertRaises(RuntimeError):
                game.left()
            attempts.append(event)

        game.subscribe_event(listener)
        self.assertTrue(game.right())
        self.assertEqual(game.mouse.position, (1, 0))
        self.assertEqual(game.grid.current_tick, 1)
        self.assertEqual(len(attempts), 1)

    def test_trail_has_three_ttl_stages(self):
        game = Game(Grid(5, 1))
        game.right()
        place = game.grid.root.right
        first = next(obj for obj in place.objects if isinstance(obj, TrailLine))
        game.right()
        self.assertIn(first.next_object, place.objects)
        game.right()
        self.assertIn(first.next_object.next_object, place.objects)
        game.right()
        self.assertNotIn(first.next_object.next_object, place.objects)


class ViewTests(unittest.TestCase):
    def test_registry_layers_mro_and_unknown_objects(self):
        @dataclass(frozen=True, slots=True, kw_only=True)
        class Marker(PlaceObject):
            name: str

        class Child(Marker):
            pass

        game = Game(Grid(3, 2))
        view = MazeView(game)
        self.addCleanup(view.close)
        calls = []

        def render(view, obj, location, context):
            calls.append(obj.name)
            return f'<g id="{obj.name}"/>'

        view.register_renderer(Marker, render)
        game.grid.place_on((0, 0), Marker(name="front", layer=3))
        game.grid.place_between((0, 0), "right", Child(name="back", layer=1))
        game.grid.place_on((2, 1), Marker(name="middle", layer=2))
        svg = view.to_svg()
        ElementTree.fromstring(svg)
        self.assertEqual(calls, ["back", "middle", "front"])

    def test_gradient_line_and_closed_edge(self):
        game = Game(Grid(5, 1))
        view = MazeView(game, show_grid=False)
        self.addCleanup(view.close)
        game.right()
        game.right()
        game.right()
        root = ElementTree.fromstring(view.to_svg())
        self.assertEqual(len(root.findall(".//s:linearGradient", NS)), 3)
        blocker = PlaceObject(blocks_movement=True)
        game.grid.place_between((3, 0), "right", blocker)
        self.assertFalse(game.right())
        barriers = [line for line in ElementTree.fromstring(view.to_svg()).findall("s:line", NS)
                    if line.attrib["stroke"] == "#334155"]
        self.assertGreaterEqual(len(barriers), 1)

    def test_display_updates_once_and_close_unsubscribes(self):
        game = Game(Grid(3, 1))
        view = MazeView(game)
        handle = Mock()
        with patch("labyrinth_view.display", return_value=handle):
            view._ipython_display_()
        game.right()
        handle.update.assert_called_once()
        view.close()
        game.right()
        handle.update.assert_called_once()
        self.assertEqual(game.grid._listeners, [])
        self.assertEqual(game._listeners, [])

    def test_demo_and_notebook_use_game_api(self):
        maze, game, view = create_demo()
        self.addCleanup(view.close)
        self.assertIs(game.grid, maze)
        notebook = json.loads((Path(__file__).resolve().parents[1] / "demo.ipynb").read_text())
        sources = "".join("".join(cell["source"]) for cell in notebook["cells"])
        self.assertNotIn("mouse.right", sources)
        self.assertNotIn("mouse.walk", sources)
        self.assertNotIn("mouse.reset", sources)

    def test_notebook_cells_execute(self):
        notebook = json.loads((Path(__file__).resolve().parents[1] / "demo.ipynb").read_text())
        namespace = {}
        with (patch("time.sleep"), patch("asyncio.sleep", new_callable=Mock),
              patch("IPython.display.display", return_value=None),
              patch("sys.stdout", new_callable=io.StringIO)):
            for cell in notebook["cells"]:
                if cell["cell_type"] != "code":
                    continue
                code = compile("".join(cell["source"]), "demo.ipynb", "exec",
                               flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
                result = eval(code, namespace)
                if asyncio.iscoroutine(result):
                    asyncio.run(result)
        self.addCleanup(namespace["view"].close)
        self.addCleanup(namespace["my_view"].close)


if __name__ == "__main__":
    unittest.main()
