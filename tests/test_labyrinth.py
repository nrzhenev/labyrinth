import asyncio
import ast
import io
import json
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from xml.etree import ElementTree

from labyrinth import ClosedMaze, Direction, Grid, Mouse, Place, PlaceObject, SpiralMaze, TrailLine
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
        first = Block(blocks_movement=True)
        second = Block(blocks_movement=True)
        place.install(first).install(second)
        for _ in range(10):
            self.assertTrue(place.closed)
        self.assertEqual(len(reads), 2)
        place.remove(first)
        self.assertTrue(place.closed)
        self.assertIs(place.objects[0], second)
        place.remove(second)
        self.assertFalse(place.closed)
        self.assertEqual(len(reads), 2)

    def test_ttl_replacement_is_atomic_and_successor_gets_full_lifetime(self):
        events = []
        place = Place(lambda p: events.append((p.objects, p.closed)))
        last = PlaceObject(ttl=1)
        middle = PlaceObject(ttl=1, next_object=last, blocks_movement=True)
        first = PlaceObject(ttl=1, next_object=middle, blocks_movement=True)
        place.install(first)
        place.tick()
        self.assertEqual(place.objects, (middle,))
        self.assertEqual(events[-1], ((middle,), True))
        place.tick()
        self.assertEqual(place.objects, (last,))
        self.assertFalse(place.closed)
        place.tick()
        self.assertEqual(place.objects, ())
        self.assertFalse(place.needs_tick)
        self.assertEqual(len(events), 4)

    def test_object_lifetime_belongs_to_each_installation(self):
        first, second = Place(), Place()
        shared = PlaceObject(ttl=2)
        first.install(shared)
        first.tick()
        second.install(shared)
        first.tick()
        second.tick()
        self.assertEqual(first.objects, ())
        self.assertEqual(second.objects, (shared,))
        second.tick()
        self.assertEqual(second.objects, ())

    def test_validation_and_identity(self):
        for kwargs in ({"ttl": 0}, {"ttl": True}, {"layer": 4}, {"layer": True},
                       {"next_object": PlaceObject()}):
            with self.assertRaises(ValueError):
                PlaceObject(**kwargs)
        place = Place()
        for invalid in (object(), None):
            with self.assertRaises(TypeError):
                place.install(invalid)
        obj = PlaceObject()
        place.install(obj)
        with self.assertRaises(ValueError):
            place.install(obj)
        self.assertFalse(place.remove(PlaceObject()))
        self.assertEqual(place.objects, (obj,))


class GridTests(unittest.TestCase):
    def test_shared_places_and_toroidal_geometry(self):
        grid = Grid(3, 2)
        self.assertEqual(grid.adjacent((0, 0), "left"), (2, 0))
        self.assertIs(grid.root.left, grid.neighbor((0, 0), "left").right)
        self.assertIs(grid.root.up, grid.neighbor((0, 0), "up").down)
        self.assertEqual(repr(grid.root), "Cell(x=0, y=0)")
        self.assertEqual(len(grid.links), 24)
        self.assertEqual(len(grid.edge_places), 12)
        self.assertFalse(hasattr(grid, "add_wall"))
        with self.assertRaises(TypeError):
            Grid(2, 2, mode="closed")

    def test_parallel_edges_and_self_loops_are_distinct(self):
        grid = Grid(2, 1)
        self.assertIsNot(grid.root.right, grid.root.left)
        self.assertIs(grid.root.right, grid.cell_at((1, 0)).left)
        grid.place_between((0, 0), "right", PlaceObject(blocks_movement=True))
        self.assertFalse(grid.can_move((0, 0), "right"))
        self.assertTrue(grid.can_move((0, 0), "left"))
        single = Grid(1, 1)
        self.assertIs(single.root.left, single.root.right)
        self.assertIs(single.root.up, single.root.down)
        self.assertIsNot(single.root.left, single.root.up)
        self.assertEqual(len(single.edge_places), 2)

    def test_placement_queries_and_direct_place_notifications(self):
        grid = Grid(3, 2)
        observer = Mock()
        grid.subscribe(observer)
        item, block = PlaceObject(), PlaceObject(blocks_movement=True)
        grid.place_on((0, 0), item).place_on((0, 0), block)
        self.assertEqual(grid.objects_at((0, 0)), (item, block))
        self.assertTrue(grid.can_move((0, 0), "right"))
        grid.root.right.install(block)
        self.assertEqual(grid.objects_between((1, 0), "left"), (block,))
        self.assertFalse(grid.can_move((0, 0), "right"))
        grid.remove_between((1, 0), "left", block)
        grid.remove_from((0, 0), item)
        self.assertEqual(grid.objects_at((0, 0)), (block,))
        self.assertEqual(observer.call_count, 5)

    def test_only_active_places_tick_once_and_notifications_are_batched(self):
        grid = Grid(3, 1)
        observer = Mock()
        grid.subscribe(observer)
        successor = PlaceObject(blocks_movement=False)
        grid.place_between((0, 0), "right",
                           PlaceObject(ttl=1, next_object=successor, blocks_movement=True))
        grid.place_on((2, 0), PlaceObject(ttl=2))
        observer.reset_mock()
        with patch.object(grid.root.place, "tick") as idle_tick:
            grid.tick()
            idle_tick.assert_not_called()
        observer.assert_called_once_with()
        self.assertEqual(grid.objects_between((0, 0), "right"), (successor,))
        self.assertTrue(grid.can_move((0, 0), "right"))
        self.assertEqual(grid.active_places, frozenset({grid.cell_at((2, 0)).place}))
        grid.tick()
        self.assertEqual(grid.active_places, frozenset())
        self.assertEqual(grid.current_tick, 2)

    def test_invalid_dimensions_and_positions(self):
        for width, height in [(0, 2), (2, -1), (2.5, 2), (True, 2)]:
            with self.assertRaises(ValueError):
                Grid(width, height)
        for position in [(2, 0), (-1, 0), [0, 0], (True, 0)]:
            with self.assertRaises(ValueError):
                Grid(2, 2, entrance=position)


class ClosedMazeTests(unittest.TestCase):
    def test_closed_maze_installs_one_wall_per_physical_edge(self):
        for width, height in [(1, 1), (2, 1), (3, 2)]:
            maze = ClosedMaze(width, height)
            self.assertIsInstance(maze, Grid)
            self.assertEqual(maze.links, ())
            self.assertEqual(len(maze.placements), 2 * width * height)
            self.assertTrue(all(place.closed for _, place in maze.edge_places))
            self.assertEqual(maze.active_places, frozenset())

    def test_wall_helpers_use_objects_and_preserve_other_blockers(self):
        maze = ClosedMaze(3, 2)
        other = PlaceObject(blocks_movement=True)
        maze.place_between((0, 0), "right", other)
        maze.remove_wall((0, 0), "right")
        self.assertFalse(maze.has_wall((0, 0), "right"))
        self.assertFalse(maze.can_move((0, 0), "right"))
        maze.remove_between((0, 0), "right", other)
        self.assertTrue(maze.can_move((0, 0), "right"))
        maze.add_wall((0, 0), "right").add_wall((0, 0), "right")
        self.assertEqual(len(maze.objects_between((1, 0), "left")), 1)

    def test_path_is_validated_before_changes_and_notifies_once(self):
        maze = ClosedMaze(3, 3)
        observer = Mock()
        maze.subscribe(observer)
        with self.assertRaises(ValueError):
            maze.open_path([(0, 0), (1, 0), (2, 1)])
        self.assertEqual(maze.links, ())
        observer.assert_not_called()
        maze.open_path([(0, 0), (1, 0), (1, 1)])
        observer.assert_called_once_with()
        self.assertTrue(maze.is_connected((1, 1), (1, 0)))
        maze.disconnect((1, 1), (1, 0))
        self.assertFalse(maze.is_connected((1, 0), (1, 1)))


class SpiralMazeTests(unittest.TestCase):
    def test_clockwise_spiral_for_rectangle(self):
        maze = SpiralMaze(4, 3)
        self.assertIsInstance(maze, ClosedMaze)
        self.assertEqual(maze.spiral_path, (
            (0, 0), (1, 0), (2, 0), (3, 0), (3, 1), (3, 2),
            (2, 2), (1, 2), (0, 2), (0, 1), (1, 1), (2, 1),
        ))
        self.assertEqual(maze.exit, (2, 1))

    def test_spiral_visits_every_cell_and_mouse_reaches_end(self):
        # Включает чётные/нечётные стороны, петли и параллельные рёбра тора.
        for width in range(1, 8):
            for height in range(1, 8):
                with self.subTest(width=width, height=height):
                    maze = SpiralMaze(width, height)
                    path = maze.spiral_path
                    self.assertEqual(len(path), width * height)
                    self.assertEqual(set(path), set(maze.positions))
                    self.assertEqual(maze.entrance, path[0])
                    self.assertEqual(maze.exit, path[-1])
                    self.assertEqual(len(maze.links), 2 * (width * height - 1))
                    mouse = Mouse(maze)
                    for start, end in zip(path, path[1:]):
                        delta = (end[0] - start[0], end[1] - start[1])
                        direction = next(d for d in Direction if d.delta == delta)
                        self.assertEqual(mouse.move(direction), 1)
                        self.assertEqual(mouse.position, end)
                    self.assertTrue(mouse.at_exit)
                    for x in range(width):
                        self.assertTrue(maze.cell_at((x, 0)).up.closed)
                        self.assertTrue(maze.cell_at((x, height - 1)).down.closed)
                    for y in range(height):
                        self.assertTrue(maze.cell_at((0, y)).left.closed)
                        self.assertTrue(maze.cell_at((width - 1, y)).right.closed)


class MouseTests(unittest.TestCase):
    def test_stops_at_closed_edge_and_only_successful_moves_tick(self):
        maze = ClosedMaze(4, 1)
        maze.open_path([(0, 0), (1, 0), (2, 0)])
        mouse = Mouse(maze)
        self.assertEqual(mouse.right(10), 2)
        self.assertEqual(mouse.position, (2, 0))
        self.assertEqual(mouse.down(), 0)
        self.assertEqual(maze.current_tick, 2)
        self.assertEqual(mouse.trail, ((0, 0), (1, 0), (2, 0)))

    def test_trail_is_three_successive_objects_with_ttl_one(self):
        grid = Grid(5, 1)
        mouse = Mouse(grid)
        mouse.right()
        place = grid.root.right
        first = place.objects[0]
        self.assertIsInstance(first, TrailLine)
        self.assertEqual(first.layer, 3)
        self.assertEqual(first.ttl, 1)
        mouse.right()
        self.assertIs(place.objects[0], first.next_object)
        mouse.right()
        self.assertIs(place.objects[0], first.next_object.next_object)
        mouse.right()
        self.assertEqual(place.objects, ())

    def test_multiple_mice_and_generic_objects_survive_reset(self):
        grid = Grid(3, 1)
        first, second = Mouse(grid), Mouse(grid)
        item = PlaceObject()
        grid.root.right.install(item)
        first.right()
        second.right()
        first.reset()
        self.assertEqual(len(grid.root.right.objects), 2)
        second.reset()
        self.assertEqual(grid.root.right.objects, (item,))
        self.assertEqual(second.trail, ((0, 0),))

    def test_notifications_deduplicate_shared_callbacks(self):
        grid = Grid(2, 1)
        mouse = Mouse(grid)
        shared, grid_only = Mock(), Mock()
        grid.subscribe(shared)
        mouse.subscribe(shared)
        grid.subscribe(grid_only)
        mouse.right()
        shared.assert_called_once_with()
        grid_only.assert_called_once_with()

    def test_directions_wrap_and_endpoint_changes(self):
        grid = Grid(3, 3, entrance=(1, 1))
        mouse = Mouse(grid)
        for command, position in [(mouse.up, (1, 0)), (mouse.right, (2, 0)),
                                  (mouse.down, (2, 1)), (mouse.left, (1, 1))]:
            command()
            self.assertEqual(mouse.position, position)
        mouse.down(10)
        self.assertEqual(mouse.position, (1, 2))
        grid.exit = mouse.position
        self.assertTrue(mouse.at_exit)
        grid.entrance = (0, 0)
        mouse.reset()
        self.assertEqual(mouse.position, (0, 0))
        single = Mouse(Grid(1, 1))
        self.assertEqual(single.right(), 1)
        self.assertTrue(single.at_exit)

    def test_async_walk_stops_at_first_blocked_command(self):
        maze = ClosedMaze(2, 2)
        maze.connect((0, 0), (1, 0))
        mouse = Mouse(maze)
        self.assertEqual(asyncio.run(mouse.walk(["right", "down", "left"], delay=0)), 1)


class ViewTests(unittest.TestCase):
    def test_registry_layers_mro_and_unknown_objects(self):
        @dataclass(frozen=True, slots=True, kw_only=True)
        class Marker(PlaceObject):
            name: str

        class Child(Marker):
            pass

        grid = Grid(3, 2)
        view = MazeView(grid)
        self.addCleanup(view.close)
        calls = []

        def render(view, obj, location, context):
            calls.append((obj.name, location, len(context.placements)))
            return f'<g id="{obj.name}"/>'

        view.register_renderer(Marker, render)
        grid.place_on((0, 0), Marker(name="front", layer=3))
        grid.place_between((0, 0), "right", Child(name="back", layer=1))
        grid.place_on((2, 1), Marker(name="middle", layer=2))
        grid.place_on((0, 0), Marker(name="front-second", layer=3))
        grid.place_on((0, 0), PlaceObject())
        svg = view.to_svg()
        ElementTree.fromstring(svg)
        self.assertEqual([name for name, _, _ in calls],
                         ["back", "middle", "front", "front-second"])
        self.assertEqual(calls[0][1].direction, Direction.RIGHT)
        self.assertEqual(calls[0][2], 5)
        calls.clear()
        view.register_renderer(Child, lambda *args: '<g id="child"/>')
        self.assertIn('id="child"', view.to_svg())
        self.assertNotIn("back", [name for name, _, _ in calls])

    def test_gradient_line_is_layer_three_and_has_no_cell_fill(self):
        grid = Grid(5, 1)
        mouse = Mouse(grid)
        view = MazeView(grid, mouse)
        self.addCleanup(view.close)
        mouse.right(3)
        svg = view.to_svg()
        root = ElementTree.fromstring(svg)
        gradients = root.findall(".//s:linearGradient", NS)
        self.assertEqual(len(gradients), 3)
        stops = [[stop.attrib["stop-color"] for stop in gradient] for gradient in gradients]
        self.assertEqual(stops, [["#dbeafe", "#bfdbfe"],
                                 ["#bfdbfe", "#93c5fd"], ["#93c5fd", "#93c5fd"]])
        self.assertNotIn('fill="#93c5fd"', svg)
        view.show_trail = False
        self.assertNotIn("linearGradient", view.to_svg())
        self.assertEqual(len(grid.placements), 3)

    def test_generic_closed_edge_and_seam_render_without_wall_knowledge(self):
        grid = Grid(3, 2)
        view = MazeView(grid, show_grid=False)
        self.addCleanup(view.close)
        self.assertNotIn("#334155", view.to_svg())
        grid.place_between((0, 0), "left", PlaceObject(blocks_movement=True))
        root = ElementTree.fromstring(view.to_svg())
        barriers = [line for line in root.findall("s:line", NS)
                    if line.attrib["stroke"] == "#334155"]
        self.assertEqual(len(barriers), 2)
        view.mouse.left()  # blocked, no trail
        self.assertNotIn("linearGradient", view.to_svg())

    def test_trail_on_toroidal_seam_is_split_into_two_half_segments(self):
        grid = Grid(3, 1)
        view = MazeView(grid)
        self.addCleanup(view.close)
        view.mouse.left()
        root = ElementTree.fromstring(view.to_svg())
        trail = [line for line in root.findall("s:polyline", NS)
                 if line.attrib["stroke"].startswith("url(")]
        self.assertEqual(len(trail), 2)
        for line in trail:
            points = [tuple(map(float, point.split(",")))
                      for point in line.attrib["points"].split()]
            self.assertEqual(abs(points[1][0] - points[0][0]), view.cell_size / 2)

    def test_display_id_updates_once_and_unsubscribes(self):
        grid = Grid(2, 2)
        view = MazeView(grid)
        self.addCleanup(view.close)
        handle = Mock()
        with patch("labyrinth_view.display", return_value=handle):
            view._ipython_display_()
        view.mouse.right()
        handle.update.assert_called_once()
        self.assertEqual(handle.update.call_args.args[0].data, view.to_svg())
        view.close()
        view.mouse.reset()
        handle.update.assert_called_once()
        self.assertEqual(grid._listeners, [])

    def test_demo_reaches_exit_and_notebook_uses_new_api(self):
        maze, mouse, view = create_demo()
        self.addCleanup(view.close)
        self.assertIsInstance(maze, ClosedMaze)
        route = ["right", "right", "down", "left", "down", "right", "right", "right", "down"]
        self.assertEqual(asyncio.run(mouse.walk(route, delay=0)), 9)
        self.assertTrue(mouse.at_exit)
        self.assertFalse(maze.can_move((0, 0), "up"))
        notebook = json.loads((Path(__file__).resolve().parents[1] / "demo.ipynb").read_text())
        sources = "".join("".join(cell["source"]) for cell in notebook["cells"])
        self.assertNotIn("mode=", sources)
        self.assertNotIn("trail_ttl", sources)
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code" and not any("await " in line for line in cell["source"]):
                compile("".join(cell["source"]), "demo.ipynb", "exec")

    def test_notebook_cells_execute_with_new_api(self):
        notebook = json.loads((Path(__file__).resolve().parents[1] / "demo.ipynb").read_text())
        namespace = {}
        with (patch("time.sleep"), patch("asyncio.sleep", new_callable=AsyncMock),
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
        self.assertTrue(namespace["mouse"].at_exit)
        self.assertTrue(namespace["my_grid"].can_move((1, 0), "right"))


if __name__ == "__main__":
    unittest.main()
