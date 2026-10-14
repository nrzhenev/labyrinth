"""Закольцованная сетка объектов. Координаты: (столбец, строка)."""

import asyncio
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from itertools import count

Position = tuple[int, int]
_orders = count()


class Direction(str, Enum):
    UP = "up"
    RIGHT = "right"
    DOWN = "down"
    LEFT = "left"

    @property
    def delta(self) -> Position:
        return {self.UP: (0, -1), self.RIGHT: (1, 0),
                self.DOWN: (0, 1), self.LEFT: (-1, 0)}[self]

    @property
    def opposite(self) -> "Direction":
        return {self.UP: self.DOWN, self.RIGHT: self.LEFT,
                self.DOWN: self.UP, self.LEFT: self.RIGHT}[self]


@dataclass(frozen=True, slots=True, kw_only=True)
class PlaceObject:
    """Неизменяемое описание объекта; таймер установки принадлежит Place."""

    layer: int = 2
    blocks_movement: bool = False
    ttl: int | None = None
    next_object: "PlaceObject | None" = None

    def __post_init__(self):
        if type(self.layer) is not int or self.layer not in (1, 2, 3):
            raise ValueError("layer должен быть 1, 2 или 3")
        if self.ttl is not None and (type(self.ttl) is not int or self.ttl < 1):
            raise ValueError("ttl должен быть положительным целым числом или None")
        if self.next_object is not None:
            if not isinstance(self.next_object, PlaceObject) or self.ttl is None:
                raise ValueError("next_object требует TTL и должен быть PlaceObject")


@dataclass(slots=True)
class _PlaceEntry:
    object: PlaceObject
    remaining_ttl: int | None
    blocking: bool
    order: int


class Place:
    """Корень агрегата: установка, удаление, таймеры и кэш закрытости."""

    def __init__(self, on_change: Callable[["Place"], None] | None = None):
        self._entries: list[_PlaceEntry] = []
        self._blocking_count = 0
        self._timed_count = 0
        self._on_change = on_change

    @property
    def objects(self) -> tuple[PlaceObject, ...]:
        return tuple(entry.object for entry in self._entries)

    @property
    def closed(self) -> bool:
        return self._blocking_count > 0

    @property
    def needs_tick(self) -> bool:
        return self._timed_count > 0

    def _changed(self):
        if self._on_change is not None:
            self._on_change(self)

    @staticmethod
    def _validate(obj: PlaceObject):
        if not isinstance(obj, PlaceObject):
            raise TypeError("Устанавливать можно только PlaceObject")
        seen = set()
        while obj is not None:
            if not isinstance(obj, PlaceObject):
                raise TypeError("Устанавливать можно только PlaceObject")
            if id(obj) in seen:
                raise ValueError("Цепочка next_object не должна содержать циклы")
            seen.add(id(obj))
            obj = obj.next_object

    def _entry(self, obj: PlaceObject, order: int) -> _PlaceEntry:
        entry = _PlaceEntry(obj, obj.ttl, bool(obj.blocks_movement), order)
        self._blocking_count += entry.blocking
        self._timed_count += entry.remaining_ttl is not None
        return entry

    def _discard(self, entry: _PlaceEntry):
        self._blocking_count -= entry.blocking
        self._timed_count -= entry.remaining_ttl is not None

    def install(self, obj: PlaceObject) -> "Place":
        self._validate(obj)
        if any(entry.object is obj for entry in self._entries):
            raise ValueError("Объект уже установлен в этом Place")
        self._entries.append(self._entry(obj, next(_orders)))
        self._changed()
        return self

    def remove(self, obj: PlaceObject) -> bool:
        return bool(self.remove_where(lambda item: item is obj))

    def remove_where(self, predicate: Callable[[PlaceObject], bool]) -> int:
        removed = [entry for entry in self._entries if predicate(entry.object)]
        if not removed:
            return 0
        removed_ids = {id(entry) for entry in removed}
        self._entries[:] = [entry for entry in self._entries if id(entry) not in removed_ids]
        for entry in removed:
            self._discard(entry)
        self._changed()
        return len(removed)

    def tick(self):
        """Преемник начинает жить сейчас, но не стареет на этом же тике."""
        if not self.needs_tick:
            return
        entries = []
        for entry in self._entries:
            if entry.remaining_ttl is not None:
                entry.remaining_ttl -= 1
                if entry.remaining_ttl == 0:
                    self._discard(entry)
                    successor = entry.object.next_object
                    if successor is not None:
                        entries.append(self._entry(successor, entry.order))
                    continue
            entries.append(entry)
        self._entries = entries
        self._changed()


@dataclass(eq=False, slots=True)
class Cell:
    x: int
    y: int
    _place: Place = field(default_factory=Place, repr=False)
    _sides: dict[Direction, Place] = field(default_factory=dict, repr=False)

    @property
    def position(self) -> Position:
        return self.x, self.y

    @property
    def place(self) -> Place:
        return self._place

    def side(self, direction: Direction | str) -> Place:
        return self._sides[Direction(direction)]

    @property
    def left(self) -> Place:
        return self.side(Direction.LEFT)

    @property
    def right(self) -> Place:
        return self.side(Direction.RIGHT)

    @property
    def up(self) -> Place:
        return self.side(Direction.UP)

    @property
    def down(self) -> Place:
        return self.side(Direction.DOWN)


@dataclass(frozen=True, slots=True)
class Location:
    position: Position
    direction: Direction | None = None


@dataclass(frozen=True, slots=True)
class ObjectPlacement:
    object: PlaceObject
    place: Place
    location: Location
    order: int


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
        self._notify_all(self)

    @staticmethod
    def _notify_all(*observables: "Observable"):
        callbacks = []
        for observable in observables:
            for callback in tuple(observable._listeners):
                if callback not in callbacks:
                    callbacks.append(callback)
        for callback in callbacks:
            callback()


class Grid(Observable):
    """Открытая тороидальная геометрия с Place на клетках и рёбрах."""

    def __init__(self, width: int, height: int, *, entrance: Position = (0, 0),
                 exit: Position | None = None):
        super().__init__()
        if type(width) is not int or type(height) is not int or min(width, height) < 1:
            raise ValueError("Размеры должны быть положительными целыми числами")
        self._width, self._height = width, height
        self._tick = 0
        self._active_places: set[Place] = set()
        self._batch_depth = 0
        self._pending_notify = False
        self.cells = [[Cell(x, y, Place(self._place_changed)) for x in range(width)]
                      for y in range(height)]
        self.root = self.cells[0][0]
        self._entrance = self.validate_position(entrance)
        self._exit = self.validate_position(exit if exit is not None else (width - 1, height - 1))
        self._edges: list[tuple[Location, Place]] = []
        for row in self.cells:
            for cell in row:
                for direction in (Direction.RIGHT, Direction.DOWN):
                    place = Place(self._place_changed)
                    cell._sides[direction] = place
                    self.neighbor(cell.position, direction)._sides[direction.opposite] = place
                    self._edges.append((Location(cell.position, direction), place))

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def current_tick(self) -> int:
        return self._tick

    @property
    def active_places(self) -> frozenset[Place]:
        return frozenset(self._active_places)

    @property
    def positions(self) -> tuple[Position, ...]:
        return tuple(cell.position for row in self.cells for cell in row)

    @property
    def edge_places(self) -> tuple[tuple[Location, Place], ...]:
        """Каждое физическое ребро ровно один раз, включая швы и петли."""
        return tuple(self._edges)

    @property
    def placements(self) -> tuple[ObjectPlacement, ...]:
        places = [(Location(cell.position), cell.place)
                  for row in self.cells for cell in row]
        places.extend(self._edges)
        return tuple(ObjectPlacement(entry.object, place, location, entry.order)
                     for location, place in places for entry in place._entries)

    @property
    def links(self) -> tuple[tuple[Position, str, Position], ...]:
        return tuple((position, direction.value, self.adjacent(position, direction))
                     for position in self.positions for direction in Direction
                     if self.can_move(position, direction))

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
        if (not isinstance(position, tuple) or len(position) != 2
            or any(type(value) is not int for value in position)
            or not (0 <= position[0] < self.width and 0 <= position[1] < self.height)):
            raise ValueError(f"Клетка вне сетки или некорректна: {position!r}")
        return position

    def cell_at(self, position: Position) -> Cell:
        x, y = self.validate_position(position)
        return self.cells[y][x]

    def adjacent(self, position: Position, direction: Direction | str) -> Position:
        x, y = self.validate_position(position)
        dx, dy = Direction(direction).delta
        return (x + dx) % self.width, (y + dy) % self.height

    def neighbor(self, position: Position, direction: Direction | str) -> Cell:
        return self.cell_at(self.adjacent(position, direction))

    def can_move(self, position: Position, direction: Direction | str) -> bool:
        return not self.cell_at(position).side(direction).closed

    def neighbors(self, position: Position) -> tuple[Position, ...]:
        return tuple(self.adjacent(position, direction) for direction in Direction
                     if self.can_move(position, direction))

    def place_on(self, position: Position, obj: PlaceObject) -> "Grid":
        self.cell_at(position).place.install(obj)
        return self

    def remove_from(self, position: Position, obj: PlaceObject) -> "Grid":
        self.cell_at(position).place.remove(obj)
        return self

    def objects_at(self, position: Position) -> tuple[PlaceObject, ...]:
        return self.cell_at(position).place.objects

    def place_between(self, position: Position, direction: Direction | str,
                      obj: PlaceObject) -> "Grid":
        self.cell_at(position).side(direction).install(obj)
        return self

    def remove_between(self, position: Position, direction: Direction | str,
                       obj: PlaceObject) -> "Grid":
        self.cell_at(position).side(direction).remove(obj)
        return self

    def objects_between(self, position: Position, direction: Direction | str
                        ) -> tuple[PlaceObject, ...]:
        return self.cell_at(position).side(direction).objects

    def _place_changed(self, place: Place):
        if place.needs_tick:
            self._active_places.add(place)
        else:
            self._active_places.discard(place)
        self._notify()

    def _notify(self):
        if self._batch_depth:
            self._pending_notify = True
        else:
            super()._notify()

    @contextmanager
    def batch(self, *, notify: bool = True):
        """Объединить изменения агрегатов в одно уведомление сетки."""
        self._batch_depth += 1
        try:
            yield
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0 and self._pending_notify:
                self._pending_notify = False
                if notify:
                    self._notify()

    def tick(self) -> "Grid":
        with self.batch():
            self._tick += 1
            for place in tuple(self._active_places):
                place.tick()
            self._notify()
        return self


class ClosedMaze(Grid):
    """Только этот класс знает стены; Grid работает с обычными объектами."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class _Wall(PlaceObject):
        layer: int = 3
        blocks_movement: bool = True

    def __init__(self, width: int, height: int, *, entrance: Position = (0, 0),
                 exit: Position | None = None):
        super().__init__(width, height, entrance=entrance, exit=exit)
        with self.batch():
            for location, _ in self.edge_places:
                self.add_wall(location.position, location.direction)

    def has_wall(self, position: Position, direction: Direction | str) -> bool:
        return any(isinstance(obj, self._Wall)
                   for obj in self.objects_between(position, direction))

    def add_wall(self, position: Position, direction: Direction | str) -> "ClosedMaze":
        if not self.has_wall(position, direction):
            self.place_between(position, direction, self._Wall())
        return self

    def remove_wall(self, position: Position, direction: Direction | str) -> "ClosedMaze":
        self.cell_at(position).side(direction).remove_where(
            lambda obj: isinstance(obj, self._Wall)
        )
        return self

    def _direction_between(self, a: Position, b: Position) -> Direction:
        self.validate_position(a)
        self.validate_position(b)
        ax, ay = a
        bx, by = b
        direct = {(0, -1): Direction.UP, (1, 0): Direction.RIGHT,
                  (0, 1): Direction.DOWN, (-1, 0): Direction.LEFT}.get((bx - ax, by - ay))
        if direct is not None:
            return direct
        for direction in Direction:
            if self.adjacent(a, direction) == b:
                return direction
        raise ValueError("Связать можно только соседние клетки")

    def connect(self, a: Position, b: Position) -> "ClosedMaze":
        return self.remove_wall(a, self._direction_between(a, b))

    def disconnect(self, a: Position, b: Position) -> "ClosedMaze":
        return self.add_wall(a, self._direction_between(a, b))

    def is_connected(self, a: Position, b: Position) -> bool:
        return self.can_move(a, self._direction_between(a, b))

    def open_path(self, positions: Iterable[Position]) -> "ClosedMaze":
        positions = list(positions)
        for position in positions:
            self.validate_position(position)
        directions = [self._direction_between(a, b) for a, b in zip(positions, positions[1:])]
        with self.batch():
            for position, direction in zip(positions, directions):
                self.remove_wall(position, direction)
        return self


class SpiralMaze(ClosedMaze):
    """Спираль по часовой стрелке от (0, 0) до центра поля."""

    def __init__(self, width: int, height: int):
        super().__init__(width, height, entrance=(0, 0), exit=(0, 0))
        self._spiral_path = self._build_spiral_path()
        with self.batch():
            self.exit = self._spiral_path[-1]
            self.open_path(self._spiral_path)

    @property
    def spiral_path(self) -> tuple[Position, ...]:
        return self._spiral_path

    def _build_spiral_path(self) -> tuple[Position, ...]:
        positions = []
        left, right = 0, self.width - 1
        top, bottom = 0, self.height - 1

        while left <= right and top <= bottom:
            for x in range(left, right + 1):
                positions.append((x, top))
            top += 1

            for y in range(top, bottom + 1):
                positions.append((right, y))
            right -= 1

            if top <= bottom:
                for x in range(right, left - 1, -1):
                    positions.append((x, bottom))
                bottom -= 1

            if left <= right:
                for y in range(bottom, top - 1, -1):
                    positions.append((left, y))
                left += 1

        return tuple(positions)


@dataclass(frozen=True, slots=True, kw_only=True)
class TrailLine(PlaceObject):
    layer: int = 3
    owner: object = field(repr=False, compare=False)
    sequence: int
    start: Position
    end: Position
    direction: Direction
    color: str


class Mouse(Observable):
    """Мышь движется по геометрии Grid и оставляет объекты на рёбрах."""

    def __init__(self, grid: Grid):
        super().__init__()
        self.grid = grid
        self._cell = grid.cell_at(grid.entrance)
        self._trail = [self.position]
        self._last_direction: Direction | None = None
        self._trail_owner = object()
        self._sequence = 0
        self._trail_places: set[Place] = set()

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
        return self.position == self.grid.exit

    def can_move(self, direction: Direction | str) -> bool:
        return self.grid.can_move(self.position, direction)

    def _line(self, start: Position, direction: Direction) -> TrailLine:
        common = dict(owner=self._trail_owner, sequence=self._sequence,
                      start=start, end=self.position, direction=direction, ttl=1)
        faded = TrailLine(**common, color="#dbeafe")
        fading = TrailLine(**common, color="#bfdbfe", next_object=faded)
        return TrailLine(**common, color="#93c5fd", next_object=fading)

    def move(self, direction: Direction | str, steps: int = 1) -> int:
        direction = Direction(direction)
        if type(steps) is not int or steps < 0:
            raise ValueError("steps должен быть неотрицательным целым числом")
        moved = 0
        for _ in range(steps):
            if not self.can_move(direction):
                break
            previous = self.position
            place = self.cell.side(direction)
            with self.grid.batch(notify=False):
                self._cell = self.grid.neighbor(previous, direction)
                self._last_direction = direction
                self._trail.append(self.position)
                self._sequence += 1
                self.grid.tick()
                self._trail_places.intersection_update(self.grid.active_places)
                place.install(self._line(previous, direction))
                self._trail_places.add(place)
            moved += 1
            self._notify_all(self.grid, self)
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
        with self.grid.batch(notify=False):
            self._cell = self.grid.cell_at(self.grid.entrance)
            self._trail = [self.position]
            self._last_direction = None
            for place in self._trail_places:
                place.remove_where(lambda obj: isinstance(obj, TrailLine)
                                   and obj.owner is self._trail_owner)
            self._trail_places.clear()
        self._notify_all(self.grid, self)

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
