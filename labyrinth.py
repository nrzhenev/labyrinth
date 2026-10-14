"""Закольцованный клеточный лабиринт. Координаты: (столбец, строка)."""

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum

Position = tuple[int, int]


class Direction(str, Enum):
    UP = "up"
    RIGHT = "right"
    DOWN = "down"
    LEFT = "left"

    @property
    def delta(self) -> Position:
        return {
            Direction.UP: (0, -1),
            Direction.RIGHT: (1, 0),
            Direction.DOWN: (0, 1),
            Direction.LEFT: (-1, 0),
        }[self]

    @property
    def opposite(self) -> "Direction":
        return {
            Direction.UP: Direction.DOWN,
            Direction.RIGHT: Direction.LEFT,
            Direction.DOWN: Direction.UP,
            Direction.LEFT: Direction.RIGHT,
        }[self]


@dataclass(eq=False, slots=True)
class Cell:
    """Узел графа с четырьмя ссылками на соседние клетки.

    Отсутствующая ссылка (`None`) означает стену с этой стороны.
    """

    x: int
    y: int
    up: "Cell | None" = field(default=None, repr=False)
    right: "Cell | None" = field(default=None, repr=False)
    down: "Cell | None" = field(default=None, repr=False)
    left: "Cell | None" = field(default=None, repr=False)

    @property
    def position(self) -> Position:
        return self.x, self.y

    def neighbor(self, direction: Direction | str) -> "Cell | None":
        return getattr(self, Direction(direction).value)


class Observable:
    def __init__(self):
        self._listeners: list[Callable[[], None]] = []

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(callback)

        def unsubscribe():
            if callback in self._listeners:
                self._listeners.remove(callback)

        return unsubscribe

    def _notify(self):
        for callback in tuple(self._listeners):
            callback()


class Maze(Observable):
    """Двумерный список связанных клеток с закольцованными краями.

    `cells[y][x]` возвращает объект клетки, `root` указывает на `cells[0][0]`.
    По умолчанию все четыре ссылки каждой клетки открыты, включая переходы
    через края поля. Стена представлена отсутствующей парой обратных ссылок.
    """

    def __init__(
        self,
        width: int,
        height: int,
        *,
        mode: str = "open",
        entrance: Position = (0, 0),
        exit: Position | None = None,
    ):
        super().__init__()
        if type(width) is not int or type(height) is not int or min(width, height) < 1:
            raise ValueError("Размеры должны быть положительными целыми числами")
        self._width, self._height = width, height
        self.cells = [[Cell(x, y) for x in range(width)] for y in range(height)]
        self.root = self.cells[0][0]
        self._entrance = self.validate_position(entrance)
        self._exit = self.validate_position(exit if exit is not None else (width - 1, height - 1))
        self.reset(mode=mode)

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def positions(self) -> tuple[Position, ...]:
        return tuple(cell.position for row in self.cells for cell in row)

    @property
    def links(self) -> tuple[tuple[Position, str, Position], ...]:
        """Все направленные ссылки как `(откуда, направление, куда)`."""
        return tuple(
            (cell.position, direction.value, neighbor.position)
            for row in self.cells
            for cell in row
            for direction in Direction
            if (neighbor := cell.neighbor(direction)) is not None
        )

    @property
    def entrance(self) -> Position:
        return self._entrance

    @entrance.setter
    def entrance(self, position: Position):
        self._entrance = self.validate_position(position)
        self._notify()

    @property
    def exit(self) -> Position:
        return self._exit

    @exit.setter
    def exit(self, position: Position):
        self._exit = self.validate_position(position)
        self._notify()

    def validate_position(self, position: Position) -> Position:
        if (
            not isinstance(position, tuple)
            or len(position) != 2
            or any(type(value) is not int for value in position)
            or not (0 <= position[0] < self.width and 0 <= position[1] < self.height)
        ):
            raise ValueError(f"Клетка вне лабиринта или некорректна: {position!r}")
        return position

    def cell_at(self, position: Position) -> Cell:
        x, y = self.validate_position(position)
        return self.cells[y][x]

    def adjacent(self, position: Position, direction: Direction | str) -> Position:
        """Закольцованный геометрический сосед, независимо от стены."""
        x, y = self.validate_position(position)
        dx, dy = Direction(direction).delta
        return (x + dx) % self.width, (y + dy) % self.height

    def reset(self, *, mode: str = "open") -> "Maze":
        """`open` открывает все ссылки, `closed` удаляет все ссылки."""
        if mode not in ("open", "closed"):
            raise ValueError("mode должен быть 'open' или 'closed'")
        for row in self.cells:
            for cell in row:
                for direction in Direction:
                    neighbor = self.cell_at(self.adjacent(cell.position, direction))
                    setattr(cell, direction.value, neighbor if mode == "open" else None)
        self._notify()
        return self

    def _direction_between(self, a: Position, b: Position) -> Direction:
        self.validate_position(a)
        self.validate_position(b)
        ax, ay = a
        bx, by = b
        direct = {
            (0, -1): Direction.UP,
            (1, 0): Direction.RIGHT,
            (0, 1): Direction.DOWN,
            (-1, 0): Direction.LEFT,
        }.get((bx - ax, by - ay))
        if direct is not None:
            return direct
        for direction in Direction:
            if self.adjacent(a, direction) == b:
                return direction
        raise ValueError("Связать можно только соседние клетки, включая клетки через край")

    def _set_link(self, position: Position, direction: Direction | str, opened: bool):
        direction = Direction(direction)
        cell = self.cell_at(position)
        neighbor = self.cell_at(self.adjacent(position, direction))
        setattr(cell, direction.value, neighbor if opened else None)
        setattr(neighbor, direction.opposite.value, cell if opened else None)

    def connect(self, a: Position, b: Position) -> "Maze":
        """Открыть связь между соседями; для неоднозначных краёв лучше remove_wall."""
        self._set_link(a, self._direction_between(a, b), True)
        self._notify()
        return self

    def disconnect(self, a: Position, b: Position) -> "Maze":
        """Закрыть связь между соседями; для неоднозначных краёв лучше add_wall."""
        self._set_link(a, self._direction_between(a, b), False)
        self._notify()
        return self

    def is_connected(self, a: Position, b: Position) -> bool:
        direction = self._direction_between(a, b)
        neighbor = self.cell_at(a).neighbor(direction)
        return neighbor is not None and neighbor.position == b

    def add_wall(self, position: Position, direction: Direction | str) -> "Maze":
        self._set_link(position, direction, False)
        self._notify()
        return self

    def remove_wall(self, position: Position, direction: Direction | str) -> "Maze":
        self._set_link(position, direction, True)
        self._notify()
        return self

    def has_wall(self, position: Position, direction: Direction | str) -> bool:
        return self.cell_at(position).neighbor(direction) is None

    def can_move(self, position: Position, direction: Direction | str) -> bool:
        return not self.has_wall(position, direction)

    def neighbors(self, position: Position) -> tuple[Position, ...]:
        """Доступные соседи по направлениям up, right, down, left."""
        cell = self.cell_at(position)
        return tuple(
            neighbor.position
            for direction in Direction
            if (neighbor := cell.neighbor(direction)) is not None
        )

    def open_path(self, positions: Iterable[Position]) -> "Maze":
        positions = list(positions)
        directions = [self._direction_between(a, b) for a, b in zip(positions, positions[1:])]
        for position in positions:
            self.validate_position(position)
        for position, direction in zip(positions, directions):
            self._set_link(position, direction, True)
        self._notify()
        return self


class Mouse(Observable):
    """Мышка переходит по ссылкам между объектами Cell."""

    def __init__(self, maze: Maze):
        super().__init__()
        self.maze = maze
        self._cell = maze.cell_at(maze.entrance)
        self._trail = [self.position]
        self._last_direction: Direction | None = None

    @property
    def cell(self) -> Cell:
        return self._cell

    @property
    def position(self) -> Position:
        return self.cell.position

    @property
    def trail(self) -> tuple[Position, ...]:
        return tuple(self._trail)

    @property
    def last_direction(self) -> Direction | None:
        return self._last_direction

    @property
    def at_exit(self) -> bool:
        return self.position == self.maze.exit

    def can_move(self, direction: Direction | str) -> bool:
        return self.cell.neighbor(direction) is not None

    def move(self, direction: Direction | str, steps: int = 1) -> int:
        direction = Direction(direction)
        if type(steps) is not int or steps < 0:
            raise ValueError("steps должен быть неотрицательным целым числом")
        moved = 0
        for _ in range(steps):
            neighbor = self.cell.neighbor(direction)
            if neighbor is None:
                break
            self._cell = neighbor
            self._last_direction = direction
            self._trail.append(self.position)
            moved += 1
            self._notify()
        if moved == 0:
            self._notify()
        return moved

    def up(self, steps: int = 1) -> int:
        return self.move(Direction.UP, steps)

    def down(self, steps: int = 1) -> int:
        return self.move(Direction.DOWN, steps)

    def right(self, steps: int = 1) -> int:
        return self.move(Direction.RIGHT, steps)

    def left(self, steps: int = 1) -> int:
        return self.move(Direction.LEFT, steps)

    def reset(self):
        self._cell = self.maze.cell_at(self.maze.entrance)
        self._trail = [self.position]
        self._last_direction = None
        self._notify()

    async def walk(self, directions: Iterable[Direction | str], *, delay: float = 0.2) -> int:
        if delay < 0:
            raise ValueError("delay не может быть отрицательным")
        moved = 0
        for direction in directions:
            if self.move(direction) == 0:
                break
            moved += 1
            await asyncio.sleep(delay)
        return moved
