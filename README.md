# Сетка, лабиринт и игра

Небольшой Python-движок для Jupyter: тороидальная сетка, объекты на клетках и
рёбрах, взаимодействия, TTL и SVG-представление.

```bash
uv sync
uv run jupyter lab demo.ipynb
```

## Быстрый старт

```python
from IPython.display import display
from labyrinth import Game, SpiralMaze
from labyrinth_view import MazeView

maze = SpiralMaze(8, 5)
game = Game(maze)
view = MazeView(game)
display(view)

game.right()  # одна попытка шага, возвращает bool
```

`Game` является единственной публичной точкой движения. Доступны только:

```python
game.up()
game.right()
game.down()
game.left()
```

Каждая команда выполняет не более одного шага. Она возвращает `True`, если
мышь переместилась, и `False`, если ребро закрыто или игра завершена. Методов
`move()`, `walk()` и аргумента количества шагов нет. `Mouse` создаётся объектом
`Game` и хранит только позицию, направление и историю пути.

Для маршрута явно вызывайте одноклеточную команду на каждой итерации:

```python
commands = {
    "up": game.up,
    "right": game.right,
    "down": game.down,
    "left": game.left,
}

for direction in ["right", "down", "right"]:
    if not commands[direction]():
        break
```

## Grid и ClosedMaze

`Grid(width, height)` создаёт открытую закольцованную сетку. `ClosedMaze`
наследует `Grid` и устанавливает стены на все физические рёбра. Только
`ClosedMaze` знает тип стены и предоставляет `add_wall()`, `remove_wall()`,
`connect()`, `disconnect()` и `open_path()`.

```python
from labyrinth import ClosedMaze, Game

maze = ClosedMaze(4, 3, exit=(3, 2))
maze.open_path([(0, 0), (1, 0), (1, 1), (2, 1), (3, 1), (3, 2)])
game = Game(maze)
```

`SpiralMaze` автоматически открывает путь по часовой стрелке от `(0, 0)` к
центру. Каждая клетка посещается один раз, а последняя становится выходом.

```python
maze = SpiralMaze(6, 4)
print(maze.spiral_path)
print(maze.exit)
```

Поддерживаются любые положительные размеры, включая `1×1`, `1×N` и `N×1`.

## Place

У каждой клетки есть пять агрегатов:

```python
cell.place  # объекты на клетке
cell.left
cell.right
cell.up
cell.down   # объекты между клетками
```

Противоположные стороны соседей возвращают один `Place`:

```python
assert cell.right is grid.neighbor(cell.position, "right").left
```

`Place.closed` кэшируется и равен `True`, если хотя бы один установленный объект
имеет `blocks_movement=True`. `Grid.can_move()` использует это свойство без
перебора объектов.

```python
from labyrinth import Grid, PlaceObject

grid = Grid(4, 3)
door = PlaceObject(blocks_movement=True)
grid.place_between((0, 0), "right", door)
assert grid.root.right.closed
grid.remove_between((0, 0), "right", door)
```

Объекты изменяются только через `Place.install()`, `remove()` и
`remove_where()`. Объекты непосредственно на клетке не блокируют вход в неё.

## TTL

Объект может задать срок жизни и готового преемника:

```python
opened = PlaceObject()
closed = PlaceObject(blocks_movement=True, ttl=2, next_object=opened)
grid.place_between((0, 0), "right", closed)
grid.tick()
grid.tick()
assert not grid.root.right.closed
```

Остаток TTL принадлежит конкретной установке в `Place`. Преемник получает
полный TTL и не стареет на тике своей установки. `Grid` обрабатывает только
`active_places`, содержащие временные объекты.

## Взаимодействия

`PlaceObject` может содержать callback `on_interact`. Он не изменяет модель
напрямую, а возвращает `InteractionEffect`:

```python
from labyrinth import InteractionEffect, PlaceObject

def take_item(context):
    print("Предмет подобран", context.target)
    return InteractionEffect(replacement=None)  # одноразовый объект

item = PlaceObject(layer=2, on_interact=take_item)
game.grid.place_on((1, 0), item)
```

При успешном шаге `Game` выполняет строго такой порядок:

```text
тик старых объектов
edge_place.interact()
перемещение мыши
destination.place.interact()
установка нового сегмента следа
публикация событий
```

Callbacks вызываются по снимку в порядке установки объектов. `Place` запрещает
рекурсивный `interact()` и прямое изменение самого себя внутри callback. Эффект
может оставить объект (`KEEP`), удалить его (`replacement=None`), заменить его
или вернуть доменные события.

Контекст содержит `game`, `actor`, `place`, `source`, `target`, `direction` и
фазу `EDGE` либо `DESTINATION`.

## Завершение игры

`Game` устанавливает во входящий `Place` выхода одноразовый объект с callback
`endgame`. При первом входе он удаляется и возвращает `EndGameEvent`.

```python
events = []
game.subscribe_event(events.append)

while not game.finished:
    if not game.right():
        break
```

После завершения все команды движения возвращают `False`. Статус доступен как
`game.status` и `game.finished`. На поле `1×1` взаимодействие начальной клетки
происходит при создании `Game`, поэтому игра завершается сразу.

Вход и выход после создания игры меняются только через неё:

```python
game.entrance = (0, 1)
game.exit = (3, 2)  # объект финиша переносится атомарно
game.reset()        # мышь на вход, новый финиш, статус RUNNING
```

## След и представление

Каждый успешный шаг устанавливает на пройденное ребро `TrailLine` слоя 3:

```text
яркий TTL 1 → промежуточный TTL 1 → блеклый TTL 1 → удаление
```

`MazeView` принимает `Game`, рисует тонкий градиентный след и подписывается на
игру и сетку:

```python
view = MazeView(game, cell_size=48, show_trail=True, show_grid=True)
```

Модель не зависит от SVG. Для пользовательского типа renderer регистрируется в
конкретном представлении:

```python
view.register_renderer(MyObject, render_my_object)
```

Объекты рисуются по `layer` от 1 до 3, внутри слоя — по порядку установки.
Неизвестные типы пропускаются. Закрытые рёбра рисуются по `Place.closed`, без
знания типа стены.

## Проверки

```bash
uv run python -m unittest discover -s tests
```
