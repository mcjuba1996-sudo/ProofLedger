# Компоненты среды исполнения

В архив включены CPython 3.13.17 для WASI и Wasmtime 50.0.1 для 64-разрядного Python на Windows x64. Остальные поддерживаемые платформы используют Python-пакет из `requirements-judge.txt`.

- `python.wasm` взят без изменений из дистрибутива CPython WASI. В `lib/python313.zip` собраны его библиотечные файлы с сохранением содержимого. Тесты, графические инструменты и установщик pip исключены. SHA-256 исходного ZIP — `ceec2512d4bf2d2377a4791603e9879329c9b6aa0e7514a19ef827cdefe3b274`. Условия поставки находятся в `CPython-LICENSE.txt`. [Сборка CPython WASI](https://github.com/brettcannon/cpython-wasi-build/releases/tag/v3.13.17).
- В `win_amd64/` распаковано колесо `wasmtime-50.0.1-py3-none-win_amd64.whl`. SHA-256 исходного колеса — `a8999581230471f4ff62073b1111eff3a01d45ccec0a908da3f2ae3a499d459c`. Лицензия поставляется в `win_amd64/wasmtime-50.0.1.dist-info/licenses/LICENSE`. [Wasmtime на PyPI](https://pypi.org/project/wasmtime/50.0.1/).

## Пакеты для автономной проверки

В `wheels/` лежат дистрибутивы pytest и его зависимостей, а также Wasmtime для Linux amd64 и arm64. У каждого пакета сохранены метаданные и лицензия. На Windows x64 судейский процесс использует движок из `win_amd64/`.

