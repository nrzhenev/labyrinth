import random
import unittest

from labyrinth import Game, RandomMaze


class RandomMazeTests(unittest.TestCase):
    def test_seed_reproduces_layout_without_sharing_state(self):
        first = RandomMaze(12, 8, seed=42)
        expected = first.links
        state = random.getstate()
        try:
            random.seed(999)
            random.random()
            before = random.getstate()
            second = RandomMaze(12, 8, seed=42)
            self.assertEqual(random.getstate(), before)
        finally:
            random.setstate(state)
        self.assertIsNot(first, second)
        self.assertEqual(second.links, expected)
        for location, place in first.edge_places:
            if not place.closed:
                first.add_wall(location.position, location.direction)
                break
        self.assertEqual(second.links, expected)
        self.assertEqual(RandomMaze(12, 8, seed=42).links, expected)
        self.assertNotEqual(first.links, expected)

    def test_different_seeds_produce_different_layouts(self):
        layouts = {RandomMaze(10, 8, seed=seed).links for seed in range(5)}
        self.assertEqual(len(layouts), 5)

    def test_layout_is_a_spanning_tree_including_degenerate_sizes(self):
        for width, height in [(1, 1), (1, 2), (2, 1), (1, 9), (9, 1),
                              (2, 2), (2, 7), (7, 2), (12, 8), (1, 1100)]:
            for seed in (0, 42, -7):
                with self.subTest(width=width, height=height, seed=seed):
                    maze = RandomMaze(width, height, seed=seed)
                    for x in range(width):
                        self.assertTrue(maze.has_wall((x, 0), "up"))
                        self.assertTrue(maze.has_wall((x, height - 1), "down"))
                    for y in range(height):
                        self.assertTrue(maze.has_wall((0, y), "left"))
                        self.assertTrue(maze.has_wall((width - 1, y), "right"))
                    visited = {maze.entrance}
                    pending = [maze.entrance]
                    while pending:
                        for neighbor in maze.neighbors(pending.pop()):
                            if neighbor not in visited:
                                visited.add(neighbor)
                                pending.append(neighbor)
                    self.assertEqual(visited, set(maze.positions))
                    self.assertEqual(
                        sum(not place.closed for _, place in maze.edge_places),
                        width * height - 1,
                    )

    def test_custom_entrance_and_exit_work_with_game(self):
        maze = RandomMaze(5, 4, seed=42, entrance=(2, 1), exit=(0, 3))
        game = Game(maze)
        self.assertEqual(game.mouse.position, (2, 1))
        self.assertEqual(game.exit, (0, 3))
        self.assertEqual(maze.seed, 42)
        self.assertTrue(Game(RandomMaze(1, 1, seed=0)).finished)

    def test_invalid_parameters(self):
        for seed in (None, True, 1.5, "42"):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                RandomMaze(5, 4, seed=seed)
        for width, height in [(0, 4), (5, -1), (True, 4), (5, 1.5)]:
            with self.subTest(size=(width, height)), self.assertRaises(ValueError):
                RandomMaze(width, height, seed=42)
        with self.assertRaises(ValueError):
            RandomMaze(5, 4, seed=42, entrance=(5, 0))
        with self.assertRaises(ValueError):
            RandomMaze(5, 4, seed=42, exit=(-1, 0))
