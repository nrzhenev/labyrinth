import asyncio
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree

from labyrinth import Cell, Maze, Mouse
from labyrinth_view import MazeView
from main import create_demo


class MazeTests(unittest.TestCase):
    def test_default_is_open_and_wrapped(self):
        maze = Maze(3, 2)
        self.assertEqual(maze.adjacent((0, 0), "left"), (2, 0))
        self.assertEqual(maze.adjacent((0, 0), "up"), (0, 1))
        self.assertTrue(maze.can_move((0, 0), "left"))
        self.assertTrue(maze.can_move((0, 0), "up"))
        closed = Maze(3, 2, mode="closed")
        self.assertEqual(closed.neighbors((1, 0)), ())

    def test_links_are_bidirectional_and_only_between_adjacent_cells(self):
        maze = Maze(3, 3, mode="closed")
        maze.connect((0, 0), (1, 0))
        self.assertTrue(maze.can_move((1, 0), "left"))
        maze.disconnect((1, 0), (0, 0))
        self.assertFalse(maze.can_move((0, 0), "right"))
        for a, b in [((0, 0), (1, 1)), ((0, 0), (0, 0)), ((0, 0), (-1, 0))]:
            with self.assertRaises(ValueError):
                maze.connect(a, b)

    def test_invalid_path_does_not_partially_change_maze(self):
        maze = Maze(3, 2, mode="closed")
        with self.assertRaises(ValueError):
            maze.open_path([(0, 0), (1, 0), (2, 1)])
        self.assertEqual(maze.neighbors((0, 0)), ())

    def test_invalid_inputs(self):
        for width, height in [(0, 2), (2, -1), (2.5, 2), (True, 2)]:
            with self.assertRaises(ValueError):
                Maze(width, height)
        with self.assertRaises(ValueError):
            Maze(2, 2, entrance=(2, 0))
        with self.assertRaises(ValueError):
            Maze(2, 2, mode="unknown")

    def test_directional_walls_reset_and_graph_snapshot(self):
        maze = Maze(3, 2)
        self.assertEqual(len(maze.cells), 2)
        self.assertEqual(len(maze.cells[0]), 3)
        self.assertIsInstance(maze.cells[1][2], Cell)
        self.assertIs(maze.root, maze.cells[0][0])
        self.assertEqual(repr(maze.root), "Cell(x=0, y=0)")
        self.assertIs(maze.root.left, maze.cells[0][2])
        self.assertIs(maze.root.up, maze.cells[1][0])
        self.assertEqual(len(maze.positions), 6)
        self.assertEqual(len(maze.links), 24)
        maze.add_wall((0, 0), "right")
        self.assertTrue(maze.has_wall((1, 0), "left"))
        self.assertIsNone(maze.cells[0][0].right)
        self.assertIsNone(maze.cells[0][1].left)
        maze.remove_wall((1, 0), "left")
        self.assertFalse(maze.has_wall((0, 0), "right"))
        maze.add_wall((0, 0), "up")
        self.assertTrue(maze.has_wall((0, 1), "down"))
        snapshot = maze.links
        maze.reset(mode="closed")
        self.assertEqual(maze.links, ())
        self.assertEqual(len(snapshot), 22)
        with self.assertRaises(ValueError):
            maze.reset(mode="unknown")
        self.assertEqual(maze.links, ())

    def test_changing_endpoints_and_resetting_mouse(self):
        maze = Maze(2, 2)
        mouse = Mouse(maze)
        maze.exit = (0, 0)
        self.assertTrue(mouse.at_exit)
        maze.entrance = (1, 1)
        self.assertEqual(mouse.position, (0, 0))
        mouse.reset()
        self.assertEqual(mouse.position, (1, 1))
        with self.assertRaises(ValueError):
            maze.exit = (2, 2)
        self.assertEqual(maze.exit, (0, 0))


class MouseTests(unittest.TestCase):
    def test_stops_at_wall_without_crossing_it(self):
        maze = Maze(4, 1, mode="closed")
        maze.open_path([(0, 0), (1, 0), (2, 0)])
        mouse = Mouse(maze)
        self.assertEqual(mouse.move("right", 10), 2)
        self.assertEqual(mouse.position, (2, 0))
        self.assertEqual(mouse.move("down"), 0)
        self.assertEqual(mouse.trail, ((0, 0), (1, 0), (2, 0)))

    def test_absolute_movement_exit_and_reset(self):
        maze = Maze(2, 2, entrance=(1, 0), exit=(0, 1))
        mouse = Mouse(maze)
        mouse.down()
        mouse.left()
        self.assertTrue(mouse.at_exit)
        mouse.reset()
        self.assertEqual(mouse.position, (1, 0))
        self.assertEqual(mouse.trail, ((1, 0),))
        self.assertFalse(mouse.at_exit)

    def test_animation_stops_at_first_blocked_command(self):
        maze = Maze(2, 2, mode="closed")
        maze.connect((0, 0), (1, 0))
        mouse = Mouse(maze)
        moved = asyncio.run(mouse.walk(["right", "down", "left"], delay=0))
        self.assertEqual(moved, 1)
        self.assertEqual(mouse.position, (1, 0))

    def test_single_cell_is_already_exit(self):
        mouse = Mouse(Maze(1, 1))
        self.assertTrue(mouse.at_exit)
        self.assertEqual(mouse.right(), 1)
        self.assertEqual(mouse.position, (0, 0))

    def test_all_four_directions_without_turns(self):
        mouse = Mouse(Maze(3, 3, entrance=(1, 1)))
        for command, expected in [
            (mouse.up, (1, 0)), (mouse.right, (2, 0)),
            (mouse.down, (2, 1)), (mouse.left, (1, 1)),
        ]:
            self.assertEqual(command(), 1)
            self.assertEqual(mouse.position, expected)
        self.assertEqual(mouse.down(10), 10)
        self.assertEqual(mouse.position, (1, 2))
        self.assertTrue(mouse.can_move("down"))
        self.assertTrue(mouse.can_move("up"))
        self.assertEqual(mouse.up(2), 2)

    def test_wraps_on_all_boundaries_and_respects_seam_wall(self):
        maze = Maze(3, 3)
        mouse = Mouse(maze)
        mouse.left()
        self.assertEqual(mouse.position, (2, 0))
        mouse.up()
        self.assertEqual(mouse.position, (2, 2))
        mouse.right()
        self.assertEqual(mouse.position, (0, 2))
        mouse.down()
        self.assertEqual(mouse.position, (0, 0))
        maze.add_wall((0, 0), "left")
        self.assertEqual(mouse.left(), 0)
        self.assertTrue(maze.has_wall((2, 0), "right"))


class ViewTests(unittest.TestCase):
    def test_open_maze_has_no_wall_strokes(self):
        view = MazeView(Maze(3, 2))
        self.addCleanup(view.close)
        self.assertNotIn("#334155", view.to_svg())

    def test_svg_and_programmatic_settings(self):
        maze = Maze(2, 2, mode="closed")
        view = MazeView(maze, show_trail=False, show_grid=False)
        self.addCleanup(view.close)
        root = ElementTree.fromstring(view.to_svg())
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertEqual(root.attrib["width"], "120")
        self.assertFalse(view.show_grid)
        self.assertFalse(view.show_trail)
        maze.remove_wall((0, 0), "right")
        view.mouse.right()
        history = view.mouse.trail
        svg_without_trail = view.to_svg()
        view.show_trail = True
        view.show_grid = True
        view.redraw()
        self.assertNotEqual(view.to_svg(), svg_without_trail)
        self.assertEqual(view.mouse.trail, history)
        view.close()
        self.assertEqual(maze._listeners, [])
        self.assertEqual(view.mouse._listeners, [])

    def test_display_id_updates_existing_outputs(self):
        maze = Maze(2, 2)
        view = MazeView(maze)
        self.addCleanup(view.close)
        first, second = Mock(), Mock()
        with patch("labyrinth_view.display", side_effect=[first, second]) as output:
            view._ipython_display_()
            view._ipython_display_()
        self.assertTrue(output.call_args.kwargs["display_id"])
        previous = view.to_svg()
        view.mouse.right()
        self.assertNotEqual(previous, view.to_svg())
        for handle in (first, second):
            handle.update.assert_called_once()
            self.assertEqual(handle.update.call_args.args[0].data, view.to_svg())
        view.close()
        view.mouse.reset()
        first.update.assert_called_once()

    def test_demo_reaches_exit(self):
        maze, mouse, view = create_demo()
        self.addCleanup(view.close)
        steps = asyncio.run(mouse.walk(
            ["right", "right", "down", "left", "down", "right", "right", "right", "down"],
            delay=0,
        ))
        self.assertEqual(steps, 9)
        self.assertEqual(mouse.position, maze.exit)
        self.assertTrue(mouse.at_exit)
        mouse.reset()
        self.assertFalse(mouse.at_exit)


if __name__ == "__main__":
    unittest.main()
