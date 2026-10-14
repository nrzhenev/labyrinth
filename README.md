# Сетка, лабиринт и мышка

Небольшой Python-движок для Jupyter: тороидальная сетка, объекты на клетках и
рёбрах, жизненные циклы через TTL и SVG-представление. Использует стандартный
`IPython.display`, без GUI-виджетов.

## Запуск

Нужен Python 3.13+.

```bash
uv sync
uv run jupyter lab demo.ipynb
```

Без uv: `python -m pip install ipython jupyterlab`, затем
`python -m jupyter lab demo.ipynb`.

Если Jupyter запущен отдельно, зарегистрируйте ядро:

```bash
uv run python -m ipykernel install --user --name labyrinth --display-name "Python (labyrinth)"
```

## Grid и ClosedMaze

`Grid(width, height)` создаёт открытую закольцованную сетку. `ClosedMaze`
наследует `Grid` и устанавливает стены на каждое ребро при инициализации.
Параметра `mode` и класса `Maze` больше нет.

```python
from labyrinth import Grid, ClosedMaze, Mouse
from labyrinth_view import MazeView
from IPython.display import display

maze = ClosedMaze(4, 3)
maze.open_path([(0, 0), (1, 0), (1, 1), (2, 1), (3, 1), (3, 2)])
mouse = Mouse(maze)
view = MazeView(maze, mouse)
display(view)
```

Координаты `(x, y)` начинаются с нуля. Клетки доступны как `grid.cells[y][x]`.
`root` — клетка `(0, 0)`. Сетка закольцована: слева от `(0, 0)` находится
`(width - 1, 0)`, а сверху — `(0, height - 1)`.

```python
grid = Grid(4, 3)
cell = grid.cell_at((0, 0))
neighbor = grid.neighbor((0, 0), "right")
assert cell.right is neighbor.left
print(grid.adjacent((0, 0), "left"))  # (3, 0)
```

`cell.left/right/up/down` — агрегаты `Place` между клетками, а не соседние
клетки. `cell.place` хранит объекты непосредственно на клетке.

Ребро задаётся клеткой и направлением. Это различает внутреннюю границу и шов
тора даже при размере 2; при размере 1 горизонтальная и вертикальная петли
также остаются разными `Place`.

### Спиральный лабиринт

`SpiralMaze` наследует `ClosedMaze` и открывает непрерывный путь по часовой
стрелке от левого верхнего угла к центру. Каждая клетка посещается один раз;
выход устанавливается в последнюю клетку пути. Поддерживаются любые
положительные размеры, включая `1×1`, `1×N` и `N×1`. Швы тора остаются закрыты.

```python
from labyrinth import SpiralMaze

maze = SpiralMaze(8, 5)
mouse = Mouse(maze)
view = MazeView(maze, mouse)
display(view)
print(maze.spiral_path)  # неизменяемый кортеж координат маршрута
print(maze.exit)
```

## Place и объекты

`Place` — корень агрегата. Объекты неизменяемые: для изменения удалите старый и
установите новый. Количество блокирующих объектов кэшируется; чтение
`place.closed` работает за O(1).

```python
from labyrinth import PlaceObject

item = PlaceObject(layer=2)
grid.place_on((1, 1), item)
print(grid.objects_at((1, 1)))
grid.remove_from((1, 1), item)

door = PlaceObject(layer=3, blocks_movement=True)
grid.place_between((0, 0), "right", door)
assert grid.root.right.closed
assert not grid.can_move((0, 0), "right")
grid.remove_between((1, 0), "left", door)  # тот же Place с другой стороны
```

Прямые `cell.place.install(obj)` и `cell.right.remove(obj)` тоже уведомляют
сетку. `place.objects` возвращает кортеж. Удаление выполняется по идентичности
объекта, а не по равенству. Один экземпляр не устанавливается в один Place
дважды; разные Place могут независимо хранить его со своими таймерами.

Объекты непосредственно на клетке не запрещают вход в неё. Движение проверяет
только `closed` соответствующего ребра. `grid.neighbors(position)` и
`grid.links` возвращают проходимые переходы.

Только `ClosedMaze` знает стены. Его `add_wall()`, `remove_wall()`, `has_wall()`,
`connect()`, `disconnect()` и `open_path()` реализованы через установку/удаление
объектов. Удаление стены сохраняет остальные объекты ребра: если дверь тоже
блокирует движение, проход останется закрытым.

```python
maze.remove_wall((0, 0), "left")
maze.add_wall((1, 1), "down")
maze.connect((0, 0), (1, 0))
```

Для неоднозначных пар соседей на торе используйте направление. `connect()` и
`open_path()` предпочитают обычное направление по разности координат.

## TTL и замена объекта

`PlaceObject` имеет свойства `layer`, `blocks_movement`, `ttl` и `next_object`.
`ttl=None` означает постоянный объект. При истечении TTL агрегат атомарно
заменяет объект на `next_object` или удаляет его, если преемника нет.

```python
open_door = PlaceObject(layer=3)
closed_door = PlaceObject(layer=3, blocks_movement=True, ttl=2,
                          next_object=open_door)
grid.place_between((0, 0), "right", closed_door)
grid.tick()
assert grid.root.right.closed
grid.tick()
assert not grid.root.right.closed
```

Остаток TTL хранится в записи `Place`, не в объекте. Преемник начинает свой
полный срок с момента замены и не стареет на том же тике. Временные объекты
стареют независимо от слоя и видимости.

`Grid.tick()` вызывает `Place.tick()` только для активных мест с TTL. Общие
рёбра обрабатываются один раз, а все изменения тика дают одно уведомление.
`grid.active_places` — неизменяемый снимок активных агрегатов.

## Мышь и след

```python
mouse.right(2)
mouse.down()
print(mouse.position, mouse.at_exit)
await mouse.walk(["right", "down", "right"], delay=0.25)
```

`up/down/left/right` и `move(direction, steps)` возвращают число сделанных
шагов. Каждый успешный шаг создаёт один тик; попытка пройти через закрытое
ребро время не расходует. `walk()` останавливается на первой заблокированной
команде. В Jupyter можно использовать `await` прямо в ячейке.

На пройденном ребре мышь устанавливает `TrailLine` на слое 3 с цепочкой:

```text
яркий (TTL 1) → промежуточный (TTL 1) → блеклый (TTL 1) → удаление
```

Каждая стадия — отдельный объект. Линия тонкая и имеет градиент между цветами
соседних сегментов. Переход через край показывается двумя половинами на
противоположных границах. `mouse.trail` остаётся полной историей посещений.
`mouse.reset()` возвращает мышь на вход и удаляет только её объекты следа.

`grid.entrance` и `grid.exit` можно изменять; смена входа не перемещает мышь.
Старый механизм `CellState` удалён: любое состояние теперь объект в `Place`.

## Реестр рендереров и слои

Модель не знает SVG. Каждый экземпляр `MazeView` имеет собственный реестр.
Объекты рисуются по `layer` от 1 до 3, внутри слоя — по порядку установки.
Замена через TTL сохраняет позицию в этом порядке. Неизвестные типы
пропускаются; для наследников выбирается ближайший зарегистрированный тип по
MRO. Закрытые рёбра рисуются по `Place.closed`, без проверки типа стены.

```python
from dataclasses import dataclass
from html import escape

@dataclass(frozen=True, slots=True, kw_only=True)
class Tile(PlaceObject):
    layer: int = 1
    color: str = "#dcfce7"

def render_tile(view, obj, location, context):
    x, y = location.position
    s, m = view.cell_size, view.margin
    return (f'<rect x="{m + x * s}" y="{m + y * s}" '
            f'width="{s}" height="{s}" fill="{escape(obj.color, quote=True)}"/>')

view.register_renderer(Tile, render_tile)
view.grid.place_on((1, 1), Tile())
```

Renderer возвращает SVG-строку. `location.direction=None` означает клетку,
иначе — каноническое ребро. `context.placements` содержит снимок всех объектов
кадра; встроенный renderer линии использует его для поиска следующего сегмента.

```python
view.show_trail = False   # скрыть линии без удаления объектов
view.show_grid = False    # скрыть тонкую сетку, закрытые границы остаются
view.redraw()
svg = view.to_svg()
view.close()              # отключить подписки
```

`display_id` обновляет уже показанный вывод, включая изменения из других ячеек.
Модель в `labyrinth.py` независима от Jupyter; представление — в
`labyrinth_view.py`; `main.py` содержит `create_demo()`.

## Проверки

```bash
uv run python -m unittest discover -s tests
```
