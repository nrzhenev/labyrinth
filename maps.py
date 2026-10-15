from IPython.display import display
from labyrinth import Direction, Game, Grid, PlaceObject, SpiralMaze, RandomMaze, Position, ClosedMaze, ClosedGrid
from labyrinth_view import MazeView


def spiral_game(w, h):
    maze = SpiralMaze(w, h)
    game = Game(maze, tick_delay=0.1)
    view = MazeView(game)
    display(view)
    return game

def generate_grid_game(w, h, x, y):
    maze = Grid(w, h, exit=(x, y))
    game = Game(maze, tick_delay=0.1)
    view = MazeView(game)
    display(view)
    return game

def generate_maze(w, h, seed, delay=0.1):
    maze = RandomMaze(w, h, seed=seed)
    game = Game(maze, tick_delay=delay)
    view = MazeView(game)
    display(view)
    return game

tunnel_path = ((0, 0), (1, 0), (2, 0))

def create_game(level: int):
    if level == 0:
        return generate_grid_game(12, 12, 7, 9)
        
    if level == 1:
        return generate_grid_game(12, 1, 11, 0)

    if level == 2:
        maze = ClosedGrid(12, 2)
        maze.add_wall((3, 0), Direction.RIGHT)
        game = Game(maze, tick_delay=0.1)
        view = MazeView(game)
        display(view)
        return game

    if level == 3:
        maze = ClosedGrid(12, 2)
        maze.add_wall((3, 0), Direction.RIGHT)
        maze.add_wall((7, 1), Direction.RIGHT)
        game = Game(maze, tick_delay=0.1)
        view = MazeView(game)
        display(view)
        return game

    if level == 4:
        return generate_maze(4, 4, 1)

    if level == 5:
        return spiral_game(5, 5)

    if level == 6:
        return spiral_game(8, 8)

    if level == 10:
        return generate_maze(17, 17, 7, 0.02)

