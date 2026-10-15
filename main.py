from labyrinth import ClosedMaze, Game
from labyrinth_view import MazeView


def create_demo():
    """В Jupyter: maze, game, view = create_demo(); display(view)."""
    maze = ClosedMaze(5, 4)
    maze.open_path(
        [
            (0, 0),
            (1, 0),
            (2, 0),
            (2, 1),
            (1, 1),
            (1, 2),
            (2, 2),
            (3, 2),
            (4, 2),
            (4, 3),
        ]
    )
    maze.open_path([(2, 0), (3, 0), (4, 0), (4, 1)])  # Тупик.
    maze.open_path([(1, 2), (0, 2), (0, 3)])  # Ещё один тупик.
    game = Game(maze)
    return maze, game, MazeView(game)


def main():
    print("Откройте demo.ipynb в Jupyter: uv run jupyter lab demo.ipynb")


if __name__ == "__main__":
    main()
